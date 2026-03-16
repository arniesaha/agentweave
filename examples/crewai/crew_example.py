"""CrewAI + AgentWeave — proxy mode.

Point CrewAI's LLM at the AgentWeave proxy so every inference call is
automatically traced, token-counted, and costed.
"""

import os

from crewai import Agent, Task, Crew, LLM

# ---------------------------------------------------------------------------
# Proxy mode — configure the LLM to route through AgentWeave
# ---------------------------------------------------------------------------

PROXY_URL = os.environ.get("AGENTWEAVE_PROXY_URL", "http://localhost:4000/v1")

llm = LLM(
    model="openai/gpt-4o-mini",
    base_url=PROXY_URL,
    api_key=os.environ["OPENAI_API_KEY"],
)

# ---------------------------------------------------------------------------
# Define agents
# ---------------------------------------------------------------------------

researcher = Agent(
    role="Researcher",
    goal="Find key facts about a topic",
    backstory="You are a diligent research assistant who gathers concise facts.",
    llm=llm,
    verbose=True,
)

writer = Agent(
    role="Writer",
    goal="Write a short summary from research notes",
    backstory="You are a skilled writer who turns research into clear summaries.",
    llm=llm,
    verbose=True,
)

# ---------------------------------------------------------------------------
# Define tasks
# ---------------------------------------------------------------------------

research_task = Task(
    description="Research the topic: 'Benefits of open-source observability'. List 3 key points.",
    expected_output="A bullet list of 3 key facts about open-source observability.",
    agent=researcher,
)

writing_task = Task(
    description="Using the research provided, write a concise 2-sentence summary.",
    expected_output="A 2-sentence summary of the research.",
    agent=writer,
)

# ---------------------------------------------------------------------------
# Run the crew
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    crew = Crew(
        agents=[researcher, writer],
        tasks=[research_task, writing_task],
        verbose=True,
    )

    result = crew.kickoff()
    print(f"\n--- Final Output ---\n{result}")
