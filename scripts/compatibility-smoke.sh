#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import ast
from pathlib import Path
import sys

root = Path.cwd()

framework_examples = {
    "LangGraph": {
        "script": root / "examples/langgraph/langgraph_example.py",
        "readme": root / "examples/langgraph/README.md",
        "requirements": root / "examples/langgraph/requirements.txt",
        "needles": ["AGENTWEAVE_PROXY_URL", "http://localhost:4000/v1", "ChatOpenAI"],
    },
    "CrewAI": {
        "script": root / "examples/crewai/crew_example.py",
        "readme": root / "examples/crewai/README.md",
        "requirements": root / "examples/crewai/requirements.txt",
        "needles": ["AGENTWEAVE_PROXY_URL", "http://localhost:4000/v1", "LLM("],
    },
    "AutoGen": {
        "script": root / "examples/autogen/autogen_example.py",
        "readme": root / "examples/autogen/README.md",
        "requirements": root / "examples/autogen/requirements.txt",
        "needles": ["AGENTWEAVE_PROXY_URL", "http://localhost:4000/v1", "OpenAIChatCompletionClient"],
    },
    "OpenAI Agents SDK": {
        "script": root / "examples/openai-agents-sdk/agents_example.py",
        "readme": root / "examples/openai-agents-sdk/README.md",
        "requirements": root / "examples/openai-agents-sdk/requirements.txt",
        "needles": ["AGENTWEAVE_PROXY_URL", "http://localhost:4000/v1", "AsyncOpenAI"],
    },
}

compat_doc = root / "docs/compatibility.md"
required_doc_terms = [
    "LangGraph",
    "CrewAI",
    "AutoGen",
    "OpenAI Agents SDK",
    "Claude Code",
    "OpenClaw bridge",
    "Plain Anthropic SDK",
    "Plain OpenAI SDK",
    "Plain Gemini SDK",
    "prov.agent.id",
    "prov.llm.model",
    "prov.project",
    "prov.session.id",
    "cost.usd",
    "Last-tested signal",
]
private_terms = [
    "192.168.",
    "arnabsaha.com",
    "o11y.",
    "30400",
    "30418",
    "proxy injects",
]

failures: list[str] = []

if not compat_doc.exists():
    failures.append("missing docs/compatibility.md")
else:
    text = compat_doc.read_text()
    for term in required_doc_terms:
        if term not in text:
            failures.append(f"docs/compatibility.md missing {term!r}")
    for term in private_terms:
        if term in text:
            failures.append(f"docs/compatibility.md contains private/local term {term!r}")

for name, cfg in framework_examples.items():
    for key in ("script", "readme", "requirements"):
        if not cfg[key].exists():
            failures.append(f"{name}: missing {cfg[key].relative_to(root)}")

    script = cfg["script"]
    if script.exists():
        source = script.read_text()
        try:
            ast.parse(source, filename=str(script))
        except SyntaxError as exc:
            failures.append(f"{name}: syntax error in {script.relative_to(root)}: {exc}")
        for needle in cfg["needles"]:
            if needle not in source:
                failures.append(f"{name}: {script.relative_to(root)} missing {needle!r}")

    readme = cfg["readme"]
    if readme.exists():
        readme_text = readme.read_text()
        if "http://localhost:4000/v1" not in readme_text:
            failures.append(f"{name}: README missing local proxy quickstart")
        for term in private_terms:
            if term in readme_text:
                failures.append(f"{name}: README contains private/local term {term!r}")

if failures:
    print("Compatibility smoke failed:")
    for failure in failures:
        print(f" - {failure}")
    sys.exit(1)

print("Compatibility smoke passed:")
for name in framework_examples:
    print(f" - {name}: example files, syntax, and local proxy wiring verified")
print(" - docs/compatibility.md: matrix and public guidance verified")
PY
