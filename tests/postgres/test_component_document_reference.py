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


@pytest.mark.parametrize("state,matching,available", [
    ("available", True, True), ("consumed", True, False), ("available", False, False)])
def test_source_read_reports_exact_current_round_and_preserves_replay(database, tmp_path, state, matching, available):
    from eva.adapters.postgres.component_improvement_actions import READ
    from eva.contracts.canonical_json import canonical_sha256
    turn = ConversationTurn.private("component-round-read-" + state + str(matching),
        "installation", "owner", "conversation", "message", "Read own component", "2026-09-24T12:00:00Z")
    PostgresConversationJournal(database, external_timeline_scope(
        turn.tenant_id, turn.installation_id, "test")).admit(turn)
    source = "VALUE = 1\n"
    (tmp_path / "example.py").write_text(source)
    identity = canonical_sha256({k:getattr(turn,k) for k in
                                ("tenant_id", "installation_id", "principal_id")})
    calls = []
    class Evaluator:
        final_assessment_scopes = (("example.py", identity),)
        def round_status(self, path, *, scope_sha256):
            assert path == "example.py" and scope_sha256 == identity
            calls.append((path, scope_sha256))
            return {"contract":"EvaComponentImprovementRound/v1", "path":path,
                "scope_sha256":identity, "registration_sha256":"e"*64,
                "baseline_sha256":hashlib.sha256(source.encode()).hexdigest() if matching else "f"*64,
                "source_target_sha256":None, "state":state, "independent_final":True, "installed":False}
    spec = SimpleNamespace(execution_id="execution:round-read", source=SimpleNamespace(
        model_dump=lambda **kw:{"message":turn.message_id}))
    instance = ComponentImprovementActions(database, spec, turn, lambda value:None,
        lambda value:None, lambda:NOW)
    instance.root, instance.evaluator = tmp_path, Evaluator()
    call = ConversationActionCall("source-read", READ, {"path":"example.py", "start_line":1})
    receipt = instance.execute(turn, call, step_index=1)
    result = receipt.output["operation_result"]
    assert result["registered_improvement_available"] is available
    assert result["improvement_round"]["state"] == state
    assert result["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert instance.execute(turn, call, step_index=1) == receipt
    assert calls == [("example.py", identity)]
    with pytest.raises(ConversationConflict, match="scope differs"):
        other = ConversationTurn.private(turn.tenant_id, turn.installation_id,
            "other-person", turn.conversation_id, turn.message_id, turn.text,
            "2026-09-24T12:00:00Z")
        instance.execute(other, call, step_index=2)


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
    call = ConversationActionCall("save", "save_workspace_document", {"path": "/allowed/proposal.json", "component_step": 1, "content": None})
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
                            ({"component_step": 1, "content": "wrong"}, 2),
                            ({"component_step": 1, "content": ""}, 2),
                            ({"component_step": None, "content": None}, 2), ({}, 2)]:
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

    empty_call = replace(call, arguments={"path": "/allowed/proposal.json", "component_step": None, "content": ""})
    assert component().resolve_document_call(turn, empty_call, 3).arguments == {"path": "/allowed/proposal.json", "content": ""}


