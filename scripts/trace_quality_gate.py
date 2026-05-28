#!/usr/bin/env python3
"""AgentWeave dogfood trace data-quality gate.

This script is intentionally standalone so it can run before the Python SDK is
installed and without touching the AgentWeave CLI surface.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_SERVICE_REGEX = "agentweave-proxy|mux-router"
ATTR_AGENT_ID = "prov.agent.id"
ATTR_ACTIVITY_TYPE = "prov.activity.type"
ATTR_MODEL = "prov.llm.model"
ATTR_PROJECT = "prov.project"
ATTR_PROMPT_TOKENS = "prov.llm.prompt_tokens"
ATTR_COMPLETION_TOKENS = "prov.llm.completion_tokens"
ATTR_COST = "cost.usd"

UNATTRIBUTED_AGENT_IDS = {"", "unknown", "unattributed"}
MODEL_MISSING_VALUES = {"", "unknown", "unattributed", "none", "null"}
LLM_ACTIVITY_TYPES = {"llm_call"}
MODELISH_ACTIVITY_TYPES = {"agent_turn"}
LIFECYCLE_PREFIXES = ("hook.", "subagent.", "openclaw.", "session.", "agent.")
SUSPICIOUS_MODEL_RE = re.compile(r"\[[0-9]+[smhd]\]$")


@dataclass
class SpanRecord:
    source: str
    service: str = ""
    span_name: str = ""
    activity_type: str = ""
    model: str = ""
    agent_id: str = ""
    project: str = ""
    count: float = 1.0
    prompt_tokens: str = ""
    completion_tokens: str = ""
    cost_usd: str = ""
    trace_id: str = ""

    def is_llm(self) -> bool:
        if self.activity_type in LLM_ACTIVITY_TYPES:
            return True
        if self.activity_type in MODELISH_ACTIVITY_TYPES and self.model.strip():
            return True
        if self.span_name.startswith("llm."):
            return True
        if self.is_lifecycle():
            return False
        return bool(self.model.strip())

    def is_lifecycle(self) -> bool:
        if self.activity_type and self.activity_type not in LLM_ACTIVITY_TYPES:
            return True
        return self.span_name.startswith(LIFECYCLE_PREFIXES)

    def identity(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "source": self.source,
            "service": self.service,
            "span_name": self.span_name,
            "activity_type": self.activity_type,
            "model": self.model,
            "agent_id": self.agent_id,
            "project": self.project,
            "count": self.count,
        }
        if self.trace_id:
            out["trace_id"] = self.trace_id
        return out


def _attr_value(value: dict[str, Any]) -> str:
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in value:
            return str(value[key])
    return ""


def _attrs_to_dict(attrs: list[dict[str, Any]] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for attr in attrs or []:
        key = attr.get("key")
        value = attr.get("value")
        if isinstance(key, str) and isinstance(value, dict):
            result[key] = _attr_value(value)
    return result


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_prometheus_response(name: str, payload: dict[str, Any]) -> list[SpanRecord]:
    """Parse Prometheus vector or matrix responses into aggregate records."""
    data = payload.get("data", {})
    results = data.get("result", [])
    records: list[SpanRecord] = []
    for result in results:
        metric = result.get("metric", {})
        value = result.get("value")
        if value:
            count = _float_value(value[1])
        else:
            values = result.get("values", [])
            count = sum(_float_value(v[1]) for v in values)

        if count <= 0:
            continue

        records.append(
            SpanRecord(
                source=f"prometheus:{name}",
                service=metric.get("service", ""),
                span_name=metric.get("span_name") or metric.get("name", ""),
                activity_type=metric.get("prov_activity_type", ""),
                model=metric.get("prov_llm_model", ""),
                agent_id=metric.get("prov_agent_id", ""),
                project=metric.get("prov_project", ""),
                count=count,
            )
        )
    return records


def _iter_tempo_search_spans(trace: dict[str, Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for span_set in trace.get("spanSets") or []:
        spans.extend(span_set.get("spans") or [])
    span_set = trace.get("spanSet")
    if span_set:
        spans.extend(span_set.get("spans") or [])
    return spans


def parse_tempo_response(name: str, payload: dict[str, Any]) -> list[SpanRecord]:
    """Parse Tempo search or trace API responses into span records."""
    records: list[SpanRecord] = []

    if "traces" in payload:
        for trace in payload.get("traces") or []:
            spans = _iter_tempo_search_spans(trace)
            if not spans:
                records.append(
                    SpanRecord(
                        source=f"tempo:{name}",
                        service=trace.get("rootServiceName", ""),
                        span_name=trace.get("rootTraceName", ""),
                        trace_id=trace.get("traceID", ""),
                    )
                )
                continue

            for span in spans:
                attrs = _attrs_to_dict(span.get("attributes"))
                records.append(
                    SpanRecord(
                        source=f"tempo:{name}",
                        service=trace.get("rootServiceName", ""),
                        span_name=trace.get("rootTraceName", ""),
                        activity_type=attrs.get(ATTR_ACTIVITY_TYPE, ""),
                        model=attrs.get(ATTR_MODEL, ""),
                        agent_id=attrs.get(ATTR_AGENT_ID, ""),
                        project=attrs.get(ATTR_PROJECT, ""),
                        prompt_tokens=attrs.get(ATTR_PROMPT_TOKENS, ""),
                        completion_tokens=attrs.get(ATTR_COMPLETION_TOKENS, ""),
                        cost_usd=attrs.get(ATTR_COST, ""),
                        trace_id=trace.get("traceID", ""),
                    )
                )
        return records

    for batch in payload.get("batches") or []:
        service_attrs = _attrs_to_dict(batch.get("resource", {}).get("attributes"))
        service = service_attrs.get("service.name", "")
        for scope_span in batch.get("scopeSpans") or []:
            for span in scope_span.get("spans") or []:
                attrs = _attrs_to_dict(span.get("attributes"))
                records.append(
                    SpanRecord(
                        source=f"tempo:{name}",
                        service=service,
                        span_name=span.get("name", ""),
                        activity_type=attrs.get(ATTR_ACTIVITY_TYPE, ""),
                        model=attrs.get(ATTR_MODEL, ""),
                        agent_id=attrs.get(ATTR_AGENT_ID, ""),
                        project=attrs.get(ATTR_PROJECT, ""),
                        prompt_tokens=attrs.get(ATTR_PROMPT_TOKENS, ""),
                        completion_tokens=attrs.get(ATTR_COMPLETION_TOKENS, ""),
                        cost_usd=attrs.get(ATTR_COST, ""),
                        trace_id=span.get("traceId", ""),
                    )
                )
    return records


def load_fixture(path: str) -> list[SpanRecord]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    records: list[SpanRecord] = []
    for name, response in (payload.get("prometheus") or {}).items():
        records.extend(parse_prometheus_response(name, response))
    for name, response in (payload.get("tempo") or {}).items():
        records.extend(parse_tempo_response(name, response))
    for item in payload.get("spans") or []:
        records.append(SpanRecord(**{"source": "fixture", **item}))
    return records


def query_json(url: str, params: dict[str, str], timeout: int) -> dict[str, Any]:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full_url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_prometheus_records(
    prometheus_url: str, time_range: str, service_regex: str, timeout: int
) -> list[SpanRecord]:
    base = prometheus_url.rstrip("/")
    query = (
        "sum by (service, span_name, prov_activity_type, prov_llm_model, "
        "prov_agent_id, prov_project) "
        f'(increase(traces_spanmetrics_calls_total{{service=~"{service_regex}"}}[{time_range}]))'
    )
    payload = query_json(f"{base}/api/v1/query", {"query": query}, timeout)
    return parse_prometheus_response("span_inventory", payload)


def fetch_tempo_records(
    tempo_url: str, time_range_seconds: int, service_regex: str, limit: int, timeout: int
) -> list[SpanRecord]:
    base = tempo_url.rstrip("/")
    end = int(time.time())
    start = end - time_range_seconds
    services = service_regex.split("|")
    service_filter = " || ".join(f'resource.service.name = "{service}"' for service in services)
    query = (
        f"{{ {service_filter} }} | select("
        "span.prov.activity.type, span.prov.llm.model, span.prov.agent.id, "
        "span.prov.project, span.prov.llm.prompt_tokens, "
        "span.prov.llm.completion_tokens, span.cost.usd)"
    )
    payload = query_json(
        f"{base}/api/search",
        {"q": query, "start": str(start), "end": str(end), "limit": str(limit)},
        timeout,
    )
    return parse_tempo_response("search", payload)


def parse_duration_seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)([smhd])", value)
    if not match:
        raise argparse.ArgumentTypeError("range must look like 15m, 6h, 1d")
    amount = int(match.group(1))
    unit = match.group(2)
    return amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def issue(level: str, code: str, message: str, record: SpanRecord) -> dict[str, Any]:
    return {
        "level": level,
        "code": code,
        "message": message,
        "span": record.identity(),
    }


def evaluate(records: list[SpanRecord]) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    info: list[dict[str, Any]] = []

    for record in records:
        is_llm = record.is_llm()
        model = record.model.strip()
        agent = record.agent_id.strip()

        if is_llm and agent.lower() in UNATTRIBUTED_AGENT_IDS:
            failures.append(
                issue(
                    "fail",
                    "unattributed_llm_span",
                    "LLM span has missing, unknown, or unattributed agent id.",
                    record,
                )
            )

        if is_llm and model.lower() in MODEL_MISSING_VALUES:
            failures.append(
                issue(
                    "fail",
                    "missing_llm_model",
                    "LLM span has no usable prov.llm.model label.",
                    record,
                )
            )

        if is_llm and model and SUSPICIOUS_MODEL_RE.search(model):
            failures.append(
                issue(
                    "fail",
                    "suspicious_model_label",
                    "Model label looks like it accidentally captured a Prometheus range suffix.",
                    record,
                )
            )

        has_usable_model = model.lower() not in MODEL_MISSING_VALUES

        if is_llm and has_usable_model and (not record.prompt_tokens or not record.completion_tokens):
            warnings.append(
                issue(
                    "warn",
                    "missing_token_fields",
                    "Known-model LLM span is missing prompt/completion token fields.",
                    record,
                )
            )

        if is_llm and has_usable_model and not record.cost_usd and record.source.startswith("tempo:"):
            warnings.append(
                issue(
                    "warn",
                    "missing_cost_field",
                    "Known-model LLM span is missing cost.usd.",
                    record,
                )
            )

        if not is_llm and not model and record.is_lifecycle():
            info.append(
                issue(
                    "info",
                    "blank_model_lifecycle_span",
                    "Lifecycle/non-LLM span has blank model and does not fail the gate.",
                    record,
                )
            )

    status = "pass"
    if failures:
        status = "fail"
    elif warnings:
        status = "warn"

    return {
        "status": status,
        "summary": {
            "records_checked": len(records),
            "llm_records": sum(1 for record in records if record.is_llm()),
            "failures": len(failures),
            "warnings": len(warnings),
            "info": len(info),
        },
        "failures": failures,
        "warnings": warnings,
        "info": info,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check AgentWeave dogfood trace attribution and model-label quality."
    )
    parser.add_argument("--prometheus-url", help="Prometheus base URL, e.g. http://host:9090")
    parser.add_argument("--tempo-url", help="Tempo query base URL, e.g. http://host:3200")
    parser.add_argument("--fixture", action="append", default=[], help="Offline fixture JSON file")
    parser.add_argument("--range", default="6h", help="Lookback range for live queries (default: 6h)")
    parser.add_argument("--service-regex", default=DEFAULT_SERVICE_REGEX)
    parser.add_argument("--tempo-limit", type=int, default=200)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="Emit full JSON report")
    parser.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Return non-zero when the report status is warn.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    range_seconds = parse_duration_seconds(args.range)

    records: list[SpanRecord] = []
    errors: list[str] = []

    for fixture in args.fixture:
        records.extend(load_fixture(fixture))

    if args.prometheus_url:
        try:
            records.extend(
                fetch_prometheus_records(
                    args.prometheus_url, args.range, args.service_regex, args.timeout
                )
            )
        except Exception as exc:  # pragma: no cover - exercised manually/live.
            errors.append(f"prometheus query failed: {exc}")

    if args.tempo_url:
        try:
            records.extend(
                fetch_tempo_records(
                    args.tempo_url,
                    range_seconds,
                    args.service_regex,
                    args.tempo_limit,
                    args.timeout,
                )
            )
        except Exception as exc:  # pragma: no cover - exercised manually/live.
            errors.append(f"tempo query failed: {exc}")

    if not records and not errors:
        parser.error("provide --fixture, --prometheus-url, or --tempo-url")

    report = evaluate(records)
    report["query"] = {
        "range": args.range,
        "service_regex": args.service_regex,
        "prometheus_url": args.prometheus_url or "",
        "tempo_url": args.tempo_url or "",
        "fixture_count": len(args.fixture),
    }
    report["errors"] = errors
    if errors and report["status"] == "pass":
        report["status"] = "warn"

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = report["summary"]
        print(
            f"trace-quality-gate: {report['status']} "
            f"({summary['records_checked']} records, {summary['failures']} failures, "
            f"{summary['warnings']} warnings)"
        )
        for item in report["failures"][:10]:
            print(f"FAIL {item['code']}: {item['message']} {item['span']}")
        for item in report["warnings"][:10]:
            print(f"WARN {item['code']}: {item['message']} {item['span']}")
        for error in errors:
            print(f"WARN query_error: {error}")

    if report["status"] == "fail":
        return 1
    if args.fail_on_warn and report["status"] == "warn":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
