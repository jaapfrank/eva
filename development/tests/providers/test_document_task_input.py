import pytest
from dataclasses import replace
from eva.application.conversation import ConversationTurn, ConversationConflict
from eva.providers.document_task_input import DocumentTaskInput


def test_two_independent_texts_and_host_ingress_binding():
    args={"title":"Gezinsdag", "first_document":"Beste familie, welkom!", "second_document":"Zaterdag samen eten?"}
    task=DocumentTaskInput.from_arguments(args)
    turn=ConversationTurn.private("tenant","home","owner","chat","message","Maak twee documenten.","2026-09-13T12:00:00Z")
    assert task.first_document not in task.second_document
    assert task.bound_identity(turn,"call")==task.bound_identity(turn,"call")
    assert task.bound_identity(replace(turn,message_id="next"),"call")!=task.bound_identity(turn,"call")
    assert task.bound_identity(replace(turn,principal_id="other",audience=replace(turn.audience,principal_id="other",audience_id="person:other")),"call")!=task.bound_identity(turn,"call")
    changed=DocumentTaskInput.from_arguments({**args,"second_document":"Gewijzigd"})
    assert changed.sha256!=task.sha256
    # Same ingress gets the same host key, allowing durable payload-conflict detection.
    assert changed.bound_identity(turn,"call")==task.bound_identity(turn,"call")


@pytest.mark.parametrize("extra",["path","owner_id","goal_id","expected_generation","tool_configuration_sha256"])
def test_model_cannot_supply_authority_or_output_locations(extra):
    with pytest.raises(ConversationConflict):
        DocumentTaskInput.from_arguments({"title":"T","first_document":"A","second_document":"B",extra:"x"})


def test_byte_limit_and_invalid_unicode_fail_closed():
    for content in ("é"*6001,"\ud800",""):
        with pytest.raises(ConversationConflict):
            DocumentTaskInput.from_arguments({"title":"T","first_document":content,"second_document":"B"})