def test_candidate_continuation_is_durable_and_cannot_cross_task(database, tmp_path):
    from eva.adapters.postgres.component_improvement_actions import EVALUATE, PREPARE
    from tests.application.test_component_improvements import arguments, report

    turn = ConversationTurn.private("component-continuation-test", "installation", "owner", "conversation",
                                    "message", "Improve a component", "2026-09-24T12:00:00Z")
    scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "test")
    PostgresConversationJournal(database, scope).admit(turn)
    dispatched = []
    class Evaluator:
        def evaluate(self, draft):
            assert draft["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
            return report(draft)
    def component(execution="execution:continuation"):
        spec = SimpleNamespace(execution_id=execution,
                               source=SimpleNamespace(model_dump=lambda **kw: {"message": turn.message_id}))
        instance = ComponentImprovementActions(database, spec, turn, lambda value: None,
            lambda value: dispatched.append(value), lambda: NOW)
        instance.root, instance.evaluator = tmp_path, Evaluator()
        instance.action_names = (DRAFT, EVALUATE, PREPARE)
        return instance
    source = "VALUE = 1\nSTYLE = 'plain'\n"
    (tmp_path / "example.py").write_text(source)
    first = component().execute(turn, ConversationActionCall("first", DRAFT,
        {**arguments(source, "VALUE = 1", "VALUE = 2"), "parent_draft_step": None}), step_index=1)
    child_args = {**arguments(first.output["operation_result"]["candidate_content"], "'plain'", "'warm'"),
                  "parent_draft_step": 1}
    call = ConversationActionCall("child", DRAFT, child_args)
    child = component().execute(turn, call, step_index=2)
    # A fresh adapter obtains the exact stored parent and child through PostgreSQL.
    assert component().pending(turn, step_index=1) == first
    assert component().execute(turn, call, step_index=2) == child
    assert len(dispatched) == 2
    with pytest.raises(ConversationConflict, match="does not exist in this task"):
        component("execution:other").execute(turn, call, step_index=2)
    with pytest.raises(ConversationConflict, match="scope differs"):
        component().execute(replace(turn, message_id="other"), call, step_index=2)
    with pytest.raises(ConversationConflict, match="earlier step"):
        component().execute(turn, replace(call, arguments={**child_args, "parent_draft_step": 3}), step_index=3)
    evaluated = component().execute(turn, ConversationActionCall("evaluate", EVALUATE, {"draft_step": 2}), step_index=3)
    prepared = component().execute(turn, ConversationActionCall("prepare", PREPARE, {"evaluation_step": 3}), step_index=4)
    package = prepared.output["operation_result"]
    assert package["candidate_lineage"]["parent_draft_step"] == 1
    assert package["baseline_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert package["candidate_sha256"] == evaluated.output["operation_result"]["candidate_sha256"]
    assert package["installed"] is False


def test_measured_selection_continuation_and_replay_use_same_task_winner(database, tmp_path):
    from eva.adapters.postgres.component_improvement_actions import EVALUATE, COMPARE
    from tests.application.test_component_improvements import arguments
    from tests.application.test_component_development import entry

    turn = ConversationTurn.private("component-choice-test", "installation", "owner",
        "conversation", "message", "Improve a component", "2026-09-24T12:00:00Z")
    PostgresConversationJournal(database, external_timeline_scope(
        turn.tenant_id, turn.installation_id, "test")).admit(turn)
    source = "VALUE = 1\n"
    (tmp_path / "example.py").write_text(source)
    dispatched = []
    class Evaluator:
        def evaluate(self, draft):
            value = entry(2, hits=3 if "VALUE = 3" in draft["candidate_content"] else 1)["report"]
            value.update(relative_path=draft["path"], baseline_sha256=draft["source_sha256"],
                         candidate_sha256=draft["candidate_sha256"])
            return value
    def component(execution="selection"):
        spec = SimpleNamespace(execution_id=execution,
            source=SimpleNamespace(model_dump=lambda **kw: {"message": turn.message_id}))
        instance = ComponentImprovementActions(database, spec, turn, lambda value: None,
            lambda value: dispatched.append(value), lambda: NOW)
        instance.root, instance.evaluator = tmp_path, Evaluator()
        instance.action_names = (DRAFT, EVALUATE, COMPARE)
        return instance
    first = component().execute(turn, ConversationActionCall("first", DRAFT,
        arguments(source, "VALUE = 1", "VALUE = 2")), step_index=1)
    best = component().execute(turn, ConversationActionCall("best", DRAFT,
        arguments(source, "VALUE = 1", "VALUE = 3")), step_index=2)
    component().execute(turn, ConversationActionCall("eval1", EVALUATE, {"draft_step": 1}), step_index=3)
    component().execute(turn, ConversationActionCall("eval2", EVALUATE, {"draft_step": 2}), step_index=4)
    compare_call = ConversationActionCall("choose", COMPARE, {"evaluation_steps": [3, 4]})
    chosen = component().execute(turn, compare_call, step_index=5)
    choice = chosen.output["operation_result"]
    assert choice["selected_draft_step"] == 2
    assert choice["selected_candidate_sha256"] == best.output["operation_result"]["candidate_sha256"]
    assert component().execute(turn, compare_call, step_index=5) == chosen
    assert len(dispatched) == 5
    child_call = ConversationActionCall("continue", DRAFT, {
        **arguments(best.output["operation_result"]["candidate_content"], "VALUE = 3", "VALUE = 4"),
        "parent_draft_step": 2, "selection_step": 5})
    child = component().execute(turn, child_call, step_index=6)
    assert child.output["operation_result"]["candidate_lineage"]["parent_draft_step"] == 2
    assert component().execute(turn, child_call, step_index=6) == child
    with pytest.raises(ConversationConflict, match="selected development winner"):
        component().execute(turn, replace(child_call, arguments={
            **arguments(first.output["operation_result"]["candidate_content"], "VALUE = 2", "VALUE = 4"),
            "parent_draft_step": 1, "selection_step": 5}), step_index=7)
    with pytest.raises(ConversationConflict):
        component("foreign").execute(turn, child_call, step_index=7)
    with pytest.raises(ConversationConflict):
        component().execute(turn, replace(compare_call, arguments={"evaluation_steps": [4, 4]}), step_index=7)


def test_selected_release_handoff_is_durable_and_bound_to_actual_task(database, tmp_path):
    from eva.adapters.postgres.component_improvement_actions import EVALUATE, COMPARE, PREPARE
    from eva.contracts.canonical_json import canonical_sha256
    from tests.application.test_component_improvements import arguments
    from tests.application.test_component_development import entry
    turn = ConversationTurn.private("component-final-test", "installation", "owner",
        "conversation", "message", "Improve a component", "2026-09-24T12:00:00Z")
    PostgresConversationJournal(database, external_timeline_scope(
        turn.tenant_id, turn.installation_id, "test")).admit(turn)
    source = "VALUE = 1\n"
    (tmp_path / "example.py").write_text(source)
    scope = canonical_sha256({k: getattr(turn,k) for k in ("tenant_id","installation_id","principal_id")})
    calls = []
    class Evaluator:
        final_assessment_scopes = (("example.py",scope),)
        def evaluate(self, draft):
            value=entry(2,hits=3)["report"]
            value.update(relative_path=draft["path"],baseline_sha256=draft["source_sha256"],
                         candidate_sha256=draft["candidate_sha256"])
            return value
        def assess(self, package, **kw):
            assert kw["task_id"] == "work:component-final"
            assert kw["scope_sha256"] == scope
            calls.append((package,kw))
            return {"review_id":package["candidate_package_sha256"],"state":"queued","installed":False}
    def component(execution="final"):
        spec=SimpleNamespace(execution_id=execution,work_id="work:component-final",
            source=SimpleNamespace(model_dump=lambda **kw: {"message":turn.message_id}))
        value=ComponentImprovementActions(database,spec,turn,lambda value:None,lambda value:None,lambda:NOW)
        value.root,value.evaluator=tmp_path,Evaluator()
        value.action_names=(DRAFT,EVALUATE,COMPARE,PREPARE)
        return value
    component().execute(turn,ConversationActionCall("draft",DRAFT,
        arguments(source,"VALUE = 1","VALUE = 3")),step_index=1)
    component().execute(turn,ConversationActionCall("eval",EVALUATE,{"draft_step":1}),step_index=2)
    choice=component().execute(turn,ConversationActionCall("compare",COMPARE,{"evaluation_steps":[2]}),step_index=3)
    call=ConversationActionCall("release",PREPARE,{"evaluation_step":2,"selection_step":3})
    result=component().execute(turn,call,step_index=4)
    assert component().execute(turn,call,step_index=4)==result and len(calls)==1
    package=result.output["operation_result"]["package"]
    assert package["development_selection"]==choice.output["operation_result"]
    assert package["draft_step"]==1
    assert result.output["operation_result"]["final_assessment_handoff"]["installed"] is False
    with pytest.raises(ConversationConflict):
        component("other").execute(turn,call,step_index=5)
    with pytest.raises(ConversationConflict):
        component().execute(turn,replace(call,arguments={"evaluation_step":2,"selection_step":2}),step_index=5)
    # An explicitly unselected legacy package never enters independent assessment.
    legacy=component().execute(turn,replace(call,call_id="legacy",arguments={
        "evaluation_step":2,"selection_step":None}),step_index=6)
    assert legacy.output["operation_result"]["contract"]=="EvaComponentReleaseCandidate/v1"
    assert len(calls)==1
