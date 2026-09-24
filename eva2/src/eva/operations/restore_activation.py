"""Database-local restore hold shared by core and both worker entrypoints.

The row is created by the trusted restore transaction, never imported. Normal
runtime roles can read it but cannot release it. This is a startup barrier for
an isolated, stopped restore target, not a pause mechanism for running services.
"""
from __future__ import annotations

from uuid import uuid4
import sqlalchemy as sa


# Matches the retained v2 archive requirements without importing operator tools.
_RELEASE_REQUIREMENTS = (
    "native_document_artifacts_and_journals_require_separate_verified_relocation",
    "hindsight_semantic_banks_require_separate_verified_restore_or_rebuild",
    "credentials_and_provider_bindings_require_fresh_authorization",
    "target_traffic_remains_closed_until_external_dependencies_are_reconciled",
)


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


def release_restored_database(connection, *, restore_id, package_manifest_raw,
                              core_manifest_raw, authority_reader, runtime_identity,
                              runtime_guard, verify_components, now):
    """Release one restore hold in the caller's uncommitted root transaction.

    Both callbacks are trusted synchronous operator composition, not evidence
    submitted through HTTP or an archive. The component verifier must freshly
    check writable relocated document targets and retained history, Hindsight
    banks, current credential/provider authorization, source fencing and closed
    target traffic, and bind its receipt to this target. Package hashes alone
    are insufficient. No external JSON receipt may replace this callback.
    Hashes identify that coverage; they are not a substitute for verification.
    The caller MUST roll back the entire transaction on any exception, even if
    caught. No runtime is started here and return does not imply a committed
    release. Callbacks must not commit, roll back or replace the transaction.
    """
    import hashlib
    import json
    from datetime import datetime
    from eva.contracts.canonical_json import canonical_sha256
    from eva.operations.portable_restore_authority import authorize_installation_recovery

    if not connection.in_transaction() or connection.in_nested_transaction():
        raise ValueError("restore activation requires a root transaction")
    if not all(callable(item) for item in (authority_reader, runtime_guard, verify_components)):
        raise ValueError("restore activation requires trusted verification callbacks")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("restore activation requires an aware timestamp")
    transaction = connection.get_transaction()

    def require_transaction():
        if (connection.get_transaction() is not transaction or not transaction.is_active
                or connection.in_nested_transaction()):
            raise ValueError("restore activation transaction changed")

    hold = connection.execute(sa.text("""
        SELECT *, target_database_oid = (SELECT oid FROM pg_database
            WHERE datname=current_database()) AS same_database
        FROM eva_core.restore_activation FOR UPDATE
    """)).mappings().one_or_none()
    if (hold is None or str(hold["restore_id"]) != str(restore_id)
            or hold["held"] is not True or not hold["same_database"]):
        raise RestoreActivationRequired("Activation requires the matching held restore")

    first, _ = authorize_installation_recovery(
        package_manifest_raw=package_manifest_raw, core_manifest_raw=core_manifest_raw,
        restore_authority_raw=authority_reader())
    package = json.loads(package_manifest_raw)
    core = json.loads(core_manifest_raw)
    if core.get("external_requirements") != list(_RELEASE_REQUIREMENTS):
        raise ValueError("activation external requirements differ")
    archive_hash = hashlib.sha256(package_manifest_raw).hexdigest()
    if (hold["archive_sha256"] != archive_hash
            or str(hold["tenant_id"]) != package["source_tenant_id"]
            or str(hold["installation_id"]) != package["source_installation_id"]
            or not isinstance(runtime_identity, dict)
            or set(runtime_identity) != {"tenant_id", "installation_id"}
            or package["runtime_identity"] != runtime_identity
            or str(hold["runtime_tenant_id"]) != runtime_identity["tenant_id"]
            or str(hold["runtime_installation_id"]) != runtime_identity["installation_id"]):
        raise RestoreActivationRequired("Activation archive or target identity differs")
    components = package.get("source_components")
    if not isinstance(components, dict) or set(components) != {"memory", "gateway", "documents"}:
        raise ValueError("activation external component requirements differ")
    expected = {
        "contract": "EvaRestoreActivationComponents/v1",
        "restore_id": str(restore_id), "archive_sha256": archive_hash,
        "target_database_oid": int(hold["target_database_oid"]),
        "verified_requirements": list(_RELEASE_REQUIREMENTS),
        "verified_components": {name: canonical_sha256(components[name]) for name in components},
    }
    require_transaction()
    runtime_guard(connection)
    require_transaction()
    verified = verify_components(connection)
    require_transaction()
    if (not isinstance(verified, dict) or set(verified) != set(expected)
            or type(verified.get("target_database_oid")) is not int or verified != expected):
        raise RestoreActivationRequired("Activation component verification receipt differs")
    runtime_guard(connection)
    require_transaction()
    latest, _ = authorize_installation_recovery(
        package_manifest_raw=package_manifest_raw, core_manifest_raw=core_manifest_raw,
        restore_authority_raw=authority_reader())
    if latest.authority_sha256 != first.authority_sha256:
        raise RestoreActivationRequired("Restore authority changed during activation")
    require_transaction()
    closed = close_restored_voice_sessions(connection, restore_id=restore_id, now=now)
    changed = connection.execute(sa.text("""
        UPDATE eva_core.restore_activation
        SET held=false, release_authority_sha256=:authority
        WHERE restore_id=:restore_id AND held=true AND archive_sha256=:archive
          AND target_database_oid=(SELECT oid FROM pg_database WHERE datname=current_database())
    """), {"authority": latest.authority_sha256, "restore_id": str(restore_id), "archive": archive_hash})
    if changed.rowcount != 1:
        raise RestoreActivationRequired("Restore hold changed before release; roll back activation")
    return {"contract": "EvaRestoreActivationRelease/v1", "restore_id": str(restore_id),
            "archive_sha256": archive_hash, "target_database_oid": expected["target_database_oid"],
            "status": "released_pending_commit", "closed_voice_sessions": closed,
            "release_authority_sha256": latest.authority_sha256,
            "components_receipt_sha256": canonical_sha256(verified), "runtime_started": False}
