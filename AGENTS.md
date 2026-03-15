# AgentWeave — Autonomous Execution Protocol

## For Sub-Agents

When assigned a GitHub issue to implement:

1. **Branch**: `git checkout -b fix/issue-{NUMBER}`
2. **Understand**: Read the issue acceptance criteria — that is your definition of done
3. **Implement**: Make the code changes
4. **Test**:
   - Python: `cd sdk/python && pip install -e ".[dev]" && pytest`
   - TypeScript: `cd sdk/js && npm ci && npx jest --verbose`
   - Go: `cd sdk/go && go test ./... -v`
5. **Commit**: `fix(scope): description (#NUMBER)` — follow conventional commits
6. **Push & PR**: Push branch, open PR referencing the issue
7. **Comment on issue**: what was changed, any caveats, PR link
8. **Do NOT merge** — Nix handles merge + deploy

## Nix Post-Merge Checklist

After merging a PR:

1. Run `scripts/deploy.sh`
2. Run `scripts/verify.sh`
3. If **both pass**: close issue with comment linking to Grafana dashboard
4. If **either fails**: reopen issue, comment with failure details and logs

## Definition of Done (for any issue)

- [ ] Code implemented and tests passing
- [ ] `scripts/deploy.sh` exits 0
- [ ] `scripts/verify.sh` exits 0 (all checks green)
- [ ] Issue closed with deploy confirmation comment

## Environment Reference

| Resource               | Address                        |
|------------------------|--------------------------------|
| Proxy (nix)            | `192.168.1.70:30400`           |
| Proxy (max)            | `192.168.1.70:30401`           |
| Tempo OTLP endpoint    | `192.168.1.70:30418`           |
| Docker registry        | `localhost:5000`               |
| K8s namespace          | `agentweave`                   |
| Grafana dashboard UID  | `agentweave-overview`          |

## Commit Message Convention

```
type(scope): description (#ISSUE)
```

Types: `fix`, `feat`, `chore`, `docs`, `refactor`, `test`

Scopes: `proxy`, `sdk-py`, `sdk-js`, `sdk-go`, `dashboard`, `k8s`, `ci`
