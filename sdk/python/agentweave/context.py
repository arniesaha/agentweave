"""Session-scoped context propagation for AgentWeave instrumentation.

A single ``ContextVar`` holds the current session_id so that
auto-instrumented LLM spans and ``@trace_tool`` spans can stamp
``session.id`` and ``prov.session.id`` without the caller having to
thread it through every call site.

Resolution order (see :func:`current_session_id`):

1. Active ``ContextVar`` value (set by ``@trace_agent`` or ``session_scope``).
2. ``AGENTWEAVE_SESSION_ID`` environment variable.
3. ``None`` — span is emitted without ``session.id``.
"""

from __future__ import annotations

import contextvars
import logging
import os
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger("agentweave")

_session_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "agentweave_session_id", default=None
)

_warned_missing: bool = False


def current_session_id() -> Optional[str]:
    """Return the active session_id, or ``None`` if no source is set."""
    sid = _session_id_var.get()
    if sid:
        return sid
    env = os.environ.get("AGENTWEAVE_SESSION_ID")
    if env:
        return env
    return None


def set_session_id(sid: Optional[str]) -> contextvars.Token:
    """Set the ContextVar; returns a Token for ``reset()``."""
    return _session_id_var.set(sid)


@contextmanager
def session_scope(sid: Optional[str]) -> Iterator[None]:
    """Bind ``sid`` as the active session_id for the duration of the block."""
    token = _session_id_var.set(sid)
    try:
        yield
    finally:
        _session_id_var.reset(token)


def warn_missing_session_id_once() -> None:
    """Emit a single debug-gated warning when a span is stamped without session_id.

    Gated on ``AGENTWEAVE_DEBUG=1`` so prod stays quiet.  The warning fires
    at most once per process to avoid log spam under load.
    """
    global _warned_missing
    if _warned_missing:
        return
    if os.environ.get("AGENTWEAVE_DEBUG") != "1":
        return
    _warned_missing = True
    logger.warning(
        "agentweave: span emitted without session_id "
        "(set AGENTWEAVE_SESSION_ID, use @trace_agent(session_id=...), "
        "or wrap in session_scope())"
    )


def _reset_warned_for_tests() -> None:
    """Test-only: reset the once-flag so tests can re-trigger the warning."""
    global _warned_missing
    _warned_missing = False
