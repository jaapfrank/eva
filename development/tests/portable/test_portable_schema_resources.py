from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path


from tools.materialize_4d_schema_artifacts import FREEZE_PATH as FREEZE, PORTABLE_ROOT


def test_materialized_portable_schemas_are_the_exact_sealed_bytes() -> None:
    authority = json.loads(FREEZE.read_bytes())
    portable = authority["portableExport"]

    record_collection = portable["generatedPortableRecordSchemas"]
    assert len(record_collection["schemas"]) == 39
    for entry in record_collection["schemas"]:
        expected = base64.b64decode(entry["canonicalBytesBase64"], validate=True)
        path = PORTABLE_ROOT / "records" / Path(entry["recordSchemaRelativePath"]).name
        assert path.read_bytes() == expected
        assert hashlib.sha256(expected).hexdigest() == entry["record_schema_sha256"]

    object_authority = portable["objectManifest"]["recordSchemaAuthority"]
    expected_object_schema = base64.b64decode(
        object_authority["canonicalBytesBase64"], validate=True
    )
    assert (
        PORTABLE_ROOT / "object-manifest-record-v1.schema.json"
    ).read_bytes() == expected_object_schema

    reader_collection = authority["genericReader"]["outputSchemaManifest"]
    assert len(reader_collection["schemas"]) == 8
    for entry in reader_collection["schemas"]:
        expected = base64.b64decode(entry["canonicalBytesBase64"], validate=True)
        path = PORTABLE_ROOT / "readback" / Path(entry["schemaRelativePath"]).name
        assert path.read_bytes() == expected
        assert hashlib.sha256(expected).hexdigest() == entry["record_schema_sha256"]

    record_manifest = base64.b64decode(
        record_collection["manifest"]["canonicalBytesBase64"], validate=True
    )
    assert (
        PORTABLE_ROOT / "record-schema-manifest-v1.json"
    ).read_bytes() == record_manifest
    assert (
        hashlib.sha256(record_manifest).hexdigest()
        == "de2e302c8d260707563a8c16af1a6dfc2a3932507013dddb79d7780a3e36dc68"
    )

    reader_manifest = base64.b64decode(
        reader_collection["manifest"]["canonicalBytesBase64"], validate=True
    )
    assert (
        PORTABLE_ROOT / "generic-reader-output-schema-manifest-v1.json"
    ).read_bytes() == reader_manifest
    assert (
        hashlib.sha256(reader_manifest).hexdigest()
        == "5b26d3b51a556b4b214b4d2d1cd664fb5532064d5eb0c7c7db70e833c320e876"
    )
