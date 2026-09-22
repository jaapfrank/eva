"""One-snapshot v2 export, retaining the frozen v1 canonical subarchive."""
from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from eva.application.portable_archive import (
    RecordSetBytes, build_portable_record, canonical_json_bytes,
    encode_jsonl_records, publish_directory_atomically, discard_staging_tree,
)
from tools.postgres_portable_export import (
    PostgresPortableSnapshot, CANONICAL_EXPORT_SPECS, _mapping_rows,
    validate_principal_binding_evidence_scope, validate_portable_v1_attention_rows,
    validate_portable_v1_no_inflight_external_work,
)
from tools.portable_export import publish_portable_archive
from tools.portable_v2_profile import (
    ALL_SPECS, CURRENT_SPECS, ATTENTION_SPEC, DEPENDENCY_SPECS, CONTRACT,
    SCHEMA_HEAD, PROFILE, PROFILE_SHA256, EXTERNAL_REQUIREMENTS,
)
from tools.portable_v2_validation import timeline_scopes, validate_current_records


class CurrentSnapshot(PostgresPortableSnapshot):
    def __init__(self, connection, *, tenant_id, installation_id, runtime_identity):
        super().__init__(connection, tenant_id=tenant_id, installation_id=installation_id,
                         table_specs=ALL_SPECS)
        self.runtime_identity = runtime_identity
        self.scopes = timeline_scopes(tenant_id, installation_id, runtime_identity)
        self.document_owners = set()
        self.document_bindings = set()

    def _selected_principal_binding_ids(self):
        sql, params = super()._selected_principal_binding_ids()
        sql += " UNION SELECT principal_binding_id FROM eva_core.delegated_work_runs WHERE tenant_id=%s AND installation_id=%s"
        params = (*params, self._tenant_id, self._installation_id)
        if self.document_bindings:
            values = tuple(sorted(self.document_bindings))
            sql += " UNION SELECT principal_binding_id FROM eva_core.principal_bindings WHERE tenant_id=%s AND principal_binding_id IN (" + ",".join("%s" for _ in values) + ")"
            params = (*params, self._tenant_id, *values)
        return sql, params

    def _reachable_principals_clause(self):
        sql, params = super()._reachable_principals_clause()
        sql = "(" + sql + ") OR (tenant_id=%s AND principal_id IN (SELECT owner_principal_id FROM eva_core.delegated_work_runs WHERE tenant_id=%s AND installation_id=%s))"
        params = (*params, self._tenant_id, self._tenant_id, self._installation_id)
        if self.document_owners:
            values = tuple(sorted(self.document_owners))
            sql += " OR (tenant_id=%s AND principal_id IN (" + ",".join("%s" for _ in values) + "))"
            params = (*params, self._tenant_id, *values)
        return sql, params

    def validate_rows(self, result):
        canonical = {spec["table"]: result[spec["table"]] for spec in CANONICAL_EXPORT_SPECS}
        ordinary = [row for row in canonical["eva_core.attention_items"]
                    if row["kind"] != "delegated_work"]
        canonical["eva_core.attention_items"] = ordinary
        validate_principal_binding_evidence_scope(canonical)
        validate_portable_v1_attention_rows(canonical)
        validate_portable_v1_no_inflight_external_work(canonical)
        for row in result["eva_core.attention_items"]:
            if row["state"] in {"pending", "claimed"}:
                raise ValueError("current export requires settled attention")

    def read_all(self):
        canonical = (self._tenant_id, self._installation_id)
        spec = CURRENT_SPECS[0]
        timeline_rows = []
        try:
            for tenant, installation in sorted(self.scopes):
                self._execute("SELECT set_config('eva.tenant_id', %s, true), "
                              "set_config('eva.installation_id', %s, true)", (tenant, installation))
                timeline_rows.extend(_mapping_rows(self._execute(
                    "SELECT " + ", ".join(spec["database_column_order"])
                    + " FROM eva_core.operational_events WHERE tenant_id=%s AND installation_id=%s "
                    + "ORDER BY " + self._order_clause(spec), (tenant, installation)),
                    spec["database_column_order"]))
        finally:
            self._execute("SELECT set_config('eva.tenant_id', %s, true), "
                          "set_config('eva.installation_id', %s, true)", canonical)
        for event in timeline_rows:
            if event["event_type"] == "DOCUMENT_SLOT_CLAIMED":
                data = event["payload"]["data"]
                owner = data["plan"]["owner_principal_id"]
                if str(UUID(owner)) != owner:
                    raise ValueError("document claim owner is not canonical")
                self.document_owners.add(owner)
                binding = data.get("return_binding")
                if binding is not None:
                    self.document_bindings.add(str(UUID(binding["principal_binding_id"])))
        result = super().read_all()
        result[spec["table"]] = timeline_rows
        result[spec["table"]].sort(key=lambda row: tuple(str(row[key]) if key != "sequence"
            else row[key] for key in spec["primary_key"]))
        return result


