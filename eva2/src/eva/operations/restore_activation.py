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


def close_restored_voice_sessions(connection, *, restore_id, now):
    """Close copied listening contexts before operator activation, never release.

    The caller owns the root activation transaction. Closure and eventual hold
    release must commit together; a failed activation rolls these events back.
    Historical events, work and delivery fences are retained.
    """
    from eva.adapters.postgres.operational_timeline import (
        TimelineScope, operational_checkpoints as checkpoints,
        operational_events as events, transition_timeline_thread,
    )
    if not connection.in_transaction() or connection.in_nested_transaction():
        raise ValueError("voice restore preparation requires a root transaction")
    hold = connection.execute(sa.text("""
        SELECT restore_id, held, target_database_oid =
            (SELECT oid FROM pg_database WHERE datname=current_database()) AS same_database
        FROM eva_core.restore_activation FOR UPDATE
    """)).mappings().one_or_none()
    if (hold is None or str(hold["restore_id"]) != str(restore_id)
            or not hold["held"] or not hold["same_database"]):
        raise RestoreActivationRequired("Voice restore preparation requires the matching held restore")
    kinds = {
        "PRIVATE_RETURN_SESSION_OPENED": ("private-return-session:", "PRIVATE_RETURN_SESSION_CLOSED"),
        "HOUSEHOLD_WORK_SESSION_OPENED": ("household-work-session:", "HOUSEHOLD_WORK_SESSION_CLOSED"),
    }
    rows = connection.execute(sa.select(checkpoints, events.c.event_type).join(events,
        sa.and_(events.c.tenant_id == checkpoints.c.tenant_id,
                events.c.installation_id == checkpoints.c.installation_id,
                events.c.thread_id == checkpoints.c.thread_id,
                events.c.sequence == 1)).where(
                    checkpoints.c.phase == "open",
                    events.c.event_type.in_(tuple(kinds))).with_for_update(of=checkpoints)
    ).mappings().all()
    for row in rows:
        prefix, close_event = kinds[row["event_type"]]
        if not row["thread_id"].startswith(prefix):
            raise ValueError("restored voice session identity differs")
        transition_timeline_thread(connection,
            TimelineScope(row["tenant_id"], row["installation_id"]),
            thread_id=row["thread_id"], expected_revision=row["revision"],
            checkpoint_id=row["thread_id"]+":closed", occurred_at=now,
            event_type=close_event, phase="closed", status="completed",
            data={"reason": "installation_restored", "restore_id": str(restore_id)})
    return len(rows)
