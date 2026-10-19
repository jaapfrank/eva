"""Current-work extension to the unchanged canonical portable archive.

The v2 container preserves a complete v1 subarchive and adds only post-freeze
operational records. Checkpoints are derived from the authoritative event log.
"""
from __future__ import annotations

import hashlib
import json
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from eva.adapters.postgres.current_catalog import delegated_work_runs
from eva.adapters.postgres.operational_timeline import operational_events
from eva.application.portable_archive import canonical_json_bytes
from tools.postgres_portable_export import CANONICAL_EXPORT_SPECS

CONTRACT = "EvaPortableArchive/v2"
SCHEMA_HEAD = "4e_0010_memory_compaction"
EXTERNAL_REQUIREMENTS = (
    "native_document_artifacts_and_journals_require_separate_verified_relocation",
    "hindsight_semantic_banks_require_separate_verified_restore_or_rebuild",
    "credentials_and_provider_bindings_require_fresh_authorization",
    "target_traffic_remains_closed_until_external_dependencies_are_reconciled",
)


def _encoding(column):
    kind = column.type
    if isinstance(kind, postgresql.UUID):
        return "uuidLowercase"
    if isinstance(kind, postgresql.BYTEA):
        return "sha256LowercaseHex"
    if isinstance(kind, sa.DateTime):
        return "utcWholeSeconds"
    if isinstance(kind, sa.Integer):
        return "jsonInteger"
    if isinstance(kind, postgresql.JSONB):
        return "canonicalJson"
    if isinstance(kind, sa.Text):
        return "utf8Text"
    raise ValueError(f"unsupported current archive type: {column}")


def _spec(table, ordinal):
    columns = tuple(column.name for column in table.columns)
    mappings = [{"portableProperty": column.name,
        "source": {"kind": "column", "column": column.name},
        "encoding": _encoding(column)} for column in table.columns]
    return {
        "export_ordinal": ordinal, "table": table.fullname,
        "scope": "installation", "database_column_order": columns,
        "column_types": {column.name: str(column.type).lower() for column in table.columns},
        "primary_key": tuple(column.name for column in table.primary_key.columns),
        "record_contract": f"EvaCurrentRecord/{table.fullname}/v1",
        "record_jsonl_relative_path": f"current/{table.name}.jsonl",
        "transform_spec": {"sourceTable": table.fullname,
            "databaseColumnOrder": list(columns), "portablePropertyOrder": list(columns),
            "propertyMappings": mappings},
    }


CURRENT_SPECS = (_spec(operational_events, 40), _spec(delegated_work_runs, 41))
ALL_SPECS = (*CANONICAL_EXPORT_SPECS, *CURRENT_SPECS)
ATTENTION_SPEC = next(spec for spec in CANONICAL_EXPORT_SPECS
                      if spec["table"] == "eva_core.attention_items")
DEPENDENCY_SPECS = tuple(spec for spec in CANONICAL_EXPORT_SPECS
    if spec["table"] in {"eva_core.principals", "eva_core.principal_bindings"})

PROFILE = {
    "contract": "EvaPortableCurrentWorkProfile/v1",
    "schema_head": SCHEMA_HEAD,
    "current_specs": CURRENT_SPECS,
    "attention_spec": ATTENTION_SPEC,
    "dependency_specs": DEPENDENCY_SPECS,
    "activation_policy": "transactional_database_hold_before_runtime_start",
    "checkpoint_policy": "rebuild_from_validated_events_in_restore_transaction",
    "external_requirements": EXTERNAL_REQUIREMENTS,
}
PROFILE = json.loads(json.dumps(PROFILE))
PROFILE_SHA256 = hashlib.sha256(canonical_json_bytes(PROFILE)).hexdigest()
