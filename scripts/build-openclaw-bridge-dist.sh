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
