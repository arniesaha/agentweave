# AgentWeave Python SDK

The **AgentWeave Python SDK** provides comprehensive tracing tools for multi-agent systems built in Python. With a small set of decorators, you can capture:
- Agent decision provenance
- Tool and LLM interactions
- Cost and performance metrics

## Getting Started

Install the Python SDK:

```bash
pip install agentweave-sdk
```

Configure the SDK to point at any OpenTelemetry (OTLP) backend:

```python
from agentweave import AgentWeaveConfig

AgentWeaveConfig.setup(
    agent_id="example-agent",
    agent_model="claude-sonnet-4-6",
    otel_endpoint="http://localhost:4318",  # Use your OTLP collector's endpoint
)
```

Refer to the [root documentation](../README.md) for a detailed example, or continue exploring Python-specific guides and examples below.