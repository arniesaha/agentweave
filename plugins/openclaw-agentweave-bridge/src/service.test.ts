import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { createAgentWeaveBridgeService } from "./service.js"

// ── Mock openclaw plugin SDK ──────────────────────────────────────────────────
// onDiagnosticEvent takes a single (evt) => void listener — capture it so tests
// can fire synthetic events directly.
let capturedListener: ((evt: unknown) => void) | null = null

vi.mock("openclaw/plugin-sdk/diagnostics-otel", () => ({
  onDiagnosticEvent: vi.fn((listener: (evt: unknown) => void) => {
    capturedListener = listener
    return () => { capturedListener = null } // unsubscribe
  }),
}))

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
vi.mock("@opentelemetry/sdk-node", () => ({ NodeSDK: vi.fn(() => ({ start: vi.fn(), shutdown: vi.fn() })) }))
vi.mock("@opentelemetry/resources", () => ({ resourceFromAttributes: vi.fn(() => ({})) }))
vi.mock("@opentelemetry/sdk-trace-base", () => ({ BatchSpanProcessor: vi.fn() }))

// ── Helpers ───────────────────────────────────────────────────────────────────
function fire(evt: object) {
  if (!capturedListener) throw new Error("No listener registered — did you call service.start()?")
  capturedListener(evt)
}

const defaultConfig = {
  config: {
    otlpEndpoint: "http://localhost:4318",
    agentId: "nix-v1",
    project: "agentweave",
    enabled: true,
  },
}

// ── Tests ─────────────────────────────────────────────────────────────────────
describe("createAgentWeaveBridgeService", () => {
  let service: ReturnType<typeof createAgentWeaveBridgeService>

  beforeEach(async () => {
    vi.clearAllMocks()
    capturedListener = null
    delete process.env.AGENTWEAVE_TRACEPARENT
    delete process.env.AGENTWEAVE_SESSION_ID
    service = createAgentWeaveBridgeService()
    await service.start(defaultConfig)
  })

  afterEach(async () => {
    await service.stop()
  })

  it("does not register listener when disabled", async () => {
    const { onDiagnosticEvent } = await import("openclaw/plugin-sdk/diagnostics-otel")
    vi.mocked(onDiagnosticEvent).mockClear()
    capturedListener = null
    const s = createAgentWeaveBridgeService()
    await s.start({ config: { ...defaultConfig.config, enabled: false } })
    expect(onDiagnosticEvent).not.toHaveBeenCalled()
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
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.project", "agentweave")
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

  it("sets ERROR status on message.processed with error", () => {
    fire({ type: "message.queued", sessionKey: "sk-3", sessionId: "sess-c", channel: "cli", source: "user", ts: Date.now(), seq: 1 })
    fire({ type: "message.processed", sessionKey: "sk-3", sessionId: "sess-c", channel: "cli", outcome: "error", error: "context limit exceeded", ts: Date.now(), seq: 2 })

    expect(mockSpan.setStatus).toHaveBeenCalledWith({ code: 2, message: "context limit exceeded" })
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("error.message", "context limit exceeded")
  })

  it("adds model.usage event to active span with correct field names", () => {
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
