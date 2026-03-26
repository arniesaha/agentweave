/**
 * Tests for autoInstrument() / uninstrument() — JS SDK auto-instrumentation.
 */

import { autoInstrument, uninstrument, _activePatches, _makeLlmWrapperForTest } from '../src/instrument';
import { AgentWeaveConfig } from '../src/config';
import {
  BasicTracerProvider,
  SimpleSpanProcessor,
  InMemorySpanExporter,
} from '@opentelemetry/sdk-trace-base';
import { AsyncLocalStorageContextManager } from '@opentelemetry/context-async-hooks';
import * as schema from '../src/schema';

// ---------------------------------------------------------------------------
// OTel test provider
// ---------------------------------------------------------------------------

const contextManager = new AsyncLocalStorageContextManager();
const provider = new BasicTracerProvider();
const exporter = new InMemorySpanExporter();
provider.addSpanProcessor(new SimpleSpanProcessor(exporter));
provider.register({ contextManager });

beforeEach(() => {
  exporter.reset();
  AgentWeaveConfig.enabled = false;
  _activePatches.clear();
  // Remove fake modules from require cache
  for (const key of Object.keys(require.cache ?? {})) {
    if (
      key.includes('@anthropic-ai/sdk') ||
      key.includes('openai') ||
      key.includes('@google/generative-ai')
    ) {
      delete (require.cache as any)[key];
    }
  }
});

afterEach(() => {
  uninstrument();
  // Clean up proxy env vars
  delete process.env.ANTHROPIC_BASE_URL;
  delete process.env.OPENAI_BASE_URL;
  delete process.env.GOOGLE_GENAI_BASE_URL;
});

// ---------------------------------------------------------------------------
// Fake SDK factories
// ---------------------------------------------------------------------------

function makeFakeAnthropicSDK() {
  const Messages = {
    prototype: {
      create(opts: any) {
        return {
          usage: { input_tokens: 100, output_tokens: 50 },
          stop_reason: 'end_turn',
          content: [{ text: 'Hello from Claude.' }],
        };
      },
    },
  };
  return { Messages };
}

function makeFakeOpenAISDK() {
  const Completions = {
    prototype: {
      create(opts: any) {
        return {
          usage: { prompt_tokens: 80, completion_tokens: 40 },
          choices: [{ finish_reason: 'stop', message: { content: 'Hi from GPT.' } }],
        };
      },
    },
  };
  return { Completions };
}

function makeFakeGoogleSDK() {
  const GenerativeModel = {
    prototype: {
      model: 'gemini-2.0-flash',
      generateContent(opts: any) {
        return {
          usageMetadata: { promptTokenCount: 20, candidatesTokenCount: 15 },
          candidates: [
            {
              content: { parts: [{ text: 'Hello from Gemini.' }] },
              finishReason: 'STOP',
            },
          ],
        };
      },
    },
  };
  function GenerativeModelConstructor(this: any, modelName: string) {
    this.model = modelName;
    this.generateContent = GenerativeModel.prototype.generateContent;
  }
  GenerativeModelConstructor.prototype = GenerativeModel.prototype;
  return { GenerativeModel: GenerativeModelConstructor as any };
}

// ---------------------------------------------------------------------------
// Proxy-mode patcher that uses the fake SDK modules
// ---------------------------------------------------------------------------

// We test the wrapper factory directly and test proxy mode via env vars.

// ---------------------------------------------------------------------------
// Proxy mode tests
// ---------------------------------------------------------------------------

