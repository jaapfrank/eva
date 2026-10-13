from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import os
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine, make_url

from eva.adapters.postgres.runtime_resource_usage import (
    PostgresDailyReflectionBudget,
    PostgresMemoryStorageBudget,
)
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.operational_timeline import (
    OperationalTimelineError,
    operational_events,
)
from eva.application.conversation import ConversationTurn, GroundedReply
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.application.memory_background import HindsightColdPathWorker
from eva.application.memory_audience import (
    MemoryAudience,
    TelegramGroupConsentReference,
)
from eva.contracts.canonical_json import canonical_json_bytes
from eva.contracts.runtime_budget import RuntimeResourceBudget
from eva.operations.portable_restore_authority import (
    PrivateRestoreAuthorityStore,
    read_private_restore_authority,
)


EVA2_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
TENANT = "reflection-budget-tenant"
INSTALLATION = "reflection-budget-installation"


def _budget() -> RuntimeResourceBudget:
    payload = {
        "background_reflections_per_installation_day": 6,
        "background_reflections_per_person_day": 4,
        "contract": "EvaInstallerBudget/v2",
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": 12_000,
        "max_output_tokens": 1_200,
        "memory_bytes_per_person": 20_971_520,
        "memory_bytes_per_group": 20_971_520,
        "memory_bytes_per_installation": 209_715_200,
        "memory_bytes_per_project": 20_971_520,
        "memory_records_per_person": 2_000,
        "memory_records_per_group": 2_000,
        "memory_records_per_installation": 20_000,
        "memory_records_per_project": 2_000,
        "profile_context_tokens": 800,
        "recall_context_tokens": 3_000,
        "retention_days": 365,
        "retrieval_nodes": 50,
        "unknown_cost_policy": "block_autonomous_spend",
    }
    return RuntimeResourceBudget(
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        source_budget_sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        recall_context_tokens=3_000,
        retrieval_nodes=50,
        max_input_tokens=12_000,
        max_output_tokens=1_200,
        profile_context_tokens=800,
        memory_records_per_person=2_000,
        memory_bytes_per_person=20_971_520,
        memory_records_per_group=2_000,
        memory_bytes_per_group=20_971_520,
        memory_records_per_project=2_000,
        memory_bytes_per_project=20_971_520,
        memory_records_per_installation=20_000,
        memory_bytes_per_installation=209_715_200,
        retention_days=365,
        background_reflections_per_person_day=4,
        background_reflections_per_installation_day=6,
        external_monthly_spend_cap_eur_cents=0,
    )


def _storage_budget() -> RuntimeResourceBudget:
    payload = {
        **_budget().budget_payload(),
        "memory_bytes_per_person": 1_048_576,
        "memory_records_per_person": 100,
        "retention_days": 7,
    }
    return RuntimeResourceBudget(
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        source_budget_sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        recall_context_tokens=3_000,
        retrieval_nodes=50,
        max_input_tokens=12_000,
        max_output_tokens=1_200,
        profile_context_tokens=800,
        memory_records_per_person=100,
        memory_bytes_per_person=1_048_576,
        memory_records_per_group=2_000,
        memory_bytes_per_group=20_971_520,
        memory_records_per_project=2_000,
        memory_bytes_per_project=20_971_520,
        memory_records_per_installation=20_000,
        memory_bytes_per_installation=209_715_200,
        retention_days=7,
        background_reflections_per_person_day=4,
        background_reflections_per_installation_day=6,
        external_monthly_spend_cap_eur_cents=0,
    )


