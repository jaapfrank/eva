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


def test_contact_turns_continue_during_durable_call_and_stop_only_target(delegated_engine: Engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
    from eva.application.conversation import ConversationActionCall, LivingConversation
    from tests.application.test_contact_model import IDENTITY, Model
    from tests.application.test_conversation import _Memory

    label = "contact-durable"
    scope, _, binding = _seed_authority(delegated_engine, label)
    owner = _id(label, "principal")
    store = PostgresDelegatedWorkStore(delegated_engine, scope)
    at = [NOW]
    service = DelegatedWorkOwnerService(store, owner_principal_id=owner,
                                       principal_binding_id=binding, clock=lambda: at[0])
    # Minimal composition of the real action adapter's existing conversation port.
    class Actions(PostgresDelegatedWorkActions):
        def active_context(self, turn):
            return ()
        def available_action_names(self, turn):
            return self.action_names if self.available_for(turn) else ()
        def continuation_action_names(self, turn):
            return self.available_action_names(turn)
        def execute_step(self, turn, call, *, step_index):
            return self.execute(turn, call, step_index=step_index)

    ids = []
    def choose(t, k):
        receipt = k["completed_action"]
        if receipt is not None:
            if receipt.call.name == "stop_delegated_work":
                return ConversationActionCall(t.message_id + ":replacement", "start_delegated_work",
                                              {"task": "Schrijf in plaats daarvan een buurtpicknickopzet"})
            result = receipt.output["operation_result"]
            if receipt.call.name == "start_delegated_work":
                assert k["available_actions"] == ()  # no polling this accepted task
                ids.append(result["work"]["work_id"])
            return "De taakstatus is opgeslagen."
        if t.text == "schrijf":
            return ConversationActionCall(t.message_id, "start_delegated_work", {"task": "Schrijf een buurtfeestopzet"})
        if t.text == "stop":
            return ConversationActionCall(t.message_id, "stop_delegated_work", {"work_id": ids[0]})
        return "Vier."

    contact = Model(choose)
    work = Model(lambda t, k: pytest.fail("durable work must not execute in foreground"))
    conversation = LivingConversation(_Memory(), work, IDENTITY, Actions(delegated_engine, service),
                                      contact_model=contact)
    conversation.respond(_turn(label, owner, 0, "schrijf"))
    first = store.load(ids[0], owner_principal_id=owner, principal_binding_id=binding)
    entered, release = Event(), Event()
    def worker():
        claim = store.claim_next(worker_id="worker:contact", release=_release(first.spec), now=NOW + timedelta(seconds=1))
        assert claim.work_id == ids[0]
        call = store.start_call(claim, now=NOW + timedelta(seconds=2))
        entered.set()
        assert release.wait(15)
        return store.record_stopped(call, usage=_usage(), now=NOW + timedelta(seconds=8))
    with ThreadPoolExecutor(max_workers=2) as executor:
        running = executor.submit(worker)
        try:
            assert entered.wait(5)
            at[0] = NOW + timedelta(seconds=3)
            conversation.respond(_turn(label, owner, 3, "schrijf"))
            reply = executor.submit(conversation.respond, _turn(label, owner, 4, "2+2")).result(timeout=5)
            assert reply.text == "Vier." and not running.done()
            at[0] = NOW + timedelta(seconds=5)
            conversation.respond(_turn(label, owner, 5, "stop"))
            saved = store.load(ids[0], owner_principal_id=owner, principal_binding_id=binding)
            assert saved.stop_requested_at is not None
            assert store.load(ids[1], owner_principal_id=owner, principal_binding_id=binding).state == "admitted"
            assert len(ids) == 3  # correction admitted after stopping only the first task
            assert store.load(ids[2], owner_principal_id=owner, principal_binding_id=binding).state == "admitted"
        finally:
            release.set()
        assert running.result().state == "stopped"


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
    stored = store.load(work["work_id"], owner_principal_id=owner, principal_binding_id=binding)
    from eva.bootstrap.delegated_work_service import DelegatedWorkServiceSettings
    settings = DelegatedWorkServiceSettings.from_environment({
        "EVA_DATABASE_DSN_FILE": "/run/secrets/database_dsn",
        "EVA_DELEGATED_WORKER_ID": "worker:distinct-scope",
        "EVA_RUNTIME_TENANT_ID": turn.tenant_id,
        "EVA_RUNTIME_INSTALLATION_ID": turn.installation_id,
        "EVA_DASHBOARD_TENANT_ID": str(scope.tenant_id),
        "EVA_DASHBOARD_INSTALLATION_ID": str(scope.installation_id),
    })
    worker_store = PostgresDelegatedWorkStore(delegated_engine, settings.scope)
    claim = worker_store.claim_next(worker_id=settings.worker_id, release=_release(stored.spec),
                                   now=NOW + timedelta(seconds=1))
    assert claim.work_id == work["work_id"]
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


def test_trusted_private_bridge_alias_keeps_identity_and_rechecks_revocation(delegated_engine):
    from dataclasses import replace
    from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
    from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
    from eva.application.conversation import ConversationActionCall, ConversationConflict
    from eva.application.memory_audience import MemoryAudience, TelegramGroupConsentReference

    label = "owner-bridge-alias"
    scope, _, binding = _seed_authority(delegated_engine, label)
    owner = _id(label, "principal")
    current = [TelegramPrincipalBindings((TelegramPrincipalBinding(
        telegram_user_id=42, principal_id=str(owner), allowed_chat_ids=("42",),
        principal_binding_id=str(binding),
    ),))]
    service = DelegatedWorkOwnerService(
        PostgresDelegatedWorkStore(delegated_engine, scope), owner_principal_id=owner,
        principal_binding_id=binding, clock=lambda: NOW + timedelta(seconds=4),
        private_bridge_principal_loader=lambda: current[0].private_bridge_principal(
            principal_id=str(owner), principal_binding_id=binding,
        ),
    )
    actions = PostgresDelegatedWorkActions(delegated_engine, service)
    turn = replace(_turn(label, "telegram:42", 0), conversation_id="telegram:42")
    assert service.available_for(turn)
    assert service.available_for(_turn(label, owner, 0))
    assert not service.available_for(_turn(label, "telegram:43", 0))
    assert not service.available_for(_turn("foreign-scope", "telegram:42", 0))
    group = replace(turn, conversation_id="telegram:-100", message_id="telegram:-100:1",
                    audience=MemoryAudience.telegram_group(principal_id=turn.principal_id,
                    group_chat_id="-100", consent_ref=TelegramGroupConsentReference(
                        turn.principal_id, "-100", "telegram-group-consent:test")))
    assert not service.available_for(group)
    call = ConversationActionCall("alias-start", "start_delegated_work", {"task": "Bedenk drie ideeën"})
    receipt = actions.execute(turn, call)
    work_id = receipt.output["operation_result"]["work"]["work_id"]
    work = service.store.load(work_id, owner_principal_id=owner, principal_binding_id=binding)
    assert work.spec.owner_principal_id == str(owner)
    assert turn.principal_id == turn.audience.principal_id == "telegram:42"
    assert turn.conversation_id == "telegram:42"
    # The canonical website identity sees and controls the same alias-started job.
    website_turn = _turn(label, owner, 1)
    listed = service.execute("list", website_turn, request_key="website-list")
    assert [job["work_id"] for job in listed["jobs"]] == [work_id]
    website_status = service.execute("get", website_turn, work_id=work_id, request_key="website-get")
    assert website_status["work"] == receipt.output["operation_result"]["work"]
    stopped = service.execute("stop", website_turn, work_id=work_id, request_key="website-stop")
    assert stopped["work"]["state"] == "stopped"
    # Resuming from Telegram preserves its real ingress and creates one successor.
    resumed = service.execute("resume", replace(turn, message_id="later-resume"), work_id=work_id, request_key="resume")
    assert resumed["work"]["generation"] == 2 and resumed["work"]["parent_work_id"] == work_id
    successor_id = resumed["work"]["work_id"]
    assert successor_id != work_id
    website_resumed = service.execute("get", _turn(label, owner, 2),
        work_id=successor_id, request_key="website-resumed")
    assert website_resumed["work"] == resumed["work"]
    final_list = service.execute("list", _turn(label, owner, 3), request_key="website-final-list")
    assert {job["work_id"]: job["state"] for job in final_list["jobs"]} == {
        work_id: "stopped", successor_id: "admitted"}
    assert actions.pending(turn) == receipt
    # A binding file change removes access even to an already saved action receipt.
    current[0] = TelegramPrincipalBindings((TelegramPrincipalBinding(
        telegram_user_id=42, principal_id=str(owner), allowed_chat_ids=("-100",),
        principal_binding_id=str(binding),
    ),))
    assert not service.available_for(turn)
    assert actions.pending(turn) is None
    with pytest.raises(ConversationConflict, match="private owner"):
        actions.execute(turn, call)
    assert service.available_for(_turn(label, owner, 0))
    current[0] = TelegramPrincipalBindings((TelegramPrincipalBinding(
        telegram_user_id=42, principal_id=str(owner), allowed_chat_ids=("42",),
        principal_binding_id=str(binding),
    ),))
    with delegated_engine.begin() as connection:
        connection.execute(sa.update(principal_bindings).where(
            principal_bindings.c.tenant_id == scope.tenant_id,
            principal_bindings.c.principal_binding_id == binding,
        ).values(status="revoked"))
    with pytest.raises(DelegatedWorkConflict, match="binding is not current"):
        actions.pending(turn)


def test_unreadable_bridge_binding_never_grants_alias_access():
    from types import SimpleNamespace
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    owner = _id("unreadable", "principal")
    scope = external_timeline_scope("tenant:unreadable", "installation:unreadable", "scope")
    def unavailable():
        raise OSError("unavailable")
    service = DelegatedWorkOwnerService(
        SimpleNamespace(scope=scope, validate_admission_authority=lambda **_: None),
        owner_principal_id=owner, principal_binding_id=_id("unreadable", "binding"),
        private_bridge_principal_loader=unavailable,
    )
    assert not service.available_for(_turn("unreadable", "telegram:42", 0))
