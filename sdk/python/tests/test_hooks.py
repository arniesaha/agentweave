"""Tests for Claude Code hooks integration — CLI, proxy endpoints, and shell scripts."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="proxy deps not installed — install with agentweave[proxy]")
pytest.importorskip("typer", reason="CLI deps not installed")

pytestmark = pytest.mark.hooks


class TestHooksInstallCLI:
    """Tests for the agentweave hooks install/uninstall commands."""

    def test_hooks_install_creates_settings(self, tmp_path: Path):
        """Install creates settings.json with hooks when none exists."""
        from typer.testing import CliRunner
        from agentweave.cli import app

        runner = CliRunner()
        settings_file = tmp_path / "settings.json"

        result = runner.invoke(app, ["hooks", "install", "--settings-path", str(settings_file)])

        assert result.exit_code == 0
        assert settings_file.exists()
        settings = json.loads(settings_file.read_text())
        assert "hooks" in settings
        assert "PostToolUse" in settings["hooks"]
        assert "SubagentStop" in settings["hooks"]
        assert "Stop" in settings["hooks"]

    def test_hooks_install_preserves_existing(self, tmp_path: Path):
        """Install preserves existing hooks and settings."""
        from typer.testing import CliRunner
        from agentweave.cli import app

        runner = CliRunner()
        settings_file = tmp_path / "settings.json"

        # Create existing settings with a custom hook
        existing = {
            "theme": "dark",
            "hooks": {
                "PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": "my-custom-hook"}]}]
            }
        }
        settings_file.write_text(json.dumps(existing))

        result = runner.invoke(app, ["hooks", "install", "--settings-path", str(settings_file)])

        assert result.exit_code == 0
        settings = json.loads(settings_file.read_text())
        # Existing settings preserved
        assert settings["theme"] == "dark"
        assert "PreToolUse" in settings["hooks"]
        # New hooks added
        assert "PostToolUse" in settings["hooks"]
        assert "SubagentStop" in settings["hooks"]
        assert "Stop" in settings["hooks"]

    def test_hooks_install_idempotent(self, tmp_path: Path):
        """Running install twice doesn't duplicate hooks."""
        from typer.testing import CliRunner
        from agentweave.cli import app

        runner = CliRunner()
        settings_file = tmp_path / "settings.json"

        # Install twice
        runner.invoke(app, ["hooks", "install", "--settings-path", str(settings_file)])
        runner.invoke(app, ["hooks", "install", "--settings-path", str(settings_file)])

        settings = json.loads(settings_file.read_text())
        # Each hook type should have exactly one entry
        assert len(settings["hooks"]["PostToolUse"]) == 1
        assert len(settings["hooks"]["SubagentStop"]) == 1
        assert len(settings["hooks"]["Stop"]) == 1

    def test_hooks_install_dry_run(self, tmp_path: Path):
        """Dry run shows changes without modifying file."""
        from typer.testing import CliRunner
        from agentweave.cli import app

        runner = CliRunner()
        settings_file = tmp_path / "settings.json"

        result = runner.invoke(app, ["hooks", "install", "--dry-run", "--settings-path", str(settings_file)])

        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert not settings_file.exists()

    def test_hooks_uninstall_removes_agentweave_hooks(self, tmp_path: Path):
        """Uninstall removes only AgentWeave hooks, preserving others."""
        from typer.testing import CliRunner
        from agentweave.cli import app

        runner = CliRunner()
        settings_file = tmp_path / "settings.json"

        # Install first
        runner.invoke(app, ["hooks", "install", "--settings-path", str(settings_file)])

        # Add a custom hook
        settings = json.loads(settings_file.read_text())
        settings["hooks"]["PostToolUse"].append({
            "matcher": "Write",
            "hooks": [{"type": "command", "command": "my-write-validator"}]
        })
        settings_file.write_text(json.dumps(settings))

        # Uninstall
        result = runner.invoke(app, ["hooks", "uninstall", "--settings-path", str(settings_file)])

        assert result.exit_code == 0
        settings = json.loads(settings_file.read_text())
        # AgentWeave hooks removed
        assert "SubagentStop" not in settings["hooks"]
        assert "Stop" not in settings["hooks"]
        # Custom hook preserved
        assert "PostToolUse" in settings["hooks"]
        assert any(
            h.get("command") == "my-write-validator"
            for entry in settings["hooks"]["PostToolUse"]
            for h in entry.get("hooks", [])
        )


