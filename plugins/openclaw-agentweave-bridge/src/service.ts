import { trace, context, propagation, type Span, type Context, SpanStatusCode } from "@opentelemetry/api"
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto"
import { NodeSDK } from "@opentelemetry/sdk-node"
import { resourceFromAttributes } from "@opentelemetry/resources"
import { BatchSpanProcessor, SimpleSpanProcessor } from "@opentelemetry/sdk-trace-base"
// Namespace import keeps the optional fork-only listeners truly optional at
// module-load time on older/public hosts, not merely guarded after loading.
import * as diagnosticRuntime from "openclaw/plugin-sdk/diagnostic-runtime"
import type { HostDiagnosticEvent, HostDiagnosticPrivateData } from "./host-diagnostic-contract.js"
import { resolveCost } from "./pricing.js"

const onDiagnosticEvent = diagnosticRuntime.onDiagnosticEvent
const optionalRuntime = diagnosticRuntime as unknown as Record<string, unknown>
const onModelDiagnosticEvent = optionalRuntime.onModelDiagnosticEvent
const onTrustedDiagnosticEvent = optionalRuntime.onTrustedDiagnosticEvent

interface ActiveTurn {
  span: Span
  ctx: Context
  /** Session identity used only to match later host diagnostic events. */
  lookupSessionId?: string
  /** Opaque OpenClaw-owned token received through trusted lifecycle private data. */
  sessionCorrelationId?: string
  /** True for spans started from session.state (gateway-agent / subagent paths)
   *  that must be ended on the session.state idle transition rather than a
   *  message.processed event (which never fires for those paths). */
  endOnIdle?: boolean
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
const activeTurnsByRunId = new Map<string, ActiveTurn>()
const activeTurnsByCallId = new Map<string, ActiveTurn>()
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

// Lifecycle event types whose upstream `clientContext` now rides the trusted
// privateData channel (`onTrustedDiagnosticEvent`) instead of the public event
// payload. When that subscription is available we route these two types through
// it so the dispatcher reads clientContext from privateData; the public
// subscription then skips them to avoid creating the root span twice.
const TRUSTED_LIFECYCLE_TYPES = new Set(["session.state", "message.queued"])

function subscribeToDiagnosticEvents(
  listener: (evt: unknown, privateData?: unknown) => void,
): () => void {
  // Three openclaw plugin-sdk subscriptions:
  //
  // 1. `onDiagnosticEvent` covers the public (untrusted) event stream —
  //    message/session lifecycle, queue events, etc. used to manage spans.
  //    The public payload no longer carries `clientContext`; that moved to
  //    the trusted privateData channel (see #3).
  // 2. `onModelDiagnosticEvent` is the narrow opt-in over the trusted
  //    `model.*` family (call.started/completed/error, usage, failover).
  //    Without it, the embedded codex runner's `model.call.completed`
  //    events never reach the bridge and codex turn spans land in
  //    AgentWeave without `prov.llm.{provider,model}` (issue: codex traffic
  //    silently buckets as "unknown" on the dashboard).
  // 3. `onTrustedDiagnosticEvent` delivers `session.state`/`message.queued`
  //    paired with the opt-in `privateData` bag (carrying the seeded
  //    `clientContext`). This is the only contracted path for upstream
  //    attribution; the public event type does not contain clientContext.
  //
  // `onModelDiagnosticEvent` and `onTrustedDiagnosticEvent` were added to the
  // plugin-sdk in separate openclaw PRs; older runtimes export only
  // `onDiagnosticEvent`, so we guard both calls to keep the bridge compatible.
  const hasTrusted = typeof onTrustedDiagnosticEvent === "function"

  // When the trusted lifecycle channel is available, the public stream must
  // not also process session.state/message.queued — those arrive (with
  // privateData) via onTrustedDiagnosticEvent below, so processing them here
  // too would create the root span twice. Without the trusted channel we handle
  // them on the public stream with local attribution only.
  const publicListener = hasTrusted
    ? (evt: unknown) => {
        const type = (evt as { type?: string }).type
        if (type && TRUSTED_LIFECYCLE_TYPES.has(type)) return
        listener(evt)
      }
    : (evt: unknown) => listener(evt)

  const unsubMain = (onDiagnosticEvent as (l: (evt: unknown) => void) => () => void)(publicListener)
  console.log("[agentweave-bridge] subscribed to diagnostic events via plugin-sdk")

  let unsubModel: (() => void) | undefined
  if (typeof onModelDiagnosticEvent === "function") {
    unsubModel = (onModelDiagnosticEvent as (l: (evt: unknown) => void) => () => void)(listener)
    console.log("[agentweave-bridge] subscribed to model.* trusted events via plugin-sdk")
  } else {
    console.warn(
      "[agentweave-bridge] openclaw plugin-sdk is missing onModelDiagnosticEvent — codex turn spans will not be enriched with prov.llm.model. Upgrade openclaw to a build that exports it.",
    )
  }

  let unsubTrusted: (() => void) | undefined
  if (hasTrusted) {
    unsubTrusted = (
      onTrustedDiagnosticEvent as (l: (evt: unknown, priv: unknown) => void) => () => void
    )((evt, privateData) => listener(evt, privateData))
    console.log("[agentweave-bridge] subscribed to trusted lifecycle clientContext via plugin-sdk")
  } else {
    console.warn(
      "[agentweave-bridge] openclaw plugin-sdk is missing onTrustedDiagnosticEvent — upstream clientContext attribution falls back to nix-v1. Upgrade openclaw to a build that exports it.",
    )
  }

  return () => {
    unsubMain()
    if (unsubModel) unsubModel()
    if (unsubTrusted) unsubTrusted()
  }
}

function getSpanSessionId(turn: ActiveTurn): string | undefined {
  return turn.lookupSessionId ?? (turn.span as any)?._attributes?.["session.id"] as string | undefined
}

function firstString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value !== "string") continue
    const trimmed = value.trim()
    if (trimmed) return trimmed
  }
  return undefined
}

