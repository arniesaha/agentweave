import {
  onDiagnosticEvent,
  type DiagnosticEventPayload,
} from "openclaw/plugin-sdk/diagnostics-otel"
import { trace, context, propagation, type Span, type Context } from "@opentelemetry/api"
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto"
import { NodeSDK } from "@opentelemetry/sdk-node"
import { resourceFromAttributes } from "@opentelemetry/resources"
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base"

interface ActiveTurn {
  span: Span
  ctx: Context
}

interface BridgeConfig {
  otlpEndpoint: string
  agentId: string
  project?: string
  enabled: boolean
}

const activeTurns = new Map<string, ActiveTurn>()

let sdk: NodeSDK | null = null

function initSdk(config: BridgeConfig): void {
  if (sdk) return

  const exporter = new OTLPTraceExporter({ url: `${config.otlpEndpoint}/v1/traces` })
  const resource = resourceFromAttributes({
    "service.name": "openclaw-agentweave-bridge",
    "prov.agent.id": config.agentId,
    ...(config.project ? { "prov.project": config.project } : {}),
  })

  sdk = new NodeSDK({
    resource,
    spanProcessors: [new BatchSpanProcessor(exporter)],
  })
  sdk.start()
}

function handleMessageQueued(
  payload: DiagnosticEventPayload<"message.queued">,
  config: BridgeConfig,
): void {
  const tracer = trace.getTracer("openclaw-agentweave-bridge")
  const span = tracer.startSpan("openclaw.turn")

  span.setAttribute("session.id", payload.sessionId)
  span.setAttribute("prov.session.id", payload.sessionId)
  span.setAttribute("prov.agent.id", config.agentId)
  span.setAttribute("channel", payload.channel)
  if (config.project) {
    span.setAttribute("prov.project", config.project)
  }

  const ctx = trace.setSpan(context.active(), span)

  // Inject traceparent into env for downstream LLM calls via proxy
  const carrier: Record<string, string> = {}
  propagation.inject(ctx, carrier)
  if (carrier["traceparent"]) {
    process.env.AGENTWEAVE_TRACEPARENT = carrier["traceparent"]
  }

  activeTurns.set(payload.sessionKey, { span, ctx })
}

function handleMessageProcessed(
  payload: DiagnosticEventPayload<"message.processed">,
): void {
  const turn = activeTurns.get(payload.sessionKey)
  if (!turn) return

  turn.span.setAttribute("outcome", payload.outcome)
  if (payload.error) {
    turn.span.setAttribute("error.message", payload.error)
    turn.span.setStatus({ code: 2, message: payload.error }) // SpanStatusCode.ERROR = 2
  }

  turn.span.end()
  activeTurns.delete(payload.sessionKey)
  delete process.env.AGENTWEAVE_TRACEPARENT
}

function handleModelUsage(
  payload: DiagnosticEventPayload<"model.usage">,
): void {
  const turn = activeTurns.get(payload.sessionKey)
  if (!turn) return

  turn.span.addEvent("model.usage", {
    "model.provider": payload.provider,
    "model.name": payload.model,
    "model.cost_usd": payload.costUsd,
    "model.usage.input_tokens": payload.usage?.inputTokens ?? 0,
    "model.usage.output_tokens": payload.usage?.outputTokens ?? 0,
  })
}

export function createAgentWeaveBridgeService() {
  return {
    name: "agentweave-bridge",

    start(config: BridgeConfig) {
      if (!config.enabled) return

      initSdk(config)

      onDiagnosticEvent("message.queued", (payload) => {
        handleMessageQueued(payload, config)
      })

      onDiagnosticEvent("message.processed", (payload) => {
        handleMessageProcessed(payload)
      })

      onDiagnosticEvent("model.usage", (payload) => {
        handleModelUsage(payload)
      })
    },

    async stop() {
      // End any in-flight spans
      for (const [key, turn] of activeTurns) {
        turn.span.setAttribute("outcome", "interrupted")
        turn.span.end()
        activeTurns.delete(key)
      }

      if (sdk) {
        await sdk.shutdown()
        sdk = null
      }
    },
  }
}
