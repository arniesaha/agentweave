"""W3C PROV-O compatible OpenTelemetry attribute names.

These constants map PROV-O concepts to OTel span attributes, enabling
provenance-aware tracing of agent decisions and tool calls.

Reference: https://www.w3.org/TR/prov-o/
"""

# --- Span attribute keys ---

# prov:Entity — data inputs and outputs (files, API responses, DB rows)
PROV_ENTITY = "prov.entity"
PROV_ENTITY_TYPE = "prov.entity.type"
PROV_ENTITY_VALUE = "prov.entity.value"
PROV_ENTITY_SIZE_BYTES = "prov.entity.size_bytes"

# prov:Activity — the tool call or agent decision
PROV_ACTIVITY = "prov.activity"
PROV_ACTIVITY_TYPE = "prov.activity.type"

# prov:Agent — which agent made the call
PROV_AGENT = "prov.agent"
PROV_AGENT_ID = "prov.agent.id"
PROV_AGENT_MODEL = "prov.agent.model"
PROV_AGENT_VERSION = "prov.agent.version"

# prov:wasGeneratedBy — output linked to producing activity
PROV_WAS_GENERATED_BY = "prov.wasGeneratedBy"

# prov:used — activity linked to consumed inputs
PROV_USED = "prov.used"

# prov:wasAssociatedWith — activity linked to agent
PROV_WAS_ASSOCIATED_WITH = "prov.wasAssociatedWith"

# prov:wasDerivedFrom — entity derived from another entity
PROV_WAS_DERIVED_FROM = "prov.wasDerivedFrom"

# prov:LLM — attributes specific to LLM invocations
PROV_LLM_PROVIDER = "prov.llm.provider"
PROV_LLM_MODEL = "prov.llm.model"
PROV_LLM_PROMPT_TOKENS = "prov.llm.prompt_tokens"
PROV_LLM_COMPLETION_TOKENS = "prov.llm.completion_tokens"
PROV_LLM_TOTAL_TOKENS = "prov.llm.total_tokens"
PROV_LLM_STOP_REASON = "prov.llm.stop_reason"
PROV_LLM_PROMPT_PREVIEW = "prov.llm.prompt_preview"    # first 512 chars of prompt
PROV_LLM_RESPONSE_PREVIEW = "prov.llm.response_preview"  # first 512 chars of response

# --- Provider IDs ---
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_GOOGLE = "google"
PROVIDER_OPENAI = "openai"

# --- Activity types ---

ACTIVITY_TOOL_CALL = "tool_call"
ACTIVITY_AGENT_TURN = "agent_turn"
ACTIVITY_LLM_CALL = "llm_call"

# --- Entity types ---

ENTITY_INPUT = "input"
ENTITY_OUTPUT = "output"
ENTITY_FILE = "file"
ENTITY_API_RESPONSE = "api_response"
ENTITY_DB_QUERY = "db_query"
ENTITY_TEXT = "text"

# --- Span name prefixes ---

SPAN_PREFIX_TOOL = "tool"
SPAN_PREFIX_AGENT = "agent"
SPAN_PREFIX_LLM = "llm"
