from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

from eva.adapters.postgres.catalog_generated import CATALOG_SPEC


EVA2_ROOT = Path(__file__).resolve().parents[2]
READER = EVA2_ROOT / "tools" / "read_eva_portable_archive.py"


def _reader_module():
    spec = importlib.util.spec_from_file_location("eva_portable_reader_test", READER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generic_reader_imports_only_the_python_standard_library() -> None:
    tree = ast.parse(READER.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    assert imported_roots <= {
        "__future__",
        "argparse",
        "base64",
        "collections",
        "contextlib",
        "dataclasses",
        "datetime",
        "hashlib",
        "json",
        "os",
        "pathlib",
        "re",
        "shutil",
        "stat",
        "sys",
        "tempfile",
        "typing",
        "unicodedata",
        "uuid",
    }
    assert "eva" not in imported_roots
    assert "dbos" not in imported_roots
    assert "sqlalchemy" not in imported_roots
    assert "psycopg" not in imported_roots


def test_reader_recomputes_canonical_payload_hashes_but_not_secret_attestations() -> (
    None
):
    reader = _reader_module()
    payload = {"contract": "example/v1", "schema_version": 1}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    reader._validate_payload_hashes(
        "eva_core.messages", {"payload": payload, "payload_sha256": digest}
    )
    with pytest.raises(reader.ArchiveValidationError, match="canonical value"):
        reader._validate_payload_hashes(
            "eva_core.messages", {"payload": payload, "payload_sha256": "0" * 64}
        )

    reader._validate_payload_hashes(
        "eva_effects.read_attempts",
        {
            "credential_grant_payload": {"contract": "EvaRedactedCredentialGrant/v1"},
            "credential_grant_sha256": "1" * 64,
            "credential_reauthorization_required": True,
        },
    )
    with pytest.raises(reader.ArchiveValidationError, match="reauthorization"):
        reader._validate_payload_hashes(
            "eva_effects.read_attempts",
            {
                "credential_grant_payload": {
                    "contract": "EvaRedactedCredentialGrant/v1"
                },
                "credential_grant_sha256": "1" * 64,
                "credential_reauthorization_required": False,
            },
        )


def test_reader_second_pass_contains_every_sealed_foreign_key() -> None:
    reader = _reader_module()
    expected = set()
    pattern = re.compile(r"^[^:]+: \(([^)]+)\) -> ([a-z_]+\.[a-z_]+)\(([^)]+)\)")
    for table in CATALOG_SPEC["canonical_tables"]:
        for declaration in table["foreignKeys"]:
            match = pattern.match(declaration)
            assert match is not None
            expected.add(
                (
                    table["table"],
                    tuple(match.group(1).split(",")),
                    match.group(2),
                    tuple(match.group(3).split(",")),
                )
            )

    assert set(reader._foreign_keys()) == expected
