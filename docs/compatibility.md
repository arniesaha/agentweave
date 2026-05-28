# Agent Framework Compatibility

AgentWeave's developer-preview compatibility target is: an agent framework can
route LLM calls through the AgentWeave proxy with minimal or no application
code changes, and the resulting spans carry enough provenance to debug agent
identity, session context, model usage, tokens, and cost.

This page is public and local-first. The examples below assume:

```bash
agentweave start --port 4000 --endpoint http://localhost:4318
export AGENTWEAVE_PROXY_URL=http://localhost:4000/v1
```

Use your normal provider API key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or
`GEMINI_API_KEY`). AgentWeave does not require a private network, private
dashboard URL, or proxy-side key injection for these quickstarts.

## Smoke Test Command

The offline compatibility smoke checks validate that the documented examples
exist, parse, and still point framework clients at the local proxy defaults.
They do not call upstream LLM APIs and do not require provider keys.

```bash
scripts/compatibility-smoke.sh
```

The smoke suite is a fast guardrail, not a full certification run. A full
compatibility pass should run each example against a local proxy and an OTLP
collector, then inspect emitted spans for the attributes listed below.

## Compatibility Matrix

Last updated: 2026-05-27.

| Integration | Install | Minimal quickstart | Expected span attributes | Caveats | Last-tested signal |
|-------------|---------|--------------------|--------------------------|---------|--------------------|
| LangGraph | `pip install -r examples/langgraph/requirements.txt` | `cd examples/langgraph && AGENTWEAVE_PROXY_URL=http://localhost:4000/v1 OPENAI_API_KEY=... python langgraph_example.py` | `prov.agent.id` when decorators are enabled; `prov.llm.model=gpt-4o-mini`; `prov.project`; `prov.session.id` when supplied by headers/env; `prov.llm.prompt_tokens`, `prov.llm.completion_tokens`, `prov.llm.total_tokens`, `cost.usd` when the upstream response includes usage. | Proxy mode captures LLM calls. Graph node/tool spans need decorators or framework callbacks if you want full graph topology. | 2026-05-27: offline smoke validates example syntax and proxy wiring. |
| CrewAI | `pip install -r examples/crewai/requirements.txt` | `cd examples/crewai && AGENTWEAVE_PROXY_URL=http://localhost:4000/v1 OPENAI_API_KEY=... python crew_example.py` | LLM spans with `prov.llm.model`; token/cost fields when available; `prov.agent.id`, `prov.project`, and `prov.session.id` when set via AgentWeave headers/env. | Crew/agent/task boundaries are not automatically separate spans in proxy-only mode. Add SDK decorators around `crew.kickoff()` or framework callbacks for richer hierarchy. | 2026-05-27: offline smoke validates example syntax and proxy wiring. |
| AutoGen | `pip install -r examples/autogen/requirements.txt` | `cd examples/autogen && AGENTWEAVE_PROXY_URL=http://localhost:4000/v1 OPENAI_API_KEY=... python autogen_example.py` | LLM spans with `prov.llm.model`; token/cost fields when available; session/project/agent attributes when passed by headers/env. | Conversable-agent turn boundaries are not inferred by the proxy. Use explicit `X-AgentWeave-Session-Id` and `X-AgentWeave-Agent-Id` when multiple AutoGen agents share a proxy. | 2026-05-27: offline smoke validates example syntax and proxy wiring. |
| OpenAI Agents SDK | `pip install -r examples/openai-agents-sdk/requirements.txt` | `cd examples/openai-agents-sdk && AGENTWEAVE_PROXY_URL=http://localhost:4000/v1 OPENAI_API_KEY=... python agents_example.py` | LLM spans with `prov.llm.model`; token/cost fields when available; tool/agent identity when added through SDK decorators or headers. | Proxy mode sees OpenAI chat-completions traffic. Higher-level agent/tool semantics depend on the SDK's model client path and may require explicit instrumentation for full topology. | 2026-05-27: offline smoke validates example syntax and proxy wiring. |
| Claude Code | `pip install -e sdk/python[proxy]` for the local proxy; configure Claude Code with `ANTHROPIC_BASE_URL=http://localhost:4000/v1` | Start the proxy, then run Claude Code with `ANTHROPIC_BASE_URL` pointing at the proxy and `ANTHROPIC_API_KEY` set normally. | `prov.llm.model`; Anthropic token fields; `prov.agent.id`, `prov.agent.type`, `prov.project`, `prov.session.id`, and `prov.parent.session.id` when hooks or wrapper scripts pass AgentWeave headers. | Claude Code integration quality depends on wrapper/hook propagation. Without headers, LLM calls are still traced but may appear as unattributed client traffic. | 2026-05-27: documented path verified by local dogfooding; public smoke coverage is header/config only. |
| OpenClaw bridge | OpenClaw plugin/package install path; bridge uses the AgentWeave proxy and headers from OpenClaw runtime context. | Configure OpenClaw agents to send provider traffic through the proxy and propagate `X-AgentWeave-*` headers on delegated calls. | `prov.agent.id`, `prov.agent.type`, `prov.project`, `prov.session.id`, `prov.parent.session.id`, `prov.llm.model`, token fields, and `cost.usd` when usage is available. | Bridge installs are environment-specific today. Public docs should avoid private hostnames and show localhost/k8s-neutral proxy addresses. | 2026-05-27: dogfooded in OpenClaw; needs a public bridge smoke harness before marking certified. |
| Plain Anthropic SDK | `pip install anthropic agentweave-sdk[proxy]` | `ANTHROPIC_BASE_URL=http://localhost:4000/v1 ANTHROPIC_API_KEY=... python your_app.py` | `prov.llm.provider=anthropic`; `prov.llm.model`; `prov.llm.prompt_tokens`, `prov.llm.completion_tokens`, `prov.llm.total_tokens`; `cost.usd`; optional session/project/agent headers. | Streaming support and response-shape edge cases should be tested per SDK version. Proxy mode only traces calls that use the configured base URL. | 2026-05-27: supported proxy route documented; full version certification pending. |
| Plain OpenAI SDK | `pip install openai agentweave-sdk[proxy]` | `OPENAI_BASE_URL=http://localhost:4000/v1 OPENAI_API_KEY=... python your_app.py` | `prov.llm.provider=openai`; `prov.llm.model`; token fields from `usage`; `cost.usd`; optional session/project/agent headers. | Some frameworks use custom client objects; confirm they honor `base_url` or `OPENAI_BASE_URL`. | 2026-05-27: covered by framework smoke examples using OpenAI-compatible clients. |
| Plain Gemini SDK | `pip install google-genai agentweave-sdk[proxy]` | Configure Gemini SDK traffic to the proxy's Gemini-compatible route, then set `GEMINI_API_KEY` normally. | `prov.llm.provider=google`; `prov.llm.model`; token fields when the Gemini response includes usage metadata; optional session/project/agent headers. | Gemini SDK base URL configuration differs by package/version. Treat this as preview until a pinned example is added. | 2026-05-27: proxy route documented; public example and smoke coverage pending. |

