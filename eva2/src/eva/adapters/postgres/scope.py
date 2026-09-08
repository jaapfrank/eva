"""Fail-closed PostgreSQL transaction-local tenant context."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from eva.application.episode_repository import (
    TransactionRequired,
    TransactionScopeMismatch,
)


class _ScopedIds(Protocol):
    tenant_id: UUID
    installation_id: UUID


class PostgresScopedRole:
    """Share the fail-closed role and tenant setup used by PostgreSQL stores."""

    _role: str
    _scope_value: _ScopedIds

    def _scope(self, connection: Connection) -> None:
        connection.exec_driver_sql(f"SET LOCAL ROLE {self._role}")
        set_local_scope(
            connection,
            tenant_id=str(self._scope_value.tenant_id),
            installation_id=str(self._scope_value.installation_id),
        )


def set_local_scope(
    connection: Connection, *, tenant_id: str, installation_id: str
) -> None:
    """Set both required GUCs on an already active caller transaction."""

    if not connection.in_transaction() or connection.in_nested_transaction():
        raise TransactionRequired()

    canonical_tenant = str(UUID(tenant_id))
    canonical_installation = str(UUID(installation_id))
    if canonical_tenant != tenant_id or canonical_installation != installation_id:
        raise ValueError("scope IDs must use canonical lowercase UUID text")

    current = connection.execute(
        sa.text(
            "SELECT NULLIF(current_setting('eva.tenant_id', true), ''), "
            "NULLIF(current_setting('eva.installation_id', true), '')"
        )
    ).one()
    current_scope = tuple(current)
    desired_scope = (tenant_id, installation_id)
    if current_scope != (None, None):
        if current_scope != desired_scope:
            raise TransactionScopeMismatch()
        return

    configured = connection.execute(
        sa.text(
            "SELECT set_config('eva.tenant_id', :tenant_id, true), "
            "set_config('eva.installation_id', :installation_id, true)"
        ),
        {"tenant_id": tenant_id, "installation_id": installation_id},
    ).one()
    if tuple(configured) != desired_scope:
        raise TransactionScopeMismatch()


@contextmanager
def try_execution_lock(engine: sa.Engine, key: str) -> Iterator[bool]:
    """Own a session lock across separate transactions, without waiting on work.

    Dispatch/model work runs outside this connection. Losing the DB session
    releases the lock; external effects still need idempotence and readback.
    """
    # Recreate the configured pool so connection factories and connect hooks are
    # retained, while long-lived locks cannot consume the work pool's capacity.
    lock_engine = sa.Engine(engine.pool.recreate(), engine.dialect, engine.url)
    try:
        with lock_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            acquired = bool(connection.scalar(sa.text(
                "SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"
            ), {"key": key}))
            if not acquired:
                yield False
                return
            try:
                yield True
            finally:
                try:
                    connection.execute(sa.text(
                        "SELECT pg_advisory_unlock(hashtextextended(:key, 0))"
                    ), {"key": key})
                except BaseException:
                    # A session that may still own the lock must not reenter the pool.
                    connection.invalidate()
                    raise
    finally:
        lock_engine.dispose()
