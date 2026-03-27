"""Tests for the AgentWeave prompt registry (issue #111)."""

from __future__ import annotations

import os
import tempfile
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path):
    """Return a fresh temporary SQLite path for each test."""
    return str(tmp_path / "prompts.db")


# ---------------------------------------------------------------------------
# Import helpers that accept a db_path override
# ---------------------------------------------------------------------------

from agentweave.prompts import (
    create_prompt,
    get_prompt,
    list_prompts,
    list_prompt_versions,
    update_prompt,
    delete_prompt,
    _hash_content,
    _get_conn,
    PromptHandle,
    fetch_prompt,
)


# ---------------------------------------------------------------------------
# Tests: CRUD
# ---------------------------------------------------------------------------


def test_create_and_get_prompt(db_path):
    record = create_prompt("greet", "Hello, world!", db_path=db_path)
    assert record.name == "greet"
    assert record.content == "Hello, world!"
    assert len(record.version) == 8  # SHA-256 first 8 hex chars
    assert record.id > 0

    fetched = get_prompt("greet", db_path=db_path)
    assert fetched is not None
    assert fetched.content == "Hello, world!"
    assert fetched.version == record.version


def test_create_prompt_explicit_version(db_path):
    record = create_prompt("system", "Be helpful.", version="v1", db_path=db_path)
    assert record.version == "v1"


def test_create_prompt_duplicate_raises(db_path):
    create_prompt("dup", "same content", version="v1", db_path=db_path)
    with pytest.raises(ValueError, match="already exists"):
        create_prompt("dup", "same content", version="v1", db_path=db_path)


def test_get_prompt_not_found(db_path):
    result = get_prompt("nonexistent", db_path=db_path)
    assert result is None


def test_get_specific_version(db_path):
    r1 = create_prompt("p", "v1 content", version="v1", db_path=db_path)
    r2 = create_prompt("p", "v2 content", version="v2", db_path=db_path)

    fetched_v1 = get_prompt("p", version="v1", db_path=db_path)
    assert fetched_v1 is not None
    assert fetched_v1.content == "v1 content"

    fetched_v2 = get_prompt("p", version="v2", db_path=db_path)
    assert fetched_v2 is not None
    assert fetched_v2.content == "v2 content"


def test_get_latest_returns_newest(db_path):
    import time
    create_prompt("p", "first", version="v1", db_path=db_path)
    time.sleep(0.01)  # ensure different created_at
    create_prompt("p", "second", version="v2", db_path=db_path)

    latest = get_prompt("p", db_path=db_path)
    assert latest is not None
    assert latest.content == "second"
    assert latest.version == "v2"


def test_list_prompts_returns_latest(db_path):
    import time
    create_prompt("alpha", "a1", version="v1", db_path=db_path)
    time.sleep(0.01)
    create_prompt("alpha", "a2", version="v2", db_path=db_path)
    create_prompt("beta", "b1", db_path=db_path)

    records = list_prompts(db_path=db_path)
    names = [r.name for r in records]
    assert "alpha" in names
    assert "beta" in names

    alpha = next(r for r in records if r.name == "alpha")
    assert alpha.version == "v2"  # latest


def test_list_prompt_versions(db_path):
    import time
    create_prompt("q", "first", version="v1", db_path=db_path)
    time.sleep(0.01)
    create_prompt("q", "second", version="v2", db_path=db_path)

    versions = list_prompt_versions("q", db_path=db_path)
    assert len(versions) == 2
    # Newest first
    assert versions[0].version == "v2"
    assert versions[1].version == "v1"


def test_update_prompt_creates_new_version(db_path):
    create_prompt("up", "original", version="v1", db_path=db_path)
    record = update_prompt("up", "updated content", version="v2", db_path=db_path)
    assert record.version == "v2"
    assert record.content == "updated content"

    # Old version still exists
    old = get_prompt("up", version="v1", db_path=db_path)
    assert old is not None
    assert old.content == "original"


def test_delete_prompt(db_path):
    create_prompt("del", "bye", version="v1", db_path=db_path)
    create_prompt("del", "bye v2", version="v2", db_path=db_path)

    deleted = delete_prompt("del", db_path=db_path)
    assert deleted == 2

    result = get_prompt("del", db_path=db_path)
    assert result is None


def test_delete_nonexistent_returns_zero(db_path):
    assert delete_prompt("ghost", db_path=db_path) == 0


def test_hash_content_deterministic():
    h1 = _hash_content("hello")
    h2 = _hash_content("hello")
    assert h1 == h2
    assert len(h1) == 8

    h3 = _hash_content("world")
    assert h1 != h3


# ---------------------------------------------------------------------------
# Tests: PromptHandle
# ---------------------------------------------------------------------------


def test_prompt_handle_span_attributes():
    handle = PromptHandle(name="greet", version="abc12345", content="Hi!", prompt_id=42)
    attrs = handle.span_attributes
    assert attrs["prov.prompt.id"] == "42"
    assert attrs["prov.prompt.name"] == "greet"
    assert attrs["prov.prompt.version"] == "abc12345"


def test_prompt_handle_str():
    handle = PromptHandle(name="greet", version="v1", content="Hello!", prompt_id=1)
    assert str(handle) == "Hello!"


def test_prompt_handle_tag_span():
    class FakeSpan:
        def __init__(self):
            self.attrs = {}
        def set_attribute(self, k, v):
            self.attrs[k] = v

    handle = PromptHandle(name="greet", version="v1", content="Hi", prompt_id=7)
    span = FakeSpan()
    handle.tag_span(span)
    assert span.attrs["prov.prompt.id"] == "7"
    assert span.attrs["prov.prompt.version"] == "v1"


def test_prompt_handle_as_span_context_noop_when_no_span():
    """as_span_context() should not raise even when there's no active span."""
    handle = PromptHandle(name="greet", version="v1", content="Hi", prompt_id=1)
    with handle.as_span_context():
        pass  # no exception


# ---------------------------------------------------------------------------
# Tests: fetch_prompt SDK entry point
# ---------------------------------------------------------------------------


def test_fetch_prompt_local(db_path, monkeypatch):
    monkeypatch.setenv("AGENTWEAVE_PROMPTS_DB", db_path)
    # Point module-level _DB_PATH at our temp db so fetch_prompt uses it
    import agentweave.prompts as _pm
    monkeypatch.setattr(_pm, "_DB_PATH", db_path)

    create_prompt("sdk-test", "SDK content", version="v1", db_path=db_path)
    handle = fetch_prompt("sdk-test", version="v1")
    assert handle.content == "SDK content"
    assert handle.version == "v1"


def test_fetch_prompt_not_found_raises(db_path, monkeypatch):
    monkeypatch.setenv("AGENTWEAVE_PROMPTS_DB", db_path)
    import agentweave.prompts as _pm
    monkeypatch.setattr(_pm, "_DB_PATH", db_path)

    with pytest.raises(KeyError, match="not found"):
        fetch_prompt("missing-prompt")


# ---------------------------------------------------------------------------
# Tests: agentweave top-level import
# ---------------------------------------------------------------------------


def test_agentweave_prompt_exported():
    import agentweave
    assert callable(agentweave.prompt)
    assert agentweave.PromptHandle is PromptHandle
