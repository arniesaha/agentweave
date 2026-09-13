from __future__ import annotations

import json
import subprocess
import tempfile
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy/k8s/monitoring/otel-collector.yaml"
FIXTURE = ROOT / "tests/fixtures/openclaw-native-model-call-usage.json"
COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib:0.126.0"


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


def fixture_attributes() -> dict[str, object]:
    fixture = json.loads(FIXTURE.read_text())
    resource_span = fixture["resourceSpans"][0]
    span = resource_span["scopeSpans"][0]["spans"][0]
    attributes: dict[str, object] = {}
    for attribute in span["attributes"]:
        value = attribute["value"]
        attributes[attribute["key"]] = int(value["intValue"]) if "intValue" in value else value["stringValue"]
    return attributes


def test_native_fixture_proves_gen_ai_input_is_cache_inclusive():
    attributes = fixture_attributes()
    assert attributes["openclaw.model_call.usage.input_tokens"] + attributes[
        "gen_ai.usage.cache_read.input_tokens"
    ] + attributes["gen_ai.usage.cache_creation.input_tokens"] == attributes[
        "gen_ai.usage.input_tokens"
    ]


def test_manifest_maps_selected_native_call_without_double_counting_cache_tokens():
    config = collector_config()

    assert "transform/openclaw_native:" in config
    assert 'span.attributes["gen_ai.usage.input_tokens"]' in config
    assert 'span.attributes["prov.llm.prompt_tokens"]' in config
    assert (
        'span.attributes["gen_ai.usage.input_tokens"] + '
        'span.attributes["gen_ai.usage.cache_read.input_tokens"]'
    ) not in config


def test_manifest_orders_mapping_after_both_strippers_and_limits_it_to_native_model_calls():
    config = collector_config()

    assert "processors: [memory_limiter, attributes/strip_pii, attributes/strip_openclaw_content, transform/openclaw_native, batch]" in config
    mapping = config.split("transform/openclaw_native:", maxsplit=1)[1].split("\n\n    exporters:", maxsplit=1)[0]
    assert 'resource.attributes["service.name"] == "openclaw"' in mapping
    assert 'span.name == "openclaw.model.call"' in mapping
    assert 'span.attributes["prov.llm.provider"] == nil' in mapping
    assert 'span.attributes["prov.llm.model"] == nil' in mapping
    assert 'span.attributes["gen_ai.request.model"] == nil' in mapping
    assert 'span.attributes["openclaw.model"] != nil' in mapping
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
