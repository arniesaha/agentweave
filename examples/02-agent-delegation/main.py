"""Example 02: Multi-Agent Delegation Loop

Demonstrates a parent orchestrator agent that delegates to two sub-agents
(analyst and writer). Each sub-agent makes its own LLM call. The session
graph in AgentWeave shows the parent → sub-agent hierarchy.

Architecture:
  orchestrator_agent
    ├── analyst_agent    (LLM: research summary)
    └── writer_agent     (LLM: draft report)

Run:
    ANTHROPIC_BASE_URL=http://192.168.1.70:30400/v1 \\
    ANTHROPIC_API_KEY=dummy \\
    python examples/02-agent-delegation/main.py
"""

from __future__ import annotations

import os
import sys
import uuid

# Ensure the local SDK is importable when not installed via pip
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sdk/python"))

import anthropic
import agentweave
from agentweave import AgentWeaveConfig, trace_agent

# ── Config ─────────────────────────────────────────────────────────────────

PROXY_URL = os.environ.get("ANTHROPIC_BASE_URL", "http://192.168.1.70:30400/v1")
OTLP_URL  = os.environ.get("AGENTWEAVE_OTLP_ENDPOINT", "http://192.168.1.70:30418")
MODEL     = "claude-3-haiku-20240307"

AgentWeaveConfig.setup(
    agent_id="example-delegation",
    agent_model=MODEL,
    agent_version="1.0.0",
    otel_endpoint=OTLP_URL,
    service_name="agentweave-examples",
)

client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY", "dummy"),
    base_url=PROXY_URL,
)


# ── Sub-agents ───────────────────────────────────────────────────────────────

@trace_agent(
    name="analyst",
    agent_type="subagent",
    turn_depth=2,
    captures_input=True,
    captures_output=True,
)
def analyst_agent(topic: str, parent_session_id: str) -> str:
    """Research sub-agent: produces a short analysis on the topic."""
    # Tag this span with its parent session so the dashboard can draw the edge
    os.environ["AGENTWEAVE_PARENT_SESSION_ID"] = parent_session_id
    os.environ["AGENTWEAVE_TASK_LABEL"] = "analyst-sub-agent"

    print(f"  [analyst] researching: {topic}")
    response = client.messages.create(
        model=MODEL,
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": (
                    f"You are a research analyst. In 3 bullet points, summarize "
                    f"the key trends in: {topic}"
                ),
            }
        ],
    )
    result = response.content[0].text
    print(f"  [analyst] done — {response.usage.input_tokens}→{response.usage.output_tokens} tokens")
    return result


@trace_agent(
    name="writer",
    agent_type="subagent",
    turn_depth=2,
    captures_input=True,
    captures_output=True,
)
def writer_agent(analysis: str, parent_session_id: str) -> str:
    """Writing sub-agent: turns research bullets into a short paragraph."""
    os.environ["AGENTWEAVE_PARENT_SESSION_ID"] = parent_session_id
    os.environ["AGENTWEAVE_TASK_LABEL"] = "writer-sub-agent"

    print(f"  [writer] drafting from {len(analysis)} char analysis…")
    response = client.messages.create(
        model=MODEL,
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Turn these research bullets into a 2-sentence executive summary:\n\n{analysis}"
                ),
            }
        ],
    )
    result = response.content[0].text
    print(f"  [writer] done — {response.usage.input_tokens}→{response.usage.output_tokens} tokens")
    return result


# ── Parent orchestrator ───────────────────────────────────────────────────────

@trace_agent(
    name="orchestrator",
    agent_type="main",
    turn_depth=1,
    captures_input=True,
    captures_output=True,
)
def orchestrator_agent(topic: str, session_id: str) -> str:
    """Parent agent: delegates research and writing to sub-agents."""
    print(f"\n[orchestrator] starting session {session_id}")
    print(f"[orchestrator] topic: {topic}")

    # Delegate to analyst sub-agent
    analysis = analyst_agent(topic, parent_session_id=session_id)

    # Delegate to writer sub-agent with the analyst's output
    report = writer_agent(analysis, parent_session_id=session_id)

    print(f"\n[orchestrator] final report:\n{report}")
    return report


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Unique session ID for this run
    session_id = f"example-delegation-{uuid.uuid4().hex[:8]}"
    topic = "open-source AI agent observability tools in 2025"

    # Set env vars for proxy span tagging
    os.environ["AGENTWEAVE_SESSION_ID"] = session_id
    os.environ["AGENTWEAVE_TASK_LABEL"] = "orchestrator-main"

    orchestrator_agent(topic, session_id=session_id)

    agentweave.shutdown()
    print(f"\nView trace: https://o11y.arnabsaha.com/explore (session: {session_id})")
    print("Done. Delegation hierarchy visible in AgentWeave session graph.")
