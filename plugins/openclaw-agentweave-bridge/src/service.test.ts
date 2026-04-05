import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { createAgentWeaveBridgeService } from "./service.js"

// ── Mock OTel APIs ────────────────────────────────────────────────────────────
const mockSpan = {
  setAttribute: vi.fn(),
  setStatus: vi.fn(),
  addEvent: vi.fn(),
  end: vi.fn(),
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

// NodeSDK must be a real constructor (not an arrow function) so `new NodeSDK()` works.
// We return an object with start/shutdown as spies so service.ts can call them.
vi.mock("@opentelemetry/sdk-node", () => ({
  NodeSDK: vi.fn().mockImplementation(function () {
    return { start: vi.fn(), shutdown: vi.fn() }
  }),
}))

vi.mock("@opentelemetry/resources", () => ({ resourceFromAttributes: vi.fn(() => ({})) }))
vi.mock("@opentelemetry/sdk-trace-base", () => ({ BatchSpanProcessor: vi.fn() }))

// ── Helpers ───────────────────────────────────────────────────────────────────

// service.ts does NOT use onDiagnosticEvent — it writes directly to
// globalThis.__openclawDiagnosticEventsState. Fire events the same way.
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

// Build a ctx in the shape service.ts actually reads from:
// ctx.config.plugins.entries["agentweave-bridge"].config
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
    // Reset shared globalThis state and env vars between tests
    delete (globalThis as Record<string, unknown>).__openclawDiagnosticEventsState
    delete process.env.AGENTWEAVE_TRACEPARENT
    delete process.env.AGENTWEAVE_SESSION_ID
    service = createAgentWeaveBridgeService()
    await service.start(makeCtx())
  })

  afterEach(async () => {
    await service.stop()
  })

  it("does not register listener when disabled", async () => {
    const g = globalThis as Record<string, unknown>
    const stateBefore = g.__openclawDiagnosticEventsState as { listeners: Set<unknown> } | undefined
    const countBefore = stateBefore?.listeners.size ?? 0

    // A second service instance with enabled:false should not add a listener
    const disabledService = createAgentWeaveBridgeService()
    await disabledService.start(makeCtx({ enabled: false }))

    const stateAfter = g.__openclawDiagnosticEventsState as { listeners: Set<unknown> } | undefined
    expect(stateAfter?.listeners.size ?? 0).toBe(countBefore)
    // Don't call disabledService.stop() — it shares module-level unsubscribe
    // with the main service and would incorrectly tear it down.
  })

  it("creates root span on message.queued and injects traceparent", () => {
    fire({
      type: "message.queued",
      sessionKey: "sk-1",
      sessionId: "sess-abc",
      channel: "telegram",
      source: "user",
      queueDepth: 0,
      ts: Date.now(),
      seq: 1,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("session.id", "sess-abc")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.id", "sess-abc")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.agent.id", "nix-v1")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.activity.type", "agent_turn")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("channel", "telegram")
    expect(process.env.AGENTWEAVE_TRACEPARENT).toBeTruthy()
    expect(process.env.AGENTWEAVE_SESSION_ID).toBe("sess-abc")
  })

  it("ends span on message.processed (completed) and cleans env", () => {
    fire({ type: "message.queued", sessionKey: "sk-2", sessionId: "sess-b", channel: "cli", source: "user", ts: Date.now(), seq: 1 })
    fire({ type: "message.processed", sessionKey: "sk-2", sessionId: "sess-b", channel: "cli", outcome: "completed", durationMs: 1200, ts: Date.now(), seq: 2 })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("outcome", "completed")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("duration_ms", 1200)
    expect(mockSpan.end).toHaveBeenCalled()
    expect(process.env.AGENTWEAVE_TRACEPARENT).toBeUndefined()
  })

  it("sets ERROR status on message.processed with error outcome", () => {
    fire({ type: "message.queued", sessionKey: "sk-3", sessionId: "sess-c", channel: "cli", source: "user", ts: Date.now(), seq: 1 })
    fire({ type: "message.processed", sessionKey: "sk-3", sessionId: "sess-c", channel: "cli", outcome: "error", error: "context limit exceeded", ts: Date.now(), seq: 2 })

    expect(mockSpan.setStatus).toHaveBeenCalledWith({ code: 2, message: "context limit exceeded" })
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("error.message", "context limit exceeded")
  })

  it("adds model.usage event and span attributes to active span", () => {
    fire({ type: "message.queued", sessionKey: "sk-4", sessionId: "sess-d", channel: "cli", source: "user", ts: Date.now(), seq: 1 })
    fire({
      type: "model.usage",
      sessionKey: "sk-4",
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

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.llm.provider", "anthropic")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.llm.model", "claude-sonnet-4-6")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("cost.usd", 0.015)
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.llm.prompt_tokens", 1000)
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.llm.completion_tokens", 500)
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.llm.cache_read_tokens", 200)
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.llm.cache_write_tokens", 100)
  })

  it("ignores model.usage for unknown sessionKey", () => {
    fire({ type: "model.usage", sessionKey: "nonexistent", provider: "anthropic", model: "haiku", usage: { input: 10, output: 5 }, costUsd: 0.001, ts: Date.now(), seq: 1 })
    expect(mockSpan.addEvent).not.toHaveBeenCalled()
  })

  it("adds tool.loop event to active span", () => {
    fire({ type: "message.queued", sessionKey: "sk-5", sessionId: "sess-e", channel: "cli", source: "user", ts: Date.now(), seq: 1 })
    fire({
      type: "tool.loop",
      sessionKey: "sk-5",
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
    fire({ type: "message.queued", sessionKey: "sk-6", sessionId: "sess-f", channel: "cli", source: "user", ts: Date.now(), seq: 1 })
    await service.stop()

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("outcome", "interrupted")
    expect(mockSpan.end).toHaveBeenCalled()
  })
})
