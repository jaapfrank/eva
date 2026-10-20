"""Real retained native/read journals, no connector or model dispatch."""
from datetime import timedelta
from dataclasses import replace
from uuid import uuid4
import hashlib
import json

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.current_catalog import delegated_work_runs
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.initiative_source_outcomes import read_recent_source_outcomes
from eva.adapters.postgres.operational_timeline import operational_events
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from tests.postgres.test_native_initiative_digital_work import native_digital, admitted, started, result
from tests.postgres.test_owner_initiatives import costless_native, NOW


def _read(f, work, *, step=0, payload=None, terminal="STATE_VERIFIED", namespace=None, method="GET", path="/api/calendars/calendar.fixture"):
    _, turn = f.auth.bind_work(work.spec)
    port = object.__new__(PostgresExternalConversationTools)
    port._engine, port._action_names = f.control._engine, ("home_assistant_request",)
    observation = payload or {"captured_at": NOW.isoformat(), "success": True,
        "http_status": 200, "events": [{"summary": "Synthetic event"}]}
    with background_execution(BackgroundExecutionScope(turn, work.spec.execution_id)):
        thread = port._thread_id(turn, "home_assistant_request", step)
        timeline = port._timeline(turn)
        args = {"transport": "rest", "method": method, "path": path,
            "body_json": "", "response_pointer": "", "offset": 0, "limit": 10}
        timeline.create_thread(thread_id=thread, checkpoint_id=thread+":admitted",
            event_type="TOOL_REQUEST_ADMITTED", phase="admitted", status="active", occurred_at=NOW,
            data={"action_call_id": "read-"+str(step), "action_name": "home_assistant_request",
                "action_arguments": args, "step_index": step,
                "principal_id": turn.principal_id, "conversation_id": turn.conversation_id,
                "source_message_id": turn.message_id,
                "execution_namespace": namespace or BackgroundExecutionScope(turn, work.spec.execution_id).namespace,
                "receipt_output": {"provider": "home_assistant"}, "observation_output_key": "result"})
        timeline.transition(thread_id=thread, expected_revision=1, checkpoint_id=thread+":done",
            event_type=terminal, phase="verified" if terminal=="STATE_VERIFIED" else "failed",
            status="completed" if terminal=="STATE_VERIFIED" else "failed", occurred_at=NOW,
            data={"proof": {"evidence": {"observation": observation, "operation_kind": "read"}}})
    return timeline, thread


def _finish(f, work, call, text="The worker reports a calendar event."):
    return f.store.record_success(call, result(call, text), now=NOW)


def test_exact_native_read_is_recalled_without_redispatch_and_worker_text_is_separate(native_digital, monkeypatch):
    f = native_digital
    _, work = admitted(f); call = started(f, work)
    timeline, thread = _read(f, work)
    _finish(f, work, call)
    before = timeline.events(thread)
    monkeypatch.setattr(PostgresExternalConversationTools, "execute", lambda *a, **k: pytest.fail("no redispatch"))
    values = read_recent_source_outcomes(f.auth, now=NOW)
    assert len(values) == 1
    item = values[0]
    assert item["work_id"] == work.spec.work_id and item["spec_sha256"] == work.spec.sha256
    assert item["source_question"] == {"text": work.spec.task[:1200],
        "sha256": hashlib.sha256(work.spec.task.encode()).hexdigest(),
        "truncated": len(work.spec.task)>1200, "is_new_instruction": False}
    assert item["reported_output"]["semantic_verification"] is False
    source = item["verified_reads"][0]
    assert source["observed_at"] == NOW.isoformat()
    assert source["historical"] and not source["is_current_proof"]
    assert source["observation"]["events"][0]["summary"] == "Synthetic event"
    assert source["source"]["thread_id"] == thread
    assert source["source"]["source_ingress_sha256"]
    assert timeline.events(thread) == before
    f.auth.revoke()  # Historical read never readmits old execution rights.
    assert read_recent_source_outcomes(f.auth, now=NOW) == values


