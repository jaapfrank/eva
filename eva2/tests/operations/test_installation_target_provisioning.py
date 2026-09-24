"""Controlled Docker backend verifies preparation mutations and failure receipts."""
import json
from copy import deepcopy
import pytest

from eva.operations.installer_runtime_inputs import InstallerRuntimeInputs, RuntimeInputReceipt
from tools.installation_target_provisioning import prepare_restore_target, TargetPreparationError


def fixture_inputs():
    return InstallerRuntimeInputs(RuntimeInputReceipt("a"*64,"b"*64,"c"*64,"d"*64,
        (("core","sha256:"+"1"*64),("pg","sha256:"+"2"*64)), ()), ())


def graph():
    services = {}
    for name, role in (("db","pg"),("schema","core"),("core","core")):
        services[name] = {"container_name":"new-target-"+name,"image":dict(fixture_inputs().receipt.images)[role],
            "restart":"no","networks":{"local":{}},"volumes":[]}
    services["db"].update(environment={"POSTGRES_USER":"postgres","POSTGRES_DB":"eva"},volumes=[{"type":"volume","source":"data","target":"/var/lib/postgresql/data"}],
                           healthcheck={"test":["CMD","pg_isready"]})
    services["schema"].update(entrypoint=["/usr/local/bin/python"],command=["-m","eva.operations.install_schema"])
    return {"services":services,"volumes":{"data":{"name":"new-target-data"}},
            "networks":{"local":{"name":"new-target-network","internal":True}}}


class Docker:
    def __init__(self, fail_schema=False, fail_create=False, occupied=False):
        self.calls=[]; self.resources={}; self.containers={}
        self.fail_schema=fail_schema; self.fail_create=fail_create; self.occupied=occupied

    def __call__(self, args, **kwargs):
        args=args[1:]; self.calls.append(args)
        if args[0]=="image" and args[1]=="inspect":
            return json.dumps([{"Id":args[2]}])
        if args[0] in {"volume","network"} and args[1]=="create":
            name=args[-1]; label=args[args.index("--label")+1].split("=",1)
            self.resources[name]={"Name":name,"Id":name,"Internal":True,"Labels":{label[0]:label[1]}}
            return name
        if args[0] in {"volume","network"} and args[1]=="inspect":
            return json.dumps([self.resources[args[-1]]])
        if args[0]=="compose":
            data=json.loads(open(args[args.index("--file")+1]).read())
            for name, spec in data["services"].items():
                if self.fail_create and len(self.containers)==2:
                    raise RuntimeError("simulated partial create")
                self.containers[spec["container_name"]]={"Id":name+"-id","Image":spec["image"],
                    "HostConfig":{"NetworkMode":"new-target-network"},
                    "NetworkSettings":{"Networks":{"new-target-network":{"NetworkID":""}}},
                    "Mounts":[{"Type":"volume","Name":data["volumes"][m["source"]]["name"],
                               "Destination":m["target"]} for m in spec["volumes"] if m["type"]=="volume"] +
                             [{"Type":"bind","Source":m["source"],"Destination":m["target"],
                               "RW":m.get("read_only") is not True}
                              for m in spec["volumes"] if m["type"]=="bind"],
                    "Config":{"Labels":spec["labels"]},"State":{"Running":False,"Status":"created"}}
            return ""
        if args[0]=="inspect":
            found=next((v for k,v in self.containers.items() if args[1] in {k,v["Id"]}),None)
            if found is None: raise RuntimeError("not present")
            return json.dumps([found])
        if args[0]=="start":
            found=next(v for v in self.containers.values() if v["Id"]==args[1])
            found["State"] = ({"Running":False,"Status":"exited","ExitCode":1 if self.fail_schema else 0}
                if args[1]=="schema-id" else {"Running":True,"Status":"running","Health":{"Status":"healthy"}})
            return args[1]
        if self.occupied and "--filter" in args: return "existing"
        return ""


