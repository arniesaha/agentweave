import { trace, context, propagation, type Span, type Context, SpanStatusCode } from "@opentelemetry/api"
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto"
import { NodeSDK } from "@opentelemetry/sdk-node"
import { resourceFromAttributes } from "@opentelemetry/resources"
import { BatchSpanProcessor, SimpleSpanProcessor } from "@opentelemetry/sdk-trace-base"

interface ActiveTurn {
  span: Span
  ctx: Context
}

export interface BridgeConfig {
  otlpEndpoint: string
  agentId?: string
  subagentId?: string
  project?: string
  enabled?: boolean
  proxyUrl?: string
}

/**
 * Resolve agent ID from OpenClaw session key format.
 *
 * Detection order:
 * 1. Explicit subagent prefixes: agent:isolated:*, agent:main:subagent:*
 * 2. Concurrent-turn heuristic: if another agent:main:* turn is already
 *    active, this new turn is likely a spawned sub-agent (#146)
 * 3. Default: main agent
 */
function resolveAgentId(
  sessionKey: string,
  config: BridgeConfig,
  currentActiveTurns: Map<string, ActiveTurn>
): { agentId: string; agentType: string; parentSessionKey?: string } {
  const subagentId = config.subagentId ?? `${config.agentId ?? "nix"}-subagent-v1`

  // 1. Explicit subagent session key prefixes
  if (sessionKey.startsWith("agent:isolated:") || sessionKey.startsWith("agent:main:subagent:")) {
    return { agentId: subagentId, agentType: "subagent" }
  }

  // 2. Concurrent-turn heuristic: if a main turn is already active and this
  //    is a DIFFERENT session key also under agent:main:*, it's a sub-agent
  if (sessionKey.startsWith("agent:main:")) {
    for (const [activeKey] of currentActiveTurns) {
      if (activeKey !== sessionKey && activeKey.startsWith("agent:main:")) {
        console.log(`[agentweave-bridge] concurrent-turn detected: ${sessionKey} while ${activeKey} is active → subagent`)
        return { agentId: subagentId, agentType: "subagent", parentSessionKey: activeKey }
      }
    }
  }

  // 3. Default: main agent
  return { agentId: config.agentId ?? "nix-v1", agentType: "main" }
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
  sdk = new NodeSDK({ resource, spanProcessors: [new SimpleSpanProcessor(exporter)] })
  sdk.start()
  console.log("[agentweave-bridge] OTel SDK started, exporting to:", `${config.otlpEndpoint}/v1/traces`)
}

interface DiagnosticEventsState {
  seq: number
  listeners: Set<(evt: unknown) => void>
  dispatchDepth: number
}

function ensureDiagnosticEventsState(): DiagnosticEventsState {
  // Mirror OpenClaw's own getDiagnosticEventsState() from
  // plugin-sdk/diagnostic-events-C_wM1rid.js — lazy-init the shared
  // globalThis singleton.  The plugin starts before any diagnostic event
  // has been emitted, so the state doesn't exist yet.  Creating it here
  // with the same shape means emitDiagnosticEvent() will find our
  // listeners when it later calls getDiagnosticEventsState().
  const g = globalThis as Record<string, unknown>
  if (!g.__openclawDiagnosticEventsState) {
    g.__openclawDiagnosticEventsState = {
      seq: 0,
      listeners: new Set(),
      dispatchDepth: 0,
    }
    console.log("[agentweave-bridge] initialized __openclawDiagnosticEventsState on globalThis")
  }
  return g.__openclawDiagnosticEventsState as DiagnosticEventsState
}

function subscribeToDiagnosticEvents(listener: (evt: unknown) => void): () => void {
  const state = ensureDiagnosticEventsState()
  state.listeners.add(listener)
  console.log("[agentweave-bridge] subscribed to diagnostic events, listeners:", state.listeners.size)
  return () => { state.listeners.delete(listener) }
}

