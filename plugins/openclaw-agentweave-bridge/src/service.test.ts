import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { createAgentWeaveBridgeService } from "./service.js"

// Mock openclaw plugin SDK
vi.mock("openclaw/plugin-sdk/diagnostics-otel", () => {
  const handlers = new Map<string, Function>()
  return {
    onDiagnosticEvent: vi.fn((event: string, handler: Function) => {
      handlers.set(event, handler)
    }),
    _getHandler: (event: string) => handlers.get(event),
    _handlers: handlers,
  }
})

// Mock OTel APIs
const mockSpan = {
  setAttribute: vi.fn(),
  setStatus: vi.fn(),
  addEvent: vi.fn(),
  end: vi.fn(),
  spanContext: vi.fn(() => ({
    traceId: "abc123",
    spanId: "def456",
    traceFlags: 1,
  })),
}

vi.mock("@opentelemetry/api", () => ({
  trace: {
    getTracer: vi.fn(() => ({
      startSpan: vi.fn(() => mockSpan),
    })),
    setSpan: vi.fn((_ctx: unknown, _span: unknown) => ({})),
  },
  context: {
    active: vi.fn(() => ({})),
  },
  propagation: {
    inject: vi.fn((_ctx: unknown, carrier: Record<string, string>) => {
      carrier["traceparent"] = "00-abc123-def456-01"
    }),
  },
}))

vi.mock("@opentelemetry/exporter-trace-otlp-proto", () => ({
  OTLPTraceExporter: vi.fn(),
}))

vi.mock("@opentelemetry/sdk-node", () => ({
  NodeSDK: vi.fn(() => ({
    start: vi.fn(),
    shutdown: vi.fn(),
  })),
}))

vi.mock("@opentelemetry/resources", () => ({
  resourceFromAttributes: vi.fn(() => ({})),
}))

vi.mock("@opentelemetry/sdk-trace-base", () => ({
  BatchSpanProcessor: vi.fn(),
}))

const defaultConfig = {
  otlpEndpoint: "http://localhost:4318",
  agentId: "nix-v1",
  project: "agentweave",
  enabled: true,
}

describe("AgentWeave Bridge Service", () => {
  let service: ReturnType<typeof createAgentWeaveBridgeService>

  beforeEach(() => {
    vi.clearAllMocks()
    service = createAgentWeaveBridgeService()
  })

  afterEach(async () => {
    await service.stop()
    delete process.env.AGENTWEAVE_TRACEPARENT
  })

  it("registers diagnostic event handlers on start", () => {
    const { onDiagnosticEvent } = require("openclaw/plugin-sdk/diagnostics-otel")
    service.start(defaultConfig)

    expect(onDiagnosticEvent).toHaveBeenCalledWith("message.queued", expect.any(Function))
    expect(onDiagnosticEvent).toHaveBeenCalledWith("message.processed", expect.any(Function))
    expect(onDiagnosticEvent).toHaveBeenCalledWith("model.usage", expect.any(Function))
  })

  it("does not register handlers when disabled", () => {
    const { onDiagnosticEvent } = require("openclaw/plugin-sdk/diagnostics-otel")
    service.start({ ...defaultConfig, enabled: false })

    expect(onDiagnosticEvent).not.toHaveBeenCalled()
  })

  it("creates root span on message.queued", () => {
    const { _getHandler } = require("openclaw/plugin-sdk/diagnostics-otel")
    service.start(defaultConfig)

    const handler = _getHandler("message.queued")
    handler({
      sessionKey: "key-1",
      sessionId: "sess-abc",
      channel: "cli",
      source: "user",
      queueDepth: 0,
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("session.id", "sess-abc")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.session.id", "sess-abc")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.agent.id", "nix-v1")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("channel", "cli")
    expect(mockSpan.setAttribute).toHaveBeenCalledWith("prov.project", "agentweave")
    expect(process.env.AGENTWEAVE_TRACEPARENT).toBe("00-abc123-def456-01")
  })

  it("ends span on message.processed", () => {
    const { _getHandler } = require("openclaw/plugin-sdk/diagnostics-otel")
    service.start(defaultConfig)

    // First queue a message
    _getHandler("message.queued")({
      sessionKey: "key-1",
      sessionId: "sess-abc",
      channel: "cli",
      source: "user",
      queueDepth: 0,
    })

    // Then process it
    _getHandler("message.processed")({
      sessionKey: "key-1",
      sessionId: "sess-abc",
      channel: "cli",
      messageId: "msg-1",
      chatId: "chat-1",
      durationMs: 1500,
      outcome: "success",
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("outcome", "success")
    expect(mockSpan.end).toHaveBeenCalled()
    expect(process.env.AGENTWEAVE_TRACEPARENT).toBeUndefined()
  })

  it("records error status on failed message", () => {
    const { _getHandler } = require("openclaw/plugin-sdk/diagnostics-otel")
    service.start(defaultConfig)

    _getHandler("message.queued")({
      sessionKey: "key-2",
      sessionId: "sess-def",
      channel: "cli",
      source: "user",
      queueDepth: 0,
    })

    _getHandler("message.processed")({
      sessionKey: "key-2",
      sessionId: "sess-def",
      channel: "cli",
      messageId: "msg-2",
      chatId: "chat-2",
      durationMs: 500,
      outcome: "error",
      error: "context limit exceeded",
    })

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("error.message", "context limit exceeded")
    expect(mockSpan.setStatus).toHaveBeenCalledWith({
      code: 2,
      message: "context limit exceeded",
    })
  })

  it("adds model.usage event to active span", () => {
    const { _getHandler } = require("openclaw/plugin-sdk/diagnostics-otel")
    service.start(defaultConfig)

    _getHandler("message.queued")({
      sessionKey: "key-3",
      sessionId: "sess-ghi",
      channel: "cli",
      source: "user",
      queueDepth: 0,
    })

    _getHandler("model.usage")({
      sessionKey: "key-3",
      sessionId: "sess-ghi",
      provider: "anthropic",
      model: "claude-sonnet-4-6",
      usage: { inputTokens: 1000, outputTokens: 500 },
      costUsd: 0.015,
    })

    expect(mockSpan.addEvent).toHaveBeenCalledWith("model.usage", {
      "model.provider": "anthropic",
      "model.name": "claude-sonnet-4-6",
      "model.cost_usd": 0.015,
      "model.usage.input_tokens": 1000,
      "model.usage.output_tokens": 500,
    })
  })

  it("ignores model.usage for unknown session", () => {
    const { _getHandler } = require("openclaw/plugin-sdk/diagnostics-otel")
    service.start(defaultConfig)

    _getHandler("model.usage")({
      sessionKey: "unknown-key",
      sessionId: "sess-xxx",
      provider: "anthropic",
      model: "claude-sonnet-4-6",
      usage: { inputTokens: 100, outputTokens: 50 },
      costUsd: 0.001,
    })

    expect(mockSpan.addEvent).not.toHaveBeenCalled()
  })

  it("ends in-flight spans on stop", async () => {
    const { _getHandler } = require("openclaw/plugin-sdk/diagnostics-otel")
    service.start(defaultConfig)

    _getHandler("message.queued")({
      sessionKey: "key-4",
      sessionId: "sess-jkl",
      channel: "cli",
      source: "user",
      queueDepth: 0,
    })

    await service.stop()

    expect(mockSpan.setAttribute).toHaveBeenCalledWith("outcome", "interrupted")
    expect(mockSpan.end).toHaveBeenCalled()
  })
})
