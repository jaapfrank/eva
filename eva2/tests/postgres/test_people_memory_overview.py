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


def test_owner_memory_sources_private_alias_pagination_erasure_and_revocation(people_memory_engine):
    from dataclasses import replace
    from tests.postgres.test_delegated_work_store import _seed_authority, _id
    from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
    from eva.adapters.postgres.current_catalog import metadata
    from eva.application.memory_audience import TelegramGroupConsentReference

    label = "owner-memory-source-page"
    scope, _, binding = _seed_authority(people_memory_engine, label)
    owner = _id(label, "principal")
    tenant, installation = f"tenant:{label}", f"installation:{label}"
    bindings = TelegramPrincipalBindings((TelegramPrincipalBinding(101, str(owner), ("101",), str(binding)),))
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id=tenant, installation_id=installation)

    def document(name, principal=None, **kwargs):
        principal = principal or str(owner)
        return replace(_document(name), tenant_id=tenant, installation_id=installation,
                       principal_id=principal, conversation_id="telegram:101",
                       audience=MemoryAudience.private_person(principal_id=principal), **kwargs)

    canonical = document("canonical")
    bridge = document("bridge", "telegram:101", content="b"*2100)
    job = outbox.enqueue(canonical)
    job = outbox.begin(job, now=NOW)
    outbox.succeed(job, MemoryRetentionReceipt("bank:test", canonical.document_id,
        hashlib.sha256(canonical.content.encode()).hexdigest()), now=NOW)
    outbox.enqueue(bridge)
    outbox.enqueue(document("other", "person:other"))
    outbox.enqueue(document("expired", retention_until="2026-08-26T12:00:00Z"))
    erased = document("erased")
    outbox.enqueue(erased)
    outbox.erase_content(principal_sha256=hashlib.sha256(str(owner).encode()).hexdigest(),
                        document_id=erased.document_id, operation_sha256="ab"*32, erased_at=NOW)
    group = replace(document("group"), conversation_id="telegram:-123",
                    audience=MemoryAudience.telegram_group(principal_id=str(owner), group_chat_id="-123",
                        consent_ref=TelegramGroupConsentReference(str(owner), "-123", "telegram-group-consent:source-page")))
    outbox.enqueue(group)

    def reader():
        return PostgresOwnerMemorySources(people_memory_engine, tenant_id=tenant,
            installation_id=installation, owner=owner, binding=binding, authority_scope=scope,
            telegram_bindings=bindings)

    page = reader().page(now=NOW, limit=1)
    assert len(page.sources) == 1 and page.next_cursor is not None
    second = reader().page(now=NOW, limit=1, before=page.next_cursor)
    assert len(second.sources) == 1 and second.next_cursor is None
    items = page.sources + second.sources
    assert {s.excerpt for s in items} == {canonical.content, "b"*2048}
    assert {s.retention_state for s in items} == {"retained", "pending"}
    assert next(s for s in items if s.excerpt.startswith("b")).truncated
    assert reader().page(now=NOW).sources == tuple(sorted(items, key=lambda s:(s.occurred_at,s.source_id), reverse=True))
    with pytest.raises(RuntimeError, match="binding is not current"):
        PostgresOwnerMemorySources(people_memory_engine, tenant_id=tenant,
            installation_id=installation, owner=_id(label,"other-owner"),
            binding=binding, authority_scope=scope, telegram_bindings=bindings).page(now=NOW)
    other_installation = PostgresOwnerMemorySources(people_memory_engine, tenant_id=tenant,
        installation_id="installation:other", owner=owner, binding=binding,
        authority_scope=scope, telegram_bindings=bindings)
    assert other_installation.page(now=NOW).sources == ()
    table = metadata.tables["eva_core.principal_bindings"]
    with people_memory_engine.begin() as c:
        c.execute(sa.update(table).where(table.c.tenant_id==scope.tenant_id,
                  table.c.principal_binding_id==binding).values(status="revoked"))
    with pytest.raises(RuntimeError, match="binding is not current"):
        reader().page(now=NOW)


