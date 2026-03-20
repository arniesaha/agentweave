"""Example 04: Nix → Max → Sub-agent Delegation

Mirrors real-world architecture:
  nix (orchestrator, claude-sonnet-4-6)
    └── max (coordinator, gemini-2.0-flash)
          ├── analyst_agent  (sub-agent, claude-3-haiku)
          └── writer_agent   (sub-agent, claude-3-haiku)

All LLM calls route through the AgentWeave proxy so every span is traced.
Session graph will show the full delegation tree with back-edges.

Run:
    ANTHROPIC_BASE_URL=http://192.168.1.70:30400/v1 \\
    python examples/04-nix-max-delegation/main.py
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sdk/python"))

import anthropic
import agentweave
from agentweave import AgentWeaveConfig, trace_agent

# ── Config ──────────────────────────────────────────────────────────────────

_raw_proxy = os.environ.get("ANTHROPIC_BASE_URL", "http://192.168.1.70:30400/v1")
PROXY_URL  = _raw_proxy.rstrip("/").removesuffix("/v1")
OTLP_URL   = os.environ.get("AGENTWEAVE_OTLP_ENDPOINT", "http://192.168.1.70:30418")

# All agents use Haiku via proxy (simulating different agents talking to the same backend)
HAIKU = "claude-3-haiku-20240307"

# ── Session IDs ─────────────────────────────────────────────────────────────

run_id        = uuid.uuid4().hex[:8]
nix_session   = f"nix-main-{run_id}"
max_session   = f"max-coordinator-{run_id}"
analyst_session = f"nix-sub-analyst-{run_id}"
writer_session  = f"nix-sub-writer-{run_id}"

# ── AgentWeave init (Nix = top-level orchestrator) ──────────────────────────

AgentWeaveConfig.setup(
    agent_id="nix-v1",
    agent_model=HAIKU,
    agent_version="1.0.0",
    otel_endpoint=OTLP_URL,
    service_name="agentweave-proxy",
)

os.environ["AGENTWEAVE_SESSION_ID"] = nix_session
os.environ["AGENTWEAVE_TASK_LABEL"] = "nix-orchestrate"

client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY", "dummy"),
    base_url=PROXY_URL,
)


# ── Sub-agents ───────────────────────────────────────────────────────────────

@trace_agent(name="analyst", agent_type="subagent", captures_input=True, captures_output=True)
def analyst_agent(topic: str, parent_session_id: str) -> str:
    os.environ["AGENTWEAVE_SESSION_ID"] = analyst_session
    os.environ["AGENTWEAVE_PARENT_SESSION_ID"] = parent_session_id
    os.environ["AGENTWEAVE_TASK_LABEL"] = "analyst-sub-agent"
    os.environ["AGENTWEAVE_AGENT_ID"] = "nix-subagent-v1"

    print(f"    [analyst] researching: {topic}")
    r = client.messages.create(
        model=HAIKU, max_tokens=200,
        messages=[{"role": "user", "content": f"In 3 bullet points, summarize key trends in: {topic}"}],
    )
    result = r.content[0].text
    print(f"    [analyst] done — {r.usage.input_tokens}→{r.usage.output_tokens} tokens")
    return result


@trace_agent(name="writer", agent_type="subagent", captures_input=True, captures_output=True)
def writer_agent(analysis: str, parent_session_id: str) -> str:
    os.environ["AGENTWEAVE_SESSION_ID"] = writer_session
    os.environ["AGENTWEAVE_PARENT_SESSION_ID"] = parent_session_id
    os.environ["AGENTWEAVE_TASK_LABEL"] = "writer-sub-agent"
    os.environ["AGENTWEAVE_AGENT_ID"] = "nix-subagent-v1"

    print(f"    [writer] drafting from {len(analysis)} char analysis…")
    r = client.messages.create(
        model=HAIKU, max_tokens=200,
        messages=[{"role": "user", "content": f"Write a 2-sentence exec summary from: {analysis}"}],
    )
    result = r.content[0].text
    print(f"    [writer] done — {r.usage.input_tokens}→{r.usage.output_tokens} tokens")
    return result


# ── Max coordinator (simulated) ───────────────────────────────────────────────

@trace_agent(name="max-coordinator", agent_type="subagent", captures_input=True, captures_output=True)
def max_coordinator(topic: str, parent_session_id: str) -> str:
    """Simulates Max receiving a task from Nix and delegating to sub-agents."""
    os.environ["AGENTWEAVE_SESSION_ID"] = max_session
    os.environ["AGENTWEAVE_PARENT_SESSION_ID"] = parent_session_id
    os.environ["AGENTWEAVE_TASK_LABEL"] = "max-coordinate"
    os.environ["AGENTWEAVE_AGENT_ID"] = "max-v1"

    print(f"  [max] received task: {topic}")

    # Max delegates to analyst
    analysis = analyst_agent(topic, parent_session_id=max_session)

    # Max delegates to writer
    summary = writer_agent(analysis, parent_session_id=max_session)

    print(f"  [max] coordination complete")
    return summary


# ── Nix orchestrator ──────────────────────────────────────────────────────────

@trace_agent(name="nix-orchestrator", captures_input=True, captures_output=True)
def nix_orchestrator(topic: str) -> str:
    os.environ["AGENTWEAVE_SESSION_ID"] = nix_session
    os.environ["AGENTWEAVE_TASK_LABEL"] = "nix-orchestrate"
    os.environ["AGENTWEAVE_AGENT_ID"] = "nix-v1"

    print(f"[nix] orchestrating: {topic}")

    # Nix makes its own LLM call first (triage/planning)
    r = client.messages.create(
        model=HAIKU, max_tokens=60,
        messages=[{"role": "user", "content": f"In one sentence, why is this topic important: {topic}"}],
    )
    context = r.content[0].text
    print(f"[nix] context: {context[:80]}…")

    # Nix delegates to Max
    result = max_coordinator(topic, parent_session_id=nix_session)

    # Nix synthesizes final response
    final = client.messages.create(
        model=HAIKU, max_tokens=100,
        messages=[{"role": "user", "content": f"Given this summary: {result}\nWhat's the one key action to take?"}],
    )
    recommendation = final.content[0].text
    print(f"[nix] recommendation: {recommendation[:100]}…")
    return recommendation


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    topic = "AI agent observability and tracing in production systems"
    print(f"\n{'='*60}")
    print(f"Nix → Max → Sub-agents delegation demo")
    print(f"Session: {nix_session}")
    print(f"{'='*60}\n")

    result = nix_orchestrator(topic)

    print(f"\n{'='*60}")
    print(f"Final result: {result[:200]}")
    print(f"\nView in AgentWeave Session Explorer:")
    print(f"  Nix session:     {nix_session}")
    print(f"  Max session:     {max_session}")
    print(f"  Analyst session: {analyst_session}")
    print(f"  Writer session:  {writer_session}")
    print(f"{'='*60}\n")