function resolveUpstreamParentSessionAttribution(
  parentSessionId: string | undefined,
  sessionCorrelationId: string | undefined,
): string | undefined {
  if (!parentSessionId) return undefined
  if (!sessionCorrelationId) return parentSessionId

  for (const [sessionKey, turn] of activeTurns) {
    if ((sessionKey === parentSessionId || turn.lookupSessionId === parentSessionId) && turn.sessionCorrelationId) {
      return turn.sessionCorrelationId
    }
  }
  return undefined
}

/** Schema discriminator for the upstream context the gateway forwards. */
const AGENTWEAVE_CONTEXT_SCHEMA = "agentweave.context.v1"

/** Upstream attribution forwarded by an orchestrator (e.g. Paperclip/Conductor). */
interface UpstreamAgentContext {
  source?: string
  sessionId?: string
  agentId?: string
  agentType?: string
  taskLabel?: string
  parentSessionId?: string
  paperclip?: { runId?: string; issueId?: string; taskId?: string }
}

/**
 * Read upstream attribution from an opaque OpenClaw diagnostic `clientContext`
 * bag. Returns undefined unless the bag declares the recognized schema, so
 * unrelated client context never hijacks attribution. Parses defensively — the
 * bag is plugin-trusted but still externally supplied data.
 */
function resolveUpstreamContext(clientContext: unknown): UpstreamAgentContext | undefined {
  if (!clientContext || typeof clientContext !== "object") return undefined
  const ctx = clientContext as Record<string, unknown>
  if (ctx.schemaVersion !== AGENTWEAVE_CONTEXT_SCHEMA) return undefined
  const paperclipRaw =
    ctx.paperclip && typeof ctx.paperclip === "object"
      ? (ctx.paperclip as Record<string, unknown>)
      : undefined
  const paperclip = paperclipRaw
    ? {
        runId: firstString(paperclipRaw.runId),
        issueId: firstString(paperclipRaw.issueId),
        taskId: firstString(paperclipRaw.taskId),
      }
    : undefined
  return {
    source: firstString(ctx.source),
    sessionId: firstString(ctx.sessionId),
    agentId: firstString(ctx.agentId),
    agentType: firstString(ctx.agentType),
    taskLabel: firstString(ctx.taskLabel),
    parentSessionId: firstString(ctx.parentSessionId),
    paperclip,
  }
}

/** Stamp upstream run/issue ids onto a span for filtering/debugging in Tempo. */
function applyUpstreamContextAttrs(span: Span, upstream: UpstreamAgentContext | undefined): void {
  if (!upstream) return
  if (upstream.source) span.setAttribute("prov.upstream.source", upstream.source)
  if (upstream.paperclip?.runId) span.setAttribute("prov.upstream.run_id", upstream.paperclip.runId)
  if (upstream.paperclip?.issueId) span.setAttribute("prov.upstream.issue_id", upstream.paperclip.issueId)
  if (upstream.paperclip?.taskId) span.setAttribute("prov.upstream.task_id", upstream.paperclip.taskId)
}

function resolveOpenClawSessionId(sessionKey: string, eventSessionId: unknown): { sessionId: string; canonicalUuid?: string } {
  const candidate = firstString(eventSessionId)
  const keyLeaf = sessionKey.split(":").pop()
  // Some older OpenClaw diagnostic events used the bare route leaf ("main",
  // "worker-a") as sessionId. That is not the canonical transcript UUID.
  const isBareRouteAlias = Boolean(candidate && sessionKey && candidate === keyLeaf && candidate !== sessionKey)
  const canonicalUuid = candidate && !isBareRouteAlias ? candidate : undefined
  return { sessionId: canonicalUuid ?? sessionKey, canonicalUuid }
}

function setActiveTurn(key: string, turn: ActiveTurn): void {
  activeTurns.set(key, turn)
}

function deleteActiveTurn(key: string): void {
  const turn = activeTurns.get(key)
  if (turn) {
    for (const [runId, mapped] of activeTurnsByRunId) if (mapped === turn) activeTurnsByRunId.delete(runId)
    for (const [callId, mapped] of activeTurnsByCallId) if (mapped === turn) activeTurnsByCallId.delete(callId)
  }
  activeTurns.delete(key)
}

