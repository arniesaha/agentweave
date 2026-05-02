# Session-ID propagation for auto-instrumented spans

**Date:** 2026-04-26
**Tracking issues:** [arniesaha/nexus#29](https://github.com/arniesaha/nexus/issues/29)
**Phase 1 scope:** Python SDK
**Phase 2 scope (deferred):** JS SDK

## Problem

A lakehouse audit found that ~40% of rows in `lakehouse.spans` have a NULL or empty `session_id`. Without a consistent `session_id`, spans cannot be correlated to `agent_events`, breaking session replay and lineage analysis.

The root cause is in **agentweave**, not the lakehouse pipeline:

- `@trace_agent(session_id=...)` stamps `session.id` and `prov.session.id` on agent spans.
- The proxy server reads `AGENTWEAVE_SESSION_ID` and stamps every span it emits.
- **`auto_instrument()` LLM wrappers (Anthropic / OpenAI / Google) and `@trace_tool` never read any session context.** Every span produced by these paths goes out without `session.id`.
- `@trace_tool` similarly has no awareness of the active session.

So whenever a user sets `auto_instrument()` and makes an LLM or tool call outside a `@trace_agent`-decorated body, the resulting span lacks a session_id.

## Goals

1. Auto-instrumented LLM spans carry the correct `session.id` and `prov.session.id` whenever any session context is active.
2. `@trace_tool` spans inherit the active session.
3. No regression for code that explicitly passes `session_id=` to `@trace_agent`.
4. No exceptions ever raised from instrumentation. Failure mode is "span without session_id" — never a thrown error.

## Non-goals

- Propagating other identity fields (`user_id`, `agent_id`) — the issue is session_id only.
- Backfilling existing NULL rows in `lakehouse.spans` — forward fix only.
- Changes to OTel Baggage / W3C trace context — out of scope.
- Changes to proxy-server span stamping — proxy already handles this correctly on its side.

## Design

### Source of truth

A new module `agentweave.context` exposes:

```python
_session_id_var: ContextVar[Optional[str]] = ContextVar("agentweave_session_id", default=None)

def current_session_id() -> Optional[str]:
    """ContextVar > AGENTWEAVE_SESSION_ID env var > None."""

def set_session_id(sid: Optional[str]) -> Token:
    """Set the ContextVar. Returns a token for reset()."""

@contextmanager
def session_scope(sid: Optional[str]) -> Iterator[None]:
    """Context manager that sets and resets the ContextVar."""
```

`current_session_id()` is the single function every reader site calls.

### Precedence

When stamping a span, the resolved session_id is the first non-None of:

1. **Explicit kwarg** passed to `@trace_agent(session_id=...)`.
2. **ContextVar** value (set by an outer `session_scope` or `@trace_agent`).
3. **`AGENTWEAVE_SESSION_ID` env var** (legacy / bare-script fallback).
4. None — span is emitted without `session.id`.

### Writers (set the ContextVar)

- `@trace_agent` — when `session_id=` kwarg is non-None, wrap the function body in `session_scope(session_id)` so nested LLM and tool spans inherit it.
- Public API — re-export `session_scope` from the top-level `agentweave` package so user code can set it manually.

### Readers (stamp `session.id` + `prov.session.id`)

- `instrument.py:_make_llm_wrapper` — reads `current_session_id()` and stamps both attributes.
- `decorators.py:_make_tool_wrapper` — same.
- `decorators.py:_make_agent_wrapper` — already stamps when `session_id=` kwarg present; additionally fall back to `current_session_id()` when kwarg is None, so `session_scope("X")` followed by an undecorated `@trace_agent` still gets stamped.

### Failure mode

When `current_session_id()` returns `None` at a reader site:

- Span is emitted without the attribute (current behavior; no regression).
- A module-level logger emits **one** warning per process when `AGENTWEAVE_DEBUG=1`. The "once" is enforced by a module-level boolean. Message:
  ```
  agentweave: span emitted without session_id (set AGENTWEAVE_SESSION_ID, use @trace_agent(session_id=...), or wrap in session_scope())
  ```
- No exceptions. Instrumentation must never break the user's request path.

## Phase 2 — JS SDK (deferred)

Mirror the Python design in `sdk-js/src/`:

- `context.ts` — `AsyncLocalStorage<string | undefined>` (Node only; browser support not required).
- `currentSessionId()` — ALS → `process.env.AGENTWEAVE_SESSION_ID` → `undefined`.
- Writers: `traceAgent` wraps body via ALS `.run`; export `sessionScope`.
- Readers: `instrument.ts:186-195` LLM wrappers and `traceTool` stamp the same attributes.
- Same precedence and same debug-gated single warning.
- Mirror test suite (vitest).

**Trigger to start Phase 2:** post-Phase-1 lakehouse audit shows JS-originated spans still leaking. Tracked as a separate issue on `arniesaha/agentweave` referencing this spec.

## Test plan

New file `sdk/python/tests/test_session_propagation.py` with these cases:

1. `test_auto_instrument_stamps_session_id_from_contextvar`
2. `test_auto_instrument_stamps_session_id_from_env_var`
3. `test_explicit_kwarg_wins_over_contextvar`
4. `test_contextvar_wins_over_env_var`
5. `test_trace_tool_inherits_session_id`
6. `test_no_session_id_emits_no_attribute_no_exception`
7. `test_debug_warning_fires_once`
8. `test_async_contextvar_isolation`

Existing `test_decorators.py` must continue to pass.

## Verification (post-merge)

Cut release, deploy to NAS, then 24–48h later run on the lakehouse:

```sql
SELECT
  COUNT(*) FILTER (WHERE session_id IS NULL OR session_id = '') AS missing,
  COUNT(*) AS total,
  1.0 * COUNT(*) FILTER (WHERE session_id IS NULL OR session_id = '') / COUNT(*) AS ratio
FROM lakehouse.spans
WHERE ingest_date >= '<release_date>';
```

Expect `ratio` to drop toward zero. Track on nexus#29; close once confirmed.

## Files affected (Phase 1)

| Path | Change |
|------|--------|
| `sdk/python/agentweave/context.py` | NEW |
| `sdk/python/agentweave/__init__.py` | Re-export `session_scope`, `current_session_id` |
| `sdk/python/agentweave/decorators.py` | `_make_agent_wrapper` falls back to `current_session_id()`; wraps body in `session_scope` when stamping. `_make_tool_wrapper` stamps from `current_session_id()` |
| `sdk/python/agentweave/instrument.py` | `_make_llm_wrapper` stamps from `current_session_id()` |
| `sdk/python/tests/test_session_propagation.py` | NEW |
| `docs/superpowers/specs/2026-04-26-session-id-propagation-design.md` | NEW (this file) |