@pytest.mark.parametrize(
    ("table_name", "changes", "message"),
    [
        ("principal_bindings", {"retention_until": "now"}, "binding is not current"),
        ("principals", {"retention_until": "now"}, "binding is not current"),
        ("principal_bindings", {"verified_at": "future"}, "binding is not current"),
        ("principal_bindings", {"status": "revoked"}, "binding is not current"),
        ("principal_bindings", {"provider_id": "other"}, "provider is not verified"),
        ("principal_bindings", {"channel_id": "group"}, "provider is not verified"),
    ],
)
def test_owner_sources_recheck_authority_between_pages(
    people_memory_engine, table_name, changes, message
):
    from dataclasses import replace

    from eva.adapters.postgres.current_catalog import metadata
    from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
    from tests.postgres.test_delegated_work_store import _id, _seed_authority

    label = f"owner-source-recheck-{table_name}-{next(iter(changes))}"
    scope, _, binding = _seed_authority(people_memory_engine, label)
    owner = _id(label, "principal")
    tenant, installation = f"tenant:{label}", f"installation:{label}"
    bindings = TelegramPrincipalBindings((
        TelegramPrincipalBinding(101, str(owner), ("101",), str(binding)),
    ))
    outbox = PostgresMemoryOutbox(
        people_memory_engine, tenant_id=tenant, installation_id=installation
    )
    for index in range(2):
        outbox.enqueue(replace(
            _document(f"{label}-{index}"), tenant_id=tenant,
            installation_id=installation, principal_id="telegram:101",
            conversation_id="telegram:101",
            audience=MemoryAudience.private_person(principal_id="telegram:101"),
        ))
    reader = PostgresOwnerMemorySources(
        people_memory_engine, tenant_id=tenant, installation_id=installation,
        owner=owner, binding=binding, telegram_bindings=bindings,
        authority_scope=scope,
    )
    first = reader.page(now=NOW, limit=1)
    assert len(first.sources) == 1 and first.next_cursor is not None
    table = metadata.tables[f"eva_core.{table_name}"]
    values = {
        key: NOW if value == "now" else NOW + timedelta(seconds=1)
        if value == "future" else value for key, value in changes.items()
    }
    with people_memory_engine.begin() as connection:
        connection.execute(sa.update(table).where(
            table.c.tenant_id == scope.tenant_id,
            table.c.principal_id == owner,
        ).values(**values))
    # Reuse the reader and cursor: neither may cache yesterday's authority.
    with pytest.raises(RuntimeError, match=message):
        reader.page(now=NOW, limit=1, before=first.next_cursor)


