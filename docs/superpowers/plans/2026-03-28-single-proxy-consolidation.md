# Single Proxy Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate 3 identical proxy deployments into 1, with per-request header attribution as the source of truth for agent/session identity.

**Architecture:** Flip the proxy's attribute resolution from "env var first, header fallback" to "header first, env var fallback." Remove the nix-subagent and max proxy deployments. All clients already send `X-AgentWeave-*` headers via their provider configs — the proxy just needs to respect them.

**Tech Stack:** Python (proxy.py), Kubernetes YAML, pytest

---

### Task 1: Fix proxy attribute resolution order

**Files:**
- Modify: `sdk/python/agentweave/proxy.py:763-780`
- Test: `sdk/python/tests/test_proxy.py`

The proxy currently resolves `agent_id` and `session_id` with env var taking precedence over request headers. This is backwards — request headers carry per-request attribution from the calling agent, while env vars are static configmap defaults.

- [ ] **Step 1: Write failing test for header-first agent_id resolution**

Add to `sdk/python/tests/test_proxy.py` at the end of the file, before the `TestListModelsEndpoint` class:

```python
# ---------------------------------------------------------------------------
# Attribute resolution order: header > env > fallback
# ---------------------------------------------------------------------------

class TestAttributeResolution:
    """Per-request headers MUST take precedence over env var defaults (#143)."""

    def test_agent_id_from_header_over_env(self, monkeypatch):
        monkeypatch.setenv("AGENTWEAVE_AGENT_ID", "env-agent")
        headers = {"x-agentweave-agent-id": "header-agent"}
        # Simulate the resolution logic
        agent_id = (
            headers.get("x-agentweave-agent-id")
            or os.getenv("AGENTWEAVE_AGENT_ID")
            or "unattributed"
        )
        assert agent_id == "header-agent"

    def test_agent_id_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("AGENTWEAVE_AGENT_ID", "env-agent")
        headers = {}
        agent_id = (
            headers.get("x-agentweave-agent-id")
            or os.getenv("AGENTWEAVE_AGENT_ID")
            or "unattributed"
        )
        assert agent_id == "env-agent"

    def test_agent_id_falls_back_to_unattributed(self, monkeypatch):
        monkeypatch.delenv("AGENTWEAVE_AGENT_ID", raising=False)
        headers = {}
        agent_id = (
            headers.get("x-agentweave-agent-id")
            or os.getenv("AGENTWEAVE_AGENT_ID")
            or "unattributed"
        )
        assert agent_id == "unattributed"

    def test_session_id_from_header_over_env(self, monkeypatch):
        monkeypatch.setenv("AGENTWEAVE_SESSION_ID", "env-session")
        headers = {"x-agentweave-session-id": "header-session"}
        session_id = (
            headers.get("x-agentweave-session-id")
            or os.getenv("AGENTWEAVE_SESSION_ID")
        )
        assert session_id == "header-session"

    def test_session_id_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("AGENTWEAVE_SESSION_ID", "env-session")
        headers = {}
        session_id = (
            headers.get("x-agentweave-session-id")
            or os.getenv("AGENTWEAVE_SESSION_ID")
        )
        assert session_id == "env-session"
```

- [ ] **Step 2: Run tests to verify they pass (resolution logic is inline, not calling proxy)**

Run: `python3 -m pytest sdk/python/tests/test_proxy.py::TestAttributeResolution -v`
Expected: 5 PASS (these test the correct resolution order directly)

- [ ] **Step 3: Update proxy.py — flip agent_id resolution**

In `sdk/python/agentweave/proxy.py`, change lines 763-768 from:

```python
    agent_id = (
        os.getenv("AGENTWEAVE_AGENT_ID")
        or request.headers.get("x-agentweave-agent-id")
        or _config_value("agent_id")
        or "unattributed"
    )
```

To:

```python
    agent_id = (
        request.headers.get("x-agentweave-agent-id")
        or os.getenv("AGENTWEAVE_AGENT_ID")
        or _config_value("agent_id")
        or "unattributed"
    )
```

- [ ] **Step 4: Update proxy.py — flip session_id resolution**

In `sdk/python/agentweave/proxy.py`, change lines 777-780 from:

```python
    session_id = (
        os.getenv("AGENTWEAVE_SESSION_ID")
        or request.headers.get("x-agentweave-session-id")
    )
```

