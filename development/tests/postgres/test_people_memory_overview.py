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
    for item in items:
        full, state = reader().document(item.source_id, now=NOW)
        assert full.content.startswith(item.excerpt)
        assert state == item.retention_state
    from uuid import uuid4
    with pytest.raises(LookupError):
        reader().document(str(uuid4()), now=NOW)
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
    with pytest.raises(RuntimeError, match="binding is not current"):
        reader().document(items[0].source_id, now=NOW)


def test_recent_private_voice_context_respects_target_and_source_boundaries(people_memory_engine):
    from dataclasses import replace
    from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
    from eva.application.conversation import ConversationTurn
    from tests.postgres.test_delegated_work_store import _seed_authority, _id

    label = "recent-private-voice-context"
    scope, _, binding = _seed_authority(people_memory_engine, label)
    owner = _id(label, "principal")
    tenant, installation = f"tenant:{label}", f"installation:{label}"
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id=tenant, installation_id=installation)
    reader = PostgresOwnerMemorySources(people_memory_engine, tenant_id=tenant,
        installation_id=installation, owner=owner, binding=binding, authority_scope=scope)
    source = replace(_document(label), tenant_id=tenant, installation_id=installation,
        principal_id=str(owner), conversation_id="voice:earlier",
        audience=MemoryAudience.private_person(principal_id=str(owner)))
    outbox.enqueue(source)
    turn = ConversationTurn.private(tenant, installation, str(owner), f"dashboard:{binding}",
        "target-message", "Continue our conversation", NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    found = reader.recent_other_channel(turn, now=NOW)
    assert found is not None and found[0].document_id == source.document_id
    voice = replace(turn, conversation_id="voice:new")
    assert reader.recent_other_channel(voice, now=NOW)[0].document_id == source.document_id
    assert reader.recent_other_channel(replace(turn, conversation_id=source.conversation_id), now=NOW) is None
    assert reader.recent_other_channel(turn, now=NOW, after=NOW) is None
    for changes in ({"tenant_id": "other-tenant"}, {"installation_id": "other-installation"},
                    {"project_id": "other-project"}):
        assert reader.recent_other_channel(replace(turn, **changes), now=NOW) is None
    stranger = replace(turn, principal_id="person:other",
        audience=MemoryAudience.private_person(principal_id="person:other"))
    assert reader.recent_other_channel(stranger, now=NOW) is None
    # Reject shared destinations before touching storage, irrespective of identity hints.
    from types import SimpleNamespace
    shared = SimpleNamespace(tenant_id=tenant, installation_id=installation,
        principal_id=str(owner), conversation_id="voice:shared", project_id=None,
        audience=SimpleNamespace(kind="household"))
    assert reader.recent_other_channel(shared, now=NOW) is None
    shared.audience = SimpleNamespace(kind="private_person", principal_id="person:other")
    assert reader.recent_other_channel(shared, now=NOW) is None
    # Once the owner's source is erased, another person's voice source cannot replace it.
    outbox.erase_content(principal_sha256=hashlib.sha256(str(owner).encode()).hexdigest(),
        document_id=source.document_id, operation_sha256="cd"*32, erased_at=NOW)
    outbox.enqueue(replace(source, document_id="other-voice-source", source_ref="other-message",
        principal_id="person:other", conversation_id="voice:other-person",
        audience=MemoryAudience.private_person(principal_id="person:other")))
    assert reader.recent_other_channel(turn, now=NOW) is None


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
EVA2_ROOT = Path(__file__).resolve().parents[3]


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
    config = Config(str(EVA2_ROOT / "deploy/alembic.ini"))
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


@pytest.fixture
def selected_private_case(people_memory_engine, request):
    from dataclasses import replace
    from types import SimpleNamespace
    from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
    from eva.application.memory import RecallCurrentFacts
    from tests.postgres.test_delegated_work_store import _seed_authority, _id

    label = "selected-private-" + hashlib.sha256(request.node.name.encode()).hexdigest()[:16]
    authority, _, binding = _seed_authority(people_memory_engine, label)
    owner = _id(label, "principal")
    tenant, installation = f"tenant:{label}", f"installation:{label}"
    bindings = TelegramPrincipalBindings((
        TelegramPrincipalBinding(101, str(owner), ("101",), str(binding)),
    ))
    reader = PostgresOwnerMemorySources(people_memory_engine, tenant_id=tenant,
        installation_id=installation, owner=owner, binding=binding,
        authority_scope=authority, telegram_bindings=bindings)
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id=tenant, installation_id=installation)
    timeline = _timeline(people_memory_engine, tenant=tenant, installation=installation)
    query = RecallCurrentFacts(recall_id=label, installation_id=installation, tenant_id=tenant,
        principal_id=str(owner), audience=MemoryAudience.private_person(principal_id=str(owner)),
        conversation_id="server-admitted-project-conversation", project_id="project:current",
        query_purpose="work_context", requested_valid_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        scope={"kind":"private"}, subject_ids=(str(owner),), query_text="continue relevant work")

    def seed(name, text="Original source", *, project="project:current", principal=None,
             conversation=None, ingress_changes=None, ingress_at=None, **document_changes):
        principal = principal or str(owner)
        conversation = conversation or "project-source:" + name
        message = "selected:" + uuid5(NAMESPACE_URL, label + name).hex
        source_time = NOW - timedelta(seconds=10)
        original = {"kind":"conversation_ingress", "principal_id":principal,
                    "conversation_id":conversation, "message_id":message, "text":text}
        if project is not None:
            original["project_id"] = project
        original.update(ingress_changes or {})
        timeline.create_thread(thread_id="conversation:" + message,
            checkpoint_id="conversation:" + message + ":ingress",
            occurred_at=ingress_at or source_time, data=original)
        document = replace(_document(label + name), tenant_id=tenant, installation_id=installation,
            principal_id=principal, conversation_id=conversation, source_ref=message,
            evidence_ref="conversation-reply:" + message, project_id=project,
            occurred_at=source_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            audience=MemoryAudience.private_person(principal_id=principal),
            content="Normalized memory material; not the original layout")
        document = replace(document, **document_changes)
        outbox.enqueue(document)
        return document, (principal, document.source_ref, document.evidence_ref)

    return SimpleNamespace(reader=reader, query=query, seed=seed, outbox=outbox,
        engine=people_memory_engine, owner=owner, binding=binding, authority=authority,
        timeline=timeline)


