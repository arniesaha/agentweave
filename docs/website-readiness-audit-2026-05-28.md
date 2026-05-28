# AgentWeave Website Readiness Audit

Date: 2026-05-28

## Verdict

AgentWeave is ready for a developer-preview website, not a broad "production
platform" launch. The core story is credible because the repo now has a real
proxy, Python/TypeScript/Go SDKs, OpenClaw bridge dogfooding, a dashboard,
framework examples, trace quality checks, and a live v0.3.1 deployment.

The public positioning should be honest:

- Agent runtime observability for multi-agent systems.
- OpenTelemetry-native, self-hostable, and provider/framework agnostic.
- Focused on causality across agent boundaries, not prompt management or evals.
- Developer preview with dogfooded integrations and an active compatibility
  matrix.

## README Updates

The README is directionally strong, but a few sections have drifted from the
current repo state.

1. Update auto-instrumentation support.
   The README says `auto_instrument()` patches Anthropic and OpenAI only. The
   Python SDK also supports Google GenAI in direct mode. The docs should split
   this into:
   - direct mode: Anthropic, OpenAI, Google GenAI, sync and async non-streaming;
   - proxy mode: Anthropic, OpenAI-compatible, and Gemini-compatible traffic by
     base URL;
   - streaming: fully handled by the proxy, still deferred/limited in direct
     auto-instrumentation.

2. Normalize proxy commands.
   The README currently uses both the new lifecycle command and older foreground
   command:
   - preferred quickstart: `pip install "agentweave-sdk[proxy]"` and
     `agentweave start --port 4000 --endpoint http://localhost:4318`;
   - advanced foreground mode: `agentweave proxy start ...`;
   - avoid `pip install "agentweave[proxy]"`, which is the wrong package name.

3. Fix attribute naming drift.
   Public docs should consistently use `prov.parent.session.id`, not
   `prov.parent_session_id`. The old spelling still appears in
   `docs/compatibility.md`, `docs/claude-code-hooks.md`, and one shell hook.

4. Expand the attribute contract.
   The PROV-O table should include the attributes that now matter for product
   demos and dashboard queries:
   - `session.id`
   - `prov.session.id`
   - `prov.parent.session.id`
   - `prov.agent.type`
   - `prov.session.turn`
   - `prov.project`
   - `prov.task.label`
   - `prov.cwd`
   - `prov.repository`
   - `prov.llm.model`
   - `cost.usd`
   - `tokens.cache_read`
   - `tokens.cache_write`
   - `cache.hit_rate`

5. Add a GenAI semantic attributes section.
   AgentWeave already dual-emits several `gen_ai.*` fields. The docs should say
   exactly which ones are emitted:
   - `gen_ai.operation.name`
   - `gen_ai.request.model`
   - `gen_ai.usage.input_tokens`
   - `gen_ai.usage.output_tokens`
   - `gen_ai.response.finish_reasons`
   - `gen_ai.agent.name`
   - currently `gen_ai.system`

   The installed OpenTelemetry semantic convention package marks
   `gen_ai.system` as deprecated in favor of `gen_ai.provider.name`. The repo
   should add `gen_ai.provider.name` while keeping `gen_ai.system` for backward
   compatibility during the preview line.

6. Refresh stale status details.
   `docs/STATUS.md` still says the public quickstart should use
   `agentweave proxy start`; the README now prefers `agentweave start`.
   It also says auto-instrumentation is Anthropic/OpenAI only. The development
   test count in the README is also stale; local post-merge Python validation
   is currently 480 passing tests across `sdk/python/tests` and `tests`.

## Architecture Diagram

The current README diagram is useful for explaining provider proxying, but it
is too narrow for the website. It makes AgentWeave look like a base URL proxy
with screenshots attached. The stronger product story is runtime causality.

Revamp the public architecture diagram around five layers:

1. Agent runtimes
   Claude Code, OpenClaw, LangGraph, CrewAI, AutoGen, custom Python/JS/Go
   agents, cron jobs, and sub-agents.

2. Instrumentation paths
   SDK decorators, `auto_instrument()`, provider proxy, and runtime bridge
   headers.

3. AgentWeave context and span contract
   Session ID, parent session ID, agent ID, project, task label, cwd/repository,
   model, tokens, cost, cache, and GenAI semantic attributes.

4. OpenTelemetry backend
   OTLP Collector, Tempo, Jaeger, Langfuse, or any compatible backend.

5. AgentWeave dashboard
   Overview, session graph, routing, replay/debug, and trace quality checks.

The diagram should show parent/child agent sessions as a causal graph. Provider
routing should be one lane, not the whole architecture.

## Website Direction

Recommended first-draft positioning:

> Observability for agent systems, not just LLM calls.

The first page should be product-first and concrete:

1. Hero: short positioning, one terminal quickstart, one real dashboard/session
   screenshot.
2. Problem: agent systems lose causality across process, machine, and provider
   boundaries.
3. Three instrumentation paths: decorators, auto-instrumentation, and proxy.
4. Session graph: parent session, delegated sub-agent, LLM calls, tools, cost.
5. Attribute contract: PROV-O plus OpenTelemetry GenAI semantic attributes.
6. Integrations: Anthropic, OpenAI, Gemini, Claude Code, OpenClaw, LangGraph,
   CrewAI, AutoGen, OpenAI Agents SDK.
7. Dogfood proof: sanitized OpenClaw/AgentWeave trace demo and dashboard
   screenshots.
8. Developer preview CTA: install, GitHub, docs, and compatibility matrix.

Avoid overclaiming evals, prompt management, governance, or enterprise runtime
features until the repo actually ships those.

## Design References

The visual direction should be more developer-infra than AI hype.

- Trigger.dev: good reference for agent/workflow developer experience,
  concrete code snippets, and operational framing.
- Langfuse: good reference for observability-first IA, integration density, and
  open-source trust signals.
- Grafana/Tempo and SigNoz/OpenObserve: good references for sober
  observability language and OpenTelemetry positioning.
- Maple.dev: good reference for a modern OpenTelemetry-native product page with
  code-first setup.
- Linear: useful as a restraint reference for typography, spacing, and polish,
  but AgentWeave should be denser and more technical than Linear's marketing
  pages.

Recommended look: dark technical canvas, dense trace/screenshot artifacts,
small accent palette, readable monospace code, and real dashboard imagery. Do
not use abstract gradient blobs or generic AI agent illustrations.

## Follow-up Issues

- Created #217 for the live OpenClaw bridge plugin update:
  https://github.com/arniesaha/agentweave/issues/217

Suggested next repo issues:

1. README/docs cleanup for auto-instrumentation, proxy commands, and attribute
   contract.
2. Add `gen_ai.provider.name` dual-emission while retaining `gen_ai.system`.
3. Replace the README architecture diagram with a runtime-causality diagram.
4. Build the first standalone AgentWeave website draft.