function truncatePreview(value: string, maxChars = 1000): string {
  const normalized = value.replace(/\s+/g, " ").trim()
  if (normalized.length <= maxChars) return normalized
  return `${normalized.slice(0, Math.max(0, maxChars - 1)).trimEnd()}…`
}

function resolveInputPreview(evt: { inputPreview?: string }, fallback?: string): string | undefined {
  const preview = firstString(evt.inputPreview, fallback)
  return preview ? truncatePreview(preview) : undefined
}

function applyLangfuseAgentTurnAttrs(span: Span, params: {
  sessionId?: string
  sessionKey?: string
  project?: string
  agentId?: string
  agentType?: string
  parentSessionId?: string
  taskLabel?: string
  inputPreview?: string
}): void {
  span.setAttribute("langfuse.observation.type", "agent")
  if (params.sessionId) span.setAttribute("langfuse.session.id", params.sessionId)
  if (params.taskLabel) {
    span.setAttribute("langfuse.trace.name", params.taskLabel)
    span.setAttribute("langfuse.trace.metadata.task_label", params.taskLabel)
  } else if (params.sessionId) {
    span.setAttribute("langfuse.trace.name", params.sessionId)
  }
  if (params.project) span.setAttribute("langfuse.trace.metadata.project", params.project)
  if (params.agentId) span.setAttribute("langfuse.trace.metadata.agent_id", params.agentId)
  if (params.agentType) span.setAttribute("langfuse.trace.metadata.agent_type", params.agentType)
  if (params.sessionKey) span.setAttribute("langfuse.trace.metadata.session_key", params.sessionKey)
  if (params.parentSessionId) span.setAttribute("langfuse.trace.metadata.parent_session_id", params.parentSessionId)
  span.setAttribute("langfuse.trace.metadata.activity_type", "agent_turn")
  if (params.inputPreview) {
    span.setAttribute("prov.input.preview", params.inputPreview)
    span.setAttribute("langfuse.observation.input", params.inputPreview)
  }
}

function findTurnForModelUsage(sessionKey: string, sessionId: string): { key: string; turn: ActiveTurn; reason: string } | null {
  const activeKeys = Array.from(activeTurns.keys())

  if (sessionKey && activeTurns.has(sessionKey)) {
    return { key: sessionKey, turn: activeTurns.get(sessionKey)!, reason: "sessionKey-exact" }
  }

  if (sessionId) {
    const bySessionId = activeKeys.filter(key => getSpanSessionId(activeTurns.get(key)!) === sessionId)
    if (bySessionId.length === 1) {
      return { key: bySessionId[0], turn: activeTurns.get(bySessionId[0])!, reason: "sessionId-span-attr" }
    }

    if (activeTurns.has(sessionId)) {
      return { key: sessionId, turn: activeTurns.get(sessionId)!, reason: "sessionId-as-key" }
    }
  }

  return null
}

function findTurnForModelCall(sessionKey: string, sessionId: string, runId?: string, callId?: string) {
  const keyedCallId = runId && callId ? `${runId}:${callId}` : undefined
  const mapped = (keyedCallId && activeTurnsByCallId.get(keyedCallId)) || (runId && activeTurnsByRunId.get(runId))
  if (mapped) {
    const key = Array.from(activeTurns.keys()).find(candidate => activeTurns.get(candidate) === mapped)
    return key ? { key, turn: mapped, reason: "runId-callId-exact" } : null
  }
  return findTurnForModelUsage(sessionKey, sessionId)
}

/**
 * Start a root span for a gateway-`agent` run from a session.state event.
 *
 * Gateway `agent` runs (e.g. Paperclip) start the embedded runner directly and
 * only emit session.state (+ model.call.*) — they never emit the message.queued
 * event the normal root-span path keys on. When OpenClaw seeds upstream context
 * (agentweave.context.v1) onto such a session, this starts the equivalent
 * upstream-attributed root span and forces the proxy to attribute LLM calls to
 * that identity. The span is ended on the session.state idle transition
 * (endOnIdle), since no message.processed event fires for this path.
 */
