"""Workspace traversal and source-byte boundaries, without a programming process."""
import base64
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess

import pytest

from eva.operations.runtime_repair_artifacts import RuntimeRepairArtifacts
from eva.operations.runtime_repair_publisher import read_image_source


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
    actual=read_image_source(SimpleNamespace(_command=command),image,path,tmp_path)
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
