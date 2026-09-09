from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
import logging
import os
from pathlib import Path
from threading import Event
from typing import Iterator
from uuid import NAMESPACE_URL, uuid5

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine, make_url

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    PostgresOperationalTimeline,
    TimelineScope,
    external_timeline_scope,
    operational_checkpoints,
    operational_events,
)
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.memory_outbox import (
    DurableMemoryRetentionJob,
    PostgresMemoryOutbox,
)
from eva.adapters.postgres.telegram_ingress import PostgresTelegramIngressStore
from eva.adapters.postgres.telegram_pairing import PostgresTelegramPairingStore
from eva.adapters.postgres.telegram_group_consent import (
    PostgresTelegramGroupConsentStore,
)
from eva.adapters.postgres.telegram_group_observation import (
    PostgresTelegramGroupObservationStore,
)
from eva.adapters.postgres.external_conversation_tools import (
    PostgresExternalConversationTools,
)
from eva.adapters.connectors.home_assistant import (
    HomeAssistantConversationReadPlanner,
    HomeAssistantToolAdapter,
)
from eva.adapters.connectors.agentmail import (
    AgentMailConfig,
    AgentMailConversationReadPlanner,
    AgentMailReadAdapter,
    MailInboxPage,
    MailMessageSummary,
)
from eva.adapters.connectors.home_assistant_automations import (
    HomeAssistantConversationAutomationPlanner,
)
from eva.adapters.connectors.home_assistant_management import (
    HomeAssistantConversationHacsPlanner,
    HomeAssistantConversationMigrationPlanner,
    HomeAssistantHacsManagementAdapter,
    HomeAssistantMigrationPlanAdapter,
)
from eva.providers.home_assistant_advanced_store import (
    PostgresAdvancedTaskStore,
)
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.connectors.home_assistant_advanced_tasks import (
    AdvancedTaskPlan,
    HomeAssistantDevice,
    HomeAssistantEntity,
    HomeAssistantIntegration,
    HomeAssistantInventory,
    HomeAssistantRepository,
    TuyaToLocalTuyaPlanner,
)
from eva.application.conversation import (
    ConversationActionCall,
    ConversationConflict,
    ConversationToolPreparation,
    ConversationTurn,
    GroundedReply,
)
from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.application.memory_audience import (
    MemoryAudience,
    TelegramGroupConsentReference,
)
from eva.application.research import ResearchArtifact, ResearchSource
from eva.application.telegram_ingress import (
    TelegramCoreReply,
    TelegramInboundMessage,
    TelegramIngressWorker,
    TelegramPrincipalBinding,
    TelegramPrincipalBindings,
)
from eva.application.telegram_pairing import (
    TelegramDynamicBinding,
    TelegramPairingChallenge,
    TelegramPairingResponder,
    derive_pairing_code,
    pairing_expiry,
)
from eva.application.telegram_group_consent import (
    TelegramGroupConsent,
    TelegramGroupConsentResponder,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


EVA2_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def test_legacy_external_tool_admission_recovers_and_checks_receipt_hash() -> None:
    class Planner:
        action_names = ("legacy_tool",)

    class Provider:
        pass

    engine = sa.create_engine("sqlite://")
    tools = PostgresExternalConversationTools(engine, Planner(), Provider())  # type: ignore[arg-type]
    turn = ConversationTurn.private(
        "legacy-tool-admission",
        "installation",
        "jaap",
        "conversation",
        "message:legacy-tool",
        "Voer de oude opdracht uit",
        "2026-09-01T10:00:00Z",
    )
    configuration_sha256 = "8" * 64
    admission = {
        "action_call_id": "call:legacy",
        "action_name": "legacy_tool",
        "action_arguments": {"message": "legacy"},
        "conversation_id": turn.conversation_id,
        "principal_id": turn.principal_id,
        "receipt_output": {
            "tool_configuration_sha256": configuration_sha256,
        },
        "request_arguments": {"message": "legacy"},
        "request_expected_proof": {"text": "legacy"},
        "request_tool_name": "proof-echo",
        "source_message_id": turn.message_id,
    }

    request = tools._request_from_admission(  # noqa: SLF001
        turn, "legacy_tool", admission
    )

    assert request.tool_configuration_sha256 == configuration_sha256
    assert request.observation_tool_name is None
    conflicting = {
        **admission,
        "request_tool_configuration_sha256": "9" * 64,
    }
    with pytest.raises(ConversationConflict, match="resume command is malformed"):
        tools._request_from_admission(  # noqa: SLF001
            turn, "legacy_tool", conflicting
        )
    engine.dispose()


def test_external_timeline_scope_preserves_existing_ids_and_error() -> None:
    scope = external_timeline_scope(
        "tenant-a",
        "installation-a",
        "scope is required",
    )

    assert scope == TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, "eva2:tenant-a"),
        installation_id=uuid5(
            NAMESPACE_URL,
            "eva2:tenant-a:installation-a",
        ),
    )
    with pytest.raises(ValueError, match="^scope is required$"):
        external_timeline_scope(" ", "installation-a", "scope is required")


def _pairing_bindings() -> TelegramPrincipalBindings:
    return TelegramPrincipalBindings(
        (
            TelegramPrincipalBinding(101, "person:jaap", ("101",)),
            TelegramPrincipalBinding(202, "person:frank", ("202",)),
        )
    )


def _upgrade(engine: Engine, config: Config) -> None:
    command.upgrade(config, "4b_0000_core_namespace")
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        config.attributes["connection"] = connection
        try:
            command.upgrade(config, "4b_0002_episode_atomicity")
        finally:
            config.attributes.pop("connection", None)
    command.upgrade(config, "head")


@pytest.fixture(scope="module")
def timeline_engine() -> Iterator[Engine]:
    dsn = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.fail("EVA2_TEST_POSTGRES_DSN is required")
    url = make_url(dsn)
    if (
        url.get_backend_name() != "postgresql"
        or not url.database
        or not url.database.startswith("eva2_living_disposable_")
    ):
        pytest.fail("timeline gate requires an explicit disposable PostgreSQL DB")
    engine = sa.create_engine(url, pool_pre_ping=False)
    # Alembic's fileConfig disables existing loggers. Restore their original
    # state so later API privacy-diagnostic tests still observe real records.
    logger_states = {name: logger.disabled for name, logger in logging.Logger.manager.loggerDict.items()
                     if isinstance(logger, logging.Logger)}
    config = Config(str(EVA2_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    try:
        with engine.connect() as connection:
            assert int(connection.scalar(sa.text("SHOW server_version_num"))) == 160013
            ledger = connection.scalar(
                sa.text("SELECT to_regclass('public.eva_alembic_version')")
            )
            if ledger is not None:
                assert (
                    connection.scalar(
                        sa.text("SELECT count(*) FROM public.eva_alembic_version")
                    )
                    == 0
                )
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'eva_%'"
                    )
                )
                == 0
            )
        _upgrade(engine, config)
        yield engine
    finally:
        try:
            command.downgrade(config, "base")
        finally:
            engine.dispose()
            for name, disabled in logger_states.items():
                logging.getLogger(name).disabled = disabled


def _scope(label: str) -> TimelineScope:
    return TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, f"eva-living-{label}-tenant"),
        installation_id=uuid5(NAMESPACE_URL, f"eva-living-{label}-installation"),
    )


def test_timeline_survives_restart_and_escalates_third_identical_failure(
    timeline_engine: Engine,
) -> None:
    scope = _scope("recovery")
    first_process = PostgresOperationalTimeline(timeline_engine, scope)
    admitted = first_process.create_thread(
        thread_id="telegram:thread:42",
        checkpoint_id="checkpoint:ingress:1",
        occurred_at=NOW,
        data={"message": "zet de verwarming op 21"},
    )
    waiting = first_process.transition(
        thread_id=admitted.thread_id,
        expected_revision=admitted.revision,
        checkpoint_id="checkpoint:thermostat:prepared",
        operation_id="operation:thermostat:42",
        phase="tool_prepared",
        status="waiting_for_proof",
        event_type="TOOL_PREPARED",
        occurred_at=NOW + timedelta(seconds=1),
        deadline=NOW + timedelta(seconds=11),
        data={"entity_id": "climate.woonkamer", "target_temperature": 21},
    )

    timeline_engine.dispose()
    restarted_engine = sa.create_engine(timeline_engine.url, pool_pre_ping=False)
    restarted = PostgresOperationalTimeline(restarted_engine, scope)
    assert restarted.load(admitted.thread_id) == waiting

    state = waiting
    for ordinal in range(1, 4):
        state = restarted.record_recovery_failure(
            thread_id=state.thread_id,
            expected_revision=state.revision,
            checkpoint_id=f"checkpoint:recovery:{ordinal}",
            operation_id=state.operation_id,
            error_text="thermostaat antwoord kon niet worden geïnterpreteerd",
            occurred_at=NOW + timedelta(seconds=ordinal + 1),
        )
    assert state.retry_count == 3
    assert state.status == "needs_help"
    assert [event.event_type for event in restarted.events(state.thread_id)] == [
        "INGRESS_COMMITTED",
        "TOOL_PREPARED",
        "RECOVERY_FAILED",
        "RECOVERY_FAILED",
        "RECOVERY_NEEDS_HELP",
    ]
    with pytest.raises(CheckpointConflict, match="revision changed"):
        restarted.transition(
            thread_id=state.thread_id,
            expected_revision=1,
            checkpoint_id="checkpoint:stale",
            phase="completed",
            status="completed",
            event_type="SHOULD_NOT_EXIST",
            occurred_at=NOW + timedelta(seconds=9),
            data={},
        )
    restarted_engine.dispose()


def test_event_history_is_immutable_and_projection_is_rebuildable(
    timeline_engine: Engine,
) -> None:
    scope = _scope("rebuild")
    timeline = PostgresOperationalTimeline(timeline_engine, scope)
    expected = timeline.create_thread(
        thread_id="telegram:thread:rebuild",
        checkpoint_id="checkpoint:admitted",
        occurred_at=NOW,
        data={"message": "hoi eva"},
    )

    with pytest.raises(sa.exc.DBAPIError, match="append_only"):
        with timeline_engine.begin() as connection:
            connection.execute(
                sa.update(operational_events)
                .where(operational_events.c.thread_id == expected.thread_id)
                .values(event_type="MUTATED")
            )

    with timeline_engine.begin() as connection:
        connection.execute(
            sa.delete(operational_checkpoints).where(
                operational_checkpoints.c.tenant_id == scope.tenant_id,
                operational_checkpoints.c.installation_id == scope.installation_id,
                operational_checkpoints.c.thread_id == expected.thread_id,
            )
        )
    rebuilt = timeline.rebuild_projection(expected.thread_id)
    assert rebuilt == expected
    assert timeline.load(expected.thread_id) == expected


def test_row_scope_hides_another_installation(timeline_engine: Engine) -> None:
    visible_scope = _scope("visible")
    hidden_scope = TimelineScope(
        tenant_id=visible_scope.tenant_id,
        installation_id=uuid5(NAMESPACE_URL, "eva-living-hidden-installation"),
    )
    visible = PostgresOperationalTimeline(timeline_engine, visible_scope)
    visible.create_thread(
        thread_id="telegram:thread:scoped",
        checkpoint_id="checkpoint:scoped",
        occurred_at=NOW,
        data={"message": "alleen voor deze installatie"},
    )
    hidden = PostgresOperationalTimeline(timeline_engine, hidden_scope)
    assert hidden.events("telegram:thread:scoped") == ()


def test_telegram_ingress_is_person_bound_delivered_and_restart_idempotent(
    timeline_engine: Engine,
) -> None:
    message = TelegramInboundMessage(71001, "101", 501, 101, NOW, "Hoi Eva")
    store = PostgresTelegramIngressStore(
        timeline_engine, tenant_id="telegram-tenant", installation_id="telegram-home"
    )
    admitted = store.admit(message, principal_id="person:jaap")
    assert admitted is not None
    text = "Hoi Jaap"
    reply = GroundedReply(
        "telegram:101",
        "telegram:101:501",
        text,
        (),
        "2026-08-23T12:00:01Z",
        "test",
    )
    prepared = store.prepare_reply(
        admitted,
        TelegramCoreReply(
            reply.conversation_id, reply.message_id, reply.text, reply.text_sha256
        ),
        now=NOW + timedelta(seconds=1),
    )
    claimed = store.claim_delivery(prepared, now=NOW + timedelta(seconds=2))
    assert claimed is not None
    store.acknowledge_delivery(
        claimed, "telegram:101:9001", now=NOW + timedelta(seconds=3)
    )

    restarted = PostgresTelegramIngressStore(
        timeline_engine, tenant_id="telegram-tenant", installation_id="telegram-home"
    )
    assert restarted.admit(message, principal_id="person:jaap") is None
    assert restarted.next_offset() == 71002
    timeline = restarted._timeline  # noqa: SLF001
    assert [
        event.event_type for event in timeline.events("telegram-inbound:71001")
    ] == [
        "TELEGRAM_INGRESS_ADMITTED",
        "TELEGRAM_REPLY_PREPARED",
        "TELEGRAM_DELIVERY_CLAIMED",
        "TELEGRAM_REPLY_DELIVERED",
    ]


def test_telegram_restart_after_delivery_claim_never_blindly_resends(
    timeline_engine: Engine,
) -> None:
    message = TelegramInboundMessage(72001, "202", 601, 202, NOW, "Ben je er?")
    store = PostgresTelegramIngressStore(
        timeline_engine,
        tenant_id="telegram-crash-tenant",
        installation_id="telegram-crash-home",
        delivery_lease_seconds=10,
    )
    admitted = store.admit(message, principal_id="person:frank")
    assert admitted is not None
    text = "Ik ben er."
    prepared = store.prepare_reply(
        admitted,
        TelegramCoreReply(
            "telegram:202",
            "telegram:202:601",
            text,
            hashlib.sha256(text.encode()).hexdigest(),
        ),
        now=NOW + timedelta(seconds=1),
    )
    assert store.claim_delivery(prepared, now=NOW + timedelta(seconds=2)) is not None

    restarted = PostgresTelegramIngressStore(
        timeline_engine,
        tenant_id="telegram-crash-tenant",
        installation_id="telegram-crash-home",
        delivery_lease_seconds=10,
    )
    recovered = restarted.admit(message, principal_id="person:frank")
    assert recovered is not None
    assert restarted.claim_delivery(recovered, now=NOW + timedelta(seconds=13)) is None
    state = restarted._timeline.load("telegram-inbound:72001")  # noqa: SLF001
    assert state.status == "needs_help"
    assert state.phase == "delivery_ambiguous"
    assert restarted.next_offset() == 72002


def test_telegram_pairing_is_private_two_sided_and_survives_restart(
    timeline_engine: Engine,
) -> None:
    store = PostgresTelegramPairingStore(
        timeline_engine,
        tenant_id="telegram-pairing",
        installation_id="home",
        trusted_bindings=_pairing_bindings(),
    )
    secret = b"p" * 32
    first = TelegramPairingChallenge(
        derive_pairing_code(secret, telegram_user_id=404, update_id=81001),
        404,
        "404",
        pairing_expiry(NOW),
    )
    accepted = store.start(first, update_id=81001, now=NOW)
    assert accepted == first
    payload = store._timeline.events("telegram-pairing-request:81001")[0].payload  # noqa: SLF001
    assert first.code not in str(payload)

    # A retry after a crash gets the original code, never a second valid code.
    restarted = PostgresTelegramPairingStore(
        timeline_engine,
        tenant_id="telegram-pairing",
        installation_id="home",
        trusted_bindings=_pairing_bindings(),
    )
    recovered = TelegramPairingChallenge(
        derive_pairing_code(secret, telegram_user_id=404, update_id=81001),
        404,
        "404",
        pairing_expiry(NOW + timedelta(minutes=1)),
    )
    assert recovered is not first
    assert (
        restarted.start(recovered, update_id=81001, now=NOW + timedelta(minutes=1))
        == first
    )
    assert (
        restarted.confirm(
            first.code,
            confirmer_user_id=101,
            confirmer_chat_id="101",
            now=NOW + timedelta(seconds=1),
        ).principal_id
        == "person:jaap"
    )
    assert restarted.resolve(telegram_user_id=404, chat_id="404") == "person:jaap"
    assert restarted.resolve(telegram_user_id=404, chat_id="-20") is None
    assert (
        restarted.confirm(
            first.code,
            confirmer_user_id=101,
            confirmer_chat_id="101",
            now=NOW + timedelta(seconds=2),
        ).telegram_user_id
        == 404
    )


