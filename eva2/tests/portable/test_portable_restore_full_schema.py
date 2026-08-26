"""One real fresh schema can admit a complete, valid empty portable archive."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from eva.adapters.postgres.portable_export import CANONICAL_EXPORT_SPECS
from eva.operations.install_schema import install_schema
from eva.operations.portable_restore import restore_column_preserving_records


ROOT = Path(__file__).resolve().parents[2]
POSTGRES_DSN = os.getenv("EVA2_TEST_POSTGRES_DSN")


@pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="EVA2_TEST_POSTGRES_DSN is required for the real full restore gate",
)
def test_fresh_full_schema_admits_the_complete_empty_portable_archive() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    receipt = install_schema(POSTGRES_DSN, alembic_ini=ROOT / "alembic.ini")
    assert receipt.final_revision == "4e_0004_delegated_work_stop"
    records = {str(spec["table"]): [] for spec in CANONICAL_EXPORT_SPECS}
    engine = sqlalchemy.create_engine(POSTGRES_DSN, pool_pre_ping=False)
    try:
        connection = engine.connect().execution_options(isolation_level="SERIALIZABLE")
        try:
            transaction = connection.begin()
            try:
                assert (
                    restore_column_preserving_records(
                        connection, records, table_specs=CANONICAL_EXPORT_SPECS
                    )
                    == 0
                )
            finally:
                transaction.rollback()
        finally:
            connection.close()
    finally:
        engine.dispose()
