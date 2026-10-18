"""Explicit native recovery preserves its original source and durable fences."""
from dataclasses import replace
from datetime import timedelta
import json

import pytest
from pydantic import ValidationError

from tests.postgres.test_native_initiative_digital_work import (
    native_digital, admitted, started, result, costless_native, NOW,
)
from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied, execution_turn
from eva.adapters.postgres.delegated_work import DelegatedWorkConflict
from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
from eva.contracts.delegated_work import load_delegated_work_spec
from eva.contracts.canonical_json import canonical_sha256


def failed(f, *, ambiguous=False):
    _, work = admitted(f)
    call = started(f, work)
    f.store.record_failure(call, usage=result(call).usage, error_code="internal_error",
        error_fingerprint="f" * 64, ambiguous=ambiguous, now=NOW)
    return f.store.load(work.spec.work_id, **f.service._owner)


def extend_owner_retention(f):
    import sqlalchemy as sa
    from eva.adapters.postgres.current_catalog import metadata
    with f.control._engine.begin() as conn:
        for name in ("principal_bindings", "principals"):
            table = metadata.tables["eva_core." + name]
            conn.execute(sa.update(table).where(
                table.c.tenant_id == f.control._authority.tenant_id,
                table.c.principal_id == f.control._owner).values(retention_until=NOW + timedelta(days=30)))


def resume(f, work):
    turn = replace(execution_turn(f.claim), conversation_id="operator:private-recovery",
        message_id="operator:recovery-1", text="Explicit operator-assisted recovery")
    return f.service.execute("resume", turn, task=None, work_id=work.spec.work_id,
        request_key="operator:recovery-1")


def test_explicit_resume_preserves_origin_and_completes_original_parent(native_digital):
    f = native_digital
    old = failed(f)
    old_events = tuple(f.auth.timeline.events(f.claim.thread_id))
    answer = resume(f, old)
    assert answer["created"] is True
    assert answer["recovery"]["spontaneous_admission"] is False
    work = f.store.load(answer["work"]["work_id"], **f.service._owner)
    assert type(work.spec) is DelegatedInitiativeDigitalWorkSpec
    assert load_delegated_work_spec(work.spec.to_payload()) == work.spec
    assert work.spec.generation == 2 and work.spec.parent_task_id == old.spec.work_id
    for field in ("origin", "source", "source_ingress_sha256", "execution_id", "episode_id", "grant"):
        assert getattr(work.spec, field) == getattr(old.spec, field)
    guard, turn = f.auth.bind_work(work.spec)
    assert turn == execution_turn(f.claim)
    assert f.auth.work_memory_source(work, now=NOW) == turn
    with pytest.raises(InitiativeMandateDenied): f.auth.bind_work(old.spec)
    events = f.auth.timeline.events(f.claim.thread_id)
    assert tuple(events[:len(old_events)]) == old_events
    data = events[-1].payload["data"]
    assert data["recovery_message_id"] == "operator:recovery-1"
    assert data["recovery_conversation_id"] == "operator:private-recovery"
    assert data["previous_work_id"] == old.spec.work_id
    assert data["previous_spec_sha256"] == old.spec.sha256
    assert resume(f, old)["created"] is False
    assert f.auth.timeline.events(f.claim.thread_id) == events
    call = started(f, work)
    saved = f.store.record_success(call, result(call), now=NOW)
    assert f.control.artifact(f.claim.intent_id, now=NOW)["content"] == saved.result.output
    assert f.auth.work_memory_source(saved, now=NOW) == turn
    assert f.auth.retained_procedure_source(source=work.spec.source.model_dump(mode="json"), now=NOW) == turn
    from eva.adapters.postgres.initiative_source_outcomes import _current_work
    assert _current_work(f.auth, work.spec.work_id, NOW)[0] == saved
    assert f.store.load(old.spec.work_id, **f.service._owner) == old
    assert resume(f, old)["created"] is False


@pytest.mark.parametrize("boundary", ["ambiguous", "stop", "revoke", "disabled", "changed_mandate", "expired"])
def test_native_resume_keeps_current_stop_privacy_and_uncertainty_fences(native_digital, boundary):
    f = native_digital
    old = failed(f, ambiguous=boundary == "ambiguous")
    if boundary == "stop": f.control.stop(f.claim.intent_id, now=NOW)
    elif boundary == "revoke": f.auth.revoke()
    elif boundary in {"disabled", "changed_mandate"}:
        if boundary == "disabled": f.policy["enabled"] = False
        else: f.policy["digital_work"]["capability_ids"] = []
        f.path.write_text(json.dumps(f.policy))
    elif boundary == "expired":
        extend_owner_retention(f)
        f.service._clock = lambda: old.retention_until
    before = tuple(f.auth.timeline.events(f.claim.thread_id))
    with pytest.raises((InitiativeMandateDenied, DelegatedWorkConflict, ValueError)):
        resume(f, old)
    assert tuple(f.auth.timeline.events(f.claim.thread_id)) == before


