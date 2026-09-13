# #289: Validate adapter fixtures against the host contract

## Goal

Make bridge fixtures compile against OpenClaw's published `DiagnosticEventPayload` for the deployed 2026.9.2 host, and make missing declared Python compression dependencies fail the suite.

## Design

Pin `openclaw@2026.9.2` as a development-only type dependency. Import its exported `DiagnosticEventPayload` through `openclaw/plugin-sdk/diagnostic-runtime`; keep the production bundle's `openclaw` import external. Type the bridge test event helper against this union and include a compile-time negative fixture for fields the host does not emit. Remove the hand-written ambient diagnostic module that currently erases the host contract. CI must use a supported Node 24 runtime and install from the lockfile without OpenClaw lifecycle scripts. Future adapters must use their host's published types or an explicitly versioned contract snapshot, and their fixture type-check must be part of CI.

Replace `pytest.importorskip("zstandard")` with a regular import in each zstd test. Since `zstandard` is declared in both development and proxy extras, missing it is an environment error, not a reason to skip the feature's tests.

## Steps and verification

1. Add a type-level failing fixture for an invalid diagnostic field and run `npm run build` red against the current `unknown` stub.
2. Add the pinned host type dependency, remove the ambient diagnostic declaration, type all event fixtures, and make `npm run build`, `npm test`, `npm run build:bundle`, and `npm run verify:bundle` pass.
3. Change Python zstd tests to imports that fail when absent. Demonstrate missing-dependency failure in an isolated test environment, then run the full Python suite with the dependency installed.
4. Update CI to Node 24 and deterministic plugin install/build/test. Review the diff, run repository checks, open PR, and perform the required post-merge deploy/verify gates before closing #289.

## Caveats

The historical #283 `contextId`/`executionId` mapping has already disappeared from current `main`; this issue is a regression guard, not a removal. The pinned public package may not include fork-only diagnostic fields. Any fork-only field must be separately documented and checked against the host fork on upgrade, never silently inserted into a local ambient declaration.
