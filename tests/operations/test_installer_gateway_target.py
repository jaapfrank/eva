import json
from pathlib import Path
import pytest
from eva.operations.installer_runtime_inputs import InstallerRuntimeInputs, RuntimeInputReceipt
import tools.installer_gateway_target as module


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    credentials={}
    for key in ("jwt","encryption","admin","access_token"):
        path=tmp_path/key;path.write_text("private-"+"a"*48);path.chmod(0o400);credentials[key]=path
    calls=[];created={};network="1"*64;image="sha256:"+"2"*64
    def command(args,**kwargs):
        calls.append(args)
        if args[1:3]==["network","inspect"]:return json.dumps([{"Name":"prepared-network","Id":network,"Internal":True}])
        if args[1:3]==["image","inspect"]:return json.dumps([{"Id":image}])
        if args[1]=="ps":return ""
        if args[1]=="compose":
            graph=json.loads(Path(args[args.index("--file")+1]).read_text());service=graph["services"]["contextforge"]
            created.update(Id="3"*64,Image=image,Config={"Labels":service["labels"]},graph=graph)
            return ""
        if args[1]=="inspect":
            if not created:raise RuntimeError("absent")
            return json.dumps([created])
        raise AssertionError(args)
    monkeypatch.setattr(module,"_command",command)
    monkeypatch.setattr(module,"_inspect_network",lambda identifier: None)
    checked=[]
    monkeypatch.setattr(module,"_target",lambda *a,**kw:checked.append((a,kw)))
    runtime=InstallerRuntimeInputs(RuntimeInputReceipt("a"*64,"b"*64,"c"*64,"d"*64,(("gateway",image),),()),())
    args=dict(runtime_inputs=runtime,image_role="gateway",network_id=network,container_name="fresh-gateway",
              workspace=tmp_path/"target",package_root=tmp_path/"package",credentials=credentials)
    return args,calls,created,checked


def test_creates_stopped_gateway_from_maintained_script_and_preserves_target_path(fixture):
    args,calls,created,checked=fixture
    result=module.prepare_installer_gateway_target(**args)
    assert result["status"]=="prepared_stopped" and result["started"] is False
    assert result["gateway_start"]["container"]==result["external_writer"]
    assert result["external_gateway"]["target_path"]==str(args["workspace"]/"data/contextforge.sqlite")
    assert not (args["workspace"]/"data/contextforge.sqlite").exists()
    assert checked[0][1]=={"running":False}
    service=created["graph"]["services"]["contextforge"]
    assert service["entrypoint"]==["/bin/sh"] and service["command"]==["/run/eva2/start-contextforge.sh"]
    assert service["healthcheck"]["test"][0]=="CMD"
    assert service["environment"]["ENABLE_HEADER_PASSTHROUGH"]=="false"
    assert not any(call[1] in {"start","stop","rm"} for call in calls)
    assert "private-" not in repr(result)


def test_existing_name_is_not_adopted(fixture,monkeypatch):
    args,calls,_,_=fixture
    original=module._command
    def occupied(cmd,**kwargs):
        if cmd[1]=="ps":return "fresh-gateway"
        return original(cmd,**kwargs)
    monkeypatch.setattr(module,"_command",occupied)
    with pytest.raises(ValueError,match="already exists"):module.prepare_installer_gateway_target(**args)
    assert not args["workspace"].exists()


def test_existing_target_workspace_is_not_reused(fixture):
    args,_,_,_=fixture
    args["workspace"].mkdir(mode=0o700)
    with pytest.raises(FileExistsError):module.prepare_installer_gateway_target(**args)


def test_failed_postcreate_validation_retains_exact_container_receipt(fixture,monkeypatch):
    args,calls,_,_=fixture
    def reject(*a,**kw):raise ValueError("controlled actual target rejection")
    monkeypatch.setattr(module,"_target",reject)
    with pytest.raises(module.GatewayTargetPreparationError) as caught:module.prepare_installer_gateway_target(**args)
    assert caught.value.receipt["container"]["id"]=="3"*64
    assert caught.value.receipt["status"]=="incomplete"
    assert json.loads((args["workspace"]/"gateway-target-receipt.json").read_text())==caught.value.receipt
    assert not any(call[1] in {"start","stop","rm"} for call in calls)


def test_package_credentials_cannot_be_treated_as_current_authority(fixture):
    args,_,_,_=fixture
    args["package_root"]=args["credentials"]["jwt"].parent
    with pytest.raises(ValueError,match="separately provisioned"):module.prepare_installer_gateway_target(**args)
    assert not args["workspace"].exists()
