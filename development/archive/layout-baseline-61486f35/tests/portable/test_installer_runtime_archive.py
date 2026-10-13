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
    trust.write_bytes((Path(__file__).resolve().parents[2] /
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
