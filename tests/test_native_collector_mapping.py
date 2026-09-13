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
