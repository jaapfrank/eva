import json
import os
from pathlib import Path
import stat
import pytest

from tools.installer_target_bindings import TargetGraphBinding
from tools.installer_runtime_access import establish_installer_runtime_access, RuntimeAccessError


def source(tmp_path):
    material=tmp_path/"material";material.mkdir(mode=0o700)
    secret=material/"secret";secret.write_text("private-test-input");secret.chmod(0o600)
    return material,secret


def binding(secret,users):
    services={}
    for index,user in enumerate(users):
        service={"image":"sha256:"+"a"*64,"volumes":[{"type":"bind","source":str(secret),
                   "target":"/run/secrets/credential","read_only":True}]}
        if user is not None:service["user"]=user
        services[str(index)]=service
    return TargetGraphBinding({"services":services},(),())


def run(material,bound,**kwargs):
    return establish_installer_runtime_access(binding=bound,materialized_bind_root=material,
        target_root=material/"access",image_users=kwargs.get("image_users",{}))


def test_fresh_private_copy_preserves_original_and_receipt(tmp_path):
    material,secret=source(tmp_path);user=f"{os.geteuid()}:{os.getegid()}"
    original=secret.stat();bound=binding(secret,[user,user])
    result=run(material,bound)
    targets=[Path(s["volumes"][0]["source"]) for s in result.graph["services"].values()]
    assert targets[0]==targets[1] and targets[0]!=secret
    assert targets[0].read_bytes()==secret.read_bytes()
    assert targets[0].stat().st_uid==os.geteuid()
    assert stat.S_IMODE(targets[0].stat().st_mode)==0o600
    assert (secret.stat().st_uid,secret.stat().st_gid,secret.stat().st_mode)==(original.st_uid,original.st_gid,original.st_mode)
    assert "private-test-input" not in repr(result.receipt)
    assert result.receipt["status"]=="access_established" and result.receipt["runtime_started"] is False
    assert bound.graph["services"]["0"]["volumes"][0]["source"]==str(secret)


def test_missing_effective_image_reader_is_not_assumed_root(tmp_path):
    material,secret=source(tmp_path)
    with pytest.raises(ValueError,match="effective reader"):
        run(material,binding(secret,[None]))
    assert not (material/"access").exists()
    result=run(material,binding(secret,[None]),image_users={"sha256:"+"a"*64:f"{os.geteuid()}:{os.getegid()}"})
    assert result.receipt["status"]=="access_established"


def test_ownership_failure_preserves_partial_receipt_and_source(tmp_path,monkeypatch):
    material,secret=source(tmp_path)
    def denied(*args,**kwargs):raise PermissionError("controlled privilege failure")
    monkeypatch.setattr("tools.installer_runtime_access.os.chown",denied)
    with pytest.raises(RuntimeAccessError) as caught:
        run(material,binding(secret,[f"{os.geteuid()}:{os.getegid()}"]))
    saved=json.loads((material/"access/access-receipt.json").read_text())
    assert saved==caught.value.receipt and saved["status"]=="incomplete"
    assert secret.read_text()=="private-test-input"


