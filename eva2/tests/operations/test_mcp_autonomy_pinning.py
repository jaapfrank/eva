import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest


PATH = Path(__file__).resolve().parents[2] / "deploy/mcp-autonomy/eva_mcp_pinning.py"
SPEC = importlib.util.spec_from_file_location("eva_mcp_pinning_test", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.fixture
def policy(monkeypatch):
    value = {"owner_networks": ["10.0.20.0/24"], "blocked_addresses": ["10.0.20.46"],
             "filesystem_origin": "http://workspace:8000", "filesystem_address": "172.30.0.3"}
    monkeypatch.setenv("EVA_MCP_PINNING_POLICY", json.dumps(value))
    return value


@pytest.mark.parametrize("url,ip,allowed", [
    ("https://public.example/mcp", "1.1.1.1", True),
    ("http://public.example/mcp", "1.1.1.1", False),
    ("https://public.example:444/mcp", "1.1.1.1", False),
    ("http://ha/mcp", "10.0.20.74", True),
    ("http://ha/mcp", "10.0.20.46", False),
    ("http://workspace:8000/mcp", "172.30.0.3", True),
    ("http://other:8000/mcp", "172.30.0.3", False),
    ("http://workspace:8000/mcp", "172.30.0.4", False),
    ("https://public.example/mcp", "127.0.0.1", False),
    ("https://public.example/mcp", "169.254.169.254", False),
])
def test_destination_policy(policy, url, ip, allowed):
    if allowed:
        module._admit(url, {"resolved_ip": ip}, module._policy())
    else:
        with pytest.raises(ValueError):
            module._admit(url, {"resolved_ip": ip}, module._policy())


def test_each_pooled_request_is_pinned_and_rebinding_fails_before_transport(monkeypatch, policy):
    resolutions = iter(["1.1.1.1", "1.0.0.1", "127.0.0.1"])
    calls = []

    async def validate(url, label):
        target = urlsplit(url)
        return {"resolved_ip": next(resolutions), "hostname": target.hostname,
                "original_authority": target.netloc}

    def pin(url, ip):
        target = urlsplit(url)
        return urlunsplit((target.scheme, ip, target.path, target.query, target.fragment))

    monkeypatch.setitem(sys.modules, "mcpgateway.common.validators",
                        SimpleNamespace(SecurityValidator=SimpleNamespace(validate_url_for_connection_pinning=validate)))
    monkeypatch.setitem(sys.modules, "mcpgateway.services.tool_service", SimpleNamespace(_pin_url_to_resolved_ip=pin))

    async def send(self, request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "http://forbidden/mcp"}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "_send_single_request", send)

    async def run():
        async with module.mcp_http_client() as client:
            for _ in range(2):
                response = await client.get("https://public.example/mcp", follow_redirects=True)
                assert response.status_code == 302
            with pytest.raises(ValueError, match="deployment policy"):
                await client.get("https://public.example/mcp")
    asyncio.run(run())
    assert [str(request.url) for request in calls] == ["https://1.1.1.1/mcp", "https://1.0.0.1/mcp"]
    assert all(request.headers["host"] == "public.example" for request in calls)
    assert all(request.extensions["sni_hostname"] == "public.example" for request in calls)


def test_opt_in_and_invalid_transport_fail_closed(monkeypatch, policy):
    with pytest.raises(ValueError):
        module.mcp_http_client(verify=False)
    with pytest.raises(ValueError):
        module.mcp_http_client(proxy="http://proxy")
    monkeypatch.setenv("EVA_MCP_PINNING_POLICY", "{}")
    with pytest.raises(ValueError):
        module.mcp_http_client()
    monkeypatch.delenv("EVA_MCP_PINNING_POLICY")
    client = module.mcp_http_client()
    assert type(client) is httpx.AsyncClient
    asyncio.run(client.aclose())


def test_owner_network_is_configuration_not_a_household_specific_code_path(monkeypatch, policy):
    policy["owner_networks"] = ["192.168.42.0/24"]
    monkeypatch.setenv("EVA_MCP_PINNING_POLICY", json.dumps(policy))
    module._admit("http://home-service/mcp", {"resolved_ip": "192.168.42.5"}, module._policy())
    with pytest.raises(ValueError):
        module._admit("http://home-service/mcp", {"resolved_ip": "10.0.20.74"}, module._policy())
    policy["owner_networks"] = ["0.0.0.0/0"]
    monkeypatch.setenv("EVA_MCP_PINNING_POLICY", json.dumps(policy))
    with pytest.raises(ValueError):
        module._policy()
