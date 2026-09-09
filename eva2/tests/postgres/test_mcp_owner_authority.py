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
