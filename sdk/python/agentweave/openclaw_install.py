"""Install/register the AgentWeave OpenClaw bridge plugin from the wheel.

All logic here is filesystem/JSON only and side-effect-explicit so it is unit
testable without invoking OpenClaw. The Typer command in cli.py is a thin wrapper.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Mapping

from agentweave.doctor import _openclaw_config_path

BRIDGE_ENTRY_KEY = "agentweave-bridge"
DEFAULT_PLUGIN_DIRNAME = "agentweave-bridge"
PACKAGED_DIST_RESOURCE = "openclaw_bridge_dist"
BUNDLE_FILES = ("index.js", "openclaw.plugin.json", "package.json")

# config-key -> env var that supplies its default
CONFIG_ENV_KEYS = {
    "proxyUrl": "AGENTWEAVE_PROXY_URL",
    "otlpEndpoint": "AGENTWEAVE_OTLP_ENDPOINT",
    "agentId": "AGENTWEAVE_AGENT_ID",
    "project": "AGENTWEAVE_PROJECT",
}


class OpenClawInstallError(Exception):
    """Raised for user-actionable install/uninstall failures."""


@dataclass
class InstallResult:
    config_path: Path
    plugin_dir: Path | None = None
    backup_path: Path | None = None
    created_entry: bool = False
    removed_entry: bool = False
    actions: list[str] = field(default_factory=list)


def resolve_packaged_dist() -> Traversable:
    """Return the packaged bundle dir, or raise if it was not shipped."""
    dist = resources.files("agentweave").joinpath(PACKAGED_DIST_RESOURCE)
    missing = [name for name in BUNDLE_FILES if not dist.joinpath(name).is_file()]
    if missing:
        raise OpenClawInstallError(
            "Bundled OpenClaw bridge is missing from this install "
            f"(absent: {', '.join(missing)}). For a source/editable checkout run "
            "`bash scripts/build-openclaw-bridge-dist.sh` first."
        )
    return dist


def resolve_config_path(env: Mapping[str, str], explicit: str | None) -> Path:
    explicit_clean = explicit.strip() if explicit else ""
    if explicit_clean:
        return Path(explicit_clean).expanduser()
    path = _openclaw_config_path(env)
    if path is None:
        raise OpenClawInstallError(
            "Could not determine the OpenClaw config path. Pass --openclaw-config "
            "or set OPENCLAW_CONFIG_PATH."
        )
    return path


def default_plugin_dir(config_path: Path) -> Path:
    return config_path.parent / "user-plugins" / DEFAULT_PLUGIN_DIRNAME


def resolve_config_values(
    env: Mapping[str, str],
    *,
    proxy_url: str | None,
    otlp_endpoint: str | None,
    agent_id: str | None,
    project: str | None,
) -> dict[str, str]:
    """Flag value wins, else env value, else the key is omitted entirely."""
    overrides = {
        "proxyUrl": proxy_url,
        "otlpEndpoint": otlp_endpoint,
        "agentId": agent_id,
        "project": project,
    }
    values: dict[str, str] = {}
    for key, env_name in CONFIG_ENV_KEYS.items():
        chosen = overrides[key] if overrides[key] is not None else env.get(env_name)
        if chosen:
            values[key] = chosen
    return values