def _records(spec, rows):
    return tuple(build_portable_record(table=spec["table"],
        record_contract=spec["record_contract"], primary_key_columns=spec["primary_key"],
        transform_spec=spec["transform_spec"], database_row=row) for row in rows)


def export_current_archive(connection, *, tenant_id, installation_id, runtime_identity,
                           destination: Path, created_at: str, export_sequence: int,
                           object_blob_root: Path | None = None):
    """Export settled work; target activation remains a separate installation step."""
    with connection.begin():
        revision = connection.exec_driver_sql("SELECT version_num FROM public.eva_alembic_version").scalar_one()
        if revision != SCHEMA_HEAD:
            raise ValueError("current portable source schema head differs")
    with CurrentSnapshot(connection, tenant_id=tenant_id, installation_id=installation_id,
                         runtime_identity=runtime_identity) as snapshot:
        raw = snapshot.read_all()
        # Frozen v1 requires exact legacy identity closure. Current-only identity
        # dependencies belong to the v2 extension, read under the same snapshot.
        legacy = PostgresPortableSnapshot(connection, tenant_id=tenant_id, installation_id=installation_id)
        legacy_identity = {}
        for spec in DEPENDENCY_SPECS:
            clause, params = legacy._scope_clause(spec)
            legacy_identity[spec["table"]] = _records(spec, _mapping_rows(snapshot._execute(
                "SELECT " + ", ".join(spec["database_column_order"]) + " FROM " + spec["table"]
                + " WHERE " + clause + " ORDER BY " + snapshot._order_clause(spec), params),
                spec["database_column_order"]))
        records = {spec["table"]: _records(spec, raw[spec["table"]]) for spec in ALL_SPECS}
        validate_current_records(records, tenant_id=tenant_id, installation_id=installation_id,
                                 runtime_identity=runtime_identity, created_at=created_at)
    destination = destination.absolute()
    staging = destination.parent / ("." + destination.name + ".staging-" + str(uuid4()))
    staging.mkdir(mode=0o700)
    try:
        base_sets = []
        delegated_attention = ()
        supplemental_identity = {}
        for spec in CANONICAL_EXPORT_SPECS:
            values = records[spec["table"]]
            if spec["table"] in legacy_identity:
                legacy_values = legacy_identity[spec["table"]]
                supplemental_identity[spec["table"]] = tuple(row for row in values if row not in legacy_values)
                values = legacy_values
            if spec["table"] == ATTENTION_SPEC["table"]:
                delegated_attention = tuple(row for row in values if row["row"]["kind"] == "delegated_work")
                values = tuple(row for row in values if row["row"]["kind"] != "delegated_work")
            base_sets.append(RecordSetBytes(export_ordinal=spec["export_ordinal"],
                table=spec["table"], contract=spec["record_contract"],
                relative_path=spec["record_jsonl_relative_path"],
                record_schema_relative_path=spec["record_schema_relative_path"],
                record_schema_sha256=spec["record_schema_sha256"], records=values,
                content=encode_jsonl_records(values, primary_key_columns=spec["primary_key"])))
        publish_portable_archive(record_sets=base_sets, source_installation_id=installation_id,
            export_sequence=export_sequence, created_at=created_at, destination=staging / "canonical",
            object_blob_root=object_blob_root)
        (staging / "current").mkdir(mode=0o700)
        payloads = {"current/profile.json": canonical_json_bytes(PROFILE),
            "current/attention_items.jsonl": encode_jsonl_records(delegated_attention,
                primary_key_columns=ATTENTION_SPEC["primary_key"])}
        for spec in DEPENDENCY_SPECS:
            payloads["current/" + spec["table"].split(".")[1] + ".jsonl"] = encode_jsonl_records(
                supplemental_identity[spec["table"]], primary_key_columns=spec["primary_key"])
        for spec in CURRENT_SPECS:
            payloads[spec["record_jsonl_relative_path"]] = encode_jsonl_records(
                records[spec["table"]], primary_key_columns=spec["primary_key"])
        for name, content in payloads.items():
            (staging / name).write_bytes(content)
        files = {str(path.relative_to(staging)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(staging.rglob("*")) if path.is_file()}
        manifest = {"contract": CONTRACT, "schema_version": 2,
            "archive_id": str(uuid4()), "source_tenant_id": tenant_id,
            "source_installation_id": installation_id, "created_at": created_at,
            "runtime_identity": runtime_identity, "schema_head": SCHEMA_HEAD,
            "profile_sha256": PROFILE_SHA256, "files": files,
            "external_requirements": list(EXTERNAL_REQUIREMENTS), "target_activation": "closed"}
        (staging / "archive.json").write_bytes(canonical_json_bytes(manifest))
        from tools.portable_v2_reader import read_current_archive
        publish_directory_atomically(staging=staging, destination=destination,
                                     validator=read_current_archive)
        return manifest
    except BaseException:
        discard_staging_tree(staging)
        raise