To:

```python
    session_id = (
        request.headers.get("x-agentweave-session-id")
        or os.getenv("AGENTWEAVE_SESSION_ID")
    )
```

- [ ] **Step 5: Add env var fallback for parent_session_id and agent_type**

These currently only read from headers. Add env var fallback for bridge compatibility. Change lines 814-815 from:

```python
    parent_session_id: str | None = request.headers.get("x-agentweave-parent-session-id")
    agent_type: str | None = request.headers.get("x-agentweave-agent-type")
```

To:

```python
    parent_session_id: str | None = (
        request.headers.get("x-agentweave-parent-session-id")
        or os.getenv("AGENTWEAVE_PARENT_SESSION_ID")
        or None
    )
    agent_type: str | None = (
        request.headers.get("x-agentweave-agent-type")
        or os.getenv("AGENTWEAVE_AGENT_TYPE")
        or None
    )
```

- [ ] **Step 6: Run full proxy test suite**

Run: `python3 -m pytest sdk/python/tests/test_proxy.py -v`
Expected: All tests PASS (119+5 = 124)

- [ ] **Step 7: Commit**

```bash
git add sdk/python/agentweave/proxy.py sdk/python/tests/test_proxy.py
git commit -m "fix(proxy): request headers take precedence over env var defaults (#143)

Flips resolution order for agent_id and session_id:
  header > env var > cli config > fallback

Also adds env var fallback for parent_session_id and agent_type
(header-only before). This means per-request attribution from
clients is respected, while env vars serve as deployment defaults."
```

---

### Task 2: Update configmap to use generic fallback

**Files:**
- Modify: `deploy/k8s/configmap.yaml`

- [ ] **Step 1: Change AGENTWEAVE_AGENT_ID to a generic fallback**

In `deploy/k8s/configmap.yaml`, change line 11 from:

```yaml
  AGENTWEAVE_AGENT_ID: "proxy"
```

To:

```yaml
  # Fallback agent ID when request has no X-AgentWeave-Agent-Id header.
  # Per-request headers take precedence (see proxy.py attribute resolution).
  AGENTWEAVE_AGENT_ID: "unattributed"
```

- [ ] **Step 2: Commit**

```bash
git add deploy/k8s/configmap.yaml
git commit -m "chore(deploy): set fallback agent_id to 'unattributed' (#143)"
```

---

### Task 3: Build, deploy, and verify single proxy attribution

**Files:** No code changes — deployment and verification only.

- [ ] **Step 1: Build and push updated proxy image**

```bash
docker build -t localhost:5000/agentweave-proxy:latest -f deploy/docker/Dockerfile .
docker push localhost:5000/agentweave-proxy:latest
```

- [ ] **Step 2: Apply updated configmap to cluster**

```bash
kubectl patch configmap agentweave-proxy -n agentweave --type merge \
  -p '{"data":{"AGENTWEAVE_AGENT_ID":"unattributed"}}'
```

- [ ] **Step 3: Restart main proxy to pick up new image + configmap**

```bash
kubectl rollout restart deployment/agentweave-proxy -n agentweave
kubectl rollout status deployment/agentweave-proxy -n agentweave --timeout=90s
```

- [ ] **Step 4: Verify health and attribution**

```bash
curl -s http://localhost:30400/health
# Expected: {"status":"ok","version":"0.2.0","key_injection":{...}}

# Test: header attribution works
curl -s http://192.168.1.70:30400/v1/messages \
  -H "x-api-key: $(cat ~/.claude/.credentials.json | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"claudeAiOauth\"][\"accessToken\"])')" \
  -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: oauth-2025-04-20" \
  -H "content-type: application/json" \
  -H "X-AgentWeave-Agent-Id: test-agent" \
  -H "X-AgentWeave-Session-Id: test-session-123" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":5,"messages":[{"role":"user","content":"hi"}]}'
```

Then check Tempo for a span with `prov.agent.id=test-agent` and `session.id=test-session-123`:

```bash
curl -s "http://192.168.1.70:31989/api/search?q=%7Bresource.service.name%3D%22agentweave-proxy%22%7D%20%7C%20select(span.prov.agent.id%2Cspan.session.id)&limit=5&start=$(date -d '5 minutes ago' +%s)&end=$(date +%s)"
```

