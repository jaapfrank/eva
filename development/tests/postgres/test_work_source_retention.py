"""Real day compaction and retained work; no model, external effect or win state."""
from datetime import timedelta
import hashlib
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace

import pytest

from eva.adapters.postgres.work_source_retention import WorkSourceRetention, retained_source_guard
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.application.conversation import ConversationConflict
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_memory_source_lifecycle import complete_dream_exchange, _payloads
from tests.postgres.test_dream_memory_handoff import _case, _dream
from tests.postgres.test_runtime_resource_usage import NOW
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_delegated_digital_work import delegated_engine
from eva.contracts.delegated_work import DelegatedWorkUsage


def _retained_work(engine, source, label, *, admit=True):
    scope, episode, binding = _seed_authority(engine, label)
    owner = _id(label, "principal")
    store = PostgresDelegatedWorkStore(engine, scope)
    relative="bootstrap/delegated_digital_runner.py"
    source_hash=hashlib.sha256((Path(__file__).resolve().parents[3]/"src/eva"/relative).read_bytes()).hexdigest()
    profile = DelegatedDigitalProfile("model:controlled", "a"*64, "b"*64,
        ("write_programming_file",), ((relative, source_hash),))
    turn = source.turn
    spec = profile.build_spec(source={"tenant_id": turn.tenant_id, "installation_id": turn.installation_id,
        "principal_id": turn.principal_id, "conversation_id": turn.conversation_id,
        "message_id": turn.message_id, "audience": {"kind": "private_person", "principal_id": turn.principal_id}},
        source_ingress_sha256=canonical_sha256(PostgresConversationJournal._ingress_data(turn)),
        work_id="work:"+label, episode_id=str(episode), generation=1, parent_task_id="chat:"+label,
        owner_principal_id=str(owner), principal_binding_id=str(binding), task="Retain this synthetic source while work is unfinished",
        created_at=NOW, deadline=NOW+timedelta(minutes=20))
    if admit:
        store.admit(spec=spec, title="Synthetic unfinished work", summary="Lifecycle check", worker_role="Digital",
            retention_policy_id="retention:test", retention_until=NOW+timedelta(days=7), occurred_at=NOW)
    owner_args={"owner_principal_id":owner,"principal_binding_id":binding}
    return store, profile, spec, owner_args


