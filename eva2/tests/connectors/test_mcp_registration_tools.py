from __future__ import annotations

import asyncio
from dataclasses import fields

import pytest

from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationTurn
from eva.application.orchestration import ToolRequest
from eva.providers.mcp_registration import McpRegistrationToolAdapter


TURN = ConversationTurn.private("tenant", "installation", "owner", "private-chat", "message-1",
                                "Sluit de documentatiedienst aan en gebruik de zoekfunctie.", "2026-09-09T20:00:00Z")


class Authority:
    configuration_sha256 = "a" * 64
    active = True
    revoked = False

    def authorize_turn(self, turn):
        if turn != TURN:
            raise ConversationConflict("another owner")

    def status(self, turn):
        self.authorize_turn(turn)
        return {"active": self.active, "revoked": self.revoked}

    def stop(self, turn):
        self.authorize_turn(turn)
        self.active = False
        self.revoked = True
        return self.status(turn)

    def authorize_endpoint(self, turn, endpoint):
        if turn != TURN or not self.active:
            raise ConversationConflict("owner mandate revoked")

    def admit_catalog(self, turn, endpoint, rows, provenance):
        self.authorize_endpoint(turn, endpoint)
        # Only the host authority supplies executable configuration; provider
        # annotations are not projected into authorization by the adapter.
        assert rows[0]["annotations"] == {"readOnlyHint": True}
        return {"host_admitted": "catalog"}


class Client:
    registrations = 0
    reads = 0

    def register(self, *, owner_key, endpoint):
        self.registrations += 1
        return {"id": "gateway-1"}

    def discover(self, *, owner_key, endpoint):
        self.reads += 1
        return {"id": "gateway-1"}, [{"name": "search", "annotations": {"readOnlyHint": True}}]


class Registry:
    recorded = None

    def register(self, turn, cid, endpoint, gateway_id, catalog, provenance):
        assert turn == TURN and catalog == {"host_admitted": "catalog"}
        self.recorded = {"connector_id": cid, "tools": [{"tool_id": "sealed-search"}]}
        return self.recorded

    def discover(self, turn, cid):
        assert turn == TURN and self.recorded["connector_id"] == cid
        return self.recorded


def command(preparation):
    accepted = {f.name for f in fields(ToolRequest)}
    kwargs = {f.name: getattr(preparation, f.name) for f in fields(preparation) if f.name in accepted}
    return ToolRequest(thread_id="thread", operation_id="operation", checkpoint_prefix="checkpoint", **kwargs)


def test_connect_readback_then_discover_uses_actual_source_and_host_catalog():
    client, registry, authority = Client(), Registry(), Authority()
    adapter = McpRegistrationToolAdapter(client, registry, authority, "owner", lambda source: TURN)
    preparation = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall("call1", "connect_service", {
        "endpoint": "https://docs.example/mcp", "documentation_url": "https://docs.example/about",
        "provenance": "Maintainer documentation inspected in this development fixture.",
    })))
    request = command(preparation)
    assert asyncio.run(adapter.execute(request)).accepted
    assert registry.recorded is None
    proof = asyncio.run(adapter.observe(request, None))
    assert proof.disposition == "verified" and request.expected_proof == proof.observed
    assert proof.evidence["observation"]["tool_count"] == 1
    assert "tools" not in proof.evidence["observation"]
    follow = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall("call2", "discover_connector_tools", {
        "connector_id": registry.recorded["connector_id"],
    })))
    assert asyncio.run(adapter.execute(command(follow))).accepted
    assert asyncio.run(adapter.observe(command(follow), None)).evidence["observation"] == registry.recorded
    assert client.registrations == client.reads == 1
    authority.active = False
    with pytest.raises(ConversationConflict, match="revoked"):
        asyncio.run(adapter.execute(request))
    assert client.registrations == 1


def test_missing_registration_proves_safe_initial_dispatch_but_not_success():
    from eva.adapters.connectors.mcp_registration import McpRegistrationAbsent
    from eva.application.orchestration import ToolDispatchResult
    client, registry, authority = Client(), Registry(), Authority()
    def absent(**kwargs):
        raise McpRegistrationAbsent("not registered")
    client.discover = absent
    adapter = McpRegistrationToolAdapter(client, registry, authority, "owner", lambda source: TURN)
    prepared = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall("call3", "connect_service", {
        "endpoint": "https://docs.example/mcp", "documentation_url": "https://docs.example/about", "provenance": "fixture",
    })))
    req = command(prepared)
    assert asyncio.run(adapter.observe(req, None)).disposition == "safe_to_execute"
    assert asyncio.run(adapter.observe(req, ToolDispatchResult(True, "sent", {}))).disposition == "pending"
    assert registry.recorded is None


def test_owner_can_stop_and_inspect_mandate_without_a_new_provider_call():
    client, authority = Client(), Authority()
    # A host-level pause must not be mistaken for the owner's durable stop.
    authority.active = False
    adapter = McpRegistrationToolAdapter(client, Registry(), authority, "owner", lambda source: TURN)
    prepared = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall("stop1", "manage_connected_services", {"action": "stop"})))
    req = command(prepared)
    assert asyncio.run(adapter.observe(req, None)).disposition == "safe_to_execute"
    dispatch = asyncio.run(adapter.execute(req))
    assert asyncio.run(adapter.observe(req, dispatch)).observed == {"revoked": True}
    status = asyncio.run(adapter.planner().prepare(TURN, ConversationActionCall("status1", "manage_connected_services", {"action": "status"})))
    assert asyncio.run(adapter.observe(command(status), None)).evidence["observation"] == {"active": False, "revoked": True}
    assert client.registrations == client.reads == 0
