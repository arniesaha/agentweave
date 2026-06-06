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


def copy_bundle(dist_dir, plugin_dir: Path) -> None:
    try:
        plugin_dir.mkdir(parents=True, exist_ok=True)
        for name in BUNDLE_FILES:
            src = dist_dir.joinpath(name) if hasattr(dist_dir, "joinpath") else Path(dist_dir) / name
            if isinstance(src, Path):
                shutil.copyfile(src, plugin_dir / name)
            else:
                with resources.as_file(src) as real:
                    shutil.copyfile(real, plugin_dir / name)
    except OSError as exc:
        raise OpenClawInstallError(
            f"Could not write plugin files to {plugin_dir}: {exc}."
        ) from exc


def _read_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise OpenClawInstallError(
            f"OpenClaw config not found at {config_path}. Install OpenClaw or pass "
            "--openclaw-config."
        )
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OpenClawInstallError(
            f"OpenClaw config at {config_path} is not valid JSON: {exc.msg}."
        ) from exc
    if not isinstance(data, dict):
        raise OpenClawInstallError(f"OpenClaw config at {config_path} is not a JSON object.")
    return data


def _write_config_atomic(config_path: Path, config: dict) -> Path:
    """Back up, then write atomically. Returns the backup path.

    Note: the ``.bak`` reflects the config as it was at the start of THIS call,
    not necessarily the original pre-install config across repeated runs.
    """
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    try:
        shutil.copyfile(config_path, backup_path)
    except OSError as exc:
        raise OpenClawInstallError(
            f"Could not create a backup of {config_path}: {exc}."
        ) from exc
    try:
        tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        tmp.replace(config_path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        shutil.copyfile(backup_path, config_path)
        raise OpenClawInstallError(
            f"Failed to write OpenClaw config at {config_path}: {exc}. Restored from backup."
        ) from exc
    return backup_path


def _entries(config: dict) -> dict:
    plugins = config.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        raise OpenClawInstallError("openclaw.json `plugins` is not an object.")
    entries = plugins.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise OpenClawInstallError("openclaw.json `plugins.entries` is not an object.")
    return entries


def install(
    env: Mapping[str, str],
    *,
    config_path: str | None = None,
    plugin_dir: str | None = None,
    dist_dir=None,
    proxy_url: str | None = None,
    otlp_endpoint: str | None = None,
    agent_id: str | None = None,
    project: str | None = None,
    enabled: bool = True,
    force: bool = False,
) -> InstallResult:
    resolved_config = resolve_config_path(env, config_path)
    config = _read_config(resolved_config)  # raises if missing/invalid

    dist = dist_dir if dist_dir is not None else resolve_packaged_dist()
    target_dir = Path(plugin_dir).expanduser() if plugin_dir else default_plugin_dir(resolved_config)

    copy_bundle(dist, target_dir)

    entries = _entries(config)
    existing = entries.get(BRIDGE_ENTRY_KEY)
    created = not isinstance(existing, dict)

    resolved_values = resolve_config_values(
        env,
        proxy_url=proxy_url,
        otlp_endpoint=otlp_endpoint,
        agent_id=agent_id,
        project=project,
    )

    merged_config: dict = {}
    if isinstance(existing, dict) and isinstance(existing.get("config"), dict):
        merged_config.update(existing["config"])
    for key, value in resolved_values.items():
        if force or key not in merged_config:
            merged_config[key] = value
    merged_config["enabled"] = enabled

    # OpenClaw discovers user-plugins by directory name (entry key) and its
    # config schema rejects a per-entry `path` field, so we never write one;
    # strip any pre-existing path to repair an entry that has it.
    existing_entry = existing if isinstance(existing, dict) else {}
    new_entry = {k: v for k, v in existing_entry.items() if k != "path"}
    new_entry["config"] = merged_config
    entries[BRIDGE_ENTRY_KEY] = new_entry

    backup = _write_config_atomic(resolved_config, config)
    return InstallResult(
        config_path=resolved_config,
        plugin_dir=target_dir,
        backup_path=backup,
        created_entry=created,
        actions=[f"{'created' if created else 'updated'} plugins.entries.{BRIDGE_ENTRY_KEY}"],
    )


def uninstall(
    env: Mapping[str, str],
    *,
    config_path: str | None = None,
    purge: bool = False,
) -> InstallResult:
    resolved_config = resolve_config_path(env, config_path)
    config = _read_config(resolved_config)
    entries = _entries(config)

    entry = entries.pop(BRIDGE_ENTRY_KEY, None)
    removed = entry is not None

    if not removed:
        return InstallResult(
            config_path=resolved_config,
            plugin_dir=None,
            removed_entry=False,
            actions=["no agentweave-bridge entry found"],
        )

    # If purging, validate the target BEFORE mutating the config so a dangerous
    # (hand-edited) path aborts the whole operation rather than removing the
    # config entry and then refusing to delete files.
    # Entries no longer carry a `path` field (OpenClaw rejects it); fall back to
    # the default convention.  Legacy entries that still have `path` are honoured
    # so hand-edited configs with a custom location continue to work.
    purge_dir: Path | None = None
    if purge and isinstance(entry, dict):
        entry_path = entry.get("path")
        if isinstance(entry_path, str):
            candidate = Path(entry_path).expanduser()
        else:
            candidate = default_plugin_dir(resolved_config)
        if candidate.name != DEFAULT_PLUGIN_DIRNAME:
            raise OpenClawInstallError(
                f"Refusing to purge {candidate}: expected the plugin directory "
                f"basename to be '{DEFAULT_PLUGIN_DIRNAME}', got '{candidate.name}'. "
                "Remove the directory manually if this was intended."
            )
        purge_dir = candidate

    backup = _write_config_atomic(resolved_config, config)

    plugin_dir = None
    if purge_dir is not None:
        if purge_dir.exists():
            shutil.rmtree(purge_dir)
        plugin_dir = purge_dir

    return InstallResult(
        config_path=resolved_config,
        plugin_dir=plugin_dir,
        backup_path=backup,
        removed_entry=True,
        actions=[f"removed plugins.entries.{BRIDGE_ENTRY_KEY}"],
    )
