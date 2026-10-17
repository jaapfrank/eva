from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from eva.application.portable_archive import PortableArchiveError
from eva.resources import portable_v1
from tools import materialize_4d_schema_artifacts as generator
from tools import portable_export, read_eva_portable_archive as reader


FREEZE_SHA256 = "f70eb25013ff84e464e4ef2a448b2e63fe23a8c11d91d4d57a54d3e431dc9d38"


def test_package_restores_original_exact_schema_authority_and_generated_catalog():
    package = Path(portable_v1.__file__).parent
    provenance = json.loads((package / "provenance.json").read_bytes())
    assert hashlib.sha256(portable_v1.sealed_freeze_bytes()).hexdigest() == FREEZE_SHA256
    for relative, metadata in provenance["resources"].items():
        raw = (package / relative).read_bytes()
        assert len(raw) == metadata["bytes"]
        assert hashlib.sha256(raw).hexdigest() == metadata["sha256"]
    expected = generator.expected_outputs()
    assert expected[generator.CATALOG_PATH] == generator.CATALOG_PATH.read_bytes()
    assert all(path.read_bytes() == raw for path, raw in expected.items())
    assert hashlib.sha256(expected[generator.CATALOG_PATH]).hexdigest() == provenance["catalogLayout"]["expectedCatalogSha256"]


def test_default_resources_are_checkout_independent_and_self_contained(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payloads = portable_export._schema_payloads()
    assert not any(name.startswith("deploy/") for name in payloads)
    assert hashlib.sha256(payloads["schemas/portable/manifest.json"]).hexdigest() == reader.PORTABLE_MANIFEST_SHA256
    assert hashlib.sha256(payloads["schemas/readback/manifest.json"]).hexdigest() == reader.READER_MANIFEST_SHA256
    archive = tmp_path / "archive"
    for name, raw in payloads.items():
        target = archive / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    with reader._root_descriptor(archive) as descriptor:
        registry, records, outputs = reader._load_schema_registry(descriptor,
            {"json_schema_manifest_sha256": reader.CONTRACT_MANIFEST_SHA256})
    assert registry and len(records) == 39 and len(outputs["schemas"]) == 8


@pytest.mark.parametrize("relative", [
    "portable/v1/contracts/manifest.json",
    "portable/v1/contracts/canonical-json-profile-v1.json",
    "portable/record-schema-manifest-v1.json",
    "portable/generic-reader-output-schema-manifest-v1.json",
])
def test_explicit_resource_override_cannot_replace_supported_pins(tmp_path, relative):
    with portable_v1.schema_resources() as root:
        copied = tmp_path / "schemas"
        shutil.copytree(root, copied)
    target = copied / relative
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(PortableArchiveError, match="pinned authority"):
        portable_export._schema_payloads(copied)


def test_standalone_reader_keeps_stdlib_only_execution_outside_checkout(tmp_path):
    script = Path(reader.__file__).resolve()
    result = subprocess.run([sys.executable, "-I", str(script), "--help"], cwd=tmp_path,
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "--validate-only" in result.stdout


def test_generator_stdlib_check_runs_outside_checkout(tmp_path):
    result = subprocess.run([sys.executable, "-I", str(Path(generator.__file__).resolve()), "--check"],
        cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4D schema outputs are current" in result.stdout