@pytest.mark.parametrize("fault", ["queue", "parent"])
def test_native_resume_is_atomic(native_digital, monkeypatch, fault):
    import eva.bootstrap.native_initiative_work as bridge
    f = native_digital
    old = failed(f)
    events = tuple(f.auth.timeline.events(f.claim.thread_id))
    def fail(*a, **k): raise RuntimeError("synthetic recovery crash")
    monkeypatch.setattr(f.store if fault == "queue" else bridge,
        "admit" if fault == "queue" else "transition_timeline_thread", fail)
    with pytest.raises(RuntimeError, match="recovery crash"): resume(f, old)
    assert tuple(f.auth.timeline.events(f.claim.thread_id)) == events
    assert len(f.store.workboard(**f.service._owner, now=NOW).cards) == 1


@pytest.mark.parametrize("field,value", [("execution_id", "work:eva-resume:00000000-0000-0000-0000-000000000000"),
    ("parent_task_id", "personal-interest:" + "f" * 64), ("work_id", "work:eva-initiative:" + "a" * 64)])
def test_native_resume_contract_cannot_rewrite_original_execution(native_digital, field, value):
    f = native_digital
    old = failed(f)
    answer = resume(f, old)
    work = f.store.load(answer["work"]["work_id"], **f.service._owner)
    payload = work.spec.to_payload()
    payload[field] = value
    with pytest.raises(ValidationError): DelegatedInitiativeDigitalWorkSpec.model_validate(payload)


@pytest.mark.parametrize("boundary", ["expired", "stop", "revoked"])
def test_idempotent_resume_rechecks_current_time_and_stop(native_digital, boundary):
    f = native_digital
    old = failed(f)
    resume(f, old)
    if boundary == "expired":
        extend_owner_retention(f)
        f.service._clock = lambda: old.retention_until
    elif boundary == "stop": f.control.stop(f.claim.intent_id, now=NOW)
    else: f.auth.revoke()
    before = tuple(f.auth.timeline.events(f.claim.thread_id))
    with pytest.raises((InitiativeMandateDenied, DelegatedWorkConflict)):
        resume(f, old)
    assert tuple(f.auth.timeline.events(f.claim.thread_id)) == before


def test_concurrent_duplicate_resume_admits_one_generation(native_digital):
    from concurrent.futures import ThreadPoolExecutor
    f = native_digital
    old = failed(f)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: resume(f, old), range(2)))
    assert sorted(a["created"] for a in answers) == [False, True]
    assert answers[0]["work"]["work_id"] == answers[1]["work"]["work_id"]
    events = f.auth.timeline.events(f.claim.thread_id)
    assert sum(e.event_type == "PERSONAL_INITIATIVE_WORK_RESUMED" for e in events) == 1


def test_second_known_failure_can_resume_same_original_execution(native_digital):
    f = native_digital
    old = failed(f)
    answer = resume(f, old)
    second = f.store.load(answer["work"]["work_id"], **f.service._owner)
    call = started(f, second)
    f.store.record_failure(call, usage=result(call).usage, error_code="internal_error",
        error_fingerprint="e" * 64, ambiguous=False, now=NOW)
    second = f.store.load(second.spec.work_id, **f.service._owner)
    third_answer = resume(f, second)
    third = f.store.load(third_answer["work"]["work_id"], **f.service._owner)
    assert third.spec.generation == 3 and third.spec.parent_task_id == second.spec.work_id
    assert third.spec.execution_id == old.spec.execution_id
    assert third.spec.origin == old.spec.origin
    f.auth.bind_work(third.spec)


def revise_mandate(f):
    old_digest = f.auth.policy()[1]
    f.policy["authorization_source"] += "; explicit owner-approved revision"
    f.path.write_text(json.dumps(f.policy))
    new_digest = canonical_sha256({k: v for k, v in f.policy.items() if k != "enabled"})
    return old_digest, new_digest


