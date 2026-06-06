# OpenClaw Bridge Install CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `agentweave openclaw install` — a one-command installer that drops a self-contained bridge bundle into OpenClaw's plugin dir and registers it in `openclaw.json`, working on any host with only `pip install agentweave`.

**Architecture:** (1) esbuild bundles the TS plugin + its OTel deps into one self-contained `bundle/index.js`; (2) a stage script copies that bundle + manifest + a minimal `package.json` into `sdk/python/agentweave/openclaw_bridge_dist/`, shipped as wheel package-data; (3) a new `agentweave openclaw` Typer command copies those files to `~/.openclaw/user-plugins/agentweave-bridge` and surgically merges a `plugins.entries["agentweave-bridge"]` entry into `openclaw.json`, reusing `doctor` helpers for config discovery and post-install verification.

**Tech Stack:** TypeScript + esbuild (plugin), Python 3.11 + Typer + hatchling (CLI/packaging), pytest (tests), GitHub Actions (publish).

**Spec:** `docs/superpowers/specs/2026-06-05-openclaw-bridge-install-cli-design.md`

**Branch:** `feat/issue-186-openclaw-install` (already created from `origin/main`).

**Important — do not touch unrelated WIP:** The working tree has uncommitted Langfuse-input-preview changes in `plugins/openclaw-agentweave-bridge/src/service.ts`, `src/service.test.ts`, and `test/stubs/...`. This plan never modifies those files. Always `git add` exact paths — never `git add -A`.

---

## File Structure

**Plugin (`plugins/openclaw-agentweave-bridge/`)**
- Modify `package.json` — add `esbuild` devDep + `build:bundle` / `verify:bundle` scripts.
- Create `scripts/verify-bundle.mjs` — asserts the bundle loads and is self-contained.
- Modify `.gitignore` (create) — ignore `bundle/`.

**Staging (repo root)**
- Create `scripts/build-openclaw-bridge-dist.sh` — build bundle + copy the 3 artifacts into the Python package-data dir.

**Python package (`sdk/python/`)**
- Create `agentweave/openclaw_install.py` — all install/uninstall logic (pure, testable).
- Modify `agentweave/cli.py` — add `openclaw_app` sub-Typer with `install` / `uninstall`.
- Modify `pyproject.toml` — include `openclaw_bridge_dist` as wheel artifacts.
- Create `tests/test_openclaw_install.py` — module + CLI tests.
- Generated (gitignored, build-time): `agentweave/openclaw_bridge_dist/{index.js,openclaw.plugin.json,package.json}`.

**CI / docs**
- Modify `.github/workflows/publish.yml` — build+stage the bundle before the pypi build; add a preflight.
- Modify `plugins/openclaw-agentweave-bridge/INSTALL.md` and `README.md` — document the one-command install.

**Repo gitignore**
- Modify root `.gitignore` — ignore `sdk/python/agentweave/openclaw_bridge_dist/`.

---

## Task 1: Self-contained plugin bundle (esbuild + verify)

**Files:**
- Modify: `plugins/openclaw-agentweave-bridge/package.json`
- Create: `plugins/openclaw-agentweave-bridge/scripts/verify-bundle.mjs`
- Create: `plugins/openclaw-agentweave-bridge/.gitignore`

All commands in this task run from `plugins/openclaw-agentweave-bridge/`.

- [ ] **Step 1: Write the verify-bundle smoke check (the test, written first)**

Create `plugins/openclaw-agentweave-bridge/scripts/verify-bundle.mjs`:

```js
// Smoke-checks the esbuild output: it must load as a module, export the
// OpenClaw plugin shape, and contain no un-inlined @opentelemetry imports.
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import path from "node:path"

const here = path.dirname(fileURLToPath(import.meta.url))
const bundlePath = path.resolve(here, "..", "bundle", "index.js")

const mod = await import(bundlePath)
const plugin = mod.default
if (!plugin || plugin.id !== "agentweave-bridge") {
  throw new Error(`bundle default export is not the bridge plugin: ${JSON.stringify(plugin?.id)}`)
}
if (typeof plugin.register !== "function") {
  throw new Error("bundle plugin.register is not a function")
}

const source = readFileSync(bundlePath, "utf8")
const leak = source.match(/(?:require\(|from\s*)["']@opentelemetry\//)
if (leak) {
  throw new Error(`bundle is not self-contained — found external @opentelemetry reference: ${leak[0]}`)
}

console.log("verify-bundle: OK (loads, exports bridge plugin, self-contained)")
```

