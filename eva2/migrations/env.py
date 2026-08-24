"""The only Alembic composition root for Eva-owned PostgreSQL schemas."""

from __future__ import annotations

from logging.config import fileConfig
import re

import sqlalchemy as sa
from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import living_metadata
from eva.operations.migration_admission import (
    InitialCatalogAdmissionError,
    run_initial_catalog_admission,
)


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = (metadata, living_metadata)
VERSION_TABLE = "eva_alembic_version"
MIGRATION_LOCK_NAMESPACE = 1_164_409_906
MIGRATION_LOCK_KEY = 4
_RELATIVE_REVISION = re.compile(r"(?:(.+?)@)?(\w+)?((?:\+|-)\d+)$")


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url").strip()
    if not url:
        raise RuntimeError("Alembic requires an explicit PostgreSQL URL")
    return url


def _one_revision(value: str | tuple[str, ...] | None) -> str | None:
    if isinstance(value, tuple):
        if len(value) != 1:
            raise RuntimeError("eva4d_offline_upgrade_unsupported")
        return value[0]
    return value


def _resolve_offline_position(
    script: ScriptDirectory,
    ordered_revisions: tuple[str, ...],
    value: str | tuple[str, ...] | None,
    *,
    current_position: int,
) -> int:
    revision = _one_revision(value)
    if revision is None or revision == "base":
        return -1

    relative = _RELATIVE_REVISION.fullmatch(revision)
    if relative is not None:
        branch, symbol, offset_text = relative.groups()
        if branch is not None:
            anchored = f"{branch}@{symbol}" if symbol is not None else branch
            anchor_revision = script.get_revision(anchored)
            anchor_position = ordered_revisions.index(anchor_revision.revision)
        elif symbol is not None:
            anchor_revision = script.get_revision(symbol)
            anchor_position = ordered_revisions.index(anchor_revision.revision)
        else:
            anchor_position = current_position
        return anchor_position + int(offset_text)

    resolved = _one_revision(script.as_revision_number(revision))
    if resolved is None:
        return -1
    exact = script.get_revision(resolved)
    return ordered_revisions.index(exact.revision)


def _offline_route_contains_4d() -> bool:
    script = ScriptDirectory.from_config(config)
    ordered_revisions = tuple(
        revision.revision for revision in reversed(tuple(script.walk_revisions()))
    )
    starting = context.get_starting_revision_argument()
    start_position = _resolve_offline_position(
        script,
        ordered_revisions,
        starting,
        current_position=-1,
    )
    destination = context.get_revision_argument()
    destination_position = _resolve_offline_position(
        script,
        ordered_revisions,
        destination,
        current_position=start_position,
    )
    lower = min(start_position, destination_position)
    upper = max(start_position, destination_position)
    return any(
        revision.startswith("4d_")
        for revision in ordered_revisions[max(lower, 0) : upper + 1]
    )


def run_migrations_offline() -> None:
    if _offline_route_contains_4d():
        raise RuntimeError("eva4d_offline_upgrade_unsupported")
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


def _run_online_migrations(
    connection: Connection, *, admission_holds_migration_lock: bool = False
) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table=VERSION_TABLE,
        transaction_per_migration=False,
    )
    with context.begin_transaction():
        if not admission_holds_migration_lock:
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(:namespace, :lock_key)"),
                {
                    "namespace": MIGRATION_LOCK_NAMESPACE,
                    "lock_key": MIGRATION_LOCK_KEY,
                },
            )
        context.run_migrations()


def _current_revision(connection: Connection) -> str | None:
    ledger_exists = bool(
        connection.scalar(
            sa.text("SELECT to_regclass('public.eva_alembic_version') IS NOT NULL")
        )
    )
    if not ledger_exists:
        return None
    return connection.scalar(
        sa.text("SELECT version_num FROM public.eva_alembic_version")
    )


def _revision_before_transaction(connection: Connection) -> str | None:
    """Read only routing state, then end that transaction before admission."""

    revision = _current_revision(connection)
    connection.rollback()
    return revision


def _run_clean_connection(connection: Connection) -> None:
    source_revision = _revision_before_transaction(connection)
    if source_revision != "4b_0002_episode_atomicity":
        _run_online_migrations(connection)
        return

    with connection.begin():
        decision = run_initial_catalog_admission(connection)
        config.attributes["eva4d_initial_admission_complete"] = True
        config.attributes["eva4d_initial_admission_decision"] = decision
        try:
            _run_online_migrations(connection, admission_holds_migration_lock=True)
        finally:
            config.attributes.pop("eva4d_initial_admission_complete", None)


def _run_supplied_connection(connection: Connection) -> None:
    if not connection.in_transaction():
        _run_clean_connection(connection)
        return

    source_revision = _current_revision(connection)
    if source_revision == "4b_0002_episode_atomicity":
        raise InitialCatalogAdmissionError("eva4d_initial_admission_unprovable")
    _run_online_migrations(connection)


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        if not isinstance(supplied_connection, Connection):
            raise TypeError("Alembic connection attribute must be a Connection")
        _run_supplied_connection(supplied_connection)
        return

    engine = sa.create_engine(_database_url(), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            _run_clean_connection(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
