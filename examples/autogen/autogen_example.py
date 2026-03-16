"""AutoGen + AgentWeave — proxy mode.

Point AutoGen's LLM config at the AgentWeave proxy so every inference call is
automatically traced, token-counted, and costed.
"""

import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient

# ---------------------------------------------------------------------------
# Proxy mode — set base_url in the model client
# ---------------------------------------------------------------------------

PROXY_URL = os.environ.get("AGENTWEAVE_PROXY_URL", "http://localhost:4000/v1")

model_client = OpenAIChatCompletionClient(
    model="gpt-4o-mini",
    base_url=PROXY_URL,
    api_key=os.environ["OPENAI_API_KEY"],
)

# ---------------------------------------------------------------------------
# Define agent
# ---------------------------------------------------------------------------

agent = AssistantAgent(
    name="assistant",
    model_client=model_client,
    system_message="You are a helpful assistant. Answer questions concisely.",
)

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def main():
    question = "What are three benefits of distributed tracing for AI agents?"
    print(f"Question: {question}\n")

    response = await agent.on_messages(
        [TextMessage(content=question, source="user")],
        CancellationToken(),
    )
    print(f"Answer: {response.chat_message.content}")


if __name__ == "__main__":
    asyncio.run(main())
