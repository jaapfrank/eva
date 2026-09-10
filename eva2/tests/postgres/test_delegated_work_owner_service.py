from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.delegated_work import DelegatedWorkConflict, PostgresDelegatedWorkStore
from eva.application.conversation import ConversationTurn
from eva.bootstrap.delegated_work_commands import DelegatedWorkConversationCommands
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.contracts.delegated_work import DelegatedWorkResult, DelegatedWorkUsage
from tests.postgres.test_delegated_work_store import (
    _id, _release, _seed_authority, _usage, delegated_engine, principal_bindings,
)

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _turn(label: str, owner, step: int, text="eigenaaropdracht") -> ConversationTurn:
    return ConversationTurn.private(
        tenant_id="tenant:" + label, installation_id="installation:" + label,
        principal_id=str(owner), conversation_id="private:owner", message_id=f"message:{step}",
        text=text, occurred_at=(NOW + timedelta(seconds=step)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def test_owner_commands_and_tools_share_durable_resume_result_and_revocation(delegated_engine: Engine):
    label = "owner-lifecycle"
    scope, _, binding = _seed_authority(delegated_engine, label)
    owner = _id(label, "principal")
    store = PostgresDelegatedWorkStore(delegated_engine, scope)
    at = [NOW]
    service = DelegatedWorkOwnerService(store, owner_principal_id=owner,
                                       principal_binding_id=binding, clock=lambda: at[0])
    commands = DelegatedWorkConversationCommands(service=service)
    turn = _turn(label, owner, 0, "Laat een onderzoeker een korte vriendelijke begroeting schrijven")
    assert service.available_for(turn)  # raw scoped names map to the durable UUID scope
    first_reply = commands.handle(turn, principal_binding_id=binding)
    assert commands.handle(turn, principal_binding_id=binding) == first_reply
    listed = service.execute("list", turn, request_key="list")
    assert len(listed["jobs"]) == 1 and not listed["truncated"]
    work_id = listed["jobs"][0]["work_id"]
    assert "task" not in listed["jobs"][0] and "result" not in listed["jobs"][0]
    assert service.execute("get", turn, work_id=work_id, request_key="get")["work"]["state"] == "admitted"
    assert service.execute("resume", turn, work_id=work_id, request_key="early")["status"] == "not_resumable"
    assert service.execute("get", turn, work_id="work:absent", request_key="missing")["status"] == "not_found"
    at[0] = NOW + timedelta(seconds=1)
    stopped_reply = commands.handle(_turn(label, owner, 1, "Stop mijn onderzoek"), principal_binding_id=binding)
    assert "voordat een modelcall begon" in stopped_reply.text
    old = store.load(work_id, owner_principal_id=owner, principal_binding_id=binding)
    resumed = service.execute("resume", _turn(label, owner, 2), work_id=work_id, request_key="resume")
    replay = service.execute("resume", _turn(label, owner, 3), work_id=work_id, request_key="another-message")
    assert resumed["created"] is True and replay["created"] is False
    assert resumed["work"] == replay["work"]
    successor_id = resumed["work"]["work_id"]
    assert successor_id != work_id and resumed["work"]["parent_work_id"] == work_id
    assert store.load(work_id, owner_principal_id=owner, principal_binding_id=binding) == old
    successor = store.load(successor_id, owner_principal_id=owner, principal_binding_id=binding)
    assert successor.spec.grant == old.spec.grant
    claim = store.claim_next(worker_id="worker:test", release=_release(successor.spec), now=NOW + timedelta(seconds=4))
    assert claim.work_id == successor_id
    call = store.start_call(claim, now=NOW + timedelta(seconds=5))
    at[0] = NOW + timedelta(seconds=6)
    stopping = service.execute("stop", _turn(label, owner, 6), work_id=successor_id, request_key="stop")
    assert stopping["work"]["state"] == "call_started" and stopping["work"]["stop_requested"]
    assert service.execute("resume", _turn(label, owner, 6), work_id=successor_id, request_key="early-again")["status"] == "not_resumable"
    usage = DelegatedWorkUsage(contract="DelegatedWorkUsage/v1", schema_version=1,
                               measurement="partial", tool_calls=0,
                               input_tokens=20, output_tokens=0, total_tokens=20)
    stopped = store.record_stopped(call, usage=usage, now=NOW + timedelta(seconds=7))
    resumed_again = service.execute("resume", _turn(label, owner, 8), work_id=successor_id, request_key="resume-after-stop")
    assert resumed_again["prior_outcome_unknown"] is True
    # Reconstruct both adapters: persisted attempts, call IDs and measured usage survive.
    store = PostgresDelegatedWorkStore(delegated_engine, scope)
    service = DelegatedWorkOwnerService(store, owner_principal_id=owner,
                                       principal_binding_id=binding, clock=lambda: at[0])
    retained = store.load(successor_id, owner_principal_id=owner, principal_binding_id=binding)
    assert retained == stopped and retained.call_id == call.call_id and retained.usage == usage
    current_id = resumed_again["work"]["work_id"]
    current = store.load(current_id, owner_principal_id=owner, principal_binding_id=binding)
    claim = store.claim_next(worker_id="worker:restarted", release=_release(current.spec), now=NOW + timedelta(seconds=9))
    assert claim.work_id == current_id
    completed_call = store.start_call(claim, now=NOW + timedelta(seconds=10))
    output = "Goedemorgen, fijn dat je er bent."
    result = DelegatedWorkResult(
        contract="DelegatedWorkResult/v1", schema_version=1, work_id=current_id,
        call_id=completed_call.call_id, output=output,
        output_sha256=hashlib.sha256(output.encode()).hexdigest(), spec_sha256=current.spec.sha256,
        usage=_usage().to_payload(), completed_at="2026-08-26T12:00:11Z",
    )
    store.record_success(completed_call, result, now=NOW + timedelta(seconds=11))
    fetched = service.execute("result", _turn(label, owner, 12), work_id=current_id, request_key="result")["work"]
    assert fetched["result"]["output"] == output
    assert fetched["result"]["output_sha256"] == result.output_sha256
    assert fetched["usage"] == _usage().to_payload() and fetched["result"]["verified"] is False
    assert service.execute("resume", _turn(label, owner, 12), work_id=current_id, request_key="already-done")["status"] == "not_resumable"
    assert len(service.execute("list", turn, request_key="all")["jobs"]) == 3
    assert not service.available_for(_turn("other-installation", owner, 12))
    assert not service.available_for(_turn(label, _id(label, "stranger"), 12))
    with pytest.raises(DelegatedWorkConflict, match="private owner scope"):
        service.execute("get", _turn(label, _id(label, "stranger"), 12), work_id=current_id, request_key="foreign")
    with delegated_engine.begin() as connection:
        connection.execute(sa.update(principal_bindings).where(
            principal_bindings.c.tenant_id == scope.tenant_id,
            principal_bindings.c.principal_binding_id == binding,
        ).values(status="revoked"))
    assert service.available_for(turn)  # catalog is structural; saved receipt release is not
    with pytest.raises(DelegatedWorkConflict, match="binding is not current"):
        service.validate_authority(turn)
    with pytest.raises(DelegatedWorkConflict, match="binding is not current"):
        service.execute("result", turn, work_id=current_id, request_key="revoked-result")
    with pytest.raises(DelegatedWorkConflict, match="binding is not current"):
        service.execute("start", turn, task="een korte vriendelijke begroeting schrijven",
                        request_key=service.command_key(turn))


def test_expired_uncertain_attempt_is_retained_and_only_explicit_successor_can_run(delegated_engine: Engine):
    label = "owner-expired"
    scope, _, binding = _seed_authority(delegated_engine, label)
    owner = _id(label, "principal")
    store = PostgresDelegatedWorkStore(delegated_engine, scope)
    service = DelegatedWorkOwnerService(store, owner_principal_id=owner, principal_binding_id=binding,
                                       clock=lambda: NOW + timedelta(seconds=3))
    started = service.execute("start", _turn(label, owner, 0), task="Vat een idee samen", request_key="start")
    work_id = started["work"]["work_id"]
    work = store.load(work_id, owner_principal_id=owner, principal_binding_id=binding)
    claim = store.claim_next(worker_id="worker:lost", release=_release(work.spec), now=NOW + timedelta(seconds=1))
    call = store.start_call(claim, now=NOW + timedelta(seconds=2))
    service.execute("stop", _turn(label, owner, 3), work_id=work_id, request_key="stop")
    old = store.load(work_id, owner_principal_id=owner, principal_binding_id=binding)
    assert old.state == "call_started" and old.usage is None and old.call_id == call.call_id
    resumed = service.execute("resume", _turn(label, owner, 301), work_id=work_id, request_key="resume-after-deadline")
    assert resumed["created"] and resumed["prior_outcome_unknown"]
    assert store.load(work_id, owner_principal_id=owner, principal_binding_id=binding) == old
    claim = store.claim_next(worker_id="worker:new", release=_release(work.spec), now=NOW + timedelta(seconds=302))
    assert claim.work_id == resumed["work"]["work_id"] and claim.work_id != work_id
    assert store.claim_next(worker_id="worker:another", release=_release(work.spec), now=NOW + timedelta(seconds=303)) is None
    repeat = service.execute("resume", _turn(label, owner, 303), work_id=work_id, request_key="repeat")
    assert not repeat["created"] and repeat["work"]["work_id"] == claim.work_id


def test_legacy_stop_keeps_its_first_target_when_delivery_is_replayed(delegated_engine: Engine):
    label = "owner-stop-replay"
    scope, _, binding = _seed_authority(delegated_engine, label)
    owner = _id(label, "principal")
    store = PostgresDelegatedWorkStore(delegated_engine, scope)
    service = DelegatedWorkOwnerService(store, owner_principal_id=owner,
                                       principal_binding_id=binding, clock=lambda: NOW + timedelta(seconds=10))
    commands = DelegatedWorkConversationCommands(service=service)
    no_work_turn = _turn(label, owner, 0, "Stop mijn onderzoek")
    no_work = commands.handle(no_work_turn, principal_binding_id=binding)
    first = service.execute("start", _turn(label, owner, 1), task="Eerste opdracht", request_key="first")["work"]
    assert commands.handle(no_work_turn, principal_binding_id=binding) == no_work
    stop_turn = _turn(label, owner, 2, "Stop mijn onderzoek")
    # Simulate interruption after resolving the target, before the stop itself.
    assert service.latest_work_id(stop_turn, stoppable=True) == first["work_id"]
    second = service.execute("start", _turn(label, owner, 3), task="Tweede opdracht", request_key="second")["work"]
    stopped = commands.handle(stop_turn, principal_binding_id=binding)
    assert commands.handle(stop_turn, principal_binding_id=binding) == stopped
    assert store.load(first["work_id"], owner_principal_id=owner, principal_binding_id=binding).state == "stopped"
    assert store.load(second["work_id"], owner_principal_id=owner, principal_binding_id=binding).state == "admitted"
    # Resume belongs to the ordinary conversation journal, which binds its exact work_id.
    assert commands.handle(_turn(label, owner, 4, "Hervat mijn onderzoek"), principal_binding_id=binding) is None


def test_configured_conversation_scope_can_differ_from_storage_authority(delegated_engine: Engine):
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    label = "owner-distinct-scope"
    scope, _, binding = _seed_authority(delegated_engine, label)
    owner = _id(label, "principal")
    store = PostgresDelegatedWorkStore(delegated_engine, scope)
    turn = _turn("runtime-namespace", owner, 0)
    service = DelegatedWorkOwnerService(
        store, owner_principal_id=owner, principal_binding_id=binding,
        conversation_scope=external_timeline_scope(turn.tenant_id, turn.installation_id, "required"),
    )
    assert service.conversation_scope != store.scope
    assert service.available_for(turn)
    work = service.execute("start", turn, task="Schrijf drie ideeën", request_key="distinct-scope")["work"]
    assert store.load(work["work_id"], owner_principal_id=owner, principal_binding_id=binding)
    assert not service.available_for(_turn(label, owner, 1))
    assert not service.available_for(_turn("other-runtime", owner, 1))
    assert not service.available_for(_turn("runtime-namespace", _id(label, "stranger"), 1))
    with delegated_engine.begin() as connection:
        connection.execute(sa.update(principal_bindings).where(
            principal_bindings.c.tenant_id == scope.tenant_id,
            principal_bindings.c.principal_binding_id == binding,
        ).values(status="revoked"))
    with pytest.raises(DelegatedWorkConflict, match="binding is not current"):
        service.execute("get", turn, work_id=work["work_id"], request_key="revoked")
