export { AgentWeaveConfig } from './config';
export { getTracer, withSpan, shutdownHandler } from './tracer';
export { traceTool, traceAgent, traceLlm } from './decorators';
export * from './schema';

// ── Graceful shutdown: register Node.js signal handlers ──────────────────────

import { shutdownHandler } from './tracer';

let _signalHandlersRegistered = false;

/**
 * Register SIGTERM and SIGINT handlers that close all open spans and flush
 * the OTel exporter before the process exits.  Idempotent — safe to call
 * multiple times.
 */
function _registerSignalHandlers(): void {
  if (_signalHandlersRegistered) return;
  _signalHandlersRegistered = true;

  process.on('SIGTERM', () => {
    shutdownHandler('sigterm');
    process.exit(0);
  });

  process.on('SIGINT', () => {
    shutdownHandler('sigint');
    process.exit(1);
  });
}

_registerSignalHandlers();
