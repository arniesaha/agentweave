# Langfuse Eval Mapping

Issue: <https://github.com/arniesaha/agentweave/issues/221>

## Boundary

AgentWeave remains backend-neutral. The canonical span contract is still
OpenTelemetry plus `prov.*` and `gen_ai.*` attributes. Langfuse receives a
second, explicit `langfuse.*` mapping so its UI and APIs can filter traces,
group sessions, and attach eval scores without becoming the source of truth.

Tempo remains the raw trace store. Langfuse is the eval and review surface.

## Attribute Mapping

| Signal | AgentWeave attribute | Langfuse attribute | Level |
| --- | --- | --- | --- |
| Trace name | `prov.task.label` or `session.id` | `langfuse.trace.name` | Trace |
| Session | `session.id`, `prov.session.id` | `langfuse.session.id` | Trace |
| Project | `prov.project` | `langfuse.trace.metadata.project` | Trace metadata |
| Agent ID | `prov.agent.id` | `langfuse.trace.metadata.agent_id` | Trace metadata |
| Agent type | `prov.agent.type` | `langfuse.trace.metadata.agent_type` | Trace metadata |
| Parent session | `prov.parent.session.id` | `langfuse.trace.metadata.parent_session_id` | Trace metadata |
| Repository | `prov.repository` | `langfuse.trace.metadata.repository` | Trace metadata |
| Task label | `prov.task.label` | `langfuse.trace.metadata.task_label` | Trace metadata |
| Activity type | `prov.activity.type` | `langfuse.trace.metadata.activity_type` | Trace metadata |
| LLM observation | `prov.activity.type=llm_call` | `langfuse.observation.type=generation` | Observation |
| Agent observation | `prov.activity.type=agent_turn` | `langfuse.observation.type=agent` | Observation |
| Tool observation | `prov.activity.type=tool_call` | `langfuse.observation.type=tool` | Observation |
| Model | `prov.llm.model`, `gen_ai.request.model` | `langfuse.observation.model.name` | Observation |
| Token usage | `gen_ai.usage.*` | `langfuse.observation.usage_details` | Observation |
| Cost | `cost.usd` | `langfuse.observation.cost_details` | Observation |

Prompt and response previews are exported to `langfuse.observation.input` and
`langfuse.observation.output` only when prompt capture is explicitly enabled.
PII scanning/redaction still runs before captured previews are stored.

## Score Mapping

Scores should be written to Langfuse via the Scores API or SDK after a trace is
available. They should not be emitted as plain span attributes because Langfuse
score records are first-class eval data.

| Score | Scope | Type | Source |
| --- | --- | --- | --- |
| `handoff_relevance` | Trace | Numeric 0-1 | LLM-as-judge over handoff context vs task |
| `handoff_completeness` | Trace | Numeric 0-1 | Required constraints preserved |
| `handoff_freshness` | Trace | Numeric 0-1 | Context age and stale-reference checks |
| `attribution_quality` | Trace | Numeric 0-1 | Parent/child session and agent labels present |
| `continuity_score` | Trace | Numeric 0-1 | Downstream agent used the provided context correctly |
| `replay_coverage` | Trace | Numeric 0-1 | Tempo/Nexus replay can reconstruct the task path |
| `decision_grounding` | Observation or trace | Numeric 0-1 | Claims tied to tool outputs or cited context |
| `task_success` | Trace | Boolean or numeric | Completed vs blocked/user-corrected |
| `cost_to_success` | Trace | Numeric | Total cost divided by success outcome |
| `latency_to_first_useful_action` | Trace | Numeric seconds | First non-trivial action after handoff |
| `context_efficiency` | Trace | Numeric 0-1 | Useful context per token |
| `user_intervention_count` | Trace | Numeric | Count of corrective user messages |
| `regression_score` | Trace | Numeric 0-1 | Comparison to previous known-good run |

## Privacy Boundary

Default export is metadata, timings, token counts, and cost. Raw prompt/tool
payloads are not exported unless capture flags enable them. Captured previews
are capped and pass through the existing PII mode (`off`, `flag`, `redact`, or
`block`). Secrets and API keys must never be written into score comments or
trace metadata.

## Current Implementation

The Python SDK and proxy emit Langfuse-native trace/session metadata and
observation types while preserving the existing `prov.*` and `gen_ai.*`
attributes. The live collector fanout sends the same OTLP stream to Tempo and
Langfuse v3.

Next implementation slice: add a small score publisher that takes a trace ID
and posts one or more Langfuse scores for context-handoff review.
