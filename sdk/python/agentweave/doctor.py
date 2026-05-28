"""Local diagnostics for AgentWeave installs.

The doctor command is intentionally conservative: it inspects local Python
metadata and environment configuration, validates URL shape, and only performs
network I/O when explicitly requested.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import sys
from dataclasses import asdict, dataclass, field
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen


Status = str

PASS: Status = "pass"
WARN: Status = "warn"
FAIL: Status = "fail"

PROVIDER_BASE_URLS = (
    "ANTHROPIC_BASE_URL",
    "OPENAI_BASE_URL",
    "GOOGLE_BASE_URL",
    "GOOGLE_GENAI_BASE_URL",
)


@dataclass(frozen=True)
class DoctorCheck:
    """Single diagnostic result emitted by ``agentweave doctor``."""

    name: str
    status: Status
    message: str
    suggestion: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def doctor_payload(checks: list[DoctorCheck]) -> dict[str, object]:
    """Return the stable JSON payload for doctor automation."""
    return {
        "ok": not has_failures(checks),
        "summary": {
            PASS: sum(1 for check in checks if check.status == PASS),
            WARN: sum(1 for check in checks if check.status == WARN),
            FAIL: sum(1 for check in checks if check.status == FAIL),
        },
        "checks": [check.to_dict() for check in checks],
    }


def doctor_payload_json(checks: list[DoctorCheck]) -> str:
    return json.dumps(doctor_payload(checks), indent=2, sort_keys=True)


def has_failures(checks: list[DoctorCheck]) -> bool:
    return any(check.status == FAIL for check in checks)


def run_doctor(
    *,
    env: Mapping[str, str] | None = None,
    check_proxy: bool = False,
    proxy_url: str | None = None,
    timeout_seconds: float = 2.0,
) -> list[DoctorCheck]:
    """Run local AgentWeave diagnostic checks."""
    effective_env = env if env is not None else os.environ
    checks: list[DoctorCheck] = [
        _check_python_version(),
        _check_package_metadata(),
    ]
    checks.extend(_check_provider_base_urls(effective_env))
    checks.append(_check_otlp_endpoint(effective_env))
    checks.extend(_check_identity_env(effective_env))
    checks.append(_check_proxy_token(effective_env))
    checks.append(_check_proxy_health(effective_env, proxy_url, timeout_seconds) if check_proxy else _check_proxy_health_skipped())
    return checks


def _check_python_version() -> DoctorCheck:
    version = sys.version_info
    version_label = platform.python_version()
    if version >= (3, 11):
        return DoctorCheck(
            name="python.version",
            status=PASS,
            message=f"Python {version_label} is supported.",
            details={"version": version_label},
        )
    return DoctorCheck(
        name="python.version",
        status=FAIL,
        message=f"Python {version_label} is not supported.",
        suggestion="Use Python 3.11 or newer.",
        details={"version": version_label},
    )


def _check_package_metadata() -> DoctorCheck:
    try:
        version = importlib.metadata.version("agentweave-sdk")
    except importlib.metadata.PackageNotFoundError:
        return DoctorCheck(
            name="package.metadata",
            status=WARN,
            message="Package metadata for agentweave-sdk was not found.",
            suggestion="Install the SDK with `pip install -e sdk/python` or `pip install agentweave-sdk`.",
        )
    return DoctorCheck(
        name="package.metadata",
        status=PASS,
        message=f"agentweave-sdk {version} metadata is readable.",
        details={"version": version},
    )


def _check_provider_base_urls(env: Mapping[str, str]) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    configured = {name: env.get(name, "").strip() for name in PROVIDER_BASE_URLS if env.get(name, "").strip()}

    if not configured:
        return [
            DoctorCheck(
                name="provider.base_urls",
                status=WARN,
                message="No provider base URL points at an AgentWeave proxy.",
                suggestion="Set ANTHROPIC_BASE_URL, OPENAI_BASE_URL, or GOOGLE_GENAI_BASE_URL to your proxy URL.",
            )
        ]

    for name, value in configured.items():
        url_error = _url_validation_error(value)
        if url_error:
            checks.append(
                DoctorCheck(
                    name=f"provider.{name.lower()}",
                    status=FAIL,
                    message=f"{name} is not a valid HTTP URL: {url_error}.",
                    suggestion=f"Set {name}=http://localhost:4000 or another reachable AgentWeave proxy URL.",
                    details={"value": value},
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    name=f"provider.{name.lower()}",
                    status=PASS,
                    message=f"{name} is configured.",
                    details={"value": value},
                )
            )
    return checks


def _check_otlp_endpoint(env: Mapping[str, str]) -> DoctorCheck:
    value = env.get("AGENTWEAVE_OTLP_ENDPOINT", "").strip()
    if not value:
        return DoctorCheck(
            name="otel.endpoint",
            status=WARN,
            message="AGENTWEAVE_OTLP_ENDPOINT is not set; SDK/proxy defaults may be used.",
            suggestion="Set AGENTWEAVE_OTLP_ENDPOINT=http://localhost:4318 for a local collector.",
        )

    url_error = _url_validation_error(value)
    if url_error:
        return DoctorCheck(
            name="otel.endpoint",
            status=FAIL,
            message=f"AGENTWEAVE_OTLP_ENDPOINT is not a valid HTTP URL: {url_error}.",
            suggestion="Set AGENTWEAVE_OTLP_ENDPOINT to a full http(s) URL, such as http://localhost:4318.",
            details={"value": value},
        )
    return DoctorCheck(
        name="otel.endpoint",
        status=PASS,
        message="AGENTWEAVE_OTLP_ENDPOINT is configured.",
        details={"value": value},
    )


def _check_identity_env(env: Mapping[str, str]) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for name, label in (
        ("AGENTWEAVE_AGENT_ID", "agent identity"),
        ("AGENTWEAVE_PROJECT", "project attribution"),
    ):
        value = env.get(name, "").strip()
        if value:
            checks.append(
                DoctorCheck(
                    name=f"identity.{name.lower()}",
                    status=PASS,
                    message=f"{name} is set for {label}.",
                    details={"value": value},
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    name=f"identity.{name.lower()}",
                    status=WARN,
                    message=f"{name} is not set; spans may be harder to group.",
                    suggestion=f"Set {name} to a stable {label} value.",
                )
            )
    return checks


def _check_proxy_token(env: Mapping[str, str]) -> DoctorCheck:
    token = env.get("AGENTWEAVE_PROXY_TOKEN", "").strip()
    provider_urls = [env.get(name, "").strip() for name in PROVIDER_BASE_URLS if env.get(name, "").strip()]

    if token:
        return DoctorCheck(
            name="proxy.auth_token",
            status=PASS,
            message="AGENTWEAVE_PROXY_TOKEN is set.",
        )

    if provider_urls and any(not _is_local_url(url) for url in provider_urls if not _url_validation_error(url)):
        return DoctorCheck(
            name="proxy.auth_token",
            status=WARN,
            message="AGENTWEAVE_PROXY_TOKEN is not set while a non-local provider proxy URL is configured.",
            suggestion="Set AGENTWEAVE_PROXY_TOKEN for shared LAN or remote proxy deployments.",
        )

    return DoctorCheck(
        name="proxy.auth_token",
        status=PASS,
        message="AGENTWEAVE_PROXY_TOKEN is not set, which is acceptable for local-only proxy use.",
    )


def _check_proxy_health_skipped() -> DoctorCheck:
    return DoctorCheck(
        name="proxy.health",
        status=WARN,
        message="Proxy health check skipped.",
        suggestion="Run `agentweave doctor --check-proxy` to query the configured proxy /health endpoint.",
    )


def _check_proxy_health(env: Mapping[str, str], proxy_url: str | None, timeout_seconds: float) -> DoctorCheck:
    base_url = (proxy_url or env.get("AGENTWEAVE_PROXY_URL") or _first_provider_url(env) or "").strip()
    if not base_url:
        return DoctorCheck(
            name="proxy.health",
            status=WARN,
            message="No proxy URL found for health check.",
            suggestion="Pass --proxy-url or set AGENTWEAVE_PROXY_URL / ANTHROPIC_BASE_URL.",
        )

    url_error = _url_validation_error(base_url)
    if url_error:
        return DoctorCheck(
            name="proxy.health",
            status=FAIL,
            message=f"Proxy URL is not a valid HTTP URL: {url_error}.",
            suggestion="Pass --proxy-url http://localhost:4000.",
            details={"value": base_url},
        )

    health_url = f"{base_url.rstrip('/')}/health"
    try:
        with urlopen(health_url, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 200))
            body = response.read(2048).decode("utf-8", errors="replace")
    except HTTPError as exc:
        return DoctorCheck(
            name="proxy.health",
            status=FAIL,
            message=f"Proxy /health returned HTTP {exc.code}.",
            suggestion="Start or repair the AgentWeave proxy, then retry `agentweave doctor --check-proxy`.",
            details={"url": health_url, "status_code": exc.code},
        )
    except URLError as exc:
        return DoctorCheck(
            name="proxy.health",
            status=WARN,
            message=f"Proxy /health was not reachable: {exc.reason}.",
            suggestion="Start the proxy with `agentweave proxy start` or pass the correct --proxy-url.",
            details={"url": health_url},
        )
    except OSError as exc:
        return DoctorCheck(
            name="proxy.health",
            status=WARN,
            message=f"Proxy /health was not reachable: {exc}.",
            suggestion="Start the proxy with `agentweave proxy start` or pass the correct --proxy-url.",
            details={"url": health_url},
        )

    if 200 <= status_code < 300:
        return DoctorCheck(
            name="proxy.health",
            status=PASS,
            message="Proxy /health is reachable.",
            details={"url": health_url, "status_code": status_code, "body": body[:512]},
        )
    return DoctorCheck(
        name="proxy.health",
        status=FAIL,
        message=f"Proxy /health returned HTTP {status_code}.",
        suggestion="Check proxy logs and upstream configuration.",
        details={"url": health_url, "status_code": status_code, "body": body[:512]},
    )


def _first_provider_url(env: Mapping[str, str]) -> str | None:
    for name in PROVIDER_BASE_URLS:
        value = env.get(name, "").strip()
        if value:
            return value
    return None


def _url_validation_error(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return "scheme must be http or https"
    if not parsed.netloc:
        return "host is missing"
    return None


def _is_local_url(value: str) -> bool:
    hostname = (urlparse(value).hostname or "").lower()
    return hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".local")