- [ ] **Step 2: Add esbuild dep and scripts to `package.json`**

In `plugins/openclaw-agentweave-bridge/package.json`, replace the `"scripts"` block and add esbuild under `devDependencies`:

```json
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest",
    "build": "tsc",
    "build:bundle": "esbuild index.ts --bundle --platform=node --format=esm --target=node20 --outfile=bundle/index.js",
    "verify:bundle": "node scripts/verify-bundle.mjs"
  },
```

In the same file's `devDependencies`, add (keep existing entries):

```json
    "esbuild": "^0.24.0",
```

- [ ] **Step 3: Create `.gitignore` for build output**

Create `plugins/openclaw-agentweave-bridge/.gitignore`:

```
node_modules/
dist/
bundle/
```

- [ ] **Step 4: Install deps and run the verify — expect it to FAIL (no bundle yet)**

Run:
```bash
npm install
npm run verify:bundle
```
Expected: FAIL — `Cannot find module .../bundle/index.js` (the bundle has not been built).

- [ ] **Step 5: Build the bundle, then verify — expect PASS**

Run:
```bash
npm run build:bundle
npm run verify:bundle
```
Expected: `verify-bundle: OK (loads, exports bridge plugin, self-contained)`.

If `--format=esm` fails the self-contained or load check due to the protobuf exporter (see spec Risks), fall back to:
`esbuild index.ts --bundle --platform=node --format=cjs --target=node20 --outfile=bundle/index.js` and rename the artifact handling in Task 2 to `index.cjs` with the staged `package.json` pointing `openclaw.extensions` at `./index.cjs`. Re-run Step 5 until it passes.

- [ ] **Step 6: Commit**

```bash
git add plugins/openclaw-agentweave-bridge/package.json \
        plugins/openclaw-agentweave-bridge/package-lock.json \
        plugins/openclaw-agentweave-bridge/scripts/verify-bundle.mjs \
        plugins/openclaw-agentweave-bridge/.gitignore
git commit -m "build(bridge): self-contained esbuild bundle + verify (#186)"
```

---

## Task 2: Stage script — copy bundle artifacts into the Python package

**Files:**
- Create: `scripts/build-openclaw-bridge-dist.sh`
- Modify: root `.gitignore`

- [ ] **Step 1: Write the stage script**

Create `scripts/build-openclaw-bridge-dist.sh`:

```bash
#!/usr/bin/env bash
# Build the self-contained OpenClaw bridge bundle and stage the three runtime
# artifacts into the agentweave Python package so they ship in the wheel.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
plugin_dir="$repo_root/plugins/openclaw-agentweave-bridge"
dist_dir="$repo_root/sdk/python/agentweave/openclaw_bridge_dist"

echo "==> Building bundle in $plugin_dir"
( cd "$plugin_dir" && npm ci && npm run build:bundle && npm run verify:bundle )

echo "==> Staging artifacts into $dist_dir"
mkdir -p "$dist_dir"
cp "$plugin_dir/bundle/index.js" "$dist_dir/index.js"
cp "$plugin_dir/openclaw.plugin.json" "$dist_dir/openclaw.plugin.json"

cat > "$dist_dir/package.json" <<'JSON'
{
  "name": "agentweave-bridge",
  "private": true,
  "type": "module",
  "description": "AgentWeave bridge plugin (prebuilt self-contained bundle)",
  "openclaw": {
    "extensions": ["./index.js"]
  }
}
JSON

echo "==> Staged:"
ls -1 "$dist_dir"
```

- [ ] **Step 2: Make it executable and ignore the generated dist dir**

Run:
```bash
chmod +x scripts/build-openclaw-bridge-dist.sh
```

Append to root `.gitignore`:
```
# Build-time staged OpenClaw bridge bundle (generated by scripts/build-openclaw-bridge-dist.sh)
sdk/python/agentweave/openclaw_bridge_dist/
```

- [ ] **Step 3: Run the stage script — expect 3 files staged**

Run:
```bash
bash scripts/build-openclaw-bridge-dist.sh
```
Expected tail output:
```
==> Staged:
index.js
openclaw.plugin.json
package.json
```

