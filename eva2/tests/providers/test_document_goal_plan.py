from dataclasses import replace
import json

import pytest

from eva.application.durable_goals import DurableGoalError, GoalArtifact
from eva.providers.document_goal_plan import DocumentGoalPlan
from tests.postgres.test_durable_goal_store import NOW, spec, store_with_timeline


def plan():
    goal = replace(spec(), followup_instruction="Use draft")
    return goal, DocumentGoalPlan(
        goal.goal_id, goal.sha256, goal.owner_id, "tenant", "installation", "a" * 64, "b" * 64,
        "/proof-state/draft.txt", "/proof-state/final.txt", "Draft", "Report: ", "\n",
    )


def host():
    return dict(owner_principal_id="jaap", tenant_id="tenant", installation_id="installation",
                authority_configuration_sha256="a" * 64, tool_configuration_sha256="b" * 64)


def test_roundtrip_and_previous_artifact_are_exact():
    goal, value = plan()
    assert DocumentGoalPlan.from_json(json.dumps(value.to_payload())) == value
    value.require_host_binding(goal, **host())
    assert value.final_content(GoalArtifact("first", "Draft", "text/plain", NOW)) == "Report: Draft\n"
    with pytest.raises(DurableGoalError, match="previous"):
        value.final_content(GoalArtifact("first", "Changed", "text/plain", NOW))


@pytest.mark.parametrize("field", list(host()))
def test_request_or_configuration_cannot_replace_host_identity(field):
    goal, value = plan()
    altered = {**host(), field: "c" * 64}
    with pytest.raises(DurableGoalError, match="host-authorized"):
        value.require_host_binding(goal, **altered)


def test_spoofed_owner_and_different_goal_fail_even_with_real_owner_in_plan():
    goal, value = plan()
    for other in (replace(goal, owner_id="attacker"), replace(goal, primary_instruction="Other work")):
        with pytest.raises(DurableGoalError, match="host-authorized"):
            value.require_host_binding(other, **host())


@pytest.mark.parametrize("path", ["/outside/x.txt", "/proof-state/../x.txt", "/proof-state/sub/x.txt",
                                  "/proof-state/.private.txt", "/proof-state/x.py", "/proof-state/x.txt\x00"])
def test_plan_rejects_outside_or_non_document_paths(path):
    _, value = plan()
    with pytest.raises(DurableGoalError, match="path"):
        replace(value, first_path=path)


@pytest.mark.parametrize("change", [
    {"first_content": "é" * 6001}, {"final_prefix": "x" * 12000},
    {"first_content": " "}, {"final_path": "/proof-state/draft.txt"},
    {"goal_sha256": "invalid"}, {"owner_principal_id": 9},
])
def test_plan_rejects_invalid_or_unbounded_data(change):
    _, value = plan()
    with pytest.raises(DurableGoalError):
        replace(value, **change)


def test_unknown_and_duplicate_fields_fail_closed():
    _, value = plan()
    with pytest.raises(DurableGoalError):
        DocumentGoalPlan.from_json(json.dumps({**value.to_payload(), "execute": "anything"}))
    raw = json.dumps(value.to_payload())
    with pytest.raises(DurableGoalError, match="repeats"):
        DocumentGoalPlan.from_json(raw[:-1] + ',"first_content":"Different"}')


def test_goal_timeline_binding_is_immutable_and_preserved_after_stop():
    goal, value = plan()
    store, timeline = store_with_timeline()
    store.admit(goal)
    assert store.bind_document_plan(goal, value.sha256, value.authority_configuration_sha256) == "bound"
    assert store.bind_document_plan(goal, value.sha256, value.authority_configuration_sha256) == "already_bound"
    with pytest.raises(DurableGoalError, match="differs"):
        store.bind_document_plan(goal, "c" * 64, value.authority_configuration_sha256)
    store.cancel(goal.goal_id, goal.owner_id, "Stop", NOW)
    assert store.document_plan_binding(goal) == (value.sha256, value.authority_configuration_sha256)
    assert [row.event_type for row in timeline.rows].count("GOAL_DOCUMENT_PLAN_BOUND") == 1