def _limited_storage_budget(
    installation_id: str,
    *,
    group_records: int = 100,
    group_bytes: int = 1_048_576,
    project_records: int = 100,
    project_bytes: int = 1_048_576,
    installation_records: int = 1_000,
    installation_bytes: int = 10_485_760,
) -> RuntimeResourceBudget:
    payload = {
        **_storage_budget().budget_payload(),
        "memory_bytes_per_group": group_bytes,
        "memory_bytes_per_installation": installation_bytes,
        "memory_bytes_per_project": project_bytes,
        "memory_records_per_group": group_records,
        "memory_records_per_installation": installation_records,
        "memory_records_per_project": project_records,
    }
    return RuntimeResourceBudget(
        tenant_id=TENANT,
        installation_id=installation_id,
        source_budget_sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        recall_context_tokens=3_000,
        retrieval_nodes=50,
        max_input_tokens=12_000,
        max_output_tokens=1_200,
        profile_context_tokens=800,
        memory_records_per_person=100,
        memory_bytes_per_person=1_048_576,
        memory_records_per_group=group_records,
        memory_bytes_per_group=group_bytes,
        memory_records_per_project=project_records,
        memory_bytes_per_project=project_bytes,
        memory_records_per_installation=installation_records,
        memory_bytes_per_installation=installation_bytes,
        retention_days=7,
        background_reflections_per_person_day=4,
        background_reflections_per_installation_day=6,
        external_monthly_spend_cap_eur_cents=0,
    )


def _document(
    document_id: str,
    *,
    principal_id: str = "person:memory",
    content: str = "x",
    installation_id: str = INSTALLATION,
    project_id: str | None = None,
) -> ConversationMemoryDocument:
    return ConversationMemoryDocument(
        tenant_id=TENANT,
        installation_id=installation_id,
        principal_id=principal_id,
        conversation_id=f"conversation:{principal_id}",
        document_id=document_id,
        content=content,
        occurred_at="2026-08-29T09:00:00Z",
        source_ref=f"source:{document_id}",
        evidence_ref=f"evidence:{document_id}",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.private_person(principal_id=principal_id),
        project_id=project_id,
    )


def _group_document(
    document_id: str,
    *,
    installation_id: str,
    principal_id: str,
    group_chat_id: str,
    content: str = "x",
) -> ConversationMemoryDocument:
    consent = TelegramGroupConsentReference(
        principal_id=principal_id,
        group_chat_id=group_chat_id,
        operation_id=f"telegram-group-consent:{principal_id}:{group_chat_id[1:]}",
    )
    return ConversationMemoryDocument(
        tenant_id=TENANT,
        installation_id=installation_id,
        principal_id=principal_id,
        conversation_id=f"telegram:{group_chat_id}",
        document_id=document_id,
        content=content,
        occurred_at="2026-08-29T09:00:00Z",
        source_ref=f"telegram:{group_chat_id}:source:{document_id}",
        evidence_ref=f"telegram:{group_chat_id}:evidence:{document_id}",
        privacy_class="internal",
        trust="direct_user",
        audience=MemoryAudience.telegram_group(
            principal_id=principal_id,
            group_chat_id=group_chat_id,
            consent_ref=consent,
        ),
    )


@pytest.fixture(scope="module")
def resource_engine() -> Iterator[Engine]:
    dsn = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.fail("EVA2_TEST_POSTGRES_DSN is required")
    url = make_url(dsn)
    if (
        url.get_backend_name() != "postgresql"
        or not url.database
        or not url.database.startswith("eva2_living_disposable_")
    ):
        pytest.fail("resource gate requires an explicit disposable PostgreSQL DB")
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
        command.upgrade(config, "head")
        yield engine
    finally:
        try:
            command.downgrade(config, "base")
        finally:
            engine.dispose()


def _governor(engine: Engine) -> PostgresDailyReflectionBudget:
    return PostgresDailyReflectionBudget(
        engine,
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        resource_budget=_budget(),
    )