@pytest.mark.parametrize("fault", ["namespace", "hash", "failed", "write", "missing"])
def test_unverified_foreign_namespace_and_write_are_never_source_facts(native_digital, fault, monkeypatch):
    f = native_digital
    _, work = admitted(f); call = started(f, work)
    if fault != "missing":
        timeline, thread = _read(f, work,
            namespace="background-work:"+"f"*64 if fault=="namespace" else None,
            terminal="STATE_VERIFICATION_TIMED_OUT" if fault=="failed" else "STATE_VERIFIED",
            method="POST" if fault=="write" else "GET")
        if fault == "hash":
            cls=type(timeline); original=cls.events
            def changed(self,identifier):
                events=original(self,identifier)
                return tuple(replace(v,payload_sha256="f"*64) if v.sequence==2 else v
                    for v in events) if identifier==thread else events
            # DB events remain append-only; simulate corruption at the reader
            # boundary instead of disabling the real database protection.
            monkeypatch.setattr(cls,"events",changed)
    _finish(f, work, call)
    item = read_recent_source_outcomes(f.auth, now=NOW)[0]
    assert item["verified_reads"] == []
    assert item["coverage"]["read_status"] == ("no_retained_ha_reads" if fault=="missing" else "unverified_or_failed")
    assert "Synthetic event" not in str(item)


@pytest.mark.parametrize("fault", ["owner", "expired", "stop", "old", "result_hash"])
def test_other_owner_stopped_expired_or_corrupt_native_result_is_excluded(native_digital, fault):
    f=native_digital
    _,work=admitted(f);call=started(f,work);_read(f,work)
    if fault=="stop": f.control.stop(f.claim.intent_id,now=NOW)
    _finish(f,work,call)
    if fault=="stop":
        # A current owner stop invalidates historical outcome authority too.
        pass
    elif fault=="old":
        assert read_recent_source_outcomes(f.auth,now=NOW+timedelta(days=2))==()
        return
    else:
        if fault=="owner":
            f.control._owner=uuid4()
            with pytest.raises(RuntimeError,match="owner binding"):
                read_recent_source_outcomes(f.auth,now=NOW)
            return
        changes={"retention_until":NOW} if fault=="expired" else {"result_sha256":bytes.fromhex("f"*64)}
        with f.store._engine.begin() as conn:
            conn.execute(sa.update(delegated_work_runs).where(
                delegated_work_runs.c.tenant_id==f.store.scope.tenant_id,
                delegated_work_runs.c.installation_id==f.store.scope.installation_id,
                delegated_work_runs.c.work_id==work.spec.work_id).values(**changes))
    values=read_recent_source_outcomes(f.auth,now=NOW)
    assert not values or values[0]["verified_reads"]==[]
    assert "Synthetic event" not in str(values)


def test_whole_context_bound_and_source_errors_remain_explicit(native_digital):
    f=native_digital
    _,work=admitted(f);call=started(f,work)
    for step in range(4):
        _read(f,work,step=step,payload={"success":False,"http_status":503,
            "truncated":True,"external_state_verified":False,"body":"x"*1100})
    _finish(f,work,call,text="worker claims success "*1000)
    values=read_recent_source_outcomes(f.auth,now=NOW)
    assert len(canonical_json_bytes(list(values)))<=8192
    item=values[0]
    assert item["reported_output"]["truncated"]
    assert item["coverage"]["context_truncated"]
    assert item["verified_reads"]
    assert all(not v["source_status"]["success"] and v["source_status"]["http_status"]==503
        and v["source_status"]["truncated"] for v in item["verified_reads"])


def test_oversized_original_read_is_hash_bound_without_inline_body(native_digital):
    f=native_digital
    _,work=admitted(f);call=started(f,work)
    _read(f,work,payload={"success":True,"body":"x"*10000});_finish(f,work,call)
    item=read_recent_source_outcomes(f.auth,now=NOW)[0]["verified_reads"][0]
    assert item["observation_bytes"]>1536 and "observation" not in item
    assert item["omitted_reason"]=="original_observation_exceeds_inline_bound"
    assert item["observation_sha256"]


def test_recent_feedback_does_not_reorder_three_original_native_completions(native_digital):
    from eva.application.free_will import FreeWillDecision, PersonalInitiativeProposal
    from tests.postgres.test_sensory_admission import _event
    f=native_digital
    works=[]
    for index in range(4):
        if index:
            f.sensory.retain(replace(_event("readback-"+str(index)),occurred_at=NOW))
            source=f.sensory.claim_next(now=NOW)
            f.sensory.complete(source,FreeWillDecision("silence","New source",0,"synthetic",
                initiative=PersonalInitiativeProposal("Source "+str(index),"Synthetic context","Read current context")),now=NOW)
            f.claim=f.interests.claim_next(tenant_id=f.claim.tenant_id,
                installation_id=f.claim.installation_id,now=NOW)
        _,work=admitted(f);call=started(f,work);_read(f,work);_finish(f,work,call)
        works.append((f.claim.intent_id,work.spec.work_id))
    before=read_recent_source_outcomes(f.auth,now=NOW)
    assert len(before)==3
    omitted=next(pair for pair in works if pair[1] not in {v["work_id"] for v in before})
    f.control.feedback(omitted[0],"Later feedback must not change source age",now=NOW+timedelta(hours=1))
    after=read_recent_source_outcomes(f.auth,now=NOW+timedelta(hours=1))
    assert [v["work_id"] for v in after]==[v["work_id"] for v in before]
    assert all(v["completed_at"]==NOW.isoformat() for v in after)
    assert len(canonical_json_bytes(list(after)))<=8192


