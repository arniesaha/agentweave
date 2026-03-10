import { withSpan } from './tracer';
import { PROV } from './schema';

export const traceTool = (name?: string) => {
  return (fn: (...args: any[]) => Promise<any>) => {
    return async (...args: any[]) => {
      return withSpan(`tool:${name ?? fn.name}`, {
        [PROV.ATTRIBUTES.TOOL_NAME]: name,
        [PROV.ATTRIBUTES.TOOL_INPUT]: JSON.stringify(args),
      }, async () => {
        const result = await fn(...args);
        return result;
      });
    };
  };
};

export const traceAgent = (name?: string) => {
  return (fn: (...args: any[]) => Promise<any>) => {
    return async (...args: any[]) => {
      return withSpan(`agent:${name ?? fn.name}`, {
        [PROV.ATTRIBUTES.AGENT_ID]: name,
      }, async () => {
        const result = await fn(...args);
        return result;
      });
    };
  };
};

export const traceLlm = ({ provider, model, capturesInput, capturesOutput }: {
  provider: string;
  model: string;
  capturesInput?: boolean;
  capturesOutput?: boolean;
}) => {
  return (fn: (...args: any[]) => Promise<any>) => {
    return async (...args: any[]) => {
      return withSpan(`llm:${provider}:${model}`, {
        [PROV.ATTRIBUTES.LLM_PROVIDER]: provider,
        [PROV.ATTRIBUTES.LLM_MODEL]: model,
        ...(capturesInput && { [PROV.ATTRIBUTES.TOOL_INPUT]: JSON.stringify(args) }),
      }, async () => {
        const result = await fn(...args);
        if (capturesOutput) {
          // Simulating extraction of output tokens, adjust according to the actual LLM response structure
          const outputTokens = result.usage ? result.usage.output_tokens : undefined;
          return {
            result,
            outputTokens,
          };
        }
        return result;
      });
    };
  };
};