- [ ] **Step 4: Commit (script + gitignore only; the staged dir is gitignored)**

```bash
git add scripts/build-openclaw-bridge-dist.sh .gitignore
git commit -m "build(bridge): stage bundle artifacts into python package (#186)"
```

---

## Task 3: Ship the staged bundle in the wheel (hatchling artifacts)

**Files:**
- Modify: `sdk/python/pyproject.toml`

- [ ] **Step 1: Add the artifacts include to the wheel target**

In `sdk/python/pyproject.toml`, replace the `[tool.hatch.build.targets.wheel]` block with:

```toml
[tool.hatch.build.targets.wheel]
packages = ["agentweave"]
artifacts = [
    "agentweave/openclaw_bridge_dist/index.js",
    "agentweave/openclaw_bridge_dist/openclaw.plugin.json",
    "agentweave/openclaw_bridge_dist/package.json",
]
```

(`artifacts` forces hatchling to include files that are otherwise gitignored.)

- [ ] **Step 2: Build the wheel and confirm the bundle is inside**

Run (the staged dir must already exist from Task 2 Step 3):
```bash
cd sdk/python && cp ../../README.md . && python -m build --wheel 2>/dev/null | tail -2
python - <<'PY'
import glob, zipfile
whl = sorted(glob.glob("dist/agentweave_sdk-*.whl"))[-1]
names = zipfile.ZipFile(whl).namelist()
want = [
    "agentweave/openclaw_bridge_dist/index.js",
    "agentweave/openclaw_bridge_dist/openclaw.plugin.json",
    "agentweave/openclaw_bridge_dist/package.json",
]
missing = [w for w in want if w not in names]
assert not missing, f"missing from wheel: {missing}"
print("wheel contains all 3 bridge artifacts:", whl)
PY
```
Expected: `wheel contains all 3 bridge artifacts: dist/agentweave_sdk-0.3.1-py3-none-any.whl`.

- [ ] **Step 3: Commit**

```bash
git add sdk/python/pyproject.toml
git commit -m "build(sdk-py): ship openclaw bridge bundle as wheel artifact (#186)"
```

---

## Task 4: Install module — packaged-bundle + config-path resolution

**Files:**
- Create: `sdk/python/agentweave/openclaw_install.py`
- Test: `sdk/python/tests/test_openclaw_install.py`

All `pytest` commands run from `sdk/python/`.

- [ ] **Step 1: Write failing tests for resolution helpers**

Create `sdk/python/tests/test_openclaw_install.py`:

```python
"""Tests for agentweave openclaw install/uninstall."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("typer", reason="CLI deps not installed")


def test_default_plugin_dir_is_user_plugins_sibling():
    from agentweave.openclaw_install import default_plugin_dir

    config_path = Path("/home/x/.openclaw/openclaw.json")
    assert default_plugin_dir(config_path) == Path(
        "/home/x/.openclaw/user-plugins/agentweave-bridge"
    )


def test_resolve_config_values_precedence_flag_over_env():
    from agentweave.openclaw_install import resolve_config_values

    env = {
        "AGENTWEAVE_PROXY_URL": "http://env-proxy:4000",
        "AGENTWEAVE_OTLP_ENDPOINT": "http://env-otlp:4318",
        "AGENTWEAVE_AGENT_ID": "env-agent",
        "AGENTWEAVE_PROJECT": "env-project",
    }
    values = resolve_config_values(
        env,
        proxy_url="http://flag-proxy:4000",
        otlp_endpoint=None,
        agent_id=None,
        project=None,
    )
    assert values == {
        "proxyUrl": "http://flag-proxy:4000",
        "otlpEndpoint": "http://env-otlp:4318",
        "agentId": "env-agent",
        "project": "env-project",
    }


def test_resolve_config_values_omits_unset_keys():
    from agentweave.openclaw_install import resolve_config_values

    values = resolve_config_values(
        {}, proxy_url=None, otlp_endpoint=None, agent_id=None, project=None
    )
    assert values == {}
```

- [ ] **Step 2: Run tests — expect FAIL (module missing)**

Run: `pytest tests/test_openclaw_install.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentweave.openclaw_install'`.

- [ ] **Step 3: Create the module with resolution helpers**

Create `sdk/python/agentweave/openclaw_install.py`:

