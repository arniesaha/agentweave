# Design: `agentweave openclaw install` — one-command bridge distribution

- **Issue:** [#186](https://github.com/arniesaha/agentweave/issues/186) — Distribute agentweave-bridge plugin to all OpenClaw installs in the fleet
- **Date:** 2026-06-05
- **Status:** Approved (design), pending implementation plan
- **Branch:** `feat/issue-186-openclaw-install`

## Problem

Installing the AgentWeave OpenClaw bridge today is a 5-step manual process
(`INSTALL.md`): clone/copy the plugin to a stable path → `npm install` →
hand-edit `~/.openclaw/openclaw.json` with an absolute path and per-machine
`agentId`/`project` → `openclaw gateway restart` → `agentweave doctor`. This
does not scale to "every OpenClaw install we want observed" (#186's coverage
gap: most fleet sessions never reach AgentWeave because the plugin was never
registered on those hosts).

PR #231 already shipped the *detection* half: `agentweave doctor` warns when an
OpenClaw config references the bridge but the plugin path is missing, plus fleet
rollout docs. This spec covers the *distribution* half: a single command that
installs and registers the bridge on any host that has `pip install agentweave`,
with **no repo checkout required**.

## Goals / Non-goals

**Goals**
- `agentweave openclaw install` registers a working bridge in one command.
- Works on a host with only the `agentweave` Python package installed (the
  plugin bundle travels inside the wheel).
- Idempotent and safe to re-run; never silently clobbers user config.
- Result is verifiable by the existing `agentweave doctor` `openclaw.bridge` check.

**Non-goals (YAGNI)**
- Publishing `@agentweave/openclaw-bridge` to npm (deferred).
- A config-sync / seed-template mechanism across hosts.
- Auto-restarting OpenClaw by default (gated behind an opt-in flag).
- Touching the unrelated in-progress Langfuse-input-preview changes currently in
  the working tree (`src/service.ts` et al.) — this work branches off `main` and
  leaves them untouched.

## Decisions (settled during brainstorming)

1. **Mechanism:** an `agentweave` CLI command (not npm publish, not config-sync).
2. **Source:** the plugin is **bundled into the Python wheel** as package-data,
   so the command needs no repo checkout.
3. **Build form:** a **prebuilt, self-contained JS bundle** (esbuild inlines all
   `@opentelemetry/*` deps into one file) — zero host-side npm/build/network at
   install time.

## Architecture

Three layers, each independently testable:

### A. Plugin bundling — `plugins/openclaw-agentweave-bridge/`

- Add **esbuild** as a dev dependency and a `build:bundle` npm script:
  `esbuild index.ts --bundle --platform=node --format=esm --outfile=bundle/index.js`.
- `index.ts` imports OpenClaw only via `import type` (erased at compile), so the
  only runtime deps are the `@opentelemetry/*` packages — all pure JS and safely
  inlinable. The plugin runs its **own** `NodeSDK`/`TracerProvider` and signals
  the proxy through the `AGENTWEAVE_TRACEPARENT` process env var; it does **not**
  depend on sharing OpenClaw's OTel global. A self-contained bundle therefore
  preserves span lineage.
- **Bundle smoke test** (vitest): import `bundle/index.js`, assert
  `default.id === "agentweave-bridge"` and `typeof default.register === "function"`.
  This proves the inlined bundle loads and exports the plugin shape OpenClaw expects.

### B. Packaged artifact — `sdk/python/agentweave/openclaw_bridge_dist/`

The install command drops exactly three files into the target plugin directory.
These three are shipped as wheel package-data:

| File | Source | Purpose |
|---|---|---|
| `index.js` | esbuild `bundle/index.js` | the self-contained plugin bundle |
| `openclaw.plugin.json` | existing plugin manifest | id + `configSchema` + `activation` |
| `package.json` | generated, minimal | `{ "type": "module", "openclaw": { "extensions": ["./index.js"] } }` so OpenClaw loads the bundle |

No `node_modules` is needed at the target — the bundle is self-contained.

`pyproject.toml` includes `openclaw_bridge_dist/*` as package-data so it travels
in the wheel.

### C. CLI command — `sdk/python/agentweave/cli.py` + new `openclaw_install.py`

- New sub-Typer `openclaw_app` (`agentweave openclaw …`), registered alongside
  the existing `trace`/`proxy`/`hooks` sub-Typers, with two commands:
  - `install` — copy the packaged bundle to the plugin dir, then add/update the
    `agentweave-bridge` entry in `openclaw.json`.
  - `uninstall` — remove the entry; `--purge` also deletes the plugin files.
- All non-trivial logic lives in a **testable `openclaw_install.py` module**
  (path resolution, JSON read/merge/write, file copy, packaged-bundle discovery);
  `cli.py` stays a thin Typer wrapper that calls it and renders output.
- **Reuse** `doctor._openclaw_config_path()` and `doctor._find_openclaw_bridge_entry()`
  for config discovery and existing-entry detection, so install and doctor agree
  on where the config is and what counts as "already installed."

## Command surface

```
agentweave openclaw install \
  [--proxy-url URL] [--otlp-endpoint URL] [--agent-id ID] [--project NAME] \
  [--openclaw-config PATH] [--path PLUGIN_DIR] \
  [--enable/--no-enable] [--restart] [--force] [--json]

agentweave openclaw uninstall [--openclaw-config PATH] [--purge] [--json]
```

### Config value precedence
For each of proxy-url / otlp-endpoint / agent-id / project:
`--flag` → env (`AGENTWEAVE_PROXY_URL`, `AGENTWEAVE_OTLP_ENDPOINT`,
`AGENTWEAVE_AGENT_ID`, `AGENTWEAVE_PROJECT`) → leave unset (the plugin applies
its own documented defaults at runtime). These are the same env vars the plugin
already honors, so behavior is consistent whether config is written or inherited.

### Config-file discovery
`--openclaw-config` → `_openclaw_config_path(env)` (which honors
`OPENCLAW_CONFIG_PATH`, then `OPENCLAW_CONFIG`, then `OPENCLAW_HOME`, then
`~/.openclaw/openclaw.json`). If the resolved file does **not** exist, error with
guidance — do not fabricate a partial OpenClaw config.

### Install target path
Default `~/.openclaw/user-plugins/agentweave-bridge` (matches current NAS
layout), overridable with `--path`.

## Behavior

1. Resolve the packaged bundle dir inside the installed wheel
   (`importlib.resources`). If missing (e.g. an editable dev install that never
   ran the bundle build), error clearly pointing at `npm run build:bundle`.
2. Resolve and read `openclaw.json`; parse JSON (refuse to proceed on parse error).
3. Create the target plugin dir; copy the three artifacts in.
4. **Back up** `openclaw.json` → `openclaw.json.bak`, then surgically add/update
   `plugins.entries["agentweave-bridge"]`:
   - always set `path` to the target plugin dir (independent of `--force`);
   - set `config` from resolved values, **merging** over any existing
     user-set config keys (do not drop keys the user added). With `--force`,
     resolved values overwrite existing ones; without it, existing values win for
     keys already present.
   - `enabled` follows `--enable/--no-enable` (default enabled).
   - Preserve every other key in the file and overall structure.
5. Write the file atomically (temp file + replace). On any write failure, restore
   from `.bak`.
6. Restart handling: by default **print** `openclaw gateway restart`; only run it
   when `--restart` is passed.
7. Post-install: run the doctor `openclaw.bridge` check and print PASS/WARN plus
   next steps (send a message, query Tempo/dashboard).

`uninstall` reverses step 4 (remove the entry, with backup) and, with `--purge`,
deletes the target plugin dir. Missing entry → no-op with a clear message.

## Error handling

| Condition | Behavior |
|---|---|
| `openclaw.json` not found | Error + manual-doc pointer; exit non-zero. |
| `openclaw.json` malformed | Refuse to write; surface the JSON parse error. |
| Packaged bundle missing in wheel | Error pointing at the bundle build step. |
| Plugin dir not writable | Error naming the path. |
| Write fails mid-way | Restore `openclaw.json` from `.bak`. |

## CI / publish

`publish.yml`, pypi job (and a matching local make/dev step): add a Node step
that runs the plugin `build:bundle` and copies the three artifacts into
`sdk/python/agentweave/openclaw_bridge_dist/` **before** `python -m build`, so the
wheel ships the current bundle. Add a preflight assertion that the bundle exists
and is self-contained (no residual bare `@opentelemetry` import/require remains in
`index.js`).

## Risks

- **Bundling CJS OTel deps into an ESM bundle.** `@opentelemetry/exporter-trace-otlp-proto`
  pulls in protobuf machinery that is CJS; esbuild generally handles CJS→ESM
  interop, but the protobuf exporter is a known rough edge. Mitigation order if
  `--format=esm` misbehaves at runtime: (1) emit `--format=cjs` to a `.cjs` file
  and point `openclaw.extensions` at it; (2) mark the troublesome package
  `--external` and ship a tiny `node_modules` for just that dep. The bundle smoke
  test plus an OpenClaw load on the NAS are the gates that catch this before it
  reaches the fleet.
- **`@opentelemetry/api` duplicate copy.** The bundle carries its own
  `@opentelemetry/api`; this is intentional and fine because the bridge owns its
  TracerProvider and communicates with the proxy via env, not via OpenClaw's OTel
  global (see Architecture A).

## Testing

**Plugin (vitest)**
- Bundle smoke test: `bundle/index.js` imports and exports the expected plugin shape.

**CLI / module (`sdk/python/tests/test_openclaw_install.py`)** — mirrors `test_doctor.py`:
- Fresh install writes all three files + a correct `plugins.entries` entry.
- Idempotent re-run: second install does not duplicate or corrupt the entry.
- Config merge vs `--force`: existing user config keys preserved by default,
  overwritten with `--force`.
- Backup file created; restore-on-failure path.
- Missing `openclaw.json` → error exit.
- Flag vs env precedence resolves correctly.
- `uninstall` removes the entry; `--purge` removes files; missing entry is a no-op.

## Rollout / docs

- Update `plugins/openclaw-agentweave-bridge/INSTALL.md` and the README bridge
  section: replace the manual fleet-rollout checklist with
  `pip install agentweave && agentweave openclaw install …`, keeping the manual
  steps as a fallback appendix.

## Verification (e2e, post-merge)

On a host: `agentweave openclaw install --proxy-url … --otlp-endpoint … --agent-id … --project …`,
`openclaw gateway restart`, send a message, then confirm an `openclaw.turn` root
span appears in Tempo for that `agentId` (per #186 acceptance: distinct fleet
`session.id` count in Tempo approaches the agent-events count over a 24h window).
