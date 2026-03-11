"""AgentWeave — observability and mesh layer for multi-agent AI systems."""

from agentweave.config import AgentWeaveConfig
from agentweave.decorators import trace_agent, trace_llm, trace_tool
from agentweave.exporter import add_console_exporter, get_tracer, shutdown
from agentweave.instrument import auto_instrument, uninstrument

__version__ = "0.2.0"

__all__ = [
    "AgentWeaveConfig",
    "add_console_exporter",
    "auto_instrument",
    "get_tracer",
    "shutdown",
    "trace_agent",
    "trace_llm",
    "trace_tool",
    "uninstrument",
]
