"""Existing wizard dispatches verified v3 archives using operator identity policy."""
from contextlib import nullcontext
from pathlib import Path
import os
import subprocess
import sys

import pytest

from tools.installer_portable_restore import InstallerPortableRestore
from tools.portable_transport import seal_portable_archive
from tools.portable_runtime_archive import read_runtime_archive
from tests.portable.test_portable_runtime_archive import runtime_archive
from tests.portable.restore_authority_fixture import restore_authority_for_archive


class Engine:
    def connect(self):
        return self

    def execution_options(self, **kwargs):
        return self

    def begin(self):
        return nullcontext()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def dispose(self):
        pass


def _workflow(args, tmp_path, *, identity=True):
    workspace = tmp_path / "wizard"
    workspace.mkdir(mode=0o700, exist_ok=True)
    arguments = dict(engine=Engine(), authority_reader=lambda: restore_authority_for_archive(
        (args["destination"] / "archive.json").read_bytes()), workspace=workspace,
        object_store_root=tmp_path / "objects", runtime_identity=args["runtime_identity"])
    if identity:
        arguments.update(identity_trust=args["identity_trust"], identity_destination=workspace / "identity.json")
    return InstallerPortableRestore(**arguments)


def test_v3_requires_explicit_operator_identity_restore(runtime_archive, tmp_path):
    args, _ = runtime_archive
    workflow = _workflow(args, tmp_path, identity=False)
    with pytest.raises(ValueError, match="externally configured"):
        workflow._read(args["destination"])


def test_existing_preview_and_execute_dispatch_v3_without_activation(runtime_archive, tmp_path, monkeypatch):
    args, _ = runtime_archive
    workflow = _workflow(args, tmp_path)
    import tools.portable_v2_restore as current_restore
    import tools.portable_runtime_restore as runtime_restore
    verified = []
    monkeypatch.setattr(current_restore, "verify_current_target", lambda connection: verified.append(connection))
    calls = []

    def restore(connection, directory, **kwargs):
        calls.append((connection, kwargs))
        archive = read_runtime_archive(directory, identity_trust=kwargs["identity_trust"])
        assert archive["runtime_identity_bytes"] == args["runtime_identity_bytes"]
        return {"status": "restored", "target_activation": "closed",
                "runtime_identity_disposition": "preserved_inactive_requires_operator_activation"}

    monkeypatch.setattr(runtime_restore, "restore_runtime_archive", restore)
    blob = tmp_path / "archive.eva"
    seal_portable_archive(args["destination"], blob, passphrase="synthetic-runtime-restore-password",
        validator=lambda path: read_runtime_archive(path, identity_trust=args["identity_trust"]))
    try:
        preview = workflow.prepare(blob.read_bytes(), "synthetic-runtime-restore-password")
        assert preview["summary"]["target_activation"] == "closed"
        assert preview["summary"]["runtime_identity_disposition"] == "preservation_inactive_planned"
        result = workflow.execute(str(preview["confirmation"]))
        assert result["runtime_identity_disposition"] == "preserved_inactive_requires_operator_activation"
        assert result["target_activation"] == "closed"
        assert verified == [workflow.engine]
        assert len(calls) == 1
        assert calls[0][1]["identity_trust"] is args["identity_trust"]
        assert calls[0][1]["identity_destination"] == workflow.identity_destination
        assert calls[0][1]["expected_runtime_identity"] == args["runtime_identity"]
        with pytest.raises(ValueError, match="confirmation"):
            workflow.execute(str(preview["confirmation"]))
    finally:
        workflow.close()


