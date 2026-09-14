from __future__ import annotations

import gzip
import importlib.util
import json
import socket
import threading
import subprocess
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy/k8s/monitoring/otel-collector.yaml"
FIXTURE = ROOT / "tests/fixtures/openclaw-native-model-call-usage.json"
COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib:0.126.0"
PROBE = ROOT / "scripts/verify-native-collector-mapping.py"


def mapping_probe():
    """Load the standalone probe without making scripts a Python package."""
    assert PROBE.is_file(), "Task 3 must provide the native collector mapping probe"
    spec = importlib.util.spec_from_file_location("verify_native_collector_mapping", PROBE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def any_value(value: str | int) -> dict[str, str]:
    return {"intValue": str(value)} if isinstance(value, int) else {"stringValue": value}


def tempo_span(name: str, trace_id: str, attributes: dict[str, str | int]) -> dict:
    return {
        "traceId": trace_id,
        "spanId": "0123456789abcdef",
        "name": name,
        "attributes": [{"key": key, "value": any_value(value)} for key, value in attributes.items()],
    }


def synthetic_tempo_trace(trace_id: str) -> dict:
    """The exact safe trace contract sent by the Task 3 probe."""
    native_resource = {"attributes": [{"key": "service.name", "value": any_value("openclaw")}]}
    other_resource = {"attributes": [{"key": "service.name", "value": any_value("other-runtime")}]}
    cached = {
        "agentweave.probe.case": "cached",
        "openclaw.provider": "cached-provider",
        "gen_ai.request.model": "cached-model",
        "gen_ai.usage.input_tokens": 100,
        "gen_ai.usage.output_tokens": 25,
        "gen_ai.usage.cache_read.input_tokens": 50,
        "gen_ai.usage.cache_creation.input_tokens": 8,
        "prov.harness": "openclaw",
        "prov.source": "native",
        "prov.llm.provider": "cached-provider",
        "prov.llm.model": "cached-model",
        "prov.llm.prompt_tokens": 100,
        "prov.llm.completion_tokens": 25,
        "tokens.cache_read": 50,
        "tokens.cache_write": 8,
    }
    no_cache = {
        "agentweave.probe.case": "no-cache",
        "openclaw.provider": "no-cache-provider",
        "openclaw.model": "fallback-model",
        "gen_ai.usage.input_tokens": 12,
        "gen_ai.usage.output_tokens": 3,
        "prov.harness": "openclaw",
        "prov.source": "native",
        "prov.llm.provider": "no-cache-provider",
        "prov.llm.model": "fallback-model",
        "prov.llm.prompt_tokens": 12,
        "prov.llm.completion_tokens": 3,
    }
    existing_target = {
        "agentweave.probe.case": "existing-target",
        "openclaw.provider": "existing-provider",
        "gen_ai.request.model": "replacement-must-not-win",
        "gen_ai.usage.input_tokens": 9,
        "gen_ai.usage.output_tokens": 2,
        "prov.llm.model": "existing-model",
        "prov.harness": "openclaw",
        "prov.source": "native",
        "prov.llm.provider": "existing-provider",
        "prov.llm.prompt_tokens": 9,
        "prov.llm.completion_tokens": 2,
    }
    usage = {"agentweave.probe.case": "native-usage", "gen_ai.usage.input_tokens": 77, "openclaw.provider": "usage-provider"}
    unrelated = {
        "agentweave.probe.case": "unrelated",
        "openclaw.provider": "other-provider",
        "gen_ai.request.model": "other-model",
        "gen_ai.usage.input_tokens": 44,
        "gen_ai.usage.output_tokens": 4,
        "openclaw.content.input_messages": "harmless-content-marker",
    }
    return {
        "batches": [
            {"resource": native_resource, "scopeSpans": [{"spans": [
                tempo_span("openclaw.model.call", trace_id, cached),
                tempo_span("openclaw.model.call", trace_id, no_cache),
                tempo_span("openclaw.model.call", trace_id, existing_target),
                tempo_span("openclaw.model.usage", trace_id, usage),
            ]}]},
            {"resource": other_resource, "scopeSpans": [{"spans": [
                tempo_span("openclaw.model.call", trace_id, unrelated),
            ]}]},
        ]
    }


def span_attributes(trace: dict, case: str) -> dict[str, str | int]:
    for batch in trace["batches"]:
        for scoped in batch["scopeSpans"]:
            for span in scoped["spans"]:
                attributes = {
                    attr["key"]: int(attr["value"]["intValue"])
                    if "intValue" in attr["value"]
                    else attr["value"]["stringValue"]
                    for attr in span["attributes"]
                }
                if attributes.get("agentweave.probe.case") == case:
                    return attributes
    raise AssertionError(f"missing synthetic span case {case}")


def test_assert_mapped_trace_accepts_exact_mapped_synthetic_trace():
    mapping_probe().assert_mapped_trace(synthetic_tempo_trace("a" * 32), "a" * 32)


@pytest.mark.parametrize(
    ("span_name", "key", "wrong_value"),
    [
        ("cached", "prov.llm.provider", "wrong-provider"),
        ("cached", "prov.llm.model", "wrong-model"),
        ("cached", "prov.harness", "wrong-harness"),
        ("cached", "prov.source", "wrong-source"),
        ("cached", "prov.llm.prompt_tokens", 101),
        ("cached", "prov.llm.completion_tokens", 26),
        ("cached", "tokens.cache_read", 51),
        ("cached", "tokens.cache_write", 9),
        ("cached", "openclaw.provider", "changed-source-provider"),
        ("cached", "gen_ai.request.model", "changed-source-model"),
        ("cached", "gen_ai.usage.input_tokens", 101),
        ("cached", "gen_ai.usage.output_tokens", 26),
        ("cached", "gen_ai.usage.cache_read.input_tokens", 51),
        ("cached", "gen_ai.usage.cache_creation.input_tokens", 9),
        ("no-cache", "prov.llm.model", "wrong-fallback"),
        ("no-cache", "prov.harness", "wrong-harness"),
        ("no-cache", "prov.source", "wrong-source"),
        ("no-cache", "prov.llm.provider", "wrong-provider"),
        ("no-cache", "prov.llm.prompt_tokens", 13),
        ("no-cache", "prov.llm.completion_tokens", 4),
        ("no-cache", "openclaw.model", "changed-fallback-source"),
        ("existing-target", "prov.llm.model", "replacement-must-not-win"),
        ("existing-target", "prov.harness", "wrong-harness"),
        ("existing-target", "prov.source", "wrong-source"),
        ("existing-target", "prov.llm.provider", "wrong-provider"),
        ("existing-target", "prov.llm.prompt_tokens", 10),
        ("existing-target", "prov.llm.completion_tokens", 3),
        ("existing-target", "gen_ai.usage.input_tokens", 10),
        ("existing-target", "gen_ai.usage.output_tokens", 3),
        ("native-usage", "gen_ai.usage.input_tokens", 78),
        ("native-usage", "openclaw.provider", "changed-usage-provider"),
        ("unrelated", "openclaw.provider", "changed-other-provider"),
        ("unrelated", "gen_ai.request.model", "changed-other-model"),
        ("unrelated", "gen_ai.usage.input_tokens", 45),
        ("unrelated", "gen_ai.usage.output_tokens", 5),
        ("unrelated", "openclaw.content.input_messages", "changed-content-marker"),
    ],
)
def test_assert_mapped_trace_rejects_each_wrong_mapped_value(span_name, key, wrong_value):
    trace = synthetic_tempo_trace("b" * 32)
    attributes = span_attributes(trace, span_name)
    attributes[key] = wrong_value
    for batch in trace["batches"]:
        for scoped in batch["scopeSpans"]:
            for span in scoped["spans"]:
                if any(
                    attr["key"] == "agentweave.probe.case"
                    and attr["value"]["stringValue"] == span_name
                    for attr in span["attributes"]
                ):
                    span["attributes"] = [{"key": name, "value": any_value(value)} for name, value in attributes.items()]
    with pytest.raises(AssertionError):
        mapping_probe().assert_mapped_trace(trace, "b" * 32)


def test_assert_mapped_trace_rejects_content_leak_or_mutated_untouched_spans():
    trace = synthetic_tempo_trace("c" * 32)
    cached = next(
        span
        for batch in trace["batches"]
        for scoped in batch["scopeSpans"]
        for span in scoped["spans"]
        if any(attr["key"] == "agentweave.probe.case" and attr["value"]["stringValue"] == "cached" for attr in span["attributes"])
    )
    cached["attributes"].append({"key": "openclaw.content.input_messages", "value": any_value("harmless-content-marker")})
    with pytest.raises(AssertionError):
        mapping_probe().assert_mapped_trace(trace, "c" * 32)


def test_fetch_trace_never_exceeds_its_deadline(monkeypatch):
    probe = mapping_probe()
    clock = iter((0.0, 59.5, 59.75, 60.0))
    request_timeouts: list[float] = []
    sleeps: list[float] = []

    def timeout(*args, **kwargs):
        request_timeouts.append(kwargs["timeout"])
        raise TimeoutError

    monkeypatch.setattr(probe.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(probe.time, "sleep", sleeps.append)
    monkeypatch.setattr(probe.urllib.request, "urlopen", timeout)

    with pytest.raises(RuntimeError, match="within 60 seconds"):
        probe.fetch_trace("e" * 32)

    assert request_timeouts == [0.5]
    assert sleeps == [0.25]


def collector_config() -> str:
    """Extract the ConfigMap literal without parsing a different YAML shape."""
    lines = MANIFEST.read_text().splitlines(keepends=True)
    start = lines.index("  collector.yaml: |\n") + 1
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("---"):
            break
        body.append(line)
    return textwrap.dedent("".join(body))


def local_collector_config(receiver_port: int, grpc_port: int, capture_port: int) -> str:
    """Retarget every temporary receiver/exporter endpoint to loopback."""
    config = collector_config()
    config = config.replace("endpoint: 0.0.0.0:4318", f"endpoint: 127.0.0.1:{receiver_port}", 1)
    config = config.replace("endpoint: 0.0.0.0:4317", f"endpoint: 127.0.0.1:{grpc_port}", 1)
    config = config.replace(
        "endpoint: http://tempo.monitoring.svc.cluster.local:4318",
        f"endpoint: http://127.0.0.1:{capture_port}\n    encoding: json",
        1,
    )
    return config.replace("exporters: [otlphttp/tempo, debug]", "exporters: [otlphttp/tempo]", 1)


def loopback_port() -> int:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def test_local_collector_config_has_only_loopback_receivers():
    config = local_collector_config(receiver_port=43180, grpc_port=43181, capture_port=43182)

    assert "endpoint: 127.0.0.1:43180" in config
    assert "endpoint: 127.0.0.1:43181" in config
    assert "endpoint: 0.0.0.0:4318" not in config
    assert "endpoint: 0.0.0.0:4317" not in config


def fixture_attributes() -> dict[str, object]:
    fixture = json.loads(FIXTURE.read_text())
    resource_span = fixture["resourceSpans"][0]
    span = resource_span["scopeSpans"][0]["spans"][0]
    attributes: dict[str, object] = {}
    for attribute in span["attributes"]:
        value = attribute["value"]
        attributes[attribute["key"]] = int(value["intValue"]) if "intValue" in value else value["stringValue"]
    return attributes


def mapping_statements() -> list[str]:
    """Return only statements under the transform, bounded by root YAML keys."""
    lines = collector_config().splitlines()
    start = lines.index("  transform/openclaw_native:")
    end = lines.index("exporters:", start)
    return [line.strip().removeprefix("- ") for line in lines[start:end] if line.strip().startswith("- set(")]


def mapping_statement(target: str, source: str) -> str:
    expression = f'set(span.attributes["{target}"], span.attributes["{source}"])'
    return next(statement for statement in mapping_statements() if statement.startswith(expression))


def literal_mapping_statement(target: str, value: str) -> str:
    expression = f'set(span.attributes["{target}"], "{value}")'
    return next(statement for statement in mapping_statements() if statement.startswith(expression))


def test_native_fixture_proves_gen_ai_input_is_cache_inclusive():
    attributes = fixture_attributes()
    assert attributes["openclaw.model_call.usage.input_tokens"] + attributes[
        "gen_ai.usage.cache_read.input_tokens"
    ] + attributes["gen_ai.usage.cache_creation.input_tokens"] == attributes[
        "gen_ai.usage.input_tokens"
    ]


def test_manifest_maps_selected_native_call_without_double_counting_cache_tokens():
    prompt_tokens = mapping_statement("prov.llm.prompt_tokens", "gen_ai.usage.input_tokens")

    assert prompt_tokens.startswith('set(span.attributes["prov.llm.prompt_tokens"], span.attributes["gen_ai.usage.input_tokens"])')
    assert (
        'span.attributes["gen_ai.usage.input_tokens"] + '
        'span.attributes["gen_ai.usage.cache_read.input_tokens"]'
    ) not in prompt_tokens


def test_native_mapping_skips_malformed_sources_and_preserves_existing_targets():
    for target, value in (("prov.harness", "openclaw"), ("prov.source", "native")):
        statement = literal_mapping_statement(target, value)
        assert 'resource.attributes["service.name"] == "openclaw"' in statement
        assert 'span.name == "openclaw.model.call"' in statement
        assert f'span.attributes["{target}"] == nil' in statement

    source_targets = {
        "prov.llm.provider": ("openclaw.provider", "IsString"),
        "prov.llm.model": ("gen_ai.request.model", "IsString"),
        "prov.llm.prompt_tokens": ("gen_ai.usage.input_tokens", "IsInt"),
        "prov.llm.completion_tokens": ("gen_ai.usage.output_tokens", "IsInt"),
        "tokens.cache_read": ("gen_ai.usage.cache_read.input_tokens", "IsInt"),
        "tokens.cache_write": ("gen_ai.usage.cache_creation.input_tokens", "IsInt"),
    }
    for target, (source, type_guard) in source_targets.items():
        statement = mapping_statement(target, source)
        assert 'resource.attributes["service.name"] == "openclaw"' in statement
        assert 'span.name == "openclaw.model.call"' in statement
        assert f'span.attributes["{source}"] != nil' in statement
        assert f'{type_guard}(span.attributes["{source}"])' in statement
        assert f'span.attributes["{target}"] == nil' in statement

    fallback = mapping_statement("prov.llm.model", "openclaw.model")
    assert 'span.attributes["gen_ai.request.model"] == nil' in fallback
    assert 'span.attributes["openclaw.model"] != nil' in fallback
    assert 'IsString(span.attributes["openclaw.model"])' in fallback
    assert 'span.attributes["prov.llm.model"] == nil' in fallback


def test_manifest_orders_mapping_after_both_strippers_and_limits_it_to_native_model_calls():
    config = collector_config()

    assert "processors: [memory_limiter, attributes/strip_pii, attributes/strip_openclaw_content, transform/openclaw_native, batch]" in config
    statements = mapping_statements()
    mapping = "\n".join(statements)
    assert len(statements) == 9
    for statement in statements:
        assert 'resource.attributes["service.name"] == "openclaw"' in statement
        assert 'span.name == "openclaw.model.call"' in statement
    for forbidden in ("prov.session.", "cost.usd", "prov.activity.type"):
        assert forbidden not in mapping


def test_pinned_collector_accepts_extracted_configuration():
    with tempfile.TemporaryDirectory() as tempdir:
        config_path = Path(tempdir) / "collector.yaml"
        config_path.write_text(collector_config())
        Path(tempdir).chmod(0o755)
        config_path.chmod(0o644)
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{tempdir}:/conf:ro",
                COLLECTOR_IMAGE,
                "validate",
                "--config=/conf/collector.yaml",
            ],
            check=True,
            capture_output=True,
            text=True,
        )