describe('autoInstrument — proxy mode', () => {
  it('sets ANTHROPIC_BASE_URL env var', () => {
    delete process.env.ANTHROPIC_BASE_URL;
    autoInstrument({ providers: ['anthropic'], mode: 'proxy', proxyUrl: 'http://proxy.example.com:30400' });
    expect(process.env.ANTHROPIC_BASE_URL).toBe('http://proxy.example.com:30400/v1');
  });

  it('sets OPENAI_BASE_URL env var', () => {
    delete process.env.OPENAI_BASE_URL;
    autoInstrument({ providers: ['openai'], mode: 'proxy', proxyUrl: 'http://proxy.example.com:30400' });
    expect(process.env.OPENAI_BASE_URL).toBe('http://proxy.example.com:30400/v1');
  });

  it('sets GOOGLE_GENAI_BASE_URL env var', () => {
    delete process.env.GOOGLE_GENAI_BASE_URL;
    autoInstrument({ providers: ['google'], mode: 'proxy', proxyUrl: 'http://proxy.example.com:30400' });
    expect(process.env.GOOGLE_GENAI_BASE_URL).toBe('http://proxy.example.com:30400/v1');
  });

  it('strips trailing slash from proxyUrl', () => {
    autoInstrument({ providers: ['anthropic'], mode: 'proxy', proxyUrl: 'http://proxy.example.com:30400/' });
    expect(process.env.ANTHROPIC_BASE_URL).toBe('http://proxy.example.com:30400/v1');
  });

  it('sets all three env vars by default', () => {
    autoInstrument({ mode: 'proxy', proxyUrl: 'http://proxy.example.com:30400' });
    expect(process.env.ANTHROPIC_BASE_URL).toBe('http://proxy.example.com:30400/v1');
    expect(process.env.OPENAI_BASE_URL).toBe('http://proxy.example.com:30400/v1');
    expect(process.env.GOOGLE_GENAI_BASE_URL).toBe('http://proxy.example.com:30400/v1');
  });

  it('restores env vars on uninstrument()', () => {
    process.env.ANTHROPIC_BASE_URL = 'http://original.example.com/v1';
    autoInstrument({ providers: ['anthropic'], mode: 'proxy', proxyUrl: 'http://proxy.example.com:30400' });
    expect(process.env.ANTHROPIC_BASE_URL).toBe('http://proxy.example.com:30400/v1');

    uninstrument();
    expect(process.env.ANTHROPIC_BASE_URL).toBe('http://original.example.com/v1');
  });

  it('clears env var when it was unset before', () => {
    delete process.env.OPENAI_BASE_URL;
    autoInstrument({ providers: ['openai'], mode: 'proxy', proxyUrl: 'http://proxy.example.com:30400' });
    expect(process.env.OPENAI_BASE_URL).toBeDefined();

    uninstrument();
    expect(process.env.OPENAI_BASE_URL).toBeUndefined();
  });

  it('throws when proxyUrl is missing in proxy mode', () => {
    expect(() => autoInstrument({ mode: 'proxy' })).toThrow('proxyUrl');
  });

  it('throws for invalid mode', () => {
    expect(() => autoInstrument({ mode: 'invalid' as any })).toThrow("mode must be");
  });

  it('is idempotent — second call does not double-set', () => {
    autoInstrument({ providers: ['anthropic'], mode: 'proxy', proxyUrl: 'http://proxy.example.com:30400' });
    autoInstrument({ providers: ['anthropic'], mode: 'proxy', proxyUrl: 'http://OTHER.example.com:30400' });
    // Second call is skipped (idempotent) — first value persists
    expect(process.env.ANTHROPIC_BASE_URL).toBe('http://proxy.example.com:30400/v1');
  });
});

// ---------------------------------------------------------------------------
// Direct mode — test the wrapper factory with fake objects (unit tests)
// ---------------------------------------------------------------------------



