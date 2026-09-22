"""Real core-v2 fixture inside an encrypted installation content envelope.

External database/document bytes are synthetic: these tests prove packaging,
not PostgreSQL/SQLite import, native relocation, process fencing or activation.
"""
from copy import deepcopy
import json
import os
import shutil

import pytest

from eva.application.portable_archive import canonical_json_bytes
from tools.installation_recovery_package import read_installation_package, write_installation_package
from tools.portable_transport import seal_portable_archive, open_portable_transport
from tests.portable.test_portable_v2_current_work import current_archive, PASSPHRASE


@pytest.fixture
def staged(current_archive, tmp_path):
    root = tmp_path / "installation"
    root.mkdir(mode=0o700)
    shutil.copytree(current_archive["archive"], root / "core")
    payloads = {
        "memory/hindsight.dump": b"Synthetic database dump; no import claim.",
        "gateway/contextforge.sqlite": b"Synthetic registry; no SQLite import claim.",
        "documents/project/report.md": b"Synthetic private document",
        "documents/project/workspace-writer.journal": b"Synthetic native journal",
        "documents/project/.project-identity.json": b'{"synthetic":true}',
        "config/provider.json": b'{"synthetic":"binding"}',
        "config/catalog.json": b'{"synthetic":"catalog"}',
    }
    for name, content in payloads.items():
        file = root / name
        file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        file.write_bytes(content)
        file.chmod(0o600)
    roles = dict(zip(list(payloads)[2:], ["document_artifact", "document_journal", "document_marker",
                                         "provider_binding", "provider_catalog"]))
    sources = {kind: [{"name": kind+"-source", "image_sha256": "a"*64}]
               for kind in ("memory", "gateway", "documents")}
    keys = {"gateway_encryption": "key:gateway-encryption", "gateway_jwt": "key:gateway-jwt",
            "gateway_access": "key:owner-access"}
    return root, dict(file_roles=roles, source_components=sources, key_references=keys)


def rewrite_manifest(root, change):
    file = root / "archive.json"
    value = json.loads(file.read_bytes())
    change(value)
    file.write_bytes(canonical_json_bytes(value))


