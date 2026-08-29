from __future__ import annotations

from argparse import Namespace
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Iterator

from alembic import command as alembic_command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine, make_url

from tests.support.noor_4d_repository import PostgresNoor4DRepository
from tools.legacy_memory_trial import LegacyMemoryTrialError
from tools.run_legacy_memory_trial import run_trial
from tests.postgres import test_memory_repository as memory_fixture


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def trial_engine() -> Iterator[Engine]:
    dsn = os.environ.get("EVA2_LEGACY_MEMORY_TRIAL_DSN", "").strip()
    if not dsn:
        pytest.fail("EVA2_LEGACY_MEMORY_TRIAL_DSN is required")
    url = make_url(dsn)
    if (
        url.get_backend_name() != "postgresql"
        or not url.database
        or not url.database.startswith("eva2_legacy_memory_trial_")
    ):
        pytest.fail("legacy memory tool test requires a disposable trial database")
    engine = sa.create_engine(url, pool_pre_ping=False)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    try:
        alembic_command.upgrade(config, "4b_0000_core_namespace")
        with engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            config.attributes["connection"] = connection
            try:
                alembic_command.upgrade(config, "head")
            finally:
                config.attributes.pop("connection", None)
        yield engine
    finally:
        try:
            alembic_command.downgrade(config, "base")
        finally:
            engine.dispose()


def test_tool_imports_confirmed_slice_as_restricted_and_not_recallable(
    trial_engine: Engine,
) -> None:
    tenant_id, installation_id, principal_id, evidence_id = memory_fixture._scope()
    with trial_engine.begin() as connection:
        connection.execute(sa.text("SET CONSTRAINTS ALL DEFERRED"))
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        repository = PostgresNoor4DRepository(connection, tenant_id, installation_id)
        for fact_id in memory_fixture.BASE_FACTS:
            assert repository.upsert_exact(
                memory_fixture._exact_fact(fact_id)
            ).disposition == "inserted"

    temporary = Path(tempfile.mkdtemp(prefix="eva2-legacy-memory-tool-", dir="/tmp"))
    try:
        source = temporary / "legacy.db"
        connection = sqlite3.connect(source)
        try:
            connection.execute(
                "CREATE TABLE memories (id INTEGER, ts TEXT, person TEXT, "
                "content TEXT, category TEXT, archived INTEGER)"
            )
            connection.execute(
                "INSERT INTO memories VALUES "
                "(7, '2026-08-01T12:00:00Z', 'owner', "
                "'private test memory', 'preference', 0)"
            )
            connection.commit()
        finally:
            connection.close()
        label = temporary / "label"
        label.write_text("owner\n", encoding="utf-8")
        label.chmod(0o600)
        confirmation = temporary / "confirmation"
        confirmation.write_text("CONFIRM_LEGACY_MEMORY_TRIAL_V1\n", encoding="utf-8")
        confirmation.chmod(0o600)

        receipt = run_trial(
            Namespace(
                source_sqlite=source,
                person_label_file=label,
                confirmation_file=confirmation,
                trial_dsn=os.environ["EVA2_LEGACY_MEMORY_TRIAL_DSN"],
                tenant_id=tenant_id,
                installation_id=installation_id,
                target_principal_id=principal_id,
                provenance_evidence_id=evidence_id,
                limit=25,
                retention_days=7,
            )
        )
        with pytest.raises(
            LegacyMemoryTrialError,
            match="target already contains memory authority data",
        ):
            run_trial(
                Namespace(
                    source_sqlite=source,
                    person_label_file=label,
                    confirmation_file=confirmation,
                    trial_dsn=os.environ["EVA2_LEGACY_MEMORY_TRIAL_DSN"],
                    tenant_id=tenant_id,
                    installation_id=installation_id,
                    target_principal_id=principal_id,
                    provenance_evidence_id=evidence_id,
                    limit=25,
                    retention_days=7,
                )
            )
    finally:
        shutil.rmtree(temporary)

    assert receipt["imported_count"] == 1
    assert receipt["ordinary_recall_count"] == 0
    assert receipt["trust"] == "legacy_unverified"
    assert receipt["privacy_class"] == "restricted"
    with trial_engine.begin() as connection:
        stored = connection.execute(
            sa.text("SELECT source_kind, trust, privacy_class FROM eva_memory.source_events")
        ).one()
    assert stored == ("legacy_unverified", "legacy_unverified", "restricted")