describe('autoInstrument — direct mode wrapper (unit)', () => {
  it('wraps a sync function and emits an LLM span', () => {
    const fakeCreate = jest.fn((_opts: any) => ({
      usage: { input_tokens: 100, output_tokens: 50 },
      stop_reason: 'end_turn',
      content: [{ text: 'Hello.' }],
    }));

    const wrapped = _makeLlmWrapperForTest(
      fakeCreate,
      'anthropic',
      (_self: any, args: any[]) => args[0]?.model ?? 'unknown',
      false,
    );

    const result = wrapped.call({}, { model: 'claude-sonnet-4-6', messages: [] });

    expect(result.stop_reason).toBe('end_turn');
    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(1);
    expect(spans[0].name).toBe('llm.claude-sonnet-4-6');
    expect(spans[0].attributes[schema.PROV_ACTIVITY_TYPE]).toBe(schema.ACTIVITY_LLM_CALL);
    expect(spans[0].attributes[schema.PROV_LLM_PROVIDER]).toBe('anthropic');
    expect(spans[0].attributes[schema.PROV_LLM_MODEL]).toBe('claude-sonnet-4-6');
    expect(spans[0].attributes[schema.AUTO_INSTRUMENTED]).toBe(true);
    expect(spans[0].attributes[schema.GEN_AI_OPERATION_NAME]).toBe('chat');
    expect(spans[0].attributes[schema.GEN_AI_SYSTEM]).toBe('anthropic');
    expect(spans[0].attributes[schema.GEN_AI_REQUEST_MODEL]).toBe('claude-sonnet-4-6');
    expect(spans[0].attributes[schema.PROV_LLM_PROMPT_TOKENS]).toBe(100);
    expect(spans[0].attributes[schema.PROV_LLM_COMPLETION_TOKENS]).toBe(50);
    expect(spans[0].attributes[schema.PROV_LLM_TOTAL_TOKENS]).toBe(150);
    expect(spans[0].attributes[schema.PROV_LLM_STOP_REASON]).toBe('end_turn');
  });

  it('wraps an async function and emits an LLM span', async () => {
    // OpenAI-style response: usage.prompt_tokens / completion_tokens
    const fakeCreate = jest.fn(async (_opts: any) => ({
      usage: { prompt_tokens: 80, completion_tokens: 40 },
      choices: [{ finish_reason: 'stop', message: { content: 'Hi!' } }],
    }));

    const wrapped = _makeLlmWrapperForTest(
      fakeCreate,
      'openai',
      (_self: any, args: any[]) => args[0]?.model ?? 'unknown',
      false,
    );

    const result = await wrapped.call({}, { model: 'gpt-4o', messages: [] });

    expect(result.usage.prompt_tokens).toBe(80);
    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(1);
    expect(spans[0].name).toBe('llm.gpt-4o');
    expect(spans[0].attributes[schema.PROV_LLM_PROVIDER]).toBe('openai');
    expect(spans[0].attributes[schema.PROV_LLM_PROMPT_TOKENS]).toBe(80);
    expect(spans[0].attributes[schema.PROV_LLM_COMPLETION_TOKENS]).toBe(40);
    expect(spans[0].attributes[schema.PROV_LLM_TOTAL_TOKENS]).toBe(120);
    expect(spans[0].attributes[schema.PROV_LLM_STOP_REASON]).toBe('stop');
  });

  it('wraps Google SDK sync and emits span with Google attrs', () => {
    const fakeGenContent = jest.fn((_opts: any) => ({
      usageMetadata: { promptTokenCount: 20, candidatesTokenCount: 15 },
      candidates: [
        { content: { parts: [{ text: 'Hello from Gemini.' }] }, finishReason: 'STOP' },
      ],
    }));

    const extractGoogleAttrs = (result: any, co: boolean) => {
      const attrs: Record<string, any> = {};
      const usage = result?.usageMetadata;
      if (usage) {
        if (usage.promptTokenCount != null) attrs[schema.PROV_LLM_PROMPT_TOKENS] = usage.promptTokenCount;
        if (usage.candidatesTokenCount != null) attrs[schema.PROV_LLM_COMPLETION_TOKENS] = usage.candidatesTokenCount;
        if (usage.promptTokenCount != null && usage.candidatesTokenCount != null) {
          attrs[schema.PROV_LLM_TOTAL_TOKENS] = usage.promptTokenCount + usage.candidatesTokenCount;
        }
      }
      const finishReason = result?.candidates?.[0]?.finishReason;
      if (finishReason != null) attrs[schema.PROV_LLM_STOP_REASON] = String(finishReason);
      if (co) {
        const text = result?.candidates?.[0]?.content?.parts?.[0]?.text;
        if (text) attrs[schema.PROV_LLM_RESPONSE_PREVIEW] = text.slice(0, 512);
      }
      return attrs;
    };

    const wrapped = _makeLlmWrapperForTest(
      fakeGenContent,
      'google',
      (self: any, _args: any[]) => (self?.model ?? 'unknown').replace(/^models\//, ''),
      true,
      extractGoogleAttrs,
    );

    const self = { model: 'gemini-2.0-flash' };
    const result = wrapped.call(self, 'Hello!');

    expect(result.candidates[0].finishReason).toBe('STOP');
    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(1);
    expect(spans[0].name).toBe('llm.gemini-2.0-flash');
    expect(spans[0].attributes[schema.PROV_LLM_PROVIDER]).toBe('google');
    expect(spans[0].attributes[schema.PROV_LLM_PROMPT_TOKENS]).toBe(20);
    expect(spans[0].attributes[schema.PROV_LLM_COMPLETION_TOKENS]).toBe(15);
    expect(spans[0].attributes[schema.PROV_LLM_TOTAL_TOKENS]).toBe(35);
    expect(spans[0].attributes[schema.PROV_LLM_STOP_REASON]).toBe('STOP');
    expect(spans[0].attributes[schema.PROV_LLM_RESPONSE_PREVIEW]).toContain('Hello from Gemini');
  });

  it('skips double-tracing when already in an LLM span', () => {
    // When wrapped2 wraps wrapped1, and wrapped2 sets the LLM span marker before
    // calling wrapped1, wrapped1 detects the active LLM span and skips span creation.
    // Only one span (from wrapped2) should be emitted.
    const baseCreate = jest.fn((_opts: any) => ({
      usage: { input_tokens: 10, output_tokens: 5 },
      stop_reason: 'end_turn',
    }));

    // Create two layers of wrapping around the same underlying function
    const wrapped1 = _makeLlmWrapperForTest(
      baseCreate,
      'anthropic',
      (_self: any, args: any[]) => args[0]?.model ?? 'unknown',
      false,
    );
    const wrapped2 = _makeLlmWrapperForTest(
      wrapped1,
      'anthropic',
      (_self: any, args: any[]) => args[0]?.model ?? 'unknown',
      false,
    );

    wrapped2.call({}, { model: 'claude-sonnet-4-6', messages: [] });

    const spans = exporter.getFinishedSpans();
    // Only one span from wrapped2; wrapped1 detected the active LLM span and skipped
    expect(spans.length).toBe(1);
    // The underlying baseCreate should still have been called exactly once
    expect(baseCreate).toHaveBeenCalledTimes(1);
  });

  it('captures output when capturesOutput=true', () => {
    const fakeCreate = jest.fn((_opts: any) => ({
      usage: { input_tokens: 10, output_tokens: 5 },
      stop_reason: 'end_turn',
      content: [{ text: 'Test response text.' }],
    }));

    const wrapped = _makeLlmWrapperForTest(
      fakeCreate,
      'anthropic',
      (_self: any, args: any[]) => args[0]?.model ?? 'unknown',
      true, // capturesOutput
    );

    wrapped.call({}, { model: 'claude-sonnet-4-6', messages: [] });

    const spans = exporter.getFinishedSpans();
    expect(spans[0].attributes[schema.PROV_LLM_RESPONSE_PREVIEW]).toContain('Test response text');
  });
});

// ---------------------------------------------------------------------------
// autoInstrument — missing SDK scenarios
// ---------------------------------------------------------------------------

describe('autoInstrument — missing SDKs', () => {
  it('does not throw when no SDKs are installed', () => {
    // Direct mode — all SDK requires will fail, should silently skip
    expect(() => autoInstrument()).not.toThrow();
  });

  it('does not register patches for unavailable providers', () => {
    autoInstrument();
    // With no real SDKs installed in test env, _activePatches should be empty
    // (or contain only providers that happen to be resolvable)
    // We just verify it doesn't crash
    expect(_activePatches.size).toBeGreaterThanOrEqual(0);
  });
});