@pytest.mark.parametrize("provider,channel", [("telegram", "private"), ("eva-dashboard", "owner-local")])
def test_owner_sources_keep_authority_and_memory_namespaces_separate(
    people_memory_engine, provider, channel
):
    from dataclasses import replace

    from eva.adapters.postgres.current_catalog import metadata
    from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    from tests.postgres.test_delegated_work_store import _id, _seed_authority

    label = f"owner-memory-distinct-{provider}"
    authority, _, binding = _seed_authority(people_memory_engine, label)
    owner = _id(label, "principal")
    table = metadata.tables["eva_core.principal_bindings"]
    with people_memory_engine.begin() as connection:
        connection.execute(sa.update(table).where(
            table.c.tenant_id == authority.tenant_id,
            table.c.principal_binding_id == binding,
        ).values(provider_id=provider, channel_id=channel))
    tenant, installation = f"memory:{label}", "memory:home"
    memory_scope = external_timeline_scope(tenant, installation, "required")
    assert memory_scope.tenant_id != authority.tenant_id
    assert memory_scope.installation_id != authority.installation_id
    mapping = TelegramPrincipalBindings((
        TelegramPrincipalBinding(101, str(owner), ("101",), str(binding)),
    ))
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id=tenant, installation_id=installation)
    for principal in (str(owner), "telegram:101", "telegram:202"):
        outbox.enqueue(replace(
            _document(f"{label}-{principal}"), tenant_id=tenant, installation_id=installation,
            principal_id=principal, conversation_id=principal,
            audience=MemoryAudience.private_person(principal_id=principal),
            content=f"private fixture for {principal}",
        ))

    def reader(scope=authority, aliases=mapping):
        return PostgresOwnerMemorySources(
            people_memory_engine, tenant_id=tenant, installation_id=installation,
            owner=owner, binding=binding, authority_scope=scope, telegram_bindings=aliases,
        )

    first = reader().page(now=NOW, limit=1)
    assert first.next_cursor is not None
    second = reader().page(now=NOW, limit=1, before=first.next_cursor)
    assert {s.excerpt for s in first.sources + second.sources} == {
        f"private fixture for {owner}", "private fixture for telegram:101",
    }
    # Removing the private map never widens access to another alias.
    without_alias = reader(aliases=None).page(now=NOW)
    assert [s.excerpt for s in without_alias.sources] == [f"private fixture for {owner}"]
    with pytest.raises(RuntimeError, match="binding is not current"):
        reader(scope=memory_scope).page(now=NOW)
    with people_memory_engine.begin() as connection:
        connection.execute(sa.update(table).where(
            table.c.tenant_id == authority.tenant_id,
            table.c.principal_binding_id == binding,
        ).values(status="revoked"))
    with pytest.raises(RuntimeError, match="binding is not current"):
        reader().page(now=NOW, before=first.next_cursor)


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
        "control_plane": {"state": "owner_viewer_candidate"},
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
        operation_id="telegram-group-consent:cockpit-active",
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

    other_tenant = PostgresPeopleMemoryOverview(
        postgres_engine,
        tenant_id="cockpit-family-other-tenant",
        installation_id=INSTALLATION,
    ).snapshot(now=NOW, hindsight_state="unknown")
    assert other_tenant.dynamic_account == "no_extra_account"
    assert other_tenant.group_sharing == "private_only"
    assert other_tenant.memory_flow == "no_signals"


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
        operation_id="telegram-group-consent:cockpit-expiring",
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


def _timeline(engine: Engine, *, tenant: str, installation: str) -> PostgresOperationalTimeline:
    return PostgresOperationalTimeline(
        engine,
        TimelineScope(
            tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant}"),
            installation_id=uuid5(
                NAMESPACE_URL, f"eva2:{tenant}:{installation}"
            ),
        ),
    )


def test_people_memory_overview_rejects_a_hash_valid_invalid_binding(
    people_memory_engine: Engine,
) -> None:
    tenant = "cockpit-invalid-binding-tenant"
    installation = "cockpit-invalid-binding-home"
    timeline = _timeline(
        people_memory_engine, tenant=tenant, installation=installation
    )
    timeline.create_thread(
        thread_id="telegram-dynamic-binding:0",
        checkpoint_id="telegram-dynamic-binding:0:bound",
        operation_id="telegram-dynamic-binding:0",
        occurred_at=NOW,
        data={"principal_id": "person:invalid-binding", "telegram_user_id": 0},
        event_type="TELEGRAM_DYNAMIC_BINDING_CREATED",
        phase="bound",
        status="completed",
    )

    result = PostgresPeopleMemoryOverview(
        people_memory_engine, tenant_id=tenant, installation_id=installation
    ).snapshot(now=NOW, hindsight_state="ready")
    assert result.dynamic_account == "unavailable"


@pytest.mark.parametrize(
    ("tenant", "operation_id", "expires_at"),
    (
        (
            "cockpit-invalid-consent-duration",
            "telegram-group-consent:invalid-duration",
            NOW + timedelta(days=60),
        ),
        (
            "cockpit-invalid-consent-operation",
            "invalid-consent-operation",
            NOW + timedelta(days=7),
        ),
    ),
)
def test_people_memory_overview_rejects_hash_valid_invalid_consent(
    people_memory_engine: Engine,
    tenant: str,
    operation_id: str,
    expires_at: datetime,
) -> None:
    installation = "cockpit-invalid-consent-home"
    group = "-300001"
    principal = "person:invalid-consent"
    timeline = _timeline(
        people_memory_engine, tenant=tenant, installation=installation
    )
    timeline.create_thread(
        thread_id=f"telegram-group-consent:{principal}:{group}",
        checkpoint_id=f"telegram-group-consent:{principal}:{group}:granted:1",
        operation_id=operation_id,
        occurred_at=NOW,
        data={
            "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "granted_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "group_chat_id": group,
            "principal_id": principal,
        },
        event_type="TELEGRAM_GROUP_CONSENT_GRANTED",
        phase="group_memory_shared",
        status="active",
    )

    result = PostgresPeopleMemoryOverview(
        people_memory_engine, tenant_id=tenant, installation_id=installation
    ).snapshot(now=NOW, hindsight_state="ready")
    assert result.group_sharing == "unavailable"