```python
"""Install/register the AgentWeave OpenClaw bridge plugin from the wheel.

All logic here is filesystem/JSON only and side-effect-explicit so it is unit
testable without invoking OpenClaw. The Typer command in cli.py is a thin wrapper.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from importlib import resources
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
    plugin_dir: Path | None
    backup_path: Path | None = None
    created_entry: bool = False
    removed_entry: bool = False
    actions: list[str] = field(default_factory=list)


def resolve_packaged_dist() -> resources.abc.Traversable:
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
    if explicit:
        return Path(explicit).expanduser()
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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_openclaw_install.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add sdk/python/agentweave/openclaw_install.py sdk/python/tests/test_openclaw_install.py
git commit -m "feat(sdk-py): openclaw_install resolution helpers (#186)"
```

---

## Task 5: Install logic — copy files + merge openclaw.json entry

**Files:**
- Modify: `sdk/python/agentweave/openclaw_install.py`
- Test: `sdk/python/tests/test_openclaw_install.py`

- [ ] **Step 1: Write failing tests for `install()`**

Append to `sdk/python/tests/test_openclaw_install.py`:

```python
def _write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_install_writes_files_and_entry(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    _write_config(config_path, {"plugins": {"entries": {}}})

    # Fake the packaged dist so the test does not depend on a built wheel.
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text(f"// {name}", encoding="utf-8")

    result = oi.install(
        {},
        dist_dir=dist,
        config_path=str(config_path),
        proxy_url="http://p:4000",
        otlp_endpoint="http://o:4318",
        agent_id="host-a",
        project="proj-a",
    )

    assert result.created_entry is True
    plugin_dir = result.plugin_dir
    assert (plugin_dir / "index.js").exists()
    assert (plugin_dir / "openclaw.plugin.json").exists()
    assert (plugin_dir / "package.json").exists()

    config = json.loads(config_path.read_text())
    entry = config["plugins"]["entries"]["agentweave-bridge"]
    assert entry["path"] == str(plugin_dir)
    assert entry["config"] == {
        "proxyUrl": "http://p:4000",
        "otlpEndpoint": "http://o:4318",
        "agentId": "host-a",
        "project": "proj-a",
        "enabled": True,
    }


def test_install_is_idempotent_and_backs_up(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / "openclaw.json"
    _write_config(config_path, {"plugins": {"entries": {}}, "other": "keep"})
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")

    oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="a")
    result2 = oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="a")

    config = json.loads(config_path.read_text())
    assert config["other"] == "keep"
    assert list(config["plugins"]["entries"].keys()) == ["agentweave-bridge"]
    assert result2.created_entry is False
    assert (config_path.parent / "openclaw.json.bak").exists()


def test_install_preserves_user_config_unless_force(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / "openclaw.json"
    _write_config(
        config_path,
        {
            "plugins": {
                "entries": {
                    "agentweave-bridge": {
                        "path": "/old",
                        "config": {"agentId": "hand-set", "extra": "keep"},
                    }
                }
            }
        },
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")

    # Without --force: existing agentId preserved, extra preserved, path updated.
    oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="new")
    entry = json.loads(config_path.read_text())["plugins"]["entries"]["agentweave-bridge"]
    assert entry["config"]["agentId"] == "hand-set"
    assert entry["config"]["extra"] == "keep"
    assert entry["path"] != "/old"

    # With force: agentId overwritten, extra still preserved.
    oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="new", force=True)
    entry = json.loads(config_path.read_text())["plugins"]["entries"]["agentweave-bridge"]
    assert entry["config"]["agentId"] == "new"
    assert entry["config"]["extra"] == "keep"


def test_install_missing_config_errors(tmp_path):
    from agentweave import openclaw_install as oi

    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")

    with pytest.raises(oi.OpenClawInstallError):
        oi.install(
            {}, dist_dir=dist, config_path=str(tmp_path / "nope.json"), agent_id="a"
        )
```

- [ ] **Step 2: Run tests — expect FAIL (`install` not defined)**

Run: `pytest tests/test_openclaw_install.py -q`
Expected: FAIL — `AttributeError: module 'agentweave.openclaw_install' has no attribute 'install'`.

- [ ] **Step 3: Implement `install()` and its file/JSON helpers**

Append to `sdk/python/agentweave/openclaw_install.py`:

