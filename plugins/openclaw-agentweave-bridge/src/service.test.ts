import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { createAgentWeaveBridgeService } from "./service.js"

// ── Mock OTel APIs ────────────────────────────────────────────────────────────
const mockSpan = {
  setAttribute: vi.fn(),
  setStatus: vi.fn(),
  addEvent: vi.fn(),
  end: vi.fn(),
  spanContext: vi.fn(() => ({ traceId: "abc", spanId: "def", traceFlags: 1 })),
}

vi.mock("@opentelemetry/api", () => ({
  trace: {
    getTracer: vi.fn(() => ({ startSpan: vi.fn(() => mockSpan) })),
    setSpan: vi.fn((_ctx: unknown, _span: unknown) => ({})),
  },
  context: { active: vi.fn(() => ({})) },
  propagation: {
    inject: vi.fn((_ctx: unknown, carrier: Record<string, string>) => {
      carrier["traceparent"] = "00-abc123def456abc123def456abc12345-def456abc12345de-01"
    }),
  },
  SpanStatusCode: { ERROR: 2, OK: 1 },
}))

vi.mock("@opentelemetry/exporter-trace-otlp-proto", () => ({ OTLPTraceExporter: vi.fn() }))

// NodeSDK must be a proper constructor (not arrow function) — arrow functions can't be new'd.
vi.mock("@opentelemetry/sdk-node", () => ({
  NodeSDK: vi.fn().mockImplementation(function () {
    return { start: vi.fn(), shutdown: vi.fn() }
  }),
}))

vi.mock("@opentelemetry/resources", () => ({ resourceFromAttributes: vi.fn(() => ({})) }))
vi.mock("@opentelemetry/sdk-trace-base", () => ({
  BatchSpanProcessor: vi.fn(),
  SimpleSpanProcessor: vi.fn(),
}))

// ── Helpers ───────────────────────────────────────────────────────────────────

// service.ts writes directly to globalThis.__openclawDiagnosticEventsState —
// not via onDiagnosticEvent. Fire events through that state.
function fire(evt: object) {
  const g = globalThis as Record<string, unknown>
  const state = g.__openclawDiagnosticEventsState as { listeners: Set<(evt: unknown) => void> } | undefined
  if (!state || state.listeners.size === 0) {
    throw new Error("No listener registered — did you call service.start()?")
  }
  for (const listener of state.listeners) {
    listener(evt)
  }
}

// Build ctx in the shape service.ts reads: ctx.config.plugins.entries["agentweave-bridge"].config
function makeCtx(overrides: Record<string, unknown> = {}) {
  return {
    config: {
      plugins: {
        entries: {
          "agentweave-bridge": {
            config: {
              otlpEndpoint: "http://localhost:4318",
              agentId: "nix-v1",
              project: "agentweave",
              enabled: true,
              ...overrides,
            },
          },
        },
      },
    },
  }
}