def test_guard_rereads_host_plan_and_calls_existing_authority_for_each_effect():
    from types import SimpleNamespace
    from eva.providers.document_goals import DocumentGoalAuthority

    goal, value = plan()
    store, _ = store_with_timeline()
    store.admit(goal)
    reads = []
    calls = []
    current = [value]
    revoked = [False]

    def read_plan():
        reads.append(True)
        return json.dumps(current[0].to_payload())

    def authorize_current(**kwargs):
        calls.append(kwargs)
        if revoked[0] and kwargs["phase"] == "execute":
            raise DurableGoalError("existing authority revoked")

    authority = SimpleNamespace(
        owner_principal_id="jaap", configuration_sha256="a" * 64,
        scope=SimpleNamespace(tenant_id="tenant", installation_id="installation"),
        authorize_current=authorize_current,
    )
    guard = DocumentGoalAuthority(store=store, authority=authority, read_plan=read_plan,
                                  tool_configuration_sha256="b" * 64)
    assert guard.authorize_effect(goal) == value
    revoked[0] = True
    with pytest.raises(DurableGoalError, match="revoked"):
        guard.authorize_effect(goal)
    assert guard.authorize_readback(goal) == value
    assert calls == [dict(phase="execute", operation_kind="effect"),
                     dict(phase="execute", operation_kind="effect"),
                     dict(phase="observe", operation_kind="effect")]
    assert len(reads) == 3
    current[0] = replace(value, first_content="Changed")
    with pytest.raises(DurableGoalError, match="changed"):
        guard.authorize_effect(goal)
    assert len(calls) == 3


def test_terminal_goal_cannot_acquire_first_document_binding():
    goal, value = plan()
    store, _ = store_with_timeline()
    store.admit(goal)
    store.cancel(goal.goal_id, goal.owner_id, "Stop", NOW)
    with pytest.raises(DurableGoalError, match="terminal"):
        store.bind_document_plan(goal, value.sha256, value.authority_configuration_sha256)


def test_step_identity_and_binding_are_stable_and_cover_previous_artifact():
    _, value = plan()
    previous = GoalArtifact("receipt:first", "Draft", "text/plain", NOW)
    first = value.step(1)
    second = value.step(2, previous)
    assert first == value.step(1)
    assert second == value.step(2, previous)
    assert first.operation_id != second.operation_id
    assert second.content == "Report: Draft\n"
    assert second.path == value.final_path
    changed_receipt = value.step(2, replace(previous, artifact_id="receipt:other"))
    assert changed_receipt.operation_id == second.operation_id
    assert changed_receipt.request_binding_sha256 != second.request_binding_sha256
    # Same operation with a conflicting request binding must fail at the provider.
    assert replace(value, tool_configuration_sha256="c" * 64).step(1) != first
    assert replace(value, first_path="/proof-state/other.txt").step(1) != first
    for number, artifact in ((1, previous), (2, None), (3, None), (True, None)):
        with pytest.raises(DurableGoalError):
            value.step(number, artifact)


def test_bootstrap_selects_exact_goal_without_scanning_queue(monkeypatch, private_tmp_path):
    from eva.bootstrap import document_goal_worker

    goal, value = plan()
    store, _ = store_with_timeline()
    store.admit(goal)
    path = private_tmp_path / "document-plan.json"
    path.write_text(json.dumps(value.to_payload())); path.chmod(0o600)
    monkeypatch.setenv("EVA_DOCUMENT_GOAL_PLAN_FILE", str(path))
    received = {}

    def loop(**hooks):
        received.update(hooks)
        return 7

    monkeypatch.setattr(document_goal_worker, "run_loop", loop)
    assert document_goal_worker.main() == 7
    assert received["select_goals"](store, NOW) == (goal,)
    assert received["failure_handler"](store, goal, RuntimeError(), NOW) == "needs_help"
    assert received["select_goals"](store, NOW) == ()
