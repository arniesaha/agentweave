import type { DiagnosticEventPayload } from "openclaw/plugin-sdk/diagnostic-runtime"

// The deployed OpenClaw fork (bf598e8, src/infra/diagnostic-events.ts)
// extends the published 2026.9.2 union with these two preview fields.
// Keep this delta explicit and review it on each host upgrade.
type QueuedEvent = Extract<DiagnosticEventPayload, { type: "message.queued" }> & {
  inputPreview?: string
}

type SessionStateEvent = Extract<DiagnosticEventPayload, { type: "session.state" }> & {
  inputPreview?: string
  taskLabel?: string
}

export type HostDiagnosticEvent =
  | Exclude<DiagnosticEventPayload, { type: "message.queued" | "session.state" }>
  | QueuedEvent
  | SessionStateEvent