// ── Tests ─────────────────────────────────────────────────────────────────────
describe("createAgentWeaveBridgeService", () => {
  let service: ReturnType<typeof createAgentWeaveBridgeService>

  beforeEach(async () => {
    vi.clearAllMocks()
    delete (globalThis as Record<string, unknown>).__openclawDiagnosticEventsState
    delete process.env.AGENTWEAVE_TRACEPARENT
    delete process.env.AGENTWEAVE_SESSION_ID
    delete process.env.AGENTWEAVE_PARENT_SESSION_ID
    service = createAgentWeaveBridgeService()
    await service.start(makeCtx())
  })

  afterEach(async () => {
    await service.stop()
  })

  it("does not register listener when disabled", async () => {
    const g = globalThis as Record<string, unknown>
    const countBefore = (g.__openclawDiagnosticEventsState as { listeners: Set<unknown> } | undefined)?.listeners.size ?? 0

    const disabledService = createAgentWeaveBridgeService()
    await disabledService.start(makeCtx({ enabled: false }))

    const countAfter = (g.__openclawDiagnosticEventsState as { listeners: Set<unknown> } | undefined)?.listeners.size ?? 0
    expect(countAfter).toBe(countBefore)
    // Don't call disabledService.stop() — shares module-level unsubscribe with main service
  })

  it("creates root span on message.queued and injects traceparent", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:test-session",
      sessionId: "018f-openclaw-main-test",
      channel: "telegram",
      source: "user",
      queueDepth: 0,
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("session_id", "018f-openclaw-main-test")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("session.id", "018f-openclaw-main-test")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.id", "018f-openclaw-main-test")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.key", "agent:main:test-session")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.session.id", "018f-openclaw-main-test")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.trace.metadata.session_key", "agent:main:test-session")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.agent.id", "nix-v1")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.agent.type", "main")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.activity.type", "agent_turn")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("channel", "telegram")
    expect(process.env.AGENTWEAVE_TRACEPARENT).toBeTruthy()
    expect(process.env.AGENTWEAVE_SESSION_ID).toBe("018f-openclaw-main-test")
  })

  it("sets Langfuse input preview on message.queued when OpenClaw provides one", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:preview-session",
      sessionId: "018f-openclaw-preview-0001",
      channel: "telegram",
      source: "user",
      inputPreview: "  summarize   the deployment\nstatus  ",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.observation.type", "agent")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.session.id", "018f-openclaw-preview-0001")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.observation.input", "summarize the deployment status")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.input.preview", "summarize the deployment status")
  })

  it("uses task labels as a safe input fallback for lifecycle spans", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:task-session",
      sessionId: "task-session",
      channel: "cron",
      source: "cron-isolated",
      taskLabel: "Daily portfolio briefing",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.trace.name", "Daily portfolio briefing")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.trace.metadata.task_label", "Daily portfolio briefing")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.observation.input", "Daily portfolio briefing")
  })

  it("sets prov.harness=openclaw on message.queued root span", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:harness-session",
      sessionId: "018f-openclaw-main-0001",
      channel: "cli",
      source: "user",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.harness", "openclaw")
  })

  it("sets prov.session.key to the qualified sessionKey on message.queued root span", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:key-session",
      sessionId: "018f-openclaw-main-0002",
      channel: "cli",
      source: "user",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.key", "agent:main:key-session")
  })

  it("sets prov.harness=openclaw on session.state subagent root span", () => {
    fire({
      type: "session.state",
      sessionKey: "agent:main:parent:subagent:worker-h",
      sessionId: "018f-openclaw-sub-0001",
      state: "processing",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.harness", "openclaw")
  })

  it("sets prov.session.key to the qualified sessionKey on session.state subagent root span", () => {
    fire({
      type: "session.state",
      sessionKey: "agent:main:parent:subagent:worker-k",
      sessionId: "018f-openclaw-sub-0002",
      state: "processing",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.key", "agent:main:parent:subagent:worker-k")
  })

  it("sets prov.session.uuid to the canonical sessionId when it differs from sessionKey", () => {
    // cron/isolated path: OpenClaw passes both a UUID sessionId and a route key.
    fire({
      type: "message.queued",
      sessionKey: "agent:main:uuid-session",
      sessionId: "018f-openclaw-main-0009",
      channel: "cron",
      source: "cron-isolated",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("session.id", "018f-openclaw-main-0009")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.id", "018f-openclaw-main-0009")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.uuid", "018f-openclaw-main-0009")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.key", "agent:main:uuid-session")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.session.id", "018f-openclaw-main-0009")
  })

  it("falls back to sessionKey when the event carries no canonical sessionId", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:no-uuid-session",
      channel: "telegram",
      source: "user",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("session.id", "agent:main:no-uuid-session")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.id", "agent:main:no-uuid-session")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.key", "agent:main:no-uuid-session")
    expect(mockSpan.setAttribute).not.toHaveBeenCalledWith("prov.session.uuid", expect.anything())
  })

  it("does not treat a bare route alias sessionId as the canonical UUID", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:main",
      sessionId: "main",
      channel: "telegram",
      source: "user",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("session.id", "agent:main:main")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.id", "agent:main:main")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.key", "agent:main:main")
    expect(mockSpan.setAttribute).not.toHaveBeenCalledWith("prov.session.uuid", "main")
    expect(mockSpan.setAttribute).not.toHaveBeenCalledWith("langfuse.session.id", "main")
  })

  it("sets cwd and repository on message.queued when provided by the event", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:repo-session",
      sessionId: "repo-session",
      channel: "cli",
      source: "user",
      cwd: "/home/arnab/dev/agentweave",
      repository: "arniesaha/agentweave",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.cwd", "/home/arnab/dev/agentweave")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.repository", "arniesaha/agentweave")
  })

  it("omits cwd and repository on message.queued when the event does not provide them", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:no-repo-session",
      sessionId: "no-repo-session",
      channel: "cli",
      source: "user",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).not.toHaveBeenCalledWith("prov.cwd", expect.anything())
    expect(mockSpan.setAttribute).not.toHaveBeenCalledWith("prov.repository", expect.anything())
  })

  it("sets prov.session.uuid on session.state subagent roots when the event carries a distinct canonical id", () => {
    fire({
      type: "session.state",
      sessionKey: "agent:main:parent:subagent:worker-u",
      sessionId: "018f-openclaw-sub-0009",
      state: "processing",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("session.id", "018f-openclaw-sub-0009")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.id", "018f-openclaw-sub-0009")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.uuid", "018f-openclaw-sub-0009")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.key", "agent:main:parent:subagent:worker-u")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.session.id", "018f-openclaw-sub-0009")
  })

  it("sets cwd and repository on session.state subagent roots when provided by the event", () => {
    fire({
      type: "session.state",
      sessionKey: "agent:main:parent:subagent:worker-c",
      sessionId: "worker-c",
      state: "processing",
      raw_data: {
        cwd: "/home/arnab/dev/agentweave/plugins/openclaw-agentweave-bridge",
        repository: "arniesaha/agentweave",
      },
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.cwd", "/home/arnab/dev/agentweave/plugins/openclaw-agentweave-bridge")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.repository", "arniesaha/agentweave")
  })

  it("sets Langfuse input preview on session.state subagent roots", () => {
    fire({
      type: "session.state",
      sessionKey: "agent:main:parent:subagent:worker-preview",
      sessionId: "018f-openclaw-sub-preview",
      state: "processing",
      taskLabel: "Review pull request #227",
      inputPreview: "Review pull request #227 and patch failures",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.observation.type", "agent")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.session.id", "018f-openclaw-sub-preview")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.trace.name", "Review pull request #227")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("langfuse.observation.input", "Review pull request #227 and patch failures")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.input.preview", "Review pull request #227 and patch failures")
  })

  it("ends span on message.processed (completed) and cleans env", () => {
    fire({ type: "message.queued", sessionKey: "agent:main:sk-2", sessionId: "sess-b", channel: "cli", source: "user", ts: Date.now(), seq: 1 })
    fire({ type: "message.processed", sessionKey: "agent:main:sk-2", sessionId: "sess-b", channel: "cli", outcome: "completed", durationMs: 1200, ts: Date.now(), seq: 2 })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("outcome", "completed")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("duration_ms", 1200)
    expect(mockSpan.end).toHaveBeenCalled()
    expect(process.env.AGENTWEAVE_TRACEPARENT).toBeUndefined()
  })

  it("sets ERROR status on message.processed with error outcome", () => {
    fire({ type: "message.queued", sessionKey: "agent:main:sk-3", sessionId: "sess-c", channel: "cli", source: "user", ts: Date.now(), seq: 1 })
    fire({ type: "message.processed", sessionKey: "agent:main:sk-3", sessionId: "sess-c", channel: "cli", outcome: "error", error: "context limit exceeded", ts: Date.now(), seq: 2 })

    expect(mockSpan.setStatus).toHaveBeenCalledWith({ code: 2, message: "context limit exceeded" })
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("error.message", "context limit exceeded")
  })

  it("adds model.usage event and span attributes to active span", () => {
    fire({ type: "message.queued", sessionKey: "agent:main:sk-4", sessionId: "sess-d", channel: "cli", source: "user", ts: Date.now(), seq: 1 })
    fire({
      type: "model.usage",
      sessionKey: "agent:main:sk-4",
      sessionId: "sess-d",
      provider: "anthropic",
      model: "claude-sonnet-4-6",
      usage: { input: 1000, output: 500, cacheRead: 200, cacheWrite: 100 },
      costUsd: 0.015,
      ts: Date.now(),
      seq: 2,
    })

    expect(mockSpan.addEvent).toHaveBeenCalledWith("model.usage", expect.objectContaining({
      "model.provider": "anthropic",
      "model.name": "claude-sonnet-4-6",
      "model.cost_usd": 0.015,
      "model.usage.input_tokens": 1000,
      "model.usage.output_tokens": 500,
      "model.usage.cache_read_tokens": 200,
      "model.usage.cache_write_tokens": 100,
    }))
    // Also written as span attributes for querying
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.llm.provider", "anthropic")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.llm.model", "claude-sonnet-4-6")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.llm.prompt_tokens", 1000)
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.llm.completion_tokens", 500)
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.llm.cache_read_tokens", 200)
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.llm.cache_write_tokens", 100)
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("cost.usd", 0.015)
  })

  it("ignores model.usage for unknown sessionKey", () => {
    fire({ type: "model.usage", sessionKey: "nonexistent", provider: "anthropic", model: "haiku", usage: { input: 10, output: 5 }, costUsd: 0.001, ts: Date.now(), seq: 1 })
    expect(mockSpan.addEvent).not.toHaveBeenCalled()
  })

  it("adds tool.loop event to active span", () => {
    fire({ type: "message.queued", sessionKey: "agent:main:sk-5", sessionId: "sess-e", channel: "cli", source: "user", ts: Date.now(), seq: 1 })
    fire({
      type: "tool.loop",
      sessionKey: "agent:main:sk-5",
      sessionId: "sess-e",
      toolName: "exec",
      level: "warning",
      action: "warn",
      detector: "generic_repeat",
      count: 5,
      message: "exec called 5 times",
      ts: Date.now(),
      seq: 2,
    })

    expect(mockSpan.addEvent).toHaveBeenCalledWith("tool.loop.detected", expect.objectContaining({
      "tool.name": "exec",
      "tool.loop.count": 5,
      "tool.loop.level": "warning",
    }))
  })

  it("ends in-flight spans on stop()", async () => {
    fire({ type: "message.queued", sessionKey: "agent:main:sk-6", sessionId: "sess-f", channel: "cli", source: "user", ts: Date.now(), seq: 1 })
    await service.stop()

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("outcome", "interrupted")
    expect(mockSpan.end).toHaveBeenCalled()
  })

  it("prefers upstream agentweave context for attribution on message.queued", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:paperclip-conductor",
      sessionId: "018f-openclaw-main-pc",
      channel: "cli",
      source: "user",
      clientContext: {
        schemaVersion: "agentweave.context.v1",
        source: "paperclip",
        sessionId: "ea03270f-e02c-4afc-b893-862dcb51b05d",
        agentId: "Conductor",
        agentType: "paperclip",
        taskLabel: "AGE-8: fix attribution",
        parentSessionId: "paperclip-root",
        paperclip: {
          runId: "ea03270f-e02c-4afc-b893-862dcb51b05d",
          issueId: "AGE-8",
          taskId: "task-1",
        },
      },
      ts: Date.now(),
      seq: 1,
    })

    // Upstream identity wins over the local nix-v1 fallback.
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.agent.id", "Conductor")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.agent.type", "paperclip")
    // session.id becomes the upstream run/session id so spans are findable by it.
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("session.id", "ea03270f-e02c-4afc-b893-862dcb51b05d")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.id", "ea03270f-e02c-4afc-b893-862dcb51b05d")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.task.label", "AGE-8: fix attribution")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.parent.session.id", "paperclip-root")
    // Paperclip ids retained for filtering/debugging.
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.upstream.source", "paperclip")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.upstream.run_id", "ea03270f-e02c-4afc-b893-862dcb51b05d")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.upstream.issue_id", "AGE-8")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.upstream.task_id", "task-1")
    // The qualified OpenClaw route key is still preserved for correlation.
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.key", "agent:main:paperclip-conductor")
  })

  it("maps partial upstream context without inventing missing fields", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:paperclip-partial",
      sessionId: "018f-openclaw-main-partial",
      channel: "cli",
      source: "user",
      clientContext: {
        schemaVersion: "agentweave.context.v1",
        source: "paperclip",
        agentId: "Conductor",
        agentType: "conductor",
      },
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.agent.id", "Conductor")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.agent.type", "conductor")
    // No upstream sessionId provided → keep the OpenClaw canonical id.
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("session.id", "018f-openclaw-main-partial")
    expect(mockSpan.setAttribute).not.toHaveBeenCalledWith("prov.parent.session.id", expect.anything())
    expect(mockSpan.setAttribute).not.toHaveBeenCalledWith("prov.upstream.run_id", expect.anything())
  })

  it("falls back to nix-v1 attribution when no upstream context is present", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:plain",
      sessionId: "sess-plain",
      channel: "cli",
      source: "user",
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.agent.id", "nix-v1")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.agent.type", "main")
    expect(mockSpan.setAttribute).not.toHaveBeenCalledWith("prov.upstream.source", expect.anything())
  })

  it("ignores clientContext with an unrecognized schemaVersion", () => {
    fire({
      type: "message.queued",
      sessionKey: "agent:main:other",
      sessionId: "sess-other",
      channel: "cli",
      source: "user",
      clientContext: { schemaVersion: "something.else.v9", agentId: "X" },
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.agent.id", "nix-v1")
    expect(mockSpan.setAttribute).not.toHaveBeenCalledWith("prov.agent.id", "X")
  })
})
