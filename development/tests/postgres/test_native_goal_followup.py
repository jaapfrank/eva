"""Completed own results feed model-selected new steps; no provider traffic."""
from dataclasses import replace
import hashlib
from datetime import timedelta
import pytest

from tests.postgres.test_native_initiative_digital_work import native_digital, costless_native, admitted, started, result, NOW
from tests.postgres.test_sensory_admission import _event
from eva.application.free_will import FreeWillDecision, PersonalInitiativeProposal
from eva.bootstrap.native_initiative_work import admit_native_initiative
from eva.adapters.postgres.initiative_adoption import bind_completed_followup
from eva.adapters.postgres.initiative_execution import execution_turn
from eva.application.conversation import ConversationConflict


def complete_parent(f):
    _, work = admitted(f)
    call = started(f, work)
    saved = f.store.record_success(call, result(call, "A retained earlier finding."), now=NOW)
    return saved


def propose(f, parent, *, question="Investigate a genuinely different next question.", digest=None, title=None, name="followup"):
    event = replace(_event(name), occurred_at=NOW)
    f.sensory.retain(event)
    claim = f.sensory.mark_model_started(f.sensory.claim_next(now=NOW), now=NOW)
    proposal = PersonalInitiativeProposal(title or f.claim.title, "Build on earlier findings.", question,
        parent.spec.origin.intent_id, digest or parent.result.output_sha256)
    decision = FreeWillDecision("silence", "Own continuing interest.", 0, "synthetic",
        initiative=proposal)
    f.sensory.complete(claim, decision, now=NOW)
    data = f.sensory._timeline.events(claim.thread_id)[-1].payload["data"]["initiative"]
    return data, claim, decision


def test_bound_followup_keeps_project_title_and_old_result_native_worker_finishes(native_digital):
    f = native_digital
    parent = complete_parent(f)
    source_before = f.auth.timeline.events(f.claim.thread_id)
    data, _, _ = propose(f, parent)
    assert data["status"] == "adopted"
    claim = f.interests.claim_next(tenant_id=f.claim.tenant_id, installation_id=f.claim.installation_id, now=NOW)
    assert claim.title == f.claim.title and claim.intent_id != f.claim.intent_id
    assert claim.followup_source["intent_id"] == f.claim.intent_id
    assert claim.followup_source["content_sha256"] == parent.result.output_sha256
    turn = execution_turn(claim)
    assert "A retained earlier finding." in turn.text and claim.question in turn.text
    history = f.sensory.recent_initiatives()
    assert next(x for x in history if x["intent_id"] == claim.intent_id)["followup_source"]["intent_id"] == f.claim.intent_id
    receipt = admit_native_initiative(f.auth, claim, f.service)
    work = f.store.load(receipt.work_id, **f.service._owner)
    call = f.store.start_call(f.store.claim_next(worker_id="followup", release=f.profile.release, now=NOW), now=NOW)
    final = f.store.record_success(call, result(call, "A genuinely completed next result."), now=NOW)
    assert final.state == "succeeded"
    assert f.control.artifact(claim.intent_id, now=NOW)["content"] == final.result.output
    assert f.auth.timeline.events(f.claim.thread_id) == source_before
    assert f.store.load(parent.spec.work_id, **f.service._owner) == parent
    later = next(x for x in f.sensory.recent_initiatives() if x["intent_id"] == claim.intent_id)
    assert later["recorded_result"]["content_sha256"] == final.result.output_sha256


@pytest.mark.parametrize("boundary", ["wrong_hash", "stopped_parent", "other_owner", "same_question"])
def test_invalid_or_replayed_parent_cannot_admit_followup(native_digital, boundary):
    f = native_digital
    parent = complete_parent(f)
    if boundary == "stopped_parent": f.control._stop_execution(f.claim.intent_id)
    if boundary == "other_owner":
        with f.control._engine.begin() as conn:
            f.control._authorize(conn, NOW)
            with pytest.raises(ConversationConflict, match="owner or origin"):
                bind_completed_followup(conn, f.control._scope,
                    {"intent_id":f.claim.intent_id,"content_sha256":parent.result.output_sha256},
                    principal_id="another-person", now=NOW)
        return
    data, _, _ = propose(f, parent, digest="f"*64 if boundary == "wrong_hash" else None,
        question=f.claim.question if boundary == "same_question" else "A new question.", title="A different title")
    assert data["status"] == "not_admitted"
    assert data["reason"] == ("followup_step_already_attempted" if boundary == "same_question"
        else "completed_followup_source_unavailable")


def test_repeated_decision_is_idempotent_and_does_not_duplicate_followup(native_digital):
    f = native_digital
    parent = complete_parent(f)
    data, claim, decision = propose(f, parent)
    with f.control._engine.begin() as conn:
        f.control._authorize(conn, NOW)
        again = f.sensory._adopt_initiative(conn, claim, decision, now=NOW)
    assert again["status"] == "already_adopted" and again["intent_id"] == data["intent_id"]


def test_durable_ancestor_stop_revokes_already_started_descendant(native_digital):
    f = native_digital
    parent = complete_parent(f)
    propose(f, parent)
    claim = f.interests.claim_next(tenant_id=f.claim.tenant_id, installation_id=f.claim.installation_id, now=NOW)
    receipt = admit_native_initiative(f.auth, claim, f.service)
    call = f.store.start_call(f.store.claim_next(worker_id="followup", release=f.profile.release, now=NOW), now=NOW)
    assert f.store.execution_authority_current(call)
    f.control._stop_execution(f.claim.intent_id)
    assert not f.store.execution_authority_current(call)
