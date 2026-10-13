from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
import uuid

import sqlalchemy as sa

from eva.adapters.postgres.catalog import (
    AUTOMATIC_FK_INDEX_ENTRIES,
    CANONICAL_TABLE_NAMES,
    NONCANONICAL_TABLE_NAMES,
    ROLE_GRANT_ENTRIES,
    application_role_grant_statements,
    metadata,
)


ROOT = Path(__file__).resolve().parents[3]
FREEZE = ROOT / "development/design/vertical/noor-4d-schema-export-freeze-v1.json"
EXPECTED_FREEZE_SHA256 = (
    "f70eb25013ff84e464e4ef2a448b2e63fe23a8c11d91d4d57a54d3e431dc9d38"
)


def test_catalog_is_generated_from_the_exact_sealed_freeze() -> None:
    assert hashlib.sha256(FREEZE.read_bytes()).hexdigest() == EXPECTED_FREEZE_SHA256
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "src/tools/materialize_4d_schema_artifacts.py"),
            "--check",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_catalog_has_exactly_39_canonical_and_5_noncanonical_tables() -> None:
    assert len(CANONICAL_TABLE_NAMES) == 39
    assert len(NONCANONICAL_TABLE_NAMES) == 5
    assert set(metadata.tables) == set(CANONICAL_TABLE_NAMES) | set(
        NONCANONICAL_TABLE_NAMES
    )


def test_catalog_columns_primary_keys_and_named_objects_match_freeze() -> None:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    tables = (
        freeze["schemaFamily"]["tables"] + freeze["schemaFamily"]["nonCanonicalTables"]
    )
    for spec in tables:
        table = metadata.tables[spec["table"]]
        declared_names = [item.split(" ", 1)[0] for item in spec["columns"]]
        assert list(table.c.keys()) == declared_names
        assert [column.name for column in table.primary_key.columns] == spec[
            "primaryKey"
        ]
        assert all(column.server_default is None for column in table.c)

        expected_constraints = {
            item.split(":", 1)[0]
            for key in ("foreignKeys", "uniques", "checks")
            for item in spec.get(key, [])
            if " WHERE " not in item
        } | {f"pk_{table.name}"}
        actual_constraints = {constraint.name for constraint in table.constraints}
        assert actual_constraints == expected_constraints
        assert None not in actual_constraints

        expected_explicit_indexes = {
            item.split(":", 1)[0]
            for key in ("additionalIndexes", "indexes", "uniques")
            for item in spec.get(key, [])
            if key != "uniques" or " WHERE " in item
        }
        assert expected_explicit_indexes <= {index.name for index in table.indexes}


def test_every_table_is_force_rls_scoped_and_has_no_default() -> None:
    for table in metadata.tables.values():
        assert table.info["scope"] in {"tenant", "installation"}
        assert table.info["enable_rls"] is True
        assert table.info["force_rls"] is True
        assert all(
            column.default is None and column.server_default is None
            for column in table.c
        )


def test_partial_uniques_are_unique_postgresql_indexes() -> None:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    tables = (
        freeze["schemaFamily"]["tables"] + freeze["schemaFamily"]["nonCanonicalTables"]
    )
    expected = {
        item.split(":", 1)[0]
        for table in tables
        for item in (*table.get("uniques", []), *table.get("additionalIndexes", []))
        if " WHERE " in item and ("UNIQUE " in item)
    }
    actual = {
        index.name
        for table in metadata.tables.values()
        for index in table.indexes
        if index.unique and index.dialect_options["postgresql"].get("where") is not None
    }
    assert actual == expected


def test_no_foreign_key_targets_dbos() -> None:
    assert all(
        not element.target_fullname.startswith("dbos.")
        for table in metadata.tables.values()
        for constraint in table.foreign_key_constraints
        for element in constraint.elements
    )


def test_catalog_uses_postgresql_native_types_without_binary_float() -> None:
    assert all(
        not isinstance(column.type, (sa.Float, sa.REAL, sa.DOUBLE_PRECISION))
        for table in metadata.tables.values()
        for column in table.c
    )


