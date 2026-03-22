import type { OpenClawPluginApi } from "openclaw/plugin-sdk/diagnostics-otel"
import { createAgentWeaveBridgeService } from "./src/service.js"

const plugin = {
  id: "agentweave-bridge",
  name: "AgentWeave Bridge",
  description: "Creates root OTel spans per user message for AgentWeave tracing",
  register(api: OpenClawPluginApi) {
    api.registerService(createAgentWeaveBridgeService())
  },
}
export default plugin
