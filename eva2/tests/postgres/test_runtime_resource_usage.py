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
from eva.application.memory_audience import MemoryAudience
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
        "contract": "EvaInstallerBudget/v1",
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": 12_000,
        "max_output_tokens": 1_200,
        "memory_bytes_per_person": 20_971_520,
        "memory_records_per_person": 2_000,
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
        retention_days=7,
        background_reflections_per_person_day=4,
        background_reflections_per_installation_day=6,
        external_monthly_spend_cap_eur_cents=0,
    )


def _document(
    document_id: str, *, principal_id: str = "person:memory", content: str = "x"
) -> ConversationMemoryDocument:
    return ConversationMemoryDocument(
        tenant_id=TENANT,
        installation_id=INSTALLATION,
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
