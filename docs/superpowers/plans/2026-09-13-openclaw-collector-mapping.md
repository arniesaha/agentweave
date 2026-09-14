# OpenClaw Collector Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested, declarative collector adapter that enriches native OpenClaw model-call spans without inventing session or cost parity.

**Architecture:** A `transform/openclaw_native` processor runs after the existing PII and content strippers and before batch/export. It selects `resource.attributes["service.name"] == "openclaw"` and `span.name == "openclaw.model.call"`, copies observed safe fields to `prov.*`, and computes prompt tokens only from a pinned live usage fixture. A standalone OTLP probe asserts what Tempo actually received.

**Tech Stack:** Collector Contrib `0.126.0`, OTTL, Kubernetes ConfigMap, Python 3 standard-library OTLP JSON probe, pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-openclaw-collector-mapping-design.md`

## Global Constraints

- Preserve source attributes; never overwrite existing `prov.*` targets.
- Select only native `openclaw.model.call` spans from `service.name=openclaw`; do not enrich `openclaw.model.usage`, bridge spans, proxy spans, or unrelated services.
- Keep `attributes/strip_pii` and `attributes/strip_openclaw_content` before the transform; export no native content fields.
- Do not emit `prov.session.*`, `cost.usd`, `prov.activity.type`, or a zero-valued unknown cost.
- Keep the bridge, proxy routing, and Codex traceparent carry unchanged.
- Use the exact deployed collector version `otel/opentelemetry-collector-contrib:0.126.0` for configuration validation.

---

## File structure

- `deploy/k8s/monitoring/otel-collector.yaml`: only production mapping and pipeline-order change.
- `scripts/verify-native-collector-mapping.py`: synthetic OTLP sender, Tempo polling, exact attribute assertions; no provider key or content data.
- `tests/test_native_collector_mapping.py`: fixture-driven assertion tests and manifest-order contract tests.
- `docs/openclaw-native-otel-architecture.md`: reusable collector-adapter contract and explicit session/cost limitations.

### Task 1: Pin usage semantics from a real native span

**Files:**
- Create: `tests/fixtures/openclaw-native-model-call-usage.json`
- Test: `tests/test_native_collector_mapping.py`

**Interfaces:**
- Consumes: Tempo `/api/traces/{trace_id}` for #285 synthetic native trace `75b4714c4766a6ab648460ac75a5f0ac` or a newer disposable native model-call trace.
- Produces: a content-free OTLP-JSON fixture containing `resourceSpans`, `service.name=openclaw`, and one `openclaw.model.call` with observed provider/model/usage attribute names and numeric values.

- [ ] **Step 1: Obtain observed numbers.** Query the specified Tempo trace; extract only `openclaw.provider`, `openclaw.model`, `gen_ai.request.model`, `gen_ai.usage.*`, and `openclaw.model_call.usage.*`. If the trace has expired, use a new disposable model turn and record its trace ID in a comment in the fixture. Never copy input/output content or credentials.
- [ ] **Step 2: Write the fixture.** Keep only the selected source attributes and `service.name`, with synthetic trace/span IDs. Record whether `gen_ai.usage.input_tokens` includes cache by comparing it with the exporter’s `total`, `output`, and cache buckets. Example decision check for the #285 counts: `818 + 39424 + 13 = 40255`; this indicates uncached input if these are the exported values.
- [ ] **Step 3: Write the first failing test.** In `tests/test_native_collector_mapping.py`, read the manifest with `Path(...).read_text()` and use `textwrap.dedent` on the text following `collector.yaml: |`. Assert that it contains `transform/openclaw_native`, the `gen_ai.usage.input_tokens` source, and the cache-bucket addition justified by the fixture. The production change that makes this test pass is the collector mapping in Task 2; a Python-only token calculator is not a substitute.
- [ ] **Step 4: Run the test and confirm RED.** `pytest tests/test_native_collector_mapping.py -q` must fail because the collector transform is absent, not because the fixture is malformed.
- [ ] **Step 5: Stop if semantics remain ambiguous.** Do not implement a prompt-token expression or claim full token parity until a real native fixture distinguishes inclusive from uncached input.

### Task 2: Implement and validate the collector transform

**Files:**
- Modify: `deploy/k8s/monitoring/otel-collector.yaml`
- Test: `tests/test_native_collector_mapping.py`

**Interfaces:**
- Consumes: Task 1 fixture and its decided token convention.
- Produces: `transform/openclaw_native` in the traces pipeline after both stripping processors; adds `prov.harness`, `prov.source`, provider/model, token and cache fields only to selected model-call spans.

- [ ] **Step 1: Extend RED tests.** Assert processor order `memory_limiter → attributes/strip_pii → attributes/strip_openclaw_content → transform/openclaw_native → batch`; assert the transform has `service.name` and exact span-name guards, non-overwrite guards, and no statements setting forbidden session/cost/activity fields.
- [ ] **Step 2: Implement the transform.** Use OTTL `trace_statements` in `context: span`. Each `set(span.attributes["target"], span.attributes["source"])` has a `where` clause requiring `resource.attributes["service.name"] == "openclaw"`, `span.name == "openclaw.model.call"`, source non-nil, and target nil. Model fallback uses `openclaw.model` only when `gen_ai.request.model` is absent. Prompt-token arithmetic follows Task 1’s observed convention; use integer arithmetic and guard missing buckets so it never reads nil as an operand. Preserve the existing processors and exporters.
- [ ] **Step 3: Confirm GREEN.** `pytest tests/test_native_collector_mapping.py -q` passes with fixture-backed formula, selection, and pipeline-order assertions. These static tests are not evidence that the Collector executes the transform correctly; Task 3's live OTLP probe supplies that evidence.
- [ ] **Step 4: Parse the actual collector config.** Add a test helper that extracts `data.collector.yaml` by finding the literal `  collector.yaml: |` line and dedenting its following indented block. Write that string to `tempfile.TemporaryDirectory()` as `collector.yaml`, then run `docker run --rm -v "<tempdir>:/conf:ro" otel/opentelemetry-collector-contrib:0.126.0 validate --config=/conf/collector.yaml` via `subprocess.run(check=True)`. If this image version lacks `validate`, run `--config=/conf/collector.yaml --dry-run` only after checking the image's help output; never treat a YAML parse alone as OTTL validation.
- [ ] **Step 5: Commit.** `git add deploy/k8s/monitoring/otel-collector.yaml tests/test_native_collector_mapping.py tests/fixtures/openclaw-native-model-call-usage.json && git commit -m 'feat(k8s): map native OpenClaw model fields (#286)'`.

### Task 3: Add a live OTLP-to-Tempo contract probe

**Files:**
- Create: `scripts/verify-native-collector-mapping.py`
- Test: `tests/test_native_collector_mapping.py`
- Modify: `docs/openclaw-native-otel-architecture.md`

**Interfaces:**
- Consumes: Task 2 collector configuration; `AGENTWEAVE_OTLP_ENDPOINT` and `AGENTWEAVE_TEMPO_URL` (same defaults as `scripts/verify-native-content-privacy.py`).
- Produces: exit 0 only when Tempo shows exact mapped values, unchanged source fields, no forbidden fields, untouched unrelated service/usage spans, and stripped content.

- [ ] **Step 1: Write RED assertion tests.** Test a pure `assert_mapped_trace(trace: dict, expected_trace_id: str) -> None` with a synthetic Tempo response. Include cached/no-cache native calls, a native usage span, an unrelated service, a pre-existing `prov.llm.model`, and one harmless content marker. Mutate each expected value and confirm the relevant test fails.
- [ ] **Step 2: Implement the probe.** Reuse the structure of `scripts/verify-native-content-privacy.py`: generate unique trace/span IDs, POST OTLP JSON to `/v1/traces`, poll `/api/traces/{trace_id}` for at most 60 seconds, decode attributes by key/value, and call `assert_mapped_trace`. Print only pass/fail and synthetic trace ID; do not print credentials, prompts, or full trace payloads.
- [ ] **Step 3: Confirm GREEN.** `pytest tests/test_native_collector_mapping.py -q` passes; run `python3 -m py_compile scripts/verify-native-collector-mapping.py`.
- [ ] **Step 4: Document the adapter.** In `docs/openclaw-native-otel-architecture.md`, show the selected native source fields, collector mapping, double-count avoidance, and the fact that session/cost remain separate follow-ups. Describe how another OTel-emitting runtime would add its own selected transform and fixture.
- [ ] **Step 5: Commit.** `git add scripts/verify-native-collector-mapping.py tests/test_native_collector_mapping.py docs/openclaw-native-otel-architecture.md && git commit -m 'test(k8s): verify native collector mapping in Tempo (#286)'`.

### Task 4: Review, PR, and post-merge gates

**Files:**
- Verify: all Task 1–3 files and issue #286 criteria.

**Interfaces:**
- Consumes: commits from Tasks 2–3.
- Produces: reviewed PR referencing #286, deploy/verify evidence, and issue resolution or explicit failure report.

- [ ] **Step 1: Run full suites.** `cd sdk/python && pip install -e '.[dev]' && pytest`; `cd sdk/js && npm ci && npx jest --verbose`; `cd sdk/go && go test ./... -v`. Run `pytest tests/test_native_collector_mapping.py -q`, `git diff --check`, and pinned collector validation again on the final tree.
- [ ] **Step 2: Request code review.** Review against the spec, inspect the diff, and resolve Critical/Important findings. Confirm the transform cannot affect unrelated services or duplicate usage spans.
- [ ] **Step 3: Rescope the tracking issue.** Update #286 acceptance criteria to: selected native model-call spans carry observed provider/model/token/cache mappings; content and PII stripping remain first; unrelated and usage spans stay unchanged; collector config is declarative; live Tempo probe passes; adapter pattern is documented. Link separate follow-up issues for native session correlation and cost, each explicitly noting that current export lacks session fields and current native OTLP bypasses the cost normalizer. Preserve the original issue context in a comment when editing its body.
- [ ] **Step 4: Push and PR.** Push `fix/issue-286`, open a PR targeting `main` with `Closes #286` only if its rescoped criteria are demonstrably met, and comment on #286 with the mapped fields, omitted session/cost, tests, caveats, and PR link. Do not merge as a sub-agent.
- [ ] **Step 5: After an authorized merge, deploy.** Run `scripts/deploy.sh`, then `scripts/verify.sh`, then `python3 scripts/verify-native-content-privacy.py` and `python3 scripts/verify-native-collector-mapping.py`. Record exact exit codes and synthetic trace IDs. A gateway/model call must not be required by the mapping probe.
- [ ] **Step 6: Resolve issue by evidence.** If all post-merge gates pass, close #286 with a deploy confirmation and Grafana dashboard link (`/d/agentweave-overview`). If either required deploy/verify gate fails, reopen/comment with logs and leave the issue open.
