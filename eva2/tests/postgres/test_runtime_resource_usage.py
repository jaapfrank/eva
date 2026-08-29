from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
