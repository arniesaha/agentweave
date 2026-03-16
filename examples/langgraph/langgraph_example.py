"""LangGraph + AgentWeave — proxy mode and optional decorator mode.

Proxy mode (default): point ChatOpenAI at the AgentWeave proxy.
Decorator mode: wrap graph nodes with AgentWeave decorators for fine control.
"""

import os

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langchain_core.tools import tool
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages


# ---------------------------------------------------------------------------
# Proxy mode — just change base_url, zero code changes to the agent
# ---------------------------------------------------------------------------

PROXY_URL = os.environ.get("AGENTWEAVE_PROXY_URL", "http://localhost:4000/v1")

llm = ChatOpenAI(
    model="gpt-4o-mini",
    openai_api_key=os.environ["OPENAI_API_KEY"],
    openai_api_base=PROXY_URL,
)


# -- Simple tool ---------------------------------------------------------- #

@tool
def word_count(text: str) -> int:
    """Count the number of words in a text."""
    return len(text.split())


# -- Graph state ---------------------------------------------------------- #

class State(TypedDict):
    messages: Annotated[list, add_messages]


# -- Graph construction --------------------------------------------------- #

tools = [word_count]
llm_with_tools = llm.bind_tools(tools)


def agent_node(state: State):
    return {"messages": [llm_with_tools.invoke(state["messages"])]}


def should_continue(state: State):
    last = state["messages"][-1]
    if last.tool_calls:
        return "tools"
    return END


graph = StateGraph(State)
graph.add_node("agent", agent_node)
graph.add_node("tools", ToolNode(tools))
graph.add_edge(START, "agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")

app = graph.compile()


# ---------------------------------------------------------------------------
# Decorator mode (optional) — explicit spans around each graph invocation
# ---------------------------------------------------------------------------

def run_with_decorators():
    """Shows how to combine proxy mode with AgentWeave decorators."""
    from agentweave import AgentWeaveConfig
    from agentweave.decorators import trace_agent

    AgentWeaveConfig.setup(
        agent_id="langgraph-example",
        agent_model="gpt-4o-mini",
        otel_endpoint=os.environ.get(
            "AGENTWEAVE_OTLP_ENDPOINT", "http://localhost:4318"
        ),
    )

    @trace_agent(name="langgraph_agent", captures_input=True, captures_output=True)
    def traced_run(question: str) -> str:
        result = app.invoke({"messages": [("user", question)]})
        return result["messages"][-1].content

    answer = traced_run(
        "How many words are in the sentence: 'The quick brown fox jumps over the lazy dog'?"
    )
    print(f"\n[decorator mode] Answer: {answer}")
    AgentWeaveConfig.shutdown()


# ---------------------------------------------------------------------------
# Main — proxy-only by default
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    question = "How many words are in the sentence: 'The quick brown fox jumps over the lazy dog'?"
    print(f"Question: {question}\n")

    result = app.invoke({"messages": [("user", question)]})
    print(f"Answer: {result['messages'][-1].content}")

    if "--decorators" in sys.argv:
        run_with_decorators()