def test_dynamically_paired_account_reaches_normal_ingress_after_restart(
    timeline_engine: Engine,
) -> None:
    tenant_id = "telegram-dynamic-ingress"
    installation_id = "home"
    trusted_bindings = _pairing_bindings()
    first_store = PostgresTelegramPairingStore(
        timeline_engine,
        tenant_id=tenant_id,
        installation_id=installation_id,
        trusted_bindings=trusted_bindings,
    )
    responder = TelegramPairingResponder(first_store, derivation_secret=b"p" * 32)
    request_text = responder.begin(
        update_id=81007,
        sender_id=404,
        chat_id="404",
        now=NOW,
    )
    assert request_text is not None
    code = request_text.rsplit(maxsplit=1)[-1]
    assert (
        responder.confirm(
            code=code,
            sender_id=101,
            chat_id="101",
            now=NOW + timedelta(seconds=1),
        )
        == "Gekoppeld."
    )
    assert trusted_bindings.resolve(404, "404") is None

    restarted_pairing = PostgresTelegramPairingStore(
        timeline_engine,
        tenant_id=tenant_id,
        installation_id=installation_id,
        trusted_bindings=trusted_bindings,
    )
    message = TelegramInboundMessage(
        81008,
        "404",
        904,
        404,
        NOW + timedelta(seconds=2),
        "Ken je mij nog?",
    )
    core_calls = []
    deliveries: list[str] = []

    class Provider:
        def poll(self, *, offset):  # type: ignore[no-untyped-def]
            return (message,)

        def send_reply(self, inbound, text):  # type: ignore[no-untyped-def]
            assert inbound == message
            deliveries.append(text)
            return "telegram:404:9904"

    class Core:
        def respond(self, inbound, *, principal_id, audience):  # type: ignore[no-untyped-def]
            core_calls.append((inbound, principal_id, audience))
            text = "Ik herken hetzelfde profiel."
            return TelegramCoreReply(
                "telegram:404",
                "telegram:404:904",
                text,
                hashlib.sha256(text.encode()).hexdigest(),
            )

    first_ingress = PostgresTelegramIngressStore(
        timeline_engine,
        tenant_id=tenant_id,
        installation_id=installation_id,
    )
    worker = TelegramIngressWorker(
        Provider(),
        Core(),
        first_ingress,
        trusted_bindings,
        dynamic_bindings=restarted_pairing,
    )

    assert worker.run_once() == "processed"
    assert len(core_calls) == 1
    assert core_calls[0][1] == "person:jaap"
    assert core_calls[0][2].kind == "private_person"
    assert core_calls[0][2].principal_id == "person:jaap"
    assert deliveries == ["Ik herken hetzelfde profiel."]

    replayed_pairing = PostgresTelegramPairingStore(
        timeline_engine,
        tenant_id=tenant_id,
        installation_id=installation_id,
        trusted_bindings=trusted_bindings,
    )
    replayed_ingress = PostgresTelegramIngressStore(
        timeline_engine,
        tenant_id=tenant_id,
        installation_id=installation_id,
    )
    replayed_worker = TelegramIngressWorker(
        Provider(),
        Core(),
        replayed_ingress,
        trusted_bindings,
        dynamic_bindings=replayed_pairing,
    )

    assert replayed_worker.run_once() == "processed"
    assert len(core_calls) == 1
    assert deliveries == ["Ik herken hetzelfde profiel."]
    assert replayed_ingress.next_offset() == 81009
    events = replayed_ingress._timeline.events(  # noqa: SLF001
        "telegram-inbound:81008"
    )
    assert [event.event_type for event in events] == [
        "TELEGRAM_INGRESS_ADMITTED",
        "TELEGRAM_REPLY_PREPARED",
        "TELEGRAM_DELIVERY_CLAIMED",
        "TELEGRAM_REPLY_DELIVERED",
    ]


def test_dynamic_binding_is_scope_isolated_and_conflict_rejects_without_text(
    timeline_engine: Engine,
) -> None:
    trusted_bindings = _pairing_bindings()
    pairing = PostgresTelegramPairingStore(
        timeline_engine,
        tenant_id="telegram-dynamic-conflict",
        installation_id="home",
        trusted_bindings=trusted_bindings,
    )
    challenge = TelegramPairingChallenge(
        "EVA-ABCDEFGHJKLM", 404, "404", pairing_expiry(NOW)
    )
    pairing.start(challenge, update_id=81009, now=NOW)
    assert pairing.confirm(
        challenge.code,
        confirmer_user_id=101,
        confirmer_chat_id="101",
        now=NOW + timedelta(seconds=1),
    ) == TelegramDynamicBinding(404, "person:jaap")
    other_scope = PostgresTelegramPairingStore(
        timeline_engine,
        tenant_id="telegram-dynamic-conflict-other",
        installation_id="home",
        trusted_bindings=trusted_bindings,
    )
    assert other_scope.resolve(telegram_user_id=404, chat_id="404") is None

    message = TelegramInboundMessage(
        81010,
        "404",
        905,
        404,
        NOW + timedelta(seconds=2),
        "Dit mag niet bij Frank terechtkomen.",
    )

    class Provider:
        def poll(self, *, offset):  # type: ignore[no-untyped-def]
            return (message,)

        def send_reply(self, inbound, text):  # type: ignore[no-untyped-def]
            raise AssertionError((inbound, text))

    class Core:
        def respond(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError((args, kwargs))

    ingress = PostgresTelegramIngressStore(
        timeline_engine,
        tenant_id="telegram-dynamic-conflict",
        installation_id="home",
    )
    worker = TelegramIngressWorker(
        Provider(),
        Core(),
        ingress,
        TelegramPrincipalBindings(
            (TelegramPrincipalBinding(404, "person:frank", ("404",)),)
        ),
        dynamic_bindings=pairing,
    )

    assert worker.run_once() == "processed"
    events = ingress._timeline.events("telegram-inbound:81010")  # noqa: SLF001
    assert [event.event_type for event in events] == ["TELEGRAM_INGRESS_REJECTED"]
    data = events[0].payload["data"]
    assert data["principal_id"] is None
    assert data["text"] is None
    assert data["reason"] == "sender_or_chat_not_authorized"


def test_telegram_pairing_rejects_self_confirmation_and_expired_codes(
    timeline_engine: Engine,
) -> None:
    store = PostgresTelegramPairingStore(
        timeline_engine,
        tenant_id="telegram-pairing-reject",
        installation_id="home",
        trusted_bindings=_pairing_bindings(),
    )
    challenge = TelegramPairingChallenge(
        "EVA-ABCDEFGHJKLM", 505, "505", pairing_expiry(NOW)
    )
    store.start(challenge, update_id=81002, now=NOW)

    assert (
        store.confirm(
            challenge.code,
            confirmer_user_id=505,
            confirmer_chat_id="505",
            now=NOW + timedelta(seconds=1),
        )
        is None
    )
    assert (
        store.confirm(
            challenge.code,
            confirmer_user_id=101,
            confirmer_chat_id="101",
            now=pairing_expiry(NOW) + timedelta(seconds=1),
        )
        is None
    )
    assert store.resolve(telegram_user_id=505, chat_id="505") is None


def test_telegram_pairing_resumes_the_crash_between_request_and_waiting_state(
    timeline_engine: Engine,
) -> None:
    store = PostgresTelegramPairingStore(
        timeline_engine,
        tenant_id="telegram-pairing-resume",
        installation_id="home",
        trusted_bindings=_pairing_bindings(),
    )
    challenge = TelegramPairingChallenge(
        "EVA-ABCDEFGHJKLM", 606, "606", pairing_expiry(NOW)
    )
    thread_id = "telegram-pairing-request:81003"
    store._timeline.create_thread(  # noqa: SLF001
        thread_id=thread_id,
        checkpoint_id=f"{thread_id}:requested",
        operation_id="telegram-pairing-request:81003",
        occurred_at=NOW,
        data=store._challenge_data(challenge, update_id=81003),  # noqa: SLF001
        event_type="TELEGRAM_PAIRING_REQUESTED",
        phase="pairing_requested",
        status="active",
    )

    assert store.start(challenge, update_id=81003, now=NOW) == challenge
    assert store._timeline.load(thread_id).status == "waiting_for_proof"  # noqa: SLF001


def test_telegram_pairing_never_overwrites_an_account_bound_by_another_code(
    timeline_engine: Engine,
) -> None:
    store = PostgresTelegramPairingStore(
        timeline_engine,
        tenant_id="telegram-pairing-collision",
        installation_id="home",
        trusted_bindings=_pairing_bindings(),
    )
    first = TelegramPairingChallenge(
        "EVA-ABCDEFGHJKLM", 707, "707", pairing_expiry(NOW)
    )
    second = TelegramPairingChallenge(
        "EVA-NPQRSTVWXYZ2", 707, "707", pairing_expiry(NOW)
    )
    store.start(first, update_id=81004, now=NOW)
    store.start(second, update_id=81005, now=NOW)
    assert store.confirm(
        first.code,
        confirmer_user_id=101,
        confirmer_chat_id="101",
        now=NOW + timedelta(seconds=1),
    ) == TelegramDynamicBinding(707, "person:jaap")
    with pytest.raises(RuntimeError, match="already bound"):
        store.confirm(
            second.code,
            confirmer_user_id=202,
            confirmer_chat_id="202",
            now=NOW + timedelta(seconds=2),
        )
    assert store.resolve(telegram_user_id=707, chat_id="707") == "person:jaap"


def test_telegram_pairing_concurrent_restarts_share_one_waiting_challenge(
    timeline_engine: Engine,
) -> None:
    challenge = TelegramPairingChallenge(
        "EVA-ABCDEFGHJKLM", 808, "808", pairing_expiry(NOW)
    )

    def resume() -> TelegramPairingChallenge:
        return PostgresTelegramPairingStore(
            timeline_engine,
            tenant_id="telegram-pairing-concurrent",
            installation_id="home",
            trusted_bindings=_pairing_bindings(),
        ).start(challenge, update_id=81006, now=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _value: resume(), range(2)))
    assert results == (challenge, challenge)


def test_telegram_poison_turn_stops_after_three_identical_processing_failures(
    timeline_engine: Engine,
) -> None:
    message = TelegramInboundMessage(73001, "303", 701, 303, NOW, "Kapotte beurt")
    store = PostgresTelegramIngressStore(
        timeline_engine,
        tenant_id="telegram-poison-tenant",
        installation_id="telegram-poison-home",
    )

    for ordinal in range(1, 4):
        job = store.admit(message, principal_id="person:mark")
        assert job is not None
        store.fail_processing(
            job,
            ValueError("dezelfde parsefout"),
            now=NOW + timedelta(seconds=ordinal),
        )

    assert store.admit(message, principal_id="person:mark") is None
    state = store._timeline.load("telegram-inbound:73001")  # noqa: SLF001
    assert state.status == "needs_help"
    assert state.retry_count == 3
    assert store.next_offset() == 73002


def test_conversation_replay_returns_one_durable_reply_and_one_memory_queue(
    timeline_engine: Engine,
) -> None:
    scope = _scope("conversation-replay")
    journal = PostgresConversationJournal(timeline_engine, scope)
    timeline = PostgresOperationalTimeline(timeline_engine, scope)
    turn = ConversationTurn.private(
        tenant_id="tenant",
        installation_id="installation",
        principal_id="jaap",
        conversation_id="telegram:jaap",
        message_id="telegram:restart:4463",
        text="Kies zelf een hobby.",
        occurred_at="2026-08-23T12:45:58Z",
    )

    admitted = journal.admit(turn)
    assert journal.admit(turn) == admitted
    assert [event.event_type for event in timeline.events(admitted.thread_id)] == [
        "INGRESS_COMMITTED"
    ]

    changed = ConversationTurn.private(
        tenant_id=turn.tenant_id,
        installation_id=turn.installation_id,
        principal_id=turn.principal_id,
        conversation_id=turn.conversation_id,
        message_id=turn.message_id,
        text="Een andere inhoud onder hetzelfde bericht-ID.",
        occurred_at=turn.occurred_at,
    )
    with pytest.raises(ConversationConflict, match="different ingress"):
        journal.admit(changed)

    reply = GroundedReply(
        conversation_id=turn.conversation_id,
        message_id=turn.message_id,
        text="Ik kies koken slimmer maken voor thuis.",
        evidence_memory_ids=("memory:1",),
        generated_at=turn.occurred_at,
        provider="live-model",
    )
    assert journal.commit_reply(turn, reply) == reply
    assert journal.commit_reply(turn, reply) == reply
    assert journal.committed_reply(turn) == reply

    changed_reply = GroundedReply(
        conversation_id=reply.conversation_id,
        message_id=reply.message_id,
        text="Een afwijkend tweede antwoord.",
        evidence_memory_ids=reply.evidence_memory_ids,
        generated_at=reply.generated_at,
        provider=reply.provider,
    )
    with pytest.raises(ConversationConflict, match="different reply"):
        journal.commit_reply(turn, changed_reply)

    completed = journal.load(turn)
    queued = journal.memory_queued(
        turn,
        completed,
        operation_document_id="telegram:jaap:exchange:4463",
    )
    assert (
        journal.memory_queued(
            turn,
            queued,
            operation_document_id="telegram:jaap:exchange:4463",
        )
        == queued
    )
    events = timeline.events(admitted.thread_id)
    assert [event.event_type for event in events] == [
        "INGRESS_COMMITTED",
        "REPLY_COMMITTED",
        "MEMORY_RETAIN_QUEUED",
    ]


def test_conversation_content_erasure_is_physical_scoped_and_replay_safe(
    timeline_engine: Engine,
) -> None:
    scope = _scope("conversation-erasure")
    journal = PostgresConversationJournal(timeline_engine, scope)
    turn = ConversationTurn.private(
        tenant_id="tenant",
        installation_id="installation",
        principal_id="person:jaap",
        conversation_id="telegram:jaap",
        message_id="telegram:erase:41",
        text="verwijderbare bronzin 41",
        occurred_at="2026-08-23T12:51:00Z",
    )
    journal.admit(turn)
    journal.commit_reply(
        turn,
        GroundedReply(
            turn.conversation_id,
            turn.message_id,
            "verwijderbaar antwoord 41",
            (),
            turn.occurred_at,
            "test",
        ),
    )
    assert [message.text for message in journal.recent(turn)] == [
        "verwijderbare bronzin 41",
        "verwijderbaar antwoord 41",
    ]

    with pytest.raises(ConversationConflict, match="does not exist"):
        journal.erase_message_content(
            principal_id="person:lola",
            message_id=turn.message_id,
            operation_sha256="1" * 64,
            erased_at=NOW,
        )

    # Even the scoped runtime role cannot use the narrow SECURITY DEFINER
    # function to alter a checkpoint or add an unrelated payload field.
    with timeline_engine.connect() as connection:
        transaction = connection.begin()
        journal._scope(connection)  # noqa: SLF001
        row = connection.execute(
            sa.select(operational_events).where(
                operational_events.c.tenant_id == scope.tenant_id,
                operational_events.c.installation_id == scope.installation_id,
                operational_events.c.event_type == "INGRESS_COMMITTED",
            )
        ).one()
        forged_payload = dict(row.payload)
        forged_data = dict(forged_payload["data"])
        forged_data.pop("text")
        forged_data["content_erasure"] = {
            "contract": "EvaConversationContentErasure/v1",
            "erased_at": "2026-08-23T12:00:00Z",
            "message_sha256": hashlib.sha256(
                turn.message_id.encode("utf-8")
            ).hexdigest(),
            "operation_sha256": "2" * 64,
            "principal_sha256": hashlib.sha256(
                turn.principal_id.encode("utf-8")
            ).hexdigest(),
            "status": "erased",
        }
        forged_payload["data"] = forged_data
        forged_payload["unrelated"] = "mutation"
        forged_json = canonical_json_bytes(forged_payload).decode("utf-8")
        with pytest.raises(sa.exc.DBAPIError, match="payload_invalid"):
            connection.scalar(
                sa.text(
                    "SELECT eva_core.erase_conversation_event_content("
                    ":event_id, :expected_hash, :payload, :new_hash)"
                ),
                {
                    "event_id": row.event_id,
                    "expected_hash": bytes(row.payload_sha256),
                    "payload": forged_json,
                    "new_hash": bytes.fromhex(canonical_sha256(forged_payload)),
                },
            )
        transaction.rollback()

    receipt = journal.erase_message_content(
        principal_id=turn.principal_id,
        message_id=turn.message_id,
        operation_sha256="2" * 64,
        erased_at=NOW,
    )
    assert receipt.redacted_event_count == 2
    assert receipt.replayed is False
    assert (
        receipt.principal_sha256
        == hashlib.sha256(turn.principal_id.encode("utf-8")).hexdigest()
    )
    assert (
        receipt.message_sha256
        == hashlib.sha256(turn.message_id.encode("utf-8")).hexdigest()
    )
    assert journal.recent(turn) == ()
    with pytest.raises(ConversationConflict, match="content was erased"):
        journal.committed_reply(turn)
    with pytest.raises(ConversationConflict, match="different ingress"):
        journal.admit(turn)

    restarted = PostgresConversationJournal(timeline_engine, scope)
    replay = restarted.erase_message_content(
        principal_id=turn.principal_id,
        message_id=turn.message_id,
        operation_sha256="3" * 64,
        erased_at=NOW + timedelta(seconds=1),
    )
    assert replay.replayed is True
    assert replay.redacted_event_count == 0
    assert replay.operation_sha256 == "2" * 64

    data = operational_events.c.payload["data"]
    with timeline_engine.begin() as connection:
        rows = connection.execute(
            sa.select(operational_events.c.payload).where(
                operational_events.c.tenant_id == scope.tenant_id,
                operational_events.c.installation_id == scope.installation_id,
                operational_events.c.event_type.in_(
                    ["INGRESS_COMMITTED", "REPLY_COMMITTED"]
                ),
                data["message_id"].astext == turn.message_id,
            )
        ).all()
    assert len(rows) == 2
    for row in rows:
        raw = str(row.payload)
        assert "verwijderbare bronzin 41" not in raw
        assert "verwijderbaar antwoord 41" not in raw
        value = row.payload["data"]
        assert "text" not in value
        assert "text_sha256" not in value
        assert value["content_erasure"] == {
            "contract": "EvaConversationContentErasure/v1",
            "erased_at": "2026-08-23T12:00:00Z",
            "message_sha256": receipt.message_sha256,
            "operation_sha256": "2" * 64,
            "principal_sha256": receipt.principal_sha256,
            "status": "erased",
        }


