from __future__ import annotations

import pytest

from eva.adapters.connectors.mcp_registration import (
    ContextForgeRegistrationClient, McpRegistrationConfig, McpRegistrationError,
    canonical_mcp_endpoint, registration_name,
)


def client():
    return ContextForgeRegistrationClient(McpRegistrationConfig("https://gateway.example", "private-control-token"))


def test_lost_registration_response_is_read_back_without_second_post(monkeypatch):
    c = client()
    endpoint = "https://docs.example/mcp"
    row = {"id": "new-gateway", "name": registration_name("owner", endpoint),
           "url": endpoint, "transport": "STREAMABLEHTTP"}
    calls = []
    registered = False

    def request(method, path, payload=None):
        nonlocal registered
        calls.append((method, path))
        if method == "POST":
            registered = True
            raise McpRegistrationError("response lost after accepted POST")
        return [row] if registered else []

    monkeypatch.setattr(c, "_request", request)
    assert c.register(owner_key="owner", endpoint=endpoint) == row
    assert c.register(owner_key="owner", endpoint=endpoint) == row
    assert sum(method == "POST" for method, _ in calls) == 1


def test_registration_name_collision_never_adopts_different_endpoint(monkeypatch):
    c = client()
    endpoint = "https://docs.example/mcp"
    row = {"id": "foreign", "name": registration_name("owner", endpoint),
           "url": "https://another.example/mcp", "transport": "STREAMABLEHTTP"}
    monkeypatch.setattr(c, "_request", lambda *args: [row])
    with pytest.raises(McpRegistrationError, match="identity differs"):
        c.register(owner_key="owner", endpoint=endpoint)


def test_discovery_filters_gateway_rows_and_does_not_turn_hints_into_authority(monkeypatch):
    c = client()
    endpoint = "https://docs.example/mcp"
    gateway = {"id": "own", "name": registration_name("owner", endpoint),
               "url": endpoint, "transport": "STREAMABLEHTTP"}
    own = {"name": "unknown_search", "gatewayId": "own", "annotations": {"readOnlyHint": True}}
    foreign = {"name": "write_secret", "gatewayId": "another"}
    monkeypatch.setattr(c, "_request", lambda method, path, payload=None:
                        [gateway] if path.startswith("/gateways") else [own, foreign])
    observed, tools = c.discover(owner_key="owner", endpoint=endpoint)
    assert observed == gateway
    assert tools == [own]
    assert "operation_kind" not in tools[0]
    assert registration_name("another-owner", endpoint) != gateway["name"]


@pytest.mark.parametrize("endpoint", ["file:///etc/passwd", "https://user:secret@host/mcp", "https://host/mcp#fragment", "https://host:99999/mcp", "https://host/\n"])
def test_endpoint_validation_rejects_ambiguous_or_credential_urls(endpoint):
    with pytest.raises(ValueError):
        canonical_mcp_endpoint(endpoint)


def test_public_visibility_requires_explicit_exclusive_owner_gateway():
    with pytest.raises(ValueError):
        McpRegistrationConfig("https://gateway.example", "token", visibility="public")
    assert McpRegistrationConfig("https://gateway.example", "token").visibility == "private"
    assert McpRegistrationConfig("https://gateway.example", "token", visibility="public", exclusive_owner_gateway=True).visibility == "public"
