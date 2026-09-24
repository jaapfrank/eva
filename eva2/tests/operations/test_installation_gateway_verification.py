"""Controlled discovery receipts only; no real gateway requests."""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json

import pytest

from tools import installation_gateway_verification as subject


@pytest.fixture
def configured(monkeypatch, tmp_path):
    raw = b"snapshot"
    package = {"archive_sha256":"archive", "manifest":{"files":{"gateway/contextforge.sqlite":{"size":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}},"source_components":{"gateway":[{"image_sha256":"a"*64}]}}}
    catalog = {"gateways":[{"id":"gateway", "url_sha256":"url", "transport":"STREAMABLEHTTP"}], "tools":[{"id":"tool", "gateway_id":"gateway", "original_name":"read", "input_schema":{"type":"object"}, "output_schema":None}]}
    counts = {kind+"_"+action:0 for kind in ("tools","resources","prompts") for action in ("added","updated","removed")}
    counts["tools_updated"] = 1
    probe = {"gateway_id":"gateway","success":True,"fresh":True,"validation_clean":True,"counts":counts,"refreshed_at":"2026-09-24T12:00:00+00:00"}
    state = {"catalog":catalog,"probe":probe,"calls":[],"checks":[]}
    monkeypatch.setattr(subject,"validate_gateway_start_configuration",lambda *args,**kwargs:None)
    monkeypatch.setattr(subject,"read_installation_package",lambda root:package)
    monkeypatch.setattr(subject,"_root_descriptor",lambda root:nullcontext(1))
    monkeypatch.setattr(subject,"_read_regular",lambda *args,**kwargs:raw)
    monkeypatch.setattr(subject,"_catalog",lambda raw:deepcopy(catalog))
    monkeypatch.setattr(subject,"_inspect_network",lambda identifier:state["checks"].append("network"))
    monkeypatch.setattr(subject,"_target",lambda *args,**kwargs: (state["checks"].append("target") or {"Config":{"Env":state.get("environment",[])}}))
    def command(args,**kwargs):
        assert args[:2] == ["docker","exec"] and args[2] == "target-pin"
        assert args[3:5] == ["python3","-c"]
        state["calls"].append(args[5])
        if args[5] == subject._REFRESH:
            assert args[7:] == ["gateway"]
            if state.get("command_error"): raise RuntimeError("unknown refresh outcome")
            return json.dumps(state["probe"])
        assert args[5] == subject._CATALOG
        observed = deepcopy(state["catalog"])
        if state.get("changed") and subject._REFRESH in state["calls"]:
            observed["tools"][0]["input_schema"] = {"type":"string"}
        return json.dumps(observed)
    monkeypatch.setattr(subject,"_command",command)
    arguments = dict(package_root=tmp_path, configuration={"container":{"id":"target-pin","name":"target","image":"sha256:"+"a"*64},"network_id":"network","access_token_path":"/run/token"}, external_configuration={},binding_configuration={"target_path":str(tmp_path/"db")},guard=lambda:state["checks"].append("guard"))
    return arguments,state


def test_fresh_discovery_preserves_catalog_and_reports_metadata_updates(configured):
    arguments,state = configured
    receipt = subject.verify_gateway_providers(**arguments)
    assert receipt["all_verified"] is True
    assert receipt["tool_calls_executed"] is False
    assert receipt["catalog_metadata_may_change"] is True
    assert receipt["probes"][0]["counts"]["tools_updated"] == 1
    assert state["calls"] == [subject._CATALOG,subject._REFRESH,subject._CATALOG]


@pytest.mark.parametrize("failure",["success","fresh","validation_clean","added","schema","command"])
def test_failed_or_ambiguous_discovery_is_not_accepted_or_retried(configured,failure):
    arguments,state = configured
    if failure in {"success","fresh","validation_clean"}:state["probe"][failure] = False
    elif failure == "added":state["probe"]["counts"]["tools_added"] = 1
    elif failure == "schema":state["changed"] = True
    else:state["command_error"] = True
    with pytest.raises(subject.GatewayProviderVerificationError) as error:
        subject.verify_gateway_providers(**arguments)
    assert error.value.receipt["all_verified"] is False
    assert error.value.receipt["refresh_attempted"] == ["gateway"]
    assert state["calls"].count(subject._REFRESH) == 1


def test_guard_failure_prevents_discovery(configured):
    arguments,state = configured
    arguments["guard"] = lambda: (_ for _ in ()).throw(ValueError("source unfenced"))
    with pytest.raises(subject.GatewayProviderVerificationError):
        subject.verify_gateway_providers(**arguments)
    assert not state["calls"]


def test_embedded_scripts_compile_and_use_maintained_refresh_only():
    compile(subject._CATALOG,"catalog","exec")
    compile(subject._REFRESH,"refresh","exec")
    assert "/tools/refresh?include_resources=false&include_prompts=false" in subject._REFRESH
    assert "tools/call" not in subject._REFRESH
    assert "_initialize_gateway(" not in subject._REFRESH
    assert "authorization" not in subject._REFRESH.lower().split("print(json.dumps(")[-1]


def test_header_passthrough_cannot_substitute_admin_token_for_upstream_identity(configured):
    arguments,state = configured
    state["environment"] = ["ENABLE_HEADER_PASSTHROUGH=true"]
    with pytest.raises(subject.GatewayProviderVerificationError):
        subject.verify_gateway_providers(**arguments)
    assert not state["calls"]
