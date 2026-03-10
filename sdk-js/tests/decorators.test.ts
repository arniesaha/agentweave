import { traceTool, traceAgent, traceLlm } from '../src/decorators';
import { BasicTracerProvider, SimpleSpanProcessor, InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';

const provider = new BasicTracerProvider();
const exporter = new InMemorySpanExporter();
provider.addSpanProcessor(new SimpleSpanProcessor(exporter));
provider.register();

beforeEach(() => {
  exporter.reset();
});

describe('tracing decorators', () => {
  it('traceTool should create a span with proper attributes', async () => {
    const tracedFn = traceTool('testTool')(async (arg) => arg);
    await tracedFn('hello');

    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(1);
    expect(spans[0].attributes['prov:tool.name']).toBe('testTool');
    expect(spans[0].attributes['prov:tool.input']).toBe(JSON.stringify(['hello']));
  });

  it('traceAgent should create a span with proper attributes', async () => {
    const tracedFn = traceAgent('testAgent')(async (arg) => arg);
    await tracedFn('hello');

    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(1);
    expect(spans[0].attributes['prov:agent.id']).toBe('testAgent');
  });

  it('traceLlm should create a span with proper attributes and input/output', async () => {
    const tracedFn = traceLlm({
      provider: 'testProvider',
      model: 'testModel',
      capturesInput: true,
      capturesOutput: true,
    })(async () => ({ usage: { output_tokens: 150 } }));

    const result = await tracedFn('prompt text');

    const spans = exporter.getFinishedSpans();
    expect(spans.length).toBe(1);
    expect(spans[0].attributes['prov:llm.provider']).toBe('testProvider');
    expect(spans[0].attributes['prov:llm.model']).toBe('testModel');
    expect(spans[0].attributes['prov:tool.input']).toBe(JSON.stringify(['prompt text']));
    expect(result.outputTokens).toBe(150);
  });
});