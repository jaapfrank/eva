from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.postgres.foreign_key_catalog import (
    ForeignKeyCatalogDrift,
    require_current_foreign_key_catalog,
)
from eva.operations.install_schema import install_schema
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]


def _revision(engine: sa.Engine) -> str:
    with engine.connect() as connection:
        value = connection.scalar(
            sa.text("SELECT version_num FROM public.eva_alembic_version")
        )
    assert isinstance(value, str)
    return value


def _kind_check(engine: sa.Engine) -> str:
    with engine.connect() as connection:
        value = connection.scalar(
            sa.text(
                "SELECT pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c "
                "JOIN pg_class t ON t.oid=c.conrelid "
                "JOIN pg_namespace n ON n.oid=t.relnamespace "
                "WHERE n.nspname='eva_core' AND t.relname='attention_items' "
                "AND c.conname='ck_attention_items_kind'"
            )
        )
    assert isinstance(value, str)
    return value


def _insert_attention(
    engine: sa.Engine,
    *,
    label: str,
    kind: str,
) -> str:
    def identifier(part: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"eva2-delegated-migration-{label}-{part}"))
    attention_id = identifier("attention")
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    values = {
        "tenant_id": identifier("tenant"),
        "installation_id": identifier("installation"),
        "attention_id": attention_id,
        "episode_id": identifier("episode"),
        "generation": 1,
        "commitment_id": None,
        "kind": kind,
        "state": "pending",
        "reason": "migrationbewijs",
        "not_before": now,
        "deadline": now + timedelta(minutes=5),
        "lease_owner": None,
        "lease_expires_at": None,
        "ordinal": 1,
        "payload": {"contract": "AttentionWake/v1", "fixture": label},
        "payload_sha256": bytes.fromhex("11" * 32),
        "revision": 1,
        "retention_policy_id": "retention:test",
        "retention_until": now + timedelta(days=1),
        "created_at": now,
        "updated_at": now,
    }
    attention_items = metadata.tables["eva_core.attention_items"]
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE eva_core.attention_items DISABLE TRIGGER ALL"
        )
        connection.execute(attention_items.insert().values(**values))
        connection.exec_driver_sql(
            "ALTER TABLE eva_core.attention_items ENABLE TRIGGER ALL"
        )
    return attention_id


def _row(engine: sa.Engine, attention_id: str) -> dict[str, object]:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                sa.text(
                    "SELECT * FROM eva_core.attention_items "
                    "WHERE attention_id=:attention_id"
                ),
                {"attention_id": attention_id},
            )
            .mappings()
            .one()
        )


def test_delegated_work_kind_upgrade_and_atomic_downgrade() -> None:
    dsn = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.fail("EVA2_TEST_POSTGRES_DSN is required")
    url = make_url(dsn)
    if not url.database or not url.database.startswith("eva2_4b_disposable_"):
        pytest.fail("delegated migration requires an explicit disposable database")

    engine = sa.create_engine(url)
    config = Config(str(ROOT / "deploy/alembic.ini"))
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    try:
        receipt = install_schema(dsn, alembic_ini=ROOT / "deploy/alembic.ini")
        assert receipt.final_revision == "4e_0007_delegated_private"
        assert (
            receipt.drift_check
            == "no_new_upgrade_operations_and_exact_foreign_keys"
        )
        with engine.connect() as connection:
            require_current_foreign_key_catalog(connection)
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    sa.text(
                        "ALTER TABLE eva_core.episodes "
                        "DROP CONSTRAINT fk_episodes_last_event"
                    )
                )
                with pytest.raises(
                    ForeignKeyCatalogDrift,
                    match="fk_episodes_last_event",
                ):
                    require_current_foreign_key_catalog(connection)
            finally:
                transaction.rollback()
        command.downgrade(config, "4e_0001_operational_timeline")
        assert _revision(engine) == "4e_0001_operational_timeline"
        assert "delegated_work" not in _kind_check(engine)

        old_id = _insert_attention(engine, label="old", kind="continuation")
        old_row = _row(engine, old_id)

        command.upgrade(config, "head")
        assert _revision(engine) == "4e_0007_delegated_private"
        assert "delegated_work" in _kind_check(engine)
        assert _row(engine, old_id) == old_row

        delegated_id = _insert_attention(
            engine, label="delegated", kind="delegated_work"
        )
        with pytest.raises(sa.exc.IntegrityError):
            _insert_attention(engine, label="unknown", kind="unknown")

        with pytest.raises(sa.exc.IntegrityError):
            command.downgrade(config, "4e_0001_operational_timeline")
        assert _revision(engine) == "4e_0007_delegated_private"
        assert _row(engine, delegated_id)["kind"] == "delegated_work"

        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "DELETE FROM eva_core.attention_items "
                    "WHERE attention_id=:attention_id"
                ),
                {"attention_id": delegated_id},
            )
        command.downgrade(config, "4e_0001_operational_timeline")
        assert _revision(engine) == "4e_0001_operational_timeline"
        assert "delegated_work" not in _kind_check(engine)
        assert _row(engine, old_id) == old_row

        command.upgrade(config, "head")
        with engine.connect() as connection:
            require_current_foreign_key_catalog(connection)
    finally:
        try:
            command.downgrade(config, "base")
        finally:
            engine.dispose()
