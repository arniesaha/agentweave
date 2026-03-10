"""
AgentWeave dogfood example: Nix → Max delegation trace

Simulates what happens when Arnab asks Nix to scrape LinkedIn
and Nix delegates to Max via A2A.

Run:
    python3 examples/nix_max_delegation.py

You should see a trace in Langfuse like:
    agent.nix [root span]
    └── tool.delegate_to_max [child span]
        ├── prov.entity = "linkedin_jobs"
        ├── prov.agent.id = "max-v1"
        └── prov.wasGeneratedBy = <span_id>
"""

import json
import time
import uuid

from agentweave.config import AgentWeaveConfig
from agentweave.decorators import trace_agent, trace_tool

# --- Configure AgentWeave to export to your Langfuse ---
AgentWeaveConfig.setup(
    agent_id="nix-v1",
    agent_model="claude-sonnet-4-6",
    agent_version="0.1.0",
    otel_endpoint="http://192.168.1.70:30893/api/public/otel",  # Langfuse OTLP endpoint
    service_name="nix",
    captures_input=True,
    captures_output=True,
)


# --- Simulated Max A2A call (replace with real HTTP call to test live) ---
@trace_tool(
    name="delegate_to_max",
    captures_input=True,
    captures_output=True,
)
def delegate_to_max(task: str) -> dict:
    """Delegate a task to Max via A2A and return the result."""
    # In real life this is:
    # POST http://192.168.1.149:8770/tasks
    # {"id": uuid, "params": {"message": {"parts": [{"type": "text", "text": task}]}}}

    # Simulated response (30 LinkedIn jobs scraped)
    time.sleep(0.05)  # simulate network latency
    return {
        "task_id": str(uuid.uuid4()),
        "agent": "max-v1",
        "result": "Scraped 30 LinkedIn jobs, 4 new (26 dupes skipped)",
        "jobs_found": 4,
        "source": "linkedin_cdp",
    }


# --- Nix agent turn ---
@trace_agent(
    name="nix",
    captures_input=True,
    captures_output=True,
)
def handle_linkedin_request(user_message: str) -> str:
    """Nix handles Arnab's LinkedIn scraping request by delegating to Max."""
    print(f"\n[Nix] Got request: {user_message!r}")

    # Nix decides to delegate to Max
    result = delegate_to_max(
        task="Scrape LinkedIn for engineering manager roles in Canada, return new job IDs"
    )

    print(f"[Nix] Max returned: {json.dumps(result, indent=2)}")

    response = (
        f"Max scraped LinkedIn and found {result['jobs_found']} new jobs. "
        f"Full result: {result['result']}"
    )
    print(f"[Nix] Responding: {response!r}\n")
    return response


# --- Run it ---
if __name__ == "__main__":
    print("=" * 60)
    print("AgentWeave Dogfood: Nix → Max Delegation")
    print("=" * 60)

    output = handle_linkedin_request(
        "Can you scrape LinkedIn for new EM roles?"
    )

    print("\n✅ Trace sent to Langfuse!")
    print("   Check: https://langfuse.arnabsaha.com")
    print("\nExpected trace structure:")
    print("  agent.nix")
    print("  └── tool.delegate_to_max")
    print("      ├── prov.agent.id = max-v1")
    print("      ├── prov.entity.type = api_response")
    print("      └── prov.wasAssociatedWith = max-v1")
