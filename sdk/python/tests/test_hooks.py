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
