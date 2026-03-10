import { trace, context, SpanAttributes, SpanKind } from '@opentelemetry/api';
import { diag, DiagConsoleLogger, DiagLogLevel } from '@opentelemetry/api';

diag.setLogger(new DiagConsoleLogger(), DiagLogLevel.ERROR);

export const getTracer = () => trace.getTracer('agentweave-tracer');

export const withSpan = async (
  name: string,
  attributes: SpanAttributes,
  fn: () => Promise<any>
) => {
  const tracer = getTracer();
  const span = tracer.startSpan(name, { attributes, kind: SpanKind.INTERNAL }, context.active());
  try {
    return await fn();
  } catch (err) {
    span.recordException(err as Error);
    throw err;
  } finally {
    span.end();
  }
};