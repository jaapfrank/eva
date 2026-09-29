"""Current-runtime core in the existing component envelope and wizard.

External bytes and database dispatch are synthetic here. Real core recovery is
covered by the retained actual-data experiment, not claimed by these tests.
"""
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from eva.application.portable_archive import canonical_json_bytes
from eva.operations.portable_restore_authority import authorize_installation_recovery
from tools.installation_recovery_package import (
    write_installation_package, read_installation_package, installation_identity_trust,
)
from tools.installation_recovery_materializer import COMPONENTS
from tools.installer_portable_restore import InstallerPortableRestore
from tools.portable_transport import seal_portable_archive
from tests.portable.test_portable_runtime_archive import runtime_archive
from tests.portable.test_installer_runtime_archive import Engine
from tests.portable.restore_authority_fixture import restore_authority_for_archive


@pytest.fixture
def runtime_package(runtime_archive, tmp_path):
    args, _ = runtime_archive
    root = tmp_path / "package"
    root.mkdir(mode=0o700)
    shutil.copytree(args["destination"], root / "core")
    for name, content in [("memory/hindsight.dump", b"synthetic-memory"),
                          ("gateway/contextforge.sqlite", b"synthetic-gateway"),
                          ("documents/result.txt", b"retained-document")]:
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
    package = write_installation_package(root,
        file_roles={"documents/result.txt": "document_artifact"},
        source_components={name: [{"name": name, "image_sha256": "a" * 64}]
                           for name in ("memory", "gateway", "documents")},
        key_references={name: name for name in ("gateway_encryption", "gateway_jwt", "gateway_access")},
        identity_trust=args["identity_trust"])
    authority = json.loads(restore_authority_for_archive(package["manifest_raw"]))
    authority["authorized_archives"] += json.loads(restore_authority_for_archive(
        package["core_archive"]["manifest_raw"]))["authorized_archives"]
    authority["authorized_archives"].sort(key=lambda row: row["archive_id"])
    return root, package, args, canonical_json_bytes(authority)


def test_package_requires_external_trust_and_both_manifest_permissions(runtime_package):
    root, package, args, authority = runtime_package
    with pytest.raises(ValueError, match="external identity trust"):
        read_installation_package(root)
    assert read_installation_package(root, identity_trust=args["identity_trust"])["archive_sha256"] == package["archive_sha256"]
    authorize_installation_recovery(package_manifest_raw=package["manifest_raw"],
        core_manifest_raw=package["core_archive"]["manifest_raw"], restore_authority_raw=authority)
    for digest in (package["archive_sha256"], package["core_archive"]["archive_sha256"]):
        denied = json.loads(authority)
        denied["authorized_archives"] = [entry for entry in denied["authorized_archives"]
                                         if entry["archive_json_sha256"] != digest]
        with pytest.raises(ValueError):
            authorize_installation_recovery(package_manifest_raw=package["manifest_raw"],
                core_manifest_raw=package["core_archive"]["manifest_raw"],
                restore_authority_raw=canonical_json_bytes(denied))


def test_nested_trust_resets_after_exception_and_explicit_trust_wins(runtime_package):
    root, _, args, _ = runtime_package
    with installation_identity_trust(args["identity_trust"]):
        read_installation_package(root)
        with pytest.raises(RuntimeError):
            with installation_identity_trust(None):
                with pytest.raises(ValueError):
                    read_installation_package(root)
                read_installation_package(root, identity_trust=args["identity_trust"])
                raise RuntimeError("operator callback failed")
        read_installation_package(root)
    with pytest.raises(ValueError):
        read_installation_package(root)


def test_concurrent_operator_contexts_do_not_share_trust(runtime_package):
    root, _, args, _ = runtime_package
    barrier = Barrier(2)
    def read(trust):
        with installation_identity_trust(trust):
            barrier.wait(timeout=5)
            if trust is None:
                with pytest.raises(ValueError):
                    read_installation_package(root)
                return "denied"
            read_installation_package(root)
            return "verified"
    with ThreadPoolExecutor(max_workers=2) as pool:
        allowed = pool.submit(read, args["identity_trust"])
        denied = pool.submit(read, None)
        assert (allowed.result(), denied.result()) == ("verified", "denied")


def test_existing_wizard_package_materialization_reaches_v3_and_external_callback(runtime_package, tmp_path, monkeypatch):
    root, package, args, authority = runtime_package
    import tools.portable_v2_restore as old
    import tools.portable_runtime_restore as current
    monkeypatch.setattr(old, "verify_current_target", lambda connection: None)
    calls = []
    def restore(connection, directory, **kwargs):
        assert kwargs["identity_trust"] == args["identity_trust"]
        calls.append("core")
        return {"restore_id": "test-held-restore", "inserted_records": 2,
                "target_activation": "closed", "rebuilt_checkpoints": 0}
    monkeypatch.setattr(current, "restore_runtime_archive", restore)
    class Target(Engine):
        def execute(self, *args, **kwargs):
            return type("Result", (), {"rowcount": 1})()
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    destination = tmp_path / "retained-package"
    def external(path, *, guard):
        guard()
        assert read_installation_package(path)["archive_sha256"] == package["archive_sha256"]
        assert (path / "documents/result.txt").read_bytes() == b"retained-document"
        calls.append("external")
        return {"status": "synthetic_callback_verified"}
    wizard = InstallerPortableRestore(engine=Target(), authority_reader=lambda: authority,
        workspace=workspace, object_store_root=tmp_path / "objects",
        runtime_identity=args["runtime_identity"], identity_trust=args["identity_trust"],
        identity_destination=workspace / "identity.json",
        recovery_materialization={"destination": str(destination),
            "owners": {role: {"uid": os.geteuid(), "gid": os.getegid()} for role in COMPONENTS}},
        external_restore=external)
    blob = tmp_path / "package.eva"
    password = "synthetic-runtime-package-passphrase"
    seal_portable_archive(root, blob, passphrase=password,
        validator=lambda path: read_installation_package(path, identity_trust=args["identity_trust"]))
    try:
        preview = wizard.prepare(blob.read_bytes(), password)
        result = wizard.execute(preview["confirmation"])
        assert calls == ["core", "external"]
        assert result["target_activation"] == "closed"
        assert result["external_components"] == "imported_inactive"
        with pytest.raises(ValueError):
            read_installation_package(destination)
    finally:
        wizard.close()