def test_conversation_records_a_content_free_memory_quota_refusal(
    timeline_engine: Engine,
) -> None:
    tenant_id = "living-memory-blocked"
    installation_id = "home"
    scope = TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
        installation_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"),
    )
    journal = PostgresConversationJournal(timeline_engine, scope)
    turn = ConversationTurn.private(
        tenant_id,
        installation_id,
        "person:jaap",
        "telegram:jaap",
        "telegram:memory-blocked:1",
        "Dit bericht past niet meer in het semantische geheugen.",
        "2026-08-23T12:56:00Z",
    )
    journal.admit(turn)
    journal.commit_reply(
        turn,
        GroundedReply(
            turn.conversation_id,
            turn.message_id,
            "Ik heb je wel gehoord.",
            (),
            turn.occurred_at,
            "test",
        ),
    )

    skipped = journal.memory_skipped(
        turn,
        journal.load(turn),
        operation_document_id="telegram:jaap:exchange:memory-blocked:1",
        reason="person_byte_limit_reached",
    )
    assert (
        journal.memory_skipped(
            turn,
            skipped,
            operation_document_id="telegram:jaap:exchange:memory-blocked:1",
            reason="person_byte_limit_reached",
        )
        == skipped
    )
    events = PostgresOperationalTimeline(timeline_engine, scope).events(
        f"conversation:{turn.message_id}"
    )
    assert [event.event_type for event in events] == [
        "INGRESS_COMMITTED",
        "REPLY_COMMITTED",
        "MEMORY_RETAIN_SKIPPED",
    ]
    assert "Dit bericht past niet" not in str(events[-1].payload)


def test_hindsight_outbox_rehydrates_and_stops_after_three_attempts(
    timeline_engine: Engine,
) -> None:
    tenant_id = "living-memory-outbox"
    installation_id = "home"
    scope = TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
        installation_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"),
    )
    first_process = PostgresMemoryOutbox(
        timeline_engine, tenant_id=tenant_id, installation_id=installation_id
    )
    document = ConversationMemoryDocument(
        tenant_id=tenant_id,
        installation_id=installation_id,
        principal_id="jaap",
        conversation_id="telegram:jaap",
        document_id="telegram:jaap:exchange:memory-outbox-1",
        content="Gebruiker (directe bron): Ik wil het daar warm hebben.",
        occurred_at="2026-08-23T12:55:00Z",
        source_ref="telegram:memory-outbox-1",
        evidence_ref="conversation-reply:telegram:memory-outbox-1",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.private_person(principal_id="jaap"),
    )

    queued = first_process.enqueue(document)
    assert first_process.enqueue(document) == queued
    first_attempt = first_process.begin(queued, now=NOW)
    assert first_attempt is not None
    assert first_attempt.state.retry_count == 1
    assert first_attempt.state.status == "waiting_for_proof"

    timeline_engine.dispose()
    restarted_engine = sa.create_engine(timeline_engine.url, pool_pre_ping=False)
    restarted = PostgresMemoryOutbox(
        restarted_engine, tenant_id=tenant_id, installation_id=installation_id
    )
    recovered = restarted.pending()
    assert len(recovered) == 1
    assert recovered[0].document == document
    second_attempt = restarted.begin(recovered[0], now=NOW + timedelta(seconds=1))
    assert second_attempt is not None
    second_failure = restarted.fail(
        second_attempt,
        RuntimeError("Hindsight document parsing failed"),
        now=NOW + timedelta(seconds=2),
    )
    assert second_failure.status == "failed"
    assert second_failure.retry_count == 2
    third_attempt = restarted.begin(
        DurableMemoryRetentionJob(document, second_failure),
        now=NOW + timedelta(seconds=3),
    )
    assert third_attempt is not None
    terminal = restarted.fail(
        third_attempt,
        RuntimeError("Hindsight document parsing failed"),
        now=NOW + timedelta(seconds=4),
    )
    assert terminal.status == "needs_help"
    assert terminal.retry_count == 3
    assert restarted.pending() == ()

    timeline = PostgresOperationalTimeline(restarted_engine, scope)
    assert [event.event_type for event in timeline.events(queued.state.thread_id)] == [
        "MEMORY_RETAIN_QUEUED",
        "MEMORY_RETAIN_ATTEMPTED",
        "MEMORY_RETAIN_ATTEMPTED",
        "MEMORY_RETAIN_RETRY_SCHEDULED",
        "MEMORY_RETAIN_ATTEMPTED",
        "MEMORY_RETAIN_NEEDS_HELP",
    ]
    restarted_engine.dispose()


def test_hindsight_outbox_commits_hash_bound_success(
    timeline_engine: Engine,
) -> None:
    tenant_id = "living-memory-success"
    installation_id = "home"
    outbox = PostgresMemoryOutbox(
        timeline_engine, tenant_id=tenant_id, installation_id=installation_id
    )
    document = ConversationMemoryDocument(
        tenant_id=tenant_id,
        installation_id=installation_id,
        principal_id="jaap",
        conversation_id="telegram:jaap",
        document_id="telegram:jaap:exchange:memory-success-1",
        content="Gebruiker (directe bron): Bram koos het blauwe speeltje.",
        occurred_at="2026-08-23T12:56:00Z",
        source_ref="telegram:memory-success-1",
        evidence_ref="conversation-reply:telegram:memory-success-1",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.private_person(principal_id="jaap"),
    )
    queued = outbox.enqueue(document)
    attempted = outbox.begin(queued, now=NOW)
    assert attempted is not None
    receipt = MemoryRetentionReceipt(
        bank_id="hindsight-bank",
        document_id=document.document_id,
        content_sha256=hashlib.sha256(document.content.encode()).hexdigest(),
    )

    completed = outbox.succeed(attempted, receipt, now=NOW + timedelta(seconds=1))

    assert completed.status == "completed"
    assert completed.retry_count == 1
    assert outbox.pending() == ()


def test_hindsight_outbox_erasure_stops_replay_and_physically_redacts_content(
    timeline_engine: Engine,
) -> None:
    tenant_id = "living-memory-outbox-erasure"
    installation_id = "home"
    outbox = PostgresMemoryOutbox(
        timeline_engine, tenant_id=tenant_id, installation_id=installation_id
    )
    document = ConversationMemoryDocument(
        tenant_id=tenant_id,
        installation_id=installation_id,
        principal_id="jaap",
        conversation_id="telegram:jaap",
        document_id="telegram:jaap:exchange:memory-erasure-1",
        content="Gebruiker (directe bron): verwijder mijn saffieren geheim.",
        occurred_at="2026-08-29T09:10:00Z",
        source_ref="telegram:memory-erasure-1",
        evidence_ref="conversation-reply:telegram:memory-erasure-1",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.private_person(principal_id="jaap"),
    )
    queued = outbox.enqueue(document)
    assert outbox.begin(queued, now=NOW) is not None
    principal_sha256 = hashlib.sha256(b"jaap").hexdigest()
    operation_sha256 = hashlib.sha256(b"outbox-erasure-operation").hexdigest()

    receipt = outbox.erase_content(
        principal_sha256=principal_sha256,
        document_id=document.document_id,
        operation_sha256=operation_sha256,
        erased_at=NOW + timedelta(seconds=1),
    )

    assert receipt.redacted_event_count == 1
    assert receipt.replayed is False
    assert outbox.pending() == ()
    scope = TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
        installation_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"),
    )
    events = PostgresOperationalTimeline(timeline_engine, scope).events(
        queued.state.thread_id
    )
    assert [event.event_type for event in events] == [
        "MEMORY_RETAIN_QUEUED",
        "MEMORY_RETAIN_ATTEMPTED",
        "MEMORY_RETAIN_ERASED",
    ]
    assert "saffieren geheim" not in str(events)
    queued_data = events[0].payload["data"]
    assert "content" not in queued_data
    assert queued_data["content_erasure"]["operation_sha256"] == operation_sha256

    replay = outbox.erase_content(
        principal_sha256=principal_sha256,
        document_id=document.document_id,
        operation_sha256=operation_sha256,
        erased_at=NOW + timedelta(seconds=1),
    )
    assert replay.redacted_event_count == 0
    assert replay.replayed is True


def test_hindsight_outbox_preserves_group_audience_across_restart(
    timeline_engine: Engine,
) -> None:
    tenant_id = "living-memory-group-audience"
    installation_id = "home"
    document = ConversationMemoryDocument(
        tenant_id=tenant_id,
        installation_id=installation_id,
        principal_id="jaap",
        conversation_id="telegram:-100123",
        document_id="telegram:-100123:exchange:memory-group-1",
        content="Gebruiker (gedeeld): morgen graag om acht uur vertrekken.",
        occurred_at="2026-08-23T12:57:00Z",
        source_ref="telegram:memory-group-1",
        evidence_ref="conversation-reply:telegram:memory-group-1",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.telegram_group(
            principal_id="jaap",
            group_chat_id="-100123",
            consent_ref=TelegramGroupConsentReference(
                principal_id="jaap",
                group_chat_id="-100123",
                operation_id="telegram-group-consent:904",
            ),
        ),
    )
    outbox = PostgresMemoryOutbox(
        timeline_engine, tenant_id=tenant_id, installation_id=installation_id
    )
    outbox.enqueue(document)

    timeline_engine.dispose()
    restarted_engine = sa.create_engine(timeline_engine.url, pool_pre_ping=False)
    restarted = PostgresMemoryOutbox(
        restarted_engine, tenant_id=tenant_id, installation_id=installation_id
    )
    recovered = restarted.pending()
    restarted_engine.dispose()

    assert len(recovered) == 1
    assert recovered[0].document == document


def test_hindsight_outbox_rejects_legacy_group_payload_without_audience() -> None:
    legacy_group_data = {
        "content": "Dit mag niet terugvallen naar persoonlijk geheugen.",
        "conversation_id": "telegram:-100123",
        "document_id": "telegram:-100123:exchange:legacy",
        "evidence_ref": "conversation-reply:legacy",
        "installation_id": "home",
        "kind": "hindsight_memory_document",
        "occurred_at": "2026-08-23T12:58:00Z",
        "principal_id": "jaap",
        "privacy_class": "internal",
        "source_ref": "telegram:legacy",
        "tenant_id": "legacy-group-audience",
        "trust": "direct_user",
    }

    with pytest.raises(
        RuntimeError, match="legacy Telegram group memory lacks an audience"
    ):
        PostgresMemoryOutbox._document_from_data(legacy_group_data)  # noqa: SLF001


def test_model_chosen_interest_survives_restart_and_is_not_adopted_twice(
    timeline_engine: Engine,
) -> None:
    actions = PostgresConversationActions(timeline_engine)
    turn = ConversationTurn.private(
        tenant_id="living-personal-interest",
        installation_id="home",
        principal_id="jaap",
        conversation_id="telegram:jaap",
        message_id="telegram:interest:restart",
        text="Kies zelf een hobby die ook thuis helpt.",
        occurred_at="2026-08-23T13:00:00Z",
    )
    # PostgresConversationActions derives its UUID scope from the wire IDs.
    derived_scope = TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, f"eva2:{turn.tenant_id}"),
        installation_id=uuid5(
            NAMESPACE_URL, f"eva2:{turn.tenant_id}:{turn.installation_id}"
        ),
    )
    journal = PostgresConversationJournal(timeline_engine, derived_scope)
    journal.admit(turn)
    call = ConversationActionCall(
        "call:interest:first",
        "adopt_personal_interest",
        {
            "motivation": "Ik vind patronen en praktische verbeteringen interessant.",
            "next_exploration": "Welke restjes leveren een goede nieuwe maaltijd op?",
            "title": "Slim koken met wat er al is",
        },
    )

    adopted = actions.execute(turn, call)
    assert adopted.output["disposition"] == "adopted"
    assert adopted.output["state"] == "active"

    timeline_engine.dispose()
    restarted_engine = sa.create_engine(timeline_engine.url, pool_pre_ping=False)
    restarted = PostgresConversationActions(restarted_engine)
    restarted_timeline = PostgresOperationalTimeline(restarted_engine, derived_scope)
    pending = restarted.pending(turn)
    assert pending is not None
    assert pending.output["disposition"] == "already_adopted"
    assert pending.output["title"] == call.arguments["title"]

    different_retry = ConversationActionCall(
        "call:interest:retry",
        "adopt_personal_interest",
        {
            "motivation": "Een later model koos iets anders.",
            "next_exploration": "Een ander onderzoek.",
            "title": "Een andere hobby",
        },
    )
    replayed = restarted.execute(turn, different_retry)
    assert replayed.output["title"] == call.arguments["title"]
    intent_thread = f"intent:{adopted.output['intent_id']}"
    assert [event.event_type for event in restarted_timeline.events(intent_thread)] == [
        "PERSONAL_INTENT_ADOPTED"
    ]
    assert [
        event.event_type
        for event in restarted_timeline.events(f"conversation:{turn.message_id}")
    ] == ["INGRESS_COMMITTED", "PERSONAL_INTENT_LINKED"]
    restarted_engine.dispose()


@pytest.mark.parametrize("step_index", [0, 1, 2])
def test_external_effect_provider_is_durable_and_not_repeated_after_restart(
    timeline_engine: Engine, step_index: int,
) -> None:
    calls: list[str] = []

    class Planner:
        action_names = ("reserve_calendar_slot",)

        async def prepare(self, turn, call):  # type: ignore[no-untyped-def]
            assert turn.principal_id == "jaap"
            assert call.arguments == {"slot": "2026-08-31T10:00:00+02:00"}
            return ConversationToolPreparation(
                tool_name="calendar.reserve",
                arguments={"slot": call.arguments["slot"]},
                expected_proof={"reserved": True},
                receipt_output={"slot": call.arguments["slot"]},
            )

    class Provider:
        async def execute(self, request):  # type: ignore[no-untyped-def]
            calls.append(request.operation_id)
            return ToolDispatchResult(True, "calendar:42", {"accepted": True})

        async def observe(self, request, dispatch):  # type: ignore[no-untyped-def]
            assert dispatch is not None and dispatch.accepted
            return ToolProofObservation(
                "verified",
                {"reserved": True},
                {"source": "calendar_test_provider"},
            )

    turn = ConversationTurn.private(
        "external-tool-routing",
        "installation",
        "jaap",
        "conversation",
        f"message:calendar-effect:{step_index}",
        "Reserveer morgen tien uur",
        "2026-08-30T19:16:00Z",
    )
    call = ConversationActionCall(
        "call:calendar-effect",
        "reserve_calendar_slot",
        {"slot": "2026-08-31T10:00:00+02:00"},
    )
    provider = Provider()
    tools = PostgresExternalConversationTools(timeline_engine, Planner(), provider)

    actions = PostgresConversationActions(timeline_engine, external_tools=(tools,))
    receipt = (
        actions.execute(turn, call) if step_index == 0
        else tools.execute(turn, call, step_index=step_index)
    )

    assert receipt.output == {
        "contract": "EvaConversationActionReceipt/v1",
        "durable": True,
        "proof": {
            "evidence": {"source": "calendar_test_provider"},
            "expected": {"reserved": True},
            "observed": {"reserved": True},
        },
        "slot": "2026-08-31T10:00:00+02:00",
        "status": "verified",
    }
    assert len(calls) == 1
    restarted_tools = PostgresExternalConversationTools(
        timeline_engine, Planner(), provider
    )
    restarted_actions = PostgresConversationActions(
        timeline_engine, external_tools=(restarted_tools,),
    )
    assert (
        restarted_actions.pending(turn) if step_index == 0
        else restarted_tools.pending(turn, step_index=step_index)
    ) == receipt
    assert restarted_tools.execute(turn, call, step_index=step_index) == receipt
    assert len(calls) == 1

    def revoked(turn):
        raise ConversationConflict("provider authorization revoked")

    class UnrelatedPlanner:
        action_names = ("private_workspace_write",)

    unrelated_tools = PostgresExternalConversationTools(
        timeline_engine, UnrelatedPlanner(), provider, authorize_turn=revoked,
    )
    # Later steps share their identity across providers. A denied unrelated
    # provider must not intercept this provider's stored step.
    assert unrelated_tools.pending(turn, step_index=step_index) is None
    assert restarted_tools.pending(turn, step_index=step_index) == receipt

    revoked_tools = PostgresExternalConversationTools(
        timeline_engine, Planner(), provider, authorize_turn=revoked,
    )
    with pytest.raises(ConversationConflict, match="authorization revoked"):
        revoked_tools.pending(turn, step_index=step_index)
    with pytest.raises(ConversationConflict, match="authorization revoked"):
        revoked_tools.execute(turn, call, step_index=step_index)
    assert len(calls) == 1



