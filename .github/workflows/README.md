# CI / CD Workflows

## Workflows

### `test.yml` — Continuous Integration
Runs on every push and pull request to `main`.
- Matrix: Python 3.11, 3.12
- Installs dev dependencies and runs `pytest`

### `publish.yml` — PyPI + Docker Release
Triggered by pushing a `v*` tag (e.g. `v0.2.0`).

1. **Tests** — same matrix as `test.yml`
2. **Publish** — builds sdist + wheel, publishes to PyPI via OIDC trusted publishing
3. **Docker** — builds and pushes to `ghcr.io/arniesaha/agentweave:<version>` and `:latest`

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