def _component_fixture(f, tmp_path, monkeypatch, directory=False, failure=False):
    from eva.adapters.postgres.component_improvement_actions import ComponentImprovementActions
    from eva.application.conversation import ConversationActionCall
    import eva.adapters.postgres.component_improvement_actions as module
    profile=replace(f.profile,capability_ids=("read_own_component_source","write_programming_file"))
    f.profile=profile;f.service.digital_profile=profile
    f.policy["digital_work"]={"worker_profile_release_sha256":profile.release.worker_profile_release_sha256,
        "capability_ids":list(profile.capability_ids)}
    f.path.write_text(json.dumps(f.policy))
    _,work=admitted(f);call=started(f,work);guard,turn=f.auth.bind_work(work.spec)
    monkeypatch.setattr(module,"configured_component_evaluator",lambda:None)
    actions=ComponentImprovementActions(f.control._engine,work.spec,turn,guard.check,
        lambda *a,**k:f.store.authorize_tool_dispatch(call),lambda:NOW)
    actions.root=tmp_path
    target=tmp_path/"sources";target.mkdir()
    (target/"example.py").write_text("FLAG = True\n"*200)
    receipt=actions.execute(turn,ConversationActionCall("own-source","read_own_component_source",
        {"path":"sources" if directory else "sources/example.py"}))
    if failure:
        from eva.contracts.delegated_work import DelegatedWorkUsage
        f.store.record_failure(call,usage=DelegatedWorkUsage.unmeasured(),error_code="internal_error",
            error_fingerprint="e"*64,ambiguous=True,now=NOW)
    else:
        _finish(f,work,call,text="I read my actual source; no changes made.")
    return work,actions,receipt


@pytest.mark.parametrize("directory",[False,True])
def test_actual_recorded_component_read_returns_original_content_without_reopening(native_digital,tmp_path,monkeypatch,directory):
    work,actions,receipt=_component_fixture(native_digital,tmp_path,monkeypatch,directory)
    import eva.adapters.postgres.component_improvement_actions as module
    monkeypatch.setattr(module,"read_component",lambda *a,**k:pytest.fail("must not reopen source"))
    data=read_recent_source_outcomes(native_digital.auth,now=NOW)[0]
    read=data["verified_reads"][0]
    assert read["action"]=="read_own_component_source"
    assert read["source"]["thread_id"]==actions._thread(0)
    assert read["source"]["receipt_sha256"]==canonical_sha256(receipt.output)
    assert read["observation_sha256"]==canonical_sha256(receipt.output["operation_result"])
    assert not read["is_current_proof"] and data["reported_output"]["semantic_verification"] is False
    observed=read["observation"]
    if directory:
        assert observed["entries"]==[{"path":"sources/example.py","kind":"python"}]
    else:
        assert observed["content"].startswith("FLAG = True\n") and observed["content_truncated"]
        assert observed["source_sha256"]==hashlib.sha256(("FLAG = True\n"*200).encode()).hexdigest()
        assert observed["excerpt_sha256"]==hashlib.sha256(observed["content"].encode()).hexdigest()
        assert observed["next_line"]==161
    assert len(canonical_json_bytes([data]))<=8192


def test_component_receipt_hash_or_typed_output_cannot_be_forged(native_digital,tmp_path,monkeypatch):
    work,actions,receipt=_component_fixture(native_digital,tmp_path,monkeypatch)
    from eva.adapters.postgres.component_improvement_actions import ComponentImprovementActions
    from eva.application.conversation import ConversationActionReceipt
    original=ComponentImprovementActions.pending
    def forged(self,turn,**kwargs):
        value=original(self,turn,**kwargs)
        if value is not None:return ConversationActionReceipt(value.call,{**value.output,"scope":"unexecuted_proposal"})
        return value
    monkeypatch.setattr(ComponentImprovementActions,"pending",forged)
    values=read_recent_source_outcomes(native_digital.auth,now=NOW)
    assert values[0]["verified_reads"]==[]
    assert values[0]["unavailable_reads"][0]["reason"]=="retained_component_source_binding_or_proof_invalid"