def test_trusted_configuration_loads_private_external_binding(runtime_archive, tmp_path, monkeypatch):
    args, _ = runtime_archive
    import tools.installer_restore_wizard as launcher
    monkeypatch.setattr(launcher, "create_engine", lambda dsn: Engine())
    trust = tmp_path / "trust.json"
    trust.write_bytes((Path(__file__).resolve().parents[3] /
        "src/eva/application/default_identity_pack_trust.json").read_bytes())
    trust.chmod(0o600)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    config = {"dsn": "operator-target", "authority": str(tmp_path / "authority"),
        "workspace": str(workspace), "objects": str(tmp_path / "objects"),
        "keyring": None, "traffic_closed": True, "runtime_guard": {},
        "runtime_identity": args["runtime_identity"],
        "runtime_identity_restore": {"trust": str(trust), "destination": str(workspace / "identity.json")}}
    with launcher.configured_restore_workflow(config) as workflow:
        assert workflow.identity_trust == args["identity_trust"]
        assert workflow.identity_destination == workspace / "identity.json"
    config["runtime_identity_restore"]["trust"] = "relative-trust.json"
    with pytest.raises(ValueError, match="absolute trusted paths"):
        with launcher.configured_restore_workflow(config):
            pytest.fail("relative trust must not be admitted")


def test_identity_restore_needs_both_values_and_absent_destination(runtime_archive, tmp_path):
    args, _ = runtime_archive
    workspace = tmp_path / "private"
    workspace.mkdir(mode=0o700)
    base = dict(engine=Engine(), authority_reader=lambda: b"", workspace=workspace,
                object_store_root=tmp_path / "objects", identity_trust=args["identity_trust"])
    with pytest.raises(ValueError, match="together"):
        InstallerPortableRestore(**base)
    destination = workspace / "existing.json"
    destination.write_text("retained")
    with pytest.raises(ValueError, match="inactive new destination"):
        InstallerPortableRestore(**base, identity_destination=destination)
    assert destination.read_text() == "retained"