def test_external_steps_repeat_a_tool_and_replay_without_replanning(
    timeline_engine: Engine,
) -> None:
    prepared: list[str] = []
    dispatched: list[str] = []

    class Planner:
        action_names = ("inspect_unfamiliar_item", "inspect_other_item")

        async def prepare(self, turn, call):
            prepared.append(call.call_id)
            return ConversationToolPreparation(
                tool_name="fixture.inspect",
                arguments=call.arguments,
                expected_proof={"found": True},
                receipt_output={"next_item": call.arguments["item"] + "/child"},
            )

    class Provider:
        async def execute(self, request):
            dispatched.append(request.operation_id)
            return ToolDispatchResult(True, request.operation_id, {})

        async def observe(self, request, dispatch):
            return ToolProofObservation("verified", {"found": True}, {})

    turn = ConversationTurn.private(
        "external-chain", "installation", "jaap", "conversation",
        "message:chain", "Onderzoek het item en daarna het gevonden kind.",
        "2026-09-08T09:00:00Z",
    )
    provider = Provider()
    tools = PostgresExternalConversationTools(timeline_engine, Planner(), provider)
    first_call = ConversationActionCall(
        "call:first", "inspect_unfamiliar_item", {"item": "root"},
    )
    first = tools.execute(turn, first_call)
    second_call = ConversationActionCall(
        "call:second", "inspect_unfamiliar_item",
        {"item": first.output["next_item"]},
    )
    second = tools.execute(turn, second_call, step_index=1)
    assert second.output["next_item"] == "root/child/child"
    assert len(set(dispatched)) == 2
    assert prepared == ["call:first", "call:second"]

    class RestartedPlanner(Planner):
        async def prepare(self, turn, call):
            raise AssertionError("an admitted step must use its stored plan")

    restarted = PostgresExternalConversationTools(
        timeline_engine, RestartedPlanner(), provider,
    )
    assert restarted.pending(turn) == first
    assert restarted.pending(turn, step_index=1) == second
    assert restarted.execute(turn, second_call, step_index=1) == second
    assert restarted.pending(turn, step_index=2) is None
    for conflicting_call in (
        ConversationActionCall("call:third", "inspect_other_item", {"item": "root"}),
        ConversationActionCall("call:second", "inspect_unfamiliar_item", {"item": "other"}),
    ):
        with pytest.raises(ConversationConflict, match="replay differs"):
            restarted.execute(turn, conflicting_call, step_index=1)
    assert len(dispatched) == 2


@pytest.mark.parametrize("step_index", [0, 1])
def test_external_tool_restart_uses_durable_separate_observation_command(
    timeline_engine: Engine, step_index: int,
) -> None:
    execution_calls: list[dict[str, object]] = []
    observation_calls: list[dict[str, object]] = []
    configuration_sha256 = "7" * 64

    class Planner:
        action_names = ("write_document",)

        async def prepare(self, turn, call):  # type: ignore[no-untyped-def]
            del turn
            assert call.arguments == {
                "content": "duurzaam",
                "path": "proof.txt",
            }
            return ConversationToolPreparation(
                tool_name="write_file",
                arguments={"content": "duurzaam", "path": "proof.txt"},
                expected_proof={"text": "duurzaam"},
                receipt_output={"provider": "fixture"},
                observation_tool_name="read_text_file",
                observation_arguments={"path": "proof.txt"},
                tool_configuration_sha256=configuration_sha256,
            )

    class KilledProvider:
        async def execute(self, request):  # type: ignore[no-untyped-def]
            execution_calls.append(
                {
                    "arguments": request.arguments,
                    "configuration": request.tool_configuration_sha256,
                    "tool": request.tool_name,
                }
            )
            raise SystemExit(137)

        async def observe(self, request, dispatch):  # type: ignore[no-untyped-def]
            raise AssertionError("first process must not reach observation")

    class RestartedProvider:
        async def execute(self, request):  # type: ignore[no-untyped-def]
            raise AssertionError("restart must observe before any second execution")

        async def observe(self, request, dispatch):  # type: ignore[no-untyped-def]
            assert dispatch is None
            observation_calls.append(
                {
                    "arguments": request.observation_arguments,
                    "configuration": request.tool_configuration_sha256,
                    "tool": request.observation_tool_name,
                }
            )
            return ToolProofObservation(
                "verified",
                {"text": "duurzaam"},
                {"source": "separate_readback"},
            )

    turn = ConversationTurn.private(
        "external-tool-separate-observation",
        "installation",
        "jaap",
        "conversation",
        f"message:separate-observation:{step_index}",
        "Schrijf het bewijsbestand",
        "2026-09-01T10:00:00Z",
    )
    call = ConversationActionCall(
        "call:separate-observation",
        "write_document",
        {"content": "duurzaam", "path": "proof.txt"},
    )
    first = PostgresExternalConversationTools(
        timeline_engine, Planner(), KilledProvider()
    )
    with pytest.raises(SystemExit, match="137"):
        first.execute(turn, call, step_index=step_index)

    allowed = False

    def authorize(resuming_turn):
        assert resuming_turn == turn
        if not allowed:
            raise ConversationConflict("workspace access revoked")

    restarted = PostgresExternalConversationTools(
        timeline_engine, Planner(), RestartedProvider(), authorize_turn=authorize
    )
    # Revocation after a crash must block both recovery entry points before IO.
    with pytest.raises(ConversationConflict, match="workspace access revoked"):
        restarted.pending(turn, step_index=step_index)
    with pytest.raises(ConversationConflict, match="workspace access revoked"):
        restarted.execute(turn, call, step_index=step_index)
    assert len(execution_calls) == 1
    assert observation_calls == []

    allowed = True
    receipt = restarted.pending(turn, step_index=step_index)

    assert receipt is not None and receipt.output["status"] == "verified"
    assert execution_calls == [
        {
            "arguments": {"content": "duurzaam", "path": "proof.txt"},
            "configuration": configuration_sha256,
            "tool": "write_file",
        }
    ]
    assert observation_calls == [
        {
            "arguments": {"path": "proof.txt"},
            "configuration": configuration_sha256,
            "tool": "read_text_file",
        }
    ]
    scope = TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, f"eva2:{turn.tenant_id}"),
        installation_id=uuid5(
            NAMESPACE_URL, f"eva2:{turn.tenant_id}:{turn.installation_id}"
        ),
    )
    timeline = PostgresOperationalTimeline(timeline_engine, scope)
    events = timeline.events(
        PostgresExternalConversationTools._thread_id(turn, "write_document", step_index)
    )
    assert [event.event_type for event in events] == [
        "TOOL_REQUEST_ADMITTED",
        "TOOL_PREPARED",
        "STATE_VERIFIED",
    ]


def test_provider_owned_automation_is_durable_and_not_repeated_after_restart(
    timeline_engine: Engine,
) -> None:
    calls: list[str] = []

    class Resolver:
        async def resolve_entities(self, values):  # type: ignore[no-untyped-def]
            assert [value[0] for value in values] == ["Jaap", "lamp in de hal"]
            return (
                {"entity_id": "person.jaap", "friendly_name": "Jaap"},
                {"entity_id": "light.hal", "friendly_name": "Lamp in de hal"},
            )

    class Provider:
        async def execute(self, request):  # type: ignore[no-untyped-def]
            calls.append(request.operation_id)
            return ToolDispatchResult(True, "automation:test", {"created": True})

        async def observe(self, request, dispatch):  # type: ignore[no-untyped-def]
            assert dispatch is not None and dispatch.accepted
            return ToolProofObservation(
                "verified",
                request.expected_proof,
                {"source": "automation_test_provider"},
            )

    turn = ConversationTurn.private(
        "external-automation-routing",
        "installation",
        "jaap",
        "conversation",
        "message:automation-effect",
        "Als ik thuiskom, zet de lamp in de hal aan.",
        "2026-08-30T19:45:00Z",
    )
    call = ConversationActionCall(
        "call:automation-effect",
        "create_home_assistant_state_automation",
        {
            "action_entity_id": "lamp in de hal",
            "action_service": "turn_on",
            "alias": "Hal aan bij thuiskomst",
            "trigger_entity_id": "Jaap",
            "trigger_to": "home",
        },
    )
    provider = Provider()
    planner = HomeAssistantConversationAutomationPlanner(Resolver())
    tools = PostgresExternalConversationTools(
        timeline_engine,
        planner,
        provider,  # type: ignore[arg-type]
    )
    actions = PostgresConversationActions(timeline_engine, external_tools=(tools,))

    receipt = actions.execute(turn, call)

    assert receipt.output["status"] == "verified"
    assert receipt.output["alias"] == "Hal aan bij thuiskomst"
    assert receipt.output["verified_config"]["triggers"] == [
        {"entity_id": "person.jaap", "to": "home", "trigger": "state"}
    ]
    assert len(calls) == 1
    restarted_tools = PostgresExternalConversationTools(
        timeline_engine,
        planner,
        provider,  # type: ignore[arg-type]
    )
    restarted = PostgresConversationActions(
        timeline_engine, external_tools=(restarted_tools,)
    )
    assert restarted.pending(turn) == receipt
    assert len(calls) == 1


def test_provider_owned_tuya_plan_is_durable_and_not_repeated_after_restart(
    timeline_engine: Engine,
) -> None:
    inventory_calls = 0

    class Inventory:
        async def inventory(self):  # type: ignore[no-untyped-def]
            nonlocal inventory_calls
            inventory_calls += 1
            return HomeAssistantInventory(
                (HomeAssistantIntegration("tuya", "cloud", "loaded", "Tuya"),),
                (HomeAssistantDevice("device", "Schuur", "Tuya", "Lamp", ("cloud",)),),
                (HomeAssistantEntity("light.schuur", "tuya", "cloud", "device", None),),
                datetime(2026, 8, 30, 19, 0, tzinfo=UTC),
                (),
                True,
                "2026.8.1",
            )

    def store(tenant_id: str, installation_id: str) -> PostgresAdvancedTaskStore:
        return PostgresAdvancedTaskStore(
            timeline_engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )

    planner = HomeAssistantConversationMigrationPlanner(Inventory())
    provider = HomeAssistantMigrationPlanAdapter(store)
    tools = PostgresExternalConversationTools(timeline_engine, planner, provider)
    actions = PostgresConversationActions(timeline_engine, external_tools=(tools,))
    turn = ConversationTurn.private(
        "external-management-routing",
        "installation",
        "jaap",
        "conversation",
        "message:tuya-plan",
        "Maak een veilig plan om Tuya lokaal te maken.",
        "2026-08-30T19:00:00Z",
    )
    call = ConversationActionCall(
        "call:tuya-plan",
        "plan_tuya_to_localtuya_migration",
        {"scope": "all_tuya_devices"},
    )

    receipt = actions.execute(turn, call)

    assert receipt.output["status"] == "verified"
    raw_plan = receipt.output["plan"]
    plan = AdvancedTaskPlan.from_payload(raw_plan)
    assert store(turn.tenant_id, turn.installation_id).load(plan.plan_id) == plan
    assert inventory_calls == 1
    restarted = PostgresConversationActions(
        timeline_engine,
        external_tools=(
            PostgresExternalConversationTools(timeline_engine, planner, provider),
        ),
    )
    assert restarted.pending(turn) == receipt
    assert inventory_calls == 1


def test_provider_owned_repository_registration_is_verified_and_not_repeated(
    timeline_engine: Engine,
) -> None:
    repository = HomeAssistantRepository(
        "maintained",
        "xZetsubou/hass-localtuya",
        "localtuya",
        False,
        None,
        "2026.7.0",
        True,
        "2025.1.0",
        True,
    )
    registered = False
    provider_calls = 0

    def inventory() -> HomeAssistantInventory:
        return _management_inventory((repository,) if registered else ())

    class Inventory:
        async def inventory(self):  # type: ignore[no-untyped-def]
            return inventory()

    class Hacs:
        async def register_exact(self, approval, **_kwargs):  # type: ignore[no-untyped-def]
            nonlocal registered, provider_calls
            provider_calls += 1
            assert approval.repository_full_name == repository.full_name
            registered = True
            return repository

        async def repository_by_full_name(self, full_name):  # type: ignore[no-untyped-def]
            assert full_name == repository.full_name
            return repository if registered else None

        async def repository(self, repository_id):  # type: ignore[no-untyped-def]
            raise AssertionError(repository_id)

        async def install_exact(self, approval, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError(approval)

    def store(tenant: str, installation: str) -> PostgresAdvancedTaskStore:
        return PostgresAdvancedTaskStore(
            timeline_engine, tenant_id=tenant, installation_id=installation
        )

    tenant = "external-hacs-repository"
    installation = "installation"
    initial = TuyaToLocalTuyaPlanner().build(
        inventory(), operation_id="operation:repository", requested_by="jaap"
    )
    store(tenant, installation).retain(initial)
    planner = HomeAssistantConversationHacsPlanner(Inventory(), store)
    provider = HomeAssistantHacsManagementAdapter(Hacs(), Inventory(), store)
    tools = PostgresExternalConversationTools(timeline_engine, planner, provider)
    actions = PostgresConversationActions(timeline_engine, external_tools=(tools,))
    turn = ConversationTurn.private(
        tenant,
        installation,
        "jaap",
        "conversation",
        "message:repository",
        "Voeg xZetsubou/hass-localtuya 2026.7.0 als HACS-repository toe.",
        "2026-08-30T19:10:00Z",
    )
    call = ConversationActionCall(
        "call:repository",
        "register_approved_localtuya_repository",
        {
            "expected_version": "2026.7.0",
            "repository_full_name": "xZetsubou/hass-localtuya",
        },
    )

    assert "Tuya→LocalTuya-plan" in actions.active_context(turn)[0]

    receipt = actions.execute(turn, call)

    assert receipt.output["status"] == "verified"
    assert receipt.output["proof"]["observed"] == {
        "available_version": "2026.7.0",
        "custom": True,
        "registered": True,
        "repository_full_name": "xZetsubou/hass-localtuya",
    }
    assert provider_calls == 1
    restarted = PostgresConversationActions(
        timeline_engine,
        external_tools=(
            PostgresExternalConversationTools(timeline_engine, planner, provider),
        ),
    )
    assert restarted.pending(turn) == receipt
    assert provider_calls == 1


def test_provider_owned_package_install_is_verified_and_not_repeated(
    timeline_engine: Engine,
) -> None:
    available = HomeAssistantRepository(
        "maintained",
        "xZetsubou/hass-localtuya",
        "localtuya",
        False,
        None,
        "2026.7.0",
        True,
        "2025.1.0",
        True,
    )
    installed = HomeAssistantRepository(
        "maintained",
        "xZetsubou/hass-localtuya",
        "localtuya",
        True,
        "2026.7.0",
        "2026.7.0",
        True,
        "2025.1.0",
        True,
    )
    package_installed = False
    provider_calls = 0

    class Inventory:
        async def inventory(self):  # type: ignore[no-untyped-def]
            return _management_inventory((available,))

    class Hacs:
        async def install_exact(self, approval, **_kwargs):  # type: ignore[no-untyped-def]
            nonlocal package_installed, provider_calls
            provider_calls += 1
            assert approval.repository_id == available.repository_id
            package_installed = True
            return installed

        async def repository(self, repository_id):  # type: ignore[no-untyped-def]
            assert repository_id == available.repository_id
            return installed if package_installed else available

        async def repository_by_full_name(self, full_name):  # type: ignore[no-untyped-def]
            raise AssertionError(full_name)

        async def register_exact(self, approval, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError(approval)

    def store(tenant: str, installation: str) -> PostgresAdvancedTaskStore:
        return PostgresAdvancedTaskStore(
            timeline_engine, tenant_id=tenant, installation_id=installation
        )

    tenant = "external-hacs-package"
    installation = "installation"
    initial = TuyaToLocalTuyaPlanner().build(
        _management_inventory((available,)),
        operation_id="operation:package",
        requested_by="jaap",
    )
    store(tenant, installation).retain(initial)
    planner = HomeAssistantConversationHacsPlanner(Inventory(), store)
    provider = HomeAssistantHacsManagementAdapter(Hacs(), Inventory(), store)
    tools = PostgresExternalConversationTools(timeline_engine, planner, provider)
    actions = PostgresConversationActions(timeline_engine, external_tools=(tools,))
    turn = ConversationTurn.private(
        tenant,
        installation,
        "jaap",
        "conversation",
        "message:package",
        "Ik geef toestemming: installeer LocalTuya 2026.7.0.",
        "2026-08-30T19:20:00Z",
    )
    call = ConversationActionCall(
        "call:package",
        "install_approved_localtuya_package",
        {"version": "2026.7.0"},
    )

    receipt = actions.execute(turn, call)

    assert receipt.output["status"] == "verified"
    assert receipt.output["proof"]["observed"] == {
        "installed": True,
        "installed_version": "2026.7.0",
        "repository_id": "maintained",
    }
    assert provider_calls == 1
    restarted = PostgresConversationActions(
        timeline_engine,
        external_tools=(
            PostgresExternalConversationTools(timeline_engine, planner, provider),
        ),
    )
    assert restarted.pending(turn) == receipt
    assert provider_calls == 1


def _management_inventory(
    repositories: tuple[HomeAssistantRepository, ...],
) -> HomeAssistantInventory:
    return HomeAssistantInventory(
        (HomeAssistantIntegration("tuya", "cloud", "loaded", "Tuya"),),
        (HomeAssistantDevice("device", "Schuur", "Tuya", "Lamp", ("cloud",)),),
        (HomeAssistantEntity("light.schuur", "tuya", "cloud", "device", None),),
        datetime(2026, 8, 30, 19, 0, tzinfo=UTC),
        repositories,
        True,
        "2026.8.1",
    )


def test_home_assistant_history_read_is_durable_and_not_repeated_after_restart(
    timeline_engine: Engine,
) -> None:
    calls: list[tuple[tuple[str, ...], str, str, datetime]] = []

    class HomeAssistant(HomeAssistantToolAdapter):
        def __init__(self) -> None:
            pass

        async def resolve_history_targets(  # type: ignore[no-untyped-def]
            self, target, entity_kind
        ):
            assert target == "licht in de overkapping"
            assert entity_kind == "light"
            return (
                {
                    "entity_id": "light.overkapping_wand",
                    "friendly_name": "Overkapping wand",
                    "state": "off",
                },
                {
                    "entity_id": "light.overkapping_spotjes",
                    "friendly_name": "Spotjes overkapping",
                    "state": "off",
                },
            )

        async def history_group(  # type: ignore[no-untyped-def]
            self, *, entity_ids, period, transition_to, observed_at
        ):
            calls.append((entity_ids, period, transition_to, observed_at))
            return {
                "aggregation": "any_member_on_all_members_off",
                "contract": "EvaHomeAssistantGroupHistory/v1",
                "entity_ids": list(entity_ids),
                "matches": [
                    {
                        "changed_at": "2026-08-23T22:15:00+02:00",
                        "from": "on",
                        "stable_for_seconds": 6300,
                        "to": "off",
                    }
                ],
                "minimum_stable_seconds": 30,
                "period": period,
                "source": "home_assistant_recorder",
                "time_zone": "Europe/Amsterdam",
                "transition_to": transition_to,
                "window_end": "2026-08-24T00:00:00+02:00",
                "window_start": "2026-08-23T00:00:00+02:00",
            }

    turn = ConversationTurn.private(
        tenant_id="living-history-read",
        installation_id="home",
        principal_id="jaap",
        conversation_id="telegram:jaap",
        message_id="telegram:history:restart",
        text="Hoe laat ging gisteren het licht in de overkapping uit?",
        occurred_at="2026-08-24T10:00:00Z",
    )
    call = ConversationActionCall(
        "call:history:first",
        "read_home_assistant_history",
        {
            "entity_kind": "light",
            "period": "yesterday",
            "target": "licht in de overkapping",
            "transition_to": "off",
        },
    )
    provider = HomeAssistant()
    actions = PostgresConversationActions(
        timeline_engine,
        external_tools=(
            PostgresExternalConversationTools(
                timeline_engine,
                HomeAssistantConversationReadPlanner(provider),
                provider,
            ),
        ),
    )

    receipt = actions.execute(turn, call)

    assert receipt.output["status"] == "verified"
    assert receipt.output["history"]["matches"][0]["changed_at"] == (
        "2026-08-23T22:15:00+02:00"
    )
    assert len(calls) == 1
    restarted = PostgresConversationActions(
        timeline_engine,
        external_tools=(
            PostgresExternalConversationTools(
                timeline_engine,
                HomeAssistantConversationReadPlanner(provider),
                provider,
            ),
        ),
    )
    assert restarted.pending(turn) == receipt
    assert len(calls) == 1
    scope = TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, f"eva2:{turn.tenant_id}"),
        installation_id=uuid5(
            NAMESPACE_URL, f"eva2:{turn.tenant_id}:{turn.installation_id}"
        ),
    )
    timeline = PostgresOperationalTimeline(timeline_engine, scope)
    thread_id = PostgresExternalConversationTools._thread_id(
        turn, "read_home_assistant_history"
    )
    assert [event.event_type for event in timeline.events(thread_id)] == [
        "TOOL_REQUEST_ADMITTED",
        "TOOL_PREPARED",
        "TOOL_EXECUTED",
        "STATE_VERIFIED",
    ]