def _tempo_shape(otlp_payload: dict) -> dict:
    """Convert a captured OTLP exporter payload into Tempo's trace response shape."""
    return {
        "batches": [
            {
                "resource": resource_span["resource"],
                "scopeSpans": resource_span["scopeSpans"],
            }
            for resource_span in otlp_payload["resourceSpans"]
        ]
    }


def test_pinned_collector_executes_native_mapping_locally_without_production_endpoints():
    """A real 0.126.0 collector must transform the complete safe fixture."""
    probe = mapping_probe()
    received: list[dict] = []
    received_event = threading.Event()

    class CaptureHandler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - HTTP handler API
            length = int(self.headers["Content-Length"])
            body = self.rfile.read(length)
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            received.append(json.loads(body))
            received_event.set()
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):  # noqa: A003 - stdlib callback name
            return

    capture_server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    capture_thread = threading.Thread(target=capture_server.serve_forever, daemon=True)
    capture_thread.start()
    receiver_port = loopback_port()
    grpc_port = loopback_port()
    capture_port = capture_server.server_address[1]
    config = local_collector_config(receiver_port, grpc_port, capture_port)
    collector = None
    try:
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "collector.yaml"
            config_path.write_text(config)
            Path(tempdir).chmod(0o755)
            config_path.chmod(0o644)
            collector = subprocess.Popen(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "host",
                    "-v",
                    f"{tempdir}:/conf:ro",
                    COLLECTOR_IMAGE,
                    "--config=/conf/collector.yaml",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            payload = json.dumps(probe.build_payload("d" * 32, time.time_ns())).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{receiver_port}/v1/traces",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            deadline = time.monotonic() + 20
            while True:
                try:
                    with urllib.request.urlopen(request, timeout=2):
                        pass
                    break
                except urllib.error.URLError:
                    if collector.poll() is not None:
                        _, stderr = collector.communicate()
                        raise AssertionError(f"local collector exited before binding: {stderr}")
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
            assert received_event.wait(15), "local collector did not export the synthetic fixture"
        captured_trace = _tempo_shape(received[0])
        try:
            probe.assert_mapped_trace(captured_trace, "d" * 32)
        except AssertionError as exc:
            raise AssertionError(f"{exc}; captured fixture: {json.dumps(captured_trace)}") from exc
    finally:
        if collector:
            collector.terminate()
            try:
                collector.wait(timeout=10)
            except subprocess.TimeoutExpired:
                collector.kill()
                collector.wait(timeout=10)
        capture_server.shutdown()
        capture_server.server_close()
