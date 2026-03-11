"""Example: A simple 3-tool agent instrumented with AgentWeave.

Run:
    python examples/simple_agent.py

This example demonstrates:
- Configuring AgentWeave with an agent identity
- Using @trace_tool to instrument tool calls
- Using @trace_agent to instrument an agent turn
- Nested spans (tool calls inside an agent turn)

Note: Without an OTLP backend running, spans will be exported but silently
dropped. Add ``agentweave.add_console_exporter()`` to see spans in stdout.
"""

from __future__ import annotations

import json
import subprocess
import time

import agentweave
from agentweave import AgentWeaveConfig, trace_agent, trace_tool

# --- Configure AgentWeave ---
AgentWeaveConfig.setup(
    agent_id="nix-v1",
    agent_model="claude-sonnet-4-6",
    agent_version="0.1.0",
    otel_endpoint="http://localhost:4318",
    service_name="simple-agent-example",
)

# Enable console output so you can see spans without a backend
agentweave.add_console_exporter()


# --- Tool definitions ---


@trace_tool(name="web_search", captures_input=True, captures_output=True)
def web_search(query: str) -> str:
    """Simulate a web search."""
    time.sleep(0.1)  # simulate latency
    return json.dumps(
        {
            "results": [
                {"title": f"Result 1 for '{query}'", "url": "https://example.com/1"},
                {"title": f"Result 2 for '{query}'", "url": "https://example.com/2"},
            ]
        }
    )


@trace_tool(name="exec", captures_input=True, captures_output=True)
def exec_command(command: str) -> str:
    """Execute a shell command and return stdout."""
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=10
    )
    return result.stdout or result.stderr


@trace_tool(name="summarize", captures_input=True, captures_output=True)
def summarize(text: str) -> str:
    """Simulate an LLM summarization call."""
    time.sleep(0.05)
    word_count = len(text.split())
    return f"Summary ({word_count} words processed): Key findings extracted."


# --- Agent turn ---


@trace_agent(name="briefing_agent", captures_input=True, captures_output=True)
def run_briefing(topic: str) -> str:
    """Run a morning briefing pipeline: search, execute, summarize."""
    # Step 1: Search for the topic
    search_results = web_search(topic)

    # Step 2: Get system info
    system_info = exec_command("uname -a")

    # Step 3: Summarize everything
    combined = f"Search: {search_results}\nSystem: {system_info}"
    summary = summarize(combined)

    return f"Briefing complete for '{topic}': {summary}"


# --- Main ---

if __name__ == "__main__":
    print("=" * 60)
    print("AgentWeave Simple Agent Example")
    print("=" * 60)
    print()

    result = run_briefing("AI agent observability")
    print()
    print(f"Result: {result}")

    # Flush spans
    agentweave.shutdown()
    print()
    print("Done. Spans have been exported.")