def test_home_assistant_current_state_read_is_durable_and_not_repeated(
    timeline_engine: Engine,
) -> None:
    calls: list[str] = []

    class HomeAssistant(HomeAssistantToolAdapter):
        def __init__(self) -> None:
            pass

        async def resolve_switch(self, target):  # type: ignore[no-untyped-def]
            assert target == "verwarming in de overkapping"
            return {
                "entity_id": "switch.heater_overkapping",
                "friendly_name": "Heater overkapping",
                "state": "on",
            }

        async def state(self, *, entity_id):  # type: ignore[no-untyped-def]
            calls.append(entity_id)
            return {
                "contract": "EvaHomeAssistantCurrentState/v1",
                "entity_id": entity_id,
                "last_changed": "2026-08-24T01:55:00+00:00",
                "last_updated": "2026-08-24T01:55:01+00:00",
                "source": "home_assistant_state",
                "state": "on",
            }

    turn = ConversationTurn.private(
        tenant_id="living-state-read",
        installation_id="home",
        principal_id="jaap",
        conversation_id="telegram:jaap",
        message_id="telegram:state:restart",
        text="Staat de verwarming in de overkapping nu aan?",
        occurred_at="2026-08-24T10:00:00Z",
    )
    call = ConversationActionCall(
        "call:state:first",
        "read_home_assistant_state",
        {"entity_kind": "switch", "target": "verwarming in de overkapping"},
    )
    provider = HomeAssistant()
    actions = PostgresConversationActions(
        timeline_engine,
        external_tools=(
            PostgresExternalConversationTools(
                timeline_engine,
                HomeAssistantConversationReadPlanner(provider),
                provider,
            ),
        ),
    )

    receipt = actions.execute(turn, call)

    assert receipt.output["status"] == "verified"
    assert receipt.output["state"]["state"] == "on"
    assert calls == ["switch.heater_overkapping"]
    restarted = PostgresConversationActions(
        timeline_engine,
        external_tools=(
            PostgresExternalConversationTools(
                timeline_engine,
                HomeAssistantConversationReadPlanner(provider),
                provider,
            ),
        ),
    )
    assert restarted.pending(turn) == receipt
    assert calls == ["switch.heater_overkapping"]


def test_current_research_artifact_is_durable_and_reopens_without_repeat(
    timeline_engine: Engine,
) -> None:
    calls: list[str] = []
    from eva.application.conversation import ConversationModelUsage

    class Research:
        def research_current_options(  # type: ignore[no-untyped-def]
            self, *, question, title, options, researched_at
        ):
            calls.append(question)
            return ResearchArtifact(
                title,
                question,
                "# Vergelijking\n\nDrie actuele opties met bronfeiten en conclusie.",
                (
                    ResearchSource("A", "https://a.example/docs"),
                    ResearchSource("B", "https://b.example/docs"),
                    ResearchSource("C", "https://c.example/docs"),
                ),
                researched_at,
                "live-search-model",
                usage=ConversationModelUsage.measured(model_attempts=1, tool_calls=0,
                    input_tokens=27040, output_tokens=1092),
            )

    turn = ConversationTurn.private(
        tenant_id="living-current-research",
        installation_id="home",
        principal_id="jaap",
        conversation_id="telegram:jaap",
        message_id="telegram:research:restart",
        text="Vergelijk drie actuele opties en bewaar een beslisdocument.",
        occurred_at="2026-08-24T10:00:00Z",
    )
    call = ConversationActionCall(
        "call:research:first",
        "research_current_options",
        {
            "options": ["A", "B", "C"],
            "question": "Vergelijk drie actuele opties op prijs en privacy.",
            "title": "Actuele opties",
        },
    )
    actions = PostgresConversationActions(timeline_engine, research=Research())

    receipt = actions.execute(turn, call)

    assert receipt.output["status"] == "verified"
    assert receipt.output["verification_scope"] == "research_artifact_only"
    artifact = receipt.output["artifact"]
    assert artifact["content_sha256"]
    assert len(artifact["sources"]) == 3
    assert artifact["model_usage"]["input_tokens"] == 27040
    assert artifact["model_usage"]["output_tokens"] == 1092
    restarted = PostgresConversationActions(timeline_engine, research=Research())
    assert restarted.pending(turn) == receipt
    assert calls == ["Vergelijk drie actuele opties op prijs en privacy."]
    timeline = PostgresOperationalTimeline(
        timeline_engine,
        TimelineScope(
            tenant_id=uuid5(NAMESPACE_URL, f"eva2:{turn.tenant_id}"),
            installation_id=uuid5(
                NAMESPACE_URL, f"eva2:{turn.tenant_id}:{turn.installation_id}"
            ),
        ),
    )
    assert [
        event.event_type for event in timeline.events(actions._research_thread_id(turn))
    ] == ["CURRENT_RESEARCH_ADMITTED", "CURRENT_RESEARCH_COMPLETED"]

    # A restored research receipt must offer the ordinary external continuation
    # route; replay must recover that effect without researching/dispatching again.
    from eva.application.conversation import LivingConversation, IdentityProfile, ConversationModelResult
    from eva.adapters.projection.hindsight import HindsightMemoryEngine

    dispatched = []
    class Memory:
        def recall_for_presence(self, query):
            return HindsightMemoryEngine._empty_presence(query)
    class Planner:
        action_names = ("use_researched_service",)
        async def prepare(self, turn, call):
            return ConversationToolPreparation("fixture.use", call.arguments,
                {"done": True}, {"service": "researched"})
    class Provider:
        async def execute(self, request):
            dispatched.append(request.operation_id)
            return ToolDispatchResult(True, request.operation_id, {})
        async def observe(self, request, dispatch):
            return ToolProofObservation("verified", {"done": True}, {})
    class Model:
        def generate(self, *args, completed_action=None, available_actions=(), **kwargs):
            assert completed_action is not None
            if completed_action.call.name == "research_current_options":
                assert "use_researched_service" in available_actions
                assert completed_action.output["verification_scope"] == "research_artifact_only"
                return ConversationModelResult(None, "fixture", ConversationActionCall(
                    "call:research:followthrough", "use_researched_service", {"source": "A"}))
            assert completed_action.output["status"] == "verified"
            return ConversationModelResult("De onderzochte dienst is gebruikt.", "fixture")
    def conversation():
        tools = PostgresExternalConversationTools(timeline_engine, Planner(), Provider())
        actions = PostgresConversationActions(timeline_engine, research=Research(), external_tools=(tools,))
        return LivingConversation(Memory(), Model(), IdentityProfile("Eva", "Huisgenoot", ("Eerlijk",), 80), actions)
    assert conversation().respond(turn).text == "De onderzochte dienst is gebruikt."
    assert conversation().respond(turn).text == "De onderzochte dienst is gebruikt."
    assert len(dispatched) == 1
    assert calls == ["Vergelijk drie actuele opties op prijs en privacy."]


def test_telegram_group_consent_is_durable_person_scoped_and_revocable(
    timeline_engine: Engine,
) -> None:
    consent = TelegramGroupConsent(
        "-100123", "person:jaap", NOW, NOW + timedelta(days=7)
    )
    store = PostgresTelegramGroupConsentStore(
        timeline_engine,
        tenant_id="telegram-consent-tenant",
        installation_id="telegram-consent-home",
    )
    assert store.grant(consent, operation_id="telegram-group-consent:901") == consent
    restarted = PostgresTelegramGroupConsentStore(
        timeline_engine,
        tenant_id="telegram-consent-tenant",
        installation_id="telegram-consent-home",
    )
    assert restarted.permits(
        principal_id="person:jaap", group_chat_id="-100123", now=NOW
    )
    assert restarted.active_reference(
        principal_id="person:jaap", group_chat_id="-100123", now=NOW
    ) == TelegramGroupConsentReference(
        principal_id="person:jaap",
        group_chat_id="-100123",
        operation_id="telegram-group-consent:901",
    )
    assert not restarted.permits(
        principal_id="person:frank", group_chat_id="-100123", now=NOW
    )
    assert (
        restarted.active_reference(
            principal_id="person:frank", group_chat_id="-100123", now=NOW
        )
        is None
    )
    assert restarted.revoke(
        principal_id="person:jaap",
        group_chat_id="-100123",
        operation_id="telegram-group-consent:902",
        now=NOW + timedelta(seconds=1),
    )
    assert not restarted.permits(
        principal_id="person:jaap",
        group_chat_id="-100123",
        now=NOW + timedelta(seconds=2),
    )
    assert (
        restarted.active_reference(
            principal_id="person:jaap",
            group_chat_id="-100123",
            now=NOW + timedelta(seconds=2),
        )
        is None
    )
    assert [
        event.event_type
        for event in restarted._timeline.events(  # noqa: SLF001
            "telegram-group-consent:person:jaap:-100123"
        )
    ] == ["TELEGRAM_GROUP_CONSENT_GRANTED", "TELEGRAM_GROUP_CONSENT_REVOKED"]


def test_telegram_group_observation_is_durable_bounded_and_contains_no_text(
    timeline_engine: Engine,
) -> None:
    store = PostgresTelegramGroupObservationStore(
        timeline_engine,
        tenant_id="telegram-observation-tenant",
        installation_id="telegram-observation-home",
    )
    store.observe(
        principal_id="telegram:7",
        group_chat_id="-100127",
        operation_id="telegram-group-observation:telegram-canary:-100127:1",
        now=NOW,
        expires_at=NOW + timedelta(hours=24),
    )
    restarted = PostgresTelegramGroupObservationStore(
        timeline_engine,
        tenant_id="telegram-observation-tenant",
        installation_id="telegram-observation-home",
    )
    assert restarted.recently_observed(
        principal_id="telegram:7", group_chat_id="-100127", now=NOW
    )
    assert not restarted.recently_observed(
        principal_id="telegram:8", group_chat_id="-100127", now=NOW
    )
    assert not restarted.recently_observed(
        principal_id="telegram:7",
        group_chat_id="-100127",
        now=NOW + timedelta(hours=24, seconds=1),
    )
    events = restarted._timeline.events(  # noqa: SLF001
        "telegram-group-observation:telegram:7:-100127"
    )
    assert [event.event_type for event in events] == [
        "TELEGRAM_GROUP_MEMBERSHIP_OBSERVED"
    ]
    assert set(events[0].payload["data"]) == {
        "expires_at",
        "group_chat_id",
        "observed_at",
        "principal_id",
    }


def test_telegram_group_consent_replay_never_renews_a_private_decision(
    timeline_engine: Engine,
) -> None:
    store = PostgresTelegramGroupConsentStore(
        timeline_engine,
        tenant_id="telegram-consent-replay-tenant",
        installation_id="telegram-consent-replay-home",
    )
    first = TelegramGroupConsent("-100124", "person:jaap", NOW, NOW + timedelta(days=7))
    assert store.grant(first, operation_id="telegram-group-consent:903") == first
    replay = TelegramGroupConsent(
        "-100124",
        "person:jaap",
        NOW + timedelta(minutes=3),
        NOW + timedelta(days=10),
    )
    assert store.grant(replay, operation_id="telegram-group-consent:903") == first
    assert [
        event.event_type
        for event in store._timeline.events(  # noqa: SLF001
            "telegram-group-consent:person:jaap:-100124"
        )
    ] == ["TELEGRAM_GROUP_CONSENT_GRANTED"]


def test_telegram_group_without_consent_is_durably_rejected(
    timeline_engine: Engine,
) -> None:
    message = TelegramInboundMessage(99001, "-100125", 71, 101, NOW, "Privé houden")
    store = PostgresTelegramIngressStore(
        timeline_engine,
        tenant_id="telegram-group-reject-tenant",
        installation_id="telegram-group-reject-home",
    )
    assert store.reject(message, reason="group_consent_required") == "rejected"
    assert store.reject(message, reason="group_consent_required") == "already_rejected"
    assert store.next_offset() == 99002
    event = store._timeline.events("telegram-inbound:99001")[0]  # noqa: SLF001
    assert event.event_type == "TELEGRAM_INGRESS_REJECTED"
    assert event.payload["data"]["reason"] == "group_consent_required"


def test_admitted_group_replay_after_consent_revoke_completes_without_core(
    timeline_engine: Engine,
) -> None:
    message = TelegramInboundMessage(
        99002, "-100126", 72, 101, NOW, "Onthoud dit voor de groep"
    )
    ingress = PostgresTelegramIngressStore(
        timeline_engine,
        tenant_id="telegram-group-replay-tenant",
        installation_id="telegram-group-replay-home",
    )
    consent = PostgresTelegramGroupConsentStore(
        timeline_engine,
        tenant_id="telegram-group-replay-tenant",
        installation_id="telegram-group-replay-home",
    )
    grant = TelegramGroupConsent("-100126", "person:jaap", NOW, NOW + timedelta(days=7))
    consent.grant(grant, operation_id="telegram-group-consent:99001")

    assert ingress.admit(message, principal_id="person:jaap") is not None
    assert consent.revoke(
        principal_id="person:jaap",
        group_chat_id="-100126",
        operation_id="telegram-group-consent:99002",
        now=NOW + timedelta(seconds=1),
    )

    class Provider:
        def poll(self, *, offset):  # type: ignore[no-untyped-def]
            assert offset == 99002
            return (message,)

        def send_reply(self, message, text):  # type: ignore[no-untyped-def]
            raise AssertionError((message, text))

    class Core:
        def respond(self, message, *, principal_id, audience):  # type: ignore[no-untyped-def]
            raise AssertionError((message, principal_id, audience))

    worker = TelegramIngressWorker(
        Provider(),
        Core(),
        ingress,
        TelegramPrincipalBindings(
            (TelegramPrincipalBinding(101, "person:jaap", ("-100126",)),)
        ),
        group_consent=TelegramGroupConsentResponder(consent),
    )

    assert worker.run_once() == "processed"
    assert ingress.next_offset() == 99003
    events = ingress._timeline.events("telegram-inbound:99002")  # noqa: SLF001
    assert [event.event_type for event in events] == [
        "TELEGRAM_INGRESS_ADMITTED",
        "TELEGRAM_INGRESS_REJECTED_AFTER_ADMISSION",
    ]
    assert events[-1].payload["data"]["reason"] == "group_consent_required"


