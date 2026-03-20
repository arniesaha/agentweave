# Example 04: Nix → Max → Sub-agent Delegation

Mirrors the real Nix/Max multi-agent architecture:

```
nix-v1 (orchestrator)
  └── max-v1 (coordinator)
        ├── nix-subagent-v1 (analyst)
        └── nix-subagent-v1 (writer)
```

Each agent registers with its real `AGENTWEAVE_AGENT_ID` so the Session Explorer
shows the actual production agent topology, not just example nodes.

Run:
```bash
ANTHROPIC_BASE_URL=http://192.168.1.70:30400/v1 python examples/04-nix-max-delegation/main.py
```
