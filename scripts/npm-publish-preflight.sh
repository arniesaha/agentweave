#!/usr/bin/env bash
set -euo pipefail

require_auth=false
if [[ "${1:-}" == "--require-auth" ]]; then
  require_auth=true
elif [[ "${1:-}" != "" ]]; then
  echo "usage: $0 [--require-auth]" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
package_dir="$repo_root/sdk/js"
registry="${NPM_CONFIG_REGISTRY:-https://registry.npmjs.org}"

if ! command -v npm >/dev/null 2>&1; then
  echo "error: npm CLI is not available on PATH; install Node.js/npm before running the publish preflight." >&2
  exit 1
fi

package_name="$(cd "$package_dir" && node -p "require('./package.json').name")"
package_version="$(cd "$package_dir" && node -p "require('./package.json').version")"

echo "npm publish preflight"
echo "  package:  $package_name@$package_version"
echo "  registry: $registry"

if ! npm view "$package_name" name --registry "$registry" >/dev/null 2>&1; then
  cat >&2 <<EOF
error: npm package '$package_name' is not visible at $registry.

This usually means the package name is wrong, the package has not been created
on npm yet, or the workflow token is pointed at the wrong registry. Create or
claim the package on npm before publishing this release.
EOF
  exit 1
fi

if npm view "$package_name@$package_version" version --registry "$registry" >/dev/null 2>&1; then
  cat >&2 <<EOF
error: npm package '$package_name@$package_version' already exists.

npm versions are immutable. Bump sdk/js/package.json before publishing again.
EOF
  exit 1
fi

if [[ "$require_auth" != true ]]; then
  echo "ok: registry/package checks passed; authenticated checks skipped"
  exit 0
fi

if [[ -z "${NODE_AUTH_TOKEN:-}" ]] && [[ -z "$(npm config get //registry.npmjs.org/:_authToken 2>/dev/null || true)" ]]; then
  cat >&2 <<EOF
error: npm auth token is not configured.

Set the GitHub Actions NPM_TOKEN secret to an npm automation token for a user
with publish access to '$package_name', or migrate the workflow to npm trusted
publishing and remove token-based auth.
EOF
  exit 1
fi

if ! npm_user="$(npm whoami --registry "$registry" 2>/tmp/agentweave-npm-whoami.err)"; then
  cat >&2 <<EOF
error: npm auth failed for $registry.

The configured token cannot authenticate with npm. Rotate NPM_TOKEN or configure
npm trusted publishing before retrying the release.

npm whoami output:
$(cat /tmp/agentweave-npm-whoami.err)
EOF
  exit 1
fi

collaborators_json="$(mktemp)"
if ! npm access ls-collaborators "$package_name" --registry "$registry" --json >"$collaborators_json" 2>/tmp/agentweave-npm-access.err; then
  cat >&2 <<EOF
error: npm user '$npm_user' cannot read collaborator access for '$package_name'.

The token may authenticate but still lack maintainer/publish rights for this
package. Add '$npm_user' as a maintainer for '$package_name' or replace
NPM_TOKEN with a token from a maintainer account.

npm access output:
$(cat /tmp/agentweave-npm-access.err)
EOF
  exit 1
fi

if ! node -e '
const fs = require("fs");
const collaborators = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const user = process.argv[2];
process.exit(Object.prototype.hasOwnProperty.call(collaborators, user) ? 0 : 1);
' "$collaborators_json" "$npm_user"; then
  cat >&2 <<EOF
error: npm user '$npm_user' is not listed as a collaborator for '$package_name'.

Add '$npm_user' as a maintainer for '$package_name' on npm, then retry the tag
publish workflow. No secret values were printed.
EOF
  exit 1
fi

echo "ok: npm auth and package access preflight passed for '$npm_user'"
