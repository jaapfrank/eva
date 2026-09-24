"""Controlled fresh component composition; no real containers or provider calls."""
from copy import deepcopy
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from eva.contracts.canonical_json import canonical_sha256
from tools import installation_restore_verification as subject


@pytest.fixture
def documents(monkeypatch,tmp_path):
    catalog = [{"name":"read"}]
    prep = {"image":"image", "logical_catalog_sha256":canonical_sha256(catalog),"project_keys":["project"]}
    package = {"archive_sha256":"archive"}
    old = {"container_name":"old","container_id":"old-id","gateway_alias":"documents"}
    promoted = {"preparation_index":0,"old_container_id":"old-id","old_container_name":"old",
                "container_name":"new","container_id":"new-id","image_id":"image","gateway_alias":"documents"}
    def receipt(contract,status,providers):
        return {"contract":contract,"status":status,"archive_sha256":"archive","providers":providers}
    args = dict(package_root=tmp_path,
        preparation_configuration={"providers":[prep]},
        connection_configuration={"providers":[{"preparation_index":0,"container_name":"old","network_id":"network"}]},
        promotion_configuration={"providers":[{"preparation_index":0,"container_name":"new"}]},
        preparation_receipt=receipt("EvaDocumentRelocationPreparation/v1","prepared_not_connected",[{}]),
        connection_receipt=receipt("EvaDocumentConnection/v1","connected_read_only",[old]),
        gateway_receipt=receipt("EvaRestoredGatewayStart/v1","gateway_started_verified",[]),
        promotion_receipt=receipt("EvaDocumentWritePromotion/v1","writable_verified",[promoted]))
    calls=[]
    args["guard"] = lambda:calls.append("guard")
    monkeypatch.setattr(subject,"validate_document_promotion_configuration",lambda *args,**kwargs:None)
    monkeypatch.setattr(subject,"read_installation_package",lambda root:package)
    monkeypatch.setattr(subject,"_input_mounts",lambda *args:{"mount":"exact"})
    monkeypatch.setattr(subject,"_inspect_network",lambda identifier:calls.append("network"))
    monkeypatch.setattr(subject,"_verify_running",lambda *args,**kwargs:calls.append("running"))
    monkeypatch.setattr(subject,"_verify_stopped",lambda *args,**kwargs:calls.append("old_stopped"))
    monkeypatch.setattr(subject,"_validate_material_report",lambda *args:calls.append("material_verified"))
    def command(command,**kwargs):
        assert command[:3] == ["docker","exec","new-id"]
        calls.append("catalog" if command[3]=="python3" else "material")
        return json.dumps(catalog if command[3]=="python3" else {"verified":True})
    monkeypatch.setattr(subject,"_command",command)
    return args,calls


def test_promoted_documents_are_reinspected_not_accepted_from_old_receipt(documents):
    args,calls=documents
    result=subject.verify_promoted_documents(**args)
    assert result["all_verified"] is True
    assert calls.count("running")==calls.count("old_stopped")==2
    assert calls.count("catalog")==calls.count("material")==calls.count("material_verified")==1


@pytest.mark.parametrize("change",["archive","id","coverage","catalog","material","old_running"])
def test_changed_promoted_documents_fail_closed(documents,monkeypatch,change):
    args,calls=documents
    if change=="archive":args["promotion_receipt"]["archive_sha256"]="different"
    elif change=="id":args["promotion_receipt"]["providers"][0]["old_container_id"]="different"
    elif change=="coverage":args["promotion_receipt"]["providers"].clear()
    elif change=="catalog":args["preparation_configuration"]["providers"][0]["logical_catalog_sha256"]="different"
    else:
        name="_validate_material_report" if change=="material" else "_verify_stopped"
        monkeypatch.setattr(subject,name,lambda *args,**kwargs:(_ for _ in ()).throw(ValueError("changed")))
    with pytest.raises(ValueError):subject.verify_promoted_documents(**args)