def test_explicit_admitted_revision_resumes_same_origin_with_current_guard(native_digital):
    f = native_digital
    old = failed(f)
    original_payload = old.spec.to_payload()
    first_answer = resume(f, old)
    second = f.store.load(first_answer["work"]["work_id"], **f.service._owner)
    old_guard, turn = f.auth.bind_work(second.spec)
    call = started(f, second)
    f.store.record_failure(call, usage=result(call).usage, error_code="incomplete_work",
        error_fingerprint="e" * 64, ambiguous=False, now=NOW)
    second = f.store.load(second.spec.work_id, **f.service._owner)
    previous_digest, current_digest = revise_mandate(f)
    with pytest.raises(InitiativeMandateDenied, match="owner admission required"):
        resume(f, second)
    with pytest.raises(InitiativeMandateDenied, match="previous mandate revision differs"):
        f.auth.admit_revision("f" * 64, current_digest)
    assert f.auth.admit_revision(previous_digest, current_digest)["status"] == "revised"
    with pytest.raises(InitiativeMandateDenied): old_guard.check(turn)
    with pytest.raises(InitiativeMandateDenied): f.auth.bind_work(second.spec)
    answer = resume(f, second)
    third = f.store.load(answer["work"]["work_id"], **f.service._owner)
    assert third.spec.generation == 3
    assert third.spec.origin == old.spec.origin
    assert third.spec.origin.mandate_sha256 == previous_digest != current_digest
    assert third.spec.execution_id == old.spec.execution_id
    assert old.spec.to_payload() == original_payload
    event = f.auth.timeline.events(f.claim.thread_id)[-1]
    assert event.payload["data"]["mandate_sha256"] == current_digest
    assert event.payload["data"]["previous_mandate_sha256"] == previous_digest
    guard, actual_turn = f.auth.bind_work(third.spec)
    assert actual_turn == turn
    assert guard.mandate_sha == current_digest
    assert f.auth._work_mandate(third.spec) == current_digest
    call = started(f, third)
    saved = f.store.record_success(call, result(call), now=NOW)
    assert f.control.artifact(f.claim.intent_id, now=NOW)["content"] == saved.result.output
    assert f.auth.work_memory_source(saved, now=NOW) == turn
    assert f.auth.retained_procedure_source(source=third.spec.source.model_dump(mode="json"), now=NOW) == turn
    assert f.auth.reviewed_work_current(third.spec.work_id, now=NOW) == (saved, turn)
    assert f.store.load(old.spec.work_id, **f.service._owner).spec.to_payload() == original_payload


@pytest.mark.parametrize("stop", ["parent", "mandate"])
def test_explicit_revision_does_not_override_stop_or_revoke(native_digital, stop):
    f = native_digital
    old = failed(f)
    before, after = revise_mandate(f)
    f.auth.admit_revision(before, after)
    if stop == "parent": f.control.stop(f.claim.intent_id, now=NOW)
    else: f.auth.revoke()
    with pytest.raises(InitiativeMandateDenied): resume(f, old)


def test_revision_admission_cannot_borrow_another_previous_digest(native_digital):
    f = native_digital
    old = failed(f)
    before, after = revise_mandate(f)
    f.auth.admit_revision(before, after)
    answer = resume(f, old)
    second = f.store.load(answer["work"]["work_id"], **f.service._owner)
    events = f.auth.timeline.events(f.claim.thread_id)
    event = events[-1]
    data = {**event.payload["data"], "previous_mandate_sha256": "f" * 64}
    payload = {**event.payload, "data": data}
    forged = replace(event, payload=payload, payload_sha256=canonical_sha256(payload))
    assert f.auth._work_admission(events[:-1] + (forged,), second.spec) is None


def test_resume_rejects_concurrent_admitted_revision_before_queue_insert(native_digital, monkeypatch):
    f = native_digital
    old = failed(f)
    events = f.auth.timeline.events(f.claim.thread_id)
    bind = f.auth.bind_resume_work
    def concurrent_revision(*args, **kwargs):
        before, after = revise_mandate(f)
        f.auth.admit_revision(before, after)
        return bind(*args, **kwargs)
    monkeypatch.setattr(f.auth, "bind_resume_work", concurrent_revision)
    with pytest.raises(InitiativeMandateDenied, match="changed during admission"):
        resume(f, old)
    assert f.auth.timeline.events(f.claim.thread_id) == events
    assert len(f.store.workboard(**f.service._owner, now=NOW).cards) == 1
