"""Tests for the PROV-O OTel schema constants."""

from agentweave import schema


class TestProvAttributes:
    """Verify PROV-O attribute constants are correctly defined."""

    def test_entity_attributes(self):
        assert schema.PROV_ENTITY == "prov.entity"
        assert schema.PROV_ENTITY_TYPE == "prov.entity.type"
        assert schema.PROV_ENTITY_VALUE == "prov.entity.value"
        assert schema.PROV_ENTITY_SIZE_BYTES == "prov.entity.size_bytes"

    def test_activity_attributes(self):
        assert schema.PROV_ACTIVITY == "prov.activity"
        assert schema.PROV_ACTIVITY_TYPE == "prov.activity.type"

    def test_agent_attributes(self):
        assert schema.PROV_AGENT == "prov.agent"
        assert schema.PROV_AGENT_ID == "prov.agent.id"
        assert schema.PROV_AGENT_MODEL == "prov.agent.model"
        assert schema.PROV_AGENT_VERSION == "prov.agent.version"

    def test_relation_attributes(self):
        assert schema.PROV_WAS_GENERATED_BY == "prov.wasGeneratedBy"
        assert schema.PROV_USED == "prov.used"
        assert schema.PROV_WAS_ASSOCIATED_WITH == "prov.wasAssociatedWith"
        assert schema.PROV_WAS_DERIVED_FROM == "prov.wasDerivedFrom"

    def test_activity_types(self):
        assert schema.ACTIVITY_TOOL_CALL == "tool_call"
        assert schema.ACTIVITY_AGENT_TURN == "agent_turn"

    def test_entity_types(self):
        assert schema.ENTITY_INPUT == "input"
        assert schema.ENTITY_OUTPUT == "output"
        assert schema.ENTITY_FILE == "file"
        assert schema.ENTITY_API_RESPONSE == "api_response"
        assert schema.ENTITY_DB_QUERY == "db_query"
        assert schema.ENTITY_TEXT == "text"

    def test_span_prefixes(self):
        assert schema.SPAN_PREFIX_TOOL == "tool"
        assert schema.SPAN_PREFIX_AGENT == "agent"

    def test_session_attributes(self):
        assert schema.PROV_SESSION_ID == "prov.session.id"
        assert schema.PROV_PROJECT == "prov.project"
        assert schema.PROV_SESSION_TURN == "prov.session.turn"

    def test_all_prov_attributes_start_with_prov(self):
        """All PROV_ constants should have values starting with 'prov.'."""
        for attr_name in dir(schema):
            if attr_name.startswith("PROV_"):
                value = getattr(schema, attr_name)
                assert value.startswith("prov."), f"{attr_name}={value} does not start with 'prov.'"