def test_real_core_encrypted_roundtrip_preserves_bytes_and_role_modes(staged, tmp_path):
    root, inputs = staged
    result = write_installation_package(root, **inputs)
    core = result["core_archive"]
    assert result["manifest"]["core_archive_sha256"] == core["archive_sha256"]
    for key in ("created_at", "source_tenant_id", "source_installation_id", "runtime_identity"):
        assert result["manifest"][key] == core["manifest"][key]
    assert result["manifest"]["files"]["config/provider.json"]["mode"] == "0400"
    assert (root / "config/provider.json").stat().st_mode & 0o777 == 0o600
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    sealed, restored = tmp_path / "installation.eva", tmp_path / "opened"
    seal_portable_archive(root, sealed, passphrase=PASSPHRASE, validator=read_installation_package)
    open_portable_transport(sealed, restored, passphrase=PASSPHRASE, validator=read_installation_package)
    after = read_installation_package(restored)
    assert after["manifest_raw"] == result["manifest_raw"]
    from eva.operations.portable_restore_authority import authorize_installation_recovery
    from tests.portable.restore_authority_fixture import restore_authority_for_archive
    authority = json.loads(restore_authority_for_archive(after["core_archive"]["manifest_raw"]))
    authority["authorized_archives"] += json.loads(
        restore_authority_for_archive(after["manifest_raw"]))["authorized_archives"]
    authority["authorized_archives"].sort(key=lambda row: row["archive_id"])
    outer, inner = authorize_installation_recovery(
        package_manifest_raw=after["manifest_raw"],
        core_manifest_raw=after["core_archive"]["manifest_raw"],
        restore_authority_raw=canonical_json_bytes(authority))
    assert outer.archive_json_sha256 == after["archive_sha256"]
    assert inner.archive_json_sha256 == after["core_archive"]["archive_sha256"]
    assert outer.authority_sha256 == inner.authority_sha256
    assert before == {str(p.relative_to(restored)): p.read_bytes() for p in restored.rglob("*") if p.is_file()}
    assert not {"quiescent", "authorized", "activated"} & set(after["manifest"])
    assert (restored / "config/provider.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("change", ["missing", "extra", "changed", "symlink", "hardlink"])
def test_closed_inventory_and_regular_file_boundary(staged, change):
    root, inputs = staged
    write_installation_package(root, **inputs)
    target = root / "documents/project/report.md"
    if change == "missing":
        target.unlink()
    elif change == "extra":
        (root / "unexpected").write_bytes(b"extra")
    elif change == "changed":
        target.write_bytes(b"changed")
    elif change == "symlink":
        target.unlink()
        target.symlink_to(root / "memory/hindsight.dump")
    else:
        os.link(target, root / "documents/project/alias.md")
    with pytest.raises(ValueError):
        read_installation_package(root)


@pytest.mark.parametrize("change", ["identity", "runtime", "core_hash", "mode", "role", "key_bytes"])
def test_manifest_cannot_conflict_with_core_or_materialization_roles(staged, change):
    root, inputs = staged
    write_installation_package(root, **inputs)
    def edit(value):
        if change == "identity":
            value["source_installation_id"] = "00000000-0000-0000-0000-000000000000"
        elif change == "runtime":
            value["runtime_identity"]["tenant_id"] = "other"
        elif change == "core_hash":
            value["core_archive_sha256"] = "0"*64
        elif change in {"mode", "role"}:
            value["files"]["config/provider.json"][change] = "0600" if change == "mode" else "document_artifact"
        else:
            value["key_references"]["gateway_encryption"] = {"secret": "not-a-reference"}
    rewrite_manifest(root, edit)
    with pytest.raises(ValueError):
        read_installation_package(root)


def test_core_validator_is_not_replaced_by_outer_hashes(staged):
    root, inputs = staged
    write_installation_package(root, **inputs)
    file = root / "core/archive.json"
    inner = json.loads(file.read_bytes())
    inner["profile_sha256"] = "0"*64
    file.write_bytes(canonical_json_bytes(inner))
    import hashlib
    def edit(value):
        value["files"]["core/archive.json"]["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
        value["files"]["core/archive.json"]["size"] = file.stat().st_size
        value["core_archive_sha256"] = value["files"]["core/archive.json"]["sha256"]
    rewrite_manifest(root, edit)
    with pytest.raises(ValueError, match="profile"):
        read_installation_package(root)


def test_second_read_rejects_mutation_during_nested_validation(staged, monkeypatch):
    root, inputs = staged
    write_installation_package(root, **inputs)
    import tools.installation_recovery_package as package
    original = package._core
    def change_after_validation(fd):
        result = original(fd)
        (root / "memory/hindsight.dump").write_bytes(b"changed during validation")
        return result
    monkeypatch.setattr(package, "_core", change_after_validation)
    with pytest.raises(ValueError, match="changed during validation"):
        read_installation_package(root)


@pytest.mark.parametrize("change", ["missing_roles", "unknown_role", "source_secret", "missing_key"])
def test_writer_refuses_incomplete_or_unknown_metadata(staged, change):
    root, original = staged
    inputs = deepcopy(original)
    if change == "missing_roles":
        inputs["file_roles"].pop("config/provider.json")
    elif change == "unknown_role":
        inputs["file_roles"]["config/provider.json"] = "secret"
    elif change == "source_secret":
        inputs["source_components"]["gateway"][0]["password"] = "not-allowed"
    else:
        inputs["key_references"].pop("gateway_encryption")
    with pytest.raises(ValueError):
        write_installation_package(root, **inputs)
    assert not (root / "archive.json").exists()


def test_writer_does_not_overwrite_an_existing_manifest(staged):
    root, inputs = staged
    first = write_installation_package(root, **inputs)["manifest_raw"]
    with pytest.raises(ValueError, match="already exists"):
        write_installation_package(root, **inputs)
    assert (root / "archive.json").read_bytes() == first
