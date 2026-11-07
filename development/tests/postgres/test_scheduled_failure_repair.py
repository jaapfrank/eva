"""Observe an actual scheduled loader failure with the original PostgreSQL authority."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest
import sqlalchemy as sa

from eva.adapters.credentials.telegram_bindings import load_principal_bindings
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline, insert_initial_timeline_thread, operational_checkpoints, transition_timeline_thread,
)
from eva.adapters.postgres.self_repair import (
    HOST_SCHEDULED_FAILURE_SOURCE, PostgresSelfRepair, _host_source_current, current_repair_binding,
)
from eva.application.conversation import ConversationTurn
from eva.bootstrap.self_repair import _ScheduledFailureRepair
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_work import DelegatedWorkUsage
from tests.postgres.test_autonomous_self_repair import prepare
from tests.postgres.test_native_initiative_digital_work import native_digital, started
from tests.postgres.test_owner_initiatives import NOW, costless_native
from tests.postgres.test_scheduled_failure_return import database


@pytest.fixture
def postgres_engine(database):
    return database


@contextmanager
def runtime_role(engine):
    def set_role(dbapi_connection, _):
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET ROLE eva_runtime")
        dbapi_connection.commit()

    sa.event.listen(engine, "connect", set_role)
    try:
        yield
    finally:
        sa.event.remove(engine, "connect", set_role)


@pytest.fixture
def blocked_recovery(native_digital, monkeypatch, request, tmp_path):
    fixture, _, turn, journal, _ = prepare(native_digital)
    store, service = fixture.store, fixture.service
    if getattr(request, "param", False):
        channel_user = 745001
        bindings_file = tmp_path / "trusted-telegram-bindings.json"
        bindings_file.write_text(json.dumps({"contract": "EvaTelegramPrincipalBindings/v2", "bindings": [{
            "telegram_user_id": channel_user, "allowed_chat_ids": [str(channel_user)],
            "principal_id": str(service.owner_principal_id),
            "principal_binding_id": str(service.principal_binding_id),
        }]}))
        bindings_file.chmod(0o600)
        monkeypatch.setenv("EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE", str(bindings_file))
        monkeypatch.setattr(service, "_private_bridge_principal_loader", lambda: load_principal_bindings(
            str(bindings_file)
        ).private_bridge_principal(principal_id=str(service.owner_principal_id),
                                  principal_binding_id=service.principal_binding_id))
        alias = service._private_bridge_principal_loader()
        turn = ConversationTurn.private(turn.tenant_id, turn.installation_id, alias, alias,
                                        f"{alias}:1", turn.text, turn.occurred_at)
        journal.admit(turn)
    admit = store.admit

    def admit_short_lived_work(**arguments):
        arguments["retention_until"] = NOW + timedelta(days=1)
        return admit(**arguments)

    # Seed the predecessor with shorter test-only retention than the verified
    # principal and binding. The original production admission path still binds
    # the actual ingress and no retained record is modified after admission.
    with monkeypatch.context() as fixture_scope:
        fixture_scope.setattr(store, "admit", admit_short_lived_work)
        admitted = service.execute("digital", turn, task=turn.text, request_key="synthetic-schedule")["work"]
    work_id = admitted["work_id"]
    call = started(fixture, store.load(work_id, **service._owner))
    store.record_failure(call, error_code="provider_unavailable", error_fingerprint="a" * 64,
                         ambiguous=True, usage=DelegatedWorkUsage.unmeasured(), now=NOW)
    predecessor = store.load(work_id, **service._owner)
    marker = f"scheduled-work:{service.owner_principal_id}:provider-recovery:" + hashlib.sha256(
        predecessor.spec.execution_id.encode()
    ).hexdigest()
    with store._engine.begin() as connection:
        store._scope(connection)
        insert_initial_timeline_thread(
            connection, store.scope, thread_id=marker, checkpoint_id=marker + ":blocked",
            occurred_at=NOW, phase="blocked", status="needs_help", event_type="SCHEDULED_WORK_BLOCKED",
            data={"operation": "resume", "work_id": work_id, "run_at": NOW.isoformat(),
                  "reason": "Synthetic blocked provider recovery", "blocker_code": "synthetic-blocker",
                  "automatic_retry": False},
        )
    now = predecessor.retention_until + timedelta(seconds=1)
    repair = PostgresSelfRepair(fixture.auth, service, clock=lambda: now)
    repair._permission()
    return service, turn, journal, predecessor, marker, repair, now


def incidents(repair):
    scope, c = repair.owner._scope, operational_checkpoints.c
    with repair.owner._engine.begin() as connection:
        repair.timeline._scope(connection)
        return connection.execute(sa.select(c.thread_id).where(
            c.tenant_id == scope.tenant_id, c.installation_id == scope.installation_id,
            c.thread_id.startswith("self-repair:"),
        )).scalars().all()


def test_current_loader_failure_creates_one_native_incident_from_intact_ingress(blocked_recovery):
    service, turn, journal, predecessor, marker, repair, now = blocked_recovery
    intake = _ScheduledFailureRepair(repair)
    assert predecessor.retention_until < now
    assert turn.tenant_id != str(service.conversation_scope.tenant_id)
    timeline = PostgresOperationalTimeline(service.store._engine, service.store.scope)
    before = timeline.events(marker)
    with runtime_role(service.store._engine):
        with pytest.raises(ValueError, match="scheduled failure history differs"):
            intake.loader.load(marker)
        assert intake.run_once() == "idle"
        incident, = incidents(repair)
        assert intake.run_once() == "idle"
        assert _ScheduledFailureRepair(repair).run_once() == "idle"
        assert incidents(repair) == [incident]
        events = repair.timeline.events(incident)
        assert [event.event_type for event in events] == ["SELF_REPAIR_OBSERVED", "SELF_REPAIR_INITIATIVE_ADMITTED"]
        observed = events[0].payload["data"]["observation"]
        assert any(frame["path"] == "adapters/postgres/scheduled_work_failure.py"
                   and frame["function"].startswith("load:") for frame in observed["frames"])
        assert turn.text not in str(events)
        assert journal.committed_reply(turn) is None
        assert service.store.load(predecessor.spec.work_id, **service._owner) == predecessor
        assert timeline.events(marker) == before




@pytest.mark.parametrize("blocked_recovery", [True], indirect=True)
@pytest.mark.parametrize("privacy_revoked", ["source_stop", "erase", "schedule_stop", "alias_changed"])
def test_standing_mandate_repairs_actual_fault_without_resuming_stopped_work(
        blocked_recovery, privacy_revoked, monkeypatch):
    service, turn, journal, predecessor, marker, repair, now = blocked_recovery
    service.store.request_stop(predecessor.spec.work_id, **service._owner, now=now)
    timeline = PostgresOperationalTimeline(service.store._engine, service.store.scope)
    stop_thread = service.store._provider_recovery_stop_thread(predecessor.spec)
    old_events = timeline.events(marker), timeline.events(stop_thread)
    old_turn_events = repair.timeline.events("conversation:" + turn.message_id)
    with runtime_role(service.store._engine):
        assert _ScheduledFailureRepair(repair).run_once() == "idle"
        incident, = incidents(repair)
        assert _ScheduledFailureRepair(repair).run_once() == "idle"
        assert incidents(repair) == [incident]
        events = repair.timeline.events(incident)
        assert [event.event_type for event in events] == ["SELF_REPAIR_OBSERVED", "SELF_REPAIR_INITIATIVE_ADMITTED"]
        observed = events[0].payload["data"]
        source = observed["source"]
        assert source["contract"] == HOST_SCHEDULED_FAILURE_SOURCE
        assert source["observation_sha256"] == observed["fingerprint"]
        assert observed["source_sha256"] == canonical_sha256(source)
        assert source["privacy_source"]["ingress_sha256"] == old_turn_events[0].payload_sha256
        assert source["privacy_source"]["principal_id"] == turn.principal_id
        assert turn.principal_id != str(service.owner_principal_id)
        assert source["technical_provenance"] == {
            "schedule_id": marker, "recovery_stop_thread": stop_thread, "work_id": predecessor.spec.work_id,
            "scheduled_marker_sha256": old_events[0][0].payload_sha256,
            "predecessor_spec_sha256": predecessor.spec.sha256,
            "recovery_stop_sha256": old_events[1][0].payload_sha256,
            "source_ingress_data_sha256": predecessor.spec.source_ingress_sha256,
        }
        assert source["technical_provenance"]["source_ingress_data_sha256"] != old_turn_events[0].payload_sha256
        adopted = repair.timeline.events("intent:" + observed["intent_id"])[0].payload["data"]
        assert adopted["adoption_source"] == "automatic_runtime_failure" and adopted["unprompted"] is True
        assert turn.text not in str(events) + str(adopted)
        assert journal.committed_reply(turn) is None
        assert repair.timeline.events("conversation:" + turn.message_id) == old_turn_events
        assert service.store.load(predecessor.spec.work_id, **service._owner) == predecessor
        assert (timeline.events(marker), timeline.events(stop_thread)) == old_events
        assert current_repair_binding(repair.authority, incident, now=now)["incident_event_sha256"] == events[0].payload_sha256
        # Even fully rehashed source metadata cannot substitute false evidence.
        for digest_key in ("recovery_stop_sha256", "source_ingress_data_sha256"):
            invalid = deepcopy(source)
            invalid["technical_provenance"][digest_key] = "f" * 64
            invalid["technical_provenance_sha256"] = canonical_sha256(invalid["technical_provenance"])
            with repair.owner._engine.begin() as connection:
                repair.owner._authorize(connection, now)
                assert not _host_source_current(connection, repair.owner, invalid,
                    observation_sha256=observed["fingerprint"], source_sha256=canonical_sha256(invalid))
        if privacy_revoked == "source_stop":
            journal.request_turn_stop(turn)
        elif privacy_revoked == "erase":
            journal.erase_message_content(principal_id=turn.principal_id, message_id=turn.message_id,
                                          operation_sha256="b" * 64, erased_at=now)
        elif privacy_revoked == "schedule_stop":
            with service.store._engine.begin() as connection:
                service.store._scope(connection)
                transition_timeline_thread(connection, service.store.scope, thread_id=marker,
                    checkpoint_id=marker + ":chain-stop", expected_revision=1, occurred_at=now,
                    event_type="WORK_CHAIN_STOP_REQUESTED", phase="stopped", status="completed",
                    data={"work_id": predecessor.spec.work_id})
        else:
            monkeypatch.delenv("EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE")
        with pytest.raises(ValueError, match="repair source or mandate changed"):
            current_repair_binding(repair.authority, incident, now=now)


@pytest.mark.parametrize("blocked_by", ["source_stop", "erase"])
@pytest.mark.parametrize("work_stopped", [False, True])
def test_existing_privacy_fences_refuse_both_background_admissions(blocked_recovery, blocked_by, work_stopped):
    service, turn, journal, predecessor, _, repair, now = blocked_recovery
    if work_stopped:
        service.store.request_stop(predecessor.spec.work_id, **service._owner, now=now)
    if blocked_by == "source_stop":
        journal.request_turn_stop(turn)
    else:
        journal.erase_message_content(principal_id=turn.principal_id, message_id=turn.message_id,
                                      operation_sha256="b" * 64, erased_at=now)
    with runtime_role(service.store._engine):
        intake = _ScheduledFailureRepair(repair)
        assert intake.run_once() == "idle"
        assert incidents(repair) == []