def invoke(tmp_path, docker, launch_graph=None, dsn_text="postgresql://postgres@db/eva"):

    data = deepcopy(launch_graph or graph())
    dsn = tmp_path / "dsn"
    dsn.write_text(dsn_text)
    data["services"]["schema"]["environment"] = {"EVA_DATABASE_DSN_FILE":"/run/secrets/dsn"}
    data["services"]["schema"]["volumes"] = [{"type":"bind","source":str(dsn),
        "target":"/run/secrets/dsn","read_only":True,"bind":{"create_host_path":False}}]
    return prepare_restore_target(runtime_inputs=fixture_inputs(),launch_graph=data,
        namespace="new-target",image_roles={"db":"pg","schema":"core","core":"core"},
        database_services=("db",),schema_service="schema",workspace=tmp_path/"target",command=docker)


def test_persistent_resources_created_but_only_database_and_schema_started(tmp_path):
    docker=Docker(); result=invoke(tmp_path,docker)
    assert result["status"]=="prepared"
    assert [c for c in docker.calls if c[0]=="start"]==[["start","db-id"],["start","schema-id"]]
    assert docker.containers["new-target-core"]["State"]["Status"]=="created"
    assert result["volumes"]==[{"name":"new-target-data","id":"new-target-data"}]
    assert not any(c[0] in {"rm","stop"} for c in docker.calls)
    assert (tmp_path/"target"/"preparation-receipt.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure",["schema","create","occupied"])
def test_failure_preserves_exact_partial_resources_without_retry(tmp_path,failure):
    docker=Docker(fail_schema=failure=="schema",fail_create=failure=="create",occupied=failure=="occupied")
    with pytest.raises(TargetPreparationError) as caught: invoke(tmp_path,docker)
    receipt=caught.value.receipt
    assert receipt["status"]=="incomplete"
    assert len(receipt["containers"])==({"schema":3,"create":2,"occupied":0}[failure])
    assert json.loads((tmp_path/"target"/"preparation-receipt.json").read_text())==receipt
    assert not any(c[0] in {"rm","stop"} for c in docker.calls)


@pytest.mark.parametrize("change",["tmpfs","image","port","external","schema","restart"])
def test_unsafe_graph_rejected_before_any_docker_mutation(tmp_path,change):
    data=graph(); docker=Docker()
    if change=="tmpfs": data["services"]["db"]["tmpfs"]=["/var/lib/postgresql/data"]
    elif change=="image": data["services"]["core"]["image"]="unapproved"
    elif change=="port": data["services"]["core"]["ports"]=["80:80"]
    elif change=="external": data["volumes"]["data"]["external"]=True
    elif change=="schema": data["services"]["schema"]["command"]=["unrelated"]
    else: data["services"]["core"]["restart"]="always"
    with pytest.raises(ValueError): invoke(tmp_path,docker,data)
    assert docker.calls==[]


def test_socket_health_does_not_skip_final_tcp_readiness(tmp_path, monkeypatch):
    docker = Docker()
    attempts = []
    def delayed_tcp(args, **kwargs):
        if args[1:3] == ["exec", "db-id"]:
            attempts.append(args)
            if len(attempts) == 1:
                raise RuntimeError("init socket only")
        return docker(args, **kwargs)
    monkeypatch.setattr("tools.installation_target_provisioning.time.sleep", lambda _: None)
    result = invoke(tmp_path, delayed_tcp)
    assert result["schema_completed"] is True
    assert len(attempts) == 2
    assert attempts[0][3:] == ["pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "eva"]


@pytest.mark.parametrize("dsn", ["postgresql://postgres@old-live/eva", "postgresql://postgres@db/old",
                                "postgresql://postgres@db/eva?host=old-live"])
def test_schema_cannot_target_existing_database(tmp_path, dsn):
    docker = Docker()
    with pytest.raises(ValueError, match="declared target database"):
        invoke(tmp_path, docker, dsn_text=dsn)
    assert docker.calls == []


def test_writable_existing_bind_is_not_target_storage(tmp_path):
    data = graph()
    data["services"]["core"]["volumes"] = [{"type":"bind", "source":str(tmp_path),
        "target":"/data", "read_only":False, "bind":{"create_host_path":False}}]
    docker = Docker()
    with pytest.raises(ValueError, match="fresh target materialization"):
        invoke(tmp_path, docker, data)
    assert docker.calls == []


def test_schema_host_override_is_rejected_before_creation(tmp_path):
    data = graph()
    data["services"]["schema"]["extra_hosts"] = ["db:192.0.2.10"]
    docker = Docker()
    with pytest.raises(ValueError, match="maintained schema installer"):
        invoke(tmp_path, docker, data)
    assert docker.calls == []