def test_people_memory_overview_rejects_incomplete_positive_memory_timeline(
    people_memory_engine: Engine,
) -> None:
    tenant = "cockpit-incomplete-memory-tenant"
    installation = "cockpit-incomplete-memory-home"
    document = ConversationMemoryDocument(
        tenant_id=tenant,
        installation_id=installation,
        principal_id="person:incomplete-memory",
        conversation_id="telegram:incomplete-memory",
        document_id="telegram:incomplete-memory:exchange:1",
        content="Gebruiker (directe bron): incomplete memory canary",
        occurred_at="2026-08-26T12:00:00Z",
        source_ref="telegram:incomplete-memory",
        evidence_ref="conversation-reply:incomplete-memory",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.private_person(
            principal_id="person:incomplete-memory"
        ),
    )
    thread_id = PostgresMemoryOutbox._thread_id(document.document_id)
    _timeline(
        people_memory_engine, tenant=tenant, installation=installation
    ).create_thread(
        thread_id=thread_id,
        checkpoint_id=f"{thread_id}:false-success",
        operation_id=str(uuid5(NAMESPACE_URL, document.document_id)),
        occurred_at=NOW,
        data=PostgresMemoryOutbox._document_data(document),
        event_type="MEMORY_RETAIN_QUEUED",
        phase="memory_retained",
        status="completed",
    )

    result = PostgresPeopleMemoryOverview(
        people_memory_engine, tenant_id=tenant, installation_id=installation
    ).snapshot(now=NOW, hindsight_state="ready")
    assert result.memory_flow == "unavailable"


def test_people_memory_overview_rejects_out_of_order_memory_success(
    people_memory_engine: Engine,
) -> None:
    tenant = "cockpit-out-of-order-memory-tenant"
    installation = "cockpit-out-of-order-memory-home"
    document = ConversationMemoryDocument(
        tenant_id=tenant,
        installation_id=installation,
        principal_id="person:out-of-order-memory",
        conversation_id="telegram:out-of-order-memory",
        document_id="telegram:out-of-order-memory:exchange:1",
        content="Gebruiker (directe bron): out of order memory canary",
        occurred_at="2026-08-26T12:00:00Z",
        source_ref="telegram:out-of-order-memory",
        evidence_ref="conversation-reply:out-of-order-memory",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.private_person(
            principal_id="person:out-of-order-memory"
        ),
    )
    timeline = _timeline(
        people_memory_engine, tenant=tenant, installation=installation
    )
    thread_id = PostgresMemoryOutbox._thread_id(document.document_id)
    operation_id = str(uuid5(NAMESPACE_URL, document.document_id))
    timeline.create_thread(
        thread_id=thread_id,
        checkpoint_id=f"{thread_id}:queued",
        operation_id=operation_id,
        occurred_at=NOW,
        data=PostgresMemoryOutbox._document_data(document),
        event_type="MEMORY_RETAIN_QUEUED",
        phase="memory_queued",
        status="active",
    )
    timeline.transition(
        thread_id=thread_id,
        expected_revision=1,
        checkpoint_id=f"{thread_id}:false-success",
        operation_id=operation_id,
        occurred_at=NOW + timedelta(seconds=1),
        data={
            "bank_id": "invalid-order-bank",
            "content_sha256": hashlib.sha256(document.content.encode()).hexdigest(),
            "document_id": document.document_id,
        },
        event_type="MEMORY_RETAIN_SUCCEEDED",
        phase="memory_retained",
        status="completed",
    )

    result = PostgresPeopleMemoryOverview(
        people_memory_engine, tenant_id=tenant, installation_id=installation
    ).snapshot(now=NOW + timedelta(seconds=2), hindsight_state="ready")
    assert result.memory_flow == "unavailable"
