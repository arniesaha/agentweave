/**
 * Auto-instrumentation for LLM SDKs (Anthropic, OpenAI, Google GenAI).
 *
 * Call ``autoInstrument()`` once to monkey-patch SDK client methods so every
 * non-streaming ``create()`` / ``generateContent()`` call gets an OTel span
 * automatically.
 *
 * Two modes:
 *  - **direct** (default): SDK patches emit OTel spans directly to the OTLP
 *    endpoint.  No proxy needed.
 *  - **proxy**: Rewrite SDK base URLs so all calls route through the
 *    AgentWeave proxy.  The proxy handles tracing.
 *
 * @example Direct mode (default):
 * ```ts
 * import { autoInstrument } from 'agentweave-sdk';
 * autoInstrument();
 * ```
 *
 * @example Proxy mode:
 * ```ts
 * import { autoInstrument } from 'agentweave-sdk';
 * autoInstrument({ mode: 'proxy', proxyUrl: 'http://192.168.1.70:30400' });
 * ```
 */

import { trace, context, SpanKind, SpanStatusCode } from '@opentelemetry/api';
import * as schema from './schema';
import { AgentWeaveConfig } from './config';

// ---------------------------------------------------------------------------
// Internal patch registry
// ---------------------------------------------------------------------------

/** Map of provider name → unpatch function. */
export const _activePatches: Map<string, () => void> = new Map();

/** Original env-var values overridden by proxy mode, keyed by env-var name. */
export const _proxyEnvOverrides: Map<string, string | undefined> = new Map();

const KNOWN_PROVIDERS: string[] = ['anthropic', 'openai', 'google'];