def test_verified_component_read_survives_ambiguous_work_without_fabricating_result(native_digital,tmp_path,monkeypatch):
    work,actions,receipt=_component_fixture(native_digital,tmp_path,monkeypatch,failure=True)
    before=actions.timeline.events(actions._thread(0))
    data=read_recent_source_outcomes(native_digital.auth,now=NOW)[0]
    assert data["result_sha256"] is None and data["reported_output"] is None
    assert data["work_outcome"]["state"]=="ambiguous"
    assert data["work_outcome"]["error_code"]=="internal_error"
    assert data["source_question"]["text"] == work.spec.task[:1200]
    assert data["source_question"]["sha256"] == hashlib.sha256(work.spec.task.encode()).hexdigest()
    assert data["source_question"]["is_new_instruction"] is False
    assert data["outcome_sha256"]==canonical_sha256(data["work_outcome"])
    assert data["verified_reads"][0]["observation"]["content"].startswith("FLAG = True")
    assert native_digital.auth.timeline.load(native_digital.claim.thread_id).phase=="initiative_blocked"
    assert actions.timeline.events(actions._thread(0))==before


@pytest.mark.parametrize("fault",["stopped","expired"])
def test_failed_component_work_cannot_bypass_stop_or_retention(native_digital,tmp_path,monkeypatch,fault):
    work,actions,receipt=_component_fixture(native_digital,tmp_path,monkeypatch,failure=True)
    f=native_digital
    if fault=="stopped":
        f.control.stop(f.claim.intent_id,now=NOW)
    else:
        with f.store._engine.begin() as conn:
            conn.execute(sa.update(delegated_work_runs).where(
                delegated_work_runs.c.tenant_id==f.store.scope.tenant_id,
                delegated_work_runs.c.installation_id==f.store.scope.installation_id,
                delegated_work_runs.c.work_id==work.spec.work_id).values(retention_until=NOW))
    values=read_recent_source_outcomes(f.auth,now=NOW)
    assert not values or (values[0]["verified_reads"]==[] and values[0]["reported_output"] is None
        and values[0]["status"]=="unavailable")


def test_exact_target_selects_only_its_original_proof_before_context_limit(native_digital):
    from eva.application.free_will import FreeWillDecision, PersonalInitiativeProposal
    from eva.contracts.delegated_work import DelegatedWorkUsage
    from tests.postgres.test_sensory_admission import _event
    f=native_digital
    _,first=admitted(f);call=started(f,first);_read(f,first);_finish(f,first,call)
    f.sensory.retain(replace(_event("other-target-source"),occurred_at=NOW))
    source=f.sensory.claim_next(now=NOW)
    f.sensory.complete(source,FreeWillDecision("silence","Other source",0,"synthetic",
        initiative=PersonalInitiativeProposal("Other target","Separate context","Read other information")),now=NOW)
    f.claim=f.interests.claim_next(tenant_id=f.claim.tenant_id,installation_id=f.claim.installation_id,now=NOW)
    _,other=admitted(f);other_call=started(f,other)
    f.store.record_failure(other_call,usage=DelegatedWorkUsage.unmeasured(),error_code="incomplete_work",
        error_fingerprint="e"*64,ambiguous=False,now=NOW)
    assert len(read_recent_source_outcomes(f.auth,now=NOW))==2
    wanted=read_recent_source_outcomes(f.auth,now=NOW,work_id=first.spec.work_id)
    assert len(wanted)==1 and wanted[0]["work_id"]==first.spec.work_id
    assert wanted[0]["verified_reads"][0]["observation"]["events"][0]["summary"]=="Synthetic event"
    excluded=read_recent_source_outcomes(f.auth,now=NOW,work_id=other.spec.work_id)
    assert len(excluded)==1 and excluded[0]["work_id"]==other.spec.work_id
    assert excluded[0]["verified_reads"]==[] and excluded[0]["reported_output"] is None
    assert "Synthetic event" not in str(excluded)
    assert read_recent_source_outcomes(f.auth,now=NOW,work_id="work:eva-initiative:"+"0"*64)==()
    with pytest.raises(ValueError,match="exact native work ID"):
        read_recent_source_outcomes(f.auth,now=NOW,work_id="other-owner-task")
