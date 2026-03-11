import { trace, context, SpanAttributes, SpanKind, Span } from '@opentelemetry/api';
import { diag, DiagConsoleLogger, DiagLogLevel } from '@opentelemetry/api';

diag.setLogger(new DiagConsoleLogger(), DiagLogLevel.ERROR);

export const getTracer = () => trace.getTracer('agentweave-tracer');

export const withSpan = <T>(
  name: string,
  attributes: SpanAttributes,
  fn: (span: Span) => T | Promise<T>
): T | Promise<T> => {
  const tracer = getTracer();
  const span = tracer.startSpan(name, { attributes, kind: SpanKind.INTERNAL }, context.active());
  const ctx = trace.setSpan(context.active(), span);

  const finishSync = (result: T): T => {
    span.end();
    return result;
  };

  const handleError = (err: unknown): never => {
    span.recordException(err as Error);
    span.end();
    throw err;
  };

  try {
    const result = context.with(ctx, () => fn(span));
    // Check if result is a Promise (async path)
    if (result != null && typeof (result as any).then === 'function') {
      return (result as Promise<T>).then(
        (val) => { span.end(); return val; },
        (err) => handleError(err),
      ) as Promise<T>;
    }
    // Sync path
    return finishSync(result as T);
  } catch (err) {
    return handleError(err);
  }
};
