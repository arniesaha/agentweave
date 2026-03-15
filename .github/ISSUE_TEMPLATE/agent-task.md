---
name: Agent Task
about: A task that can be executed autonomously by a sub-agent
title: ''
labels: agent-task
assignees: ''
---

## Objective

<!-- What needs to be done, in one sentence -->

## Acceptance Criteria

<!-- Concrete, testable criteria — the agent's definition of done -->

- [ ] ...
- [ ] ...

## Affected Components

<!-- Which parts of the codebase are involved -->

- [ ] Proxy (`sdk/python/agentweave/proxy.py`)
- [ ] Python SDK (`sdk/python/`)
- [ ] TypeScript SDK (`sdk/js/`)
- [ ] Go SDK (`sdk/go/`)
- [ ] K8s manifests (`deploy/k8s/`)
- [ ] Grafana dashboard (`deploy/grafana/`)
- [ ] CI/CD (`.github/workflows/`)
- [ ] Other: ...

## Verification Steps

<!-- How to confirm the fix works beyond unit tests -->

1. `scripts/deploy.sh` exits 0
2. `scripts/verify.sh` exits 0
3. <!-- Any additional manual or automated checks -->

## Context

<!-- Background info, links, or relevant traces/logs -->
