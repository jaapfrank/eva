import json
import os
import pytest
from eva.operations.installer_runtime_inputs import InstallerRuntimeInputs, RuntimeInputReceipt
import tools.installer_prepared_components as module


@pytest.fixture
def setup(tmp_path,monkeypatch):
    root=tmp_path/"prepared";resources=root/"resources";resources.mkdir(parents=True,mode=0o700)
    path=resources/"preparation-receipt.json"
    def write(path,value):path.write_text(json.dumps(value));path.chmod(0o600)
    write(root/"target-workflow.json",{"contract":"EvaInstallerTargetWorkflow/v1","status":"prepared", "namespace":"new-target",
        "plan_sha256":"a"*64,"preparation_receipt":str(path)})
    memory={"name":"new-memory","id":"1"*64,"image":"sha256:"+"2"*64}
    write(path,{"contract":"EvaRestoreTargetPreparation/v1","status":"prepared","schema_completed":True,"namespace":"new-target", "plan_sha256":"a"*64,
        "networks":[{"id":"3"*64,"name":"new-network"}],"containers":[{"service":"hindsight",**memory}]})
    runtime=InstallerRuntimeInputs(RuntimeInputReceipt("a"*64,"b"*64,"c"*64,"d"*64,
        (("gateway","sha256:"+"4"*64),("documents","sha256:"+"5"*64)),()),())
    monkeypatch.setattr(module,"_inspect_network",lambda _:None)
    monkeypatch.setattr(module,"_configured_network",lambda *a:None)
    monkeypatch.setattr(module,"_actual",lambda p:{"State":{"Running":False,"Pid":0,"Status":"created"},"HostConfig":{"RestartPolicy":{"Name":"no"}}})
    events=[]
    def documents(**kwargs):
        events.append(("documents",kwargs))
        return {"document_recovery":{"providers":[]},"document_connection":{"providers":[]},
                "document_gateway_binding":{"target_path":str(kwargs["gateway_database"]),"providers":[]},"document_write_access":{"providers":[]}}
    def gateway(**kwargs):
        events.append(("gateway",kwargs))
        pin={"name":kwargs["container_name"],"id":"6"*64,"image":"sha256:"+"4"*64}
        return {"status":"prepared_stopped","external_gateway":{"target_path":str(kwargs["workspace"]/"data/contextforge.sqlite")},
                "external_writer":pin,"gateway_start":{"container":pin,"network_id":kwargs["network_id"],"access_token_path":"/run/secrets/access-token"}}
    monkeypatch.setattr(module,"build_document_components",documents)
    monkeypatch.setattr(module,"prepare_installer_gateway_target",gateway)
    for key in ("jwt","encryption","admin","access_token"):
        file=tmp_path/key;file.write_text("synthetic-"+"a"*48);file.chmod(0o400)
    args=dict(runtime_inputs=runtime,prepared_workspace=root,package_root=tmp_path/"package",layouts=[{}],gateway_image_role="gateway",document_image_role="documents",
        credentials={k:tmp_path/k for k in ("jwt","encryption","admin","access_token")},
        runtime_owners={k:{"uid":os.geteuid(),"gid":os.getegid()} for k in ("core","memory","gateway","documents","config")},workspace=tmp_path/"components")
    return args,events


def test_existing_helpers_join_once_and_memory_identity_stays_for_config_builder(setup):
    args,events=setup
    result=module.prepare_installer_components(**args)
    assert [name for name,_ in events]==["documents","gateway"]
    config=result["component_configuration"]
    assert "memory" not in config["external_recovery"]
    assert [p["id"] for p in config["external_recovery"]["writers"]]==["6"*64,"1"*64]
    assert config["document_gateway_binding"]["target_path"]==config["external_recovery"]["gateway"]["target_path"]
    assert result["status"]=="prepared" and result["import_executed"] is False
    parent=args["workspace"]/"documents"
    assert parent.is_dir() and parent.stat().st_uid==os.geteuid()
    assert parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):module.prepare_installer_components(**args)
    assert [name for name,_ in events].count("gateway")==1


def test_gateway_failure_retains_durable_attempt_and_never_recreates(setup,monkeypatch):
    args,events=setup
    count=[]
    def failed(**kwargs):count.append(1);raise RuntimeError("retained gateway failure")
    monkeypatch.setattr(module,"prepare_installer_gateway_target",failed)
    with pytest.raises(module.PreparedComponentsError):module.prepare_installer_components(**args)
    assert json.loads((args["workspace"]/"components-receipt.json").read_text())["status"]=="needs_inspection"
    with pytest.raises(FileExistsError):module.prepare_installer_components(**args)
    assert count==[1]


def test_layout_failure_precedes_gateway_effects(setup,monkeypatch):
    args,events=setup
    def invalid(**kwargs):raise ValueError("invalid saved layout")
    monkeypatch.setattr(module,"build_document_components",invalid)
    with pytest.raises(ValueError):module.prepare_installer_components(**args)
    assert events==[] and not args["workspace"].exists()


def test_running_writer_prevents_gateway_creation(setup,monkeypatch):
    args,events=setup
    monkeypatch.setattr(module,"_actual",lambda p:{"State":{"Running":True,"Pid":42,"Status":"running"},"HostConfig":{"RestartPolicy":{"Name":"no"}}})
    with pytest.raises(ValueError,match="fenced"):module.prepare_installer_components(**args)
    assert events==[]


def test_document_config_owner_mismatch_prevents_gateway_effects(setup):
    args,events=setup
    args["runtime_owners"]["config"]={"uid":os.geteuid(),"gid":os.getegid()+1}
    with pytest.raises(ValueError):module.prepare_installer_components(**args)
    assert events==[] and not args["workspace"].exists()
