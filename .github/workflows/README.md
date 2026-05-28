# CI / CD Workflows

## Workflows

### `test.yml` — Continuous Integration
Runs on every push and pull request to `main`.
- Matrix: Python 3.11, 3.12
- Installs dev dependencies and runs `pytest`

### `publish.yml` — PyPI + npm + Docker Release
Triggered by pushing a `v*` tag (e.g. `v0.2.0`).

1. **Tests** — same matrix as `test.yml`
2. **Publish** — builds sdist + wheel, publishes to PyPI via OIDC trusted publishing
3. **npm** — builds `sdk/js` and publishes `agentweave-sdk`
4. **Docker** — builds and pushes to `ghcr.io/arniesaha/agentweave:<version>` and `:latest`

## How to Release

```bash
# 1. Update version in pyproject.toml
# 2. Commit the version bump
git add pyproject.toml
git commit -m "release: v0.2.0"

# 3. Tag and push
git tag v0.2.0
git push origin main --tags
```

The `publish.yml` workflow handles the rest automatically.

## Configuring PyPI Trusted Publishing

OIDC trusted publishing means **no API tokens are stored as secrets**. Instead,
PyPI trusts the GitHub Actions workflow directly.

### One-time setup on PyPI

1. Go to <https://pypi.org/manage/project/agentweave/settings/publishing/>
   (or create the project first by doing a manual upload)
2. Add a new **trusted publisher**:
   - **Owner:** `arniesaha`
   - **Repository:** `agentweave`
   - **Workflow name:** `publish.yml`
   - **Environment:** `pypi`
3. Save — no secrets needed in GitHub.

### GitHub environment

Create a GitHub environment named **`pypi`** at:
`Settings → Environments → New environment → "pypi"`

This is referenced in `publish.yml` and required for OIDC token exchange.

## Configuring npm Publishing

The npm job publishes `sdk/js` as the public `agentweave-sdk` package. The
workflow runs `scripts/npm-publish-preflight.sh --require-auth` before
`npm publish` so auth and package-access problems fail with a useful message
instead of a late `npm publish` error.

### Required GitHub setup

Create a GitHub environment named **`npm`** at:
`Settings -> Environments -> New environment -> "npm"`

Then configure one of these npm auth paths:

1. **Current token-based path:** add a repository or environment secret named
   `NPM_TOKEN`. It must be an npm automation token for a user that is listed as
   a maintainer/collaborator on `agentweave-sdk`.
2. **Future trusted-publishing path:** configure npm trusted publishing for the
   `arniesaha/agentweave` `publish.yml` workflow and then remove token-based
   auth from `publish.yml`.

### Diagnosing npm 404 during publish

If the workflow fails with:

```text
404 Not Found - PUT https://registry.npmjs.org/agentweave-sdk
```

and `npm view agentweave-sdk version` still shows the previous release, the
package was not published. For an existing package, that usually means the
token authenticates as a user that cannot publish `agentweave-sdk`, or the token
is stale. Fix the npm-side maintainer/token setup, then rerun the tag workflow.

Useful local checks:

```bash
npm view agentweave-sdk version --registry https://registry.npmjs.org
NODE_AUTH_TOKEN=... npm whoami --registry https://registry.npmjs.org
NODE_AUTH_TOKEN=... npm access ls-collaborators agentweave-sdk --json
```