class TestHooksProxyEndpoints:
    """Tests for /hooks/span and /hooks/batch proxy endpoints."""

    @pytest.fixture
    def client(self):
        """Create a test client for the proxy app."""
        from fastapi.testclient import TestClient
        from agentweave.proxy import app
        return TestClient(app)

    def test_hooks_span_creates_span(self, client):
        """POST /hooks/span creates a span with provided attributes."""
        response = client.post("/hooks/span", json={
            "span_name": "subagent.stop",
            "session_id": "test-session-123",
            "attributes": {
                "prov.parent_session_id": "parent-session-456",
                "prov.agent.type": "subagent",
            }
        })

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["span_name"] == "subagent.stop"

    def test_hooks_span_minimal_payload(self, client):
        """POST /hooks/span works with minimal payload."""
        response = client.post("/hooks/span", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["span_name"] == "hook.span"

    def test_hooks_batch_creates_spans(self, client):
        """POST /hooks/batch creates spans for all events."""
        response = client.post("/hooks/batch", json={
            "session_id": "test-session-123",
            "events": [
                {
                    "event": "post_tool_use",
                    "ts": 1711234567890,
                    "data": {"tool_name": "Read", "tool_input": "/path/to/file.py"}
                },
                {
                    "event": "post_tool_use",
                    "ts": 1711234567891,
                    "data": {"tool_name": "Write", "tool_input": "/path/to/output.py"}
                },
            ]
        })

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["spans_created"] == 2

    def test_hooks_batch_empty_events(self, client):
        """POST /hooks/batch handles empty events list."""
        response = client.post("/hooks/batch", json={
            "session_id": "test-session-123",
            "events": []
        })

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["spans_created"] == 0

    def test_hooks_batch_extracts_tool_data(self, client):
        """POST /hooks/batch extracts tool name, input, and result."""
        response = client.post("/hooks/batch", json={
            "session_id": "test-session-123",
            "events": [
                {
                    "event": "post_tool_use",
                    "ts": 1711234567890,
                    "data": {
                        "toolName": "Bash",
                        "toolInput": "ls -la",
                        "toolResult": "total 42\ndrwxr-xr-x  5 user staff  160 Mar 23 12:00 ."
                    }
                },
            ]
        })

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["spans_created"] == 1


class TestHooksSpanContent:
    """Tests asserting on emitted span structure, not just HTTP status.

    Covers #246 (spans emitted as trace roots), #247 (no agent attribution on
    hook spans), and #248 (spans stamped with export time, not event time).
    """

    @pytest.fixture
    def captured_spans(self):
        """Install an in-memory exporter on the proxy's tracer provider."""
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )
        from agentweave import exporter as aw_exporter

        previous = aw_exporter._provider
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        aw_exporter._provider = provider
        try:
            yield exporter
        finally:
            aw_exporter._provider = previous

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from agentweave.proxy import app

        return TestClient(app)

    # -- #246: parent context ------------------------------------------------

    def test_extract_parent_context_parses_valid_traceparent(self):
        """A well-formed traceparent yields a context carrying that trace id."""
        from opentelemetry import trace as otel_trace
        from agentweave.proxy import _extract_parent_context

        traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

        ctx = _extract_parent_context(traceparent)

        assert ctx is not None
        span_ctx = otel_trace.get_current_span(ctx).get_span_context()
        assert span_ctx.trace_id == 0x4BF92F3577B34DA6A3CE929D0E0E4736
        assert span_ctx.span_id == 0x00F067AA0BA902B7

    def test_extract_parent_context_returns_none_for_malformed(self):
        """A malformed traceparent degrades to no parent rather than raising."""
        from agentweave.proxy import _extract_parent_context

        assert _extract_parent_context("not-a-traceparent") is None

    def test_hooks_batch_parents_spans_under_traceparent(self, client, captured_spans):
        """Events posted with a traceparent become children of that trace."""
        client.post(
            "/hooks/batch",
            json={
                "session_id": "sess-1",
                "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
                "events": [
                    {"event": "post_tool_use", "ts": 1711234567890, "data": {"tool_name": "Read"}},
                ],
            },
        )

        spans = captured_spans.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].parent is not None
        assert spans[0].parent.span_id == 0x00F067AA0BA902B7
        assert spans[0].context.trace_id == 0x4BF92F3577B34DA6A3CE929D0E0E4736

    def test_hooks_batch_without_traceparent_emits_root(self, client, captured_spans):
        """Absent a traceparent, spans remain roots — existing behaviour."""
        client.post(
            "/hooks/batch",
            json={
                "session_id": "sess-1",
                "events": [
                    {"event": "post_tool_use", "ts": 1711234567890, "data": {"tool_name": "Read"}},
                ],
            },
        )

        spans = captured_spans.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].parent is None

    # -- #247: agent attribution --------------------------------------------

    def test_hooks_batch_sets_agent_attribution(self, client, captured_spans):
        """Agent identity supplied in the body lands on every emitted span."""
        client.post(
            "/hooks/batch",
            json={
                "session_id": "sess-1",
                "agent_id": "claude-code-nas",
                "agent_type": "main",
                "project": "agentweave",
                "cwd": "/home/arnab/dev/agentweave",
                "events": [
                    {"event": "post_tool_use", "ts": 1711234567890, "data": {"tool_name": "Bash"}},
                ],
            },
        )

        attrs = captured_spans.get_finished_spans()[0].attributes
        assert attrs["prov.agent.id"] == "claude-code-nas"
        assert attrs["prov.agent.type"] == "main"
        assert attrs["prov.project"] == "agentweave"
        assert attrs["prov.cwd"] == "/home/arnab/dev/agentweave"
        assert attrs["prov.harness"] == "claude-code"

    def test_hooks_batch_omits_absent_attribution_fields(self, client, captured_spans):
        """Missing agent identity omits the keys rather than writing empties."""
        client.post(
            "/hooks/batch",
            json={
                "session_id": "sess-1",
                "events": [
                    {"event": "post_tool_use", "ts": 1711234567890, "data": {"tool_name": "Bash"}},
                ],
            },
        )

        attrs = captured_spans.get_finished_spans()[0].attributes
        assert "prov.agent.id" not in attrs
        assert "prov.project" not in attrs
        # harness is always known on this endpoint
        assert attrs["prov.harness"] == "claude-code"

    def test_hooks_span_sets_agent_attribution(self, client, captured_spans):
        """SubagentStop spans are attributable too, not just batched ones."""
        client.post(
            "/hooks/span",
            json={
                "span_name": "subagent.stop",
                "session_id": "sess-1",
                "agent_id": "claude-code-nas",
                "project": "agentweave",
                "attributes": {"prov.agent.type": "subagent"},
            },
        )

        attrs = captured_spans.get_finished_spans()[0].attributes
        assert attrs["prov.agent.id"] == "claude-code-nas"
        assert attrs["prov.project"] == "agentweave"
        assert attrs["prov.harness"] == "claude-code"
        # explicit attributes still win for keys they set
        assert attrs["prov.agent.type"] == "subagent"

    # -- #248: event-time timestamps ----------------------------------------

    def test_hooks_batch_stamps_spans_at_event_time(self, client, captured_spans):
        """Span start times come from the buffered ts, not the export moment."""
        client.post(
            "/hooks/batch",
            json={
                "session_id": "sess-1",
                "events": [
                    {"event": "post_tool_use", "ts": 1711234567890, "data": {"tool_name": "Read"}},
                    {"event": "post_tool_use", "ts": 1711234599999, "data": {"tool_name": "Write"}},
                ],
            },
        )

        spans = captured_spans.get_finished_spans()
        assert len(spans) == 2
        starts = sorted(s.start_time for s in spans)
        assert starts[0] == 1711234567890 * 1_000_000
        assert starts[1] == 1711234599999 * 1_000_000

    def test_hooks_batch_falls_back_to_wall_clock_without_ts(self, client, captured_spans):
        """An event with no ts still produces a span with a sane start time."""
        client.post(
            "/hooks/batch",
            json={
                "session_id": "sess-1",
                "events": [{"event": "post_tool_use", "data": {"tool_name": "Read"}}],
            },
        )

        spans = captured_spans.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].start_time > 0


