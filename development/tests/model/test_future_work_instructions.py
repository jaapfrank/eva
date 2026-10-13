"""Observe effective model instructions without credentials or provider calls."""
import pytest

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
from tests.model.test_pydantic_conversation_model import _generate
from tests.support.scripted_pydantic_conversation import ScriptedPhase, ScriptedPydanticConversation


@pytest.mark.parametrize("available, can_start_work", [
    (("create_bounded_durable_goal",), True),
    (("start_delegated_work",), True),
    (("start_background_research",), True),
    (("resume_delegated_work",), True),
    ((), False),
    (("get_delegated_work",), False),
    (("stop_delegated_work",), False),
])
@pytest.mark.parametrize("after_memory", [False, True])
def test_effective_instructions_follow_authorized_work_tools(tmp_path, available, can_start_work, after_memory):
    scripted = ScriptedPydanticConversation((ScriptedPhase.text("Ik houd me aan de beschikbare mogelijkheden."),))
    model = PydanticConversationModel(auth_file=tmp_path / "absent-auth.json",
        model_backend=scripted.model,
        conversation_tools=PostgresDelegatedWorkActions.conversation_tools(include_research=True))
    receipt = (ConversationActionReceipt(
        ConversationActionCall("memory-1", "search_personal_memory", {"query_text": "afspraak"}),
        {"facts": []}) if after_memory else None)
    _generate(model, available_actions=available, completed_action=receipt)
    instructions = scripted.captures[0].instructions
    assert ("Deze gespreksroute kan geen toekomstig werk inplannen." not in instructions) == can_start_work
    assert "Beloof geen tijdstip of latere terugkoppeling zonder zichtbaar bewijs." in instructions
    # Registered but unauthorized tools do not create prompt capabilities.
    # Schemas and descriptions remain exclusively in the deferred tool catalog.
    assert "duurzame hulpagent. Geef alle benodigde context mee" not in instructions
    assert '"properties"' not in instructions
