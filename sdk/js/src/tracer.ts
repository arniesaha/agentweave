import { trace, context, SpanAttributes, SpanKind, Span, SpanStatusCode } from '@opentelemetry/api';
import { diag, DiagConsoleLogger, DiagLogLevel } from '@opentelemetry/api';

diag.setLogger(new DiagConsoleLogger(), DiagLogLevel.ERROR);

export const getTracer = () => trace.getTracer('agentweave-tracer');

// ── Open span tracking for graceful shutdown ──────────────────────────────────

/** All currently in-flight spans, keyed by a unique string ID. */
export const _openSpans: Map<string, Span> = new Map();

let _shutdownCalled = false;
let _spanCounter = 0;

/**
 * Close all in-flight spans with ABORTED status and flush the exporter.
 *
 * Called automatically by SIGTERM/SIGINT handlers registered in index.ts,
 * and can also be called manually via `AgentWeaveConfig.shutdown()`.
 */
export function shutdownHandler(reason: string = 'atexit'): void {
  if (_shutdownCalled) return;
  _shutdownCalled = true;

  for (const [key, span] of _openSpans) {
    try {
      span.setAttribute('shutdown.reason', reason);
      span.setStatus({ code: SpanStatusCode.ERROR, message: `Process aborted: ${reason}` });
      span.end();
    } catch (_) {
      // best-effort — never throw from a shutdown handler
    }
  }
  _openSpans.clear();
}

/** Reset shutdown state — used in tests only. */
export function _resetShutdownState(): void {
  _openSpans.clear();
  _shutdownCalled = false;
}

// ── withSpan ─────────────────────────────────────────────────────────────────

export const withSpan = <T>(
  name: string,
  attributes: SpanAttributes,
  fn: (span: Span) => T | Promise<T>
): T | Promise<T> => {
  const tracer = getTracer();
  const span = tracer.startSpan(name, { attributes, kind: SpanKind.INTERNAL }, context.active());
  const ctx = trace.setSpan(context.active(), span);

  // Register in open span tracking
  const spanKey = `${++_spanCounter}`;
  _openSpans.set(spanKey, span);

  const finishSync = (result: T): T => {
    _openSpans.delete(spanKey);
    span.end();
    return result;
  };

  const handleError = (err: unknown): never => {
    _openSpans.delete(spanKey);
    span.recordException(err as Error);
    span.end();
    throw err;
  };

  try {
    const result = context.with(ctx, () => fn(span));
    // Check if result is a Promise (async path)
    if (result != null && typeof (result as any).then === 'function') {
      return (result as Promise<T>).then(
        (val) => { _openSpans.delete(spanKey); span.end(); return val; },
        (err) => handleError(err),
      ) as Promise<T>;
    }
    // Sync path
    return finishSync(result as T);
  } catch (err) {
    return handleError(err);
  }
};
