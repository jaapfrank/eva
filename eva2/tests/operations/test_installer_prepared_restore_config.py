import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from sqlalchemy.engine import make_url

import tools.installer_prepared_restore_config as module


@pytest.fixture
def setup(tmp_path,monkeypatch):
    root=tmp_path/"prepared";root.mkdir(mode=0o700)
    resources=root/"resources";resources.mkdir(mode=0o700)
    source={"core_name":"approved-source","workers":[{"name":"approved-source","id":"9"*64,"image":"sha256:"+"8"*64}]}
    pins=[{"service":name,"name":"new-"+name,"id":str(i)*64,"image":"sha256:"+str(i)*64}
          for i,name in enumerate(("core","durable-goal-worker","telegram-ingress","postgres","hindsight-postgres","hindsight"),1)]
    prepared={"contract":"EvaRestoreTargetPreparation/v1","status":"prepared","schema_completed":True,"namespace":"new-target","plan_sha256":"a"*64,"containers":pins}
    services={name:{"networks":{"local":{}},"environment":{"POSTGRES_DB":db,"POSTGRES_USER":"postgres"}}
              for name,db in (("postgres","eva"),("hindsight-postgres","hindsight"))}
    graph={"name":"new-target","services":services}
    def save(path,value):path.write_text(json.dumps(value));path.chmod(0o600)
    save(resources/"compose.json",graph);save(resources/"preparation-receipt.json",prepared)
    save(root/"target-workflow.json",{"contract":"EvaInstallerTargetWorkflow/v1","status":"prepared", "namespace":"new-target", "plan_sha256":"a"*64,
                                    "preparation_receipt":str(resources/"preparation-receipt.json")})
    private=tmp_path/"dsn";private.write_text("postgresql+psycopg://postgres:private-test@postgres:5432/eva");private.chmod(0o600)
    rows={}
    for pin in pins:
        env=services.get(pin["service"],{}).get("environment",{})
        rows[pin["id"]]={"Id":pin["id"],"Name":"/"+pin["name"],"Image":pin["image"],"State":{"Running":pin["service"].endswith("postgres")},
            "Config":{"Env":[k+"="+v for k,v in env.items()]},"Mounts":[]}
    rows[pins[0]["id"]]["Config"]["Env"]=["EVA_DATABASE_DSN_FILE=/run/secrets/database_dsn"]
    rows[pins[0]["id"]]["Mounts"]=[{"Source":str(private),"Destination":"/run/secrets/database_dsn","Type":"bind","RW":False}]
    identities={"eva":{"name":"eva","oid":10,"system_identifier":"100"},"hindsight":{"name":"hindsight","oid":20,"system_identifier":"200"}}
    def command(args,**kwargs):
        if args[1]=="inspect":return json.dumps([rows[args[2]]])
        assert args[1]=="exec"
        return json.dumps(identities[args[args.index("-d")+1]])
    calls=[]
    def capture(connection,**kwargs):
        calls.append(kwargs)
        return {"source":deepcopy(kwargs["approved_source"]),"target":{"core_name":"new-core","workers":[]},"target_database":deepcopy(identities["eva"])}
    monkeypatch.setattr(module,"_command",command)
    monkeypatch.setattr(module,"capture_prepared_restore_runtime",capture)
    dsn="postgresql+psycopg://postgres:private-test@192.0.2.1:5432/eva"
    args=dict(preparation_workspace=root,connection=SimpleNamespace(engine=SimpleNamespace(url=make_url(dsn))),target_dsn=dsn,
        approved_source=source,authority=tmp_path/"approved-authority",workspace=tmp_path/"restore",objects=tmp_path/"objects",keyring=None)
    return args,rows,calls,private


def test_actual_prepared_pins_bind_existing_core_restore_config(setup):
    args,rows,calls,_=setup
    result=module.build_prepared_restore_config(**args)
    assert result.config["runtime_guard"]["source"]==args["approved_source"]
    assert calls[0]["writer_services"]==("core","durable-goal-worker","telegram-ingress")
    assert "private-test" not in repr(result)
    assert result.receipt["memory_target"]["database"]["name"]=="hindsight"
    assert "external_recovery" in result.pending and "activation" in result.pending
    assert result.config["traffic_closed"] is True
    assert result.receipt["restore_executed"] is False


