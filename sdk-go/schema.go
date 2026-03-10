package agentweave

// W3C PROV-O compatible OpenTelemetry attribute names.
// These match the Python and TypeScript SDK constants exactly.

const (
	// Activity type — what kind of action this span represents
	ProvActivityType = "prov.activity.type"

	// Activity type values
	ActivityToolCall  = "tool_call"
	ActivityAgentTurn = "agent_turn"
	ActivityLLMCall   = "llm_call"

	// Agent identity
	ProvAgentID      = "prov.agent.id"
	ProvAgentModel   = "prov.agent.model"
	ProvAgentVersion = "prov.agent.version"

	// PROV-O relations
	ProvWasAssociatedWith = "prov.wasAssociatedWith"
	ProvWasGeneratedBy    = "prov.wasGeneratedBy"
	ProvUsed              = "prov.used"

	// LLM-specific attributes
	ProvLLMProvider          = "prov.llm.provider"
	ProvLLMModel             = "prov.llm.model"
	ProvLLMPromptTokens      = "prov.llm.prompt_tokens"
	ProvLLMCompletionTokens  = "prov.llm.completion_tokens"
	ProvLLMTotalTokens       = "prov.llm.total_tokens"
	ProvLLMStopReason        = "prov.llm.stop_reason"
	ProvLLMPromptPreview     = "prov.llm.prompt_preview"
	ProvLLMResponsePreview   = "prov.llm.response_preview"

	// Provider IDs
	ProviderAnthropic = "anthropic"
	ProviderGoogle    = "google"
	ProviderOpenAI    = "openai"

	// Span name prefixes
	SpanPrefixTool  = "tool"
	SpanPrefixAgent = "agent"
	SpanPrefixLLM   = "llm"
)
