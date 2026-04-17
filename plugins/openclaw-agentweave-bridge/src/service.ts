import { trace, context, propagation, type Span, type Context, SpanStatusCode } from "@opentelemetry/api"
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto"
import { NodeSDK } from "@opentelemetry/sdk-node"
import { resourceFromAttributes } from "@opentelemetry/resources"
import { BatchSpanProcessor, SimpleSpanProcessor } from "@opentelemetry/sdk-trace-base"
import { resolveCost } from "./pricing.js"

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

/** Sources that indicate a spawned sub-agent (not a user-initiated message). */
const SUBAGENT_SOURCES = new Set([
  "sessions_spawn",
  "subagent",
  "spawn",
  "delegated",
])

/**
 * Resolve agent ID from OpenClaw diagnostic event context.
 *
 * Detection order:
 * 1. Event source field: "sessions_spawn" etc. → subagent (reliable, from OpenClaw)
 * 2. Explicit subagent session key prefixes: agent:isolated:*, agent:main:subagent:*
 * 3. Concurrent-turn heuristic: another agent:main:* turn is already active (fallback)
 * 4. Default: main agent
 */
function resolveAgentId(
  sessionKey: string,
  config: BridgeConfig,
  currentActiveTurns: Map<string, ActiveTurn>,
  source?: string
): { agentId: string; agentType: string; parentSessionKey?: string } {
  const subagentId = config.subagentId ?? `${config.agentId ?? "nix"}-subagent-v1`

  // 1. Event source field (most reliable — directly from OpenClaw)
  if (source && SUBAGENT_SOURCES.has(source)) {
    console.log(`[agentweave-bridge] source="${source}" → subagent`)
    // Find parent from active turns
    const parentKey = Array.from(currentActiveTurns.keys())
      .find(k => k.startsWith("agent:main:") && k !== sessionKey)
    return { agentId: subagentId, agentType: "subagent", parentSessionKey: parentKey }
  }

  // 2. Explicit subagent session key prefixes
  if (sessionKey.startsWith("agent:isolated:") || sessionKey.startsWith("agent:main:subagent:")) {
    return { agentId: subagentId, agentType: "subagent" }
  }

  // 3. Concurrent-turn heuristic (fallback — less reliable)
  if (sessionKey.startsWith("agent:main:")) {
    for (const [activeKey] of currentActiveTurns) {
      if (activeKey !== sessionKey && activeKey.startsWith("agent:main:")) {
        console.log(`[agentweave-bridge] concurrent-turn: ${sessionKey} while ${activeKey} active → subagent`)
        return { agentId: subagentId, agentType: "subagent", parentSessionKey: activeKey }
      }
    }
  }

  // 4. Default: main agent
  return { agentId: config.agentId ?? "nix-v1", agentType: "main" }
}

const activeTurns = new Map<string, ActiveTurn>()
let sdk: NodeSDK | null = null
let unsubscribe: (() => void) | null = null


function normalizeProxyBaseUrl(url?: string): string | undefined {
  if (!url) return undefined
  return url.replace(/\/v1\/?$/, "")
}

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

function getSpanSessionId(turn: ActiveTurn): string | undefined {
  return (turn.span as any)?._attributes?.["session.id"] as string | undefined
}

