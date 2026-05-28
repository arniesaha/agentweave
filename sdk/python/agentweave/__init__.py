"""AgentWeave — observability and mesh layer for multi-agent AI systems."""

from agentweave.config import AgentWeaveConfig
from agentweave.context import current_session_id, session_scope, set_session_id
from agentweave.decorators import trace_agent, trace_llm, trace_tool
from agentweave.exporter import add_console_exporter, get_tracer, shutdown
from agentweave.instrument import auto_instrument, uninstrument
from agentweave.prompts import fetch_prompt as prompt, PromptHandle

__version__ = "0.3.0"

__all__ = [
    "AgentWeaveConfig",
    "add_console_exporter",
    "auto_instrument",
    "current_session_id",
    "get_tracer",
    "prompt",
    "PromptHandle",
    "session_scope",
    "set_session_id",
    "shutdown",
    "trace_agent",
    "trace_llm",
    "trace_tool",
    "uninstrument",
]