def test_daily_reflection_limits_survive_races_restart_and_day_boundary(
    resource_engine: Engine,
) -> None:
    first = _governor(resource_engine)

    with ThreadPoolExecutor(max_workers=8) as pool:
        jaap = tuple(
            pool.map(
                lambda _index: first.admit_background_reflection(
                    tenant_id=TENANT,
                    installation_id=INSTALLATION,
                    principal_id="person:jaap",
                    now=NOW,
                ),
                range(8),
            )
        )

    assert sum(item.admitted for item in jaap) == 4
    assert {item.reason for item in jaap if not item.admitted} == {
        "person_daily_limit_reached"
    }

    restarted = _governor(resource_engine)
    lola = tuple(
        restarted.admit_background_reflection(
            tenant_id=TENANT,
            installation_id=INSTALLATION,
            principal_id="person:lola",
            now=NOW + timedelta(seconds=index + 1),
        )
        for index in range(3)
    )
    assert [item.admitted for item in lola] == [True, True, False]
    assert lola[-1].reason == "installation_daily_limit_reached"
    assert lola[-1].installation_used == 6

    next_day = restarted.admit_background_reflection(
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        principal_id="person:jaap",
        now=NOW + timedelta(days=1),
    )
    assert next_day.admitted is True
    assert next_day.person_used == 1
    assert next_day.installation_used == 1


def test_reflection_admission_rejects_cross_scope(
    resource_engine: Engine,
) -> None:
    with pytest.raises(ValueError, match="crossed its runtime scope"):
        _governor(resource_engine).admit_background_reflection(
            tenant_id="other-tenant",
            installation_id=INSTALLATION,
            principal_id="person:jaap",
            now=NOW,
        )


def test_memory_storage_limits_survive_races_restart_and_expiry(
    resource_engine: Engine,
) -> None:
    storage = PostgresMemoryStorageBudget(
        resource_engine,
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        resource_budget=_storage_budget(),
        removal_lease_seconds=1,
    )
    large = _document("memory:large", content="é" * 300_000)
    admitted = storage.admit_memory(large, now=NOW)
    replayed = storage.admit_memory(large, now=NOW + timedelta(seconds=1))

    assert admitted.admitted is True
    assert admitted.content_bytes == 600_000
    assert admitted.retention_until == "2026-09-05T09:00:00Z"
    assert replayed.admitted is True
    assert replayed.replayed is True
    assert replayed.person_records_used == 1
    assert replayed.person_bytes_used == 600_000

    byte_blocked = storage.admit_memory(
        _document("memory:too-many-bytes", content="b" * 500_000),
        now=NOW + timedelta(seconds=2),
    )
    assert byte_blocked.admitted is False
    assert byte_blocked.reason == "person_byte_limit_reached"
    assert byte_blocked.retention_until is None

    other_person = storage.admit_memory(
        _document(
            "memory:other-person",
            principal_id="person:other",
            content="b" * 500_000,
        ),
        now=NOW + timedelta(seconds=3),
    )
    assert other_person.admitted is True

    for index in range(99):
        assert storage.admit_memory(
            _document(f"memory:race:prefill:{index}", principal_id="person:race"),
            now=NOW + timedelta(seconds=10 + index),
        ).admitted
    with ThreadPoolExecutor(max_workers=8) as pool:
        raced = tuple(
            pool.map(
                lambda index: storage.admit_memory(
                    _document(f"memory:race:final:{index}", principal_id="person:race"),
                    now=NOW + timedelta(seconds=200 + index),
                ),
                range(8),
            )
        )
    assert sum(item.admitted for item in raced) == 1
    assert {item.reason for item in raced if not item.admitted} == {
        "person_record_limit_reached"
    }

    retained_document = replace(large, retention_until=admitted.retention_until)
    receipt = MemoryRetentionReceipt(
        bank_id="eva2-" + "a" * 32,
        document_id=large.document_id,
        content_sha256=hashlib.sha256(large.content.encode("utf-8")).hexdigest(),
    )
    storage.mark_retained(retained_document, receipt, now=NOW + timedelta(seconds=300))
    assert storage.claim_expired(now=NOW + timedelta(days=7, seconds=-1)) == ()

    restarted = PostgresMemoryStorageBudget(
        resource_engine,
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        resource_budget=_storage_budget(),
        removal_lease_seconds=1,
    )
    first_claim = restarted.claim_expired(now=NOW + timedelta(days=7))
    assert len(first_claim) == 1
    assert first_claim[0].document_id == large.document_id
    assert restarted.claim_expired(now=NOW + timedelta(days=7)) == ()
    second_claim = restarted.claim_expired(now=NOW + timedelta(days=7, seconds=1))
    assert len(second_claim) == 1
    assert second_claim[0].state.retry_count == 2
    restarted.mark_removed(second_claim[0], now=NOW + timedelta(days=7, seconds=2))

    replacement = restarted.admit_memory(
        _document("memory:replacement", content="c" * 500_000),
        now=NOW + timedelta(days=7, seconds=3),
    )
    assert replacement.admitted is True
    assert replacement.person_records_used == 1
    assert replacement.person_bytes_used == 500_000