```python
def copy_bundle(dist_dir, plugin_dir: Path) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for name in BUNDLE_FILES:
        src = dist_dir.joinpath(name) if hasattr(dist_dir, "joinpath") else Path(dist_dir) / name
        with resources.as_file(src) as real:
            shutil.copyfile(real, plugin_dir / name)


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
    """Back up, then write atomically. Returns the backup path."""
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copyfile(config_path, backup_path)
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        tmp.replace(config_path)
    except OSError:
        shutil.copyfile(backup_path, config_path)
        raise
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

    entries[BRIDGE_ENTRY_KEY] = {"path": str(target_dir), "config": merged_config}

    backup = _write_config_atomic(resolved_config, config)
    return InstallResult(
        config_path=resolved_config,
        plugin_dir=target_dir,
        backup_path=backup,
        created_entry=created,
        actions=[f"{'created' if created else 'updated'} plugins.entries.{BRIDGE_ENTRY_KEY}"],
    )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_openclaw_install.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add sdk/python/agentweave/openclaw_install.py sdk/python/tests/test_openclaw_install.py
git commit -m "feat(sdk-py): openclaw bridge install() with merge + backup (#186)"
```

---

## Task 6: Uninstall logic

**Files:**
- Modify: `sdk/python/agentweave/openclaw_install.py`
- Test: `sdk/python/tests/test_openclaw_install.py`

- [ ] **Step 1: Write failing tests for `uninstall()`**

Append to `sdk/python/tests/test_openclaw_install.py`:

```python
def test_uninstall_removes_entry_and_optionally_purges(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / "openclaw.json"
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")
    _write_config(config_path, {"plugins": {"entries": {}}, "keep": 1})

    installed = oi.install({}, dist_dir=dist, config_path=str(config_path), agent_id="a")
    plugin_dir = installed.plugin_dir

    result = oi.uninstall({}, config_path=str(config_path), purge=True)

    config = json.loads(config_path.read_text())
    assert "agentweave-bridge" not in config["plugins"]["entries"]
    assert config["keep"] == 1
    assert result.removed_entry is True
    assert not plugin_dir.exists()  # purged


def test_uninstall_missing_entry_is_noop(tmp_path):
    from agentweave import openclaw_install as oi

    config_path = tmp_path / "openclaw.json"
    _write_config(config_path, {"plugins": {"entries": {}}})

    result = oi.uninstall({}, config_path=str(config_path))
    assert result.removed_entry is False
```

- [ ] **Step 2: Run tests — expect FAIL (`uninstall` not defined)**

Run: `pytest tests/test_openclaw_install.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'uninstall'`.

- [ ] **Step 3: Implement `uninstall()`**

Append to `sdk/python/agentweave/openclaw_install.py`:

```python
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

    backup = _write_config_atomic(resolved_config, config)

    plugin_dir = None
    if purge and isinstance(entry, dict) and isinstance(entry.get("path"), str):
        plugin_dir = Path(entry["path"]).expanduser()
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)

    return InstallResult(
        config_path=resolved_config,
        plugin_dir=plugin_dir,
        backup_path=backup,
        removed_entry=True,
        actions=[f"removed plugins.entries.{BRIDGE_ENTRY_KEY}"],
    )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_openclaw_install.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add sdk/python/agentweave/openclaw_install.py sdk/python/tests/test_openclaw_install.py
git commit -m "feat(sdk-py): openclaw bridge uninstall (#186)"
```

---

## Task 7: CLI wiring — `agentweave openclaw install|uninstall`

**Files:**
- Modify: `sdk/python/agentweave/cli.py`
- Test: `sdk/python/tests/test_openclaw_install.py`

- [ ] **Step 1: Write failing CLI tests**

Append to `sdk/python/tests/test_openclaw_install.py`:

