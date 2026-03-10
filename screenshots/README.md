# Screenshots

Screenshots showing AgentWeave traces in Grafana Tempo.

## Trace waterfall — agent + llm + tool spans

`trace-waterfall.png` — Full `agent.nix → llm.claude-sonnet-4-6 × 3 → tool.image_search × 3` chain from the Lisbon photo hunt demo (Mar 9, 2026). Shows 7 spans under a single trace ID, token counts and latency on each LLM span.

<!-- Add: screenshots/trace-waterfall.png -->

## Live dogfooding — real Nix conversation trace

`live-dogfood.png` — Real `llm.claude-sonnet-4-6` span from an actual Nix conversation via the proxy. Shows `prov.llm.prompt_tokens`, `prov.llm.completion_tokens`, `prov.llm.total_tokens`, `prov.llm.stop_reason`, and `agentweave.latency_ms` in the Grafana span detail panel.

<!-- Add: screenshots/live-dogfood.png -->

---

To add screenshots: drop PNG files in this folder and update the README links above.
