"""Database-local restore hold shared by core and both worker entrypoints.

The row is created by the trusted restore transaction, never imported. Normal
runtime roles can read it but cannot release it. This is a startup barrier for
an isolated, stopped restore target, not a pause mechanism for running services.
"""
from __future__ import annotations

from uuid import uuid4
import sqlalchemy as sa


class RestoreActivationRequired(RuntimeError):
    """Restored state is not yet authorized for runtime execution."""


def hold_restored_database(connection, *, archive_sha256, tenant_id, installation_id,
                           runtime_identity):
    restore_id = str(uuid4())
    connection.execute(sa.text("""
        INSERT INTO eva_core.restore_activation
            (restore_id, archive_sha256, tenant_id, installation_id,
             runtime_tenant_id, runtime_installation_id, target_database_oid)
        VALUES (:restore_id, :archive_sha256, :tenant_id, :installation_id,
                :runtime_tenant_id, :runtime_installation_id,
                (SELECT oid FROM pg_database WHERE datname=current_database()))
    """), dict(restore_id=restore_id, archive_sha256=archive_sha256,
                tenant_id=tenant_id, installation_id=installation_id,
                runtime_tenant_id=runtime_identity["tenant_id"],
                runtime_installation_id=runtime_identity["installation_id"]))
    return restore_id


def require_restore_activation(engine):
    """Fail closed on a hold, invalid lineage, missing schema or read error."""
    try:
        with engine.connect() as connection:
            rows = connection.execute(sa.text("""
                SELECT held, release_authority_sha256,
                       target_database_oid = (SELECT oid FROM pg_database
                           WHERE datname=current_database()) AS lineage_matches
                FROM eva_core.restore_activation
            """)).mappings().all()
        if any(row["held"] or not row["lineage_matches"]
               or not row["release_authority_sha256"] for row in rows):
            raise RestoreActivationRequired("Restored Eva requires verified operator activation")
    except RestoreActivationRequired:
        raise
    except Exception as error:
        raise RestoreActivationRequired("Eva restore activation could not be verified") from error
