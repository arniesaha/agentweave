"""Example 03: Silent Tool Failure Root Cause

Demonstrates how a tool that returns subtly wrong data causes the LLM to
produce a wrong answer — and how AgentWeave's span attributes (captures_input,
captures_output) reveal the bad data without any extra logging.

Scenario:
  An agent fetches weather data and asks the LLM to report the temperature.
  The buggy tool returns a JSON with the wrong field name ("temp_f" instead of
  "temperature_f"). The LLM sees null/missing data and hallucinates an answer.
  The fixed tool uses the correct field name.

Run:
    ANTHROPIC_BASE_URL=http://192.168.1.70:30400/v1 \\
    ANTHROPIC_API_KEY=dummy \\
    python examples/03-tool-failure/main.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

# Ensure the local SDK is importable when not installed via pip
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sdk/python"))

import anthropic
import agentweave
from agentweave import AgentWeaveConfig, trace_tool

# ── Config ─────────────────────────────────────────────────────────────────

PROXY_URL = os.environ.get("ANTHROPIC_BASE_URL", "http://192.168.1.70:30400/v1")
OTLP_URL  = os.environ.get("AGENTWEAVE_OTLP_ENDPOINT", "http://192.168.1.70:30418")
MODEL     = "claude-3-haiku-20240307"

AgentWeaveConfig.setup(
    agent_id="example-tool-failure",
    agent_model=MODEL,
    agent_version="1.0.0",
    otel_endpoint=OTLP_URL,
    service_name="agentweave-examples",
)

client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY", "dummy"),
    base_url=PROXY_URL,
)

# ── Simulated weather data ───────────────────────────────────────────────────

_REAL_WEATHER = {
    "city": "Vancouver",
    "temperature_f": 58,  # ← correct field name
    "conditions": "Partly cloudy",
    "humidity_pct": 72,
}


# ── Buggy tool — wrong field name ────────────────────────────────────────────

@trace_tool(
    name="get_weather_buggy",
    captures_input=True,   # input stored as prov.used
    captures_output=True,  # output stored as prov.entity.output.value
)
def get_weather_buggy(city: str) -> str:
    """Buggy version: returns 'temp_f' instead of 'temperature_f'.

    The LLM prompt asks for 'temperature_f', so it gets None/missing and
    may hallucinate a value or report an error.
    """
    time.sleep(0.05)
    buggy_data = {
        "city": _REAL_WEATHER["city"],
        "temp_f": _REAL_WEATHER["temperature_f"],   # ← wrong key name!
        "conditions": _REAL_WEATHER["conditions"],
        "humidity_pct": _REAL_WEATHER["humidity_pct"],
    }
    return json.dumps(buggy_data)


# ── Fixed tool — correct field name ──────────────────────────────────────────

@trace_tool(
    name="get_weather_fixed",
    captures_input=True,
    captures_output=True,
)
def get_weather_fixed(city: str) -> str:
    """Fixed version: returns the correct field name 'temperature_f'."""
    time.sleep(0.05)
    return json.dumps(_REAL_WEATHER)


# ── Agent pipeline ────────────────────────────────────────────────────────────

def run_weather_agent(session_id: str, use_fixed: bool) -> None:
    label = "fixed" if use_fixed else "buggy"
    print(f"\n{'='*60}")
    print(f"Run: {label.upper()}  |  session: {session_id}")
    print(f"{'='*60}")

    os.environ["AGENTWEAVE_SESSION_ID"] = session_id
    os.environ["AGENTWEAVE_TASK_LABEL"] = f"weather-agent-{label}"

    city = "Vancouver"

    # Step 1: fetch weather
    if use_fixed:
        raw_data = get_weather_fixed(city)
    else:
        raw_data = get_weather_buggy(city)

    parsed = json.loads(raw_data)
    print(f"[tool] returned: {raw_data}")
    print(f"[tool] temperature_f present: {'temperature_f' in parsed}")

    # Step 2: ask LLM to extract the temperature
    print("[llm] asking Claude to report the temperature…")
    response = client.messages.create(
        model=MODEL,
        max_tokens=100,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Here is the weather data as JSON:\n{raw_data}\n\n"
                    "What is the current temperature in Fahrenheit? "
                    "Report ONLY the numeric value from the 'temperature_f' field."
                ),
            }
        ],
    )

    llm_answer = response.content[0].text.strip()
    expected   = str(_REAL_WEATHER["temperature_f"])
    correct    = expected in llm_answer

    print(f"[llm] answer: {llm_answer}")
    print(f"[llm] expected: {expected}°F  →  {'✓ CORRECT' if correct else '✗ WRONG (hallucination!)'}")
    print(f"\nView trace: https://o11y.arnabsaha.com/explore (session: {session_id})")
    print(
        "  → In Tempo: look at 'prov.entity.output.value' on the tool span "
        "to see the raw JSON with the wrong/right field name."
    )


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Run 1: buggy tool — wrong field name causes LLM to produce wrong answer
    run_weather_agent(
        session_id=f"example-tool-failure-buggy-{uuid.uuid4().hex[:6]}",
        use_fixed=False,
    )

    time.sleep(1)

    # Run 2: fixed tool — correct field name, LLM gives right answer
    run_weather_agent(
        session_id=f"example-tool-failure-fixed-{uuid.uuid4().hex[:6]}",
        use_fixed=True,
    )

    agentweave.shutdown()
    print("\nDone. Compare tool outputs in AgentWeave span attributes.")
