"""Operator repair of one certified producer fault, without rewriting old failure."""
from dataclasses import replace
import pytest
from tests.postgres.test_native_initiative_digital_work import native_digital, costless_native, admitted, started, result, NOW
from tests.postgres.test_native_initiative_profile_refresh import change_program
from eva.application import initiative_execution as proof
from eva.bootstrap.native_initiative_work import resume_native_initiative, refresh_one_unstarted_native
from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied

FP = "02e70758637b3ceefec093f5fede3d74a5e42d5081a68ab2fb228e5de1e774a9"

def failed_prepare(f, monkeypatch):
    _, work = admitted(f)
    call = started(f, work)
    usage = result(call).usage.model_copy(update={"tool_calls":0})
    old = f.store.record_failure(call, usage=usage, error_code="internal_error", error_fingerprint=FP, ambiguous=True, now=NOW)
    # Synthetic producer has the same asserted control flow. The production map
    # remains frozen to the independently audited historical program only.
    monkeypatch.setitem(proof._PREPARATION_FAILURE_PRODUCERS,
        (old.spec.worker_program_sha256, FP), "synthetic-certified-prepare")
    from eva.contracts.canonical_json import canonical_sha256
    evidence = {"contract": "NativePreparationRecoveryEvidence/v1", "cause": "synthetic-certified-prepare",
        "work_id": old.spec.work_id, "spec_sha256": old.spec.sha256, "call_id": old.call_id,
        "producer_program_sha256": old.spec.worker_program_sha256, "error_fingerprint": FP,
        "usage_sha256": canonical_sha256(old.usage.to_payload()),
        "scope": "no_task_tool_dispatch_not_zero_model_or_database_usage"}
    monkeypatch.setattr(proof, "_CERTIFIED_PREPARATION_EVIDENCE_SHA256",
        proof._CERTIFIED_PREPARATION_EVIDENCE_SHA256 | {canonical_sha256(evidence)})
    return old


def test_one_certified_operator_recovery_keeps_old_attempt_and_real_result_binding(native_digital, monkeypatch):
    f = native_digital
    old = failed_prepare(f, monkeypatch)
    assert proof.certified_preparation_evidence(old) is not None
    change_program(f)
    assert refresh_one_unstarted_native(f.service) == "idle"  # No automatic uncertain retry.
    admission = resume_native_initiative(f.service, old, created=NOW, _certified_prepare_recovery=True)
    saved = admission.work
    assert saved.spec.task == old.spec.task and saved.spec.origin == old.spec.origin
    assert saved.spec.execution_id == old.spec.execution_id and saved.spec.grant == old.spec.grant
    assert saved.spec.source == old.spec.source and saved.spec.generation == old.spec.generation+1
    assert resume_native_initiative(f.service, old, created=NOW, _certified_prepare_recovery=True).disposition == "already_present"
    event = f.auth.timeline.events(f.claim.thread_id)[-1]
    data = event.payload["data"]
    assert data["recovery_kind"] == "host_certified_prepare_recovery"
    assert data["preparation_evidence"] == proof.certified_preparation_evidence(old)
    call = started(f, saved)
    assert f.store.execution_authority_current(call)
    completed = f.store.record_success(call, result(call, "Completed original self-chosen question"), now=NOW)
    assert completed.state == "succeeded"
    assert f.control.artifact(f.claim.intent_id, now=NOW)["content"] == completed.result.output
    assert f.store.load(old.spec.work_id, **f.service._owner) == old and old.state == "ambiguous"


@pytest.mark.parametrize("boundary", ["dispatched", "more_responses", "different_error", "different_program", "stopped", "leased", "same_program", "different_call", "different_work", "different_usage"])
def test_uncertified_or_unchanged_attempt_is_never_recovered(native_digital, monkeypatch, boundary):
    f = native_digital
    old = failed_prepare(f, monkeypatch)
    if boundary == "dispatched": old=replace(old, usage=old.usage.model_copy(update={"tool_calls":1}))
    elif boundary == "more_responses": old=replace(old, usage=old.usage.model_copy(update={"completed_model_responses":2}))
    elif boundary == "different_error": old=replace(old, error_fingerprint="f"*64)
    elif boundary == "different_program": old=replace(old, spec=old.spec.model_copy(update={"worker_program_sha256":"f"*64}))
    elif boundary == "stopped": old=replace(old, stop_requested_at=NOW)
    elif boundary == "leased": old=replace(old, lease_owner="another-worker")
    elif boundary == "different_call": old=replace(old, call_id="child-call:other")
    elif boundary == "different_work": old=replace(old, spec=old.spec.model_copy(update={"work_id":"work:eva-resume:00000000-0000-5000-8000-000000000000"}))
    elif boundary == "different_usage": old=replace(old, usage=old.usage.model_copy(update={"input_tokens":31,"total_tokens":41}))
    if boundary != "same_program":
        assert proof.certified_preparation_evidence(old) is None
        change_program(f)
    with pytest.raises(InitiativeMandateDenied):
        resume_native_initiative(f.service, old, created=NOW, _certified_prepare_recovery=True)