@pytest.fixture
def composition(monkeypatch,tmp_path):
    from tools import installation_memory_verification as memory
    from tools import installation_gateway_verification as gateway
    from tools import restore_runtime_guard
    # Native authorization is a separate reviewed module; this fixture isolates
    # the composition boundary rather than claiming native validation coverage.
    native=ModuleType("tools.installation_runtime_authorization")
    state={"failed":None,"calls":[]}
    def result(kind,**values):
        state["calls"].append(kind)
        if state["failed"]==kind:raise ValueError("component failed")
        return values
    native.verify_runtime_authorization=lambda *args,**kwargs:result("native",contract="EvaRestoredRuntimeAuthorization/v1",scope="locally_authorized_target_bindings",verified=True,archive_sha256="archive",restore_id="restore",target_database_oid=17)
    native.validate_runtime_authorization_configuration=lambda config:None
    monkeypatch.setitem(sys.modules,"tools.installation_runtime_authorization",native)
    monkeypatch.setattr(subject,"validate_restore_verification_configuration",lambda config:None)
    monkeypatch.setattr(memory,"verify_memory_data",lambda *args,**kwargs:result("memory",contract="EvaRestoredMemoryDataVerification/v1",archive_sha256="archive",target_binding_sha256=canonical_sha256({})))
    monkeypatch.setattr(gateway,"verify_gateway_providers",lambda *args,**kwargs:result("gateway",all_verified=True,archive_sha256="archive"))
    monkeypatch.setattr(subject,"verify_promoted_documents",lambda *args,**kwargs:result("documents",all_verified=True,archive_sha256="archive"))
    monkeypatch.setattr(restore_runtime_guard,"verify_restore_runtime",lambda *args,**kwargs:state["calls"].append("guard"))
    manifest={"source_tenant_id":"tenant","source_installation_id":"installation",
              "runtime_identity":{"tenant_id":"tenant","installation_id":"installation"},
              "source_components":{"memory":["memory"],"gateway":["gateway"],"documents":["documents"]}}
    monkeypatch.setattr(subject,"read_installation_package",lambda root:{"archive_sha256":"archive","manifest":manifest})
    hold={"restore_id":"restore","held":True,"same_database":True,"archive_sha256":"archive","target_database_oid":17,
          "tenant_id":"tenant","installation_id":"installation","runtime_tenant_id":"tenant","runtime_installation_id":"installation"}
    transaction=SimpleNamespace(is_active=True)
    class Connection:
        def in_transaction(self):return transaction.is_active
        def in_nested_transaction(self):return False
        def get_transaction(self):return transaction
        def execute(self,query):return self
        def mappings(self):return self
        def one(self):return hold
    configuration={key:{} for key in ("runtime","external","gateway","binding","document_preparation","document_connection","document_promotion","native_authorization")}
    configuration["runtime"]={"target_database":{"oid":17}}
    configuration["external"]={"memory":{}}
    args=dict(connection=Connection(),package_root=tmp_path,restore_id="restore",configuration=configuration,
              preparation_receipt={},connection_receipt={},gateway_receipt={},promotion_receipt={},guard=lambda:None)
    return args,state,hold,transaction,manifest


def test_component_release_receipt_requires_all_fresh_checks(composition):
    args,state,_,_,manifest=composition
    receipt=subject.verify_restored_installation(**args)
    assert receipt["contract"]=="EvaRestoreActivationComponents/v1"
    assert receipt["verified_components"]=={key:canonical_sha256(value) for key,value in manifest["source_components"].items()}
    assert [value for value in state["calls"] if value!="guard"]==["native","memory","documents","gateway","native"]
    assert len(receipt["verified_requirements"])==4


@pytest.mark.parametrize("failed",["native","memory","documents","gateway","native"])
def test_any_failed_component_prevents_release_receipt(composition,failed):
    args,state,*_=composition
    state["failed"]=failed
    with pytest.raises(ValueError):subject.verify_restored_installation(**args)


@pytest.mark.parametrize("change",["held","archive_sha256","restore_id","runtime_tenant_id"])
def test_changed_restore_binding_prevents_component_calls(composition,change):
    args,state,hold,*_=composition
    hold[change]=False if change=="held" else "other"
    with pytest.raises(ValueError):subject.verify_restored_installation(**args)
    assert state["calls"]==["guard"]


def test_nontransactional_invocation_is_rejected(composition):
    args,state,_,transaction,_=composition
    transaction.is_active=False
    with pytest.raises(ValueError):subject.verify_restored_installation(**args)
    assert not state["calls"]


def test_native_authorization_change_during_other_checks_prevents_release(composition, monkeypatch):
    args,state,*_=composition
    native = sys.modules["tools.installation_runtime_authorization"]
    original = native.verify_runtime_authorization
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        if state["calls"].count("native") > 1:
            value["authorized_profiles"] = {"target":"changed"}
        return value
    monkeypatch.setattr(native,"verify_runtime_authorization",changed)
    with pytest.raises(ValueError,match="changed during"):
        subject.verify_restored_installation(**args)