def test_automatic_fk_index_manifest_is_exact_collision_free_authority() -> None:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    conventions = freeze["databaseConventions"]
    rule = conventions["automaticIndexRule"]
    manifest = conventions["automaticForeignKeyIndexManifest"]
    entries = list(AUTOMATIC_FK_INDEX_ENTRIES)
    canonical = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert entries == manifest["manifestCore"]["entries"]
    assert len(entries) == rule["manifestEntryCount"] == 79
    assert (
        sum(item["naturalName"] != item["selectedName"] for item in entries)
        == (rule["shortenedEntryCount"])
        == 50
    )
    assert (
        hashlib.sha256(canonical).hexdigest()
        == (rule["orderedEntriesCanonicalSha256"])
        == "72c91fe2d7a1ebf24d193e5a1b5ddb2b1b90ad42f67b8198e3c87b6a876c4ad4"
    )
    selected = [item["selectedName"] for item in entries]
    assert len(selected) == len(set(selected))
    assert all(name.isascii() and len(name.encode("utf-8")) <= 63 for name in selected)
    assert {
        item["selectedName"]
        for item in entries
        if item["table"] == "eva_core.relationships"
        and item["localColumns"]
        == [
            "tenant_id",
            "installation_id",
            "source_event_id",
        ]
    } == {"ix_relationships__fk__tenant_id__installation__119e5c90f021c3ab"}


def test_preserved_timestamp_typmods_are_explicit_catalog_exceptions() -> None:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    exceptions = freeze["databaseConventions"]["preservedPhysical4BExceptions"]
    preserved = set(exceptions["timestampTypmodMinusOne"])
    assert preserved == {
        "eva_core.tenants.created_at",
        "eva_core.installations.created_at",
        "eva_core.episodes.created_at",
        "eva_core.episodes.updated_at",
        "eva_core.episode_events.occurred_at",
    }

    for table in metadata.tables.values():
        for column in table.c:
            if not isinstance(column.type, sa.TIMESTAMP):
                continue
            full_name = f"{table.fullname}.{column.name}"
            assert column.type.timezone is True
            assert column.type.precision == (None if full_name in preserved else 0)