function startUpstreamRootSpanFromSessionState(
  e: Extract<HostDiagnosticEvent, { type: "session.state" }>,
  sessionKey: string,
  upstream: UpstreamAgentContext,
  config: BridgeConfig,
  sessionCorrelationId?: string,
): void {
  const identity = resolveOpenClawSessionId(sessionKey, e.sessionId)
  const effectiveSessionId = upstream.sessionId ?? identity.sessionId
  const attributedSessionId = sessionCorrelationId ?? effectiveSessionId
  const proxySessionKey = sessionCorrelationId ?? sessionKey
  const exportedSessionKey = sessionCorrelationId ? undefined : sessionKey
  const agentId = upstream.agentId ?? config.agentId ?? "nix-v1"
  const agentType = upstream.agentType ?? "main"

  const tracer = trace.getTracer("openclaw-agentweave-bridge")
  const span = tracer.startSpan("openclaw.turn")
  span.setAttribute("session_id", attributedSessionId)
  span.setAttribute("session.id", attributedSessionId)
  span.setAttribute("prov.session.id", attributedSessionId)
  if (exportedSessionKey) span.setAttribute("prov.session.key", exportedSessionKey)
  if (!sessionCorrelationId && identity.canonicalUuid && identity.canonicalUuid !== sessionKey) {
    span.setAttribute("prov.session.uuid", identity.canonicalUuid)
  }
  span.setAttribute("prov.harness", "openclaw")
  span.setAttribute("prov.agent.id", agentId)
  span.setAttribute("prov.agent.type", agentType)
  span.setAttribute("prov.activity.type", "agent_turn")
  if (config.project) span.setAttribute("prov.project", config.project)
  applyUpstreamContextAttrs(span, upstream)
  const taskLabel = upstream.taskLabel ?? firstString(e.taskLabel)
  if (taskLabel) span.setAttribute("prov.task.label", taskLabel)
  const inputPreview = resolveInputPreview(e, taskLabel)

  const parentSid = resolveUpstreamParentSessionAttribution(
    upstream.parentSessionId,
    sessionCorrelationId,
  )
  if (parentSid) {
    span.setAttribute("prov.parent.session.id", parentSid)
    process.env.AGENTWEAVE_PARENT_SESSION_ID = parentSid
  } else {
    delete process.env.AGENTWEAVE_PARENT_SESSION_ID
  }
  applyLangfuseAgentTurnAttrs(span, {
    sessionId: attributedSessionId,
    sessionKey: exportedSessionKey,
    project: config.project,
    agentId,
    agentType,
    parentSessionId: parentSid,
    taskLabel,
    inputPreview,
  })

  const spanCtx = trace.setSpan(context.active(), span)
  const carrier: Record<string, string> = {}
  propagation.inject(spanCtx, carrier)
  if (carrier["traceparent"]) {
    process.env.AGENTWEAVE_TRACEPARENT = carrier["traceparent"]
  }
  let parentTraceIdHex = ""
  let parentSpanIdHex = ""
  const spanContext = span.spanContext()
  if (spanContext) {
    parentTraceIdHex = spanContext.traceId.replace(/-/g, "").padStart(32, "0")
    parentSpanIdHex = spanContext.spanId.replace(/-/g, "").padStart(16, "0")
    process.env.AGENTWEAVE_PARENT_TRACE_ID = parentTraceIdHex
    process.env.AGENTWEAVE_PARENT_SPAN_ID = parentSpanIdHex
  }
  // The bridge environment is consumed by in-process SDKs. Once OpenClaw has
  // supplied a trusted correlation token, never export the raw route/session
  // identity through that channel.
  process.env.AGENTWEAVE_SESSION_ID = attributedSessionId
  process.env.AGENTWEAVE_SESSION_KEY = proxySessionKey
  process.env.AGENTWEAVE_AGENT_ID = agentId
  process.env.AGENTWEAVE_AGENT_TYPE = agentType
  const proxyBaseUrl = normalizeProxyBaseUrl(config.proxyUrl)
  if (proxyBaseUrl) {
    process.env.ANTHROPIC_BASE_URL = proxyBaseUrl
    process.env.OPENAI_BASE_URL = proxyBaseUrl
    process.env.OPENAI_API_BASE = proxyBaseUrl
  }

  setActiveTurn(sessionKey, {
    span,
    ctx: spanCtx,
    endOnIdle: true,
    lookupSessionId: effectiveSessionId,
    sessionCorrelationId,
  })
  console.log(`[agentweave-bridge] started root span for ${agentType} session:`, effectiveSessionId, "agent:", agentId)

  if (proxyBaseUrl) {
    const sessionPayload: Record<string, unknown> = {
      session_id: attributedSessionId,
      // Forced proxy contexts are keyed by the value returned in the request
      // header. A trusted token is opaque and therefore safe as that key.
      session_key: proxySessionKey,
      agent_id: agentId,
      agent_type: agentType,
      harness: "openclaw",
      // Force the proxy to attribute LLM calls to the upstream identity.
      force: true,
    }
    if (config.project) sessionPayload.project = config.project
    if (parentSid) sessionPayload.parent_session_id = parentSid
    if (taskLabel) sessionPayload.task_label = taskLabel
    if (!sessionCorrelationId && identity.canonicalUuid) sessionPayload.session_uuid = identity.canonicalUuid
    if (upstream.paperclip?.runId) sessionPayload.run_id = upstream.paperclip.runId
    if (upstream.paperclip?.issueId) sessionPayload.issue_id = upstream.paperclip.issueId
    if (parentTraceIdHex && parentSpanIdHex) {
      sessionPayload.parent_trace_id = parentTraceIdHex
      sessionPayload.parent_span_id = parentSpanIdHex
    }
    fetch(`${proxyBaseUrl}/session`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-agentweave-session-key": proxySessionKey,
      },
      body: JSON.stringify(sessionPayload),
    }).then(() => console.log(`[agentweave-bridge] proxy session set for ${agentType}: ${effectiveSessionId}`))
      .catch(err => console.warn(`[agentweave-bridge] proxy session set failed:`, err.message))
  }
}

