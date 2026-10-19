"""Synthetic owner-local catalog admission; no remote discovery or provider traffic."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
from eva.application.conversation import ConversationActionCall
from eva.application.orchestration import ToolRequest
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.mcp_registration import McpRegistrationToolAdapter, _RegistrationPlanner, _source
from eva.providers.mcp_discovery import McpConnectorNotFound
from tests.postgres.test_owner_initiatives import costless_native, NOW


CONFIGURATION = "c" * 64
REGISTRY_ID = "synthetic-owner-catalog"


def _closed(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _constant(value):
    return {"type": "string", "const": value}


def _catalog_capabilities(claim):
    # These are the planner's prepared command fields, not the model's raw args.
    source = _closed({
        "tenant_id": _constant(claim.tenant_id),
        "installation_id": _constant(claim.installation_id),
        "principal_id": _constant(claim.principal_id),
        "conversation_id": _constant("initiative-task:{initiative_key}"),
        "message_id": _constant("initiative-execute:{initiative_key}"),
        "audience": _closed({"kind": _constant("private_person"),
            "principal_id": _constant(claim.principal_id)}),
    })
    schemas = {
        "manage_connected_services": _closed({"source": source, "action": _constant("status")}),
        "discover_connector_tools": _closed({"source": source, "connector_id": {
            "type": "string", "minLength": 1, "maxLength": 120,
            "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$"}}),
    }
    return [{"action_name": name, "configuration_sha256": CONFIGURATION,
        "tool_name": name, "observation_tool_name": name,
        "new_cost_eur_cents": 0, "new_obligation": False,
        "data_scope": "owner_private_local", "communication_scope": "none",
        "cost_evidence": "Synthetic existing local registry metadata; no remote call or new cost.",
        "execute_schema": schema, "observe_schema": deepcopy(schema)}
        for name, schema in schemas.items()]


def _admit(fixture, *, digital=False):
    auth, claim, _, control, _, path, policy = fixture
    _, baseline = auth.policy()
    candidate = deepcopy(policy)
    candidate["capabilities"].extend(_catalog_capabilities(claim))
    profile = None
    if digital:
        relative = "bootstrap/delegated_digital_runner.py"
        source = Path(__file__).resolve().parents[3] / "src/eva" / relative
        profile = DelegatedDigitalProfile("model:synthetic", "a" * 64, "b" * 64,
            ("save_work_progress", "manage_connected_services", "discover_connector_tools"),
            ((relative, hashlib.sha256(source.read_bytes()).hexdigest()),))
        candidate["digital_work"] = {
            "worker_profile_release_sha256": profile.release.worker_profile_release_sha256,
            "capability_ids": ["save_work_progress"],
        }
    path.write_text(json.dumps(candidate), encoding="utf-8")
    digest = canonical_sha256({k: v for k, v in candidate.items() if k != "enabled"})
    assert auth.admit_revision(baseline, digest)["mandate_sha256"] == digest
    guard, turn = auth.bind(claim)
    return SimpleNamespace(auth=auth, guard=guard, turn=turn, control=control,
        claim=claim, path=path, policy=candidate, profile=profile)


class _LocalAuthority:
    configuration_sha256 = CONFIGURATION

    def __init__(self, turn):
        self.turn = turn

    def authorize_turn(self, turn):
        assert turn == self.turn

    def status(self, turn):
        self.authorize_turn(turn)
        return {"active": True, "paused": False, "revoked": False, "revision": 1}

    def managed_endpoints(self, turn):
        self.authorize_turn(turn)
        return []

    def authorize_endpoint(self, turn, endpoint):
        self.authorize_turn(turn)
        assert endpoint == "https://synthetic.invalid/mcp"

    def __getattr__(self, name):
        raise AssertionError("No local authority mutation is allowed: " + name)


class _LocalRegistry:
    def __init__(self, turn):
        self.turn = turn
        self.calls = []

    def inventory(self, turn):
        assert turn == self.turn
        self.calls.append("inventory")
        return [{"connector_id": REGISTRY_ID, "endpoint": "https://synthetic.invalid/mcp"}]

    def discover(self, turn, connector_id):
        assert turn == self.turn and connector_id == REGISTRY_ID
        self.calls.append("discover")
        return {"connector_id": REGISTRY_ID, "connection_status": "registered",
            "tools": [{"name": "synthetic.read", "input_schema": _closed({})}]}


class _NoRemoteClient:
    def __getattr__(self, name):
        raise AssertionError("No gateway/network operation is allowed: " + name)


def _adapter(f):
    authority, registry = _LocalAuthority(f.turn), _LocalRegistry(f.turn)
    def load_source(source):
        assert source == _source(f.turn)
        return f.turn
    return McpRegistrationToolAdapter(_NoRemoteClient(), registry, authority,
        "synthetic-owner", load_source), registry


def _request(f, name, args):
    prep = asyncio.run(_RegistrationPlanner(_LocalAuthority(f.turn), "synthetic-owner").prepare(
        f.turn, ConversationActionCall("catalog-call", name, args)))
    return ToolRequest("synthetic:catalog", "synthetic:catalog:operation", "synthetic:checkpoint",
        prep.tool_name, prep.arguments, prep.expected_proof,
        observation_tool_name=prep.observation_tool_name,
        observation_arguments=prep.observation_arguments,
        tool_configuration_sha256=prep.tool_configuration_sha256)


@pytest.mark.parametrize("name,args,local_read", [
    ("manage_connected_services", {"action": "status"}, "inventory"),
    ("discover_connector_tools", {"connector_id": REGISTRY_ID}, "discover"),
])
def test_native_catalog_status_and_stored_schemas_are_local_only(costless_native, name, args, local_read):
    f = _admit(costless_native)
    request = _request(f, name, args)
    assert f.guard.action_allowed(name)
    f.guard.authorize_request(request, phase="execute")
    f.guard.authorize_request(request, phase="observe")
    adapter, registry = _adapter(f)
    dispatch = asyncio.run(adapter.execute(request))
    assert dispatch.accepted and registry.calls == []
    proof = asyncio.run(adapter.observe(request, dispatch))
    assert proof.disposition == "verified" and registry.calls == [local_read]
    observation = proof.evidence["observation"]
    assert proof.evidence["observation_sha256"] == canonical_sha256(observation)
    if name == "discover_connector_tools":
        assert observation["tools"][0]["input_schema"] == _closed({})
    else:
        assert observation["reachability_verified"] is False
        assert observation["connectors"][0]["connector_id"] == REGISTRY_ID


@pytest.mark.parametrize("action", ["pause", "resume", "stop"])
@pytest.mark.parametrize("phase", ["execute", "observe"])
def test_native_catalog_management_mutations_are_not_admitted(costless_native, action, phase):
    f = _admit(costless_native)
    request = _request(f, "manage_connected_services", {"action": action})
    with pytest.raises(InitiativeMandateDenied, match="outside initiative mandate"):
        f.guard.authorize_request(request, phase=phase)


def test_native_catalog_connection_is_not_admitted(costless_native):
    f = _admit(costless_native)
    assert not f.guard.action_allowed("connect_service")
    request = _request(f, "connect_service", {"endpoint": "https://synthetic.invalid/mcp",
        "documentation_url": "https://synthetic.invalid/mcp", "provenance": "synthetic"})
    for phase in ("execute", "observe"):
        with pytest.raises(InitiativeMandateDenied, match="outside initiative mandate"):
            f.guard.authorize_request(request, phase=phase)


def test_native_catalog_url_discovery_is_not_admitted(costless_native):
    f = _admit(costless_native)
    request = _request(f, "discover_connector_tools", {"connector_id": "https://synthetic.invalid/mcp"})
    assert "endpoint" in request.arguments
    for phase in ("execute", "observe"):
        with pytest.raises(InitiativeMandateDenied, match="outside initiative mandate"):
            f.guard.authorize_request(request, phase=phase)


@pytest.mark.parametrize("field", ["tenant_id", "installation_id", "principal_id",
    "conversation_id", "message_id", "audience_kind", "audience_principal"])
@pytest.mark.parametrize("phase", ["execute", "observe"])
def test_native_catalog_foreign_source_is_not_admitted(costless_native, field, phase):
    f = _admit(costless_native)
    request = _request(f, "discover_connector_tools", {"connector_id": REGISTRY_ID})
    args = deepcopy(request.arguments)
    if field == "audience_kind":
        args["source"]["audience"]["kind"] = "household"
    elif field == "audience_principal":
        args["source"]["audience"]["principal_id"] = "different-owner"
    else:
        args["source"][field] = "different-source"
    request = replace(request, arguments=args, observation_arguments=deepcopy(args))
    with pytest.raises(InitiativeMandateDenied, match="outside initiative mandate"):
        f.guard.authorize_request(request, phase=phase)


def test_native_catalog_foreign_configuration_is_not_admitted(costless_native):
    f = _admit(costless_native)
    request = replace(_request(f, "manage_connected_services", {"action": "status"}),
        tool_configuration_sha256="d" * 64)
    with pytest.raises(InitiativeMandateDenied, match="outside initiative mandate"):
        f.guard.authorize_request(request, phase="execute")


@pytest.mark.parametrize("boundary", ["stop", "revoke", "unadmitted_revision"])
def test_native_catalog_preserves_stop_and_current_admitted_mandate(costless_native, boundary):
    f = _admit(costless_native)
    request = _request(f, "manage_connected_services", {"action": "status"})
    if boundary == "stop":
        f.control.stop(f.claim.intent_id, now=NOW)
    elif boundary == "revoke":
        f.auth.revoke()
    else:
        changed = deepcopy(f.policy)
        changed["authorization_source"] += " changed without admission"
        f.path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(InitiativeMandateDenied):
        f.guard.authorize_request(request, phase="execute")


def test_native_catalog_grant_intersects_native_capabilities_without_extending_local_enum(costless_native):
    f = _admit(costless_native, digital=True)
    assert f.policy["digital_work"]["capability_ids"] == ["save_work_progress"]
    grant = f.auth.digital_grant(f.profile)
    assert set(grant.capability_ids) == {"save_work_progress", "manage_connected_services",
        "discover_connector_tools"}


def test_native_catalog_unknown_registry_id_reports_absence_without_remote_discovery(costless_native):
    f = _admit(costless_native)
    request = _request(f, "discover_connector_tools", {"connector_id": "unknown-own-catalog"})
    f.guard.authorize_request(request, phase="execute")
    f.guard.authorize_request(request, phase="observe")
    class MissingRegistry:
        calls = 0
        def discover(self, turn, connector_id):
            assert turn == f.turn and connector_id == "unknown-own-catalog"
            self.calls += 1
            raise McpConnectorNotFound("Synthetic catalog is not retained")
    registry = MissingRegistry()
    adapter = McpRegistrationToolAdapter(_NoRemoteClient(), registry, _LocalAuthority(f.turn),
        "synthetic-owner", lambda source: f.turn if source == _source(f.turn) else None)
    dispatch = asyncio.run(adapter.execute(request))
    assert registry.calls == 0
    proof = asyncio.run(adapter.observe(request, dispatch))
    assert proof.disposition == "verified" and registry.calls == 1
    assert proof.observed == {"connector_id": "unknown-own-catalog"}
    observation = proof.evidence["observation"]
    assert observation["connection_status"] == "not_connected" and observation["tools"] == []
    assert proof.evidence["observation_sha256"] == canonical_sha256(observation)
