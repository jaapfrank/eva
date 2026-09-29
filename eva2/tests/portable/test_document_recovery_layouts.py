"""Actual journal layouts remain explicit in operator restoration."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from eva.contracts.canonical_json import canonical_sha256
from tools.installation_document_restore import _validate_sources
from tools.installation_document_connection import _validate_material_report


def source(tmp_path, *, version, journal):
    root = tmp_path / "package"
    root.mkdir(mode=0o700)
    files = {"config/binding.json": (json.dumps({"version": version,
        **({"journal_mode": "per_path_prefix_v1"} if version == 2 else {})}).encode(), "provider_binding"),
        "config/catalog.json": (b"{}", "provider_catalog"),
        "documents/artifacts/retained.txt": (b"retained", "document_artifact")}
    for name in journal:
        files["documents/journal/" + name] = (b"retained journal bytes", "document_journal")
    entries = {}
    for name, (raw, role) in files.items():
        path = root / name
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.write_bytes(raw)
        entries[name] = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "role": role}
    config = {"original_binding": "config/binding.json", "catalog_path": "config/catalog.json",
        "document_directory": "documents/artifacts", "journal_directory": "documents/journal",
        "logical_catalog_sha256": canonical_sha256({}), "project_keys": [],
        "runtime_uid": os.geteuid(), "runtime_gid": os.getegid()}
    return root, {"manifest": {"files": entries}}, config


@pytest.mark.parametrize("version,journal", [(1, []), (1, ["workspace-writer.journal"]), (2, [])])
def test_empty_and_flat_sources_preserve_layout_without_invented_markers(tmp_path, version, journal):
    root, package, config = source(tmp_path, version=version, journal=journal)
    original = _validate_sources(root, package, config)
    assert json.loads(original)["version"] == version
    assert sorted(p.name for p in (root / "documents/journal").iterdir()) == journal
    assert (root / "documents/artifacts/retained.txt").read_bytes() == b"retained"


def test_empty_declaration_cannot_hide_unlisted_history(tmp_path):
    root, package, config = source(tmp_path, version=2, journal=[])
    directory = root / "documents/journal"
    directory.mkdir(mode=0o700)
    (directory / "unlisted").write_bytes(b"history")
    with pytest.raises(ValueError, match="omit existing"):
        _validate_sources(root, package, config)


def test_flat_inventory_does_not_accept_project_directories(tmp_path):
    root, package, config = source(tmp_path, version=1, journal=["unexpected"])
    with pytest.raises(ValueError, match="flat journal inventory"):
        _validate_sources(root, package, config)


@pytest.mark.parametrize("has_history", [False, True])
def test_flat_material_report_binds_exact_snapshot_presence(has_history):
    receipt = {"journal_layout": "flat", "files": {"binding.json": {"sha256": "a" * 64}}}
    report = {"contract": "EvaDocumentMaterialVerification/v1", "binding_sha256": "a" * 64,
              "project_count": 0, "projects": {}, "flat": None}
    if has_history:
        receipt["files"]["flat.relocation"] = {"sha256": "b" * 64, "size": 42}
        report["flat"] = {"snapshot_sha256": "b" * 64, "snapshot_bytes": 42}
    _validate_material_report(report, {"project_keys": []}, receipt)
    report["flat"] = None if has_history else {"snapshot_sha256": "b" * 64, "snapshot_bytes": 42}
    with pytest.raises(ValueError, match="snapshot presence"):
        _validate_material_report(report, {"project_keys": []}, receipt)


def gateway_source(tmp_path):
    import sqlite3
    root, package, config = source(tmp_path, version=1, journal=[])
    database_path = root / "gateway/contextforge.sqlite"
    database_path.parent.mkdir(mode=0o700)
    with sqlite3.connect(database_path) as database:
        database.execute("CREATE TABLE gateways(id TEXT)")
        database.execute("INSERT INTO gateways VALUES ('retained-provider')")
        database.execute("CREATE TABLE tools(gateway_id TEXT, original_name TEXT, original_description TEXT, "
                         "description TEXT, input_schema TEXT, output_schema TEXT, annotations TEXT)")
        database.executemany("INSERT INTO tools VALUES (?,?,?,?,?,?,?)", [
            ("retained-provider", "write", "Original write", "Gateway override", '{"type":"object"}', "null", "{}"),
            ("retained-provider", "read", None, "Read", '{"type":"object"}', "null", "{}"),
            ("other-provider", "unrelated", None, "Other", '{}', "null", "{}")])
    expected = [{"name": "read", "description": "Read", "inputSchema": {"type": "object"}},
                {"name": "write", "description": "Original write", "inputSchema": {"type": "object"}}]
    raw = database_path.read_bytes()
    package["manifest"]["files"]["gateway/contextforge.sqlite"] = {
        "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "role": "gateway_database"}
    config.update(catalog_path="gateway/contextforge.sqlite", catalog_gateway_id="retained-provider",
                  logical_catalog_sha256=canonical_sha256(expected), image="sha256:" + "a" * 64,
                  output_directory=str(tmp_path / "prepared"))
    return root, package, config, expected


def test_preserved_gateway_catalog_drives_existing_document_source_validation(tmp_path):
    from tools.installation_document_restore import validate_document_configuration, _preserved_gateway_catalog
    root, package, config, expected = gateway_source(tmp_path)
    validate_document_configuration({"providers": [config]})
    assert _preserved_gateway_catalog((root / config["catalog_path"]).read_bytes(), "retained-provider") == expected
    assert json.loads(_validate_sources(root, package, config))["version"] == 1


@pytest.mark.parametrize("change", ["bytes", "pin", "gateway", "role"])
def test_preserved_gateway_catalog_rejects_changed_authority(tmp_path, change):
    root, package, config, expected = gateway_source(tmp_path)
    if change == "bytes":
        with (root / config["catalog_path"]).open("ab") as stream:
            stream.write(b"changed")
    elif change == "pin":
        config["logical_catalog_sha256"] = canonical_sha256(expected[:1])
    elif change == "gateway":
        config["catalog_gateway_id"] = "missing-provider"
    else:
        package["manifest"]["files"][config["catalog_path"]]["role"] = "provider_catalog"
    with pytest.raises(ValueError):
        _validate_sources(root, package, config)


def test_preserved_gateway_catalog_rejects_duplicate_native_names(tmp_path):
    import sqlite3
    from tools.installation_document_restore import _preserved_gateway_catalog
    root, package, config, expected = gateway_source(tmp_path)
    path = root / config["catalog_path"]
    with sqlite3.connect(path) as database:
        database.execute("INSERT INTO tools SELECT * FROM tools WHERE original_name='read'")
    with pytest.raises(ValueError, match="duplicated"):
        _preserved_gateway_catalog(path.read_bytes(), "retained-provider")