/** Env-var names used by each provider's SDK to configure the base URL. */
const PROXY_ENV_KEYS: Record<string, string> = {
  anthropic: 'ANTHROPIC_BASE_URL',
  openai: 'OPENAI_BASE_URL',
  google: 'GOOGLE_GENAI_BASE_URL',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getConfigAttrs(): Record<string, string> {
  if (!AgentWeaveConfig.enabled) return {};
  const attrs: Record<string, string> = {};
  if (AgentWeaveConfig.agentId) {
    attrs[schema.PROV_AGENT_ID] = AgentWeaveConfig.agentId;
    attrs[schema.PROV_WAS_ASSOCIATED_WITH] = AgentWeaveConfig.agentId;
  }
  if (AgentWeaveConfig.agentModel) {
    attrs[schema.PROV_AGENT_MODEL] = AgentWeaveConfig.agentModel;
  }
  if (AgentWeaveConfig.agentVersion) {
    attrs[schema.PROV_AGENT_VERSION] = AgentWeaveConfig.agentVersion;
  }
  return attrs;
}

/**
 * Return true if we are currently executing inside an LLM span.
 * Used to prevent double-tracing when SDK patches are applied on top of
 * explicit ``@traceLlm`` decorators.
 */
export function isAlreadyInLlmSpan(): boolean {
  const span = trace.getActiveSpan();
  if (!span || !span.isRecording()) return false;
  // Access attributes via the internal ReadableSpan shape (available in OTel SDK spans)
  const attrs = (span as any).attributes ?? {};
  return attrs[schema.PROV_ACTIVITY_TYPE] === schema.ACTIVITY_LLM_CALL;
}

/** Extract token counts and stop reason from Anthropic / OpenAI responses. */
export function extractLlmAttrs(response: any, capturesOutput: boolean): Record<string, any> {
  const attrs: Record<string, any> = {};
  const usage = response?.usage;
  if (usage != null) {
    // Anthropic: input_tokens / output_tokens
    let prompt: number | undefined = usage.input_tokens;
    let completion: number | undefined = usage.output_tokens;
    // OpenAI: prompt_tokens / completion_tokens
    if (prompt == null) prompt = usage.prompt_tokens;
    if (completion == null) completion = usage.completion_tokens;
    if (prompt != null) attrs[schema.PROV_LLM_PROMPT_TOKENS] = prompt;
    if (completion != null) attrs[schema.PROV_LLM_COMPLETION_TOKENS] = completion;
    if (prompt != null && completion != null) attrs[schema.PROV_LLM_TOTAL_TOKENS] = prompt + completion;
  }

  // Stop reason — Anthropic: response.stop_reason, OpenAI: choices[0].finish_reason
  let stopReason = response?.stop_reason;
  if (stopReason == null && response?.choices?.[0]) {
    stopReason = response.choices[0].finish_reason;
  }
  if (stopReason != null) attrs[schema.PROV_LLM_STOP_REASON] = stopReason;

  if (capturesOutput) {
    let preview: string | undefined;
    if (response?.content?.[0]?.text) preview = response.content[0].text;
    if (preview == null && response?.choices?.[0]?.message?.content) {
      preview = response.choices[0].message.content;
    }
    if (preview) attrs[schema.PROV_LLM_RESPONSE_PREVIEW] = preview.slice(0, 512);
  }

  return attrs;
}

/** Extract token counts and stop reason from Google GenAI responses. */
export function extractGoogleAttrs(response: any, capturesOutput: boolean): Record<string, any> {
  const attrs: Record<string, any> = {};
  const usage = response?.usageMetadata;
  if (usage != null) {
    const prompt = usage.promptTokenCount;
    const completion = usage.candidatesTokenCount;
    if (prompt != null) attrs[schema.PROV_LLM_PROMPT_TOKENS] = prompt;
    if (completion != null) attrs[schema.PROV_LLM_COMPLETION_TOKENS] = completion;
    if (prompt != null && completion != null) attrs[schema.PROV_LLM_TOTAL_TOKENS] = prompt + completion;
  }

  const finishReason = response?.candidates?.[0]?.finishReason;
  if (finishReason != null) attrs[schema.PROV_LLM_STOP_REASON] = String(finishReason);

  if (capturesOutput) {
    const text = response?.candidates?.[0]?.content?.parts?.[0]?.text;
    if (text) attrs[schema.PROV_LLM_RESPONSE_PREVIEW] = String(text).slice(0, 512);
  }

  return attrs;
}

// ---------------------------------------------------------------------------
// Core LLM span wrapper factory
// ---------------------------------------------------------------------------

/**
 * Build a sync/async wrapper that emits an OTel LLM span around ``original``.
 *
 * This is the single source of truth for span creation in auto-instrumentation.
 * Exposed as ``_makeLlmWrapperForTest`` for white-box unit tests.
 */
export function makeLlmWrapper(
  original: (...args: any[]) => any,
  provider: string,
  getModel: (self: any, args: any[]) => string,
  capturesOutput: boolean,
  extractAttrs: (result: any, co: boolean) => Record<string, any> = extractLlmAttrs,
): (...args: any[]) => any {
  return function wrappedLlmCall(this: any, ...args: any[]) {
    // If we're already inside a traceLlm span, skip — no double-tracing
    if (isAlreadyInLlmSpan()) {
      return original.apply(this, args);
    }

    const model = getModel(this, args);
    const spanName = `${schema.SPAN_PREFIX_LLM}.${model}`;
    const tracer = trace.getTracer('agentweave-tracer');
    const span = tracer.startSpan(spanName, { kind: SpanKind.INTERNAL }, context.active());
    const ctx = trace.setSpan(context.active(), span);

    // Set identifying attrs before calling the original so that nested wrappers
    // can detect this span via isAlreadyInLlmSpan() and skip double-tracing.
    span.setAttribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_LLM_CALL);
    span.setAttribute(schema.PROV_LLM_PROVIDER, provider);
    span.setAttribute(schema.PROV_LLM_MODEL, model);
    span.setAttribute(schema.AUTO_INSTRUMENTED, true);
    span.setAttribute(schema.GEN_AI_OPERATION_NAME, schema.GEN_AI_OP_CHAT);
    span.setAttribute(schema.GEN_AI_SYSTEM, provider);
    for (const [k, v] of Object.entries(getConfigAttrs())) {
      span.setAttribute(k, v);
    }
    span.setAttribute(schema.GEN_AI_REQUEST_MODEL, model);

    const applyResponseAttrs = (response: any): void => {
      for (const [k, v] of Object.entries(extractAttrs(response, capturesOutput))) {
        span.setAttribute(k, v);
      }
    };

    let rawResult: any;
    try {
      rawResult = context.with(ctx, () => original.apply(this, args));
    } catch (err) {
      span.recordException(err as Error);
      span.setStatus({ code: SpanStatusCode.ERROR, message: String(err) });
      span.end();
      throw err;
    }

    // Async path
    if (rawResult != null && typeof rawResult.then === 'function') {
      return (rawResult as Promise<any>).then(
        (val: any) => {
          applyResponseAttrs(val);
          span.end();
          return val;
        },
        (err: unknown) => {
          span.recordException(err as Error);
          span.setStatus({ code: SpanStatusCode.ERROR, message: String(err) });
          span.end();
          throw err;
        },
      );
    }

    // Sync path
    applyResponseAttrs(rawResult);
    span.end();
    return rawResult;
  };
}

