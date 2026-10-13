"""Single-snapshot PostgreSQL source for the framework-independent archive."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from eva.adapters.postgres.catalog_generated import CATALOG_SPEC
from eva.application.portable_archive import (
    PortableArchiveError,
    RecordSetBytes,
    build_portable_record,
    encode_jsonl_records,
)


SNAPSHOT_BEGIN_SQL = (
    "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY DEFERRABLE"
)
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_PORTABLE_V1_ATTENTION_KINDS = {
    "ingress",
    "continuation",
    "deadline",
    "external_signal",
    "delivery_recovery",
}
# A portable archive is a recovery artefact, not a way to hand a second Eva a
# live external call. These states mean that a provider call/delivery could
# still be attempted by a scheduler after restore, so v1 refuses to export
# them. Durable goals may remain, but they must reach a normal waiting state
# with no executable external work before the snapshot is taken.
_PORTABLE_V1_INFLIGHT_STATES = {
    "eva_core.model_attempts": frozenset({"reserved", "dispatch_authorized"}),
    "eva_core.plan_steps": frozenset({"ready", "running"}),
    "eva_core.attention_items": frozenset({"pending", "claimed"}),
    "eva_effects.read_attempts": frozenset({"prepared", "dispatch_authorized"}),
    "eva_effects.effect_intents": frozenset(
        {"prepared", "dispatch_authorized", "waiting_breaker"}
    ),
    "eva_effects.effect_attempts": frozenset({"reserved", "dispatch_authorized"}),
    "eva_effects.deliveries": frozenset(
        {"prepared", "dispatch_authorized", "provider_accepted"}
    ),
}


class PrincipalBindingIsolationError(PortableArchiveError):
    """A selected tenant binding is not self-contained in this installation."""


def _default_table_specs() -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for table in CATALOG_SPEC["canonical_tables"]:
        portable = table["portableExport"]
        column_types = {
            declaration.split(" ", 1)[0]: declaration.split(" ", 2)[1]
            for declaration in table["columns"]
        }
        result.append(
            {
                "export_ordinal": table["exportOrdinal"],
                "table": table["table"],
                "scope": table["scope"],
                "database_column_order": tuple(
                    portable["transformSpec"]["databaseColumnOrder"]
                ),
                "column_types": column_types,
                "primary_key": tuple(table["primaryKey"]),
                "record_contract": portable["recordContract"],
                "record_jsonl_relative_path": portable["recordJsonlRelativePath"],
                "record_schema_relative_path": portable["recordSchemaRelativePath"],
                "record_schema_sha256": portable["record_schema_sha256"],
                "transform_spec": portable["transformSpec"],
                "foreign_keys": tuple(table["foreignKeys"]),
                "uniques": tuple(table["uniques"]),
                "reference_validations": tuple(table.get("referenceValidations", ())),
            }
        )
    return tuple(result)


CANONICAL_EXPORT_SPECS = _default_table_specs()


def _canonical_uuid(value: str) -> str:
    try:
        canonical = str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PortableArchiveError("snapshot scope is not a UUID") from exc
    if canonical != value:
        raise PortableArchiveError("snapshot scope UUID is not lowercase canonical")
    return canonical


def _validate_identifier(identifier: str) -> None:
    if not _IDENTIFIER.fullmatch(identifier):
        raise PortableArchiveError(f"unsafe sealed SQL identifier: {identifier!r}")


def _normalize_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    table = str(spec.get("table"))
    parts = table.split(".")
    if len(parts) != 2:
        raise PortableArchiveError("canonical export table must be schema-qualified")
    for part in parts:
        _validate_identifier(part)
    if parts[0] == "dbos":
        raise PortableArchiveError("DBOS tables are forbidden in portable export")
    columns = tuple(spec.get("database_column_order", ()))
    primary_key = tuple(spec.get("primary_key", ()))
    for column in (*columns, *primary_key):
        _validate_identifier(str(column))
    if not columns or not primary_key or not set(primary_key) <= set(columns):
        raise PortableArchiveError(f"{table} export columns/primary key are incomplete")
    normalized = dict(spec)
    normalized["table"] = table
    normalized["database_column_order"] = columns
    normalized["primary_key"] = primary_key
    normalized["export_ordinal"] = int(spec["export_ordinal"])
    return normalized


def _one_value(result: Any) -> Any:
    row = result.fetchone()
    if row is None:
        raise PortableArchiveError("PostgreSQL snapshot query returned no row")
    if isinstance(row, Mapping):
        if len(row) != 1:
            raise PortableArchiveError("PostgreSQL scalar query returned extra columns")
        return next(iter(row.values()))
    return row[0]


def _two_values(result: Any) -> tuple[Any, Any]:
    row = result.fetchone()
    if row is None:
        raise PortableArchiveError("PostgreSQL attestation query returned no row")
    if isinstance(row, Mapping):
        if len(row) != 2:
            raise PortableArchiveError("PostgreSQL attestation returned wrong width")
        values = tuple(row.values())
    elif hasattr(row, "_mapping"):
        values = tuple(row._mapping.values())
    else:
        values = tuple(row)
    if len(values) != 2:
        raise PortableArchiveError("PostgreSQL attestation returned wrong width")
    return values[0], values[1]


def _portable_row(record: Mapping[str, Any]) -> Mapping[str, Any]:
    row = record.get("row")
    return row if isinstance(row, Mapping) else record


def validate_principal_binding_evidence_scope(
    records: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    """Reject a tenant binding whose verification evidence escaped the snapshot."""

    bindings = records.get("eva_core.principal_bindings", ())
    if not bindings:
        return
    installations = records.get("eva_core.installations", ())
    if len(installations) != 1:
        raise PrincipalBindingIsolationError(
            "principal binding export lacks one target installation"
        )
    installation_id = _portable_row(installations[0]).get("installation_id")
    evidence = {
        (
            _portable_row(record).get("evidence_id"),
            _portable_row(record).get("installation_id"),
        )
        for record in records.get("eva_evidence.evidence_envelopes", ())
    }
    for record in bindings:
        row = _portable_row(record)
        evidence_id = row.get("verification_evidence_id")
        if (evidence_id, installation_id) not in evidence:
            raise PrincipalBindingIsolationError(
                "principal binding verification evidence is not in the same installation"
            )


def validate_portable_v1_attention_rows(
    records: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    """Reject current attention rows that the sealed v1 archive cannot restore."""

    for row in records.get("eva_core.attention_items", ()):
        payload = row.get("payload")
        if (
            row.get("kind") not in _PORTABLE_V1_ATTENTION_KINDS
            or not isinstance(payload, Mapping)
            or payload.get("contract") != "AttentionWake/v1"
            or payload.get("schema_version") != 1
            or payload.get("kind") != row.get("kind")
        ):
            raise PortableArchiveError(
                "portable v1 cannot export this attention kind or payload contract"
            )


def validate_portable_v1_no_inflight_external_work(
    records: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    """Reject an archive that could resume an old external action after restore."""

    for table, forbidden_states in _PORTABLE_V1_INFLIGHT_STATES.items():
        for record in records.get(table, ()):
            row = _portable_row(record)
            state = row.get("state")
            if state in forbidden_states:
                raise PortableArchiveError(
                    "portable v1 requires quiescent external work: "
                    f"{table} state {state!r} is still executable"
                )


def _mapping_rows(result: Any, columns: Sequence[str]) -> list[dict[str, Any]]:
    if hasattr(result, "mappings"):
        rows = result.mappings().all()
    else:
        rows = result.fetchall()
    mapped: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            value = dict(row)
        elif hasattr(row, "_mapping"):
            value = dict(row._mapping)
        else:
            if len(row) != len(columns):
                raise PortableArchiveError("PostgreSQL row width differs from catalog")
            value = dict(zip(columns, row, strict=True))
        if set(value) != set(columns):
            raise PortableArchiveError("PostgreSQL row columns differ from catalog")
        mapped.append(value)
    return mapped


class PostgresPortableSnapshot:
    """Read all 39 sets under exactly one caller-independent snapshot."""

    def __init__(
        self,
        connection: Any,
        *,
        tenant_id: str,
        installation_id: str,
        table_specs: Sequence[Mapping[str, Any]] = CANONICAL_EXPORT_SPECS,
    ) -> None:
        self._connection = connection
        self._tenant_id = _canonical_uuid(tenant_id)
        self._installation_id = _canonical_uuid(installation_id)
        self._specs = tuple(_normalize_spec(spec) for spec in table_specs)
        ordinals = [spec["export_ordinal"] for spec in self._specs]
        if ordinals != list(range(1, len(self._specs) + 1)):
            raise PortableArchiveError("portable export ordinals are not contiguous")
        self._entered = False
        self._read = False
        self.snapshot_id: str | None = None

    def _execute(self, sql: str, params: tuple[str, ...] | None = None) -> Any:
        driver_sql = getattr(self._connection, "exec_driver_sql", None)
        if driver_sql is not None:
            return driver_sql(sql) if params is None else driver_sql(sql, params)
        return (
            self._connection.execute(sql)
            if params is None
            else self._connection.execute(sql, params)
        )

    def __enter__(self) -> "PostgresPortableSnapshot":
        if self._entered:
            raise PortableArchiveError("snapshot context cannot be entered twice")
        in_transaction = getattr(self._connection, "in_transaction", None)
        if callable(in_transaction) and in_transaction():
            raise PortableArchiveError(
                "portable export requires a connection outside a transaction"
            )
        self._entered = True
        try:
            self._execute(SNAPSHOT_BEGIN_SQL)
            self._execute("SET LOCAL ROLE eva_backup")
            self._execute("SET LOCAL row_security = on")
            self._execute(
                "SELECT set_config('eva.tenant_id', %s, true), "
                "set_config('eva.installation_id', %s, true)",
                (self._tenant_id, self._installation_id),
            )
            role, row_security = _two_values(
                self._execute(
                    "SELECT current_user, pg_catalog.current_setting('row_security')"
                )
            )
            if (role, row_security) != ("eva_backup", "on"):
                raise PortableArchiveError(
                    "PostgreSQL export role/row_security attestation differs"
                )
            snapshot_id = _one_value(
                self._execute("SELECT pg_catalog.pg_export_snapshot()")
            )
            if not isinstance(snapshot_id, str) or not snapshot_id:
                raise PortableArchiveError("PostgreSQL returned an invalid snapshot ID")
            self.snapshot_id = snapshot_id
            return self
        except BaseException:
            self._connection.rollback()
            raise

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()

    def _scope_clause(self, spec: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
        table = spec["table"]
        if table == "eva_core.tenants":
            return "tenant_id = %s", (self._tenant_id,)
        if table == "eva_core.principals":
            return self._reachable_principals_clause()
        if table == "eva_core.principal_bindings":
            return self._reachable_principal_bindings_clause()
        if "installation_id" not in spec["database_column_order"]:
            raise PortableArchiveError(
                f"{table} lacks an exact installation scope rule"
            )
        return (
            "tenant_id = %s AND installation_id = %s",
            (self._tenant_id, self._installation_id),
        )

    def _reachable_principals_clause(self) -> tuple[str, tuple[str, ...]]:
        # Every union arm is installation-scoped.  This prevents unrelated people
        # in the same tenant from leaking into a one-installation archive.
        references: list[tuple[str, tuple[str, ...]]] = [
            (sql, (self._tenant_id, self._installation_id))
            for sql in (
                "SELECT owner_principal_id AS principal_id FROM eva_core.conversations WHERE tenant_id=%s AND installation_id=%s",
                "SELECT unnest(participant_principal_ids) FROM eva_core.conversations WHERE tenant_id=%s AND installation_id=%s",
                "SELECT principal_id FROM eva_core.messages WHERE tenant_id=%s AND installation_id=%s",
                "SELECT principal_id FROM eva_core.ingress_requests WHERE tenant_id=%s AND installation_id=%s",
                "SELECT principal_id FROM eva_core.episodes WHERE tenant_id=%s AND installation_id=%s",
                "SELECT principal_id FROM eva_core.consent_grants WHERE tenant_id=%s AND installation_id=%s",
                "SELECT owner_principal_id FROM eva_core.commitments WHERE tenant_id=%s AND installation_id=%s",
                "SELECT owner_principal_id FROM eva_core.connector_bindings WHERE tenant_id=%s AND installation_id=%s",
                "SELECT principal_id FROM eva_core.relationships WHERE tenant_id=%s AND installation_id=%s",
                "SELECT related_principal_id FROM eva_core.relationships WHERE tenant_id=%s AND installation_id=%s",
                "SELECT owner_principal_id FROM eva_evidence.object_refs WHERE tenant_id=%s AND installation_id=%s",
                "SELECT principal_id FROM eva_memory.source_events WHERE tenant_id=%s AND installation_id=%s",
            )
        ]
        selected_bindings, selected_parameters = self._selected_principal_binding_ids()
        references.append(
            (
                "SELECT binding.principal_id "
                "FROM eva_core.principal_bindings AS binding "
                "WHERE binding.tenant_id=%s "
                f"AND binding.principal_binding_id IN ({selected_bindings})",
                (self._tenant_id, *selected_parameters),
            )
        )
        params = tuple(
            parameter
            for _, arm_parameters in references
            for parameter in arm_parameters
        )
        return (
            "tenant_id = %s AND principal_id IN ("
            + " UNION ".join(sql for sql, _ in references)
            + ")",
            (self._tenant_id, *params),
        )

    def _selected_principal_binding_ids(self) -> tuple[str, tuple[str, ...]]:
        references = (
            "SELECT principal_binding_id FROM eva_core.ingress_requests WHERE tenant_id=%s AND installation_id=%s",
            "SELECT principal_binding_id FROM eva_core.consent_grants WHERE tenant_id=%s AND installation_id=%s",
            "SELECT binding.principal_binding_id FROM eva_core.principal_bindings AS binding JOIN eva_core.relationships AS relationship ON relationship.tenant_id=binding.tenant_id AND binding.verification_evidence_id=ANY(relationship.channel_binding_evidence_refs) WHERE relationship.tenant_id=%s AND relationship.installation_id=%s",
        )
        return (
            " UNION ".join(references),
            (self._tenant_id, self._installation_id) * len(references),
        )

    def _reachable_principal_bindings_clause(self) -> tuple[str, tuple[str, ...]]:
        selected_bindings, selected_parameters = self._selected_principal_binding_ids()
        return (
            f"tenant_id = %s AND principal_binding_id IN ({selected_bindings})",
            (self._tenant_id, *selected_parameters),
        )

    @staticmethod
    def _order_clause(spec: Mapping[str, Any]) -> str:
        column_types = spec.get("column_types", {})
        parts = []
        for column in spec["primary_key"]:
            if str(column_types.get(column, "")).startswith("text"):
                parts.append(f'{column} COLLATE "C" ASC')
            else:
                parts.append(f"{column} ASC")
        return ", ".join(parts)

    def read_all(self) -> dict[str, list[dict[str, Any]]]:
        if not self._entered or self.snapshot_id is None:
            raise PortableArchiveError("portable rows require an active snapshot")
        if self._read:
            raise PortableArchiveError("portable snapshot can only be consumed once")
        self._read = True
        result: dict[str, list[dict[str, Any]]] = {}
        for spec in self._specs:
            table = spec["table"]
            where, params = self._scope_clause(spec)
            expected_count = _one_value(
                self._execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params)
            )
            if isinstance(expected_count, bool) or not isinstance(expected_count, int):
                raise PortableArchiveError(f"{table} COUNT(*) was not an integer")
            columns = spec["database_column_order"]
            query = (
                f"SELECT {', '.join(columns)} FROM {table} WHERE {where} "
                f"ORDER BY {self._order_clause(spec)}"
            )
            rows = _mapping_rows(self._execute(query, params), columns)
            if len(rows) != expected_count:
                raise PortableArchiveError(
                    f"{table} row count changed inside the fixed snapshot"
                )
            result[table] = rows
        self.validate_rows(result)
        return result

    def validate_rows(self, result: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        """Validate the selected profile; v1 retains its frozen checks."""
        validate_principal_binding_evidence_scope(result)
        validate_portable_v1_attention_rows(result)
        validate_portable_v1_no_inflight_external_work(result)

    def build_record_sets(self) -> tuple[RecordSetBytes, ...]:
        raw_sets = self.read_all()
        materialized: list[RecordSetBytes] = []
        for spec in self._specs:
            if "transform_spec" not in spec:
                raise PortableArchiveError("test-only table spec cannot build records")
            records = tuple(
                build_portable_record(
                    table=spec["table"],
                    record_contract=spec["record_contract"],
                    primary_key_columns=spec["primary_key"],
                    transform_spec=spec["transform_spec"],
                    database_row=row,
                )
                for row in raw_sets[spec["table"]]
            )
            content = encode_jsonl_records(
                records, primary_key_columns=spec["primary_key"]
            )
            materialized.append(
                RecordSetBytes(
                    export_ordinal=spec["export_ordinal"],
                    table=spec["table"],
                    contract=spec["record_contract"],
                    relative_path=spec["record_jsonl_relative_path"],
                    record_schema_relative_path=spec["record_schema_relative_path"],
                    record_schema_sha256=spec["record_schema_sha256"],
                    records=records,
                    content=content,
                )
            )
        return tuple(materialized)
