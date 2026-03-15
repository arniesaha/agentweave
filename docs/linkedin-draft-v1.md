# LinkedIn Post Draft — v1
*Status: Draft — do not publish yet (pending #56, #51, #52)*

---

I've been building multi-agent AI systems for a while, and one thing kept bothering me: **you have no idea what's happening inside them.**

Which agent made that decision? Which tool call spiked your token budget? Why did the pipeline return the wrong answer?

So I built **AgentWeave** — an observability layer for multi-agent AI systems.

It gives you:
🔍 **Distributed tracing** across agents, tools, and LLM calls — with full W3C trace propagation so you can follow a request across spawned sub-agents
📊 **Real metrics** — token usage, latency, cost, per model and per tool
🔌 **Zero-code proxy mode** — point your existing app at the AgentWeave proxy, get traces without touching your code
🛠️ **SDK decorators** for deeper instrumentation — `@trace_tool`, `@trace_agent`, `@trace_llm`

Ships with Python, TypeScript, and Go SDKs. Works with LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, and anything else that talks to an LLM API.

I've been dogfooding it on my own multi-agent setup — every LLM call my agents make flows through AgentWeave and lands in Grafana Tempo. Debugging went from "add print statements and hope" to actually drilling into traces.

Open source, early days. Would love feedback from anyone building agentic systems.

👉 https://github.com/arniesaha/agentweave

---

*Tags to add: #AI #AgentAI #Observability #OpenSource #LLM #MultiAgent #LangGraph #CrewAI*
