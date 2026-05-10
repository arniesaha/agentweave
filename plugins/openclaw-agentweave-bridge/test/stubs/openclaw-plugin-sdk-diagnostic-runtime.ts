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
}

function getState(): DiagnosticEventsState {
  const g = globalThis as Record<string, unknown>
  let state = g.__openclawDiagnosticEventsState as DiagnosticEventsState | undefined
  if (!state) {
    state = { listeners: new Set() }
    g.__openclawDiagnosticEventsState = state
  }
  return state
}

export function onDiagnosticEvent(listener: (evt: unknown) => void): () => void {
  const state = getState()
  state.listeners.add(listener)
  return () => {
    state.listeners.delete(listener)
  }
}