// ---------------------------------------------------------------------------
// Provider-specific model extractors
// ---------------------------------------------------------------------------

function getAnthropicModel(_self: any, args: any[]): string {
  if (args.length > 0 && typeof args[0] === 'object' && args[0] !== null) {
    if (args[0].model) return String(args[0].model);
  }
  return 'unknown';
}

function getOpenAIModel(_self: any, args: any[]): string {
  if (args.length > 0 && typeof args[0] === 'object' && args[0] !== null) {
    if (args[0].model) return String(args[0].model);
  }
  return 'unknown';
}

function getGoogleModel(self: any, _args: any[]): string {
  if (self && self.model) {
    return String(self.model).replace(/^models\//, '');
  }
  return 'unknown';
}

// ---------------------------------------------------------------------------
// Provider patchers
// ---------------------------------------------------------------------------

function patchAnthropicSDK(capturesOutput: boolean): () => void {
  let anthropic: any;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    anthropic = require('@anthropic-ai/sdk');
  } catch {
    throw new Error('anthropic sdk not available');
  }

  const Messages = anthropic.Messages ?? anthropic?.default?.Messages;
  if (!Messages?.prototype?.create) throw new Error('anthropic: Messages.create not found');

  const originalCreate = Messages.prototype.create;
  Messages.prototype.create = makeLlmWrapper(originalCreate, 'anthropic', getAnthropicModel, capturesOutput);

  return () => {
    Messages.prototype.create = originalCreate;
  };
}

function patchOpenAISDK(capturesOutput: boolean): () => void {
  let openai: any;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    openai = require('openai');
  } catch {
    throw new Error('openai sdk not available');
  }

  const Completions = openai.Completions ?? openai?.default?.Completions;
  if (!Completions?.prototype?.create) throw new Error('openai: Completions.create not found');

  const originalCreate = Completions.prototype.create;
  Completions.prototype.create = makeLlmWrapper(originalCreate, 'openai', getOpenAIModel, capturesOutput);

  return () => {
    Completions.prototype.create = originalCreate;
  };
}

function patchGoogleSDK(capturesOutput: boolean): () => void {
  let genai: any;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    genai = require('@google/generative-ai');
  } catch {
    throw new Error('google sdk not available');
  }

  const GenerativeModel = genai.GenerativeModel ?? genai?.default?.GenerativeModel;
  if (!GenerativeModel?.prototype?.generateContent) throw new Error('google: GenerativeModel.generateContent not found');

  const originalGenContent = GenerativeModel.prototype.generateContent;
  GenerativeModel.prototype.generateContent = makeLlmWrapper(
    originalGenContent,
    'google',
    getGoogleModel,
    capturesOutput,
    extractGoogleAttrs,
  );

  return () => {
    GenerativeModel.prototype.generateContent = originalGenContent;
  };
}

// ---------------------------------------------------------------------------
// Proxy mode helpers
// ---------------------------------------------------------------------------

function applyProxyMode(proxyUrl: string, providers: string[]): void {
  const base = proxyUrl.replace(/\/$/, '');
  for (const name of providers) {
    const envKey = PROXY_ENV_KEYS[name];
    if (!envKey) continue;
    // Only save the original value once (idempotency guard)
    if (!_proxyEnvOverrides.has(envKey)) {
      _proxyEnvOverrides.set(envKey, process.env[envKey]);
    }
    process.env[envKey] = `${base}/v1`;
  }
}

