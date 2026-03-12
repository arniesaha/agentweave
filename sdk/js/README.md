# AgentWeave TypeScript SDK

AgentWeave SDK provides decorators and tools to instrument multi-agent systems with OpenTelemetry support.

## Installation

```bash
npm install agentweave-sdk
```

## Usage

### Setup

```typescript
import { AgentWeaveConfig, traceTool, traceAgent, traceLlm } from 'agentweave';

AgentWeaveConfig.setup({
  agentId: 'my-agent',
  otlpEndpoint: 'http://localhost:4318',
});
```

### Trace a Tool

```typescript
const search = traceTool('web_search')(async (query: string) => {
  // Perform web search here
  return { result: 'search results' };
});
await search('example query');
```

### Trace an Agent

```typescript
const processRequest = traceAgent('user-agent')(async (request: any) => {
  // Process user request here
  return { success: true };
});
await processRequest({ userId: 123 });
```

### Trace an LLM Interaction

```typescript
const callLlm = traceLlm({
  provider: 'openai',
  model: 'gpt-4',
  capturesInput: true,
  capturesOutput: true,
})(async (messages) => {
  // Call LLM API here
  return { usage: { input_tokens: 50, output_tokens: 100 } };
});
const response = await callLlm([{ role: 'user', content: 'Hello!' }]);
console.log(response.outputTokens);
```

## License
MIT