def test_foreign_owner_is_refused_before_database_effects(runtime_archive, tmp_path, monkeypatch):
    from tools import portable_runtime_restore as restore
    args, _ = runtime_archive
    parent = tmp_path / "identity-owner"
    parent.mkdir(mode=0o700)
    monkeypatch.setattr(restore.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(restore, "_restore_archive", lambda *a, **k: pytest.fail("database insertion must not begin"))
    with pytest.raises(ValueError, match="lacks authority"):
        restore.restore_runtime_archive(type("Connection", (), {"in_transaction": lambda self: True})(),
            args["destination"], identity_trust=args["identity_trust"], identity_destination=parent / "identity.json",
            identity_runtime_owner={"uid":65532,"gid":65532}, authority_reader=lambda:b"",
            expected_runtime_identity=args["runtime_identity"], object_store_root=tmp_path / "objects")


def test_private_identity_is_readable_by_actual_different_runtime_uid(tmp_path):
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
        pytest.skip("requires existing noninteractive root authority for uid transition")
    script = """
import os,sys
from pathlib import Path
from tools.portable_runtime_restore import _preserve_identity
parent=Path(sys.argv[1]);parent.mkdir(mode=0o700)
try:
    target=parent/'identity.json'
    _preserve_identity(b'verified-test-identity',target,{'uid':65532,'gid':65532})
    assert target.stat().st_uid==parent.stat().st_uid==65532
    assert target.stat().st_mode&0o777==0o600
    assert parent.stat().st_mode&0o777==0o700
    child=os.fork()
    if child==0:
        os.chdir(parent);os.setgroups([]);os.setgid(65532);os.setuid(65532)
        assert Path('identity.json').read_bytes()==b'verified-test-identity'
        os._exit(0)
    assert os.waitpid(child,0)[1]==0
finally:
    for path in parent.iterdir():path.unlink()
    parent.rmdir()
"""
    done = subprocess.run(["sudo", "-n", "env", "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONPATH=" + os.pathsep.join(sys.path), sys.executable, "-c", script,
        str(tmp_path / "runtime-private")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("source_head,target_head,index_valid,reason", [
    ("4e_0011_timeline_type_time", "4e_0010_memory_compaction", True, "heads differ"),
    ("4e_0010_memory_compaction", "4e_0011_timeline_type_time", True, "target schema differs"),
    ("4e_0011_timeline_type_time", "4e_0012_conversation_source_lock", True, "unsupported"),
    ("4e_0011_timeline_type_time", "4e_0011_timeline_type_time", False, "pinned shape"),
    ("4e_0012_conversation_source_lock", "4e_0012_conversation_source_lock", True, "unsupported"),
])
def test_runtime_target_pair_and_index_are_not_spoofed(source_head, target_head, index_valid, reason):
    from tests.portable.test_portable_runtime_archive import SchemaConnection
    from tools.portable_runtime_restore import verify_runtime_restore_target
    with pytest.raises(ValueError, match=reason):
        verify_runtime_restore_target(SchemaConnection(target_head, index_valid=index_valid), source_head)


def test_real_target_interface_checks_empty_and_matching_0011_without_writes():
    from tests.portable.test_portable_runtime_archive import SchemaConnection
    from tools.portable_runtime_restore import verify_runtime_restore_target
    connection = SchemaConnection("4e_0011_timeline_type_time")
    assert verify_runtime_restore_target(connection, connection.head) == {
        "source_schema_head": connection.head, "target_schema_head": connection.head}
    assert any("to_regclass" in query for query in connection.queries)
    assert any("SELECT COUNT(*) FROM eva_core.operational_checkpoints" in query for query in connection.queries)
    assert not any(query.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER")) for query in connection.queries)


def test_installation_preview_resolves_inner_v3_head(runtime_archive, tmp_path, monkeypatch):
    import json
    from tests.portable.test_portable_runtime_archive import SchemaConnection
    import tools.installation_recovery_package as package
    args, _ = runtime_archive
    workflow = _workflow(args, tmp_path)
    manifest = json.dumps({"contract": "EvaInstallationRecovery/v1"}).encode()
    head = "4e_0011_timeline_type_time"
    def verified_inner(directory, *, identity_trust):
        assert identity_trust == args["identity_trust"]
        return {"manifest_raw": manifest, "core_archive": {"manifest": {
            "contract": "EvaPortableArchive/v3", "schema_head": head}}}
    monkeypatch.setattr(package, "read_installation_package", verified_inner)
    try:
        assert workflow._verify_archive_target(SchemaConnection(head), args["destination"], manifest) == {
            "source_schema_head": head, "target_schema_head": head}
        with pytest.raises(ValueError, match="heads differ"):
            workflow._verify_archive_target(SchemaConnection("4e_0010_memory_compaction"), args["destination"], manifest)
    finally:
        workflow.close()


def test_0011_wizard_preview_and_execution_keep_runtime_head(runtime_archive, tmp_path, monkeypatch):
    from tests.portable.test_portable_runtime_archive import SchemaConnection
    import tools.portable_runtime_archive as source
    import tools.portable_runtime_restore as restore
    args, _ = runtime_archive
    current = dict(args, destination=tmp_path / "actual-head0011")
    source.export_runtime_archive(SchemaConnection(source.CURRENT_SCHEMA_HEAD), **current)
    workflow = _workflow(current, tmp_path)
    class CurrentEngine(Engine, SchemaConnection):
        pass
    workflow.engine = CurrentEngine(source.CURRENT_SCHEMA_HEAD)
    calls = []
    def inactive_restore(connection, directory, **kwargs):
        checked = read_runtime_archive(directory, identity_trust=kwargs["identity_trust"])
        assert checked["manifest"]["schema_head"] == source.CURRENT_SCHEMA_HEAD
        binding = restore.verify_runtime_restore_target(connection, checked["manifest"]["schema_head"])
        calls.append(binding)
        return {"status": "restored", "target_activation": "closed", **binding}
    monkeypatch.setattr(restore, "restore_runtime_archive", inactive_restore)
    blob = tmp_path / "actual-head0011.eva"
    seal_portable_archive(current["destination"], blob, passphrase="a sufficiently long passphrase",
        validator=lambda path: read_runtime_archive(path, identity_trust=args["identity_trust"]))
    try:
        preview = workflow.prepare(blob.read_bytes(), "a sufficiently long passphrase")
        result = workflow.execute(str(preview["confirmation"]))
        assert result["source_schema_head"] == result["target_schema_head"] == source.CURRENT_SCHEMA_HEAD
        assert result["target_activation"] == "closed" and len(calls) == 1
    finally:
        workflow.close()
