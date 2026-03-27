"""AgentWeave Prompt Registry — SQLite-backed CRUD with OTel span tagging.

Stores named prompts with content-hash versioning.  Integrates with the
AgentWeave proxy so spans are enriched with ``prov.prompt.id`` and
``prov.prompt.version`` attributes for version-aware tracing.

Usage (SDK)::

    import agentweave

    # Fetch a prompt (latest version)
    handle = agentweave.prompt("system-prompt")
    print(handle.content)

    # Fetch a specific version
    handle = agentweave.prompt("system-prompt", version="abc12345")

    # Use inside a traced span — attributes auto-attached
    with handle.as_span_context():
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": handle.content}],
        )

Storage::

    ~/.agentweave/prompts.db   (SQLite, stdlib only)

REST API (proxy)::

    GET    /v1/prompts                  list all prompts (latest versions)
    GET    /v1/prompts/{name}           get latest version of a named prompt
    GET    /v1/prompts/{name}/{version} get specific version
    POST   /v1/prompts                  create/update prompt
    DELETE /v1/prompts/{name}           delete all versions of a prompt
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

_DB_PATH_DEFAULT = os.path.expanduser("~/.agentweave/prompts.db")
_DB_PATH: str = os.getenv("AGENTWEAVE_PROMPTS_DB", _DB_PATH_DEFAULT)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None
_conn_cache: dict[str, sqlite3.Connection] = {}


def _get_conn(db_path: str = _DB_PATH) -> sqlite3.Connection:
    """Return a cached SQLite connection for *db_path*.

    When *db_path* equals the default module-level path, a single shared
    connection is reused (production path).  For other paths (e.g. tests),
    a per-path cache is used so tests with different ``db_path`` fixtures
    don't share the same connection.
    """
    if db_path in _conn_cache:
        return _conn_cache[db_path]
    with _lock:
        if db_path not in _conn_cache:
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            _init_schema(conn)
            _conn_cache[db_path] = conn
    return _conn_cache[db_path]


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS prompts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            version     TEXT    NOT NULL,
            content     TEXT    NOT NULL,
            description TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL,
            UNIQUE(name, version)
        );
        CREATE INDEX IF NOT EXISTS idx_prompts_name ON prompts(name);
        CREATE INDEX IF NOT EXISTS idx_prompts_name_created ON prompts(name, created_at DESC);
    """)
    conn.commit()


