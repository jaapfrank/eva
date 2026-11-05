"""A failed repair POST retains its exact source-bound request, never a retry."""
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from eva.adapters.postgres.component_improvement_actions import ComponentImprovementActions, REPAIR
from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationStopped
from eva.contracts.canonical_json import canonical_sha256

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


class Timeline:
    def __init__(self):
        self.rows = {}

    def events(self, thread):
        return self.rows.get(thread, ())

    def create_thread(self, *, thread_id, data, event_type, **kwargs):
        if thread_id in self.rows:
            raise ConversationConflict("duplicate dispatch fence")
        self.rows[thread_id] = (SimpleNamespace(event_type=event_type,
            payload={"data": deepcopy(data)}),)

    def transition(self, *, thread_id, expected_revision, data, event_type, **kwargs):
        assert len(self.rows[thread_id]) == expected_revision
        self.rows[thread_id] += (SimpleNamespace(event_type=event_type,
            payload={"data": deepcopy(data)}),)


def adapter(*, timeline=None, source=None, spec=None):
    component = ComponentImprovementActions.__new__(ComponentImprovementActions)
    component.source = source or SimpleNamespace(tenant_id="tenant", installation_id="install", principal_id="owner")
    origin = SimpleNamespace(model_dump=lambda **kw: {"tenant_id": "tenant", "installation_id": "install",
        "principal_id": "owner", "conversation_id": "private-chat", "message_id": "private-message"})
    component.spec = spec or SimpleNamespace(work_id="work:repair", execution_id="work:repair",
        source=origin, sha256="a" * 64, source_ingress_sha256="b" * 64)
    component.timeline = timeline or Timeline()
    component.clock = lambda: NOW
    component.authorize = lambda turn: None
    component.dispatch = lambda turn: None
    component.action_names = (REPAIR,)
    component.repair_guard = SimpleNamespace(_repair_incident_id="self-repair:" + "c" * 64,
        repair_role=None, check=lambda turn: None)
    calls = []

    def repair(**request):
        calls.append(deepcopy(request))
        # Proves admission commits before host dispatch, not merely before ack.
        admission = component.timeline.events(component._thread(3))[0].payload["data"]
        assert admission["request"] == request
        assert admission["request_sha256"] == canonical_sha256(request)
        return {"state": "waiting_for_builder", "candidate_sha256": "d" * 64, "installed": False}

    component.evaluator = SimpleNamespace(runtime_repair=repair)
    return component, calls


@pytest.mark.parametrize("action", ["describe", "submit", "status"])
def test_exact_request_precedes_dispatch_and_success_replays_without_post(action):
    component, calls = adapter()
    call = ConversationActionCall("repair-call", REPAIR, {"action": action})
    receipt = component.execute(component.source, call, step_index=3)
    events = component.timeline.events(component._thread(3))
    assert [event.event_type for event in events] == ["RUNTIME_REPAIR_REQUEST_ADMITTED", "COMPONENT_ACTION_RECORDED"]
    admission = events[0].payload["data"]
    assert admission["spec_sha256"] == component.spec.sha256
    assert admission["source_ingress_sha256"] == component.spec.source_ingress_sha256
    assert admission["execution_id"] == component.spec.execution_id
    assert admission["call_id"] == call.call_id and admission["arguments"] == call.arguments
    assert admission["request"]["task_id"] == component.spec.work_id
    assert receipt.output["operation_result"]["candidate_sha256"] == "d" * 64
    assert component.pending(component.source, step_index=3) == receipt
    assert component.execute(component.source, call, step_index=3) == receipt
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["http400", "crash", "lost_ack"])
def test_failure_keeps_request_fence_and_never_reposts(failure):
    component, calls = adapter()
    original = component.evaluator.runtime_repair

    def fail(**request):
        original(**request)
        if failure == "http400":
            raise HTTPError("https://private/credential", 400, "PRIVATE_ERROR_BODY", {}, None)
        if failure == "crash":
            raise SystemExit("simulated process death")
        return {"installed": False}

    component.evaluator.runtime_repair = fail
    if failure == "lost_ack":
        component.timeline.transition = lambda **kw: (_ for _ in ()).throw(OSError("lost database ack"))
    call = ConversationActionCall("repair-call", REPAIR, {"action": "submit"})
    with pytest.raises((HTTPError, SystemExit, OSError)):
        component.execute(component.source, call, step_index=3)
    events = component.timeline.events(component._thread(3))
    assert events[0].payload["data"]["request"] == calls[0]
    if failure == "http400":
        assert events[-1].payload["data"]["http_status"] == 400
    retained = repr([event.payload for event in events])
    assert "PRIVATE_ERROR_BODY" not in retained and "https://private/credential" not in retained
    with pytest.raises(ConversationConflict, match="requires reconciliation"):
        component.pending(component.source, step_index=3)
    with pytest.raises(ConversationConflict, match="requires reconciliation"):
        component.execute(component.source, call, step_index=3)
    assert len(calls) == 1


@pytest.mark.parametrize("stage", ["before_admission", "after_admission"])
def test_stop_never_dispatches_or_clears_an_admitted_request(stage):
    component, calls = adapter()
    def authorize(turn):
        if stage == "before_admission" or component.timeline.events(component._thread(3)):
            raise ConversationStopped("owner STOP")
    component.authorize = authorize
    with pytest.raises(ConversationStopped):
        component.execute(component.source, ConversationActionCall("call", REPAIR, {"action": "submit"}), step_index=3)
    assert not calls
    assert bool(component.timeline.events(component._thread(3))) == (stage == "after_admission")


def test_invalid_private_arguments_never_persist_or_reach_host():
    component, calls = adapter()
    with pytest.raises((ValueError, ConversationConflict)):
        component.execute(component.source, ConversationActionCall("call", REPAIR,
            {"action": "submit", "private": "PASSWORD"}), step_index=3)
    assert not calls and not component.timeline.rows


def test_fence_is_checked_before_dispatch_even_if_other_worker_wins_after_pending():
    component, calls = adapter()
    original = component.pending
    def race(turn, *, step_index):
        result = original(turn, step_index=step_index)
        if result is None:
            component.timeline.create_thread(thread_id=component._thread(3), data={}, event_type="RUNTIME_REPAIR_REQUEST_ADMITTED")
        return result
    component.pending = race
    with pytest.raises(ConversationConflict, match="duplicate dispatch fence"):
        component.execute(component.source, ConversationActionCall("call", REPAIR, {"action": "submit"}), step_index=3)
    assert not calls


def test_unresolved_request_cannot_be_rebound_to_another_original_source():
    component, calls = adapter()
    component.evaluator.runtime_repair = lambda **request: (_ for _ in ()).throw(OSError("offline"))
    with pytest.raises(OSError):
        component.execute(component.source, ConversationActionCall("call", REPAIR, {"action": "status"}), step_index=3)
    component.spec.source_ingress_sha256 = "e" * 64
    with pytest.raises(ConversationConflict, match="source binding differs"):
        component.pending(component.source, step_index=3)