function restoreProxyMode(providers?: string[]): void {
  const envKeys = providers
    ? providers.map((n) => PROXY_ENV_KEYS[n]).filter(Boolean)
    : Array.from(_proxyEnvOverrides.keys());

  for (const envKey of envKeys) {
    if (!_proxyEnvOverrides.has(envKey)) continue;
    const original = _proxyEnvOverrides.get(envKey);
    if (original === undefined) {
      delete process.env[envKey];
    } else {
      process.env[envKey] = original;
    }
    _proxyEnvOverrides.delete(envKey);
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface AutoInstrumentOptions {
  /** Providers to patch. Defaults to all supported: anthropic, openai, google. */
  providers?: string[];
  /** Capture response text in span attributes. Default: false. */
  capturesOutput?: boolean;
  /**
   * Instrumentation mode.
   *  - "direct": Emit OTel spans locally from each SDK call (default).
   *  - "proxy": Rewrite SDK base URLs to route through the AgentWeave proxy.
   */
  mode?: 'direct' | 'proxy';
  /** AgentWeave proxy base URL. Required when mode="proxy". */
  proxyUrl?: string;
}

/**
 * Monkey-patch LLM SDK methods to emit OTel spans automatically.
 *
 * Supports zero-code-change instrumentation for the Anthropic, OpenAI, and
 * Google GenAI SDKs.  Call once at the top of your agent script.
 *
 * @example
 * ```ts
 * import { autoInstrument } from 'agentweave-sdk';
 * autoInstrument(); // instruments all installed SDKs
 * ```
 *
 * @example Proxy mode
 * ```ts
 * autoInstrument({ mode: 'proxy', proxyUrl: 'http://192.168.1.70:30400' });
 * ```
 */
export function autoInstrument(options: AutoInstrumentOptions = {}): void {
  const {
    providers = KNOWN_PROVIDERS,
    capturesOutput = false,
    mode = 'direct',
    proxyUrl,
  } = options;

  if (mode !== 'direct' && mode !== 'proxy') {
    throw new Error(`mode must be 'direct' or 'proxy', got '${mode}'`);
  }
  if (mode === 'proxy' && !proxyUrl) {
    throw new Error("proxyUrl is required when mode='proxy'");
  }

  if (mode === 'proxy') {
    // Only patch providers not already registered (idempotent)
    const newProviders = providers.filter((name) => !_activePatches.has(name));
    if (newProviders.length > 0) {
      applyProxyMode(proxyUrl!, newProviders);
      for (const name of newProviders) {
        _activePatches.set(name, () => undefined);
      }
    }
    return;
  }

  // Direct mode: monkey-patch SDK class methods
  const patchers: Record<string, (co: boolean) => () => void> = {
    anthropic: patchAnthropicSDK,
    openai: patchOpenAISDK,
    google: patchGoogleSDK,
  };

  for (const name of providers) {
    if (_activePatches.has(name)) continue; // idempotent
    const patcher = patchers[name];
    if (!patcher) continue;
    try {
      const unpatch = patcher(capturesOutput);
      _activePatches.set(name, unpatch);
    } catch {
      // SDK not installed or class not found — skip silently
    }
  }
}

/**
 * Restore original SDK methods, undoing ``autoInstrument()``.
 *
 * Also restores any environment variables overridden in proxy mode.
 *
 * @param providers Specific providers to uninstrument (default: all).
 */
export function uninstrument(providers?: string[]): void {
  const targets = providers ?? Array.from(_activePatches.keys());

  for (const name of targets) {
    const unpatch = _activePatches.get(name);
    if (unpatch) {
      unpatch();
      _activePatches.delete(name);
    }
  }

  // Restore proxy env vars for the specified providers (or all if none specified)
  restoreProxyMode(providers);
}

/**
 * Test-only export: exposes the wrapper factory for white-box unit testing.
 *
 * Do NOT use in production code — use ``autoInstrument()`` instead.
 */
export function _makeLlmWrapperForTest(
  original: (...args: any[]) => any,
  provider: string,
  getModel: (self: any, args: any[]) => string,
  capturesOutput: boolean,
  extractAttrs?: (result: any, co: boolean) => Record<string, any>,
): (...args: any[]) => any {
  return makeLlmWrapper(original, provider, getModel, capturesOutput, extractAttrs);
}