def test_role_grant_manifest_is_exactly_derived_from_sealed_write_and_delete_profiles() -> (
    None
):
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    tables = (
        freeze["schemaFamily"]["tables"] + freeze["schemaFamily"]["nonCanonicalTables"]
    )
    manifest = freeze["databaseConventions"]["roleTableColumnGrantManifest"]
    expected_entries = []
    for table in tables:
        cas = table["cas"]
        update_enabled = any(
            cas.startswith(prefix)
            for prefix in ("revisionCas", "currentSwap", "projectionUpsert")
        )
        column_names = [item.split(" ", 1)[0] for item in table["columns"]]
        retention = table["retention"]
        delete_owner = table["owner"]["delete"]
        if isinstance(retention, dict):
            assert retention["deleteOwner"] == delete_owner
            retention_profile = retention["profile"]
        else:
            retention_profile = retention
        expected_entries.append(
            {
                "table": table["table"],
                "roles": ["eva_runtime", "eva_dbos"],
                "tablePrivileges": ["SELECT", "INSERT", "DELETE"],
                "updateColumns": (
                    [name for name in column_names if name not in table["primaryKey"]]
                    if update_enabled
                    else []
                ),
                "sourceCasProfile": cas,
                "sourceDeleteOwner": delete_owner,
                "sourceRetentionProfile": retention_profile,
            }
        )

    canonical = json.dumps(
        expected_entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert manifest["entries"] == expected_entries
    assert manifest["roleOrder"] == ["eva_runtime", "eva_dbos"]
    assert manifest["entryCount"] == len(expected_entries) == 44
    assert manifest["updateTableCount"] == sum(
        bool(item["updateColumns"]) for item in expected_entries
    )
    assert manifest["deleteTableCount"] == 44
    assert (
        manifest["orderedEntriesCanonicalSha256"]
        == hashlib.sha256(canonical).hexdigest()
    )


def test_all_201_collatable_columns_are_materialized_with_c_collation() -> None:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    tables = (
        freeze["schemaFamily"]["tables"] + freeze["schemaFamily"]["nonCanonicalTables"]
    )
    manifest = freeze["databaseConventions"]["collationManifest"]
    expected_entries = []
    historical = {
        ("eva_core.episodes", "state"),
        ("eva_core.episodes", "state_contract"),
        ("eva_core.episode_events", "event_kind"),
        ("eva_core.episode_events", "before_state"),
        ("eva_core.episode_events", "after_state"),
    }
    for table in tables:
        for declaration in table["columns"]:
            name, type_and_nullability = declaration.split(" ", 1)
            type_name = type_and_nullability.removesuffix(" NOT NULL").removesuffix(
                " NULL"
            )
            if type_name not in {"text", "text[]", "varchar(64)"}:
                continue
            expected_entries.append(
                {
                    "table": table["table"],
                    "column": name,
                    "declaredType": type_name,
                    "physicalAction": (
                        "alter_preserved_4b"
                        if (table["table"], name) in historical
                        else "create_or_add"
                    ),
                }
            )

    canonical = json.dumps(
        expected_entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert manifest["entries"] == expected_entries
    assert manifest["entryCount"] == len(expected_entries) == 201
    assert manifest["preserved4BAlterCount"] == 5
    assert manifest["collation"] == "C"
    assert (
        manifest["orderedEntriesCanonicalSha256"]
        == hashlib.sha256(canonical).hexdigest()
    )

    for entry in expected_entries:
        column = metadata.tables[entry["table"]].c[entry["column"]]
        if isinstance(column.type, sa.ARRAY):
            assert column.type.item_type.collation == "C"
        else:
            assert column.type.collation == "C"


def test_application_acl_sql_is_generated_from_each_exact_manifest_entry() -> None:
    for entry in ROLE_GRANT_ENTRIES:
        statements = application_role_grant_statements(entry["table"])
        assert statements[0].startswith("REVOKE ALL PRIVILEGES ON TABLE ")
        assert statements[1].startswith("REVOKE ALL (")
        assert statements[2] == (
            f"GRANT SELECT, INSERT, DELETE ON TABLE {entry['table']} "
            "TO eva_runtime, eva_dbos"
        )
        if entry["updateColumns"]:
            assert statements[3] == (
                f"GRANT UPDATE ({', '.join(entry['updateColumns'])}) ON TABLE "
                f"{entry['table']} TO eva_runtime, eva_dbos"
            )
            assert len(statements) == 4
        else:
            assert len(statements) == 3


def test_installation_object_store_projection_authority_is_executable() -> None:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    installation = next(
        table
        for table in freeze["schemaFamily"]["tables"]
        if table["table"] == "eva_core.installations"
    )
    binding = installation["payloadContracts"][0]
    authority = binding["projectionAuthority"]
    assert authority["installationId"] == {
        "scalarColumn": "installation_id",
        "payloadField": "installation_id",
        "rule": "byte_equal",
    }
    object_store = authority["objectStoreId"]
    assert object_store["algorithm"] == "UUIDv5_SHA1_namespace_name"
    assert object_store["nameEncoding"] == "UTF-8"
    assert (
        str(
            uuid.uuid5(
                uuid.UUID(object_store["namespaceUuid"]),
                object_store["sealedEvaLogicalId"],
            )
        )
        == object_store["sealedEvaUuid"]
        == "62e07e08-9793-5eee-a1f7-2ed9305211de"
    )
    assert authority["absoluteHostPath"] == {
        "payloadField": "absolute_host_path_in_contract",
        "requiredValue": False,
    }


def test_principal_binding_export_selection_includes_relationship_evidence_refs() -> (
    None
):
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    tables = {table["table"]: table for table in freeze["schemaFamily"]["tables"]}
    authority = tables["eva_core.principal_bindings"]["export"]["selectionAuthority"]
    assert authority == {
        "operator": "set_union_distinct",
        "directReferenceClosure": {
            "sourceFamilies": [
                "eva_core.ingress_requests",
                "eva_core.messages",
                "eva_core.consent_grants",
            ],
            "rule": (
                "existing exported direct principal-binding-reference closure "
                "scoped to the target tenant and installation"
            ),
        },
        "relationshipChannelBindingEvidenceClosure": {
            "relationshipTable": "eva_core.relationships",
            "relationshipScopeColumns": ["tenant_id", "installation_id"],
            "relationshipEvidenceArrayColumn": "channel_binding_evidence_refs",
            "bindingTable": "eva_core.principal_bindings",
            "bindingTenantColumn": "tenant_id",
            "bindingVerificationEvidenceColumn": "verification_evidence_id",
            "matchRule": (
                "binding.tenant_id=relationship.tenant_id AND "
                "binding.verification_evidence_id="
                "ANY(relationship.channel_binding_evidence_refs)"
            ),
        },
        "selectedBindingEvidenceInstallationValidation": {
            "evidenceTable": "eva_evidence.evidence_envelopes",
            "bindingEvidenceColumn": "verification_evidence_id",
            "requiredEvidenceScopeColumns": ["tenant_id", "installation_id"],
            "requiredScope": "target tenant and installation",
            "onMismatch": "reject_export_without_widening",
        },
    }
    relationship_rule = tables["eva_core.relationships"]["referenceValidations"]
    assert relationship_rule == [
        "every channel_binding_evidence_refs element must resolve to "
        "eva_evidence.evidence_envelopes in the same tenant and installation before "
        "commit; each element may select every same-tenant "
        "eva_core.principal_bindings row whose verification_evidence_id matches, "
        "subject to the selected binding evidence belonging to this relationship's "
        "target installation"
    ]
