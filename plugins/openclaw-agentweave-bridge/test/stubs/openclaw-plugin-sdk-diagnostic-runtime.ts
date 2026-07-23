// Test-only stub for the host-provided `openclaw/plugin-sdk/diagnostic-runtime`
// module. At runtime in OpenClaw, the host injects the real module; under
// vitest there is nothing to inject, so this stub is aliased in via
// vitest.config.ts.
//
// The test harness in service.test.ts fires events by walking
// `globalThis.__openclawDiagnosticEventsState.listeners` — the same global
// singleton the plugin used to write to directly before the plugin-sdk
// hand-off. We register listeners into that same state so existing tests
// keep working.

interface DiagnosticEventsState {
  listeners: Set<(evt: unknown) => void>
  // Trusted lifecycle channel: session.state / message.queued arrive here paired
  // with a `privateData` bag carrying the seeded upstream `clientContext`. Mirrors
  // the host's `onTrustedDiagnosticEvent` so the harness can exercise the same
  // attribution path the plugin uses in production.
  trustedListeners: Set<(evt: unknown, privateData: unknown) => void>
}

function getState(): DiagnosticEventsState {
  const g = globalThis as Record<string, unknown>
  let state = g.__openclawDiagnosticEventsState as DiagnosticEventsState | undefined
  if (!state) {
    state = { listeners: new Set(), trustedListeners: new Set() }
    g.__openclawDiagnosticEventsState = state
  }
  // Back-compat for any state created before trustedListeners existed.
  if (!state.trustedListeners) state.trustedListeners = new Set()
  return state
}

export function onDiagnosticEvent(listener: (evt: unknown) => void): () => void {
  const state = getState()
  state.listeners.add(listener)
  return () => {
    state.listeners.delete(listener)
  }
}

export function onTrustedDiagnosticEvent(
  listener: (evt: unknown, privateData: unknown) => void,
): () => void {
  const state = getState()
  state.trustedListeners.add(listener)
  return () => {
    state.trustedListeners.delete(listener)
  }
}
