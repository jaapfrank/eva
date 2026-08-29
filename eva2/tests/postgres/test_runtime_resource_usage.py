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
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.application.memory_audience import (
    MemoryAudience,
    TelegramGroupConsentReference,
)
from eva.contracts.canonical_json import canonical_json_bytes
from eva.contracts.runtime_budget import RuntimeResourceBudget


EVA2_ROOT = Path(__file__).resolve().parents[2]
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
        source_budget_sha256=hashlib.sha256(
            canonical_json_bytes(payload)
        ).hexdigest(),
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
        source_budget_sha256=hashlib.sha256(
            canonical_json_bytes(payload)
        ).hexdigest(),
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
        source_budget_sha256=hashlib.sha256(
            canonical_json_bytes(payload)
        ).hexdigest(),
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
                    _document(
                        f"memory:race:final:{index}", principal_id="person:race"
                    ),
                    now=NOW + timedelta(seconds=200 + index),
                ),
                range(8),
            )
        )
    assert sum(item.admitted for item in raced) == 1
    assert {item.reason for item in raced if not item.admitted} == {
        "person_record_limit_reached"
    }

    retained_document = replace(
        large, retention_until=admitted.retention_until
    )
    receipt = MemoryRetentionReceipt(
        bank_id="eva2-" + "a" * 32,
        document_id=large.document_id,
        content_sha256=hashlib.sha256(large.content.encode("utf-8")).hexdigest(),
    )
    storage.mark_retained(
        retained_document, receipt, now=NOW + timedelta(seconds=300)
    )
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
    second_claim = restarted.claim_expired(
        now=NOW + timedelta(days=7, seconds=1)
    )
    assert len(second_claim) == 1
    assert second_claim[0].state.retry_count == 2
    restarted.mark_removed(
        second_claim[0], now=NOW + timedelta(days=7, seconds=2)
    )

    replacement = restarted.admit_memory(
        _document("memory:replacement", content="c" * 500_000),
        now=NOW + timedelta(days=7, seconds=3),
    )
    assert replacement.admitted is True
    assert replacement.person_records_used == 1
    assert replacement.person_bytes_used == 500_000


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
            content_sha256=hashlib.sha256(
                document.content.encode("utf-8")
            ).hexdigest(),
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
