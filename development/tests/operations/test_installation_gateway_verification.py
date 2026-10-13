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
    catalog = {"gateways":[{"id":"gateway", "url_sha256":"url", "transport":"STREAMABLEHTTP", "enabled":True}], "tools":[{"id":"tool", "gateway_id":"gateway", "original_name":"read", "input_schema":{"type":"object"}, "output_schema":None, "enabled":True}]}
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
            assert args[7:] == ["gateway", "preserve_disabled" if state["catalog"]["tools"][0]["enabled"] is False else "allow_reactivation"]
            if state.get("command_error"): raise RuntimeError("unknown refresh outcome")
            return json.dumps(state["probe"])
        if args[5] == subject._SUSPEND:
            assert args[7:] == ["gateway"]
            state["catalog"]["gateways"][0]["enabled"] = False
            if not state.get("unsafe_tool"):
                state["catalog"]["tools"][0]["enabled"] = False
            return json.dumps({"gateway_id":"gateway","disabled":True,"success":True})
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
    assert state["calls"] == [subject._CATALOG,subject._CATALOG,subject._REFRESH,subject._CATALOG,subject._CATALOG]


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
    compile(subject._SUSPEND,"suspend","exec")
    assert "/tools/refresh?include_resources=false&include_prompts=false" in subject._REFRESH
    assert "src/tools/call" not in subject._REFRESH
    assert "_initialize_gateway(" not in subject._REFRESH
    assert "authorization" not in subject._REFRESH.lower().split("print(json.dumps(")[-1]


def test_explicit_suspension_retains_catalog_and_never_refreshes_offline_provider(configured):
    arguments,state = configured
    original = deepcopy(state["catalog"])
    receipt = subject.verify_gateway_providers(**arguments,suspended_gateway_ids=["gateway"])
    assert receipt["all_verified"] is True
    assert receipt["suspended_gateway_ids"] == ["gateway"]
    assert receipt["refresh_attempted"] == []
    assert state["calls"].count(subject._SUSPEND) == 1
    for kind in ("gateways","tools"):
        for item in original[kind]:item["enabled"] = False
    assert state["catalog"] == original


def test_already_suspended_target_is_observed_without_repeating_state_change(configured,monkeypatch):
    arguments,state = configured
    # The source projection remains enabled, but a previous target attempt
    # already completed its disable transition before another check failed.
    original = deepcopy(state["catalog"])
    monkeypatch.setattr(subject,"_catalog",lambda raw: deepcopy(original))
    for kind in ("gateways","tools"):
        state["catalog"][kind][0]["enabled"] = False
    receipt = subject.verify_gateway_providers(**arguments,suspended_gateway_ids=["gateway"])
    assert receipt["all_verified"] is True
    assert subject._SUSPEND not in state["calls"]
    assert subject._REFRESH not in state["calls"]


def test_disabling_gateway_without_disabling_its_tools_is_rejected(configured):
    arguments,state = configured
    state["unsafe_tool"] = True
    with pytest.raises(subject.GatewayProviderVerificationError):
        subject.verify_gateway_providers(**arguments,suspended_gateway_ids=["gateway"])
    assert subject._REFRESH not in state["calls"]


@pytest.mark.parametrize("selection", [["absent"],["gateway","gateway"],[None],"gateway"])
def test_unknown_or_malformed_selection_cannot_skip_discovery(configured,selection):
    arguments,state = configured
    with pytest.raises(subject.GatewayProviderVerificationError):
        subject.verify_gateway_providers(**arguments,suspended_gateway_ids=selection)
    assert not state["calls"]


def test_header_passthrough_cannot_substitute_admin_token_for_upstream_identity(configured):
    arguments,state = configured
    state["environment"] = ["ENABLE_HEADER_PASSTHROUGH=true"]
    with pytest.raises(subject.GatewayProviderVerificationError):
        subject.verify_gateway_providers(**arguments)
    assert not state["calls"]


def test_reachable_gateway_with_disabled_tool_does_not_allow_bulk_reactivation(configured):
    arguments,state = configured
    state["catalog"]["tools"][0]["enabled"] = False
    receipt = subject.verify_gateway_providers(**arguments)
    assert receipt["all_verified"] is True
    assert state["catalog"]["tools"][0]["enabled"] is False


@pytest.mark.parametrize("permit", [True, False])
def test_native_refresh_reconnects_unreachable_gateway_only_without_disabled_tools(permit):
    """Execute the real embedded transition against a controlled native client."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import urllib.request
    import urllib.parse

    gateway = {"enabled": True, "reachable": False, "lastRefreshAt": None}
    requests = []
    class Response:
        def __init__(self, value): self.value = value
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return json.dumps(self.value).encode()
    class Client:
        def open(self, request, timeout):
            requests.append(request.full_url)
            if request.full_url.endswith("/state?activate=true"):
                gateway["reachable"] = True
                return Response({"status": "success"})
            assert gateway["reachable"] is True
            gateway["lastRefreshAt"] = datetime.now(timezone.utc).isoformat()
            counts = {kind+"_"+action: 0 for kind in ("tools", "resources", "prompts") for action in ("added", "updated", "removed")}
            return Response({"success": True, "validation_errors": [], **counts})
    output = []
    environment = {"sys": SimpleNamespace(argv=["-c", "/run/token", "gateway", "allow_reactivation" if permit else "preserve_disabled"]),
                   "json": json, "urllib": __import__("urllib"), "client": Client(), "token": "private-test-token",
                   "get": lambda route: deepcopy(gateway), "print": lambda value: output.append(json.loads(value))}
    script = subject._REFRESH[len(subject._BASE):]
    if not permit:
        with pytest.raises(RuntimeError, match="retained tool activation"):
            exec(compile(script, "native-refresh", "exec"), environment)
        assert requests == [] and gateway["reachable"] is False
    else:
        exec(compile(script, "native-refresh", "exec"), environment)
        assert len(requests) == 2
        assert output[0]["fresh"] is True and output[0]["reactivated"] is True
