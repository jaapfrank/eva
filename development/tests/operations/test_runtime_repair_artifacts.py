"""Workspace traversal and source-byte boundaries, without a programming process."""
import base64
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys

import pytest

from eva.application.self_repair import MAX_REPAIR_SOURCE_BYTES
from eva.operations.runtime_repair_artifacts import RuntimeRepairArtifacts
from eva.operations.runtime_repair_publisher import read_image_source, require_unshadowed_repair_source


def test_artifact_snapshot_refuses_symlinks_hardlinks_and_duplicate_fields(tmp_path):
    key='a'*64;folder=tmp_path/key/'repair';folder.mkdir(parents=True)
    artifact=folder/'candidate.json';artifact.write_text('{"candidate":1}')
    client=RuntimeRepairArtifacts(SimpleNamespace(config=SimpleNamespace(workspace_root=tmp_path)))
    meta={'workspace_key':key,'state':'succeeded'}
    assert client.read(meta,'candidate.json')=={'candidate':1}
    artifact.write_text('{"candidate":1,"candidate":2}')
    with pytest.raises(ValueError,match='duplicate'):client.read(meta,'candidate.json')
    artifact.unlink();secret=tmp_path/'unrelated';secret.write_text('{"private":true}')
    artifact.symlink_to(secret)
    with pytest.raises(OSError):client.read(meta,'candidate.json')
    artifact.unlink();os.link(secret,artifact)
    with pytest.raises(ValueError,match='regular'):client.read(meta,'candidate.json')
    artifact.unlink();folder.rename(folder.with_name('other'));folder.symlink_to(folder.with_name('other'))
    with pytest.raises(OSError):client.read(meta,'candidate.json')


def test_image_source_transport_is_byte_exact_including_whitespace(tmp_path):
    source=b'\n# installed bytes\r\ndef behavior():\n    return "ok"\n\n'
    publisher=SimpleNamespace(_command=lambda *_:base64.b64encode(source).decode().strip())
    assert read_image_source(publisher,'sha256:'+'1'*64,'application/example.py',tmp_path)==source


@pytest.mark.skipif(not os.environ.get('EVA_RUNTIME_REPAIR_TEST_IMAGE'),reason='explicit existing local image required')
def test_actual_image_source_transport_matches_raw_installed_bytes(tmp_path):
    image=os.environ['EVA_RUNTIME_REPAIR_TEST_IMAGE'];path='application/conversation.py'
    def command(argv,_):return subprocess.check_output(argv).decode().strip()
    publisher=SimpleNamespace(_command=command)
    actual=read_image_source(publisher,image,path,tmp_path)
    assert read_image_source(publisher,image,'application/eva_test_ordinary_absent_source.py',tmp_path) is None
    raw=subprocess.check_output(['docker','run','--rm','--network','none','--read-only','--entrypoint','python',
        image,'-I','-c','import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())',
        '/opt/eva/src/eva/'+path])
    assert actual==raw and actual.endswith(b'\n')


def test_independent_source_seed_does_not_receive_builder_candidate_schema(tmp_path):
    import hashlib
    from eva.contracts.canonical_json import canonical_sha256
    workspace=tmp_path/'programming';workspace.mkdir()
    client=RuntimeRepairArtifacts(SimpleNamespace(config=SimpleNamespace(workspace_root=workspace)))
    raw=b'def ordinary_behavior():\n    return 1\n';path='application/not_predefined.py'
    target=dict(contract='EvaRuntimeRepairTarget/v1',target_id='a'*64,sources={path:hashlib.sha256(raw).hexdigest()},
        installation={'core':{'image':'sha256:'+'1'*64}})
    metadata=dict(workspace_key='b'*64,role='original_behavior_oracle')
    client.seed_sources(metadata,target,source=lambda *args:raw)
    folder=workspace/metadata['workspace_key']/'repair'
    assert (folder/'source/eva'/path).read_bytes()==raw
    assert not (folder/'handoff.json').exists()


