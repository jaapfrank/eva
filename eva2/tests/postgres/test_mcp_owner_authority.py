import json
from pathlib import Path

import pytest

from eva.application.conversation import ConversationConflict, ConversationTurn
from eva.providers.mcp_owner_authority import McpOwnerAuthority
from tests.postgres.test_operational_timeline import timeline_engine  # noqa: F401


def test_owner_mandate_admits_unknown_invocation_and_stop_survives_restart(timeline_engine, private_tmp_path: Path):
    path = private_tmp_path / "mandate.json"
    mandate = {"contract": "OwnerConnectorMandate/v1", "mandate_id": "test-owner-mandate",
               "tenant_id": "mandate-test", "installation_id": "home", "owner_principal_id": "owner",
               "issued_at": "2020-01-01T00:00:00Z", "authorization_source": "explicit fixture owner grant",
               "enabled": True, "public_https": True, "local_networks": ["10.0.20.0/24"]}
    path.write_text(json.dumps(mandate)); path.chmod(0o600)
    owner = ConversationTurn.private("mandate-test", "home", "owner", "private-owner", "source-1",
                                     "Connect and use this service.", "2026-09-09T12:00:00Z")
    authority = McpOwnerAuthority(timeline_engine, str(path))
    assert authority.status(owner)["active"]
    authority.authorize_endpoint(owner, "https://1.1.1.1/mcp")
    authority.authorize_endpoint(owner, "http://10.0.20.74/mcp")
    for endpoint in ("http://1.1.1.1/mcp", "http://127.0.0.1/mcp", "http://172.30.0.2/mcp"):
        with pytest.raises(ConversationConflict, match="outside"):
            authority.authorize_endpoint(owner, endpoint)
    with pytest.raises(ConversationConflict, match="another owner"):
        authority.status(ConversationTurn.private("mandate-test", "home", "other", "private-other",
                          "other-source", "Connect this service.", owner.occurred_at))
    # Neither provider names nor readOnlyHint are an authorization classifier.
    row = {"name": "new_service_arbitrary_operation", "description": "Unknown operation",
           "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
           "enabled": True, "reachable": True, "deprecated": False, "integrationType": "MCP",
           "requestType": "STREAMABLEHTTP", "visibility": "private", "annotations": {"readOnlyHint": True}}
    catalog = authority.admit_catalog(owner, "https://1.1.1.1/mcp", [row], {"documentation_url": "https://1.1.1.1/docs"})
    assert catalog["tools"][0]["operation_kind"] == "invoke"
    assert catalog["tools"][0]["proof_bindings"] == []
    path.write_text(json.dumps({**mandate, "enabled": False}))
    assert not authority.status(owner)["active"] and not authority.status(owner)["revoked"]
    authority.stop(owner)
    path.write_text(json.dumps(mandate))
    restarted = McpOwnerAuthority(timeline_engine, str(path))
    assert not restarted.status(owner)["active"]
    with pytest.raises(ConversationConflict, match="stopped"):
        restarted.authorize_current(phase="execute", operation_kind="invoke")
    restarted.authorize_current(phase="observe", operation_kind="invoke")
    with pytest.raises(ConversationConflict, match="stopped"):
        restarted.authorize_current(phase="observe", operation_kind="read")
    assert not restarted.stop(owner)["active"]
    assert len(restarted._timeline.events(restarted._thread_id)) == 2


def test_managed_endpoint_is_exact_owner_bound_and_cannot_revive_stopped_mandate(
    timeline_engine, private_tmp_path: Path, monkeypatch,
):
    import socket

    mandate_file = private_tmp_path / "mandate.json"
    managed_file = private_tmp_path / "managed-endpoints.json"
    mandate = {
        "contract": "OwnerConnectorMandate/v1", "mandate_id": "managed-endpoint-test",
        "tenant_id": "managed-test", "installation_id": "home", "owner_principal_id": "owner",
        "issued_at": "2020-01-01T00:00:00Z", "authorization_source": "explicit fixture owner grant",
        "enabled": True, "public_https": True, "local_networks": ["10.0.20.0/24"],
    }
    original = json.dumps(mandate).encode()
    mandate_file.write_bytes(original)
    mandate_file.chmod(0o600)
    owner = ConversationTurn.private(
        "managed-test", "home", "owner", "private-owner", "managed-source",
        "Use my installed service.", "2026-09-09T12:00:00Z",
    )
    authority = McpOwnerAuthority(timeline_engine, str(mandate_file))
    original_configuration = authority.configuration_sha256
    endpoint = "http://installed-service:8931/mcp"
    addresses = ["192.168.0.2"]
    resolutions = []

    def resolve(host, port, **kwargs):
        resolutions.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(ConversationConflict, match="outside"):
        authority.authorize_endpoint(owner, endpoint)
    managed = {
        "contract": "EvaManagedConnectorEndpoints/v1",
        "tenant_id": mandate["tenant_id"], "installation_id": mandate["installation_id"],
        "owner_principal_id": mandate["owner_principal_id"],
        "mandate_configuration_sha256": original_configuration,
        "endpoints": [{"endpoint": endpoint, "resolved_ip": "192.168.0.2"}],
    }
    managed_file.write_text(json.dumps(managed))
    managed_file.chmod(0o600)
    installed = McpOwnerAuthority(timeline_engine, str(mandate_file), str(managed_file))
    installed.authorize_endpoint(owner, endpoint)
    assert installed.configuration_sha256 == original_configuration
    assert len(installed._timeline.events(installed._thread_id)) == 1
    # The exact URL cannot grant another path, hostname, port, or Docker address.
    for other in (endpoint + "/other", endpoint.replace("8931", "8932"),
                  endpoint.replace("installed-service", "another-service")):
        with pytest.raises(ConversationConflict, match="outside"):
            installed.authorize_endpoint(owner, other)
    for resolved in (["192.168.0.3"], ["192.168.0.2", "192.168.0.3"], ["1.1.1.1"]):
        addresses[:] = resolved
        with pytest.raises(ConversationConflict, match="address differs"):
            installed.authorize_endpoint(owner, endpoint)
    addresses[:] = ["192.168.0.2"]
    for field in ("tenant_id", "installation_id", "owner_principal_id", "mandate_configuration_sha256"):
        managed_file.write_text(json.dumps({**managed, field: "another-scope"}))
        with pytest.raises(ConversationConflict, match="bind the owner mandate"):
            McpOwnerAuthority(timeline_engine, str(mandate_file), str(managed_file))
    managed_file.write_text(json.dumps(managed))
    installed.stop(owner)
    restarted = McpOwnerAuthority(timeline_engine, str(mandate_file), str(managed_file))
    resolutions.clear()
    with pytest.raises(ConversationConflict, match="stopped"):
        restarted.authorize_endpoint(owner, endpoint)
    assert resolutions == []  # Durable stop precedes DNS and the transport exception.
    assert restarted.status(owner)["revoked"]
    assert len(restarted._timeline.events(restarted._thread_id)) == 2
    assert mandate_file.read_bytes() == original
    assert restarted.configuration_sha256 == original_configuration