def test_email_provider_read_is_durable_and_not_repeated_after_restart(
    timeline_engine: Engine,
) -> None:
    calls: list[int] = []
    inbox_id = "eva@example.invalid"

    class EmailProvider(AgentMailReadAdapter):
        def __init__(self) -> None:
            super().__init__(AgentMailConfig(inbox_id, "synthetic-test-secret"))

        async def list_messages(
            self, *, limit: int = 10, page_token: str | None = None
        ) -> MailInboxPage:
            assert limit == 1
            assert page_token is None
            calls.append(limit)
            return MailInboxPage(
                inbox_id,
                (
                    MailMessageSummary(
                        "message-001",
                        "thread-001",
                        "2026-08-30T20:45:00Z",
                        "Lola <lola@example.invalid>",
                        (inbox_id,),
                        "Planning",
                        "Kunnen we morgen...",
                        ("inbox",),
                    ),
                ),
                None,
            )

    turn = ConversationTurn.private(
        "external-email-read",
        "installation",
        "jaap",
        "conversation",
        "message:email-list",
        "Welke nieuwe e-mail heb ik?",
        "2026-08-30T20:45:00Z",
    )
    call = ConversationActionCall(
        "call:email-list",
        "list_email_messages",
        {"limit": 1},
    )
    provider = EmailProvider()
    planner = AgentMailConversationReadPlanner(
        hashlib.sha256(inbox_id.encode()).hexdigest()
    )
    actions = PostgresConversationActions(
        timeline_engine,
        external_tools=(
            PostgresExternalConversationTools(timeline_engine, planner, provider),
        ),
    )

    receipt = actions.execute(turn, call)

    assert receipt.output["status"] == "verified"
    inbox = receipt.output["inbox"]
    assert isinstance(inbox, dict)
    messages = inbox["messages"]
    assert isinstance(messages, list)
    assert messages[0]["subject"] == "Planning"
    assert calls == [1]
    restarted = PostgresConversationActions(
        timeline_engine,
        external_tools=(
            PostgresExternalConversationTools(timeline_engine, planner, provider),
        ),
    )
    assert restarted.pending(turn) == receipt
    assert calls == [1]


@pytest.mark.parametrize("step_index", [0, 1])
def test_external_concurrent_retry_cannot_resume_an_inflight_dispatch(
    timeline_engine: Engine, step_index: int,
) -> None:
    entered, release = Event(), Event()
    dispatched: list[str] = []

    class Planner:
        action_names = ("perform_unfamiliar_action",)

        async def prepare(self, turn, call):
            return ConversationToolPreparation(
                tool_name="fixture.action", arguments={},
                expected_proof={"done": True}, receipt_output={},
            )

    class Provider:
        async def execute(self, request):
            dispatched.append(request.operation_id)
            entered.set()
            assert release.wait(5), "test did not release the first dispatch"
            return ToolDispatchResult(True, request.operation_id, {})

        async def observe(self, request, dispatch):
            if dispatch is None:
                return ToolProofObservation("safe_to_execute", {}, {})
            return ToolProofObservation("verified", {"done": True}, {})

    turn = ConversationTurn.private(
        "external-concurrency", "installation", "jaap", "conversation",
        f"message:concurrency:{step_index}", "Doe de actie",
        "2026-09-08T09:00:00Z",
    )
    call = ConversationActionCall("call", "perform_unfamiliar_action", {})
    first = PostgresExternalConversationTools(timeline_engine, Planner(), Provider())
    second = PostgresExternalConversationTools(timeline_engine, Planner(), Provider())
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(first.execute, turn, call, step_index=step_index)
        try:
            assert entered.wait(5), "first dispatch did not start"
            with pytest.raises(ConversationConflict, match="already running"):
                second.pending(turn, step_index=step_index)
            with pytest.raises(ConversationConflict, match="already running"):
                second.execute(turn, call, step_index=step_index)
        finally:
            release.set()
        receipt = future.result(timeout=5)
    assert second.pending(turn, step_index=step_index) == receipt
    assert len(dispatched) == 1


@pytest.mark.parametrize(("crash_in_second_step", "stop_stage", "crash_in_model"), [
    (False, None, False), (True, None, False), (False, "after_first_effect", False),
    (False, "during_second_preparation", False), (False, None, True),
])
def test_pydantic_conversation_chain_restarts_from_durable_receipts(
    timeline_engine: Engine, tmp_path: Path, crash_in_second_step: bool, stop_stage: str | None,
    crash_in_model: bool,
) -> None:
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.application.conversation import (
        ConversationToolDescriptor, IdentityProfile, LivingConversation,
    )
    from eva.application.memory import PresenceMemory
    from eva.contracts.canonical_json import canonical_sha256
    from eva.contracts.memory import MemoryRecall
    from tests.support.scripted_pydantic_conversation import (
        ScriptedCall, ScriptedPhase, ScriptedPydanticConversation, tool_returns,
    )

    descriptor = ConversationToolDescriptor(
        name="inspect_chain_node",
        description="Inspect a named node and return the name of its child.",
        input_schema={
            "type": "object", "properties": {"node": {"type": "string"}},
            "required": ["node"], "additionalProperties": False,
        },
    )
    calls = [ScriptedCall("node:0", descriptor.name, {"node": "root"}),
             ScriptedCall("node:1", descriptor.name, {"node": "child"})]
    effects: list[str] = []
    crash = crash_in_second_step
    model_crash = crash_in_model

    def stop_current_turn():
        journal = PostgresConversationJournal(timeline_engine, external_timeline_scope(
            turn.tenant_id, turn.installation_id, "scope",
        ))
        assert journal.request_conversation_stop(
            principal_id=turn.principal_id, conversation_id=turn.conversation_id,
        ) >= 1

    class Planner:
        action_names = (descriptor.name,)

        async def prepare(self, turn, call):
            node = call.arguments["node"]
            if node == "child" and stop_stage == "during_second_preparation":
                stop_current_turn()
            return ConversationToolPreparation(
                tool_name="fixture.inspect", arguments={"node": node},
                expected_proof={"node": node},
                receipt_output={"child": {"root": "child", "child": "leaf"}[node]},
            )

    class Provider:
        async def execute(self, request):
            nonlocal crash
            effects.append(request.arguments["node"])
            if request.arguments["node"] == "root" and stop_stage == "after_first_effect":
                stop_current_turn()
            if crash and request.arguments["node"] == "child":
                crash = False
                raise SystemExit(137)
            return ToolDispatchResult(True, request.operation_id, {})

        async def observe(self, request, dispatch):
            assert request.arguments["node"] in effects
            return ToolProofObservation("verified", request.expected_proof, {"source": "fixture"})

    class Memory:
        def recall_for_presence(self, query):
            return PresenceMemory(MemoryRecall(
                schema_version=1, contract="MemoryRecall/v1", recall_id=query.recall_id,
                query_purpose=query.query_purpose, tenant_id=query.tenant_id,
                principal_id=query.principal_id, conversation_id=query.conversation_id,
                project_id=query.project_id, requested_valid_at=query.requested_valid_at,
                admitted_fact_ids_sha256=canonical_sha256([]), selected=[],
                excluded_reasons={}, projection_version="fixture",
                scope_sha256=canonical_sha256(query.scope), created_at=query.requested_valid_at,
            ), ())

    def conversation(scripted):
        nonlocal model_crash
        model = PydanticConversationModel(
            auth_file=tmp_path / "unused.json", model_backend=scripted.model,
            conversation_tools=(descriptor,),
        )
        generate = model.generate

        def interrupt_final_model(*args, **kwargs):
            nonlocal model_crash
            if model_crash and effects == ["root", "child"]:
                model_crash = False
                from eva.application.conversation import begin_current_model_request
                begin_current_model_request()
                raise SystemExit(137)
            return generate(*args, **kwargs)

        model.generate = interrupt_final_model
        actions = PostgresConversationActions(timeline_engine, external_tools=(
            PostgresExternalConversationTools(timeline_engine, Planner(), Provider()),
        ))
        runtime = object.__new__(PostgresHindsightMemoryRuntime)
        runtime._engine = timeline_engine
        runtime._runtime_tenant_id = "pydantic-chain"
        runtime._runtime_installation_id = "installation"
        runtime._conversation = LivingConversation(
            Memory(), model, IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80), actions,
        )
        return runtime

    turn = ConversationTurn.private(
        "pydantic-chain", "installation", "jaap", "conversation",
        f"message:chain:{crash_in_second_step}:{stop_stage}:{crash_in_model}", "Inspecteer root en daarna zijn kind.",
        "2026-09-08T09:00:00Z",
    )
    phases = tuple(ScriptedPhase.deferred(call) for call in calls)
    if not crash_in_second_step:
        phases += (ScriptedPhase.text("Het kind heeft leaf als volgende node."),)
    first = ScriptedPydanticConversation(phases)
    if crash_in_second_step or crash_in_model:
        with pytest.raises(SystemExit, match="137"):
            conversation(first).respond(turn)
    else:
        first_reply = conversation(first).respond(turn)
        if stop_stage is not None:
            assert first_reply.provider == "runtime:conversation-stop"
            assert first_reply.model_usage.model_attempts == len(first.captures)
            assert effects == ["root"]
            restarted_model = ScriptedPydanticConversation((ScriptedPhase.text("Must not run."),))
            assert conversation(restarted_model).respond(turn) == first_reply
            assert restarted_model.captures == []
            return
        assert first_reply.model_usage.tool_calls == 2
    assert effects == ["root", "child"]
    second_input = tool_returns(first.captures[1].messages)
    assert second_input[-1].content["child"] == calls[1].arguments["node"]

    resumed = ScriptedPydanticConversation((ScriptedPhase.text("Root en child zijn gecontroleerd."),))
    result = conversation(resumed).respond(turn)
    assert effects == ["root", "child"]
    if crash_in_second_step or crash_in_model:
        assert result.text == "Root en child zijn gecontroleerd."
        assert result.model_usage.model_attempts == (4 if crash_in_model else 3)
        assert result.model_usage.tool_calls == 2
        assert result.model_usage.measurement == ("partial" if crash_in_model else "measured")
        assert len(resumed.captures) == 1
        assert [part.content["child"] for part in tool_returns(resumed.captures[0].messages)] == ["child", "leaf"]
    else:
        assert result.text == "Het kind heeft leaf als volgende node."
        assert resumed.captures == []  # The journal replays the entire answer.



def test_runtime_serializes_message_before_model_and_replays_committed_reply(
    timeline_engine: Engine,
) -> None:
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime

    work_engine = sa.create_engine(
        timeline_engine.url, pool_size=1, max_overflow=0, pool_timeout=0.1,
    )
    entered, release = Event(), Event()
    model_runs = []

    class Conversation:
        def respond(self, turn, recent, *, before_step, model_journal):
            model_runs.append(turn.message_id)
            entered.set()
            assert release.wait(5)
            return GroundedReply(
                turn.conversation_id, turn.message_id, "Klaar.", (), turn.occurred_at, "fixture",
            )

    def runtime():
        result = object.__new__(PostgresHindsightMemoryRuntime)
        result._engine = work_engine
        result._runtime_tenant_id = "runtime-lock"
        result._runtime_installation_id = "installation"
        result._conversation = Conversation()
        return result

    turn = ConversationTurn.private(
        "runtime-lock", "installation", "jaap", "conversation", "message:concurrent",
        "Voer de opdracht uit.", "2026-09-08T09:00:00Z",
    )
    first, second = runtime(), runtime()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(first.respond, turn)
        try:
            if not entered.wait(1):
                future.result(timeout=1)  # Surface a work-pool timeout directly.
                pytest.fail("model did not start")
            with timeline_engine.connect() as connection:
                assert connection.scalar(sa.text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname=current_database() AND state='idle in transaction'"
                )) == 0
            with pytest.raises(ConversationConflict, match="message is already running"):
                second.respond(turn)
            assert model_runs == [turn.message_id]
        finally:
            release.set()
        reply = future.result(timeout=5)
    assert second.respond(turn) == reply
    assert model_runs == [turn.message_id]
    work_engine.dispose()



def test_owner_conversation_stop_is_durable_and_does_not_cross_people_or_chats(
    timeline_engine: Engine,
) -> None:
    from eva.application.conversation import ConversationStopped
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime

    owner = uuid5(NAMESPACE_URL, "stop-owner")
    binding = uuid5(NAMESPACE_URL, "stop-binding")
    conversation_id = f"dashboard:{binding}"
    scope = external_timeline_scope("owner-stop", "installation", "scope")
    journal = PostgresConversationJournal(timeline_engine, scope)

    def turn(message_id, principal_id=str(owner), chat=conversation_id):
        return ConversationTurn.private(
            "owner-stop", "installation", principal_id, chat, message_id,
            "Doe de opdracht.", "2026-09-08T09:00:00Z",
        )

    active = turn("stop:active")
    other_person = turn("stop:other-person", principal_id="another-person")
    other_chat = turn("stop:other-chat", chat="another-conversation")
    done = turn("stop:done")
    for item in (active, other_person, other_chat, done):
        journal.admit(item)
    journal.commit_reply(done, GroundedReply(
        done.conversation_id, done.message_id, "Klaar.", (), done.occurred_at, "fixture",
    ))
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._engine = timeline_engine
    runtime._runtime_tenant_id = "owner-stop"
    runtime._runtime_installation_id = "installation"
    runtime._dashboard_owner_principal_id = owner
    runtime._dashboard_principal_binding_id = binding
    assert runtime.dashboard_stop_conversation() == 1
    assert runtime.dashboard_stop_conversation() == 1  # Idempotent marker, no duplicate event.

    restarted = PostgresConversationJournal(timeline_engine, scope)
    with pytest.raises(ConversationStopped):
        restarted.ensure_running(active)
    for item in (other_person, other_chat, done):
        restarted.ensure_running(item)
    new_turn = turn("stop:new")
    restarted.admit(new_turn)
    restarted.ensure_running(new_turn)

    class Conversation:
        def respond(self, turn, recent, *, before_step, model_journal):
            before_step(turn, None)
            raise AssertionError("stopped turn must not start model work")

    runtime._conversation = Conversation()
    stopped_reply = runtime.respond(active)
    assert stopped_reply.provider == "runtime:conversation-stop"
    assert "geen verdere stappen" in stopped_reply.text
    assert runtime.respond(active) == stopped_reply



@pytest.mark.parametrize("kind", ["tool", "model"])
def test_stop_and_dispatch_admission_share_a_transaction_fence(timeline_engine: Engine, kind):
    from time import monotonic, sleep
    from eva.application.conversation import ConversationStopped

    scope = external_timeline_scope(f"stop-fence-{kind}", "installation", "scope")
    turn = ConversationTurn.private(
        "stop-fence", "installation", "owner", "chat", "message:fence",
        "Doe de opdracht.", "2026-09-08T09:00:00Z",
    )
    entered, release = Event(), Event()

    class PausedStopJournal(PostgresConversationJournal):
        def _stop_fence(self, connection, identity):
            super()._stop_fence(connection, identity)
            entered.set()
            assert release.wait(5)

    stop_journal = PausedStopJournal(timeline_engine, scope)
    dispatch_journal = PostgresConversationJournal(timeline_engine, scope)
    dispatch_journal.admit(turn)
    dispatch_journal.admit_dispatch(turn, "already-started")
    with ThreadPoolExecutor(max_workers=2) as executor:
        stop = executor.submit(stop_journal.request_conversation_stop,
                               principal_id="owner", conversation_id="chat")
        try:
            assert entered.wait(2)
            dispatch = (executor.submit(dispatch_journal.admit_dispatch, turn, "new-step")
                        if kind == "tool" else executor.submit(dispatch_journal.begin_model_request, turn))
            deadline = monotonic() + 2
            blocked = False
            while monotonic() < deadline:
                with timeline_engine.connect() as connection:
                    blocked = bool(connection.scalar(sa.text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND wait_event_type='Lock' AND query LIKE '%pg_advisory_xact_lock%'"
                    )))
                if blocked:
                    break
                sleep(0.01)
            assert blocked, "dispatch must wait for the uncommitted stop fence"
        finally:
            release.set()
        assert stop.result(timeout=2) == 1
        with pytest.raises(ConversationStopped):
            dispatch.result(timeout=2)
    with pytest.raises(ConversationStopped):
        dispatch_journal.admit_dispatch(turn, "already-started")