```python
def test_cli_install_reports_and_writes_entry(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from agentweave import openclaw_install as oi
    from agentweave.cli import app

    config_path = tmp_path / "openclaw.json"
    _write_config(config_path, {"plugins": {"entries": {}}})
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(oi, "resolve_packaged_dist", lambda: dist)

    result = CliRunner().invoke(
        app,
        [
            "openclaw", "install",
            "--openclaw-config", str(config_path),
            "--agent-id", "cli-host",
            "--no-restart",
        ],
    )
    assert result.exit_code == 0, result.output
    entry = json.loads(config_path.read_text())["plugins"]["entries"]["agentweave-bridge"]
    assert entry["config"]["agentId"] == "cli-host"
    assert "openclaw gateway restart" in result.output


def test_cli_install_missing_config_exits_nonzero(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from agentweave import openclaw_install as oi
    from agentweave.cli import app

    dist = tmp_path / "dist"
    dist.mkdir()
    for name in oi.BUNDLE_FILES:
        (dist / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(oi, "resolve_packaged_dist", lambda: dist)

    result = CliRunner().invoke(
        app, ["openclaw", "install", "--openclaw-config", str(tmp_path / "nope.json")]
    )
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_cli_uninstall_noop_when_absent(tmp_path):
    from typer.testing import CliRunner
    from agentweave.cli import app

    config_path = tmp_path / "openclaw.json"
    _write_config(config_path, {"plugins": {"entries": {}}})

    result = CliRunner().invoke(
        app, ["openclaw", "uninstall", "--openclaw-config", str(config_path)]
    )
    assert result.exit_code == 0, result.output
```

- [ ] **Step 2: Run tests — expect FAIL (no `openclaw` command)**

Run: `pytest tests/test_openclaw_install.py -k cli -q`
Expected: FAIL — exit code 2 / "No such command 'openclaw'".

- [ ] **Step 3: Add the `openclaw` sub-Typer to `cli.py`**

In `sdk/python/agentweave/cli.py`, after the existing sub-Typer registration block (the lines `app.add_typer(trace_app)` / `app.add_typer(proxy_app)` / `app.add_typer(hooks_app)`), add:

```python
openclaw_app = typer.Typer(name="openclaw", help="Install the AgentWeave bridge into OpenClaw.")
app.add_typer(openclaw_app)
```

Then add these two commands (place near the end of the file, before the `version` command):

```python
@openclaw_app.command("install")
def openclaw_install_cmd(
    proxy_url: Optional[str] = typer.Option(None, "--proxy-url", help="AgentWeave proxy URL injected into sub-agent provider env vars."),
    otlp_endpoint: Optional[str] = typer.Option(None, "--otlp-endpoint", help="OTLP HTTP endpoint for trace export."),
    agent_id: Optional[str] = typer.Option(None, "--agent-id", help="Agent identifier stamped on this host's spans."),
    project: Optional[str] = typer.Option(None, "--project", help="Project tag for dashboard filtering."),
    openclaw_config: Optional[str] = typer.Option(None, "--openclaw-config", help="Path to openclaw.json (else OPENCLAW_CONFIG_PATH / ~/.openclaw/openclaw.json)."),
    path: Optional[str] = typer.Option(None, "--path", help="Plugin install dir (default: <openclaw>/user-plugins/agentweave-bridge)."),
    enabled: bool = typer.Option(True, "--enable/--no-enable", help="Set the plugin entry enabled flag."),
    restart: bool = typer.Option(False, "--restart/--no-restart", help="Run `openclaw gateway restart` after install."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing bridge config values."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Install and register the AgentWeave bridge plugin in OpenClaw."""
    import os
    import subprocess

    from agentweave.doctor import _check_openclaw_bridge
    from agentweave.openclaw_install import OpenClawInstallError, install

    try:
        result = install(
            os.environ,
            config_path=openclaw_config,
            plugin_dir=path,
            proxy_url=proxy_url,
            otlp_endpoint=otlp_endpoint,
            agent_id=agent_id,
            project=project,
            enabled=enabled,
            force=force,
        )
    except OpenClawInstallError as exc:
        if as_json:
            console.print_json(json.dumps({"ok": False, "error": str(exc)}))
        else:
            console.print(f"[red]Install failed:[/red] {exc}")
        raise typer.Exit(code=1)

    verify = _check_openclaw_bridge({**os.environ, "OPENCLAW_CONFIG_PATH": str(result.config_path)})

    if as_json:
        console.print_json(json.dumps({
            "ok": True,
            "config_path": str(result.config_path),
            "plugin_dir": str(result.plugin_dir),
            "created_entry": result.created_entry,
            "backup_path": str(result.backup_path) if result.backup_path else None,
            "doctor": verify.to_dict(),
        }))
    else:
        verb = "Installed" if result.created_entry else "Updated"
        console.print(f"[green]{verb}[/green] agentweave-bridge → {result.plugin_dir}")
        console.print(f"Config: {result.config_path} (backup: {result.backup_path})")
        console.print(f"doctor openclaw.bridge: {_doctor_status_markup(verify.status)} — {verify.message}")
        if restart:
            console.print("Restarting OpenClaw gateway…")
            subprocess.run(["openclaw", "gateway", "restart"], check=False)
        else:
            console.print("Next: restart OpenClaw to load the plugin → [bold]openclaw gateway restart[/bold]")


@openclaw_app.command("uninstall")
def openclaw_uninstall_cmd(
    openclaw_config: Optional[str] = typer.Option(None, "--openclaw-config", help="Path to openclaw.json."),
    purge: bool = typer.Option(False, "--purge", help="Also delete the installed plugin files."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Remove the AgentWeave bridge plugin entry from OpenClaw."""
    import os

    from agentweave.openclaw_install import OpenClawInstallError, uninstall

    try:
        result = uninstall(os.environ, config_path=openclaw_config, purge=purge)
    except OpenClawInstallError as exc:
        if as_json:
            console.print_json(json.dumps({"ok": False, "error": str(exc)}))
        else:
            console.print(f"[red]Uninstall failed:[/red] {exc}")
        raise typer.Exit(code=1)

    if as_json:
        console.print_json(json.dumps({
            "ok": True,
            "removed_entry": result.removed_entry,
            "config_path": str(result.config_path),
        }))
    elif result.removed_entry:
        console.print(f"[green]Removed[/green] agentweave-bridge from {result.config_path}")
        console.print("Restart OpenClaw to apply → [bold]openclaw gateway restart[/bold]")
    else:
        console.print("No agentweave-bridge entry found; nothing to do.")
```

