#!/usr/bin/env python3
"""Materialize only the byte-sealed 4D schema outputs.

The design freeze contains canonical bytes, rather than a recipe that is free
to evolve.  This command therefore decodes and verifies those bytes and emits
the small generated PostgreSQL catalog resource consumed by the runtime.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FREEZE_PATH = ROOT / "development/design/vertical/noor-4d-schema-export-freeze-v1.json"
EXPECTED_FREEZE_SHA256 = (
    "f70eb25013ff84e464e4ef2a448b2e63fe23a8c11d91d4d57a54d3e431dc9d38"
)
CATALOG_PATH = ROOT / "src/eva/adapters/postgres/catalog_generated.py"
PORTABLE_ROOT = ROOT / "deploy/schemas/portable"


class MaterializationError(RuntimeError):
    """The sealed source or one of its embedded byte bindings is invalid."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode_bound_bytes(
    item: dict[str, Any],
    *,
    hash_key: str,
    length_key: str = "canonicalByteLength",
) -> bytes:
    if item.get("canonicalBytesEncoding") != "base64-standard":
        raise MaterializationError("unsupported canonical byte encoding")
    try:
        data = base64.b64decode(item["canonicalBytesBase64"], validate=True)
    except (KeyError, ValueError) as error:
        raise MaterializationError("invalid base64-standard canonical bytes") from error
    if len(data) != item.get(length_key):
        raise MaterializationError("canonical byte length does not match binding")
    if _sha256(data) != item.get(hash_key):
        raise MaterializationError("canonical byte SHA-256 does not match binding")
    return data


def _load_freeze() -> dict[str, Any]:
    raw = FREEZE_PATH.read_bytes()
    actual = _sha256(raw)
    if actual != EXPECTED_FREEZE_SHA256:
        raise MaterializationError(
            f"schema freeze SHA-256 mismatch: expected {EXPECTED_FREEZE_SHA256}, got {actual}"
        )
    freeze = json.loads(raw)
    if freeze.get("contract") != "Eva24DSchemaExportFreeze/v1":
        raise MaterializationError("unexpected schema freeze contract")
    return freeze


def _catalog_bytes(freeze: dict[str, Any]) -> bytes:
    generated = freeze["portableExport"]["generatedPortableRecordSchemas"]
    export_by_table = {
        item["table"]: {
            key: value
            for key, value in item.items()
            if key
            in {
                "exportOrdinal",
                "table",
                "recordContract",
                "recordJsonlRelativePath",
                "recordSchemaRelativePath",
                "sourceDefinitionSha256",
                "transformSpec",
                "transformSpecSha256",
                "portableRowProperties",
                "jsonbBindings",
                "canonicalByteLength",
                "record_schema_sha256",
            }
        }
        for item in generated["schemas"]
    }

    def table_resource(item: dict[str, Any]) -> dict[str, Any]:
        kept = {
            key: item[key]
            for key in (
                "exportOrdinal",
                "ordinal",
                "table",
                "classification",
                "owner",
                "scope",
                "columns",
                "primaryKey",
                "alternateKeyForTenantReferences",
                "foreignKeys",
                "uniques",
                "checks",
                "additionalIndexes",
                "indexes",
                "cas",
                "retention",
                "referenceValidations",
            )
            if key in item
        }
        if item["classification"] == "canonical":
            kept["portableExport"] = export_by_table[item["table"]]
        return kept

    family = freeze["schemaFamily"]
    conventions = freeze["databaseConventions"]
    automatic_manifest = conventions["automaticForeignKeyIndexManifest"]
    resource = {
        "schema_freeze_sha256": EXPECTED_FREEZE_SHA256,
        "role_grant_manifest": conventions["roleTableColumnGrantManifest"],
        "collation_manifest": conventions["collationManifest"],
        "automatic_index_rule": conventions["automaticIndexRule"],
        "automatic_fk_index_entries": automatic_manifest["manifestCore"]["entries"],
        "automatic_fk_index_entries_sha256": automatic_manifest[
            "orderedEntriesCanonicalSha256"
        ],
        "canonical_tables": [table_resource(item) for item in family["tables"]],
        "noncanonical_tables": [
            table_resource(item) for item in family["nonCanonicalTables"]
        ],
    }
    payload = json.dumps(resource, ensure_ascii=False, separators=(",", ":"))
    source = (
        '"""Generated from noor-4d-schema-export-freeze-v1.json; do not edit."""\n\n'
        "from __future__ import annotations\n\n"
        "import json\n\n"
        f"CATALOG_SPEC = json.loads(\n    {payload!r}\n)\n"
    )
    return source.encode("utf-8")


def _portable_outputs(freeze: dict[str, Any]) -> dict[Path, bytes]:
    portable = freeze["portableExport"]
    generated = portable["generatedPortableRecordSchemas"]
    outputs: dict[Path, bytes] = {}
    for item in generated["schemas"]:
        relative = Path(item["recordSchemaRelativePath"])
        expected_prefix = Path("deploy/schemas/portable/records")
        if relative.parent != expected_prefix:
            raise MaterializationError(
                f"record schema escapes sealed output root: {relative}"
            )
        outputs[ROOT / relative] = _decode_bound_bytes(
            item, hash_key="record_schema_sha256"
        )

    outputs[PORTABLE_ROOT / "record-schema-manifest-v1.json"] = _decode_bound_bytes(
        generated["manifest"],
        hash_key="sha256",
        length_key="canonicalByteLength",
    )

    object_manifest = portable["objectManifest"]
    object_schema = object_manifest["recordSchemaAuthority"]
    outputs[PORTABLE_ROOT / "object-manifest-record-v1.schema.json"] = (
        _decode_bound_bytes(object_schema, hash_key="record_schema_sha256")
    )

    reader_manifest = freeze["genericReader"]["outputSchemaManifest"]
    for item in reader_manifest["schemas"]:
        relative = Path(item["schemaRelativePath"])
        expected_prefix = Path("deploy/schemas/readback")
        if relative.parent != expected_prefix:
            raise MaterializationError(
                f"reader schema escapes sealed output root: {relative}"
            )
        outputs[PORTABLE_ROOT / "readback" / relative.name] = _decode_bound_bytes(
            item, hash_key="record_schema_sha256"
        )
    outputs[PORTABLE_ROOT / "generic-reader-output-schema-manifest-v1.json"] = (
        _decode_bound_bytes(
            reader_manifest["manifest"],
            hash_key="sha256",
            length_key="canonicalByteLength",
        )
    )
    return outputs


def expected_outputs() -> dict[Path, bytes]:
    freeze = _load_freeze()
    return {CATALOG_PATH: _catalog_bytes(freeze), **_portable_outputs(freeze)}


def materialize(*, check: bool) -> list[str]:
    stale: list[str] = []
    for path, expected in sorted(expected_outputs().items()):
        actual = path.read_bytes() if path.is_file() else None
        if actual == expected:
            continue
        stale.append(path.relative_to(ROOT).as_posix())
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
    return stale


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if any generated output is absent or differs",
    )
    args = parser.parse_args()
    try:
        stale = materialize(check=args.check)
    except (KeyError, TypeError, json.JSONDecodeError, MaterializationError) as error:
        print(f"4D schema materialization failed: {error}")
        return 1
    if args.check and stale:
        print("stale 4D schema outputs:")
        for path in stale:
            print(f"- {path}")
        return 1
    if stale:
        print(f"materialized {len(stale)} sealed 4D schema outputs")
    else:
        print("4D schema outputs are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