def test_stop_during_final_answer_preserves_its_reported_usage(timeline_engine: Engine):
    from eva.application.conversation import ConversationModelUsage
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime

    usage = ConversationModelUsage.measured(model_attempts=2, tool_calls=1, input_tokens=33, output_tokens=4)
    turn = ConversationTurn.private(
        "stop-final", "installation", "owner", "chat", "message:final",
        "Doe de opdracht.", "2026-09-08T09:00:00Z",
    )
    journal = PostgresConversationJournal(timeline_engine, external_timeline_scope(
        turn.tenant_id, turn.installation_id, "scope",
    ))

    class Conversation:
        def respond(self, current, recent, *, before_step, model_journal):
            assert journal.request_conversation_stop(principal_id="owner", conversation_id="chat") == 1
            return GroundedReply(current.conversation_id, current.message_id, "Klaar.", (),
                                 current.occurred_at, "fixture", model_usage=usage)

    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._engine = timeline_engine
    runtime._runtime_tenant_id = turn.tenant_id
    runtime._runtime_installation_id = turn.installation_id
    runtime._conversation = Conversation()
    reply = runtime.respond(turn)
    assert reply.provider == "runtime:conversation-stop"
    assert reply.model_usage == usage
    assert runtime.respond(turn) == reply



def test_model_usage_survives_restart_with_unknown_attempt_and_owner_stop(timeline_engine: Engine):
    from dataclasses import replace
    from eva.application.memory_audience import MemoryAudience
    from eva.application.conversation import ConversationModelUsage, ConversationStopped

    turn = ConversationTurn.private(
        "usage-restart", "installation", "owner", "chat", "message:usage-restart",
        "Voer mijn opdracht uit.", "2026-09-08T09:00:00Z",
    )
    scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "scope")
    journal = PostgresConversationJournal(timeline_engine, scope)
    completed = journal.begin_model_request(turn)
    usage = ConversationModelUsage.measured(
        model_attempts=2, tool_calls=1, input_tokens=31, output_tokens=8,
    )
    journal.complete_model_request(turn, completed, usage)
    journal.complete_model_request(turn, completed, usage)  # Exact receipt replay.
    unknown = journal.begin_model_request(turn)  # Process could die before or during I/O.
    restarted = PostgresConversationJournal(timeline_engine, scope)
    assert restarted.model_usage(turn) == ConversationModelUsage.partial(
        model_attempts=3, tool_calls=1, input_tokens=31, output_tokens=8,
    )
    assert restarted.model_usage(replace(turn, principal_id="someone-else", audience=MemoryAudience.private_person(principal_id="someone-else"))).measurement == "unmeasured"
    with pytest.raises(ConversationConflict, match="admission differs"):
        restarted.complete_model_request(replace(turn, principal_id="someone-else", audience=MemoryAudience.private_person(principal_id="someone-else")), unknown, usage)
    with pytest.raises(ConversationConflict, match="result differs"):
        restarted.complete_model_request(turn, completed, ConversationModelUsage.unmeasured())
    assert restarted.request_conversation_stop(principal_id="owner", conversation_id="chat") == 1
    with pytest.raises(ConversationStopped):
        restarted.begin_model_request(turn)
    # An already admitted phase can report its actual usage after stop.
    restarted.complete_model_request(turn, unknown, usage)
    assert restarted.model_usage(turn) == ConversationModelUsage.measured(
        model_attempts=4, tool_calls=2, input_tokens=62, output_tokens=16,
    )


@pytest.mark.parametrize("command", ["/stop", "stop deze opdracht"])
def test_telegram_stop_command_interrupts_other_running_turn_without_model(timeline_engine, command):
    from eva.application.conversation import ConversationModelUsage
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime

    entered, release = Event(), Event()
    active = ConversationTurn.private(
        "telegram-stop", "installation", "owner", "telegram:123", f"active:{command}",
        "Doe de opdracht.", "2026-09-08T09:00:00Z",
    )
    stop = ConversationTurn.private(
        active.tenant_id, active.installation_id, active.principal_id, active.conversation_id,
        f"stop:{command}", command, active.occurred_at,
    )
    calls = []
    class Conversation:
        def respond(self, turn, recent, *, before_step, model_journal):
            calls.append(turn.message_id)
            entered.set()
            assert release.wait(5)
            before_step(turn, ConversationModelUsage.unmeasured())
            raise AssertionError("stopped work must not continue")
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._engine = timeline_engine
    runtime._runtime_tenant_id = active.tenant_id
    runtime._runtime_installation_id = active.installation_id
    runtime._conversation = Conversation()
    with ThreadPoolExecutor(max_workers=2) as executor:
        running = executor.submit(runtime.respond, active)
        try:
            assert entered.wait(2)
            stopped = executor.submit(runtime.respond, stop).result(timeout=2)
            assert stopped.provider == "runtime:conversation-stop-command"
            assert "geen verdere stappen" in stopped.text
        finally:
            release.set()
        assert running.result(timeout=2).provider == "runtime:conversation-stop"
    assert runtime.respond(stop) == stopped
    assert calls == [active.message_id]


def test_stop_command_crash_replay_does_not_stop_later_work_or_other_people(timeline_engine):
    from dataclasses import replace
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime

    turn = ConversationTurn.private(
        "stop-snapshot", "installation", "owner", "telegram:456", "stop-command",
        "/stop", "2026-09-08T09:00:00Z",
    )
    scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "scope")
    crashed = False
    class Journal(PostgresConversationJournal):
        def commit_reply(self, current, reply):
            nonlocal crashed
            if not crashed:
                crashed = True
                raise SystemExit(137)
            return super().commit_reply(current, reply)
    journal = Journal(timeline_engine, scope)
    existing = replace(turn, message_id="old-work", text="Doe iets.")
    other = ConversationTurn.private(turn.tenant_id, turn.installation_id, "other", turn.conversation_id,
                                     "other-work", "Doe iets.", turn.occurred_at)
    journal.admit(existing)
    journal.admit(other)
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._runtime_tenant_id = turn.tenant_id
    runtime._runtime_installation_id = turn.installation_id
    runtime._journal = lambda current: journal
    with pytest.raises(SystemExit):
        runtime.respond(turn)
    later = replace(existing, message_id="later-work")
    journal.admit(later)
    reply = runtime.respond(turn)
    assert reply.provider == "runtime:conversation-stop-command"
    journal.ensure_running(later)
    journal.ensure_running(other)
    from eva.application.conversation import ConversationStopped
    with pytest.raises(ConversationStopped):
        journal.ensure_running(existing)



def test_telegram_bridge_accepts_stop_while_an_http_answer_is_running(timeline_engine):
    from fastapi.testclient import TestClient
    from eva.adapters.api.app import create_app
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    from tests.api.test_telegram_canary_api import _Runtime, _payload

    entered, release = Event(), Event()
    model_turns = []
    class Conversation:
        def respond(self, turn, recent, *, before_step, model_journal):
            model_turns.append(turn)
            entered.set()
            assert release.wait(5)
            before_step(turn, None)
            raise AssertionError("the stop must reach the running HTTP turn")
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._engine = timeline_engine
    runtime._runtime_tenant_id = "family"
    runtime._runtime_installation_id = "home"
    runtime._telegram_principal_bindings = None
    runtime._delegated_work_commands = None
    runtime._conversation = Conversation()
    runtime.admit_telegram_canary = _Runtime().bridge.admit
    runtime.after_response = lambda *args: None
    runtime.start_background_work = lambda: None
    runtime.stop_background_work = lambda: None
    with TestClient(create_app(runtime)) as client, ThreadPoolExecutor(max_workers=2) as executor:
        url = "/internal/v1/telegram/canary/respond"
        running = executor.submit(client.post, url, json=_payload(message_id=101, text="Voer de opdracht uit."))
        try:
            assert entered.wait(2)
            stop = executor.submit(client.post, url, json=_payload(message_id=102, text="/stop")).result(timeout=2)
            assert stop.status_code == 200
            assert stop.json()["provider"] == "runtime:conversation-stop-command"
        finally:
            release.set()
        response = running.result(timeout=2)
    assert response.status_code == 200
    assert response.json()["provider"] == "runtime:conversation-stop"
    assert len(model_turns) == 1
    assert model_turns[0].principal_id == "telegram:7"



def test_exact_stop_arriving_before_ingress_prevents_model_start(timeline_engine):
    from dataclasses import replace
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    from eva.application.conversation import ConversationStopped
    turn = ConversationTurn.private("early-stop", "installation", "owner", "telegram:101",
                                    "early-message", "Werk", "2026-09-08T09:00:00Z")
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._engine = timeline_engine
    runtime._runtime_tenant_id = turn.tenant_id
    runtime._runtime_installation_id = turn.installation_id
    from fastapi.testclient import TestClient
    from eva.adapters.api.app import create_app
    from eva.application.memory_audience import memory_audience_to_wire
    payload = {"tenant_id": turn.tenant_id, "installation_id": turn.installation_id,
               "principal_id": turn.principal_id, "conversation_id": turn.conversation_id,
               "message_id": turn.message_id, "text": turn.text, "occurred_at": turn.occurred_at,
               "audience": memory_audience_to_wire(turn.audience), "recent_messages": []}
    client = TestClient(create_app(runtime))
    for _ in range(2):
        response = client.post("/internal/v1/turns/stop", json=payload)
        assert response.status_code == 200
        assert response.json() == {"contract": "EvaTurnStop/v1",
                                   "conversation_id": turn.conversation_id, "message_id": turn.message_id}
        assert response.headers["cache-control"] == "no-store"
    assert client.post("/internal/v1/turns/stop", json={**payload, "tenant_id": "other"}).status_code == 422
    journal = runtime._journal(turn)
    journal.admit(turn)
    with pytest.raises(ConversationStopped):
        journal.begin_model_request(turn)
    later = replace(turn, message_id="later-message")
    journal.admit(later)
    journal.ensure_running(later)
    assert journal.request_turn_stop(turn, request_message_id="native-stop") == turn.message_id
    restarted = PostgresConversationJournal(timeline_engine, journal._scope_value)
    assert restarted.request_turn_stop(later, request_message_id="native-stop") == turn.message_id
    restarted.ensure_running(later)
    with pytest.raises(ValueError, match="scope"):
        runtime.request_turn_stop(replace(turn, tenant_id="other-tenant"))



def test_model_request_limit_is_durable_across_restart_and_unknown_usage(timeline_engine):
    from eva.application.conversation import ConversationExecutionLimitReached
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    turn = ConversationTurn.private("turn-budget", "installation", "owner", "chat", "limited-turn",
                                    "Voer de opdracht uit.", "2026-09-08T09:00:00Z")
    scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "scope")
    journal = PostgresConversationJournal(timeline_engine, scope, maximum_model_requests=2)
    journal.begin_model_request(turn)
    journal.begin_model_request(turn)  # Unknown usage still consumes an admission.
    restarted = PostgresConversationJournal(timeline_engine, scope, maximum_model_requests=100)
    with pytest.raises(ConversationExecutionLimitReached):
        restarted.begin_model_request(turn)
    class Conversation:
        def respond(self, turn, recent, *, before_step, model_journal):
            model_journal.begin_model_request(turn)
            raise AssertionError("provider must not start after its durable limit")
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._engine = timeline_engine
    runtime._runtime_tenant_id = turn.tenant_id
    runtime._runtime_installation_id = turn.installation_id
    runtime._conversation = Conversation()
    reply = runtime.respond(turn)
    assert reply.provider == "runtime:conversation-limit"
    assert "nog niet af" in reply.text
    assert reply.model_usage.measurement == "partial"
    assert reply.model_usage.model_attempts == 2
    assert runtime.respond(turn) == reply
    from dataclasses import replace
    another = replace(turn, message_id="another-turn")
    restarted.begin_model_request(another)  # No installation/day-wide cap.


@pytest.mark.parametrize("stop_first", [True, False])
def test_native_voice_stop_survives_new_store_and_rejects_late_reply(
    timeline_engine: Engine, stop_first: bool,
) -> None:
    from eva.adapters.postgres.voice_requests import PostgresVoiceRequests

    scope = _scope(f"voice-stop-{stop_first}")
    def store():
        return PostgresVoiceRequests(timeline_engine, tenant_id=str(scope.tenant_id),
                                    installation_id=str(scope.installation_id))
    args = {"device_id": "a" * 32, "request_id": "b" * 32}
    if not stop_first:
        assert store().begin(**args, now=NOW)
    store().interrupt(**args, now=NOW + timedelta(seconds=1))
    timeline_engine.dispose()
    assert not store().begin(**args, now=NOW + timedelta(seconds=2))
    assert not store().claim_reply(**args, now=NOW + timedelta(seconds=3))
    store().interrupt(**args, now=NOW + timedelta(seconds=4))
    # A different physical speaker, request or installation stays independent.
    assert store().begin(**{**args, "device_id": "c" * 32}, now=NOW)
    assert store().begin(**{**args, "request_id": "c" * 32}, now=NOW)
    other = _scope(f"voice-other-{stop_first}")
    assert PostgresVoiceRequests(timeline_engine, tenant_id=str(other.tenant_id),
                                installation_id=str(other.installation_id)).begin(**args, now=NOW)


def test_native_voice_reply_is_single_use_and_expires(timeline_engine: Engine) -> None:
    from eva.adapters.postgres.voice_requests import PostgresVoiceRequests

    scope = _scope("voice-expiry")
    store = PostgresVoiceRequests(timeline_engine, tenant_id=str(scope.tenant_id),
                                 installation_id=str(scope.installation_id))
    now = datetime.now(UTC).replace(microsecond=0)
    args = {"device_id": "a" * 32, "request_id": "b" * 32}
    assert store.begin(**args, now=now)
    assert not store.begin(**args, now=now)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: store.claim_reply(**args, now=now), range(2)))
    assert sorted(claims) == [False, True]
    store.interrupt(**args, now=now + timedelta(seconds=1))
    assert not store.claim_reply(**args, now=now + timedelta(seconds=2))
    args["request_id"] = "c" * 32
    assert store.begin(**args, now=now)
    assert not store.claim_reply(**args, now=now + timedelta(seconds=32))


def test_native_voice_racing_stop_cannot_be_reopened(timeline_engine: Engine) -> None:
    from eva.adapters.postgres.voice_requests import PostgresVoiceRequests

    scope = _scope("voice-race")
    store = PostgresVoiceRequests(timeline_engine, tenant_id=str(scope.tenant_id),
                                 installation_id=str(scope.installation_id))
    for index in range(12):
        args = {"device_id": "a" * 32, "request_id": f"{index:032x}"}
        with ThreadPoolExecutor(max_workers=2) as pool:
            start = pool.submit(store.begin, **args, now=NOW)
            stop = pool.submit(store.interrupt, **args, now=NOW)
            start.result()
            stop.result()
        assert not store.begin(**args, now=NOW)
        assert not store.claim_reply(**args, now=NOW)



def test_native_voice_reply_waiting_for_lock_expires(timeline_engine: Engine) -> None:
    from eva.adapters.postgres.voice_requests import PostgresVoiceRequests

    scope = _scope("voice-lock-expiry")
    store = PostgresVoiceRequests(timeline_engine, tenant_id=str(scope.tenant_id),
                                 installation_id=str(scope.installation_id))
    args = {"device_id": "a" * 32, "request_id": "b" * 32}
    now = datetime.now(UTC).replace(microsecond=0)
    assert store.begin(**args, now=now - timedelta(seconds=31))
    reached_transition = Event()
    original = store._timeline.transition
    def observed_transition(**kwargs):
        reached_transition.set()
        return original(**kwargs)
    store._timeline.transition = observed_transition
    bound = external_timeline_scope(str(scope.tenant_id), str(scope.installation_id), "test")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with timeline_engine.begin() as lock:
            rows = lock.execute(sa.select(operational_checkpoints).where(
                operational_checkpoints.c.tenant_id == bound.tenant_id,
                operational_checkpoints.c.installation_id == bound.installation_id,
            ).with_for_update()).all()
            assert len(rows) == 1
            assert rows[0].deadline == now + timedelta(seconds=1)
            claim = pool.submit(store.claim_reply, **args, now=now)
            assert reached_transition.wait(3)
            lock.execute(sa.text("SELECT pg_sleep(1.1)"))
        assert claim.result(timeout=3) is False



