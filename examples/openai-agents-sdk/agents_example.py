"""OpenAI Agents SDK + AgentWeave — proxy mode.

Point the AsyncOpenAI client at the AgentWeave proxy so every inference call is
automatically traced, token-counted, and costed.
"""

import asyncio
import os

from openai import AsyncOpenAI
from agents import Agent, Runner, function_tool, ModelSettings
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

# ---------------------------------------------------------------------------
# Proxy mode — set base_url on the AsyncOpenAI client
# ---------------------------------------------------------------------------

PROXY_URL = os.environ.get("AGENTWEAVE_PROXY_URL", "http://localhost:4000/v1")

client = AsyncOpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=PROXY_URL,
)

# ---------------------------------------------------------------------------
# Define a simple tool
# ---------------------------------------------------------------------------


@function_tool
def summarize_text(text: str) -> str:
    """Return a one-sentence summary of the given text."""
    # The LLM will call this tool; we return a mock summary for demonstration.
    words = text.split()
    return f"Summary ({len(words)} words): {' '.join(words[:10])}..."


# ---------------------------------------------------------------------------
# Define the agent
# ---------------------------------------------------------------------------

model = OpenAIChatCompletionsModel(model="gpt-4o-mini", openai_client=client)

agent = Agent(
    name="Summarizer",
    instructions="You are a helpful assistant that summarizes text. Use the summarize_text tool when asked to summarize.",
    tools=[summarize_text],
    model=model,
)

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def main():
    prompt = (
        "Please summarize the following text: "
        "'Distributed tracing helps developers understand how requests flow "
        "through microservices. It captures timing, errors, and metadata at "
        "each hop, making it easier to debug latency issues and identify "
        "bottlenecks in complex systems.'"
    )
    print(f"Prompt: {prompt}\n")

    result = await Runner.run(agent, prompt)
    print(f"Answer: {result.final_output}")


if __name__ == "__main__":
    asyncio.run(main())