def _hash_content(content: str) -> str:
    """SHA-256 first 8 chars — short deterministic version string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PromptRecord:
    id: int
    name: str
    version: str
    content: str
    description: str
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "content": self.content,
            "description": self.description,
            "created_at": self.created_at,
        }


def _row_to_record(row: sqlite3.Row) -> PromptRecord:
    return PromptRecord(
        id=row["id"],
        name=row["name"],
        version=row["version"],
        content=row["content"],
        description=row["description"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

def list_prompts(db_path: str = _DB_PATH) -> list[PromptRecord]:
    """Return the latest version of every named prompt."""
    conn = _get_conn(db_path)
    rows = conn.execute("""
        SELECT p.*
        FROM prompts p
        INNER JOIN (
            SELECT name, MAX(created_at) AS latest
            FROM prompts
            GROUP BY name
        ) latest ON p.name = latest.name AND p.created_at = latest.latest
        ORDER BY p.name
    """).fetchall()
    return [_row_to_record(r) for r in rows]


def list_prompt_versions(name: str, db_path: str = _DB_PATH) -> list[PromptRecord]:
    """Return all versions of a named prompt, newest first."""
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM prompts WHERE name = ? ORDER BY created_at DESC",
        (name,),
    ).fetchall()
    return [_row_to_record(r) for r in rows]


def get_prompt(name: str, version: str | None = None, db_path: str = _DB_PATH) -> PromptRecord | None:
    """Fetch a prompt by name and optional version.  Returns latest if version is None."""
    conn = _get_conn(db_path)
    if version:
        row = conn.execute(
            "SELECT * FROM prompts WHERE name = ? AND version = ?",
            (name, version),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM prompts WHERE name = ? ORDER BY created_at DESC LIMIT 1",
            (name,),
        ).fetchone()
    return _row_to_record(row) if row else None


def create_prompt(
    name: str,
    content: str,
    description: str = "",
    version: str | None = None,
    db_path: str = _DB_PATH,
) -> PromptRecord:
    """Create a new prompt version.

    If *version* is not given, it is derived from the content hash.
    Raises ``ValueError`` if the exact (name, version) pair already exists.
    """
    conn = _get_conn(db_path)
    ver = version or _hash_content(content)
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _lock:
            conn.execute(
                "INSERT INTO prompts (name, version, content, description, created_at) VALUES (?, ?, ?, ?, ?)",
                (name, ver, content, description, now),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"Prompt '{name}' version '{ver}' already exists")
    row = conn.execute(
        "SELECT * FROM prompts WHERE name = ? AND version = ?",
        (name, ver),
    ).fetchone()
    return _row_to_record(row)


def update_prompt(
    name: str,
    content: str,
    description: str | None = None,
    version: str | None = None,
    db_path: str = _DB_PATH,
) -> PromptRecord:
    """Create a new version of an existing prompt (non-destructive update).

    Functionally equivalent to ``create_prompt`` — old versions are preserved.
    """
    existing = get_prompt(name, db_path=db_path)
    desc = description if description is not None else (existing.description if existing else "")
    return create_prompt(name, content, description=desc, version=version, db_path=db_path)


def delete_prompt(name: str, db_path: str = _DB_PATH) -> int:
    """Delete all versions of a named prompt.  Returns number of rows deleted."""
    conn = _get_conn(db_path)
    with _lock:
        cur = conn.execute("DELETE FROM prompts WHERE name = ?", (name,))
        conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# PromptHandle — returned by SDK agentweave.prompt(...)
# ---------------------------------------------------------------------------

@dataclass
class PromptHandle:
    """Wraps a fetched prompt and provides helpers for span attribute injection.

    Attributes:
        name:       Prompt name (registry key)
        version:    Content-hash version string
        content:    The prompt text
        prompt_id:  Numeric DB row ID (stable identifier)

    Use ``handle.content`` as the prompt text.
    Use ``handle.span_attributes`` to manually add attributes to a span.
    Use ``handle.tag_span(span)`` to enrich an existing OTel span.
    Use ``handle.as_span_context()`` as a context manager to set attributes
    on the *current active span*.
    """

    name: str
    version: str
    content: str
    prompt_id: int

    @property
    def span_attributes(self) -> dict[str, Any]:
        return {
            "prov.prompt.id": str(self.prompt_id),
            "prov.prompt.name": self.name,
            "prov.prompt.version": self.version,
        }

    def tag_span(self, span: Any) -> None:
        """Set prompt attributes on *span* directly."""
        for k, v in self.span_attributes.items():
            span.set_attribute(k, v)

    @contextmanager
    def as_span_context(self) -> Iterator[None]:
        """Context manager that tags the *currently active* OTel span."""
        from opentelemetry import trace as _trace
        span = _trace.get_current_span()
        if span and span.is_recording():
            self.tag_span(span)
        yield

    def __str__(self) -> str:  # noqa: D401
        return self.content


# ---------------------------------------------------------------------------
# SDK entry point
# ---------------------------------------------------------------------------

def fetch_prompt(
    name: str,
    version: str | None = None,
    proxy_url: str | None = None,
) -> PromptHandle:
    """Fetch a prompt from the registry and return a ``PromptHandle``.

    By default uses the local SQLite database.  If *proxy_url* is given
    (or ``AGENTWEAVE_PROXY_URL`` is set), fetches via the proxy REST API
    instead so multi-process agent deployments share one registry.

    Args:
        name:      Prompt name to look up.
        version:   Specific version hash (default: latest).
        proxy_url: Override proxy base URL (e.g. ``http://localhost:4000``).

    Returns:
        A ``PromptHandle`` wrapping the prompt content.

    Raises:
        KeyError: If no matching prompt is found.
    """
    base = proxy_url or os.getenv("AGENTWEAVE_PROXY_URL")
    if base:
        return _fetch_via_proxy(name, version, base)
    return _fetch_local(name, version)


def _fetch_local(name: str, version: str | None) -> PromptHandle:
    # Use the current value of _DB_PATH (may be monkeypatched in tests)
    record = get_prompt(name, version, db_path=_DB_PATH)
    if record is None:
        raise KeyError(f"Prompt '{name}'" + (f" version '{version}'" if version else "") + " not found")
    return PromptHandle(
        name=record.name,
        version=record.version,
        content=record.content,
        prompt_id=record.id,
    )


def _fetch_via_proxy(name: str, version: str | None, base: str) -> PromptHandle:
    """Fetch prompt from proxy REST API (avoids db path config on agents)."""
    import urllib.request, json as _json  # noqa: E401 — stdlib only

    base = base.rstrip("/")
    path = f"{base}/v1/prompts/{name}"
    if version:
        path += f"/{version}"
    with urllib.request.urlopen(path, timeout=5) as resp:  # noqa: S310
        data = _json.loads(resp.read())
    if "error" in data:
        raise KeyError(data["error"])
    return PromptHandle(
        name=data["name"],
        version=data["version"],
        content=data["content"],
        prompt_id=data["id"],
    )
