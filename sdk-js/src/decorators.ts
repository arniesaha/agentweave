import { SpanStatusCode, Span } from '@opentelemetry/api';
import { withSpan } from './tracer';
import * as schema from './schema';
import { AgentWeaveConfig } from './config';

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

function extractLlmAttrs(response: any, capturesOutput: boolean): Record<string, any> {
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

  // Stop reason -- Anthropic: response.stop_reason, OpenAI: choices[0].finish_reason
  let stopReason = response?.stop_reason;
  if (stopReason == null && response?.choices?.[0]) {
    stopReason = response.choices[0].finish_reason;
  }
  if (stopReason != null) attrs[schema.PROV_LLM_STOP_REASON] = stopReason;

  // Response preview
  if (capturesOutput) {
    let preview: string | undefined;
    // Anthropic: content[0].text
    if (response?.content?.[0]?.text) {
      preview = response.content[0].text;
    }
    // OpenAI: choices[0].message.content
    if (preview == null && response?.choices?.[0]?.message?.content) {
      preview = response.choices[0].message.content;
    }
    if (preview) attrs[schema.PROV_LLM_RESPONSE_PREVIEW] = preview.slice(0, 512);
  }

  return attrs;
}

// ---------------------------------------------------------------------------
// traceTool
// ---------------------------------------------------------------------------

export function traceTool(name?: string): (fn: (...args: any[]) => any) => (...args: any[]) => any;
export function traceTool(opts: {
  name?: string;
  capturesInput?: boolean;
  capturesOutput?: boolean;
}): (fn: (...args: any[]) => any) => (...args: any[]) => any;
export function traceTool(nameOrOpts?: string | { name?: string; capturesInput?: boolean; capturesOutput?: boolean }) {
  const opts = typeof nameOrOpts === 'string' ? { name: nameOrOpts } : (nameOrOpts ?? {});
  const { name, capturesInput = false, capturesOutput = false } = opts;

  return (fn: (...args: any[]) => any) => {
    const spanName = `${schema.SPAN_PREFIX_TOOL}.${name ?? fn.name}`;
    const wrapper = (...args: any[]) => {
      return withSpan(spanName, {}, (span: Span) => {
        span.setAttribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_TOOL_CALL);
        for (const [k, v] of Object.entries(getConfigAttrs())) {
          span.setAttribute(k, v);
        }
        if (capturesInput) {
          span.setAttribute(schema.PROV_USED, args.length > 0 ? String(args[0]) : JSON.stringify({}));
        }

        const handleResult = (result: any) => {
          if (capturesOutput) {
            span.setAttribute(schema.PROV_WAS_GENERATED_BY, spanName);
            span.setAttribute(`${schema.PROV_ENTITY}.output.value`, String(result));
          }
          return result;
        };

        const handleError = (err: unknown) => {
          span.recordException(err as Error);
          span.setStatus({ code: SpanStatusCode.ERROR, message: String(err) });
          throw err;
        };

        try {
          const result = fn(...args);
          if (result != null && typeof result.then === 'function') {
            return result.then(handleResult, handleError);
          }
          return handleResult(result);
        } catch (err) {
          return handleError(err);
        }
      });
    };
    Object.defineProperty(wrapper, 'name', { value: fn.name });
    return wrapper;
  };
}

// ---------------------------------------------------------------------------
// traceAgent
// ---------------------------------------------------------------------------

export function traceAgent(name?: string): (fn: (...args: any[]) => any) => (...args: any[]) => any;
export function traceAgent(opts: { name?: string }): (fn: (...args: any[]) => any) => (...args: any[]) => any;
export function traceAgent(nameOrOpts?: string | { name?: string }) {
  const opts = typeof nameOrOpts === 'string' ? { name: nameOrOpts } : (nameOrOpts ?? {});
  const { name } = opts;

  return (fn: (...args: any[]) => any) => {
    const spanName = `${schema.SPAN_PREFIX_AGENT}.${name ?? fn.name}`;
    const wrapper = (...args: any[]) => {
      return withSpan(spanName, {}, (span: Span) => {
        span.setAttribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_AGENT_TURN);
        for (const [k, v] of Object.entries(getConfigAttrs())) {
          span.setAttribute(k, v);
        }

        const result = fn(...args);
        return result;
      });
    };
    Object.defineProperty(wrapper, 'name', { value: fn.name });
    return wrapper;
  };
}

// ---------------------------------------------------------------------------
// traceLlm
// ---------------------------------------------------------------------------

export function traceLlm({ provider, model, capturesInput = false, capturesOutput = false }: {
  provider: string;
  model: string;
  capturesInput?: boolean;
  capturesOutput?: boolean;
}) {
  return (fn: (...args: any[]) => any) => {
    const spanName = `${schema.SPAN_PREFIX_LLM}.${model}`;
    const wrapper = (...args: any[]) => {
      return withSpan(spanName, {}, (span: Span) => {
        span.setAttribute(schema.PROV_ACTIVITY_TYPE, schema.ACTIVITY_LLM_CALL);
        span.setAttribute(schema.PROV_LLM_PROVIDER, provider);
        span.setAttribute(schema.PROV_LLM_MODEL, model);
        for (const [k, v] of Object.entries(getConfigAttrs())) {
          span.setAttribute(k, v);
        }
        if (capturesInput) {
          span.setAttribute(schema.PROV_USED, args.length > 0 ? String(args[0]) : JSON.stringify({}));
        }

        const handleResult = (result: any) => {
          const llmAttrs = extractLlmAttrs(result, capturesOutput);
          for (const [k, v] of Object.entries(llmAttrs)) {
            span.setAttribute(k, v);
          }
          return result;
        };

        const handleError = (err: unknown) => {
          span.recordException(err as Error);
          span.setStatus({ code: SpanStatusCode.ERROR, message: String(err) });
          throw err;
        };

        try {
          const result = fn(...args);
          if (result != null && typeof result.then === 'function') {
            return result.then(handleResult, handleError);
          }
          return handleResult(result);
        } catch (err) {
          return handleError(err);
        }
      });
    };
    Object.defineProperty(wrapper, 'name', { value: fn.name });
    return wrapper;
  };
}
