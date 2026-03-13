import { traceTool, traceAgent, traceLlm } from '../src/decorators';
import { AgentWeaveConfig } from '../src/config';
import { BasicTracerProvider, SimpleSpanProcessor, InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { AsyncLocalStorageContextManager } from '@opentelemetry/context-async-hooks';
import { SpanStatusCode } from '@opentelemetry/api';
import * as schema from '../src/schema';

const contextManager = new AsyncLocalStorageContextManager();
const provider = new BasicTracerProvider();
const exporter = new InMemorySpanExporter();
provider.addSpanProcessor(new SimpleSpanProcessor(exporter));
provider.register({ contextManager });

beforeEach(() => {
  exporter.reset();
  AgentWeaveConfig.enabled = false;
});

describe('schema constants', () => {
  it('uses dot separators and snake_case', () => {
    expect(schema.PROV_ACTIVITY_TYPE).toBe('prov.activity.type');
    expect(schema.PROV_LLM_PROMPT_TOKENS).toBe('prov.llm.prompt_tokens');
    expect(schema.PROV_LLM_COMPLETION_TOKENS).toBe('prov.llm.completion_tokens');
    expect(schema.PROV_LLM_TOTAL_TOKENS).toBe('prov.llm.total_tokens');
    expect(schema.PROV_AGENT_ID).toBe('prov.agent.id');
    expect(schema.PROV_WAS_ASSOCIATED_WITH).toBe('prov.wasAssociatedWith');
    expect(schema.ACTIVITY_TOOL_CALL).toBe('tool_call');
    expect(schema.ACTIVITY_AGENT_TURN).toBe('agent_turn');
    expect(schema.ACTIVITY_LLM_CALL).toBe('llm_call');
  });
});

describe('traceTool', () => {
  it('creates a span with correct name and activity type (async)', async () => {
    const tracedFn = traceTool('testTool')(async (arg: string) => arg);
    const result = await tracedFn('hello');

    expect(result).toBe('hello');
    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(1);
    expect(spans[0].name).toBe('tool.testTool');
    expect(spans[0].attributes[schema.PROV_ACTIVITY_TYPE]).toBe(schema.ACTIVITY_TOOL_CALL);
  });

  it('creates a span with correct name and activity type (sync)', () => {
    const tracedFn = traceTool('syncTool')((arg: string) => arg.toUpperCase());
    const result = tracedFn('hello');

    expect(result).toBe('HELLO');
    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(1);
    expect(spans[0].name).toBe('tool.syncTool');
    expect(spans[0].attributes[schema.PROV_ACTIVITY_TYPE]).toBe(schema.ACTIVITY_TOOL_CALL);
  });

  it('captures input and output when configured', async () => {
    const tracedFn = traceTool({ name: 'ioTool', capturesInput: true, capturesOutput: true })(
      async (arg: string) => `result:${arg}`
    );
    const result = await tracedFn('data');

    expect(result).toBe('result:data');
    const spans = exporter.getFinishedSpans();
    expect(spans[0].attributes[schema.PROV_USED]).toBe('data');
    expect(spans[0].attributes[`${schema.PROV_ENTITY}.output.value`]).toBe('result:data');
  });
});

describe('traceAgent', () => {
  it('creates a span with correct name and activity type (async)', async () => {
    const tracedFn = traceAgent('testAgent')(async (arg: string) => arg);
    const result = await tracedFn('hello');

    expect(result).toBe('hello');
    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(1);
    expect(spans[0].name).toBe('agent.testAgent');
    expect(spans[0].attributes[schema.PROV_ACTIVITY_TYPE]).toBe(schema.ACTIVITY_AGENT_TURN);
  });

  it('creates a span with correct name and activity type (sync)', () => {
    const tracedFn = traceAgent('syncAgent')((arg: string) => arg);
    const result = tracedFn('hello');

    expect(result).toBe('hello');
    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(1);
    expect(spans[0].name).toBe('agent.syncAgent');
    expect(spans[0].attributes[schema.PROV_ACTIVITY_TYPE]).toBe(schema.ACTIVITY_AGENT_TURN);
  });
});

describe('traceLlm', () => {
  it('creates a span with provider/model and extracts token counts', async () => {
    const tracedFn = traceLlm({
      provider: 'anthropic',
      model: 'claude-sonnet-4-6',
      capturesOutput: true,
    })(async () => ({
      usage: { input_tokens: 100, output_tokens: 150 },
      stop_reason: 'end_turn',
      content: [{ text: 'Hello from Claude' }],
    }));

    const result = await tracedFn('prompt text');

    // Issue #17: result should be the original object, not wrapped
    expect(result.usage.input_tokens).toBe(100);
    expect(result.usage.output_tokens).toBe(150);

    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(1);
    expect(spans[0].name).toBe('llm.claude-sonnet-4-6');
    expect(spans[0].attributes[schema.PROV_ACTIVITY_TYPE]).toBe(schema.ACTIVITY_LLM_CALL);
    expect(spans[0].attributes[schema.PROV_LLM_PROVIDER]).toBe('anthropic');
    expect(spans[0].attributes[schema.PROV_LLM_MODEL]).toBe('claude-sonnet-4-6');
    expect(spans[0].attributes[schema.PROV_LLM_PROMPT_TOKENS]).toBe(100);
    expect(spans[0].attributes[schema.PROV_LLM_COMPLETION_TOKENS]).toBe(150);
    expect(spans[0].attributes[schema.PROV_LLM_TOTAL_TOKENS]).toBe(250);
    expect(spans[0].attributes[schema.PROV_LLM_STOP_REASON]).toBe('end_turn');
    expect(spans[0].attributes[schema.PROV_LLM_RESPONSE_PREVIEW]).toBe('Hello from Claude');
  });

  it('works with sync functions', () => {
    const tracedFn = traceLlm({
      provider: 'openai',
      model: 'gpt-4',
    })(() => ({
      usage: { prompt_tokens: 50, completion_tokens: 80 },
      choices: [{ finish_reason: 'stop', message: { content: 'Hi' } }],
    }));

    const result = tracedFn();

    expect(result.usage.prompt_tokens).toBe(50);
    const spans = exporter.getFinishedSpans();
    expect(spans[0].name).toBe('llm.gpt-4');
    expect(spans[0].attributes[schema.PROV_LLM_PROMPT_TOKENS]).toBe(50);
    expect(spans[0].attributes[schema.PROV_LLM_COMPLETION_TOKENS]).toBe(80);
    expect(spans[0].attributes[schema.PROV_LLM_TOTAL_TOKENS]).toBe(130);
    expect(spans[0].attributes[schema.PROV_LLM_STOP_REASON]).toBe('stop');
  });
});

describe('config wiring', () => {
  it('sets agent identity attributes from config on every span', async () => {
    AgentWeaveConfig.agentId = 'my-agent';
    AgentWeaveConfig.agentModel = 'claude-sonnet-4-6';
    AgentWeaveConfig.agentVersion = '1.0.0';
    AgentWeaveConfig.enabled = true;

    const tracedFn = traceTool('configTool')(async () => 'ok');
    await tracedFn();

    const spans = exporter.getFinishedSpans();
    expect(spans[0].attributes[schema.PROV_AGENT_ID]).toBe('my-agent');
    expect(spans[0].attributes[schema.PROV_AGENT_MODEL]).toBe('claude-sonnet-4-6');
    expect(spans[0].attributes[schema.PROV_AGENT_VERSION]).toBe('1.0.0');
    expect(spans[0].attributes[schema.PROV_WAS_ASSOCIATED_WITH]).toBe('my-agent');
  });
});

describe('context propagation', () => {
  it('nested spans form parent-child hierarchy', async () => {
    const inner = traceTool('child')(async () => 'inner-result');
    const outer = traceAgent('parent')(async () => {
      return inner();
    });

    await outer();

    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(2);

    const childSpan = spans.find(s => s.name === 'tool.child')!;
    const parentSpan = spans.find(s => s.name === 'agent.parent')!;

    expect(childSpan).toBeDefined();
    expect(parentSpan).toBeDefined();
    // Child's parent span ID should match the parent's span ID
    expect(childSpan.parentSpanId).toBe(parentSpan.spanContext().spanId);
  });

  it('deep nesting: agent → llm → tool forms 3-level parent chain', async () => {
    const tool = traceTool('deepTool')(async () => 'tool-result');
    const llm = traceLlm({ provider: 'anthropic', model: 'claude-sonnet-4-6' })(async () => {
      const r = await tool();
      return { usage: { input_tokens: 10, output_tokens: 20 }, stop_reason: 'end_turn', content: [{ text: r }] };
    });
    const agent = traceAgent('deepAgent')(async () => {
      return llm();
    });

    await agent();

    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(3);

    const toolSpan = spans.find(s => s.name === 'tool.deepTool')!;
    const llmSpan = spans.find(s => s.name === 'llm.claude-sonnet-4-6')!;
    const agentSpan = spans.find(s => s.name === 'agent.deepAgent')!;

    expect(toolSpan).toBeDefined();
    expect(llmSpan).toBeDefined();
    expect(agentSpan).toBeDefined();

    expect(toolSpan.parentSpanId).toBe(llmSpan.spanContext().spanId);
    expect(llmSpan.parentSpanId).toBe(agentSpan.spanContext().spanId);
  });
});

describe('error handling', () => {
  describe('traceTool', () => {
    it('re-throws error and records ERROR status on span (async)', async () => {
      const err = new Error('tool-async-fail');
      const tracedFn = traceTool('failTool')(async () => { throw err; });

      await expect(tracedFn()).rejects.toThrow('tool-async-fail');

      const spans = exporter.getFinishedSpans();
      expect(spans.length).toBe(1);
      expect(spans[0].status.code).toBe(SpanStatusCode.ERROR);
      expect(spans[0].events.length).toBeGreaterThanOrEqual(1);
      expect(spans[0].events.some(e => e.name === 'exception')).toBe(true);
    });

    it('re-throws error and records ERROR status on span (sync)', () => {
      const err = new Error('tool-sync-fail');
      const tracedFn = traceTool('failToolSync')(() => { throw err; });

      expect(() => tracedFn()).toThrow('tool-sync-fail');

      const spans = exporter.getFinishedSpans();
      expect(spans.length).toBe(1);
      expect(spans[0].status.code).toBe(SpanStatusCode.ERROR);
      expect(spans[0].events.some(e => e.name === 'exception')).toBe(true);
    });
  });

  describe('traceLlm', () => {
    it('re-throws error and records ERROR status on span (async)', async () => {
      const tracedFn = traceLlm({ provider: 'anthropic', model: 'claude-sonnet-4-6' })(
        async () => { throw new Error('llm-async-fail'); }
      );

      await expect(tracedFn()).rejects.toThrow('llm-async-fail');

      const spans = exporter.getFinishedSpans();
      expect(spans.length).toBe(1);
      expect(spans[0].status.code).toBe(SpanStatusCode.ERROR);
      expect(spans[0].events.some(e => e.name === 'exception')).toBe(true);
    });

    it('re-throws error and records ERROR status on span (sync)', () => {
      const tracedFn = traceLlm({ provider: 'openai', model: 'gpt-4' })(
        () => { throw new Error('llm-sync-fail'); }
      );

      expect(() => tracedFn()).toThrow('llm-sync-fail');

      const spans = exporter.getFinishedSpans();
      expect(spans.length).toBe(1);
      expect(spans[0].status.code).toBe(SpanStatusCode.ERROR);
      expect(spans[0].events.some(e => e.name === 'exception')).toBe(true);
    });
  });

  describe('traceAgent', () => {
    it('re-throws error and records exception on span (async)', async () => {
      const tracedFn = traceAgent('failAgent')(async () => { throw new Error('agent-async-fail'); });

      await expect(tracedFn()).rejects.toThrow('agent-async-fail');

      const spans = exporter.getFinishedSpans();
      expect(spans.length).toBe(1);
      // traceAgent does not call span.setStatus(ERROR) itself — withSpan only records the exception
      expect(spans[0].events.some(e => e.name === 'exception')).toBe(true);
    });

    it('re-throws error and records exception on span (sync)', () => {
      const tracedFn = traceAgent('failAgentSync')(() => { throw new Error('agent-sync-fail'); });

      expect(() => tracedFn()).toThrow('agent-sync-fail');

      const spans = exporter.getFinishedSpans();
      expect(spans.length).toBe(1);
      expect(spans[0].events.some(e => e.name === 'exception')).toBe(true);
    });
  });
});

describe('traceTool — sync I/O capture', () => {
  it('captures input and output on sync functions', () => {
    const tracedFn = traceTool({ name: 'syncIoTool', capturesInput: true, capturesOutput: true })(
      (arg: string) => `sync:${arg}`
    );
    const result = tracedFn('data');

    expect(result).toBe('sync:data');
    const spans = exporter.getFinishedSpans();
    expect(spans[0].attributes[schema.PROV_USED]).toBe('data');
    expect(spans[0].attributes[`${schema.PROV_ENTITY}.output.value`]).toBe('sync:data');
  });
});

describe('traceLlm — edge cases', () => {
  it('handles missing usage gracefully without crashing', async () => {
    const tracedFn = traceLlm({ provider: 'anthropic', model: 'claude-sonnet-4-6' })(
      async () => ({ stop_reason: 'end_turn', content: [{ text: 'no usage' }] })
    );

    const result = await tracedFn();
    expect(result.stop_reason).toBe('end_turn');

    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(1);
    expect(spans[0].attributes[schema.PROV_LLM_PROMPT_TOKENS]).toBeUndefined();
    expect(spans[0].attributes[schema.PROV_LLM_COMPLETION_TOKENS]).toBeUndefined();
    expect(spans[0].attributes[schema.PROV_LLM_TOTAL_TOKENS]).toBeUndefined();
    expect(spans[0].attributes[schema.PROV_LLM_STOP_REASON]).toBe('end_turn');
  });

  it('captures prompt input via capturesInput', async () => {
    const tracedFn = traceLlm({
      provider: 'anthropic',
      model: 'claude-sonnet-4-6',
      capturesInput: true,
    })(async () => ({
      usage: { input_tokens: 10, output_tokens: 20 },
      stop_reason: 'end_turn',
    }));

    await tracedFn('What is 2+2?');

    const spans = exporter.getFinishedSpans();
    expect(spans[0].attributes[schema.PROV_USED]).toBe('What is 2+2?');
  });
});