def test_selected_private_sources_preserve_original_project_text_time_and_dedup(selected_private_case):
    from dataclasses import replace
    case = selected_private_case
    text = "Nu alleen drie bullets:\n- eerst een\n- dan twee\n- daarna de volgende stap. Stop na die stap."
    document, ref = case.seed("format", text)
    duplicate = replace(document, document_id=document.document_id + ":duplicate")
    case.outbox.enqueue(duplicate)
    case.seed("nearby-unselected", "Do not fetch this nearby conversation")
    result = case.reader.selected_private_sources(case.query, (ref, ref), now=NOW)
    assert result == ({"source_ref":document.source_ref, "evidence_ref":document.evidence_ref,
        "principal_id":str(case.owner), "project_id":"project:current",
        "occurred_at":(NOW-timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "text":text, "truncated":False},)
    assert "Normalized memory material" not in result[0]["text"]


@pytest.mark.parametrize("source_project,query_project,available", [
    (None,None,True), ("project:current","project:current",True),
    (None,"project:current",False), ("project:current",None,False),
    ("project:other","project:current",False),
])
def test_selected_private_sources_require_exact_project_including_none(
    selected_private_case, source_project, query_project, available,
):
    from dataclasses import replace
    case = selected_private_case
    _doc, ref = case.seed("project", project=source_project)
    result = case.reader.selected_private_sources(replace(case.query, project_id=query_project), (ref,), now=NOW)
    assert bool(result) is available


def test_selected_private_sources_bound_original_count_order_and_explicit_fragment(selected_private_case):
    case = selected_private_case
    first, ref1 = case.seed("first", "first source")
    second, ref2 = case.seed("long", "x" * 5000)
    third, ref3 = case.seed("third", "third source")
    _fourth, ref4 = case.seed("fourth", "fourth source is outside the result budget")
    result = case.reader.selected_private_sources(case.query, (ref2,ref1,ref2,ref3,ref4), now=NOW)
    assert [r["source_ref"] for r in result] == [second.source_ref,first.source_ref,third.source_ref]
    assert result[0]["truncated"] is True and len(result[0]["text"]) == 4096
    assert result[0]["text"].endswith("[Fragment; overige oorspronkelijke tekst niet opgenomen.]")
    assert all("fourth source" not in r["text"] for r in result)


def test_selected_private_sources_refuse_foreign_people_alias_chats_and_shared_queries(selected_private_case):
    from dataclasses import replace
    from types import SimpleNamespace
    case = selected_private_case
    owner_doc, owner_ref = case.seed("owner")
    _other_doc, other_ref = case.seed("other", principal="person:other")
    _alias_doc, alias_ref = case.seed("alias", principal="telegram:101", conversation="telegram:101")
    _misbound_doc, misbound_ref = case.seed("misbound", principal="telegram:101", conversation="telegram:202")
    assert case.reader.selected_private_sources(case.query, (other_ref,misbound_ref), now=NOW) == ()
    assert len(case.reader.selected_private_sources(case.query, (alias_ref,), now=NOW)) == 1
    stranger = replace(case.query, principal_id="person:other",
        audience=MemoryAudience.private_person(principal_id="person:other"))
    assert case.reader.selected_private_sources(stranger, (owner_ref,), now=NOW) == ()
    alias_query = replace(case.query, principal_id="telegram:101", conversation_id="telegram:202",
        audience=MemoryAudience.private_person(principal_id="telegram:101"))
    assert case.reader.selected_private_sources(alias_query, (alias_ref,), now=NOW) == ()
    for kind in ("household", "telegram_group"):
        shared = SimpleNamespace(**{**{field:getattr(case.query, field)
            for field in case.query.__dataclass_fields__},
            "audience":SimpleNamespace(kind=kind,principal_id=str(case.owner))})
        assert case.reader.selected_private_sources(shared, (owner_ref,), now=NOW) == ()
    for conversation in ("telegram:-123", "household:shared:conversation:test"):
        assert case.reader.selected_private_sources(replace(case.query, conversation_id=conversation),
                                                   (owner_ref,), now=NOW) == ()


@pytest.mark.parametrize("reason", ["source_erased","ingress_erased","expired","future_source",
                                    "future_ingress","private_class","wrong_evidence","ingress_project"])
def test_selected_private_sources_reject_unavailable_or_mismatched_source(selected_private_case, reason):
    from dataclasses import replace
    case = selected_private_case
    changes = {}
    if reason == "expired":
        changes["retention_until"] = (NOW-timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if reason == "future_source":
        changes["occurred_at"] = (NOW+timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if reason == "future_ingress":
        changes["ingress_at"] = NOW+timedelta(seconds=1)
    if reason == "private_class":
        changes["privacy_class"] = "restricted"
    if reason == "ingress_erased":
        changes["ingress_changes"] = {"content_erasure":{"status":"erased"}, "text":None}
    if reason == "ingress_project":
        changes["ingress_changes"] = {"project_id":"project:other"}
    document, ref = case.seed(reason, **changes)
    if reason == "source_erased":
        case.outbox.erase_content(principal_sha256=hashlib.sha256(str(case.owner).encode()).hexdigest(),
            document_id=document.document_id, operation_sha256="ab"*32, erased_at=NOW)
    if reason == "wrong_evidence":
        ref = (ref[0],ref[1],"conversation-reply:another-source")
    assert case.reader.selected_private_sources(case.query, (ref,), now=NOW) == ()


@pytest.mark.parametrize("source_binding,ingress_binding,query_binding,available", [
    ("a"*64,"a"*64,"a"*64,True), ("b"*64,"b"*64,"a"*64,False),
    ("a"*64,"b"*64,"a"*64,False), ("a"*64,"a"*64,None,False),
])
def test_selected_private_sources_keep_exact_observation_binding(selected_private_case,
    source_binding, ingress_binding, query_binding, available):
    from dataclasses import replace
    case = selected_private_case
    _doc, ref = case.seed("observation", evidence_ref="observation-binding:"+source_binding,
        ingress_changes={"observation_binding":{"binding_sha256":ingress_binding}})
    scope = {"kind":"private", **({"observation_binding_sha256":query_binding} if query_binding else {})}
    result = case.reader.selected_private_sources(replace(case.query, scope=scope), (ref,), now=NOW)
    assert bool(result) is available


def test_selected_private_sources_recheck_current_owner_and_raw_ingress_hash(selected_private_case, monkeypatch):
    import eva.adapters.postgres.people_memory_overview as module
    from eva.adapters.postgres.current_catalog import metadata
    case = selected_private_case
    _doc, ref = case.seed("hash")
    canonical = module.canonical_sha256
    def wrong_ingress_hash(value):
        return "00"*32 if value.get("data",{}).get("kind") == "conversation_ingress" else canonical(value)
    with monkeypatch.context() as patch:
        patch.setattr(module,"canonical_sha256",wrong_ingress_hash)
        with pytest.raises(RuntimeError, match="ingress hash differs"):
            case.reader.selected_private_sources(case.query, (ref,), now=NOW)
    assert case.reader.selected_private_sources(case.query, (ref,), now=NOW)
    bindings = metadata.tables["eva_core.principal_bindings"]
    with case.engine.begin() as connection:
        connection.execute(sa.update(bindings).where(bindings.c.tenant_id==case.authority.tenant_id,
            bindings.c.principal_binding_id==case.binding).values(status="revoked"))
    with pytest.raises(RuntimeError, match="binding is not current"):
        case.reader.selected_private_sources(case.query, (ref,), now=NOW)


def _learning_arguments(**changes):
    return {"kind": "expectation", "interpretation": "This plan may need revision.",
        "uncertainty_reason": "The actual outcome is not yet known.",
        "review_at": "2026-08-27T12:00:00Z", "outcome": "open",
        "previous_source_ref": None, "previous_evidence_ref": None, **changes}


def test_human_understanding_record_is_host_bound_canonical_and_provisional():
    from eva.application.conversation import ConversationTurn
    from eva.application.human_understanding import (
        build_human_understanding_document, parse_human_understanding_record,
    )
    turn = ConversationTurn.private("tenant", "installation", "person", "conversation",
        "message", "I expect to complete this tomorrow.", "2026-08-26T12:00:00Z",
        project_id="project")
    document = build_human_understanding_document(turn, _learning_arguments(),
        retention_until="2026-08-28T12:00:00Z")
    record = parse_human_understanding_record(document.content, evidence_ref=document.evidence_ref)
    assert record["source_text_sha256"] == hashlib.sha256(turn.text.encode()).hexdigest()
    assert record["recorded_at"] == turn.occurred_at
    assert document.source_ref == turn.message_id and document.project_id == turn.project_id
    assert document.trust == "model_inference_unconfirmed"
    assert document.principal_id == turn.principal_id and document.audience == turn.audience
    assert document == build_human_understanding_document(turn, _learning_arguments(),
        retention_until="2026-08-28T12:00:00Z")
    for content, evidence in ((document.content + " ", document.evidence_ref),
                              (document.content, "human-understanding:" + "0" * 64)):
        with pytest.raises(ValueError, match="bytes or evidence hash"):
            parse_human_understanding_record(content, evidence_ref=evidence)


@pytest.mark.parametrize("changes", [
    {"principal_id": "other"}, {"interpretation": "x" * 1601},
    {"uncertainty_reason": ""}, {"review_at": None},
    {"review_at": "2026-08-27T12:00:00+00:00"}, {"outcome": "confirmed"},
    {"previous_source_ref": "source", "previous_evidence_ref": None},
])
def test_human_understanding_record_rejects_unbound_or_invalid_model_fields(changes):
    from eva.application.conversation import ConversationTurn
    from eva.application.human_understanding import build_human_understanding_document
    turn = ConversationTurn.private("tenant", "installation", "person", "conversation",
        "message", "Real user source", "2026-08-26T12:00:00Z")
    with pytest.raises(ValueError):
        build_human_understanding_document(turn, _learning_arguments(**changes))


def _seed_learning(case, name, *, seconds_ago, previous=None, retained=True,
                   project="project:current", principal=None, **arguments):
    from eva.application.conversation import ConversationTurn
    from eva.application.human_understanding import build_human_understanding_document
    source_time = NOW - timedelta(seconds=seconds_ago)
    text = "Actual user source: " + name
    original, _ref = case.seed(name, text, project=project, principal=principal,
        ingress_at=source_time, occurred_at=source_time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    turn = ConversationTurn.private(original.tenant_id, original.installation_id,
        original.principal_id, original.conversation_id, original.source_ref, text,
        original.occurred_at, project_id=original.project_id)
    if previous is not None:
        arguments.update(previous_source_ref=previous.source_ref,
                         previous_evidence_ref=previous.evidence_ref)
    document = build_human_understanding_document(turn, _learning_arguments(**arguments),
        retention_until="2026-08-28T12:00:00Z")
    _retain_learning(case, document, retained=retained)
    return document


def _retain_learning(case, document, *, retained=True):
    job = case.outbox.enqueue(document)
    if retained:
        job = case.outbox.begin(job, now=NOW)
        case.outbox.succeed(job, MemoryRetentionReceipt("bank:learning", document.document_id,
            hashlib.sha256(document.content.encode()).hexdigest()), now=NOW)


def test_learning_source_rehydrates_exact_retained_chain_and_keeps_direct_sources(selected_private_case):
    case = selected_private_case
    parent = _seed_learning(case, "expectation", seconds_ago=20)
    revised = _seed_learning(case, "counterevidence", seconds_ago=10,
        previous=parent, outcome="refuted", interpretation="Revise the expectation.")
    context = case.reader.learning_source(case.query, revised.source_ref, revised.evidence_ref, now=NOW)
    assert context["contract"] == "EvaHumanUnderstandingContext/v1"
    assert [item["record"]["outcome"] for item in context["records"]] == ["refuted", "open"]
    assert [item["source_ref"] for item in context["records"]] == [revised.source_ref, parent.source_ref]
    # The ordinary three-source budget, original layout, and source trust do not change.
    direct = [case.seed("direct-" + str(i), "Original direct " + str(i)) for i in range(3)]
    sources = case.reader.selected_private_sources(case.query, tuple(ref for _doc, ref in direct), now=NOW)
    # The existing resolver also appends current typed learning, independently
    # of semantic selection. The ordinary source allowance remains exactly three.
    ordinary = [item for item in sources if "learning_context" not in item]
    assert len(ordinary) == 3
    assert [item["text"] for item in ordinary] == ["Original direct " + str(i) for i in range(3)]
    assert all(item["learning_context"]["records"][0]["record"]["outcome"] == "refuted"
               for item in sources if "learning_context" in item)
    case.outbox.erase_content(principal_sha256=hashlib.sha256(str(case.owner).encode()).hexdigest(),
        document_id=parent.document_id, operation_sha256="ab" * 32, erased_at=NOW)
    assert case.reader.learning_source(case.query, revised.source_ref, revised.evidence_ref, now=NOW) is None
    result = case.reader.selected_private_sources(case.query,
        ((str(case.owner), revised.source_ref, revised.evidence_ref),), now=NOW)
    assert len(result) == 1 and result[0]["text"] == "Actual user source: counterevidence"
    assert "learning_context" not in result[0]


@pytest.mark.parametrize("reason", ["pending", "other_project", "other_person", "kind", "same_time",
                                    "source_hash", "record_time", "trust"])
def test_learning_source_refuses_unavailable_or_incoherent_dependencies(selected_private_case, reason):
    from dataclasses import replace
    from eva.application.human_understanding import (
        CONTENT_PREFIX, EVIDENCE_PREFIX, human_understanding_document_id,
        parse_human_understanding_record,
    )
    from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
    case = selected_private_case
    parent = _seed_learning(case, "parent", seconds_ago=20,
        retained=reason != "pending", project="project:other" if reason == "other_project" else "project:current",
        principal="person:other" if reason == "other_person" else None)
    # Use a separate source for updates; coincident source time cannot revise the parent.
    source_time = NOW - timedelta(seconds=20 if reason == "same_time" else 10)
    original, _ = case.seed("child", "Actual new outcome", ingress_at=source_time,
        occurred_at=source_time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    from eva.application.conversation import ConversationTurn
    from eva.application.human_understanding import build_human_understanding_document
    turn = ConversationTurn.private(original.tenant_id, original.installation_id,
        original.principal_id, original.conversation_id, original.source_ref,
        "Actual new outcome", original.occurred_at, project_id=original.project_id)
    document = build_human_understanding_document(turn, _learning_arguments(
        kind="person_hypothesis" if reason == "kind" else "expectation", outcome="refuted",
        previous_source_ref=parent.source_ref, previous_evidence_ref=parent.evidence_ref))
    if reason in ("source_hash", "record_time"):
        record = parse_human_understanding_record(document.content, evidence_ref=document.evidence_ref)
        record["source_text_sha256" if reason == "source_hash" else "recorded_at"] = (
            "0" * 64 if reason == "source_hash" else "2026-08-26T11:00:00Z")
        evidence = EVIDENCE_PREFIX + canonical_sha256(record)
        document = replace(document, evidence_ref=evidence,
            content=CONTENT_PREFIX + canonical_json_bytes(record).decode(),
            document_id=human_understanding_document_id(tenant_id=document.tenant_id,
                installation_id=document.installation_id, principal_id=document.principal_id,
                conversation_id=document.conversation_id, source_ref=document.source_ref, evidence_ref=evidence))
    if reason == "trust":
        document = replace(document, trust="direct_user")
    _retain_learning(case, document)
    assert case.reader.learning_source(case.query, document.source_ref, document.evidence_ref, now=NOW) is None


def test_learning_source_keeps_bounded_chain_and_rechecks_owner(selected_private_case):
    from eva.adapters.postgres.current_catalog import metadata
    case = selected_private_case
    documents = []
    for index in range(9):
        documents.append(_seed_learning(case, "bounded-" + str(index), seconds_ago=90 - index * 5,
            previous=documents[-1] if documents else None))
    assert case.reader.learning_source(case.query, documents[7].source_ref, documents[7].evidence_ref,
        now=NOW, follow_revisions=False) is not None
    assert case.reader.learning_source(case.query, documents[8].source_ref, documents[8].evidence_ref,
        now=NOW) is None
    bindings = metadata.tables["eva_core.principal_bindings"]
    with case.engine.begin() as connection:
        connection.execute(sa.update(bindings).where(bindings.c.tenant_id == case.authority.tenant_id,
            bindings.c.principal_binding_id == case.binding).values(status="revoked"))
    with pytest.raises(RuntimeError, match="binding is not current"):
        case.reader.learning_source(case.query, documents[7].source_ref, documents[7].evidence_ref, now=NOW)


def test_learning_old_selection_follows_revision_and_stops_at_erased_successor(selected_private_case):
    from eva.contracts.canonical_json import canonical_sha256
    case = selected_private_case
    parent = _seed_learning(case, "old-person-picture", seconds_ago=30, kind="person_hypothesis", review_at=None,
        interpretation="Earlier provisional interpretation.")
    revised = _seed_learning(case, "actual-counterevidence", seconds_ago=20, previous=parent,
        kind="person_hypothesis", review_at=None, outcome="refuted", interpretation="Revised provisional interpretation.")
    # Explicit action metadata preserves the relation, not the inference text.
    data = {"principal_id": parent.principal_id, "project_id": parent.project_id,
        "source_ref": revised.source_ref, "evidence_ref": revised.evidence_ref,
        "previous_source_ref": parent.source_ref, "previous_evidence_ref": parent.evidence_ref}
    case.timeline.create_thread(thread_id="human-understanding-action:" + canonical_sha256(data),
        checkpoint_id="learning-revision:admitted", occurred_at=NOW-timedelta(seconds=20),
        data=data, event_type="HUMAN_UNDERSTANDING_ADMITTED", phase="learning_admitted")
    result = case.reader.learning_source(case.query, parent.source_ref, parent.evidence_ref, now=NOW)
    assert result["records"][0]["record"]["interpretation"] == "Revised provisional interpretation."
    assert result["records"][-1]["evidence_ref"] == parent.evidence_ref
    case.outbox.erase_content(principal_sha256=hashlib.sha256(str(case.owner).encode()).hexdigest(),
        document_id=revised.document_id, operation_sha256="ac"*32, erased_at=NOW)
    assert case.reader.learning_source(case.query, parent.source_ref, parent.evidence_ref, now=NOW) is None


def test_learning_ordinary_action_outbox_recall_and_later_context(selected_private_case):
    from eva.application.conversation import (ConversationActionCall, ConversationModelResult,
        ConversationTurn, IdentityProfile, LivingConversation, ConversationConflict)
    from eva.application.memory_background import HindsightColdPathWorker
    from eva.application.human_understanding import build_human_understanding_document
    from eva.adapters.postgres.conversation_actions import PostgresConversationActions
    from eva.adapters.projection.hindsight import HindsightMemoryEngine, HindsightProjectionConfig
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.providers.human_understanding_actions import HumanUnderstandingActions, ACTION
    from dataclasses import replace
    case = selected_private_case
    cold = HindsightColdPathWorker(object(), outbox=case.outbox)
    provider = HumanUnderstandingActions(case.engine, source_reader=lambda: case.reader,
        submit=cold.submit, clock=lambda: NOW)
    actions = PostgresConversationActions(case.engine, external_tools=(provider,))
    identity = IdentityProfile("Eva", "Helpful", ("Preserve uncertainty",))
    class EmptyMemory:
        def recall_for_presence(self, query):
            return HindsightMemoryEngine._empty_presence(query)
    class ScriptedModel:
        def __init__(self, call): self.call, self.index = call, 0
        def generate(self, *args, **kwargs):
            self.index += 1
            return (ConversationModelResult(None, "scripted", action_call=self.call) if self.index == 1
                    else ConversationModelResult("The provisional record is queued.", "scripted"))
    engine = HindsightMemoryEngine(HindsightProjectionConfig("http://127.0.0.1:1"),
        private_source_resolver=lambda query, refs: case.reader.selected_private_sources(query, refs, now=NOW))
    empty_semantic_selection = HindsightMemoryEngine._empty_presence(case.query)
    docs = []
    for index in range(2):
        stamp = NOW-timedelta(seconds=30-index*10)
        original, _ = case.seed("ordinary-learning-"+str(index), "Genuine synthetic user counterinformation " + str(index),
            ingress_at=stamp, occurred_at=stamp.strftime("%Y-%m-%dT%H:%M:%SZ"))
        turn = ConversationTurn.private(original.tenant_id, original.installation_id, original.principal_id,
            original.conversation_id, original.source_ref, "Genuine synthetic user counterinformation " + str(index),
            original.occurred_at, project_id=original.project_id)
        arguments = _learning_arguments(kind="person_hypothesis", review_at=None,
            interpretation="Initial provisional picture." if index == 0 else "Changed provisional picture.",
            outcome="open" if index == 0 else "refuted",
            previous_source_ref=None if index == 0 else docs[0].source_ref,
            previous_evidence_ref=None if index == 0 else docs[0].evidence_ref)
        call = ConversationActionCall("learning-call-"+str(index), ACTION, arguments)
        reply = LivingConversation(EmptyMemory(), ScriptedModel(call), identity, actions).respond(turn)
        assert "queued" in reply.text
        receipt = provider.pending(turn)
        assert receipt.call == call and receipt.output["semantic_retention_completed"] is False
        document = build_human_understanding_document(turn, arguments)
        _retain_learning(case, document)
        assert provider.execute(turn, call) == receipt
        event = case.timeline.events(provider._slot(turn, 0))[0]
        assert "interpretation" not in event.payload["data"] and "arguments" not in event.payload["data"]
        docs.append(document)
        # Hindsight did not select a learning fact. Exact current PG references
        # must still be visible, including after a later write for the same query.
        direct = engine._with_private_sources(case.query, empty_semantic_selection)
        assert len(direct.confirmed_facts) == 1
        current = direct.confirmed_facts[0].value["learning_context"]["records"][0]
        assert current["evidence_ref"] == document.evidence_ref
        assert current["record"]["outcome"] == arguments["outcome"]
        projected = object.__new__(PydanticConversationModel)._instructions(identity, direct, ())
        assert document.evidence_ref in projected and document.source_ref in projected
        assert "previous_source_ref" in projected and "record_human_understanding" in projected
    old_ref = (str(case.owner), docs[0].source_ref, docs[0].evidence_ref)
    sources = case.reader.selected_private_sources(case.query, (old_ref,), now=NOW)
    assert sources[0]["learning_context"]["records"][0]["record"]["interpretation"] == "Changed provisional picture."
    # Real recall enrichment is tested with the selected old semantic fact, not a fabricated direct-user fact.
    from eva.application.memory import PresenceMemory
    from eva.contracts.memory import MemoryRecall, RecalledFact
    from eva.contracts.planning import ConfirmedFact
    from eva.contracts.canonical_json import canonical_sha256
    fact = ConfirmedFact(fact_id="learning-fact", subject=str(case.owner), predicate="memory.observation",
        value={"text":"Obsolete consolidation."}, source_ref=docs[0].source_ref,
        evidence_ref=docs[0].evidence_ref, observed_at=case.query.requested_valid_at, trust="host_policy")
    recall = MemoryRecall(schema_version=1, contract="MemoryRecall/v1", recall_id=case.query.recall_id,
        query_purpose=case.query.query_purpose, tenant_id=case.query.tenant_id, principal_id=case.query.principal_id,
        conversation_id=case.query.conversation_id, project_id=case.query.project_id,
        requested_valid_at=case.query.requested_valid_at, admitted_fact_ids_sha256=canonical_sha256([fact.fact_id]),
        selected=[{"rank":1,"fact_id":fact.fact_id,"source_event_ids":[fact.source_ref],
            "trust":"model_inference_unconfirmed","ranking_reason":"semantic_match"}], excluded_reasons={},
        projection_version="hindsight-test",scope_sha256=canonical_sha256(case.query.scope),created_at=case.query.requested_valid_at)
    enriched = engine._with_private_sources(case.query, PresenceMemory(recall,(fact,)))
    assert enriched.confirmed_facts[0].value["text"] == "Changed provisional picture."
    instructions = object.__new__(PydanticConversationModel)._instructions(identity, enriched, ())
    assert "Changed provisional picture." in instructions and "refuted" in instructions
    assert "Obsolete consolidation." not in instructions
    case.outbox.erase_content(principal_sha256=hashlib.sha256(str(case.owner).encode()).hexdigest(),
        document_id=docs[1].document_id,operation_sha256="ad"*32,erased_at=NOW)
    rechecked = engine._with_private_sources(case.query, enriched)
    assert rechecked.confirmed_facts == () and rechecked.recall.selected == []
    for changes in ({"principal_id":"someone-else", "audience":MemoryAudience.private_person(principal_id="someone-else")}, {"conversation_id":"household:other"}, {"scope":{"kind":"public"}}):
        with pytest.raises(RuntimeError,match="scope differs"):
            case.reader.require_learning_query(replace(case.query, **changes),now=NOW)

    assert case.reader.learning_source(replace(case.query, project_id="another-project"),
        docs[0].source_ref, docs[0].evidence_ref, now=NOW) is None
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    from eva.application.conversation import ConversationStopped
    journal = PostgresConversationJournal(case.engine, external_timeline_scope(turn.tenant_id, turn.installation_id, "scope"))
    journal.request_turn_stop(turn)
    with pytest.raises(ConversationStopped):
        provider.execute(turn, call)


@pytest.mark.parametrize("outcome", ["confirmed", "refuted"])
def test_terminal_expectation_without_review_keeps_exact_predecessor_and_unconfirmed_trust(outcome):
    from eva.application.conversation import ConversationTurn, ConversationActionCall, ConversationActionInputRejected
    from eva.application.human_understanding import build_human_understanding_document, parse_human_understanding_record
    from eva.providers.human_understanding_actions import HumanUnderstandingActions, ACTION
    original = ConversationTurn.private("tenant", "installation", "person", "conversation", "expectation-source",
        "Original synthetic user information.", "2026-08-26T12:00:00Z", project_id="project")
    parent = build_human_understanding_document(original, _learning_arguments())
    turn = ConversationTurn.private("tenant", "installation", "person", "conversation", "actual-outcome-source",
        "Actual synthetic user outcome.", "2026-08-27T12:00:00Z", project_id="project")
    arguments = _learning_arguments(outcome=outcome, review_at=None, previous_source_ref=parent.source_ref,
        previous_evidence_ref=parent.evidence_ref)
    document = build_human_understanding_document(turn, arguments)
    record = parse_human_understanding_record(document.content, evidence_ref=document.evidence_ref)
    assert record["review_at"] is None and record["outcome"] == outcome
    assert record["source_text_sha256"] == hashlib.sha256(turn.text.encode()).hexdigest()
    assert document.trust == "model_inference_unconfirmed" and document.project_id == turn.project_id
    class Reader:
        available = True
        def learning_source(self, query, source, evidence, *, now, follow_revisions):
            assert (source, evidence) == (parent.source_ref, parent.evidence_ref)
            assert follow_revisions is False
            return ({"contract":"EvaHumanUnderstandingContext/v1", "records":[{"source_ref":source,
                "evidence_ref":evidence,"record":parse_human_understanding_record(parent.content,evidence_ref=evidence)}]}
                if self.available else None)
    reader = Reader()
    provider = HumanUnderstandingActions(None, source_reader=lambda:reader, submit=lambda doc:None)
    assert provider._validate(turn, ConversationActionCall("terminal", ACTION, arguments), reader, None, NOW) == document
    reader.available = False
    with pytest.raises(ConversationActionInputRejected):
        provider._validate(turn, ConversationActionCall("missing-parent", ACTION, arguments), reader, None, NOW)
    with pytest.raises(ValueError, match="requires a predecessor"):
        build_human_understanding_document(turn, _learning_arguments(outcome=outcome,review_at=None))
    with pytest.raises(ValueError, match="must be paired"):
        build_human_understanding_document(turn, {**arguments, "previous_evidence_ref":None})


@pytest.mark.parametrize("outcome", ["open", "unknown"])
def test_unresolved_expectation_still_requires_canonical_review(outcome):
    from eva.application.conversation import ConversationTurn
    from eva.application.human_understanding import build_human_understanding_document, parse_human_understanding_record
    turn = ConversationTurn.private("tenant", "installation", "person", "conversation", "message",
        "Synthetic unresolved user source.", "2026-08-26T12:00:00Z")
    arguments = _learning_arguments(outcome=outcome, previous_source_ref="older-source",
        previous_evidence_ref="human-understanding:"+"a"*64)
    with pytest.raises(ValueError, match="review time is required"):
        build_human_understanding_document(turn,{**arguments,"review_at":None})
    with pytest.raises(ValueError, match="canonical UTC"):
        build_human_understanding_document(turn,{**arguments,"review_at":"2026-08-27T12:00:00+00:00"})
    doc = build_human_understanding_document(turn,arguments)
    assert parse_human_understanding_record(doc.content,evidence_ref=doc.evidence_ref)["review_at"] == arguments["review_at"]