- [ ] **Step 4: Run the CLI tests — expect PASS**

Run: `pytest tests/test_openclaw_install.py -q`
Expected: 12 passed.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `pytest -q`
Expected: all pass (prior suite count + the new openclaw tests).

- [ ] **Step 6: Commit**

```bash
git add sdk/python/agentweave/cli.py sdk/python/tests/test_openclaw_install.py
git commit -m "feat(cli): agentweave openclaw install/uninstall (#186)"
```

---

## Task 8: CI — build+stage the bundle before pypi build

**Files:**
- Modify: `.github/workflows/publish.yml`

- [ ] **Step 1: Stage the bundle in the `publish-pypi` job**

In `.github/workflows/publish.yml`, in the `publish-pypi` job, add a Node setup + bundle-stage step **before** the `Build package` step:

```yaml
      - name: Set up Node.js
        uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Build and stage OpenClaw bridge bundle
        run: bash scripts/build-openclaw-bridge-dist.sh
```

- [ ] **Step 2: Add a bundle preflight to the `preflights` job**

In the `preflights` job's steps, append:

```yaml
      - name: Validate OpenClaw bridge bundle is self-contained
        run: |
          bash scripts/build-openclaw-bridge-dist.sh
          test -f sdk/python/agentweave/openclaw_bridge_dist/index.js
          test -f sdk/python/agentweave/openclaw_bridge_dist/openclaw.plugin.json
          test -f sdk/python/agentweave/openclaw_bridge_dist/package.json
```

- [ ] **Step 3: Lint the workflow YAML locally**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/publish.yml')); print('publish.yml is valid YAML')"
```
Expected: `publish.yml is valid YAML`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/publish.yml
git commit -m "ci: build+stage openclaw bridge bundle before pypi build (#186)"
```

---

## Task 9: Docs — one-command install

**Files:**
- Modify: `plugins/openclaw-agentweave-bridge/INSTALL.md`
- Modify: `README.md`

- [ ] **Step 1: Add the one-command path to `INSTALL.md`**

In `plugins/openclaw-agentweave-bridge/INSTALL.md`, immediately under the top-level intro paragraph (before `## Prerequisites`), insert:

```markdown
## Quick install (recommended)

On any host with the AgentWeave CLI:

```bash
pip install agentweave
agentweave openclaw install \
  --proxy-url http://localhost:4000 \
  --otlp-endpoint http://localhost:4318 \
  --agent-id "$(hostname)" \
  --project my-project
