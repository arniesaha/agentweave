/**
 * W3C PROV-O compatible OpenTelemetry attribute names.
 *
 * These constants map PROV-O concepts to OTel span attributes, enabling
 * provenance-aware tracing of agent decisions and tool calls.
 *
 * Reference: https://www.w3.org/TR/prov-o/
 */

// --- Span attribute keys ---

// prov:Entity -- data inputs and outputs (files, API responses, DB rows)
export const PROV_ENTITY = 'prov.entity';
export const PROV_ENTITY_TYPE = 'prov.entity.type';
export const PROV_ENTITY_VALUE = 'prov.entity.value';
export const PROV_ENTITY_SIZE_BYTES = 'prov.entity.size_bytes';

// prov:Activity -- the tool call or agent decision
export const PROV_ACTIVITY = 'prov.activity';
export const PROV_ACTIVITY_TYPE = 'prov.activity.type';

// prov:Agent -- which agent made the call
export const PROV_AGENT = 'prov.agent';
export const PROV_AGENT_ID = 'prov.agent.id';
export const PROV_AGENT_MODEL = 'prov.agent.model';
export const PROV_AGENT_VERSION = 'prov.agent.version';

// prov:wasGeneratedBy -- output linked to producing activity
export const PROV_WAS_GENERATED_BY = 'prov.wasGeneratedBy';

// prov:used -- activity linked to consumed inputs
export const PROV_USED = 'prov.used';

// prov:wasAssociatedWith -- activity linked to agent
export const PROV_WAS_ASSOCIATED_WITH = 'prov.wasAssociatedWith';

// prov:wasDerivedFrom -- entity derived from another entity
export const PROV_WAS_DERIVED_FROM = 'prov.wasDerivedFrom';

// prov:LLM -- attributes specific to LLM invocations
export const PROV_LLM_PROVIDER = 'prov.llm.provider';
export const PROV_LLM_MODEL = 'prov.llm.model';
export const PROV_LLM_PROMPT_TOKENS = 'prov.llm.prompt_tokens';
export const PROV_LLM_COMPLETION_TOKENS = 'prov.llm.completion_tokens';
export const PROV_LLM_TOTAL_TOKENS = 'prov.llm.total_tokens';
export const PROV_LLM_STOP_REASON = 'prov.llm.stop_reason';
export const PROV_LLM_PROMPT_PREVIEW = 'prov.llm.prompt_preview';
export const PROV_LLM_RESPONSE_PREVIEW = 'prov.llm.response_preview';

// --- Provider IDs ---
export const PROVIDER_ANTHROPIC = 'anthropic';
export const PROVIDER_GOOGLE = 'google';
export const PROVIDER_OPENAI = 'openai';

// --- Activity types ---
export const ACTIVITY_TOOL_CALL = 'tool_call';
export const ACTIVITY_AGENT_TURN = 'agent_turn';
export const ACTIVITY_LLM_CALL = 'llm_call';

// --- Entity types ---
export const ENTITY_INPUT = 'input';
export const ENTITY_OUTPUT = 'output';
export const ENTITY_FILE = 'file';
export const ENTITY_API_RESPONSE = 'api_response';
export const ENTITY_DB_QUERY = 'db_query';
export const ENTITY_TEXT = 'text';

// --- Span name prefixes ---
export const SPAN_PREFIX_TOOL = 'tool';
export const SPAN_PREFIX_AGENT = 'agent';
export const SPAN_PREFIX_LLM = 'llm';