@pytest.mark.skipif(os.geteuid()!=0,reason="isolated ownership proof requires installer privilege")
def test_shared_model_directory_keeps_producer_and_consumer_on_same_copy(tmp_path):
    material,_=source(tmp_path)
    lease=material/"lease";lease.mkdir(mode=0o700)
    (lease/"auth.json").write_text("private-access-only");(lease/"auth.json").chmod(0o600)
    bound=binding(lease,["1000:65532","65532:65532"])
    for service in bound.graph["services"].values():service["volumes"][0]["target"]="/run/eva/model-lease"
    bound.graph["services"]["0"]["volumes"][0]["read_only"]=False
    result=run(material,bound)
    first=Path(result.graph["services"]["0"]["volumes"][0]["source"])
    assert str(first)==result.graph["services"]["1"]["volumes"][0]["source"]
    assert (first.stat().st_uid,first.stat().st_gid,stat.S_IMODE(first.stat().st_mode))==(1000,65532,0o2750)
    assert ((first/"auth.json").stat().st_uid,(first/"auth.json").stat().st_gid,stat.S_IMODE((first/"auth.json").stat().st_mode))==(1000,65532,0o640)
    assert stat.S_IMODE(lease.stat().st_mode)==0o700
    consumer=Path(result.graph["services"]["1"]["volumes"][0]["source"])
    def read_as_consumer():
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            try:
                os.chroot(consumer)
                os.chdir("/")
                os.setgroups([])
                os.setgid(65532)
                os.setuid(65532)
                with open("/auth.json", "rb") as stream:
                    os.write(write_fd, stream.read())
                os.close(write_fd)
                os._exit(0)
            except BaseException:
                os._exit(1)
        os.close(write_fd)
        try:
            raw = os.read(read_fd, 1024)
        finally:
            os.close(read_fd)
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        return raw
    assert read_as_consumer() == b"private-access-only"
    refreshed=first/"next.json";refreshed.write_text("refreshed-access-only")
    os.chown(refreshed,1000,65532);refreshed.chmod(0o640)
    os.replace(refreshed,first/"auth.json")
    assert read_as_consumer() == b"refreshed-access-only"
    assert (lease/"auth.json").read_text()=="private-access-only"


def test_existing_authority_directory_is_not_copied_or_changed(tmp_path):
    material,_=source(tmp_path)
    authority=tmp_path/"authority";authority.mkdir(mode=0o700)
    auth=authority/"auth.json";auth.write_text("existing-refresh-authority");auth.chmod(0o600)
    result=run(material,binding(authority,[f"{os.geteuid()}:{os.getegid()}"]))
    assert result.graph["services"]["0"]["volumes"][0]["source"]==str(authority)
    assert result.receipt["bindings"][0]["source_preserved"] is True
    assert not any(p.name=="auth.json" for p in (material/"access").rglob("*"))


def test_target_cannot_be_nested_inside_source_directory(tmp_path):
    material,_=source(tmp_path)
    user=f"{os.geteuid()}:{os.getegid()}"
    with pytest.raises(ValueError,match="overlaps"):
        run(material,binding(material,[user]))
    assert not (material/"access").exists()


def test_private_authority_still_needs_owner_directory_execute(tmp_path):
    material,_=source(tmp_path)
    authority=tmp_path/"authority";authority.mkdir(mode=0o600)
    with pytest.raises(RuntimeAccessError):
        run(material,binding(authority,[f"{os.geteuid()}:{os.getegid()}"]))
    assert stat.S_IMODE(authority.stat().st_mode)==0o600


def test_fresh_sibling_access_root_is_supported(tmp_path):
    material,secret=source(tmp_path)
    result=establish_installer_runtime_access(binding=binding(secret,[f"{os.geteuid()}:{os.getegid()}"]),
        materialized_bind_root=material,target_root=tmp_path/"access",image_users={})
    assert result.receipt["status"]=="access_established"


def test_voice_policy_directory_gets_runtime_owned_copy_and_live_replacement(tmp_path):
    material,_=source(tmp_path)
    policy=tmp_path/'prepared-policy';policy.mkdir(mode=0o700)
    (policy/'policy.json').write_text('{"state":"enabled"}')
    bound=binding(policy,[f'{os.geteuid()}:{os.getegid()}'])
    bound.graph['services']['0']['volumes'][0]['target']='/run/eva/voice'
    result=run(material,bound)
    actual=Path(result.graph['services']['0']['volumes'][0]['source'])
    assert actual!=policy and actual.is_dir()
    assert actual.stat().st_uid==os.geteuid()
    replacement=actual/'new.json';replacement.write_text('{"state":"disabled"}')
    replacement.replace(actual/'policy.json')
    assert json.loads((actual/'policy.json').read_bytes())['state']=='disabled'
    assert json.loads((policy/'policy.json').read_bytes())['state']=='enabled'
    (actual/'policy.json').unlink();assert not (actual/'policy.json').exists()
