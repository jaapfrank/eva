"""Persist component results through the unchanged document write route."""
from datetime import UTC, datetime
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.component_improvement_actions import ComponentImprovementActions, DRAFT
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationToolPreparation, ConversationTurn
from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
from eva.bootstrap.delegated_digital_runner import _has_completed_tool_evidence
from tests.postgres.test_scheduled_failure_return import database

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


def test_exact_component_document_is_durable_and_replay_keeps_original_call(database, tmp_path):
    turn = ConversationTurn.private("component-test", "installation", "owner", "conversation", "message",
                                    "Improve a component", "2026-09-24T12:00:00Z")
    scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "test")
    PostgresConversationJournal(database, scope).admit(turn)
    spec = SimpleNamespace(execution_id="execution:first", source=SimpleNamespace(model_dump=lambda **kw: {"message": turn.message_id}))
    def component(execution="execution:first"):
        instance = ComponentImprovementActions(database, SimpleNamespace(execution_id=execution, source=spec.source),
            turn, lambda value: None, lambda value: None, lambda: NOW)
        instance.root = tmp_path
        return instance
    source = "VALUE = 1\n"
    (tmp_path / "example.py").write_text(source)
    draft = component().execute(turn, ConversationActionCall("draft", DRAFT, {
        "path": "example.py", "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "old_text": "VALUE = 1", "new_text": "VALUE = 2", "problem": "controlled fixture",
        "expected_gain": "fixture only", "verification_plan": "controlled readback"}), step_index=1)
    writes, preparations = [], []
    class Planner:
        action_names = ("save_workspace_document",)
        async def prepare(self, actual, call):
            assert actual == turn
            assert "component_step" not in call.arguments
            assert len(call.arguments["content"]) <= 12000
            assert call.arguments["path"] == "/allowed/proposal.json"
            preparations.append(call)
            return ConversationToolPreparation("fixture.document", call.arguments, {"saved": True}, {})
    class Provider:
        async def execute(self, request):
            writes.append(request.arguments)
            return ToolDispatchResult(True, "document:fixture", {})
        async def observe(self, request, dispatch):
            assert request.arguments == writes[-1]
            return ToolProofObservation("verified", {"saved": True}, {})
    original = PostgresExternalConversationTools(database, Planner(), Provider())
    call = ConversationActionCall("save", "save_workspace_document", {"path": "/allowed/proposal.json", "component_step": 1})
    route = original.with_preparation_call_resolver(component().resolve_document_call)
    result = route.execute(turn, call, step_index=2)
    assert result.call == call and result.output["status"] == "verified"
    assert json.loads(writes[0]["content"]) == draft.output["operation_result"]
    assert _has_completed_tool_evidence([draft, result])
    restarted = original.with_preparation_call_resolver(component().resolve_document_call)
    assert restarted.execute(turn, call, step_index=2) == result
    assert restarted.pending(turn, step_index=2) == result
    assert len(writes) == len(preparations) == 1
    with pytest.raises(ConversationConflict, match="replay differs"):
        restarted.execute(turn, replace(call, arguments={"path": "/allowed/other.json", "component_step": 1}), step_index=2)
    assert original._preparation_call_resolver is None
    for arguments, step in [({"component_step": 2}, 2), ({"component_step": True}, 2),
                            ({"component_step": -1}, 2), ({"component_step": 0}, 2),
                            ({"component_step": 1, "content": "wrong"}, 2), ({}, 2)]:
        with pytest.raises(ConversationConflict):
            component().resolve_document_call(turn, replace(call, arguments=arguments), step)
    with pytest.raises(ConversationConflict, match="does not exist"):
        component("execution:other").resolve_document_call(turn, call, 2)
    with pytest.raises(ConversationConflict, match="source differs"):
        component().resolve_document_call(replace(turn, message_id="other-message"), call, 2)
    # Expansion never bypasses the existing planner's destination check.
    with pytest.raises(AssertionError):
        route.execute(turn, replace(call, call_id="bad-path", arguments={"path": "/outside", "component_step": 1}), step_index=3)
    assert len(writes) == 1
