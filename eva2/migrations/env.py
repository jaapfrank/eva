"""The only Alembic composition root for Eva-owned PostgreSQL schemas."""

from __future__ import annotations

from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from eva.adapters.postgres.schema import metadata


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata
VERSION_TABLE = "eva_alembic_version"
MIGRATION_LOCK_NAMESPACE = 1_164_409_906
MIGRATION_LOCK_KEY = 4


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url").strip()
    if not url:
        raise RuntimeError("Alembic requires an explicit PostgreSQL URL")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table=VERSION_TABLE,
        transaction_per_migration=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_online_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table=VERSION_TABLE,
        transaction_per_migration=False,
    )
    with context.begin_transaction():
        connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(:namespace, :lock_key)"),
            {
                "namespace": MIGRATION_LOCK_NAMESPACE,
                "lock_key": MIGRATION_LOCK_KEY,
            },
        )
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        if not isinstance(supplied_connection, Connection):
            raise TypeError("Alembic connection attribute must be a Connection")
        _run_online_migrations(supplied_connection)
        return

    engine = sa.create_engine(_database_url(), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            _run_online_migrations(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
