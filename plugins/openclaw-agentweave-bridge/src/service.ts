import {
  onDiagnosticEvent,
  type DiagnosticEventPayload,
} from "openclaw/plugin-sdk/diagnostics-otel"
import { trace, context, propagation, type Span, type Context, SpanStatusCode } from "@opentelemetry/api"
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto"
import { NodeSDK } from "@opentelemetry/sdk-node"
import { resourceFromAttributes } from "@opentelemetry/resources"
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base"

interface ActiveTurn {
  span: Span
  ctx: Context
}

export interface BridgeConfig {
  otlpEndpoint: string
  agentId?: string
  project?: string
  enabled?: boolean
}

const activeTurns = new Map<string, ActiveTurn>()
let sdk: NodeSDK | null = null
let unsubscribe: (() => void) | null = null

function initSdk(config: BridgeConfig): void {
  if (sdk) return
  const exporter = new OTLPTraceExporter({ url: `${config.otlpEndpoint.replace(/\/$/, "")}/v1/traces` })
  const resource = resourceFromAttributes({
    "service.name": "agentweave-proxy",
    "prov.agent.id": config.agentId ?? "nix-v1",
    ...(config.project ? { "prov.project": config.project } : {}),
  })
  sdk = new NodeSDK({ resource, spanProcessors: [new BatchSpanProcessor(exporter)] })
  sdk.start()
}

export function createAgentWeaveBridgeService() {
  return {
    id: "agentweave-bridge",

    async start(ctx: { config: BridgeConfig }) {
      const config = ctx.config
      if (config.enabled === false) return
      initSdk(config)

      unsubscribe = onDiagnosticEvent((evt: DiagnosticEventPayload) => {
        try {
          switch (evt.type) {
            case "message.queued": {
              const sessionKey = evt.sessionKey ?? ""
              const sessionId = evt.sessionId ?? ""
              if (!sessionKey) break

              const tracer = trace.getTracer("openclaw-agentweave-bridge")
              const span = tracer.startSpan("openclaw.turn")
              span.setAttribute("session.id", sessionId)
              span.setAttribute("prov.session.id", sessionId)
              span.setAttribute("prov.agent.id", config.agentId ?? "nix-v1")
              span.setAttribute("prov.activity.type", "agent_turn")
              if (evt.channel) span.setAttribute("channel", evt.channel)
              if (config.project) span.setAttribute("prov.project", config.project)

              const spanCtx = trace.setSpan(context.active(), span)
              const carrier: Record<string, string> = {}
              propagation.inject(spanCtx, carrier)
              if (carrier["traceparent"]) {
                process.env.AGENTWEAVE_TRACEPARENT = carrier["traceparent"]
              }
              process.env.AGENTWEAVE_SESSION_ID = sessionId

              activeTurns.set(sessionKey, { span, ctx: spanCtx })
              break
            }

            case "message.processed": {
              const sessionKey = evt.sessionKey ?? ""
              const turn = activeTurns.get(sessionKey)
              if (!turn) break

              turn.span.setAttribute("outcome", evt.outcome)
              if (evt.durationMs != null) turn.span.setAttribute("duration_ms", evt.durationMs)
              if (evt.outcome === "error" && evt.error) {
                turn.span.setStatus({ code: SpanStatusCode.ERROR, message: evt.error })
                turn.span.setAttribute("error.message", evt.error)
              }
              turn.span.end()
              activeTurns.delete(sessionKey)
              delete process.env.AGENTWEAVE_TRACEPARENT
              break
            }

            case "model.usage": {
              const sessionKey = evt.sessionKey ?? ""
              const turn = activeTurns.get(sessionKey)
              if (!turn) break

              turn.span.addEvent("model.usage", {
                "model.provider": evt.provider ?? "",
                "model.name": evt.model ?? "",
                "model.cost_usd": evt.costUsd ?? 0,
                "model.usage.input_tokens": evt.usage?.input ?? 0,
                "model.usage.output_tokens": evt.usage?.output ?? 0,
                "model.usage.cache_read_tokens": evt.usage?.cacheRead ?? 0,
                "model.usage.cache_write_tokens": evt.usage?.cacheWrite ?? 0,
              })
              break
            }

            case "tool.loop": {
              const sessionKey = evt.sessionKey ?? ""
              const turn = activeTurns.get(sessionKey)
              if (!turn) break
              turn.span.addEvent("tool.loop.detected", {
                "tool.name": evt.toolName,
                "tool.loop.count": evt.count,
                "tool.loop.level": evt.level,
                "tool.loop.detector": evt.detector,
              })
              break
            }
          }
        } catch (err) {
          console.warn("[agentweave-bridge] event handler error:", err)
        }
      })
    },

    async stop() {
      if (unsubscribe) { unsubscribe(); unsubscribe = null }
      for (const [key, turn] of activeTurns) {
        turn.span.setAttribute("outcome", "interrupted")
        turn.span.end()
        activeTurns.delete(key)
      }
      delete process.env.AGENTWEAVE_TRACEPARENT
      if (sdk) { await sdk.shutdown(); sdk = null }
    },
  }
}
