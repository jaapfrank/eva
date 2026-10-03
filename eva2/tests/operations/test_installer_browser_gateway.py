"""Selected browser access binds actual tools without gateway administration."""
from copy import deepcopy
import json
import pytest
from tools.installer_browser_gateway import browser_catalog, prepare_browser_gateway_files


def row(name, properties=None):
    return {"name": "eva_browser_" + name, "originalName": name,
        "inputSchema": {"type": "object", "properties": properties or {}},
        "description": "A browser operation", "enabled": True, "reachable": True,
        "integrationType": "MCP", "requestType": "POST", "visibility": "public"}


def test_browser_catalog_keeps_actual_arguments_and_does_not_claim_readback():
    rows = [row("browser_snapshot"), row("browser_navigate", {"url": {"type": "string"}})]
    original = deepcopy(rows)
    catalog = browser_catalog(rows)
    assert rows == original
    assert {tool["eva_name"] for tool in catalog["tools"]} == {"browser_snapshot", "browser_navigate"}
    assert all(tool["operation_kind"] == "invoke" and not tool["proof_bindings"] for tool in catalog["tools"])
    assert catalog["tools"][1]["execute_argument_bindings"] == [{"argument_name": "url", "provider_name": "url"}]
    assert catalog["tools"][0]["input_schema"]["required"] == []


@pytest.mark.parametrize("problem", ["unreachable", "duplicate", "missing_navigation"])
def test_changed_or_incomplete_browser_cannot_be_admitted(problem):
    rows = [row("browser_snapshot"), row("browser_navigate")]
    if problem == "unreachable":
        rows[0]["reachable"] = False
    elif problem == "duplicate":
        rows.append(deepcopy(rows[0]))
    else:
        rows.pop()
    with pytest.raises((ValueError, RuntimeError)):
        browser_catalog(rows)


def test_unselected_package_images_cannot_create_optional_browser_files(tmp_path):
    with pytest.raises(ValueError, match="exact inspected"):
        prepare_browser_gateway_files(package_root=tmp_path, workspace=tmp_path,
            namespace="new-eva", browser_plan={}, gateway_port=44000, images={})
    assert not (tmp_path / "browser-gateway").exists()


@pytest.mark.parametrize("problem", [None, "wrong_subnet", "wrong_owner", "missing_attachment", "wrong_network_id", "unpublished"])
def test_gateway_binds_only_existing_checked_browser_networks(tmp_path, monkeypatch, problem):
    from types import SimpleNamespace
    from tools import installer_browser_gateway as module
    namespace = "new-eva"
    internal_id, egress_id = "a" * 64, "b" * 64
    internal_name, egress_name = namespace + "-net", namespace + "-egress"
    internal = {"Id": internal_id, "Name": internal_name, "Internal": True}
    egress = {"Id": egress_id, "Name": egress_name, "Internal": False,
        "Labels": {"io.eva2.browser-candidate": namespace},
        "IPAM": {"Config": [{"Subnet": "10.254.4.0/24"}]}}
    if problem == "wrong_subnet":
        egress["IPAM"]["Config"][0]["Subnet"] = "10.254.5.0/24"
    if problem == "wrong_owner":
        egress["Labels"]["io.eva2.browser-candidate"] = "other-eva"
    image = "sha256:" + "c" * 64
    plan = {"namespace": namespace, "workspace": str(tmp_path), "package_root": str(tmp_path),
        "gateway_name": namespace + "-browser-gateway", "image": "image@" + image,
        "gateway_host": "10.254.3.1", "gateway_port": 45621, "browser_address": "10.254.3.2",
        "browser_plan": {"internal_network": internal_name, "egress_subnet": "10.254.4.0/24", "mcp_url": "http://10.254.3.2:8931/mcp"},
        "token_file": str(tmp_path / "token"), "catalog_file": str(tmp_path / "catalog.json")}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets/admin").write_text("fixture")
    actual = {"Id": "d" * 64, "Image": image, "NetworkSettings": {
        "Networks": {internal_name: {"NetworkID": internal_id}, egress_name: {"NetworkID": egress_id}},
        "Ports": {"4444/tcp": [{"HostIp": "10.254.3.1", "HostPort": "45621"}]}}}
    if problem == "missing_attachment":
        actual["NetworkSettings"]["Networks"].pop(egress_name)
    if problem == "wrong_network_id":
        actual["NetworkSettings"]["Networks"][egress_name]["NetworkID"] = "e" * 64
    if problem == "unpublished":
        actual["NetworkSettings"]["Ports"]["4444/tcp"] = None
    calls, waits = [], []
    def inspect(*args):
        if args[:2] == ("image", "inspect"):
            return [{"Id": image}]
        if args[:2] == ("network", "inspect"):
            return [internal if args[2] == internal_id else egress]
        assert args == ("inspect", plan["gateway_name"])
        return [actual]
    rows = [dict(row("browser_snapshot"), gateway_id="gateway"), dict(row("browser_navigate"), gateway_id="gateway")]
    helper = SimpleNamespace(_require_docker_resource_absent=lambda *args: None, _docker_json=inspect,
        _wait_contextforge=lambda *args: waits.append(True), _login_contextforge=lambda *args: "admin",
        _register_gateway=lambda *args, **kwargs: "gateway", _wait_gateway_tool=lambda *args, **kwargs: None,
        _list_tools=lambda *args, **kwargs: rows, _row_gateway_id=lambda item: item["gateway_id"],
        _make_tool_public=lambda *args: args[-1], _gateway_request=lambda *args, **kwargs: {
            "access_token": "fixture-token", "token": {"id": "token", "expires_at": "later"}})
    monkeypatch.setattr(module, "_helper", lambda: helper)
    monkeypatch.setattr(module, "_gateway_service", lambda *args: {"environment": {"SSRF_ALLOW_PRIVATE_NETWORKS": "false"}})
    monkeypatch.setattr(module.subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    if problem:
        with pytest.raises((ValueError, RuntimeError)):
            module.start_browser_gateway(plan, browser_runtime={"network_id": internal_id})
        assert not waits
        if problem in {"wrong_subnet", "wrong_owner"}:
            assert not calls and not (tmp_path / "compose.json").exists()
        return
    result = module.start_browser_gateway(plan, browser_runtime={"network_id": internal_id})
    graph = json.loads((tmp_path / "compose.json").read_text())
    assert graph["networks"] == {"browser": {"external": True, "name": internal_name},
        "browser-egress": {"external": True, "name": egress_name}}
    service = graph["services"]["gateway"]
    assert set(service["networks"]) == {"browser", "browser-egress"}
    assert service["ports"] == [{"host_ip": "10.254.3.1", "published": "45621", "target": 4444, "protocol": "tcp"}]
    assert service["environment"]["SSRF_ALLOWED_NETWORKS"] == '["10.254.3.2/32"]'
    assert service["environment"]["SSRF_ALLOW_PRIVATE_NETWORKS"] == "false"
    assert result["status"] == "ready" and result["egress_network_id"] == egress_id
    assert len(calls) == 1 and calls[0][:2] == ["docker", "compose"]
