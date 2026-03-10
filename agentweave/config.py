"""AgentWeave configuration — global setup for tracing."""

from __future__ import annotations

import threading
from typing import Optional

from pydantic import BaseModel, Field
from agentweave.exporter import init_tracer  # noqa: F401 — re-exported so tests can patch it

# --- Module-level singleton state (outside Pydantic model) ---
_instance: Optional["AgentWeaveConfig"] = None
_lock: threading.Lock = threading.Lock()


class AgentWeaveConfig(BaseModel):
    """Configuration for AgentWeave tracing.

    Call ``AgentWeaveConfig.setup(...)`` once at application startup to configure
    the global agent identity and OTLP endpoint.
    """

    agent_id: str = Field(description="Unique identifier for this agent (e.g. 'nix-v1')")
    agent_model: str = Field(default="", description="Model name (e.g. 'claude-sonnet-4-6')")
    agent_version: str = Field(default="0.1.0", description="Agent version string")
    otel_endpoint: str = Field(
        default="http://localhost:4318",
        description="OTLP HTTP endpoint (e.g. 'http://localhost:4318')",
    )
    service_name: str = Field(default="agentweave", description="OTel service name")
    enabled: bool = Field(default=True, description="Enable or disable tracing globally")
    captures_input: bool = Field(default=False, description="Default: capture tool inputs")
    captures_output: bool = Field(default=False, description="Default: capture tool outputs")

    @classmethod
    def setup(cls, **kwargs: object) -> "AgentWeaveConfig":
        """Create or replace the global config. Thread-safe."""
        global _instance
        with _lock:
            _instance = cls(**kwargs)  # type: ignore[arg-type]
            # Initialise the OTel exporter with this config
            init_tracer(_instance)
            return _instance

    @classmethod
    def get(cls) -> "AgentWeaveConfig":
        """Return the current global config; raises if ``setup()`` was never called."""
        if _instance is None:
            raise RuntimeError(
                "AgentWeaveConfig.setup() has not been called. "
                "Call it once at application startup."
            )
        return _instance

    @classmethod
    def get_or_none(cls) -> Optional["AgentWeaveConfig"]:
        """Return the current global config or ``None``."""
        return _instance

    @classmethod
    def reset(cls) -> None:
        """Reset global config (useful in tests)."""
        global _instance
        with _lock:
            _instance = None
