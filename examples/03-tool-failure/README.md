# Example 03: Silent Tool Failure Root Cause

## What this shows

The hardest bugs to debug: a tool that **doesn't crash** but **returns wrong
data**. The LLM uses the bad data and produces a wrong answer. Without
captured inputs/outputs, you have no idea where it went wrong.

This example shows how `@trace_tool(captures_output=True)` stores the raw
tool output as a span attribute, making bad data immediately visible in the
trace — no extra logging required.

### The scenario

```
get_weather("Vancouver")
    ↓
{ "temp_f": 58 }          ← buggy: wrong key name
    ↓
LLM: "What is temperature_f?"
    ↓
LLM doesn't find "temperature_f" → wrong answer
```

vs.

```
get_weather("Vancouver")
    ↓
{ "temperature_f": 58 }   ← fixed: correct key name
    ↓
LLM: "What is temperature_f?"
    ↓
LLM answers: "58" ✓
```

## How to run

```bash
export ANTHROPIC_BASE_URL=http://192.168.1.70:30400/v1
export ANTHROPIC_API_KEY=dummy   # proxy injects the real key (AGENTWEAVE_ANTHROPIC_API_KEY)

cd /path/to/agentweave
python examples/03-tool-failure/main.py
```

The script runs both versions back-to-back, printing whether the LLM's answer
was correct.

## What to look for in the dashboard

1. **`prov.entity.output.value`** on the `tool.get_weather_buggy` span —
   this contains the raw JSON string `{"temp_f": 58, ...}`. Notice the wrong
   field name immediately.

2. **`prov.used`** on the tool spans — shows the input (`"Vancouver"`), so
   you can confirm the right query was passed.

3. **LLM response vs. expected** — compare the LLM output across both runs.
   In the buggy run, the LLM will either hallucinate a temperature or report
   that it can't find the field.

4. **No code changes needed to debug** — `captures_output=True` is the only
   addition. All you need is to look at the span in Grafana Tempo.

## Key insight

Traditional logging requires you to know in advance what to log. AgentWeave
`captures_output=True` records the raw output of every tool call as a span
attribute — so when something goes wrong, the evidence is already there.

## Prerequisites

- AgentWeave proxy running at `http://192.168.1.70:30400`
- Grafana/Tempo at `https://o11y.arnabsaha.com`
- Python packages: `anthropic`, `agentweave` (from `sdk/python/`)
