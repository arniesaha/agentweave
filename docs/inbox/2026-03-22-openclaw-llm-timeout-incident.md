# Incident: OpenClaw "LLM request timed out" — Mar 22, 2026

## Summary

The Nix agent (OpenClaw main session, Telegram channel) stopped responding to all messages with `"LLM request timed out"`. This coincided with the agentweave-bridge plugin implementation (issue #103, PR #104), which was initially suspected but ruled out as the cause.

**Root cause:** Anthropic 429 rate limiting on a bloated session (~476K tokens) combined with a proxy bug that masked the 429 error behind an HTTP 200 streaming response.

**Resolution:** Rotated the agent to a fresh session and fixed the proxy streaming error propagation.

---

## Timeline (all times PDT, Mar 21–22 2026)

| Time | Event |
|------|-------|
| ~22:30 | Nix actively working on agentweave-bridge plugin (issue #103). Session at ~476K tokens. |
| 22:47:44 | Nix rewrites `service.ts` (7026 bytes) — new version of the plugin |
| 22:48:22 | Nix sends `SIGUSR1` to gateway PID 766061 to reload plugins |
| 22:48:24 | **First failure**: `stopReason: "error"`, `errorMessage: "request ended without sending any chunks"`, all usage fields = 0 |
| 22:48:35 | Gateway process restarts (new PID 767014 via systemd) |
| 22:48:54 | Second failure — same error. Every subsequent message fails identically. |
| 22:49:04 | User message "Is it working now?" — same error |
| 22:55:21 | Another attempt — same error |
| ~23:00 | Investigation begins in Claude Code session |

---

## Investigation

### What we checked

1. **Proxy health**: `GET /health` → `{"status":"ok","version":"0.2.0"}` — proxy pod running fine
2. **Gateway HTTP endpoint**: `POST /v1/chat/completions` on port 18789 → responded correctly ("Hey Arnab! ⚡")
3. **Proxy pod logs**: Every agent request showed `200 OK` followed by `← upstream error status=429`
4. **Session log analysis**: Last successful call had `totalTokens: 476084`, `cacheRead: 473773`
5. **Config diff**: Only change from backup was adding `agentweave-bridge` plugin entry

### Plugin ruled out

Disabled the `agentweave-bridge` plugin in `openclaw.json` (`enabled: false`) and restarted the gateway. Same 429 error persisted — **plugin is not the cause**.

### Root cause identified

**Two independent issues compounding:**

1. **Anthropic 429 rate limiting**: The session had accumulated ~476K tokens of context. Each request sent this full context to Anthropic, consuming massive rate limit capacity for the OAuth token (`sk-ant-oat01-*`). Anthropic rejected every request with 429 within ~1.3 seconds.

2. **Proxy streaming 429 bug**: The proxy's `_stream_and_trace` handler creates a `StreamingResponse(status_code=200)` before reading the upstream response. When upstream returned 429, the JSON error body was streamed as fake SSE. OpenClaw received no valid SSE chunks and reported `"request ended without sending any chunks"`, displayed to the user as `"LLM request timed out"`.

### Why it appeared to correlate with the plugin

The `SIGUSR1` reload happened to be the moment the session was near-max context. The gateway restart (via systemd auto-restart after the old process died) loaded the same bloated session, and every subsequent request hit the 429 wall. The timing made it look like the plugin caused the break.

---

## Resolution

### Immediate fix: Session rotation

1. Backed up the session file:
   ```
   /home/Arnab/.openclaw/agents/main/sessions/07e2e8ff-...jsonl.backup-20260321-231028
   ```

2. Archived the active session file (`.deleted.*` convention)

3. Removed `agent:main:main` entry from `sessions.json`

4. Restarted gateway: `openclaw gateway restart`

5. Re-enabled the agentweave-bridge plugin

**Result:** New session `8cde6255-1cda-4314-9dea-a63c4e9109bd` — responded in 2.6s with 27K tokens. Clean `200 OK` from proxy, no 429.

### Code fix: Proxy streaming error propagation

**File:** `sdk/python/agentweave/proxy.py`

Added `_stream_preflight()` — before committing to a 200 `StreamingResponse`, the proxy now opens a probe connection to check the upstream status code. If it's >= 400, the error body is read and returned as a `JSONResponse` with the correct HTTP status code.

This means callers (OpenClaw, Claude Code, etc.) now see the real 429/500/etc. error instead of a silent empty stream that masquerades as a timeout.

---

## Files involved

| File | Purpose |
|------|---------|
| `/home/Arnab/.openclaw/openclaw.json` | OpenClaw config — plugin enable/disable |
| `/home/Arnab/.openclaw/agents/main/sessions/sessions.json` | Session store — rotated entry |
| `/home/Arnab/.openclaw/agents/main/sessions/07e2e8ff-*.backup-*` | Backed-up session transcript |
| `/home/Arnab/dev/agentweave/sdk/python/agentweave/proxy.py` | Proxy fix — `_stream_preflight()` |
| `/home/Arnab/clawd/.openclaw/extensions/openclaw-agentweave-bridge/src/service.ts` | Plugin (not the cause) |

---

## Lessons learned

1. **Large sessions + OAuth rate limits**: Consumer OAuth tokens have per-token rate limits. Sessions approaching 500K context tokens can exhaust the rate budget in a single request. OpenClaw's compaction mode (`safeguard`) should be tuned to compact earlier.

2. **Proxy must propagate error status codes in streaming mode**: The `StreamingResponse` pattern in FastAPI commits to HTTP 200 before reading the upstream response. A preflight check is necessary to catch 4xx/5xx before streaming begins.

3. **Correlation ≠ causation**: The plugin reload and the LLM failure happened at the same timestamp, but the actual cause was the session size hitting Anthropic's rate limit. Always verify by disabling the suspected component.

4. **"LLM request timed out" is misleading**: OpenClaw displays this for any failed LLM call, including rate limits and auth errors. The actual error (`request ended without sending any chunks`) was only visible in the session JSONL transcript.

---

## Follow-up items

- [ ] Tune OpenClaw compaction settings to prevent sessions from growing past ~200K tokens
- [ ] Deploy the proxy streaming preflight fix to the `agentweave-proxy` pod
- [ ] Consider adding retry-after header parsing in the proxy for 429 responses
- [ ] The agentweave-bridge plugin (issue #103) still needs validation — traces were not flowing because `__openclawDiagnosticEventsState` was not found on `globalThis` (see session log at 22:47)