def test_image_source_absence_is_distinct_from_invalid_or_unreadable_path(tmp_path):
    root = tmp_path / "eva"
    root.mkdir()
    source = root / "application"
    source.mkdir()
    (source / "valid.py").write_bytes(b"# exact source\r\n")
    (source / "directory.py").mkdir()
    (source / "link.py").symlink_to(source / "not-present.py")
    (source / "empty.py").touch()
    (source / "large.py").write_bytes(b"x" * (MAX_REPAIR_SOURCE_BYTES + 1))
    (root / "adapters").symlink_to(tmp_path / "absent-directory")
    os.mkfifo(source / "fifo.py")

    def command(argv, _):
        assert "--network" in argv and "--read-only" in argv and "-S" in argv
        script, relative, maximum, absence = argv[argv.index("-c") + 1:]
        script = script.replace('"/opt/eva/src/eva"', repr(str(root)))
        return subprocess.check_output([sys.executable, "-I", "-S", "-c",
                                        script, relative, maximum, absence],
                                       stderr=subprocess.DEVNULL, timeout=3, text=True).strip()

    publisher = SimpleNamespace(_command=command)
    image = "sha256:" + "1" * 64
    assert read_image_source(publisher, image, "application/valid.py", tmp_path) == b"# exact source\r\n"
    assert read_image_source(publisher, image, "application/missing.py", tmp_path) is None
    assert read_image_source(publisher, image, "domain/ordinary_missing_parent.py", tmp_path) is None
    for relative in ("application/directory.py", "application/link.py", "application/fifo.py",
                     "application/empty.py", "application/large.py", "adapters/example.py"):
        with pytest.raises(subprocess.CalledProcessError):
            read_image_source(publisher, image, relative, tmp_path)
    root.rename(tmp_path / "old-root")
    root.symlink_to(tmp_path / "old-root")
    with pytest.raises(subprocess.CalledProcessError):
        read_image_source(publisher, image, "application/missing.py", tmp_path)


def test_image_source_rejects_mutable_image_and_propagates_command_failure(tmp_path):
    def failed(*_):
        raise PermissionError("source or image read refused")
    publisher = SimpleNamespace(_command=failed)
    with pytest.raises(ValueError, match="immutable repair image"):
        read_image_source(publisher, "mutable:latest", "application/example.py", tmp_path)
    with pytest.raises(PermissionError, match="read refused"):
        read_image_source(publisher, "sha256:" + "1" * 64, "application/example.py", tmp_path)


@pytest.mark.parametrize("mount", ["/opt/eva", "/opt/eva/src/eva", "/opt/eva/src/eva/application/example.py",
                                  "//opt/eva/src/eva"])
def test_runtime_image_sources_cannot_be_shadowed_by_bind_or_tmpfs(mount):
    for storage in ({"Mounts": [{"Destination": mount}], "HostConfig": {"ReadonlyRootfs": True}},
                    {"Mounts": [], "HostConfig": {"ReadonlyRootfs": True, "Tmpfs": {mount: "ro"}}}):
        with pytest.raises(ValueError, match="shadowed"):
            require_unshadowed_repair_source(storage)


def test_runtime_source_root_must_be_read_only():
    current = {"Mounts": [{"Destination": "/var/lib/eva"}],
               "HostConfig": {"ReadonlyRootfs": True, "Tmpfs": {"/tmp": "rw"}}}
    require_unshadowed_repair_source(current)
    current["HostConfig"]["ReadonlyRootfs"] = False
    with pytest.raises(ValueError, match="read-only"):
        require_unshadowed_repair_source(current)


def test_role_scoped_seed_uses_exact_worker_image(tmp_path):
    import hashlib
    path = "application/example.py"
    raw = b"# worker-only installed source\n"
    client = RuntimeRepairArtifacts(SimpleNamespace(config=SimpleNamespace(workspace_root=tmp_path)))
    role = "delegated-work-worker"
    image = "sha256:" + "2" * 64
    target = {"contract": "EvaRuntimeRepairTarget/v2", "target_id": "a" * 64,
              "repair_services": [role], "sources": {path: hashlib.sha256(raw).hexdigest()},
              "installation": {"core": {"image": "sha256:" + "1" * 64}, role: {"image": image}}}
    calls = []
    def source(observed_image, relative):
        calls.append((observed_image, relative))
        return raw
    metadata = {"workspace_key": "b" * 64, "role": "original_behavior_oracle"}
    client.seed_sources(metadata, target, source=source)
    assert calls == [(image, path)]
    assert (tmp_path / metadata["workspace_key"] / "repair/source/eva" / path).read_bytes() == raw