def test_retained_work_preserves_exact_source_then_releases_without_starving_or_blocking_forget(delegated_engine, tmp_path):
    for case in ("stop", "expiry", "wrong_owner", "erase", "failed_parent_stopped_child", "compacted_resume"):
        location=tmp_path/case;location.mkdir()
        _, installation=_case(delegated_engine,location)
        source=complete_dream_exchange(delegated_engine,_dream(installation,"pinned"))
        later=complete_dream_exchange(delegated_engine,_dream(installation,"unrelated"))
        store, profile, spec, owner=_retained_work(delegated_engine,source,"pin-"+case)
        registry=WorkSourceRetention(store)
        outbox=PostgresMemoryOutbox(delegated_engine,tenant_id=source.turn.tenant_id,
            installation_id=source.turn.installation_id,work_source_retention=registry)
        when=NOW+timedelta(days=1,seconds=1)
        original=_payloads(source)
        assert registry.protected_sources(source.turn.tenant_id,source.turn.installation_id,when)=={
            (source.turn.principal_id,source.turn.conversation_id,source.turn.message_id)}
        assert registry.protected_sources(source.turn.tenant_id,"another-installation",when)==frozenset()
        # A fully pinned cohort must not consume limit=1 and starve unrelated text.
        changed=outbox.compact_day_sources(now=when,limit=1)
        assert [v["document_id"] for v in changed]==[later.document.document_id]
        assert _payloads(source)==original
        service=SimpleNamespace(store=store,conversation_scope=source.outbox._scope_value,validate_authority=lambda turn: None,now=lambda:when)
        with retained_source_guard(service,spec):
            pass  # The original source still loads through the real scoped journal.
        if case=="compacted_resume":
            call=store.start_call(store.claim_next(worker_id="synthetic",release=profile.release,now=NOW+timedelta(seconds=1)),now=NOW+timedelta(seconds=1))
            store.record_failure(call,usage=DelegatedWorkUsage.unmeasured(),error_code="provider_transient",
                error_fingerprint="a"*64,ambiguous=False,now=NOW+timedelta(seconds=2))
            # Reproduce an already-compacted legacy source using the old standalone compactor.
            # The new runtime uses the configured registry and never takes this path.
            assert len(source.outbox.compact_day_sources(now=when))==1
            before=store.load(spec.work_id,**owner)
            actual=DelegatedWorkOwnerService(store,**owner,digital_profile=profile,clock=lambda:when,
                conversation_scope=source.outbox._scope_value,
                private_bridge_principal_loader=lambda:source.turn.principal_id)
            resume=replace(source.turn,message_id="fresh:resume",text="Continue my existing task",
                occurred_at=when.strftime("%Y-%m-%dT%H:%M:%SZ"))
            source.journal.admit(resume)
            result=actual.execute("resume",resume,work_id=spec.work_id,request_key="fresh-resume")
            assert result["status"]=="source_unavailable" and result["created"] is False
            assert result["work"] is None and len(store.list_owned(**owner,limit=10))==1
            assert store.load(spec.work_id,**owner)==before
            continue
        if case=="wrong_owner":
            assert ("another-person",source.turn.conversation_id,source.turn.message_id) not in registry.protected_sources(
                source.turn.tenant_id,source.turn.installation_id,when)
            store.request_stop(spec.work_id,now=when,**owner)
        elif case=="erase":
            receipt=source.journal.erase_message_content(principal_id=source.turn.principal_id,
                message_id=source.turn.message_id,operation_sha256="e"*64,erased_at=when)
            assert receipt.redacted_event_count==2
            assert "text" not in source.journal._timeline.events("conversation:"+source.turn.message_id)[0].payload["data"]
            with pytest.raises(ConversationConflict):
                with retained_source_guard(service,spec): pass
            continue  # Erasure remains authoritative; never restore its plaintext.
        elif case=="failed_parent_stopped_child":
            call=store.start_call(store.claim_next(worker_id="synthetic",release=profile.release,now=NOW+timedelta(seconds=1)),now=NOW+timedelta(seconds=1))
            store.record_failure(call,usage=DelegatedWorkUsage.unmeasured(),error_code="provider_transient",
                error_fingerprint="a"*64,ambiguous=False,now=NOW+timedelta(seconds=2))
            child=profile.build_spec(source=spec.source.model_dump(mode="json"),source_ingress_sha256=spec.source_ingress_sha256,
                grant=spec.grant,work_id=spec.work_id+":resume",execution_id=spec.execution_id,episode_id=spec.episode_id,
                generation=2,parent_task_id=spec.work_id,owner_principal_id=spec.owner_principal_id,
                principal_binding_id=spec.principal_binding_id,task=spec.task,created_at=NOW+timedelta(seconds=3),
                deadline=NOW+timedelta(minutes=20))
            store.admit(spec=child,title="Synthetic continuation",summary="Retained identity",worker_role="Digital",
                retention_policy_id="retention:test",retention_until=NOW+timedelta(days=7),occurred_at=NOW+timedelta(seconds=3))
            store.request_stop(child.work_id,now=when,**owner)
        elif case=="stop": store.request_stop(spec.work_id,now=when,**owner)
        else: when=NOW+timedelta(days=8)
        assert registry.protected_sources(source.turn.tenant_id,source.turn.installation_id,when)==frozenset()
        changed=outbox.compact_day_sources(now=when)
        assert [v["document_id"] for v in changed]==[source.document.document_id]
        with pytest.raises(ConversationConflict):
            with retained_source_guard(service,spec): pass

    # An already dispatched owner appointment is an exact task input, not a
    # conversation replay. Recover only those bytes under the existing grant.
    from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
    from eva.adapters.postgres.work_source_retention import TaskSourceLoader
    from eva.providers.mcp_autonomy import _SourceTurnLoader
    from eva.application.background_execution import BackgroundExecutionScope, background_execution
    for mode in ("retained_snapshot", "pending_appointment"):
        location=tmp_path/mode;location.mkdir()
        _, installation=_case(delegated_engine,location)
        source=complete_dream_exchange(delegated_engine,_dream(installation,"source"))
        store, profile, spec, owner=_retained_work(delegated_engine,source,"pin-"+mode,admit=mode=="retained_snapshot")
        clock=[NOW]
        actual=DelegatedWorkOwnerService(store,**owner,digital_profile=profile,clock=lambda:clock[0],
            conversation_scope=source.outbox._scope_value,
            private_bridge_principal_loader=lambda:source.turn.principal_id)
        scheduler=ScheduledOwnerWork(actual)
        if mode=="pending_appointment":
            scheduled=scheduler.schedule(source.turn,request_key="future-start",request={
                "task":"A genuine future synthetic task","run_at":(NOW+timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")})
            outbox=PostgresMemoryOutbox(delegated_engine,tenant_id=source.turn.tenant_id,
                installation_id=source.turn.installation_id,work_source_retention=WorkSourceRetention(store))
            clock[0]=NOW+timedelta(days=1,seconds=1)
            assert outbox.compact_day_sources(now=clock[0])==()
            actual.execute("schedule_cancel",source.turn,schedule_id=scheduled["schedule_id"],request_key="cancel")
            assert len(outbox.compact_day_sources(now=clock[0]))==1
            continue
        call=store.start_call(store.claim_next(worker_id="synthetic",release=profile.release,now=NOW+timedelta(seconds=1)),now=NOW+timedelta(seconds=1))
        store.record_failure(call,usage=DelegatedWorkUsage.unmeasured(),error_code="provider_transient",
            error_fingerprint="a"*64,ambiguous=False,now=NOW+timedelta(seconds=2))
        clock[0]=NOW+timedelta(seconds=3)
        scheduler.schedule(source.turn,request_key="accepted-resume",request={"work_id":spec.work_id,
            "run_at":(NOW+timedelta(seconds=4)).strftime("%Y-%m-%dT%H:%M:%SZ")},resume=True)
        clock[0]=NOW+timedelta(seconds=4)
        assert scheduler.run_once()=="dispatched"
        second=next(row.spec for row in store.list_owned(**owner,limit=10) if row.spec.generation==2)
        call=store.start_call(store.claim_next(worker_id="synthetic",release=profile.release,now=NOW+timedelta(seconds=5)),now=NOW+timedelta(seconds=5))
        store.record_failure(call,usage=DelegatedWorkUsage.unmeasured(),error_code="provider_transient",
            error_fingerprint="a"*64,ambiguous=False,now=NOW+timedelta(seconds=6))
        clock[0]=NOW+timedelta(days=1,seconds=1)
        assert len(source.outbox.compact_day_sources(now=clock[0]))==1  # legacy day compaction
        compacted=_payloads(source)
        assert "text" not in compacted[1].payload["data"]
        resume=replace(source.turn,message_id="fresh:snapshot-resume",text="Continue the existing task",
            occurred_at=clock[0].strftime("%Y-%m-%dT%H:%M:%SZ"))
        source.journal.admit(resume)
        resumed=actual.execute("resume",resume,work_id=second.work_id,request_key="continue-known-input")
        assert resumed["created"] is True
        third=store.load(resumed["work"]["work_id"],**owner).spec
        assert third.source==spec.source and third.grant==spec.grant and third.execution_id==spec.execution_id
        ordinary=_SourceTurnLoader(delegated_engine,SimpleNamespace(scope=source.outbox._scope_value,
            authorize_turn=actual.validate_authority))
        with pytest.raises(ConversationConflict): ordinary(spec.source.model_dump(mode="json"))
        loader=TaskSourceLoader(actual,ordinary)
        restored=loader.for_task(third)
        assert restored==source.turn
        with background_execution(BackgroundExecutionScope(restored,third.execution_id,
                validate_source=lambda turn:loader.validate_current(third,turn))):
            assert ordinary(third.source.model_dump(mode="json"))==source.turn
            assert _payloads(source)==compacted  # no conversation or memory plaintext is restored
            source.journal.erase_message_content(principal_id=source.turn.principal_id,
                message_id=source.turn.message_id,operation_sha256="d"*64,erased_at=clock[0])
            with pytest.raises(ConversationConflict): ordinary(third.source.model_dump(mode="json"))
            with pytest.raises(ConversationConflict): loader.for_task(third)