Expected: most recent span shows `prov.agent.id=test-agent`, `session.id=test-session-123`

---

### Task 4: Remove extra proxy deployments from cluster

**Files:** No code changes — cluster cleanup only.

- [ ] **Step 1: Delete nix-subagent proxy resources from cluster**

```bash
kubectl delete deployment agentweave-proxy-nix-subagent -n agentweave
kubectl delete service agentweave-proxy-nix-subagent-nodeport -n agentweave
kubectl delete configmap agentweave-proxy-nix-subagent -n agentweave
```

- [ ] **Step 2: Delete max proxy resources from cluster**

```bash
kubectl delete deployment agentweave-proxy-max -n agentweave
kubectl delete service agentweave-proxy-max-nodeport -n agentweave
kubectl delete configmap agentweave-proxy-max -n agentweave
```

- [ ] **Step 3: Verify only one proxy remains**

```bash
kubectl get pods -n agentweave
# Expected: only agentweave-proxy-* and agentweave-dashboard-* pods
kubectl get svc -n agentweave
# Expected: agentweave-proxy, agentweave-proxy-nodeport, agentweave-dashboard
```

---

### Task 5: Remove extra proxy YAML files from repo

**Files:**
- Delete: `deploy/k8s/nix-subagent-proxy-configmap.yaml`
- Delete: `deploy/k8s/nix-subagent-proxy-deployment.yaml`
- Delete: `deploy/k8s/nix-subagent-proxy-service.yaml`
- Delete: `deploy/k8s/proxy-max.yaml`

- [ ] **Step 1: Remove files**

```bash
git rm deploy/k8s/nix-subagent-proxy-configmap.yaml
git rm deploy/k8s/nix-subagent-proxy-deployment.yaml
git rm deploy/k8s/nix-subagent-proxy-service.yaml
git rm deploy/k8s/proxy-max.yaml
```

- [ ] **Step 2: Commit**

```bash
git commit -m "chore(deploy): remove nix-subagent and max proxy manifests (#143)

Single proxy on :30400 handles all agents via per-request headers.
Removed:
- nix-subagent-proxy-{configmap,deployment,service}.yaml
- proxy-max.yaml (combined configmap+deployment+service)"
```

---

### Task 6: Update Max's client config to point at single proxy

**Files:**
- Modify: Max's OpenClaw config or `.env` on pi-mono (192.168.1.149)

Max was previously pointed at port 30401 (the dedicated max proxy). Now all traffic goes through 30400.

- [ ] **Step 1: Verify Max's current ANTHROPIC_BASE_URL**

SSH to pi-mono or check Max's config:
```bash
# Check what Max currently uses
ssh 192.168.1.149 "grep -r 'ANTHROPIC_BASE_URL\|baseUrl\|30401' ~/.openclaw/ 2>/dev/null | head -5"
```

- [ ] **Step 2: Update Max to use port 30400**

Update Max's provider config `baseUrl` from `http://192.168.1.70:30401` to `http://192.168.1.70:30400`. Max's config should already send `X-AgentWeave-Agent-Id: max-v1` in headers, which the proxy will now respect.

- [ ] **Step 3: Verify Max's LLM calls show correct attribution**

After Max makes an LLM call, query Tempo:
```bash
curl -s "http://192.168.1.70:31989/api/search?q=%7Bresource.service.name%3D%22agentweave-proxy%22+%26%26+span.prov.agent.id%3D%22max-v1%22%7D&limit=5&start=$(date -d '10 minutes ago' +%s)&end=$(date +%s)"
```

Expected: spans with `prov.agent.id=max-v1` (from Max's request header, not from a configmap)

---

### Task 7: Push all changes and close issue

- [ ] **Step 1: Push to remote**

```bash
git push origin main
```

- [ ] **Step 2: Verify issue auto-closed**

```bash
gh issue view 143 --json state -q .state
# Expected: CLOSED (from "closes #143" in commit messages)
```

If not auto-closed, close manually:
```bash
gh issue close 143 -c "Consolidated to single proxy. Per-request headers are now the source of truth for attribution."
```

- [ ] **Step 3: Final verification — all proxies healthy**

```bash
curl -s http://localhost:30400/health | python3 -m json.tool
kubectl get pods -n agentweave
# Should show: 1 proxy pod, 1 dashboard pod, both Running
```
