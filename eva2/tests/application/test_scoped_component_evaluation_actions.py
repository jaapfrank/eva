"""Scoped adapters use their real saved draft, current action and unchanged guards."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.component_improvement_actions import ComponentImprovementActions, DRAFT, EVALUATE
from eva.application.component_development import WORK_STRATEGY_EVALUATION_PATH, make_scoped_component_evaluation_binding
from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationStopped
from eva.contracts.canonical_json import canonical_sha256


class Timeline:
    def __init__(self):
        self.rows = {}

    def events(self, thread_id):
        return self.rows.get(thread_id, ())

    def create_thread(self, *, thread_id, checkpoint_id, occurred_at, phase, status, event_type, data):
        assert thread_id not in self.rows
        payload = {"checkpoint": {"checkpoint_id": checkpoint_id, "status": status, "phase": phase},
                   "data": deepcopy(data)}
        event = SimpleNamespace(thread_id=thread_id, event_type=event_type,
            payload=payload, payload_sha256=canonical_sha256(payload))
        self.rows[thread_id] = (event,)
        return event


def adapter(path=WORK_STRATEGY_EVALUATION_PATH, *, registered=True):
    component = ComponentImprovementActions.__new__(ComponentImprovementActions)
    source = SimpleNamespace(tenant_id="tenant", installation_id="installation", principal_id="owner")
    origin = SimpleNamespace(model_dump=lambda **kw: {"tenant_id": "tenant", "installation_id": "installation",
        "principal_id": "owner", "conversation_id": "private-conversation", "message_id": "original"})
    component.spec = SimpleNamespace(work_id="original-work", execution_id="original-work",
        source=origin, sha256="a" * 64, source_ingress_sha256="b" * 64,
        deadline="2026-10-12T12:15:00Z", grant=SimpleNamespace(timeout_seconds=1200))
    component.source = source
    component.timeline = Timeline()
    checks = []
    component.authorize = lambda turn: checks.append(("authorize", turn))
    component.dispatch = lambda turn: checks.append(("dispatch", turn))
    component.clock = lambda: "2026-10-12T12:00:00Z"
    component.action_names = (DRAFT, EVALUATE)
    component._source = lambda p: ({"path": p, "source_sha256": "e" * 64}, None)
    draft = {"path": path, "source_sha256": "e" * 64, "candidate_sha256": "f" * 64,
        "candidate_content": "candidate literal", "draft_step": 2}
    draft_call = ConversationActionCall("draft-original", DRAFT, {"path": path})
    component.timeline.create_thread(thread_id=component._thread(2), checkpoint_id=component._thread(2) + ":saved",
        occurred_at=component.clock(), phase="recorded", status="completed", event_type="COMPONENT_ACTION_RECORDED",
        data={"call_id": draft_call.call_id, "name": draft_call.name, "arguments": draft_call.arguments,
              "output": {"status": "verified", "operation_result": draft, "component_step": 2}})
    calls = []
    def evaluate(value, **kwargs):
        calls.append((deepcopy(value), kwargs))
        return {"contract": "synthetic-static-report"}
    component.evaluator = SimpleNamespace(evaluate=evaluate, suite_sha256="0" * 64,
        suite_bindings=((path, "1" * 64),), work_execution_paths=(path,) if registered else ())
    return component, checks, calls


def test_binding_uses_original_draft_event_spec_and_selected_suite_and_replay_is_free():
    component, checks, calls = adapter()
    call = ConversationActionCall("evaluation-original", EVALUATE, {"draft_step": 2})
    event = component.timeline.events(component._thread(2))[0]
    receipt = component.execute(component.source, call, step_index=3)
    binding = calls[0][1]["execution_binding"]
    assert binding == make_scoped_component_evaluation_binding(task_id="original-work", draft_step=2,
        evaluation_step=3, evaluation_call_id="evaluation-original", spec_sha256="a" * 64,
        source_ingress_sha256="b" * 64, draft_thread_id=component._thread(2),
        draft_event_payload_sha256=event.payload_sha256, path=WORK_STRATEGY_EVALUATION_PATH,
        baseline_sha256="e" * 64, candidate_sha256="f" * 64, suite_sha256="1" * 64)
    assert receipt.output["status"] == "verified"
    assert calls[0][1]["remaining_seconds"] == 900
    assert checks[-1] == ("authorize", component.source)
    assert sum(kind == "dispatch" for kind, turn in checks) == 1
    assert all(turn is component.source for kind, turn in checks)
    assert component.execute(component.source, call, step_index=3) == receipt
    assert len(calls) == 1
    assert sum(kind == "dispatch" for kind, turn in checks) == 1


@pytest.mark.parametrize("case", ["unverified", "wrong-thread", "wrong-step", "wrong-path", "tampered-hash", "extra-event"])
def test_unverified_or_misbound_draft_is_refused_before_evaluator(case):
    component, _, calls = adapter()
    thread = component._thread(2)
    event = component.timeline.rows[thread][0]
    if case == "unverified": event.payload["data"]["output"]["status"] = "unconfirmed"
    if case == "wrong-thread": event.thread_id = "component-draft:" + "0" * 64
    if case == "wrong-step": event.payload["data"]["output"]["operation_result"]["draft_step"] = 1
    if case == "wrong-path": event.payload["data"]["arguments"]["path"] = "another.py"
    if case == "tampered-hash": event.payload_sha256 = "0" * 64
    else: event.payload_sha256 = canonical_sha256(event.payload)
    if case == "extra-event": component.timeline.rows[thread] = (event, event)
    with pytest.raises(ConversationConflict):
        component.execute(component.source, ConversationActionCall("eval", EVALUATE, {"draft_step": 2}), step_index=3)
    assert not calls


@pytest.mark.parametrize("path", ["application/ordinary.py", "saved_artifact_" + "c" * 64 + ".py", WORK_STRATEGY_EVALUATION_PATH])
def test_unregistered_static_and_saved_routes_keep_existing_client_signature(path):
    component, _, calls = adapter(path, registered=False)
    def legacy_evaluate(draft):
        calls.append(draft)
        return {"contract": "legacy-report"}
    component.evaluator.evaluate = legacy_evaluate
    component.execute(component.source, ConversationActionCall("eval", EVALUATE, {"draft_step": 2}), step_index=3)
    assert len(calls) == 1


def test_other_original_turn_has_no_dispatch_and_owner_stop_keeps_existing_exception():
    component, checks, calls = adapter()
    call = ConversationActionCall("eval", EVALUATE, {"draft_step": 2})
    with pytest.raises(ConversationConflict):
        component.execute(SimpleNamespace(principal_id="another-owner"), call, step_index=3)
    assert not calls and all(kind != "dispatch" for kind, _ in checks)
    def stopped(turn):
        raise ConversationStopped("owner stop")
    component.authorize = stopped
    with pytest.raises(ConversationStopped):
        component.execute(component.source, call, step_index=3)
    assert not calls


def test_source_changed_after_draft_cannot_be_rebound_and_model_cannot_supply_binding():
    component, _, calls = adapter()
    component._source = lambda p: ({"path": p, "source_sha256": "2" * 64}, None)
    with pytest.raises(ConversationConflict, match="source binding changed"):
        component.execute(component.source, ConversationActionCall("eval", EVALUATE, {"draft_step": 2}), step_index=3)
    component._source = lambda p: ({"path": p, "source_sha256": "e" * 64}, None)
    with pytest.raises(ConversationConflict):
        component.execute(component.source, ConversationActionCall("eval", EVALUATE,
            {"draft_step": 2, "execution_binding": {"task_id": "forged"}}), step_index=3)
    assert not calls


@pytest.mark.parametrize("deadline", ["2026-10-12T11:59:59Z", "2026-10-12T12:00:00Z"])
def test_expired_original_work_starts_no_host_evaluation(deadline):
    component, _, calls = adapter()
    component.spec.deadline = deadline
    with pytest.raises(ConversationConflict, match="deadline expired"):
        component.execute(component.source,
            ConversationActionCall("evaluation-expired", EVALUATE, {"draft_step": 2}), step_index=3)
    assert not calls


def test_transport_wait_cannot_exceed_original_grant():
    component, _, calls = adapter()
    component.spec.grant.timeout_seconds = 420
    component.execute(component.source,
        ConversationActionCall("evaluation-bounded", EVALUATE, {"draft_step": 2}), step_index=3)
    assert calls[0][1]["remaining_seconds"] == 420