function endUpstreamRootSpanOnIdle(sessionKey: string): void {
  const turn = activeTurns.get(sessionKey)
  if (!turn?.endOnIdle) return
  turn.span.setAttribute("outcome", "completed")
  turn.span.end()
  deleteActiveTurn(sessionKey)
  delete process.env.AGENTWEAVE_TRACEPARENT
  delete process.env.AGENTWEAVE_PARENT_TRACE_ID
  delete process.env.AGENTWEAVE_PARENT_SPAN_ID
  delete process.env.ANTHROPIC_BASE_URL
  delete process.env.OPENAI_BASE_URL
  delete process.env.OPENAI_API_BASE
  delete process.env.AGENTWEAVE_AGENT_ID
  delete process.env.AGENTWEAVE_AGENT_TYPE
  delete process.env.AGENTWEAVE_PARENT_SESSION_ID
  delete process.env.AGENTWEAVE_SESSION_ID
  delete process.env.AGENTWEAVE_SESSION_KEY
  console.log("[agentweave-bridge] ended upstream root span for session:", sessionKey)
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

      unsubscribe = subscribeToDiagnosticEvents((evt: unknown, privateData?: unknown) => {
        const e = evt as HostDiagnosticEvent
        // The host's public event union has no clientContext. Upstream
        // attribution is accepted only from the trusted privateData channel;
        // older hosts without it use local attribution.
        const trustedPrivateData = privateData as HostDiagnosticPrivateData | undefined
        const clientContext = trustedPrivateData?.clientContext
        // `privateData` is passed only by onTrustedDiagnosticEvent for lifecycle
        // events; public and model callbacks invoke this listener without it.
        const sessionCorrelationId = trustedPrivateData?.sessionCorrelationId
        console.log(
          "[agentweave-bridge] event:", e.type,
          "sessionKey:", "sessionKey" in e ? e.sessionKey : undefined,
          "source:", "source" in e ? e.source : undefined,
        )
        try {
          switch (e.type) {
            case "message.queued": {
              const sessionKey = e.sessionKey ?? ""
              if (!sessionKey) break
              const identity = resolveOpenClawSessionId(sessionKey, e.sessionId)

              const { agentId, agentType, parentSessionKey } = resolveAgentId(sessionKey, config, activeTurns, e.source)

              // Prefer upstream attribution (e.g. Paperclip/Conductor) when the
              // gateway forwarded an agentweave.context.v1 bag; otherwise keep
              // the local nix-v1 fallback derived above.
              const upstream = resolveUpstreamContext(clientContext)
              const effectiveSessionId = upstream?.sessionId ?? identity.sessionId
              const attributedSessionId = sessionCorrelationId ?? effectiveSessionId
              const proxySessionKey = sessionCorrelationId ?? sessionKey
              const exportedSessionKey = sessionCorrelationId ? undefined : sessionKey
              const effectiveAgentId = upstream?.agentId ?? agentId
              const effectiveAgentType = upstream?.agentType ?? agentType

              const tracer = trace.getTracer("openclaw-agentweave-bridge")
              const span = tracer.startSpan("openclaw.turn")
              span.setAttribute("session_id", attributedSessionId)
              span.setAttribute("session.id", attributedSessionId)
              span.setAttribute("prov.session.id", attributedSessionId)
              // Preserve the qualified route key for compatibility only when
              // OpenClaw did not provide its opaque correlation token.
              if (exportedSessionKey) span.setAttribute("prov.session.key", exportedSessionKey)
              if (!sessionCorrelationId && identity.canonicalUuid && identity.canonicalUuid !== sessionKey) {
                span.setAttribute("prov.session.uuid", identity.canonicalUuid)
              }
              span.setAttribute("prov.harness", "openclaw")
              span.setAttribute("prov.agent.id", effectiveAgentId)
              span.setAttribute("prov.agent.type", effectiveAgentType)
              span.setAttribute("prov.activity.type", "agent_turn")
              if (e.channel) span.setAttribute("channel", e.channel)
              if (config.project) span.setAttribute("prov.project", config.project)
              applyUpstreamContextAttrs(span, upstream)
              const taskLabel = upstream?.taskLabel
              if (taskLabel) span.setAttribute("prov.task.label", taskLabel)
              const inputPreview = resolveInputPreview(e, taskLabel)

              // Link to parent session: an explicit upstream parent wins;
              // otherwise fall back to the sub-agent concurrent-turn heuristic.
              let parentSid = resolveUpstreamParentSessionAttribution(
                upstream?.parentSessionId,
                sessionCorrelationId,
              )
              if (parentSid) {
                span.setAttribute("prov.parent.session.id", parentSid)
                process.env.AGENTWEAVE_PARENT_SESSION_ID = parentSid
              } else if (agentType === "subagent") {
                // Use parentSessionKey from concurrent-turn heuristic, or find any active main turn
                const parentKey = parentSessionKey
                  ?? Array.from(activeTurns.keys()).find(k => k.startsWith("agent:main:") && !k.startsWith("agent:main:subagent:"))
                if (parentKey) {
                  const parentTurn = activeTurns.get(parentKey)
                  // A tokenized child may reference only an active parent's
                  // opaque attribution. Do not fall back to a raw route key.
                  const resolvedParentSessionId = sessionCorrelationId
                    ? parentTurn?.sessionCorrelationId
                    : parentKey
                  if (resolvedParentSessionId) {
                    span.setAttribute("prov.parent.session.id", resolvedParentSessionId)
                    process.env.AGENTWEAVE_PARENT_SESSION_ID = resolvedParentSessionId
                  }
                  parentSid = resolvedParentSessionId
                }
              }
              if (!parentSid) delete process.env.AGENTWEAVE_PARENT_SESSION_ID
              applyLangfuseAgentTurnAttrs(span, {
                sessionId: attributedSessionId,
                sessionKey: exportedSessionKey,
                project: config.project,
                agentId: effectiveAgentId,
                agentType: effectiveAgentType,
                parentSessionId: parentSid,
                taskLabel,
                inputPreview,
              })

              const spanCtx = trace.setSpan(context.active(), span)
              const carrier: Record<string, string> = {}
              propagation.inject(spanCtx, carrier)
              if (carrier["traceparent"]) {
                process.env.AGENTWEAVE_TRACEPARENT = carrier["traceparent"]
              }

              // Capture parent span/trace IDs for the /session POST below so
              // the proxy can build links[] on llm_call spans (issue #178).
              // process.env is set as a best-effort signal for any in-process
              // sub-agent code that inherits env, but the proxy is a separate
              // process and reads these via POST /session — that is the
              // authoritative path.
              let parentTraceIdHex = ""
              let parentSpanIdHex = ""
              const spanContext = span.spanContext()
              if (spanContext) {
                parentTraceIdHex = spanContext.traceId.replace(/-/g, "").padStart(32, "0")
                parentSpanIdHex = spanContext.spanId.replace(/-/g, "").padStart(16, "0")
                process.env.AGENTWEAVE_PARENT_TRACE_ID = parentTraceIdHex
                process.env.AGENTWEAVE_PARENT_SPAN_ID = parentSpanIdHex
              }
              process.env.AGENTWEAVE_SESSION_ID = attributedSessionId
              process.env.AGENTWEAVE_SESSION_KEY = proxySessionKey
              process.env.AGENTWEAVE_AGENT_ID = effectiveAgentId
              process.env.AGENTWEAVE_AGENT_TYPE = effectiveAgentType
              const proxyBaseUrl = normalizeProxyBaseUrl(config.proxyUrl)
              if (proxyBaseUrl) {
                process.env.ANTHROPIC_BASE_URL = proxyBaseUrl
                process.env.OPENAI_BASE_URL = proxyBaseUrl
                process.env.OPENAI_API_BASE = proxyBaseUrl
              }

              setActiveTurn(sessionKey, {
                span,
                ctx: spanCtx,
                lookupSessionId: effectiveSessionId,
                sessionCorrelationId,
              })
              console.log(`[agentweave-bridge] started root span for ${effectiveAgentType} session:`, effectiveSessionId, "agent:", effectiveAgentId)

              // Push session context into the proxy so its _session_context dict
              // includes session_id / agent_id / project / task_label for the
              // upcoming LLM calls. Bridge sets env vars too, but the proxy
              // snapshots env only at startup — POST /session is the live path.
              const proxyBaseUrlForSession = normalizeProxyBaseUrl(config.proxyUrl)
              if (proxyBaseUrlForSession) {
                // Upstream parent wins; else the sub-agent concurrent-turn heuristic.
                // `parentSid` has already resolved an active parent to its
                // opaque token. On tokenized turns it is the only parent
                // identity that may leave the bridge.
                const proxyParentSid = parentSid
                  ?? (!sessionCorrelationId && effectiveAgentType === "subagent"
                    ? (parentSessionKey
                      ?? Array.from(activeTurns.keys()).find(k => k.startsWith("agent:main:") && !k.startsWith("agent:main:subagent:")))
                    : undefined)
                const sessionPayload: Record<string, unknown> = {
                  session_id: attributedSessionId,
                  session_key: proxySessionKey,
                  agent_id: effectiveAgentId,
                  agent_type: effectiveAgentType,
                  harness: "openclaw",
                  // Force the proxy to attribute LLM calls to upstream identity
                  // (same as sub-agents) so codex/model spans match the run.
                  force: Boolean(sessionCorrelationId) || Boolean(upstream) || effectiveAgentType === "subagent",
                }
                if (config.project) sessionPayload.project = config.project
                if (proxyParentSid) sessionPayload.parent_session_id = proxyParentSid
                if (taskLabel) sessionPayload.task_label = taskLabel
                if (!sessionCorrelationId && identity.canonicalUuid) sessionPayload.session_uuid = identity.canonicalUuid
                if (upstream?.paperclip?.runId) sessionPayload.run_id = upstream.paperclip.runId
                if (upstream?.paperclip?.issueId) sessionPayload.issue_id = upstream.paperclip.issueId
                if (parentTraceIdHex && parentSpanIdHex) {
                  sessionPayload.parent_trace_id = parentTraceIdHex
                  sessionPayload.parent_span_id = parentSpanIdHex
                }
                fetch(`${proxyBaseUrlForSession}/session`, {
                  method: "POST",
                  headers: {
                    "Content-Type": "application/json",
                    "x-agentweave-session-key": proxySessionKey,
                  },
                  body: JSON.stringify(sessionPayload),
                }).then(() => console.log(`[agentweave-bridge] proxy session set for ${effectiveAgentType}: ${effectiveSessionId}`))
                  .catch(err => console.warn(`[agentweave-bridge] proxy session set failed:`, err.message))
              }
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
              deleteActiveTurn(sessionKey)
              delete process.env.AGENTWEAVE_TRACEPARENT
              delete process.env.AGENTWEAVE_PARENT_TRACE_ID
              delete process.env.AGENTWEAVE_PARENT_SPAN_ID
              delete process.env.ANTHROPIC_BASE_URL
              delete process.env.OPENAI_BASE_URL
              delete process.env.OPENAI_API_BASE
              delete process.env.AGENTWEAVE_AGENT_ID
              delete process.env.AGENTWEAVE_AGENT_TYPE
              delete process.env.AGENTWEAVE_PARENT_SESSION_ID
              delete process.env.AGENTWEAVE_SESSION_ID
              delete process.env.AGENTWEAVE_SESSION_KEY
              console.log("[agentweave-bridge] ended root span for session:", sessionKey)
              // Don't POST a clear — /session replaces the whole context dict,
              // so {task_label: ""} would wipe session_id/agent_id too. The
              // next message.queued POST will overwrite cleanly.
              break
            }

            case "session.state": {
              const sessionKey = e.sessionKey ?? ""
              const state = e.state
              // Detect OpenClaw native sub-agent sessions (agent:*:subagent:*)
              // These don't emit message.queued, only session.state transitions
              if (sessionKey.includes(":subagent:") && !activeTurns.has(sessionKey)) {
                if (state === "processing") {
                  const subagentId = config.subagentId ?? `${config.agentId ?? "nix"}-subagent-v1`
                  const identity = resolveOpenClawSessionId(sessionKey, e.sessionId)
                  const sessionId = identity.sessionId
                  const attributedSessionId = sessionCorrelationId ?? sessionId
                  const proxySessionKey = sessionCorrelationId ?? sessionKey
                  const exportedSessionKey = sessionCorrelationId ? undefined : sessionKey
                  const tracer = trace.getTracer("openclaw-agentweave-bridge")
                  const span = tracer.startSpan("openclaw.subagent")
                  span.setAttribute("session_id", attributedSessionId)
                  span.setAttribute("session.id", attributedSessionId)
                  span.setAttribute("prov.session.id", attributedSessionId)
                  if (exportedSessionKey) span.setAttribute("prov.session.key", exportedSessionKey)
                  if (!sessionCorrelationId && identity.canonicalUuid && identity.canonicalUuid !== sessionKey) {
                    span.setAttribute("prov.session.uuid", identity.canonicalUuid)
                  }
                  span.setAttribute("prov.harness", "openclaw")
                  span.setAttribute("prov.agent.id", subagentId)
                  span.setAttribute("prov.agent.type", "subagent")
                  span.setAttribute("prov.activity.type", "agent_turn")
                  if (config.project) span.setAttribute("prov.project", config.project)
                  const taskLabel = firstString(e.taskLabel)
                  if (taskLabel) span.setAttribute("prov.task.label", taskLabel)
                  const inputPreview = resolveInputPreview(e, taskLabel)
                  // Link to active main session as parent
                  const mainKey = Array.from(activeTurns.keys()).find(k =>
                    k.startsWith("agent:main:") && !k.includes(":subagent:"))
                  let parentSessionId: string | undefined
                  if (mainKey) {
                    const mainTurn = activeTurns.get(mainKey)
                    // A tokenized child may reference only an active parent's
                    // opaque attribution. Do not fall back to a raw route key.
                    const mainSessionId = sessionCorrelationId
                      ? mainTurn?.sessionCorrelationId
                      : mainKey
                    if (mainSessionId) {
                      span.setAttribute("prov.parent.session.id", mainSessionId)
                      parentSessionId = mainSessionId
                    }
                  }
                  if (parentSessionId) {
                    process.env.AGENTWEAVE_PARENT_SESSION_ID = parentSessionId
                  } else {
                    delete process.env.AGENTWEAVE_PARENT_SESSION_ID
                  }
                  applyLangfuseAgentTurnAttrs(span, {
                    sessionId: attributedSessionId,
                    sessionKey: exportedSessionKey,
                    project: config.project,
                    agentId: subagentId,
                    agentType: "subagent",
                    parentSessionId,
                    taskLabel,
                    inputPreview,
                  })
                  const spanCtx = trace.setSpan(context.active(), span)
                  process.env.AGENTWEAVE_SESSION_ID = attributedSessionId
                  process.env.AGENTWEAVE_SESSION_KEY = proxySessionKey
                  setActiveTurn(sessionKey, {
                    span,
                    ctx: spanCtx,
                    lookupSessionId: sessionId,
                    sessionCorrelationId,
                  })

                  // Force the proxy to attribute LLM calls to this sub-agent session
                  const proxyUrl = normalizeProxyBaseUrl(config.proxyUrl) || "http://192.168.1.70:30400"
                  const mainSessionId = parentSessionId ?? (sessionCorrelationId ? undefined : (mainKey ? (activeTurns.get(mainKey)?.span as any)?._attributes?.["session.id"] || "nix-main" : "nix-main"))
                  // Issue #189: include session_key so the proxy stores this
                  // forced context per-key in _forced_session_contexts, instead
                  // of toggling the legacy global _session_context_force flag
                  // (which would hijack attribution for unrelated callers).
                  fetch(`${proxyUrl}/session`, {
                    method: "POST",
                    headers: {
                      "Content-Type": "application/json",
                      "x-agentweave-session-key": proxySessionKey,
                    },
                    body: JSON.stringify({
                      session_key: proxySessionKey,
                      session_id: attributedSessionId,
                      ...(!sessionCorrelationId && identity.canonicalUuid ? { session_uuid: identity.canonicalUuid } : {}),
                      ...(mainSessionId ? { parent_session_id: mainSessionId } : {}),
                      agent_id: subagentId,
                      agent_type: "subagent",
                      harness: "openclaw",
                      ...(taskLabel ? { task_label: taskLabel } : (sessionCorrelationId ? {} : { task_label: `subagent ${sessionKey.split(":")[1] || "unknown"}` })),
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
                  const proxySessionKey = turn.sessionCorrelationId ?? sessionKey
                  const attributedSessionId = turn.sessionCorrelationId ?? getSpanSessionId(turn) ?? "nix-main"
                  turn.span.setAttribute("outcome", "completed")
                  turn.span.end()
                  deleteActiveTurn(sessionKey)

                  // Restore proxy to main session — clear the per-key forced
                  // context for this sessionKey (issue #189). force:false +
                  // matching session_key removes the entry from
                  // _forced_session_contexts without touching the legacy
                  // global flag.
                  const proxyUrl = normalizeProxyBaseUrl(config.proxyUrl) || "http://192.168.1.70:30400"
                  fetch(`${proxyUrl}/session`, {
                    method: "POST",
                    headers: {
                      "Content-Type": "application/json",
                      "x-agentweave-session-key": proxySessionKey,
                    },
                    body: JSON.stringify({
                      session_key: proxySessionKey,
                      session_id: attributedSessionId,
                      agent_type: "main",
                      force: false,
                    }),
                  }).then(() => console.log(`[agentweave-bridge] proxy session restored to nix-main`))
                    .catch(err => console.warn(`[agentweave-bridge] proxy session restore failed:`, err.message))

                  delete process.env.AGENTWEAVE_SESSION_ID
                  delete process.env.AGENTWEAVE_SESSION_KEY

                  console.log(`[agentweave-bridge] ended subagent span: ${sessionKey}`)
                }
              }
              // Gateway `agent` runs (e.g. Paperclip) emit session.state but no
              // message.queued for the initial turn, so start the upstream root
              // span here when OpenClaw seeded an agentweave.context.v1 bag onto
              // the session, and end it on the idle transition. Non-subagent
              // keys only; subagent sessions are handled above.
              if (!sessionKey.includes(":subagent:")) {
                const upstream = resolveUpstreamContext(clientContext)
                if (state === "processing" && upstream && !activeTurns.has(sessionKey)) {
                  startUpstreamRootSpanFromSessionState(
                    e,
                    sessionKey,
                    upstream,
                    config,
                    sessionCorrelationId,
                  )
                } else if (state === "idle") {
                  endUpstreamRootSpanOnIdle(sessionKey)
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

            case "model.call.started":
            case "model.call.completed":
            case "model.call.error": {
              // OpenClaw's embedded codex/Responses runner emits this — NOT
              // `model.usage` (which only fires from the legacy openai-compat
              // HTTP path). Without a handler, codex turn spans land in
              // Tempo without `prov.llm.{provider,model}`, so the dashboard's
              // "Calls by Model" panel can't bucket them.
              //
              const sessionKey = e.sessionKey ?? ""
              const sessionId = e.sessionId ?? ""
              const runId = firstString((e as Record<string, unknown>).runId)
              const callId = firstString((e as Record<string, unknown>).callId)
              const match = findTurnForModelCall(sessionKey, sessionId, runId, callId)
              if (!match) break
              if (runId) {
                activeTurnsByRunId.set(runId, match.turn)
                match.turn.span.setAttribute("prov.openclaw.run.id", runId)
              }
              if (runId && callId) {
                activeTurnsByCallId.set(`${runId}:${callId}`, match.turn)
                match.turn.span.setAttribute("prov.openclaw.call.id", callId)
              }
              const provider = e.provider ?? ""
              const model = e.model ?? ""
              if (!provider && !model) break
              if (provider) match.turn.span.setAttribute("prov.llm.provider", provider)
              if (model) match.turn.span.setAttribute("prov.llm.model", model)
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
        deleteActiveTurn(key)
      }
      delete process.env.AGENTWEAVE_TRACEPARENT
      delete process.env.ANTHROPIC_BASE_URL
      delete process.env.OPENAI_BASE_URL
      delete process.env.OPENAI_API_BASE
      delete process.env.AGENTWEAVE_SESSION_ID
      delete process.env.AGENTWEAVE_SESSION_KEY
      if (sdk) { await sdk.shutdown(); sdk = null }
    },
  }
}
