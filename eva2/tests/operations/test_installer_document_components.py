"""Saved document layouts produce complete existing restore callback settings."""
import json
from copy import deepcopy
import pytest
from tools import installer_document_components as module


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    root = tmp_path / "package"
    root.mkdir(mode=0o700)
    (root / "config").mkdir()
    (root / "config/catalog.json").write_text("[]")
    (root / "config/original.json").write_text(json.dumps({"version": 2, "journal_mode": "per_path_prefix_v1"}))
    project = "c" * 64
    entries = {"config/original.json": {"role": "provider_binding"},
               "config/catalog.json": {"role": "provider_catalog"},
               "documents/work/report.md": {"role": "document_artifact"},
               f"documents/journal/{project}/journal": {"role": "document_journal"}}
    monkeypatch.setattr(module, "read_installation_package", lambda root: {"manifest": {"files": entries}})
    args = dict(package_root=root, layouts=[dict(original_binding="config/original.json",
        catalog_path="config/catalog.json", document_directory="documents/work",
        journal_directory="documents/journal", gateway_id="saved-provider", provider_alias="saved-documents")],
        runtime_image="sha256:" + "a" * 64, runtime_uid=1000, runtime_gid=1000,
        credentials={name: str(tmp_path / name) for name in
                     ("jwt_secret_file", "encryption_secret_file", "admin_password_file")},
        network_id="b" * 64, namespace="eva-fresh", workspace=tmp_path / "outputs",
        gateway_database=tmp_path / "contextforge.sqlite")
    return args, entries


def test_preserves_provider_identity_and_builds_distinct_targets(inputs):
    args, _ = inputs
    original = deepcopy(args)
    result = module.build_document_components(**args)
    assert result["document_recovery"]["providers"][0]["project_keys"] == ["c" * 64]
    assert result["document_gateway_binding"]["providers"][0]["provider_alias"] == "saved-documents"
    assert result["document_connection"]["providers"][0]["container_name"] != result["document_write_access"]["providers"][0]["container_name"]
    assert args == original
    assert not args["workspace"].exists()


def test_rejects_unmapped_retained_documents(inputs):
    args, entries = inputs
    entries["documents/other/lost.md"] = {"role": "document_artifact"}
    with pytest.raises(ValueError, match="all retained documents"):
        module.build_document_components(**args)


def test_rejects_duplicate_provider_data(inputs):
    args, _ = inputs
    args["layouts"].append(deepcopy(args["layouts"][0]))
    with pytest.raises(ValueError, match="coverage"):
        module.build_document_components(**args)


def test_layout_cannot_supply_runtime_authority(inputs):
    args, _ = inputs
    args["layouts"][0]["image"] = "untrusted:latest"
    with pytest.raises(ValueError, match="layout differs"):
        module.build_document_components(**args)


def test_shared_documents_with_separate_empty_and_flat_journals(inputs):
    args, entries = inputs
    root = args["package_root"]
    for key in list(entries):
        if key.startswith("documents/journal/"):
            del entries[key]
    (root / "config/flat.json").write_text('{"version":1}')
    entries["config/flat.json"] = {"role": "provider_binding"}
    entries["documents/flat/workspace-writer.journal"] = {"role": "document_journal"}
    second = {**args["layouts"][0], "original_binding": "config/flat.json",
              "journal_directory": "documents/flat", "gateway_id": "second-provider",
              "provider_alias": "second-documents"}
    args["layouts"].append(second)
    result = module.build_document_components(**args)
    assert [p["project_keys"] for p in result["document_recovery"]["providers"]] == [[], []]
    assert len(result["document_connection"]["providers"]) == 2


def test_gateway_catalog_selection_reuses_saved_registration(inputs, monkeypatch):
    args, entries = inputs
    root = args["package_root"]
    (root / "gateway").mkdir()
    (root / "gateway/contextforge.sqlite").write_bytes(b"verified snapshot fixture")
    args["layouts"][0]["catalog_path"] = "gateway/contextforge.sqlite"
    entries["gateway/contextforge.sqlite"] = {"role": "gateway_database"}
    selected = []
    def extract(raw, identifier):
        selected.append((raw, identifier))
        return [{"name": "read", "inputSchema": {"type": "object"}}]
    monkeypatch.setattr(module, "_preserved_gateway_catalog", extract)
    result = module.build_document_components(**args)
    assert selected == [(b"verified snapshot fixture", "saved-provider")]
    assert result["document_recovery"]["providers"][0]["catalog_gateway_id"] == "saved-provider"


@pytest.mark.parametrize("journal", ["documents/journal", "documents/journal/nested"])
def test_empty_journals_cannot_overlap_across_different_document_roots(inputs, journal):
    args, entries = inputs
    for key in list(entries):
        if key.startswith("documents/journal/"):
            del entries[key]
    entries["documents/other/report.md"] = {"role": "document_artifact"}
    args["layouts"].append({**args["layouts"][0], "document_directory": "documents/other",
        "journal_directory": journal, "gateway_id": "other-provider", "provider_alias": "other-documents"})
    with pytest.raises(ValueError, match="journal directories overlap"):
        module.build_document_components(**args)
