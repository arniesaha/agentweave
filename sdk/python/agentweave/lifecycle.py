"""Local AgentWeave proxy lifecycle helpers.

These helpers intentionally manage only a developer-preview local proxy
process. They do not try to be a service manager, and they keep state in a
small per-user file so the CLI can report and stop the process it started.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


STATE_ENV_VAR = "AGENTWEAVE_STATE_DIR"


@dataclass(frozen=True)
class ProxyState:
    """Persisted state for a CLI-managed local proxy."""

    pid: int
    host: str
    port: int
    url: str
    command: list[str]
    log_file: str
    started_at: float

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ProxyState":
        return cls(
            pid=int(payload["pid"]),
            host=str(payload["host"]),
            port=int(payload["port"]),
            url=str(payload["url"]),
            command=[str(part) for part in payload["command"]],
            log_file=str(payload["log_file"]),
            started_at=float(payload["started_at"]),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def state_dir() -> Path:
    """Return the per-user AgentWeave state directory."""

    override = os.getenv(STATE_ENV_VAR)
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        return Path(base).expanduser() / "AgentWeave" if base else Path.home() / "AppData" / "Local" / "AgentWeave"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "agentweave"

    base = os.getenv("XDG_STATE_HOME")
    return Path(base).expanduser() / "agentweave" if base else Path.home() / ".local" / "state" / "agentweave"


def state_file() -> Path:
    return state_dir() / "proxy.json"


def log_file() -> Path:
    return state_dir() / "proxy.log"


def read_state() -> ProxyState | None:
    path = state_file()
    if not path.exists():
        return None
    try:
        return ProxyState.from_dict(json.loads(path.read_text()))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def write_state(state: ProxyState) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n")


def clear_state() -> None:
    try:
        state_file().unlink()
    except FileNotFoundError:
        pass


def is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def current_status() -> tuple[str, ProxyState | None]:
    """Return ``running``, ``stale``, or ``stopped`` with any known state."""

    state = read_state()
    if not state:
        return "stopped", None
    if is_process_running(state.pid):
        return "running", state
    return "stale", state


def start_proxy_process(
    *,
    host: str,
    port: int,
    endpoint: str | None = None,
    agent_id: str | None = None,
    capture_prompts: bool = False,
    auth_token: str | None = None,
) -> ProxyState:
    """Start a detached local proxy process and persist its state."""

    existing_status, existing_state = current_status()
    if existing_status == "running" and existing_state:
        raise RuntimeError(f"AgentWeave proxy is already running as pid {existing_state.pid}.")
    if existing_status == "stale":
        clear_state()

    path = log_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "agentweave.cli",
        "proxy",
        "start",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if endpoint:
        command.extend(["--endpoint", endpoint])
    if agent_id:
        command.extend(["--agent-id", agent_id])
    if capture_prompts:
        command.append("--capture-prompts")
    if auth_token:
        command.extend(["--auth-token", auth_token])

    env = os.environ.copy()
    stdout = path.open("ab")
    creationflags = 0
    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            env=env,
            close_fds=os.name != "nt",
            creationflags=creationflags,
            **popen_kwargs,
        )
    finally:
        stdout.close()

    state = ProxyState(
        pid=process.pid,
        host=host,
        port=port,
        url=f"http://localhost:{port}",
        command=command,
        log_file=str(path),
        started_at=time.time(),
    )
    write_state(state)
    return state


def stop_proxy_process(*, timeout_seconds: float = 5.0) -> tuple[str, ProxyState | None]:
    """Stop the CLI-managed proxy if it is still running."""

    status, state = current_status()
    if not state:
        return "stopped", None
    if status == "stale":
        clear_state()
        return "stale", state

    try:
        os.kill(state.pid, signal.SIGTERM)
    except ProcessLookupError:
        clear_state()
        return "stale", state

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not is_process_running(state.pid):
            clear_state()
            return "stopped", state
        time.sleep(0.1)

    if os.name != "nt":
        try:
            os.kill(state.pid, signal.SIGKILL)
        except ProcessLookupError:
            clear_state()
            return "stopped", state

    clear_state()
    return "killed", state