def test_owner_erasure_is_scoped_durable_and_content_free(
    resource_engine: Engine,
) -> None:
    installation_id = "memory-owner-erasure"
    budget = _limited_storage_budget(
        installation_id,
        group_bytes=1_048_576,
        installation_bytes=1_048_576,
    )
    storage = PostgresMemoryStorageBudget(
        resource_engine,
        tenant_id=TENANT,
        installation_id=installation_id,
        resource_budget=budget,
        removal_lease_seconds=1,
    )
    document = _document(
        "memory:owner-erasure",
        installation_id=installation_id,
        principal_id="person:owner",
        content="persoonlijk geheim",
    )
    admission = storage.admit_memory(document, now=NOW)
    retained = replace(document, retention_until=admission.retention_until)
    storage.mark_retained(
        retained,
        MemoryRetentionReceipt(
            bank_id="eva2-" + "e" * 32,
            document_id=document.document_id,
            content_sha256=hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
        ),
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(OperationalTimelineError, match="scope differs"):
        storage.request_erasure(
            principal_id="person:other",
            document_id=document.document_id,
            now=NOW + timedelta(seconds=2),
        )
    job = storage.request_erasure(
        principal_id="person:owner",
        document_id=document.document_id,
        now=NOW + timedelta(seconds=3),
        source_message_id="telegram:owner:41",
    )
    assert job is not None
    assert (
        storage.request_erasure(
            principal_id="person:owner",
            document_id=document.document_id,
            now=NOW + timedelta(seconds=3),
        )
        is None
    )

    scope = storage._scope_value  # exact disposable-PostgreSQL evidence inspection
    with resource_engine.begin() as connection:
        storage._scope(connection)
        payload = connection.scalar(
            sa.select(operational_events.c.payload).where(
                operational_events.c.tenant_id == scope.tenant_id,
                operational_events.c.installation_id == scope.installation_id,
                operational_events.c.thread_id == job.state.thread_id,
                operational_events.c.event_type == "MEMORY_STORAGE_ERASURE_CLAIMED",
            )
        )
    assert payload["data"] == {
        "content_sha256": job.content_sha256,
        "contract": "EvaMemoryStorageErasure/v1",
        "document_sha256": job.document_sha256,
        "principal_sha256": hashlib.sha256(b"person:owner").hexdigest(),
        "reason": "explicit_request",
        "requested_at": "2026-08-29T09:00:03Z",
        "source_message_id": "telegram:owner:41",
    }
    assert document.content not in str(payload)

    restarted = PostgresMemoryStorageBudget(
        resource_engine,
        tenant_id=TENANT,
        installation_id=installation_id,
        resource_budget=budget,
        removal_lease_seconds=1,
    )
    recovered = restarted.claim_expired(now=NOW + timedelta(seconds=4))
    assert len(recovered) == 1
    assert recovered[0].document_id == document.document_id
    assert recovered[0].principal_sha256 == hashlib.sha256(b"person:owner").hexdigest()
    assert recovered[0].source_message_id == "telegram:owner:41"
    restarted.mark_removed(recovered[0], now=NOW + timedelta(seconds=5))
    assert (
        restarted.request_erasure(
            principal_id="person:owner",
            document_id=document.document_id,
            now=NOW + timedelta(seconds=6),
        )
        is None
    )


def test_owner_erasure_resumes_across_semantic_and_raw_stores_after_crash(
    resource_engine: Engine,
    private_tmp_path: Path,
) -> None:
    installation_id = "memory-cross-store-erasure"
    principal_id = "person:owner-cross-store"
    message_id = "telegram:owner-cross-store:41"
    document_id = "telegram:owner-cross-store:exchange:41"
    storage = PostgresMemoryStorageBudget(
        resource_engine,
        tenant_id=TENANT,
        installation_id=installation_id,
        resource_budget=_limited_storage_budget(installation_id),
        removal_lease_seconds=1,
    )
    journal = PostgresConversationJournal(resource_engine, storage._scope_value)  # noqa: SLF001
    turn = ConversationTurn.private(
        tenant_id=TENANT,
        installation_id=installation_id,
        principal_id=principal_id,
        conversation_id="telegram:owner-cross-store",
        message_id=message_id,
        text="ruwe geheime bronzin 41",
        occurred_at="2026-08-29T09:00:00Z",
    )
    journal.admit(turn)
    journal.commit_reply(
        turn,
        GroundedReply(
            turn.conversation_id,
            turn.message_id,
            "ruw geheim antwoord 41",
            (),
            "2026-08-29T09:00:01Z",
            "test",
        ),
    )
    document = ConversationMemoryDocument(
        tenant_id=TENANT,
        installation_id=installation_id,
        principal_id=principal_id,
        conversation_id=turn.conversation_id,
        document_id=document_id,
        content="Gebruiker (directe bron): ruwe geheime bronzin 41",
        occurred_at=turn.occurred_at,
        source_ref=message_id,
        evidence_ref=f"conversation-reply:{message_id}",
        privacy_class="internal",
        trust="direct_user",
        audience=turn.audience,
    )
    admission = storage.admit_memory(document, now=NOW)
    retained = replace(document, retention_until=admission.retention_until)
    storage.mark_retained(
        retained,
        MemoryRetentionReceipt(
            bank_id="eva2-" + "f" * 32,
            document_id=document_id,
            content_sha256=hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
        ),
        now=NOW + timedelta(seconds=1),
    )
    outbox = PostgresMemoryOutbox(
        resource_engine,
        tenant_id=TENANT,
        installation_id=installation_id,
    )
    queued = outbox.enqueue(document)
    assert outbox.begin(queued, now=NOW + timedelta(seconds=1)) is not None

    provider_deletes: list[str] = []

    class Memory:
        def remove_conversation_document(  # type: ignore[no-untyped-def]
            self, *, bank_id, document_id, principal_sha256
        ):
            assert bank_id == "eva2-" + "f" * 32
            assert (
                principal_sha256
                == hashlib.sha256(principal_id.encode("utf-8")).hexdigest()
            )
            provider_deletes.append(document_id)

    class CrashAfterRawErasure:
        def erase_message_content(self, **kwargs):  # type: ignore[no-untyped-def]
            journal.erase_message_content(**kwargs)
            raise RuntimeError("simulated crash after raw erasure")

    private_tmp_path.chmod(0o700)
    authority_path = private_tmp_path / "restore-authority.json"
    restore_authority = PrivateRestoreAuthorityStore(
        authority_path,
        source_installation_id="22222222-2222-4222-8222-222222222222",
    )

    crashing = HindsightColdPathWorker(
        Memory(),  # type: ignore[arg-type]
        outbox=outbox,
        storage_budget=storage,
        conversation_erasure=CrashAfterRawErasure(),  # type: ignore[arg-type]
        restore_authority_erasure=restore_authority,
    )
    with pytest.raises(RuntimeError, match="simulated crash after raw erasure"):
        crashing.erase_document(
            principal_id=principal_id,
            document_id=document_id,
            source_message_id=message_id,
            now=NOW + timedelta(seconds=2),
        )
    assert journal.recent(turn) == ()

    restarted_storage = PostgresMemoryStorageBudget(
        resource_engine,
        tenant_id=TENANT,
        installation_id=installation_id,
        resource_budget=_limited_storage_budget(installation_id),
        removal_lease_seconds=1,
    )
    recovered = restarted_storage.claim_expired(now=NOW + timedelta(seconds=4))
    assert len(recovered) == 1
    assert recovered[0].source_message_id == message_id
    resumed = HindsightColdPathWorker(
        Memory(),  # type: ignore[arg-type]
        outbox=PostgresMemoryOutbox(
            resource_engine,
            tenant_id=TENANT,
            installation_id=installation_id,
        ),
        storage_budget=restarted_storage,
        conversation_erasure=PostgresConversationJournal(
            resource_engine,
            restarted_storage._scope_value,  # noqa: SLF001
        ),
        restore_authority_erasure=restore_authority,
    )
    resumed._remove_job(recovered[0], now=NOW + timedelta(seconds=5))  # noqa: SLF001
    assert provider_deletes == [document_id, document_id]
    assert outbox.pending() == ()
    assert "ruwe geheime bronzin 41" not in str(
        outbox._timeline.events(queued.state.thread_id)  # noqa: SLF001
    )
    assert (
        restarted_storage._timeline.load(  # noqa: SLF001
            recovered[0].state.thread_id
        ).phase
        == "memory_storage_removed"
    )
    assert (
        PostgresConversationJournal(
            resource_engine,
            restarted_storage._scope_value,  # noqa: SLF001
        ).recent(turn)
        == ()
    )
    authority = __import__("json").loads(
        read_private_restore_authority(authority_path)
    )
    assert authority["erasure_generation"] == 1
    assert authority["authorized_archives"] == []


def test_group_and_project_limits_are_atomic_across_people_and_restart(
    resource_engine: Engine,
) -> None:
    installation_id = "memory-scope-group-project"
    budget = _limited_storage_budget(installation_id)
    storage = PostgresMemoryStorageBudget(
        resource_engine,
        tenant_id=TENANT,
        installation_id=installation_id,
        resource_budget=budget,
    )

    group_large = storage.admit_memory(
        _group_document(
            "memory:group:large",
            installation_id=installation_id,
            principal_id="person:group-a",
            group_chat_id="-1001",
            content="é" * 300_000,
        ),
        now=NOW,
    )
    group_bytes_blocked = storage.admit_memory(
        _group_document(
            "memory:group:bytes-blocked",
            installation_id=installation_id,
            principal_id="person:group-b",
            group_chat_id="-1001",
            content="b" * 500_000,
        ),
        now=NOW + timedelta(seconds=1),
    )
    assert group_large.admitted is True
    assert group_large.content_bytes == 600_000
    assert group_bytes_blocked.admitted is False
    assert group_bytes_blocked.reason == "group_byte_limit_reached"
    assert group_bytes_blocked.group_records_used == 1
    assert group_bytes_blocked.group_bytes_used == 600_000

    for index in range(99):
        assert storage.admit_memory(
            _group_document(
                f"memory:group:race:prefill:{index}",
                installation_id=installation_id,
                principal_id=f"person:group-prefill:{index}",
                group_chat_id="-1002",
            ),
            now=NOW + timedelta(seconds=10 + index),
        ).admitted
    restarted = PostgresMemoryStorageBudget(
        resource_engine,
        tenant_id=TENANT,
        installation_id=installation_id,
        resource_budget=budget,
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        group_race = tuple(
            pool.map(
                lambda index: restarted.admit_memory(
                    _group_document(
                        f"memory:group:race:final:{index}",
                        installation_id=installation_id,
                        principal_id=f"person:group-final:{index}",
                        group_chat_id="-1002",
                    ),
                    now=NOW + timedelta(seconds=200 + index),
                ),
                range(8),
            )
        )
    assert sum(item.admitted for item in group_race) == 1
    assert {item.reason for item in group_race if not item.admitted} == {
        "group_record_limit_reached"
    }

    project_large = restarted.admit_memory(
        _document(
            "memory:project:large",
            installation_id=installation_id,
            principal_id="person:project-a",
            project_id="project:alpha",
            content="é" * 300_000,
        ),
        now=NOW + timedelta(seconds=300),
    )
    project_bytes_blocked = restarted.admit_memory(
        _document(
            "memory:project:bytes-blocked",
            installation_id=installation_id,
            principal_id="person:project-b",
            project_id="project:alpha",
            content="b" * 500_000,
        ),
        now=NOW + timedelta(seconds=301),
    )
    assert project_large.admitted is True
    assert project_bytes_blocked.admitted is False
    assert project_bytes_blocked.reason == "project_byte_limit_reached"
    assert project_bytes_blocked.project_records_used == 1
    assert project_bytes_blocked.project_bytes_used == 600_000

    for index in range(99):
        assert restarted.admit_memory(
            _document(
                f"memory:project:race:prefill:{index}",
                installation_id=installation_id,
                principal_id=f"person:project-prefill:{index}",
                project_id="project:race",
            ),
            now=NOW + timedelta(seconds=400 + index),
        ).admitted
    with ThreadPoolExecutor(max_workers=8) as pool:
        project_race = tuple(
            pool.map(
                lambda index: restarted.admit_memory(
                    _document(
                        f"memory:project:race:final:{index}",
                        installation_id=installation_id,
                        principal_id=f"person:project-final:{index}",
                        project_id="project:race",
                    ),
                    now=NOW + timedelta(seconds=600 + index),
                ),
                range(8),
            )
        )
    assert sum(item.admitted for item in project_race) == 1
    assert {item.reason for item in project_race if not item.admitted} == {
        "project_record_limit_reached"
    }


def test_installation_limits_are_atomic_across_independent_scopes(
    resource_engine: Engine,
) -> None:
    bytes_installation = "memory-scope-installation-bytes"
    bytes_budget = _limited_storage_budget(
        bytes_installation,
        installation_records=1_000,
        installation_bytes=1_048_576,
    )
    byte_storage = PostgresMemoryStorageBudget(
        resource_engine,
        tenant_id=TENANT,
        installation_id=bytes_installation,
        resource_budget=bytes_budget,
    )
    first = byte_storage.admit_memory(
        _document(
            "memory:installation:large",
            installation_id=bytes_installation,
            principal_id="person:installation-a",
            content="é" * 300_000,
        ),
        now=NOW,
    )
    blocked = byte_storage.admit_memory(
        _document(
            "memory:installation:bytes-blocked",
            installation_id=bytes_installation,
            principal_id="person:installation-b",
            content="b" * 500_000,
        ),
        now=NOW + timedelta(seconds=1),
    )
    assert first.admitted is True
    assert blocked.admitted is False
    assert blocked.reason == "installation_byte_limit_reached"
    assert blocked.installation_records_used == 1
    assert blocked.installation_bytes_used == 600_000

    records_installation = "memory-scope-installation-records"
    records_budget = _limited_storage_budget(
        records_installation,
        installation_records=100,
        installation_bytes=10_485_760,
    )
    record_storage = PostgresMemoryStorageBudget(
        resource_engine,
        tenant_id=TENANT,
        installation_id=records_installation,
        resource_budget=records_budget,
    )
    for index in range(99):
        assert record_storage.admit_memory(
            _document(
                f"memory:installation:race:prefill:{index}",
                installation_id=records_installation,
                principal_id=f"person:installation-prefill:{index}",
            ),
            now=NOW + timedelta(seconds=10 + index),
        ).admitted
    restarted = PostgresMemoryStorageBudget(
        resource_engine,
        tenant_id=TENANT,
        installation_id=records_installation,
        resource_budget=records_budget,
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        raced = tuple(
            pool.map(
                lambda index: restarted.admit_memory(
                    _document(
                        f"memory:installation:race:final:{index}",
                        installation_id=records_installation,
                        principal_id=f"person:installation-final:{index}",
                    ),
                    now=NOW + timedelta(seconds=200 + index),
                ),
                range(8),
            )
        )
    assert sum(item.admitted for item in raced) == 1
    assert {item.reason for item in raced if not item.admitted} == {
        "installation_record_limit_reached"
    }


def test_removal_releases_group_and_installation_capacity_only_after_ack(
    resource_engine: Engine,
) -> None:
    installation_id = "memory-scope-removal"
    budget = _limited_storage_budget(
        installation_id,
        installation_bytes=1_048_576,
    )
    storage = PostgresMemoryStorageBudget(
        resource_engine,
        tenant_id=TENANT,
        installation_id=installation_id,
        resource_budget=budget,
        removal_lease_seconds=1,
    )
    document = _group_document(
        "memory:scope-removal:large",
        installation_id=installation_id,
        principal_id="person:removal-a",
        group_chat_id="-2001",
        content="é" * 300_000,
    )
    admission = storage.admit_memory(document, now=NOW)
    retained = replace(document, retention_until=admission.retention_until)
    storage.mark_retained(
        retained,
        MemoryRetentionReceipt(
            bank_id="eva2-" + "b" * 32,
            document_id=document.document_id,
            content_sha256=hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
        ),
        now=NOW + timedelta(seconds=1),
    )
    before_ack = storage.admit_memory(
        _group_document(
            "memory:scope-removal:before-ack",
            installation_id=installation_id,
            principal_id="person:removal-b",
            group_chat_id="-2001",
            content="b" * 500_000,
        ),
        now=NOW + timedelta(days=7),
    )
    assert before_ack.admitted is False
    assert before_ack.reason == "group_byte_limit_reached"

    jobs = storage.claim_expired(now=NOW + timedelta(days=7))
    assert len(jobs) == 1
    storage.mark_removed(jobs[0], now=NOW + timedelta(days=7, seconds=1))
    replacement = storage.admit_memory(
        _group_document(
            "memory:scope-removal:replacement",
            installation_id=installation_id,
            principal_id="person:removal-b",
            group_chat_id="-2001",
            content="b" * 500_000,
        ),
        now=NOW + timedelta(days=7, seconds=2),
    )
    assert replacement.admitted is True
    assert replacement.group_records_used == 1
    assert replacement.group_bytes_used == 500_000
    assert replacement.installation_records_used == 1
    assert replacement.installation_bytes_used == 500_000


def test_periodic_reflections_preserve_allowance_for_later_daily_windows(resource_engine):
    start = NOW.replace(hour=0) + timedelta(days=100)

    def admit(at):
        # A new governor sees the durable count on each invocation.
        return _governor(resource_engine).admit_background_reflection(
            tenant_id=TENANT, installation_id=INSTALLATION,
            principal_id="person:periodic", now=at, spread_over_day=True,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        attempts = tuple(pool.map(lambda _: admit(start), range(4)))
    assert sum(a.admitted for a in attempts) == 1
    assert {a.reason for a in attempts if not a.admitted} == {"person_daily_window_not_ready"}
    for hour in (6, 12, 18):
        early = admit(start + timedelta(hours=hour, seconds=-1))
        assert not early.admitted
        assert early.reason == "person_daily_window_not_ready"
        accepted = admit(start + timedelta(hours=hour))
        assert accepted.admitted
        assert accepted.person_used == 1 + hour // 6
    exhausted = admit(start + timedelta(hours=23))
    assert not exhausted.admitted
    assert exhausted.reason == "person_daily_limit_reached"
    next_day = admit(start + timedelta(days=1))
    assert next_day.admitted and next_day.person_used == 1


def test_periodic_reflections_share_existing_usage_without_increasing_daily_cap(resource_engine):
    start = NOW.replace(hour=0) + timedelta(days=102)
    governor = _governor(resource_engine)
    args = dict(tenant_id=TENANT, installation_id=INSTALLATION,
                principal_id="person:mixed", now=start)
    # Existing memory work retains its original admission behavior.
    assert governor.admit_background_reflection(**args).admitted
    held = governor.admit_background_reflection(**args, spread_over_day=True)
    assert not held.admitted and held.person_used == 1
    for _ in range(3):
        assert governor.admit_background_reflection(**args).admitted
    late = governor.admit_background_reflection(
        **{**args, "now": start + timedelta(hours=18)}, spread_over_day=True)
    assert not late.admitted and late.person_used == 4
    assert late.reason == "person_daily_limit_reached"