def test_native_ha_http_client_to_core_postgres_cancel_and_resume(timeline_engine: Engine) -> None:
    import asyncio
    import importlib.util
    import socket
    import sys
    import aiohttp
    import uvicorn
    from fastapi import FastAPI
    from eva.adapters.api.native_voice import NativeVoiceService, VoiceReply, install_native_voice
    from eva.adapters.postgres.voice_requests import PostgresVoiceRequests

    path = EVA2_ROOT / "deploy/home-assistant/custom_components/eva2_voice/client.py"
    spec = importlib.util.spec_from_file_location("ha_voice_http_integration", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    scope = _scope("ha-native-http-postgres")
    ledger = PostgresVoiceRequests(timeline_engine, tenant_id=str(scope.tenant_id),
                                  installation_id=str(scope.installation_id))
    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()
        request_ids = []
        async def respond(request):
            request_ids.append(request.request_id)
            if request.text == "eerste":
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return VoiceReply("Ik luister weer.", True)
        service = NativeVoiceService(token="synthetic", devices={"assist_satellite.room": "a" * 32},
                                     ledger=ledger, respond=respond, validate_current=lambda: None)
        app = FastAPI()
        install_native_voice(app, service)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off"))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(3):
                while not server.started:
                    await asyncio.sleep(0.01)
            async with aiohttp.ClientSession() as session:
                client = module.EvaVoiceClient(session, url=f"http://127.0.0.1:{port}",
                    token="synthetic", satellite_ids=frozenset({"assist_satellite.room"}))
                args = dict(satellite_id="assist_satellite.room", device_id="a" * 32,
                            conversation_id="ha-session", language="nl")
                try:
                    old = asyncio.create_task(client.respond(**args, text="eerste"))
                    await asyncio.wait_for(started.wait(), 3)
                    old.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await old
                    await asyncio.wait_for(cancelled.wait(), 3)
                    assert not ledger.begin(device_id="a" * 32, request_id=request_ids[0],
                                            now=datetime.now(UTC).replace(microsecond=0))
                    assert not ledger.claim_reply(device_id="a" * 32, request_id=request_ids[0],
                                                  now=datetime.now(UTC).replace(microsecond=0))
                    reply = await client.respond(**args, text="volgende")
                    assert reply.text == "Ik luister weer." and reply.continue_conversation
                    assert len(request_ids) == 2 and len(set(request_ids)) == 2
                    await client.verify_playback_support(satellite_id=args["satellite_id"], device_id=args["device_id"])
                    from eva.application.audio_playback import AudioPlaybackResult
                    result = AudioPlaybackResult("a" * 16, 2, "interrupted", 2400, 480)
                    await client.record_playback(satellite_id=args["satellite_id"], device_id=args["device_id"],
                                                 answer=reply, result=result.to_wire())
                    assert ledger.playback_status(device_id=args["device_id"], request_id=reply.request_id,
                        reply_sha256=hashlib.sha256(reply.text.encode()).hexdigest()) == "interrupted"
                    # A raw HTTP disconnect sends no explicit interrupt POST.
                    # The ASGI watcher must still cancel and persist the stop.
                    started.clear()
                    cancelled.clear()
                    raw_id = "f" * 32
                    raw = asyncio.create_task(session.post(
                        f"http://127.0.0.1:{port}/internal/v1/ha-voice/turns",
                        headers={"Authorization": "Bearer synthetic"},
                        json={**args, "request_id": raw_id, "text": "eerste"}))
                    await asyncio.wait_for(started.wait(), 3)
                    raw.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await raw
                    await asyncio.wait_for(cancelled.wait(), 3)
                    async with asyncio.timeout(3):
                        while service._cleanups or service._active:
                            await asyncio.sleep(0.01)
                    assert not ledger.claim_reply(device_id="a" * 32, request_id=raw_id,
                                                  now=datetime.now(UTC).replace(microsecond=0))
                finally:
                    await client.close()
        finally:
            server.should_exit = True
            await serving
            await service.close()
            sock.close()
    asyncio.run(run())



def test_household_memory_outbox_preserves_exact_audience_after_reconnect(timeline_engine: Engine):
    from eva.application.memory_audience import HouseholdConsentReference
    actor = "household-device:" + "a" * 32
    ref = HouseholdConsentReference(actor, "home", "household-consent:mandate-1")
    audience = MemoryAudience.household(principal_id=actor, household_id="home", consent_ref=ref)
    conversation = "household:home:conversation:room"
    document = ConversationMemoryDocument(
        tenant_id="household-outbox", installation_id="home", principal_id=actor,
        conversation_id=conversation, document_id=conversation + ":document:1",
        source_ref=conversation + ":message:1", evidence_ref=conversation + ":evidence:1",
        content="De barbecue begint om zes uur.", occurred_at="2026-09-08T21:00:00Z",
        privacy_class="internal", trust="direct_user", audience=audience,
    )
    outbox = PostgresMemoryOutbox(timeline_engine, tenant_id="household-outbox", installation_id="home")
    queued = outbox.enqueue(document)
    assert outbox.enqueue(document) == queued
    timeline_engine.dispose()
    restarted = PostgresMemoryOutbox(timeline_engine, tenant_id="household-outbox", installation_id="home")
    recovered = restarted.pending()
    assert len(recovered) == 1
    assert recovered[0].document == document
    assert recovered[0].document.audience.household_consent_ref == ref
    assert recovered[0].document.audience.kind == "household"



@pytest.mark.parametrize("step_index", [0, 1])
def test_native_stop_during_tool_preparation_prevents_dispatch(timeline_engine: Engine, step_index):
    import asyncio
    from eva.application.cancellation import run_in_cancellation_thread
    entered, release = Event(), Event()
    dispatched = []
    class Planner:
        action_names = ("fixture_action",)
        async def prepare(self, turn, call):
            entered.set()
            assert release.wait(3)
            return ConversationToolPreparation(tool_name="fixture.action", arguments={},
                                               expected_proof={"done": True}, receipt_output={})
    class Provider:
        async def execute(self, request):
            dispatched.append(request.operation_id)
            return ToolDispatchResult(True, request.operation_id, {})
        async def observe(self, request, dispatch):
            return ToolProofObservation("verified", {"done": True}, {})
    tools = PostgresExternalConversationTools(timeline_engine, Planner(), Provider())
    turn = ConversationTurn.private("native-tool-stop", "installation", "owner", "chat",
                                    f"message:{step_index}", "Doe de actie", "2026-09-08T21:00:00Z")
    call = ConversationActionCall("call", "fixture_action", {})
    async def run():
        task = asyncio.create_task(run_in_cancellation_thread(lambda: tools.execute(turn, call, step_index=step_index)))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)  # Deliver owner cancellation before releasing preparation.
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert dispatched == []
    asyncio.run(run())



def test_native_household_responder_runs_real_core_and_pydantic_with_stop_and_resume(timeline_engine: Engine, tmp_path):
    import asyncio
    from pydantic_ai.models.function import FunctionModel
    from eva.adapters.api.native_voice import VoiceRequest
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.adapters.projection.hindsight import HindsightMemoryEngine
    from eva.application.conversation import IdentityProfile, LivingConversation
    from eva.application.memory_audience import HouseholdConsentReference
    from eva.bootstrap.native_voice import NativeHouseholdResponder
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    started, closed = Event(), Event()
    calls = []
    class Memory:
        def recall_for_presence(self, query):
            assert query.audience.kind == "household"
            return HindsightMemoryEngine._empty_presence(query)
    class Consent:
        def active_reference(self, *, principal_id, household_id, now):
            return HouseholdConsentReference(principal_id, household_id, "household-consent:1")
    async def stream(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        yield "Ik luister weer."
    model = PydanticConversationModel(auth_file=tmp_path / "unused.json",
        model_backend=FunctionModel(stream_function=stream), timeout_seconds=10)
    actions = PostgresConversationActions(timeline_engine)
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._engine = timeline_engine
    runtime._runtime_tenant_id = "household-real-core"
    runtime._runtime_installation_id = "installation"
    runtime._household_consent = Consent()
    runtime._conversation_actions = actions
    runtime._conversation = LivingConversation(Memory(), model,
        IdentityProfile("Eva", "Huisgenoot.", ("Eerlijk.",), 80), actions)
    outbox = PostgresMemoryOutbox(timeline_engine, tenant_id=runtime._runtime_tenant_id,
                                 installation_id=runtime._runtime_installation_id)
    class ColdPath:
        def submit(self, document):
            return outbox.enqueue(document)
    runtime._cold_path = ColdPath()
    respond = NativeHouseholdResponder(runtime=runtime, tenant_id=runtime._runtime_tenant_id,
        installation_id=runtime._runtime_installation_id, household_id="home",
        devices={"assist_satellite.room": "a" * 32}, consent=runtime._household_consent)
    payload = dict(request_id="b" * 32, satellite_id="assist_satellite.room", device_id="a" * 32,
                   conversation_id="ha-session", text="Hallo", language="nl")
    async def run():
        task = asyncio.create_task(respond(VoiceRequest(**payload)))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert closed.is_set()
        assert outbox.pending() == ()
        result = await respond(VoiceRequest(**{**payload, "request_id": "c" * 32}))
        assert result.text == "Ik luister weer."
        queued = outbox.pending()
        assert len(queued) == 1
        assert queued[0].document.audience.kind == "household"
        assert queued[0].document.principal_id == "household-device:" + "a" * 32
        assert len(calls) == 2
        assert "gedeeld huishoudelijk gesprek" in str(calls[0])
        assert "speakerapparaat is geen persoon" in str(calls[0])
    asyncio.run(run())


@pytest.mark.parametrize("status", ["completed", "interrupted", "unconfirmed"])
def test_native_playback_survives_stop_restart_and_changes_household_history(timeline_engine, status):
    from eva.adapters.postgres.voice_requests import PostgresVoiceRequests
    from eva.application.audio_playback import AudioPlaybackResult, PlaybackConflict
    from eva.application.memory_audience import HouseholdConsentReference
    from eva.adapters.model.pydantic_conversation import _recent_message_line
    tenant, installation = "voice-history-" + status, "home"
    scope = external_timeline_scope(tenant, installation, "test")
    now = datetime.now(UTC).replace(microsecond=0)
    args = {"device_id": "a" * 32, "request_id": "b" * 32}
    actor = "household-device:" + args["device_id"]
    conversation = "household:home:conversation:" + "c" * 64
    audience = MemoryAudience.household(principal_id=actor, household_id="home",
        consent_ref=HouseholdConsentReference(actor, "home", "household-consent:test"))
    turn = ConversationTurn(tenant_id=tenant, installation_id=installation, principal_id=actor,
        conversation_id=conversation, message_id=conversation + ":request:" + args["request_id"],
        text="Vertel iets", occurred_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), audience=audience)
    journal = PostgresConversationJournal(timeline_engine, scope)
    journal.admit(turn)
    reply = GroundedReply(conversation, turn.message_id, "Eén. Twee. Drie.", (), turn.occurred_at, "test")
    journal.commit_reply(turn, reply)
    def ledger():
        return PostgresVoiceRequests(timeline_engine, tenant_id=tenant, installation_id=installation)
    store = ledger()
    assert store.begin(**args, now=now)
    assert store.claim_reply(**args, now=now, reply_sha256=reply.text_sha256)
    assert journal.recent(turn)[-1].playback_status == "unconfirmed"
    # A later stop intentionally replaces checkpoint data; reply binding must
    # still be recovered from immutable events, including after restart.
    store.interrupt(**args, now=now + timedelta(seconds=1))
    result = AudioPlaybackResult("c" * 16, 1, status, 4800,
        4800 if status == "completed" else 1200 if status == "interrupted" else None)
    store.record_playback(**args, reply_sha256=reply.text_sha256, result=result,
                          now=now + timedelta(seconds=90))
    timeline_engine.dispose()
    restarted = ledger()
    assert restarted.playback_status(**args, reply_sha256=reply.text_sha256) == status
    assert restarted._timeline.load(restarted._key(**args)).phase == "voice_stopped"
    assert not restarted.claim_reply(**args, now=now + timedelta(seconds=91), reply_sha256=reply.text_sha256)
    # Exact retries remain idempotent after expiry.
    restarted.record_playback(**args, reply_sha256=reply.text_sha256, result=result,
                              now=now + timedelta(minutes=11))
    history = PostgresConversationJournal(timeline_engine, scope).recent(turn)
    assert [m.role for m in history] == ["Onbekende spreker", "Eva"]
    assert history[-1].text == reply.text and history[-1].playback_status == status
    context = _recent_message_line(history[-1])
    assert "gegenereerd antwoord" in context
    assert ("afspelen onderbroken" if status == "interrupted" else
            "afspelen voltooid" if status == "completed" else "afspelen niet bevestigd") in context
    assert reply.text in context  # Never invent an exact heard-word prefix.
    assert restarted.playback_status(**{**args, "device_id": "d" * 32}, reply_sha256=reply.text_sha256) == "unconfirmed"
    other = PostgresVoiceRequests(timeline_engine, tenant_id=tenant + "-other", installation_id=installation)
    assert other.playback_status(**args, reply_sha256=reply.text_sha256) == "unconfirmed"
    with pytest.raises(PlaybackConflict):
        other.record_playback(**args, reply_sha256=reply.text_sha256, result=result, now=now)
    with pytest.raises(PlaybackConflict):
        restarted.record_playback(**args, reply_sha256="d" * 64, result=result, now=now)


def test_native_playback_conflicting_reports_cannot_downgrade_completion(timeline_engine):
    from dataclasses import replace
    from eva.adapters.postgres.voice_requests import PostgresVoiceRequests
    from eva.application.audio_playback import AudioPlaybackResult, PlaybackConflict
    store = PostgresVoiceRequests(timeline_engine, tenant_id="voice-playback-conflict", installation_id="home")
    args = {"device_id": "a" * 32, "request_id": "b" * 32, "reply_sha256": "c" * 64}
    now = datetime.now(UTC).replace(microsecond=0)
    assert store.begin(device_id=args["device_id"], request_id=args["request_id"], now=now)
    assert store.claim_reply(**args, now=now)
    unknown = AudioPlaybackResult("a" * 16, 1, "unconfirmed", 480, None)
    store.record_playback(**args, result=unknown, now=now)
    completed = replace(unknown, status="completed", offered_frames=960, confirmed_frames=960)
    for changed in [replace(completed, session_id="b" * 16), replace(completed, generation=2)]:
        with pytest.raises(PlaybackConflict):
            store.record_playback(**args, result=changed, now=now)
    store.record_playback(**args, result=completed, now=now)
    store.interrupt(device_id=args["device_id"], request_id=args["request_id"], now=now)
    with pytest.raises(PlaybackConflict):
        store.record_playback(**args, result=replace(completed, status="interrupted"), now=now)
    assert store.playback_status(**args) == "completed"
    assert store._timeline.load(store._key(device_id=args["device_id"], request_id=args["request_id"])).phase == "voice_stopped"


def test_native_playback_racing_terminal_results_record_only_one(timeline_engine):
    from eva.adapters.postgres.voice_requests import PostgresVoiceRequests
    from eva.application.audio_playback import AudioPlaybackResult, PlaybackConflict
    store = PostgresVoiceRequests(timeline_engine, tenant_id="voice-playback-race", installation_id="home")
    now = datetime.now(UTC).replace(microsecond=0)
    args = {"device_id": "a" * 32, "request_id": "b" * 32, "reply_sha256": "c" * 64}
    store.begin(device_id=args["device_id"], request_id=args["request_id"], now=now)
    store.claim_reply(**args, now=now)
    def record(status):
        try:
            store.record_playback(**args, result=AudioPlaybackResult("a" * 16, 1, status, 480, 480), now=now)
            return status
        except PlaybackConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(record, ("completed", "interrupted")))
    assert results.count("conflict") == 1
    assert store.playback_status(**args) in set(results) - {"conflict"}


def test_native_playback_reporting_deadline_is_checked_after_database_lock(timeline_engine):
    from eva.adapters.postgres.voice_requests import PostgresVoiceRequests
    from eva.application.audio_playback import AudioPlaybackResult, PlaybackConflict
    store = PostgresVoiceRequests(timeline_engine, tenant_id="voice-report-lock-expiry", installation_id="home")
    now = datetime.now(UTC).replace(microsecond=0)
    # Model a previously issued claim whose reporting window ends in one second.
    args = {"device_id": "a" * 32, "request_id": "b" * 32, "reply_sha256": "c" * 64}
    key = store._key(device_id=args["device_id"], request_id=args["request_id"])
    store._timeline.create_thread(thread_id=key, checkpoint_id=key + ":1", occurred_at=now - timedelta(seconds=599),
        data={"reply_sha256": args["reply_sha256"]}, event_type="VOICE_REPLY_CLAIMED", phase="voice_reply_claimed")
    reached = Event()
    original = store._timeline.transition
    def transition(**kwargs):
        reached.set()
        return original(**kwargs)
    store._timeline.transition = transition
    with ThreadPoolExecutor(max_workers=1) as pool:
        with timeline_engine.begin() as lock:
            row = lock.execute(sa.select(operational_checkpoints).where(
                operational_checkpoints.c.thread_id == key,
                operational_checkpoints.c.tenant_id == store._timeline._scope_value.tenant_id,
            ).with_for_update()).one()
            pending = pool.submit(store.record_playback, **args,
                result=AudioPlaybackResult("a" * 16, 1, "completed", 480, 480), now=now)
            assert reached.wait(3)
            lock.execute(sa.text("SELECT pg_sleep(1.1)"))
        with pytest.raises(PlaybackConflict):
            pending.result(timeout=3)
    assert store.playback_status(**args) == "unconfirmed"



def test_timeline_load_uses_one_snapshot_during_concurrent_transition(timeline_engine):
    timeline = PostgresOperationalTimeline(timeline_engine, _scope("load-snapshot-race"))
    first = timeline.create_thread(thread_id="snapshot-thread", checkpoint_id="snapshot:1", occurred_at=NOW, data={})
    advanced = False
    def concurrent_transition(connection, cursor, statement, parameters, context, executemany):
        nonlocal advanced
        if (not advanced and statement.lstrip().startswith("SELECT")
                and "operational_checkpoints" in statement and "FOR UPDATE" not in statement):
            advanced = True
            timeline.transition(thread_id=first.thread_id, expected_revision=first.revision,
                checkpoint_id="snapshot:2", phase="completed", status="completed",
                event_type="SNAPSHOT_ADVANCED", occurred_at=NOW, data={})
    sa.event.listen(timeline_engine, "after_cursor_execute", concurrent_transition)
    try:
        loaded = timeline.load(first.thread_id)
        assert advanced and loaded.revision == 1
    finally:
        sa.event.remove(timeline_engine, "after_cursor_execute", concurrent_transition)
    assert timeline.load(first.thread_id).revision == 2