function findTurnForModelUsage(sessionKey: string, sessionId: string): { key: string; turn: ActiveTurn; reason: string } | null {
  const activeKeys = Array.from(activeTurns.keys())

  if (sessionKey && activeTurns.has(sessionKey)) {
    return { key: sessionKey, turn: activeTurns.get(sessionKey)!, reason: "sessionKey-exact" }
  }

  if (sessionId) {
    const bySessionId = activeKeys.find(key => getSpanSessionId(activeTurns.get(key)!) === sessionId)
    if (bySessionId) {
      return { key: bySessionId, turn: activeTurns.get(bySessionId)!, reason: "sessionId-span-attr" }
    }

    if (activeTurns.has(sessionId)) {
      return { key: sessionId, turn: activeTurns.get(sessionId)!, reason: "sessionId-as-key" }
    }
  }

  const usageSubagentId = sessionKey.includes(":subagent:") ? sessionKey.split(":subagent:")[1] : ""
  if (usageSubagentId) {
    const bySubagentSuffix = activeKeys.find(key => key.includes(":subagent:") && key.endsWith(`:subagent:${usageSubagentId}`))
    if (bySubagentSuffix) {
      return { key: bySubagentSuffix, turn: activeTurns.get(bySubagentSuffix)!, reason: "subagent-suffix" }
    }
  }

  if (sessionKey.startsWith("agent:main:")) {
    const activeSubagents = activeKeys.filter(key => key.includes(":subagent:"))
    if (activeSubagents.length > 0) {
      const latestSubagent = activeSubagents[activeSubagents.length - 1]
      return { key: latestSubagent, turn: activeTurns.get(latestSubagent)!, reason: "main-key-fallback-to-active-subagent" }
    }
  }

  return null
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
        const e = evt as { type?: string; sessionKey?: string; sessionId?: string; channel?: string; source?: string; outcome?: string; error?: string; durationMs?: number; provider?: string; model?: string; costUsd?: number; usage?: { input?: number; output?: number; cacheRead?: number; cacheWrite?: number }; toolName?: string; level?: string; detector?: string; count?: number; queueDepth?: number }
        console.log("[agentweave-bridge] event:", e.type, "sessionKey:", e.sessionKey, "source:", e.source)
        try {
          switch (e.type) {
            case "message.queued": {
              const sessionKey = e.sessionKey ?? ""
              // Use sessionKey (e.g. "agent:main:main") as the session ID, not
              // sessionId (which is just "main" — the bare agent name).
              // sessionKey is the fully qualified identifier across OpenClaw.
              const sessionId = e.sessionKey || e.sessionId || ""
              if (!sessionKey) break

              const { agentId, agentType, parentSessionKey } = resolveAgentId(sessionKey, config, activeTurns, e.source)

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
              const proxyBaseUrl = normalizeProxyBaseUrl(config.proxyUrl)
              if (proxyBaseUrl) {
                process.env.ANTHROPIC_BASE_URL = proxyBaseUrl
                process.env.OPENAI_BASE_URL = proxyBaseUrl
                process.env.OPENAI_API_BASE = proxyBaseUrl
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
              delete process.env.OPENAI_BASE_URL
              delete process.env.OPENAI_API_BASE
              delete process.env.AGENTWEAVE_AGENT_ID
              delete process.env.AGENTWEAVE_AGENT_TYPE
              delete process.env.AGENTWEAVE_PARENT_SESSION_ID
              console.log("[agentweave-bridge] ended root span for session:", sessionKey)
              break
            }

            case "session.state": {
              const sessionKey = e.sessionKey ?? ""
              const state = (e as any).state as string | undefined
              // Detect OpenClaw native sub-agent sessions (agent:*:subagent:*)
              // These don't emit message.queued, only session.state transitions
              if (sessionKey.includes(":subagent:") && !activeTurns.has(sessionKey)) {
                if (state === "processing") {
                  const subagentId = config.subagentId ?? `${config.agentId ?? "nix"}-subagent-v1`
                  const sessionId = (e as any).sessionId || sessionKey
                  const tracer = trace.getTracer("openclaw-agentweave-bridge")
                  const span = tracer.startSpan("openclaw.subagent")
                  span.setAttribute("session_id", sessionId)
                  span.setAttribute("session.id", sessionId)
                  span.setAttribute("prov.session.id", sessionId)
                  span.setAttribute("prov.agent.id", subagentId)
                  span.setAttribute("prov.agent.type", "subagent")
                  span.setAttribute("prov.activity.type", "agent_turn")
                  if (config.project) span.setAttribute("prov.project", config.project)
                  // Link to active main session as parent
                  const mainKey = Array.from(activeTurns.keys()).find(k =>
                    k.startsWith("agent:main:") && !k.includes(":subagent:"))
                  if (mainKey) {
                    const mainTurn = activeTurns.get(mainKey)
                    if (mainTurn) {
                      const mainSessionId = (mainTurn.span as any)._attributes?.["session.id"] || mainKey
                      span.setAttribute("prov.parent.session.id", mainSessionId)
                    }
                  }
                  const spanCtx = trace.setSpan(context.active(), span)
                  activeTurns.set(sessionKey, { span, ctx: spanCtx })

                  // Force the proxy to attribute LLM calls to this sub-agent session
                  const proxyUrl = normalizeProxyBaseUrl(config.proxyUrl) || "http://192.168.1.70:30400"
                  const mainSessionId = mainKey ? (activeTurns.get(mainKey)?.span as any)?._attributes?.["session.id"] || "nix-main" : "nix-main"
                  fetch(`${proxyUrl}/session`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      session_id: sessionId,
                      parent_session_id: mainSessionId,
                      agent_id: subagentId,
                      agent_type: "subagent",
                      task_label: `subagent ${sessionKey.split(":")[1] || "unknown"}`,
                      force: true,
                    }),
                  }).then(() => console.log(`[agentweave-bridge] proxy session forced to subagent: ${sessionId}`))
                    .catch(err => console.warn(`[agentweave-bridge] proxy session set failed:`, err.message))

                  console.log(`[agentweave-bridge] started subagent span: ${sessionKey} agent: ${subagentId}`)
                }
              }
              // End subagent span when session goes idle — restore main session on proxy
              if (sessionKey.includes(":subagent:") && activeTurns.has(sessionKey)) {
                if (state === "idle") {
                  const turn = activeTurns.get(sessionKey)!
                  turn.span.setAttribute("outcome", "completed")
                  turn.span.end()
                  activeTurns.delete(sessionKey)

                  // Restore proxy to main session (clear force)
                  const proxyUrl = normalizeProxyBaseUrl(config.proxyUrl) || "http://192.168.1.70:30400"
                  fetch(`${proxyUrl}/session`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      session_id: "nix-main",
                      agent_type: "main",
                      force: false,
                    }),
                  }).then(() => console.log(`[agentweave-bridge] proxy session restored to nix-main`))
                    .catch(err => console.warn(`[agentweave-bridge] proxy session restore failed:`, err.message))

                  console.log(`[agentweave-bridge] ended subagent span: ${sessionKey}`)
                }
              }
              break
            }

            case "model.usage": {
              const sessionKey = e.sessionKey ?? ""
              const sessionId = e.sessionId ?? ""
              const activeKeys = Array.from(activeTurns.keys())
              console.log(`[agentweave-bridge] model.usage lookup incoming sessionKey=${sessionKey || "<empty>"} sessionId=${sessionId || "<empty>"} activeTurns=[${activeKeys.join(", ")}]`)

              const match = findTurnForModelUsage(sessionKey, sessionId)
              if (!match) {
                console.log(`[agentweave-bridge] model.usage no active span found for sessionKey=${sessionKey || "<empty>"} sessionId=${sessionId || "<empty>"}`)
                break
              }

              const { key: targetKey, turn, reason } = match
              console.log(`[agentweave-bridge] model.usage matched active turn key=${targetKey} reason=${reason}`)

              const provider = e.provider ?? ""
              const model = e.model ?? ""
              const inputTokens = e.usage?.input ?? 0
              const outputTokens = e.usage?.output ?? 0
              const cacheReadTokens = e.usage?.cacheRead ?? 0
              const cacheWriteTokens = e.usage?.cacheWrite ?? 0

              // OpenClaw may not know pricing for every model (e.g. MiniMax) and
              // reports costUsd=0 in that case. Fall back to a local pricing
              // table so the span carries a real cost rather than silently 0.
              const costUsd = resolveCost(e.costUsd ?? 0, model, {
                inputTokens,
                outputTokens,
                cacheReadTokens,
                cacheWriteTokens,
              })

              // Keep event emission for event-level timelines/debugging.
              turn.span.addEvent("model.usage", {
                "model.provider": provider,
                "model.name": model,
                "model.cost_usd": costUsd,
                "model.usage.input_tokens": inputTokens,
                "model.usage.output_tokens": outputTokens,
                "model.usage.cache_read_tokens": cacheReadTokens,
                "model.usage.cache_write_tokens": cacheWriteTokens,
              })

              // Write provider/model/cost/tokens to the existing open span.
              // model.usage can fire after span creation; setAttribute updates span state in-place.
              turn.span.setAttribute("prov.llm.provider", provider)
              turn.span.setAttribute("prov.llm.model", model)
              turn.span.setAttribute("cost.usd", costUsd)
              turn.span.setAttribute("prov.llm.prompt_tokens", inputTokens)
              turn.span.setAttribute("prov.llm.completion_tokens", outputTokens)
              turn.span.setAttribute("prov.llm.cache_read_tokens", cacheReadTokens)
              turn.span.setAttribute("prov.llm.cache_write_tokens", cacheWriteTokens)
              break
            }

            case "tool.loop": {
              const sessionKey = e.sessionKey ?? ""
              const turn = activeTurns.get(sessionKey)
              if (!turn) break
              turn.span.addEvent("tool.loop.detected", {
                "tool.name": e.toolName ?? "",
                "tool.loop.count": e.count ?? 0,
                "tool.loop.level": e.level ?? "",
                "tool.loop.detector": e.detector ?? "",
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
      delete process.env.ANTHROPIC_BASE_URL
      delete process.env.OPENAI_BASE_URL
      delete process.env.OPENAI_API_BASE
      if (sdk) { await sdk.shutdown(); sdk = null }
    },
  }
}
