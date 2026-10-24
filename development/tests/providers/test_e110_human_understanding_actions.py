"""The existing learning tool exposes an own-behavior adjustment, not new rights."""
import pytest
from eva.providers.human_understanding_actions import HumanUnderstandingActions
from tests.application.test_housemate_development import source


def test_existing_tool_exposes_personality_delta_without_adding_another_action():
    tools = HumanUnderstandingActions.conversation_tools()
    assert len(tools) == 1 and tools[0].name == "record_human_understanding"
    assert "personality_delta" in tools[0].input_schema["properties"]["kind"]["enum"]
    # Existing argument envelope and source/revision validation remain in use.
    assert "previous_source_ref" in tools[0].input_schema["required"]
    assert not tools[0].input_schema["additionalProperties"]


def test_delta_cannot_turn_into_unbounded_profile_text():
    with pytest.raises(ValueError,match="compact"):
        source("x"*401)
    assert source("x"*400)["learning_context"]["records"][0]["record"]["kind"] == "personality_delta"