class TestHooksShellScripts:
    """Tests for hook shell script existence and structure."""

    def _get_hooks_dir(self):
        """Find the hooks directory (in package or project root)."""
        from pathlib import Path

        # First try the package directory
        test_dir = Path(__file__).parent
        package_hooks = test_dir.parent / "agentweave" / "hooks" / "claude-code"
        if package_hooks.exists():
            return package_hooks

        # Fallback to project root
        project_hooks = test_dir.parent.parent.parent / "agentweave" / "hooks" / "claude-code"
        return project_hooks

    def test_hook_scripts_exist(self):
        """All required hook scripts exist."""
        hooks_dir = self._get_hooks_dir()

        assert (hooks_dir / "post_tool_use.sh").exists()
        assert (hooks_dir / "subagent_stop.sh").exists()
        assert (hooks_dir / "stop.sh").exists()

    def test_hook_scripts_executable(self):
        """Hook scripts have executable permissions."""
        import os

        hooks_dir = self._get_hooks_dir()

        for script in ["post_tool_use.sh", "subagent_stop.sh", "stop.sh"]:
            script_path = hooks_dir / script
            assert os.access(script_path, os.X_OK), f"{script} should be executable"

    def _run_stop_hook(self, tmp_path, env_extra):
        """Run stop.sh against a stub curl, returning the JSON body it posted."""
        import os
        import subprocess

        session_id = "sess-shell-1"
        buffer = tmp_path / f"hooks_buffer_{session_id}.jsonl"
        buffer.write_text(
            json.dumps(
                {"event": "post_tool_use", "ts": 1711234567890, "session_id": session_id,
                 "data": {"tool_name": "Bash"}}
            )
            + "\n"
        )

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        captured = tmp_path / "payload.json"
        stub = bin_dir / "curl"
        stub.write_text(
            '#!/bin/bash\n'
            'prev=""\n'
            'for a in "$@"; do\n'
            '  if [ "$prev" = "-d" ]; then printf "%s" "$a" > ' f'"{captured}"' '; fi\n'
            '  prev="$a"\n'
            'done\n'
        )
        stub.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["CLAUDE_SESSION_ID"] = session_id
        env["AGENTWEAVE_HOOKS_BUFFER"] = str(buffer)
        env.update(env_extra)

        subprocess.run(
            ["bash", str(self._get_hooks_dir() / "stop.sh")],
            env=env, check=True, capture_output=True,
        )
        return json.loads(captured.read_text())

    def test_stop_hook_sends_agent_attribution(self, tmp_path):
        """stop.sh forwards agent identity from env so spans are attributable."""
        payload = self._run_stop_hook(
            tmp_path,
            {
                "AGENTWEAVE_AGENT_ID": "claude-code-nas",
                "AGENTWEAVE_AGENT_TYPE": "main",
                "AGENTWEAVE_PROJECT": "agentweave",
            },
        )

        assert payload["agent_id"] == "claude-code-nas"
        assert payload["agent_type"] == "main"
        assert payload["project"] == "agentweave"

    def test_stop_hook_omits_unset_attribution(self, tmp_path):
        """Unset agent identity yields empty strings the proxy then drops."""
        payload = self._run_stop_hook(tmp_path, {})

        assert payload["session_id"] == "sess-shell-1"
        assert not payload.get("agent_id")

    def test_settings_template_valid_json(self):
        """Settings template is valid JSON with expected structure."""
        hooks_dir = self._get_hooks_dir()
        template_path = hooks_dir / "settings_template.json"

        content = json.loads(template_path.read_text())
        assert "hooks" in content
        assert "PostToolUse" in content["hooks"]
        assert "SubagentStop" in content["hooks"]
        assert "Stop" in content["hooks"]

        # Verify PostToolUse has a matcher
        post_tool_use = content["hooks"]["PostToolUse"][0]
        assert "matcher" in post_tool_use
        assert post_tool_use["matcher"] == ".*"
