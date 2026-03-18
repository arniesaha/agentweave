# Versioning Policy

AgentWeave follows [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`).

## Pre-release Status

AgentWeave is currently in **0.x.y pre-release**. During this phase:
- Any release may include breaking changes
- The proxy API and span schema are stabilising but not yet guaranteed stable
- Once we reach `1.0.0`, the compatibility guarantees below apply fully

---

## Version Numbers by Component

| Component | Current Version | Location |
|-----------|----------------|----------|
| Proxy + Python SDK | `0.2.0` | `sdk/python/pyproject.toml` |
| TypeScript SDK | `0.2.5` | `sdk/js/package.json` |
| Go SDK | `v0.x.y` | `sdk/go/go.mod` (tag-based) |
| Docker image | matches proxy version | built from `deploy/docker/Dockerfile` |

---

## What Constitutes a Breaking Change (MAJOR bump)

- **Span attribute renames** — e.g., `prov.agent.id` → `prov.agent.identifier` breaks existing Grafana queries and dashboards
- **Proxy API changes** — removing or renaming endpoints (`/health`, `/session`, `/v1/*`)
- **Proxy token header change** — renaming `Authorization: Bearer` to something else
- **SDK public API changes** — renaming or removing `@trace_tool`, `@trace_agent`, `@trace_llm` decorators
- **OTLP attribute schema changes** that break existing Tempo queries

## What Is Non-Breaking (MINOR or PATCH)

- **New span attributes** — adding `prov.task.label` is additive; existing queries still work
- **New proxy endpoints** — adding `POST /session` does not break existing callers
- **New SDK decorators** — adding `@trace_session` is additive
- **New pricing entries** — adding a new model to `pricing.py` is non-breaking
- **Bug fixes** — correcting token counting, fixing streaming edge cases
- **New provider support** — adding Mistral or Cohere support is additive

---

## Release Process

### Bumping a version

1. **Proxy + Python SDK** — update `version` in `sdk/python/pyproject.toml`
2. **TypeScript SDK** — update `version` in `sdk/js/package.json`
3. **Go SDK** — create a git tag: `git tag sdk/go/v0.x.y && git push origin sdk/go/v0.x.y`
4. **Docker image** — rebuilt automatically; tag matches proxy version

### Coordinated releases
When the proxy API changes (new endpoint, new span attribute), bump all SDKs together in the same PR or a coordinated release sequence.

### Changelog
Update `CHANGELOG.md` (if it exists) or include a clear PR description with:
- What changed
- Migration steps for breaking changes
- Which component versions are affected

---

## Publishing

- **Python SDK**: `cd sdk/python && python -m build && twine upload dist/*`
- **TypeScript SDK**: `cd sdk/js && npm publish` (requires npm auth, CI workflow pending — see issue #91)
- **Go SDK**: push a version tag; Go module proxy handles the rest
- **Docker image**: `docker build -f deploy/docker/Dockerfile -t localhost:5000/agentweave-proxy:latest . && docker push`
