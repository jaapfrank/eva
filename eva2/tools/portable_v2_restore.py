"""Atomic current-work restore using existing row/CAS/authority boundaries."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from uuid import UUID
from graphlib import TopologicalSorter

from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.postgres.operational_timeline import operational_checkpoints
from eva.operations.portable_restore_authority import authorize_portable_restore
from tools.portable_restore import restore_column_preserving_records, verify_empty_restore_target
from tools.portable_v2_profile import ALL_SPECS
from tools.portable_v2_reader import read_current_archive
from tools.portable_object_materializer import materialize_verified_portable_objects


def restore_specs():
    specs = {spec["table"]: spec for spec in ALL_SPECS}
    dependencies = {}
    for name in specs:
        table = metadata.tables.get(name)
        dependencies[name] = ([] if table is None else [fk.referred_table.fullname
            for fk in table.foreign_key_constraints if not fk.deferrable
            and fk.referred_table.fullname != name and fk.referred_table.fullname in specs])
    return tuple(specs[name] for name in TopologicalSorter(dependencies).static_order())


def verify_current_target(connection):
    verify_empty_restore_target(connection, table_specs=ALL_SPECS)
    if connection.exec_driver_sql("SELECT COUNT(*) FROM eva_core.operational_checkpoints").scalar_one():
        raise ValueError("current restore checkpoint target is not empty")
    from tools.portable_v2_profile import SCHEMA_HEAD
    if connection.exec_driver_sql("SELECT version_num FROM public.eva_alembic_version").scalar_one() != SCHEMA_HEAD:
        raise ValueError("current restore target schema differs")


def restore_current_archive(connection, directory: Path, *, authority_reader,
                            expected_runtime_identity, object_store_root: Path,
                            key_resolver=None):
    """Caller holds a traffic-closed target transaction; no worker is started."""
    archive = read_current_archive(directory)
    manifest, raw = archive["manifest"], archive["manifest_raw"]
    if manifest["runtime_identity"] != expected_runtime_identity:
        raise ValueError("archive runtime identity differs from trusted installation configuration")
    authorize_portable_restore(archive_manifest_raw=raw,
                               restore_authority_raw=authority_reader())
    records = archive["records"]
    with connection.begin_nested():
        verify_current_target(connection)
        connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
        materialized = ()
        if records["eva_evidence.object_refs"]:
            if key_resolver is None:
                raise ValueError("separately provisioned object keys required")
            materialized = materialize_verified_portable_objects(
                source_object_root=directory / "canonical" / "objects",
                target_object_root=object_store_root, records_by_table=records,
                key_resolver=key_resolver)
        inserted = restore_column_preserving_records(connection, records,
            archive_manifest_raw=raw, restore_authority_raw=authority_reader(),
            table_specs=restore_specs(), materialized_objects=materialized,
            object_store_root=object_store_root)
        for (tenant, installation, thread), state in archive["projections"].items():
            values = asdict(state)
            values.update(tenant_id=UUID(tenant), installation_id=UUID(installation))
            values["last_error_fingerprint"] = (None if state.last_error_fingerprint is None
                                                else bytes.fromhex(state.last_error_fingerprint))
            connection.execute(operational_checkpoints.insert().values(**values))
        # Forgetting authorization is read freshly after all materialization and
        # inserts, still inside the rollback-capable caller transaction.
        authorize_portable_restore(archive_manifest_raw=raw,
                                   restore_authority_raw=authority_reader())
    return {"status": "restored", "inserted_records": inserted,
            "rebuilt_checkpoints": len(archive["projections"]),
            "target_activation": "closed", "object_store_disposition": "private_inactive_preserved",
            "external_requirements": manifest["external_requirements"],
            "archive_sha256": archive["archive_sha256"]}