openclaw gateway restart
agentweave doctor          # openclaw.bridge should PASS
```

This copies a prebuilt, self-contained bundle into
`~/.openclaw/user-plugins/agentweave-bridge` and registers it in
`~/.openclaw/openclaw.json`. Re-running is safe (idempotent); hand-edited config
values are preserved unless you pass `--force`. Remove with
`agentweave openclaw uninstall --purge`.

The manual steps below remain available for custom layouts.
```

- [ ] **Step 2: Point the README bridge section at the one-command install**

In `README.md`, the bridge link lives in a Markdown **table** (the row
`| OpenClaw bridge install | [plugins/openclaw-agentweave-bridge/INSTALL.md](...) |`,
around line 430). Do **not** insert inside the table. Instead, find the blank
line that terminates that table and insert this block immediately after it (a
new paragraph following the table):

```markdown
Install the OpenClaw bridge on any host with one command:

​```bash
agentweave openclaw install --proxy-url <proxy> --otlp-endpoint <otlp> --agent-id "$(hostname)"
​```
```

(Remove the zero-width spaces before the inner ` ```bash ` fences — they are only
here so this plan's outer fence does not close early.)

- [ ] **Step 3: Verify the doc links/markdown render (no broken fences)**

Run:
```bash
grep -n "agentweave openclaw install" plugins/openclaw-agentweave-bridge/INSTALL.md README.md
```
Expected: at least one match in each file.

- [ ] **Step 4: Commit**

```bash
git add plugins/openclaw-agentweave-bridge/INSTALL.md README.md
git commit -m "docs(bridge): document one-command openclaw install (#186)"
```

---

## Task 10: Final verification & PR

- [ ] **Step 1: Full Python suite**

Run: `cd sdk/python && pytest -q`
Expected: all pass.

- [ ] **Step 2: Plugin bundle + tests**

Run: `cd plugins/openclaw-agentweave-bridge && npm run build:bundle && npm run verify:bundle && npm test`
Expected: bundle verify OK; vitest passes (existing `src/**/*.test.ts`, including the unrelated WIP tests if present).

- [ ] **Step 3: End-to-end dry run against a scratch config**

Run:
```bash
cd sdk/python && pip install -e . >/dev/null
tmp=$(mktemp -d); printf '{"plugins":{"entries":{}}}' > "$tmp/openclaw.json"
bash ../../scripts/build-openclaw-bridge-dist.sh >/dev/null
agentweave openclaw install --openclaw-config "$tmp/openclaw.json" --agent-id e2e --no-restart
cat "$tmp/openclaw.json"
agentweave openclaw uninstall --openclaw-config "$tmp/openclaw.json" --purge
```
Expected: the entry appears with `path` + `config.agentId = "e2e"` after install, and is gone after uninstall.

- [ ] **Step 4: Confirm the unrelated WIP was never committed**

Run: `git log --oneline origin/main..HEAD && git diff --stat origin/main..HEAD -- plugins/openclaw-agentweave-bridge/src/service.ts`
Expected: the commit list is only the #186 commits; the `service.ts` diff is empty (WIP untouched and still uncommitted in the working tree).

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin feat/issue-186-openclaw-install
gh pr create --base main --title "feat(cli): one-command OpenClaw bridge install (#186)" \
  --body "Implements the agentweave openclaw install/uninstall command per docs/superpowers/specs/2026-06-05-openclaw-bridge-install-cli-design.md. Closes part of #186 (full bridge distribution).

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Self-Review Notes

- **Spec coverage:** bundling (T1), wheel package-data (T2/T3), CLI install+uninstall with merge/backup/idempotency (T4–T7), config discovery via doctor reuse (T4), post-install doctor verification (T7), CI staging+preflight (T8), docs (T9), e2e verification (T10). All spec sections mapped.
- **Type consistency:** `InstallResult`, `OpenClawInstallError`, `BUNDLE_FILES`, `BRIDGE_ENTRY_KEY`, `install()`/`uninstall()`/`resolve_config_values()`/`default_plugin_dir()`/`resolve_packaged_dist()` signatures are defined in T4–T6 and used unchanged in T7 tests and CLI.
- **WIP isolation:** every commit uses explicit paths; T10 Step 4 asserts the Langfuse WIP was never swept in.
- **Risk fallback:** T1 Step 5 documents the esbuild CJS fallback from the spec Risks section.
