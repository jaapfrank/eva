from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import os
from pathlib import Path
from typing import Iterator
from uuid import NAMESPACE_URL, uuid5

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine, make_url

from eva.adapters.postgres.memory_outbox import (
    DurableMemoryRetentionJob,
    PostgresMemoryOutbox,
)
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline,
    TimelineScope,
)
from eva.adapters.postgres.people_memory_overview import PostgresPeopleMemoryOverview
from eva.adapters.postgres.telegram_group_consent import (
    PostgresTelegramGroupConsentStore,
)
from eva.adapters.postgres.telegram_pairing import PostgresTelegramPairingStore
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.application.memory_audience import MemoryAudience
from eva.application.telegram_group_consent import TelegramGroupConsent
from eva.application.telegram_ingress import (
    TelegramPrincipalBinding,
    TelegramPrincipalBindings,
)
from eva.application.telegram_pairing import (
    TelegramPairingChallenge,
    derive_pairing_code,
    pairing_expiry,
)


NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
TENANT = "cockpit-family-canary-tenant"
INSTALLATION = "cockpit-family-canary-home"
PRINCIPAL = "person:cockpit-private-canary"
EVA2_ROOT = Path(__file__).resolve().parents[2]


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
def people_memory_engine() -> Iterator[Engine]:
    dsn = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.fail("EVA2_TEST_POSTGRES_DSN is required")
    url = make_url(dsn)
    if (
        url.get_backend_name() != "postgresql"
        or not url.database
        or not url.database.startswith("eva2_4b_disposable_")
    ):
        pytest.fail("people/memory gate requires an explicit disposable PostgreSQL DB")
    engine = sa.create_engine(url, pool_pre_ping=False)
    config = Config(str(EVA2_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    try:
        with engine.connect() as connection:
            assert int(connection.scalar(sa.text("SHOW server_version_num"))) == 160013
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


def _bindings() -> TelegramPrincipalBindings:
    return TelegramPrincipalBindings(
        (TelegramPrincipalBinding(101, PRINCIPAL, ("101",)),)
    )


def _document(label: str) -> ConversationMemoryDocument:
    return ConversationMemoryDocument(
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        principal_id=PRINCIPAL,
        conversation_id="telegram:private-conversation-canary",
        document_id=f"telegram:private:exchange:{label}",
        content=f"Gebruiker (directe bron): privé geheugen canary {label}",
        occurred_at="2026-08-26T12:00:00Z",
        source_ref=f"telegram:{label}",
        evidence_ref=f"conversation-reply:{label}",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.private_person(principal_id=PRINCIPAL),
    )


def _complete_pairing(engine: Engine) -> None:
    store = PostgresTelegramPairingStore(
        engine,
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        trusted_bindings=_bindings(),
    )
    code = derive_pairing_code(b"p" * 32, telegram_user_id=404, update_id=81001)
    challenge = TelegramPairingChallenge(code, 404, "404", pairing_expiry(NOW))
    store.start(challenge, update_id=81001, now=NOW)
    binding = store.confirm(
        code,
        confirmer_user_id=101,
        confirmer_chat_id="101",
        now=NOW + timedelta(seconds=1),
    )
    assert binding is not None


def test_people_memory_overview_tracks_real_durable_states_without_raw_data(
    people_memory_engine: Engine,
) -> None:
    postgres_engine = people_memory_engine
    overview = PostgresPeopleMemoryOverview(
        postgres_engine, tenant_id=TENANT, installation_id=INSTALLATION
    )
    assert overview.snapshot(now=NOW, hindsight_state="ready").to_payload() == {
        "contains_raw_content": False,
        "contract": "EvaPeopleMemoryOverview/v1",
        "control_plane": {"state": "selected_not_connected"},
        "hindsight": {"state": "ready"},
        "memory": {"state": "no_signals"},
        "people": {"dynamic_account": "no_extra_account"},
        "read_only": True,
        "sharing": {"state": "private_only"},
    }

    _complete_pairing(postgres_engine)
    consent = PostgresTelegramGroupConsentStore(
        postgres_engine, tenant_id=TENANT, installation_id=INSTALLATION
    )
    consent.grant(
        TelegramGroupConsent("-100123", PRINCIPAL, NOW, NOW + timedelta(days=7)),
        operation_id="cockpit-consent-active",
    )
    outbox = PostgresMemoryOutbox(
        postgres_engine, tenant_id=TENANT, installation_id=INSTALLATION
    )
    first = _document("retained")
    queued = outbox.enqueue(first)
    assert overview.snapshot(now=NOW, hindsight_state="ready").memory_flow == (
        "processing"
    )
    attempted = outbox.begin(queued, now=NOW + timedelta(seconds=2))
    assert attempted is not None
    outbox.succeed(
        attempted,
        MemoryRetentionReceipt(
            bank_id="private-hindsight-bank-canary",
            document_id=first.document_id,
            content_sha256=hashlib.sha256(first.content.encode()).hexdigest(),
        ),
        now=NOW + timedelta(seconds=3),
    )

    restarted = PostgresPeopleMemoryOverview(
        sa.create_engine(postgres_engine.url, pool_pre_ping=False),
        tenant_id=TENANT,
        installation_id=INSTALLATION,
    )
    snapshot = restarted.snapshot(now=NOW + timedelta(seconds=4), hindsight_state="ready")
    assert snapshot.dynamic_account == "extra_account_linked"
    assert snapshot.group_sharing == "group_active"
    assert snapshot.memory_flow == "up_to_date"
    wire = str(snapshot.to_payload())
    for canary in (
        PRINCIPAL,
        "404",
        "-100123",
        "private-conversation-canary",
        "privé geheugen canary",
        "private-hindsight-bank-canary",
    ):
        assert canary not in wire

    other_scope = PostgresPeopleMemoryOverview(
        postgres_engine,
        tenant_id=TENANT,
        installation_id="cockpit-family-other-installation",
    ).snapshot(now=NOW, hindsight_state="unknown")
    assert other_scope.dynamic_account == "no_extra_account"
    assert other_scope.group_sharing == "private_only"
    assert other_scope.memory_flow == "no_signals"
    assert other_scope.hindsight == "unknown"


def test_people_memory_overview_recalculates_consent_and_memory_attention(
    people_memory_engine: Engine,
) -> None:
    postgres_engine = people_memory_engine
    tenant = "cockpit-attention-tenant"
    installation = "cockpit-attention-home"
    principal = "person:attention-canary"
    overview = PostgresPeopleMemoryOverview(
        postgres_engine, tenant_id=tenant, installation_id=installation
    )
    consent = PostgresTelegramGroupConsentStore(
        postgres_engine, tenant_id=tenant, installation_id=installation
    )
    consent.grant(
        TelegramGroupConsent("-200001", principal, NOW, NOW + timedelta(hours=12)),
        operation_id="cockpit-consent-expiring",
    )
    assert overview.snapshot(now=NOW, hindsight_state="ready").group_sharing == (
        "expires_soon"
    )
    assert overview.snapshot(
        now=NOW + timedelta(days=1), hindsight_state="ready"
    ).group_sharing == "private_only"

    outbox = PostgresMemoryOutbox(
        postgres_engine, tenant_id=tenant, installation_id=installation
    )
    document = ConversationMemoryDocument(
        tenant_id=tenant,
        installation_id=installation,
        principal_id=principal,
        conversation_id="telegram:attention-canary",
        document_id="telegram:attention:exchange:memory",
        content="Gebruiker (directe bron): attention private content canary",
        occurred_at="2026-08-26T12:00:00Z",
        source_ref="telegram:attention-memory",
        evidence_ref="conversation-reply:attention-memory",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.private_person(principal_id=principal),
    )
    job = outbox.enqueue(document)
    for attempt in range(3):
        started = outbox.begin(job, now=NOW + timedelta(seconds=attempt * 2 + 1))
        assert started is not None
        failed = outbox.fail(
            started,
            RuntimeError("private provider error canary"),
            now=NOW + timedelta(seconds=attempt * 2 + 2),
        )
        job = DurableMemoryRetentionJob(document, failed)
        if attempt < 2:
            assert overview.snapshot(
                now=NOW + timedelta(seconds=attempt * 2 + 2),
                hindsight_state="attention",
            ).memory_flow == "retrying"
    result = overview.snapshot(now=NOW + timedelta(seconds=8), hindsight_state="attention")
    assert result.memory_flow == "needs_help"
    assert result.hindsight == "attention"


def test_people_memory_overview_makes_a_corrupt_track_unavailable(
    people_memory_engine: Engine,
) -> None:
    postgres_engine = people_memory_engine
    tenant = "cockpit-corrupt-tenant"
    installation = "cockpit-corrupt-home"
    timeline = PostgresOperationalTimeline(
        postgres_engine,
        TimelineScope(
            tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant}"),
            installation_id=uuid5(
                NAMESPACE_URL, f"eva2:{tenant}:{installation}"
            ),
        ),
    )
    timeline.create_thread(
        thread_id="memory-retain:malformed-cockpit-canary",
        checkpoint_id="memory-retain:malformed-cockpit-canary:queued",
        occurred_at=NOW,
        data={"kind": "corrupted-canary"},
        event_type="MEMORY_RETAIN_QUEUED",
        phase="memory_queued",
        status="active",
    )

    result = PostgresPeopleMemoryOverview(
        postgres_engine, tenant_id=tenant, installation_id=installation
    ).snapshot(now=NOW, hindsight_state="ready")
    assert result.memory_flow == "unavailable"
    assert result.dynamic_account == "no_extra_account"
    assert result.group_sharing == "private_only"
