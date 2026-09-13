# Runtime adapter contract tests

Adapter test fixtures must derive from the host's published event or attribute contract. A fixture
typed only as `object`, `unknown`, `Record<string, unknown>`, or a hand-written permissive event
interface does not prove that the runtime emits those fields. A negative compile-time fixture for
a known nonexistent field should fail if the type guard is removed. Keep the type check in CI,
alongside behavior tests. For collector-side mappings, check a real exported span's attribute
names against every mapped source key before treating an invented mapping as tested.

The OpenClaw bridge pins the published `openclaw@2026.9.2` package as a development-only type
dependency. `src/service.test.ts` imports `DiagnosticEventPayload` from its exported
`plugin-sdk/diagnostic-runtime` subpath, and CI runs `npm run build` before Vitest. The production
bundle still marks `openclaw` external.

The deployed local OpenClaw fork at `bf598e8bbe1df8585310220aeae660c8b0ce381c` has the
following additions relative to the published 2026.9.2 type declarations:

| Event | Fork-only fields | Local source |
|---|---|---|
| `message.queued` | `inputPreview` | `src/infra/diagnostic-events.ts`, `DiagnosticMessageQueuedEvent` |
| `session.state` | `inputPreview`, `taskLabel` | `src/infra/diagnostic-events.ts`, `DiagnosticSessionStateEvent` |

The fork also exports `onModelDiagnosticEvent` and `onTrustedDiagnosticEvent` from the same SDK
subpath. Production uses narrowly placed `@ts-expect-error` directives for those two fork-only
imports; `onDiagnosticEvent` and the event union come from the published package without an
ambient override. These directives should become unused compile errors if the public package
adds those exports, prompting review of the delta.

The bridge test helper declares only this explicit delta. It does not declare `contextId`,
`executionId`, `cwd`, `repository`, or `raw_data` on those events. Before an OpenClaw host upgrade,
compare the new host source and published `DiagnosticEventPayload` with this table, update the pin
and the delta together, then run `npm ci --ignore-scripts`, `npm run build`, `npm test`, and
`npm run build:bundle && npm run verify:bundle`. A green test suite against the old pin is not
evidence about a new host.

Python tests for declared extras follow the same fail-closed rule: import the dependency normally.
If an extra such as `zstandard` is missing, those tests must fail rather than report a green suite
with skips. The CI Python job installs `.[dev]` before testing.