## Attribute Contract

For developer preview, integrations should preserve this minimum span contract:

| Attribute | Required when | Purpose |
|-----------|---------------|---------|
| `prov.llm.model` | Every LLM span | Groups cost, latency, and quality by model. |
| `prov.llm.prompt_tokens` | Provider returns prompt usage | Cost and prompt-size analysis. |
| `prov.llm.completion_tokens` | Provider returns completion usage | Cost and output-size analysis. |
| `prov.llm.total_tokens` | Provider returns total usage or it can be derived | Cross-provider token rollups. |
| `cost.usd` | Model pricing is known | Cost rollups by agent/session/model. |
| `gen_ai.provider.name` | Every LLM span | OpenTelemetry GenAI provider discriminator. |
| `gen_ai.request.model` | Every LLM span | OpenTelemetry GenAI model attribute for backend compatibility. |
| `gen_ai.usage.input_tokens` | Provider returns prompt usage | OpenTelemetry GenAI input-token rollups. |
| `gen_ai.usage.output_tokens` | Provider returns completion usage | OpenTelemetry GenAI output-token rollups. |
| `prov.agent.id` | Agent identity is known | Distinguishes orchestrators, workers, hooks, and tools. |
| `prov.agent.type` | Agent role is known | Separates main agents, delegated agents, hooks, and subagents. |
| `prov.project` | Project/app identity is known | Keeps unrelated systems separated in shared telemetry. |
| `prov.task.label` | Task label is known | Gives dashboard/session views human-readable work labels. |
| `prov.cwd` | Runtime working directory is known | Helps connect traces back to the executing checkout. |
| `prov.repository` | Runtime git repository can be detected | Groups traces by source repository. |
| `prov.session.id` | Conversation/task/session identity is known | Filters all spans for one agent run. |
| `prov.parent.session.id` | Delegation crosses an agent boundary | Connects child agent sessions to parent sessions. |
| `tokens.cache_read` | Provider returns prompt-cache usage | Separates cheaper cache-read tokens from regular input tokens. |
| `tokens.cache_write` | Provider returns prompt-cache usage | Separates cache-write tokens from regular input tokens. |
| `cache.hit_rate` | Provider returns prompt-cache usage | Shows prompt-cache effectiveness by agent/model/session. |

## Certification Levels

| Level | Meaning |
|-------|---------|
| Documented | Public docs explain the integration path and caveats. |
| Smoke checked | Offline scaffolding verifies example files, syntax, and proxy configuration. |
| Dogfooded | Used in a real AgentWeave/OpenClaw workflow with useful spans. |
| Certified | A clean checkout can run an automated provider or mocked-provider smoke test and assert emitted span attributes. |

Current developer preview target: at least `Smoke checked` for framework
examples and `Dogfooded` for Claude Code/OpenClaw bridge paths. `Certified`
requires a mocked provider or local fixture proxy that can assert emitted OTLP
spans without real API keys.
