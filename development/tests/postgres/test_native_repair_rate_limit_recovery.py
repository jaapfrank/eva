"""Bounded native repair recovery on disposable PostgreSQL; no model or real tools."""
from dataclasses import replace
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from tests.postgres.test_autonomous_self_repair import (
    database, postgres_engine, native_digital, costless_native, prepare, NOW,
)
from tests.postgres.test_native_initiative_digital_work import admitted, started
from eva.bootstrap.native_initiative_work import (
    admit_native_initiative, resume_native_initiative, resume_one_rate_limited_repair,
)
from eva.adapters.postgres.initiative_execution import (
    InitiativeMandateDenied, RATE_LIMIT_FINGERPRINT, valid_repair_rate_limit_recovery,
)
from eva.contracts.delegated_work import DelegatedWorkUsage


def rejected(fixture, *, repair_task=True, ambiguous=False, tool_calls=0, fingerprint=RATE_LIMIT_FINGERPRINT):
    f = fixture
    if repair_task:
        f, repair, source, journal, observation = prepare(f)
        repair.observe(source, observation)
        f.claim = f.interests.claim_next(tenant_id=source.tenant_id,
            installation_id=source.installation_id, now=NOW)
    receipt = admit_native_initiative(f.auth, f.claim, f.service)
    work = f.store.load(receipt.work_id, **f.service._owner)
    call = started(f, work)
    usage = DelegatedWorkUsage(contract="DelegatedWorkUsage/v1", schema_version=1,
        measurement="partial", completed_model_responses=1, tool_calls=tool_calls,
        input_tokens=0, output_tokens=0, total_tokens=0, cost_usd_micros=None)
    saved = f.store.record_failure(call, usage=usage, error_code="provider_transient",
        error_fingerprint=fingerprint, ambiguous=ambiguous, now=NOW)
    f.service._clock = lambda: NOW + timedelta(minutes=6)
    return f, saved


def test_one_automatic_continuation_retains_identity_and_then_stops_on_second_429(native_digital):
    f, previous = rejected(native_digital)
    assert resume_one_rate_limited_repair(f.service) == "repair_resumed"
    event = f.auth.timeline.events(f.claim.thread_id)[-1]
    data = event.payload["data"]
    assert data["recovery_kind"] == "host_repair_rate_limit_recovery"
    assert valid_repair_rate_limit_recovery(data)
    assert data["recovery_conversation_id"] is None and data["recovery_message_id"] is None
    work = f.store.load(data["work_id"], **f.service._owner)
    for key in ("source", "source_ingress_sha256", "origin", "execution_id", "episode_id", "grant"):
        assert getattr(work.spec, key) == getattr(previous.spec, key)
    assert f.store.load(previous.spec.work_id, **f.service._owner) == previous
    assert resume_one_rate_limited_repair(f.service) == "idle"
    guard, turn = f.auth.bind_work(work.spec)
    from eva.application.background_execution import BackgroundExecutionScope, background_execution
    assert guard.explicit_resume_binding(turn) is None
    with background_execution(BackgroundExecutionScope(turn, work.spec.execution_id)):
        assert guard.explicit_resume_binding(turn)["parent_task_id"] == previous.spec.work_id
    now = f.service.now()
    call = f.store.start_call(f.store.claim_next(worker_id="test-successor",
        release=f.profile.release, now=now), now=now)
    f.store.record_failure(call, usage=previous.usage, error_code="provider_transient",
        error_fingerprint=RATE_LIMIT_FINGERPRINT, ambiguous=False, now=now)
    f.service._clock = lambda: NOW + timedelta(minutes=12)
    assert resume_one_rate_limited_repair(f.service) == "idle"
    assert len([e for e in f.auth.timeline.events(f.claim.thread_id)
                if e.event_type == "PERSONAL_INITIATIVE_WORK_RESUMED"]) == 1
    assert f.auth.timeline.load(f.claim.thread_id).phase == "initiative_blocked"


@pytest.mark.parametrize("boundary", ["ordinary", "ambiguous", "tools", "other_error", "too_early", "stop", "revoke", "expired"])
def test_no_automatic_replay_outside_exact_no_action_repair(native_digital, boundary):
    f, previous = rejected(native_digital, repair_task=boundary != "ordinary",
        ambiguous=boundary == "ambiguous", tool_calls=1 if boundary == "tools" else 0,
        fingerprint="f" * 64 if boundary == "other_error" else RATE_LIMIT_FINGERPRINT)
    if boundary == "too_early": f.service._clock = lambda: NOW + timedelta(minutes=4)
    elif boundary == "stop": f.control.stop(f.claim.intent_id, now=NOW)
    elif boundary == "revoke":
        f.policy["self_repair"]["enabled"] = False
        f.path.write_text(json.dumps(f.policy))
    elif boundary == "expired": f.service._clock = lambda: previous.retention_until + timedelta(seconds=1)
    before = f.auth.timeline.events(f.claim.thread_id)
    assert resume_one_rate_limited_repair(f.service) == "idle"
    assert f.auth.timeline.events(f.claim.thread_id) == before


def test_concurrent_callbacks_admit_one_successor(native_digital):
    f, previous = rejected(native_digital)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: resume_one_rate_limited_repair(f.service), range(2)))
    assert outcomes.count("repair_resumed") == 1
    assert len([e for e in f.auth.timeline.events(f.claim.thread_id)
                if e.event_type == "PERSONAL_INITIATIVE_WORK_RESUMED"]) == 1


def test_receipts_checked_in_admission_transaction_and_rollback_blocks_dispatch(native_digital, monkeypatch):
    import eva.bootstrap.delegated_digital_runner as runner
    f, previous = rejected(native_digital)
    original = runner._recorded_action_slots
    seen = []
    def existing_receipt(engine, spec, source, *, connection=None):
        assert connection is not None and connection.in_transaction()
        assert original(engine, spec, source, connection=connection) == ()
        seen.append(True)
        return ((0, "synthetic_prior_action", "synthetic-call"),)
    monkeypatch.setattr(runner, "_recorded_action_slots", existing_receipt)
    before = f.auth.timeline.events(f.claim.thread_id)
    with pytest.raises(InitiativeMandateDenied, match="prior action receipts"):
        resume_native_initiative(f.service, previous, created=f.service.now(), _repair_rate_limit_recovery=True)
    assert seen == [True]
    assert f.auth.timeline.events(f.claim.thread_id) == before


def test_recovery_evidence_cannot_be_relabelled_as_owner_input(native_digital):
    f, _ = rejected(native_digital)
    assert resume_one_rate_limited_repair(f.service) == "repair_resumed"
    data = f.auth.timeline.events(f.claim.thread_id)[-1].payload["data"]
    assert not valid_repair_rate_limit_recovery({**data, "recovery_message_id": "invented-owner-message"})
    assert not valid_repair_rate_limit_recovery({**data, "generation": 3})
    assert not valid_repair_rate_limit_recovery({**data, "rate_limit_evidence_sha256": "f" * 64})