@pytest.mark.parametrize("change",["dsn","container","mounted_dsn","reserved","stale_memory"])
def test_wrong_target_or_override_is_rejected(setup,change):
    args,rows,_,private=setup
    if change=="dsn":args["target_dsn"]="postgresql+psycopg://postgres@other/eva"
    elif change=="container":rows["1"*64]["Image"]="sha256:"+"f"*64
    elif change=="mounted_dsn":private.write_text("postgresql://postgres@old-source/eva")
    elif change=="reserved":args["component_configuration"]={"runtime_guard":{}}
    else:args["component_configuration"]={"external_recovery":{"memory":{"container":"old-target"}}}
    with pytest.raises(ValueError):module.build_prepared_restore_config(**args)


def test_supplied_database_connection_must_match_prepared_lineage(setup,monkeypatch):
    args,_,_,_=setup
    monkeypatch.setattr(module,"capture_prepared_restore_runtime",lambda *a,**kw:{"target_database":{"name":"eva","oid":999,"system_identifier":"old"}})
    with pytest.raises(ValueError,match="lineage"):module.build_prepared_restore_config(**args)


def test_memory_facts_fill_only_missing_memory_without_changing_caller(setup):
    args,_,_,_=setup
    explicit={"external_recovery":{"gateway":{"target_path":"/private/new.sqlite"},"writers":[]}}
    args["component_configuration"]=explicit
    result=module.build_prepared_restore_config(**args)
    assert result.config["external_recovery"]["memory"]["container"]["id"]=="5"*64
    assert "memory" not in explicit["external_recovery"]


def test_changed_preparation_plan_cannot_mix_with_workflow_receipt(setup):
    args,_,_,_=setup
    path=args["preparation_workspace"]/"resources/preparation-receipt.json"
    prepared=json.loads(path.read_text())
    prepared["plan_sha256"]="b"*64
    path.write_text(json.dumps(prepared))
    with pytest.raises(ValueError,match="plan differs"):
        module.build_prepared_restore_config(**args)


def activation_inputs(args):
    path = args["preparation_workspace"] / "resources/preparation-receipt.json"
    receipt = json.loads(path.read_text())
    receipt["networks"] = [{"name": "new-network", "id": "a" * 64}]
    path.write_text(json.dumps(receipt))
    pin = lambda digit, name: {"name": name, "id": digit * 64, "image": "sha256:" + digit * 64}
    source = pin("8", "old-memory")
    args["component_configuration"] = {"external_recovery": {
        "gateway": {"target_path": "/private/new.sqlite"},
        "writers": [pin("6", "new-hindsight"), source]}}
    args["activation_settings"] = {"model_authority_file": "/private/model-authority",
        "writers": {name: {"profile_sha256": "b" * 64, "model_lease_path": "/run/model/auth.json", "credential_files": []}
                    for name in ("core", "durable-goal-worker", "telegram-ingress")},
        "memory_profile_sha256": "c" * 64, "source_memory_writers": [source]}


def test_activation_resolves_new_ids_without_replacing_approved_profiles(setup):
    args, _, _, _ = setup
    activation_inputs(args)
    original = deepcopy(args["activation_settings"])
    result = module.build_prepared_restore_config(**args)
    activation = result.config["activation"]
    assert [x["id"] for x in activation["native_authorization"]["writers"]] == [str(i) * 64 for i in (1, 2, 3)]
    assert activation["memory_api"]["container"]["id"] == "6" * 64
    assert activation["memory_api"]["profile_sha256"] == "c" * 64
    assert args["activation_settings"] == original
    assert "activation" not in result.pending


def test_activation_cannot_omit_a_prepared_writer(setup):
    args, _, _, _ = setup
    activation_inputs(args)
    del args["activation_settings"]["writers"]["telegram-ingress"]
    with pytest.raises(ValueError, match="coverage"):
        module.build_prepared_restore_config(**args)
