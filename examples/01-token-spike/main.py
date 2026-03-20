"""Example 01: Runaway Token Triage

Demonstrates a multi-step pipeline where a "summarize" tool returns a massive
blob of text, causing a token spike. AgentWeave traces show exactly which tool
is the offender. A second "fixed" run truncates the output.

Pipeline:
  search_tool → summarize_tool (offending, returns ~5000 words) → LLM → report_tool

Run:
    ANTHROPIC_BASE_URL=http://192.168.1.70:30400/v1 \\
    ANTHROPIC_API_KEY=dummy \\
    python examples/01-token-spike/main.py
"""

from __future__ import annotations

import os
import sys
import time
import uuid

# Ensure the local SDK is importable when not installed via pip
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sdk/python"))

import anthropic
import agentweave
from agentweave import AgentWeaveConfig, auto_instrument, trace_tool

# ── Config ─────────────────────────────────────────────────────────────────

# Strip trailing /v1 if present — Anthropic SDK appends its own /v1 path prefix
_raw_proxy = os.environ.get("ANTHROPIC_BASE_URL", "http://192.168.1.70:30400/v1")
PROXY_URL = _raw_proxy.rstrip("/").removesuffix("/v1")
OTLP_URL  = os.environ.get("AGENTWEAVE_OTLP_ENDPOINT", "http://192.168.1.70:30418")
MODEL     = "claude-3-haiku-20240307"

AgentWeaveConfig.setup(
    agent_id="example-token-spike",
    agent_model=MODEL,
    agent_version="1.0.0",
    otel_endpoint=OTLP_URL,
    service_name="agentweave-examples",
)

# Patch the Anthropic client so every LLM call gets a span automatically
auto_instrument()

client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY", "dummy"),
    base_url=PROXY_URL,
)

# ── Simulated "massive document" ────────────────────────────────────────────

_FILLER = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
) * 200  # ~5000 words

# ── Tools ───────────────────────────────────────────────────────────────────

@trace_tool(name="search", captures_input=True, captures_output=True)
def search_tool(query: str) -> str:
    """Simulate a web search — returns a short summary."""
    time.sleep(0.05)
    return f"Found 12 results for '{query}'. Top hit: 'AI Observability Best Practices (2025)'."


@trace_tool(name="summarize_broken", captures_input=True, captures_output=True)
def summarize_tool_broken(url: str) -> str:
    """Broken version: returns full document text (token spike!)."""
    time.sleep(0.1)
    return f"Document content for {url}:\n\n{_FILLER}"


@trace_tool(name="summarize_fixed", captures_input=True, captures_output=True)
def summarize_tool_fixed(url: str, max_chars: int = 800) -> str:
    """Fixed version: truncates output before returning."""
    time.sleep(0.1)
    full_content = f"Document content for {url}:\n\n{_FILLER}"
    return full_content[:max_chars] + "... [truncated]"


@trace_tool(name="report", captures_input=True, captures_output=True)
def report_tool(summary: str) -> str:
    """Format the final report."""
    return f"REPORT ({len(summary.split())} words): {summary[:200]}..."


# ── Pipeline ─────────────────────────────────────────────────────────────────

def run_pipeline(session_id: str, use_fixed: bool) -> None:
    """Run the full pipeline, tracing each step."""
    label = "fixed" if use_fixed else "broken"
    print(f"\n{'='*60}")
    print(f"Run: {label.upper()}  |  session: {session_id}")
    print(f"{'='*60}")

    # Set session env vars so proxy and SDK tag spans correctly
    os.environ["AGENTWEAVE_SESSION_ID"] = session_id
    os.environ["AGENTWEAVE_TASK_LABEL"] = f"token-spike-{label}"

    # Step 1: search
    search_result = search_tool("AI observability tools 2025")
    print(f"[search] {search_result[:80]}")

    # Step 2: summarize (offending step)
    top_url = "https://example.com/ai-observability-2025"
    if use_fixed:
        doc_text = summarize_tool_fixed(top_url)
    else:
        doc_text = summarize_tool_broken(top_url)
    print(f"[summarize] returned {len(doc_text):,} chars  ({'FIXED ✓' if use_fixed else 'BROKEN — token spike!'})")

    # Step 3: LLM call (auto-instrumented, token counts captured by proxy)
    print("[llm] Summarizing with Claude Haiku…")
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": (
                    "Summarize this in 2 sentences:\n\n"
                    + doc_text[:3000]  # hard cap to avoid real rate limits
                ),
            }
        ],
    )
    llm_summary = response.content[0].text
    in_tok  = response.usage.input_tokens
    out_tok = response.usage.output_tokens
    print(f"[llm] tokens in={in_tok}  out={out_tok}  ({'HIGH ⚠️' if in_tok > 500 else 'normal ✓'})")

    # Step 4: report
    final_report = report_tool(llm_summary)
    print(f"[report] {final_report[:120]}")

    print(f"\nView trace: https://o11y.arnabsaha.com/explore (session: {session_id})")


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Run 1: broken — tool returns a 5000-word blob
    run_pipeline(
        session_id=f"example-token-spike-broken-{uuid.uuid4().hex[:6]}",
        use_fixed=False,
    )

    time.sleep(1)  # small gap between runs

    # Run 2: fixed — tool truncates output to 800 chars
    run_pipeline(
        session_id=f"example-token-spike-fixed-{uuid.uuid4().hex[:6]}",
        use_fixed=True,
    )

    agentweave.shutdown()
    print("\nDone. Both traces sent to AgentWeave.")