export function createAgentWeaveBridgeService() {
  return {
    id: "agentweave-bridge",

    async start(ctx: { config: Record<string, unknown> }) {
      const pluginEntry = (ctx.config as Record<string, unknown>)?.plugins as Record<string, unknown> | undefined
      const pluginConfig = (pluginEntry?.entries as Record<string, unknown>)?.["agentweave-bridge"] as Record<string, unknown> | undefined
      const fileConfig = pluginConfig?.config as Partial<BridgeConfig> ?? {}
      const config: BridgeConfig = {
        otlpEndpoint: fileConfig.otlpEndpoint
          ?? process.env.AGENTWEAVE_OTLP_ENDPOINT
          ?? "http://localhost:4318",
        agentId: fileConfig.agentId
          ?? process.env.AGENTWEAVE_AGENT_ID
          ?? "nix-v1",
        project: fileConfig.project
          ?? process.env.AGENTWEAVE_PROJECT
          ?? undefined,
        enabled: fileConfig.enabled ?? true,
        proxyUrl: fileConfig.proxyUrl
          ?? process.env.AGENTWEAVE_PROXY_URL
          ?? undefined,
      }

      if (config.enabled === false) return
      initSdk(config)

      // Ensure the diagnostic events state exists on globalThis before OpenClaw
      // emits any events.  OpenClaw's emitDiagnosticEvent() lazy-inits the same
      // key, so both sides converge on the same singleton.
      const g = globalThis as Record<string, unknown>
      console.log("[agentweave-bridge] globalThis keys with openclaw:", Object.keys(g).filter(k => k.includes("openclaw")))

      unsubscribe = subscribeToDiagnosticEvents((evt: unknown) => {
        const e = evt as { type?: string; sessionKey?: string; sessionId?: string; channel?: string; outcome?: string; error?: string; durationMs?: number; provider?: string; model?: string; costUsd?: number; usage?: { input?: number; output?: number; cacheRead?: number; cacheWrite?: number }; toolName?: string; level?: string; detector?: string; count?: number }
        console.log("[agentweave-bridge] event:", e.type)
        try {
          switch (e.type) {
            case "message.queued": {
              const sessionKey = e.sessionKey ?? ""
              const sessionId = e.sessionId || e.sessionKey || ""
              if (!sessionKey) break

              const { agentId, agentType, parentSessionKey } = resolveAgentId(sessionKey, config, activeTurns)

              const tracer = trace.getTracer("openclaw-agentweave-bridge")
              const span = tracer.startSpan("openclaw.turn")
              span.setAttribute("session_id", sessionId)
              span.setAttribute("session.id", sessionId)
              span.setAttribute("prov.session.id", sessionId)
              span.setAttribute("prov.agent.id", agentId)
              span.setAttribute("prov.agent.type", agentType)
              span.setAttribute("prov.activity.type", "agent_turn")
              if (e.channel) span.setAttribute("channel", e.channel)
              if (config.project) span.setAttribute("prov.project", config.project)

              // Link sub-agent to parent session
              if (agentType === "subagent") {
                // Use parentSessionKey from concurrent-turn heuristic, or find any active main turn
                const parentKey = parentSessionKey
                  ?? Array.from(activeTurns.keys()).find(k => k.startsWith("agent:main:") && !k.startsWith("agent:main:subagent:"))
                if (parentKey) {
                  const parentTurn = activeTurns.get(parentKey)
                  if (parentTurn) {
                    // Get the parent's session ID from its span attributes
                    const parentSid = parentTurn.span.spanContext?.()
                    span.setAttribute("prov.parent.session.id", parentKey)
                    process.env.AGENTWEAVE_PARENT_SESSION_ID = parentKey
                  }
                }
              }

              const spanCtx = trace.setSpan(context.active(), span)
              const carrier: Record<string, string> = {}
              propagation.inject(spanCtx, carrier)
              if (carrier["traceparent"]) {
                process.env.AGENTWEAVE_TRACEPARENT = carrier["traceparent"]
              }
              process.env.AGENTWEAVE_SESSION_ID = sessionId
              process.env.AGENTWEAVE_AGENT_ID = agentId
              process.env.AGENTWEAVE_AGENT_TYPE = agentType
              if (config.proxyUrl) {
                process.env.ANTHROPIC_BASE_URL = config.proxyUrl
              }

              activeTurns.set(sessionKey, { span, ctx: spanCtx })
              console.log(`[agentweave-bridge] started root span for ${agentType} session:`, sessionId, "agent:", agentId)
              break
            }

            case "message.processed": {
              const sessionKey = e.sessionKey ?? ""
              const turn = activeTurns.get(sessionKey)
              if (!turn) break

              turn.span.setAttribute("outcome", e.outcome ?? "unknown")
              if (e.durationMs != null) turn.span.setAttribute("duration_ms", e.durationMs)
              if (e.outcome === "error" && e.error) {
                turn.span.setStatus({ code: SpanStatusCode.ERROR, message: e.error })
                turn.span.setAttribute("error.message", e.error)
              }
              turn.span.end()
              activeTurns.delete(sessionKey)
              delete process.env.AGENTWEAVE_TRACEPARENT
              delete process.env.ANTHROPIC_BASE_URL
              delete process.env.AGENTWEAVE_AGENT_ID
              delete process.env.AGENTWEAVE_AGENT_TYPE
              delete process.env.AGENTWEAVE_PARENT_SESSION_ID
              console.log("[agentweave-bridge] ended root span for session:", sessionKey)
              break
            }

            case "model.usage": {
              const sessionKey = e.sessionKey ?? ""
              const turn = activeTurns.get(sessionKey)
              if (!turn) break
              turn.span.addEvent("model.usage", {
                "model.provider": e.provider ?? "",
                "model.name": e.model ?? "",
                "model.cost_usd": e.costUsd ?? 0,
                "model.usage.input_tokens": e.usage?.input ?? 0,
                "model.usage.output_tokens": e.usage?.output ?? 0,
                "model.usage.cache_read_tokens": e.usage?.cacheRead ?? 0,
                "model.usage.cache_write_tokens": e.usage?.cacheWrite ?? 0,
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
