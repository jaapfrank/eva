"""Digital composition shares configured tools and the existing worker dispatch."""
import asyncio
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from eva.application.conversation import ConversationToolDescriptor
from eva.bootstrap.delegated_digital_composition import (
    build_digital_profile, digital_tools, DigitalEnabledDelegatedRunner)
from eva.providers.contracts import ExternalProviderBundle, ExternalProviderSettings, LoadedProvider


@dataclass(frozen=True)
class Budget:
    max_input_tokens: int = 12000
    max_output_tokens: int = 1200


@dataclass(frozen=True)
class ProviderSettings:
    endpoint: str = "http://connector.invalid"


def configuration():
    tool = ConversationToolDescriptor("use_connector_tool", "Use admitted connector", {"type": "object", "additionalProperties": False, "properties": {}, "required": []})
    route = SimpleNamespace(action_names=(tool.name,))
    bundle = ExternalProviderBundle((tool,), (route,), None)
    settings = SimpleNamespace(runtime_resource_budget=Budget(),
        external_provider=ExternalProviderSettings((LoadedProvider("mcp_autonomy", None, ProviderSettings()),)),
        conversation_model="configured-model", conversation_reasoning_effort="medium",
        conversation_maximum_requests=32, hindsight_url="http://memory.invalid",
        hindsight_bank_namespace="eva2", hindsight_recall_budget="low",
        hindsight_recall_max_tokens=2000, hindsight_recall_node_budget=40)
    return settings, bundle


def test_profile_uses_same_configuration_and_changes_when_model_or_provider_changes():
    settings, bundle = configuration()
    first = build_digital_profile(settings, bundle)
    assert first.release == build_digital_profile(settings, bundle).release
    assert first.maximum_response_input_tokens == 12000
    assert first.grant().maximum_input_tokens == 384000
    assert "research_current_options" in first.capability_ids
    assert "get_delegated_work" in first.capability_ids
    assert "use_connector_tool" in first.capability_ids
    assert first.maximum_model_runs == 32
    assert first.maximum_response_output_tokens == 1200
    assert first.grant().maximum_output_tokens == 38400
    assert first.maximum_tool_calls == 31
    settings.conversation_model = "another-model"
    assert first.release != build_digital_profile(settings, bundle).release
    settings.conversation_model = "configured-model"
    settings.external_provider = ExternalProviderSettings((
        LoadedProvider("mcp_autonomy", None, ProviderSettings("http://another.invalid")),))
    assert first.release != build_digital_profile(settings, bundle).release


def test_connector_composition_rejects_missing_route_and_excludes_task_delegation():
    _, bundle = configuration()
    recursive = ConversationToolDescriptor("start_digital_work", "Start work", {"type": "object", "additionalProperties": False, "properties": {}, "required": []})
    expanded = replace(bundle, conversation_tools=(*bundle.conversation_tools, recursive),
        external_tools=(*bundle.external_tools, SimpleNamespace(action_names=(recursive.name,))))
    descriptors, routes = digital_tools(expanded)
    assert {"use_connector_tool", "get_delegated_work"} <= {tool.name for tool in descriptors}
    assert "start_digital_work" not in {tool.name for tool in descriptors}
    assert routes == bundle.external_tools
    with pytest.raises(ValueError, match="differ"):
        digital_tools(replace(bundle, external_tools=()))


def test_scoped_actions_follow_late_profile_assignment():
    from eva.adapters.postgres.scoped_delegated_work_actions import ScopedDelegatedWorkActions
    from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
    service = SimpleNamespace(research_profile=None, digital_profile=None)
    actions = ScopedDelegatedWorkActions(PostgresDelegatedWorkActions(None, service), None)
    assert "start_digital_work" not in actions.private.action_names
    service.digital_profile = object()
    assert "start_digital_work" in actions.action_names
    assert "start_digital_work" in {tool.name for tool in actions.conversation_tools(include_digital=True)}

def test_runner_factory_reuses_resource_factory_and_exposes_only_connector_tools(monkeypatch):
    import eva.bootstrap.delegated_digital_composition as composition
    import eva.bootstrap.private_conversation_resources as resources
    import eva.bootstrap.household_voice as household_module
    import eva.bootstrap.delegated_work_owner_service as owner_module
    import eva.adapters.model.pydantic_conversation as model_module
    import eva.providers.mcp_autonomy as source_module
    import eva.adapters.postgres.telegram_group_consent as group_module
    from uuid import UUID
    settings, bundle = configuration()
    settings.background_principal_id = "00000000-0000-0000-0000-000000000001"
    settings.dashboard_principal_binding_id = "00000000-0000-0000-0000-000000000002"
    settings.background_tenant_id = "tenant:test"
    settings.background_installation_id = "installation:test"
    settings.telegram_principal_bindings_file = None
    settings.conversation_auth_file = "/private/model-lease"
    observed = {}
    def owner(store, **kwargs):
        observed["owner"] = kwargs
        return SimpleNamespace(conversation_scope=kwargs["conversation_scope"], validate_authority=lambda turn: None)
    def model(**kwargs):
        observed["model"] = kwargs
        return object()
    memory, identity = object(), object()
    def shared(*args, **kwargs):
        observed["resources"] = kwargs
        return memory, identity
    monkeypatch.setattr(household_module, "load_household_voice_authority", lambda *args, **kwargs: "household-consent")
    monkeypatch.setattr(owner_module, "DelegatedWorkOwnerService", owner)
    monkeypatch.setattr(model_module, "PydanticConversationModel", model)
    monkeypatch.setattr(resources, "load_conversation_identity_pack", lambda settings: "signed-identity")
    monkeypatch.setattr(resources, "build_private_conversation_resources", shared)
    monkeypatch.setattr(group_module, "PostgresTelegramGroupConsentStore", lambda *args, **kwargs: "group-consent")
    monkeypatch.setattr(source_module, "_SourceTurnLoader", lambda *args: "source-loader")
    store = SimpleNamespace(_engine=object())
    runner = composition.build_digital_runner(settings, store, provider_bundle=bundle)
    assert runner.memory is memory and runner.identity is identity
    assert runner.connector_tools[:-1] == bundle.external_tools
    assert runner.connector_tools[-1].action_names == ("get_delegated_work",)
    assert observed["model"]["resource_budget"].max_output_tokens == 1200
    assert observed["model"]["resource_budget"].max_input_tokens == 12000
    assert observed["model"]["timeout_seconds"] == 120.0
    assert runner.profile.grant().timeout_seconds == 1200
    assert observed["model"]["model"] == settings.conversation_model
    assert observed["model"]["conversation_tools"] == composition.digital_tools(bundle)[0]
    assert observed["owner"]["owner_principal_id"] == UUID(settings.background_principal_id)
    assert observed["resources"]["identity_pack"] == "signed-identity"
    assert observed["resources"]["household_voice"] == "household-consent"
    assert runner.profile.release == composition.build_digital_profile(settings, bundle).release


def test_existing_worker_polls_four_profiles_without_another_scheduler():
    from eva.operations.delegated_work_worker import DelegatedWorkWorker
    seen = []
    store = SimpleNamespace(claim_next=lambda **kwargs: seen.append(kwargs["release"]))
    releases = tuple(object() for _ in range(4))
    worker = DelegatedWorkWorker(store, None, worker_id="test:worker", release=releases)
    assert asyncio.run(worker.run_once()) == "idle"
    assert tuple(seen) == releases
    with pytest.raises(ValueError, match="four"):
        DelegatedWorkWorker(store, None, worker_id="test:worker", release=(*releases, object()))


def test_wrapper_keeps_existing_contracts_on_existing_runner():
    from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
    seen = []
    class Runner:
        def __init__(self, name): self.name = name
        async def run(self, spec, **kwargs):
            seen.append((self.name, spec, kwargs))
            return self.name
    wrapper = DigitalEnabledDelegatedRunner(Runner("existing"), Runner("digital"))
    old = object()
    digital = DelegatedDigitalWorkSpec.model_construct()
    assert asyncio.run(wrapper.run(old, call_id="old")) == "existing"
    assert asyncio.run(wrapper.run(digital, call_id="new")) == "digital"
    assert seen[0][1] is old and seen[1][1] is digital


def test_procedure_learning_and_verified_writer_share_the_pinned_profile():
    settings, bundle = configuration()
    def descriptor(name):
        properties = ({"path": {"type": "string"}, "content": {"type": "string", "maxLength": 12000}}
                      if name == "save_workspace_document" else {})
        return ConversationToolDescriptor(name, name, {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False})
    names = ("list_reusable_procedures", "resolve_reusable_procedure", "designate_reusable_procedure", "save_workspace_document")
    route = SimpleNamespace(action_names=names)
    bundle = replace(bundle, conversation_tools=(*bundle.conversation_tools, *(descriptor(n) for n in names)),
                     external_tools=(*bundle.external_tools, route))
    settings.external_provider = ExternalProviderSettings((*settings.external_provider.providers,
        LoadedProvider("procedure_references", None, ("mandate-a", ProviderSettings()))))
    profile = build_digital_profile(settings, bundle)
    assert set(profile.capability_ids) == {"research_current_options", "use_connector_tool", "get_delegated_work",
        "save_work_progress", "read_own_component_source", "draft_own_component_improvement", *names}
    assert "providers/procedure_reference_actions.py" in dict(profile.execution_source_sha256)
    assert "adapters/postgres/procedure_reference_host.py" in dict(profile.execution_source_sha256)
    settings.external_provider = ExternalProviderSettings((settings.external_provider.providers[0],
        LoadedProvider("procedure_references", None, ("mandate-b", ProviderSettings()))))
    assert build_digital_profile(settings, bundle).release != profile.release


def test_gateway_configuration_changes_the_digital_profile():
    settings, bundle = configuration()
    settings.external_provider = ExternalProviderSettings((*settings.external_provider.providers,
        LoadedProvider("mcp_gateway", None, ProviderSettings("http://gateway-a.invalid"))))
    first = build_digital_profile(settings, bundle)
    settings.external_provider = ExternalProviderSettings((settings.external_provider.providers[0],
        LoadedProvider("mcp_gateway", None, ProviderSettings("http://gateway-b.invalid"))))
    assert build_digital_profile(settings, bundle).release != first.release
    assert "adapters/postgres/external_conversation_tools.py" in dict(first.execution_source_sha256)


def test_digital_grant_accepts_configured_catalog_beyond_legacy_sixteen_tools():
    settings, bundle = configuration()
    profile = build_digital_profile(settings, bundle)
    names = ("connect_service", "discover_connector_tools", "manage_connected_services", "use_connector_tool",
             "list_reusable_procedures", "resolve_reusable_procedure", "designate_reusable_procedure",
             "save_workspace_document", "save_work_progress", "read_own_component_source",
             "draft_own_component_improvement", "evaluate_own_component_improvement",
             "prepare_own_component_release", "run_reusable_procedure", "home_assistant_request",
             "read_home_assistant_inventory", "get_delegated_work")
    expanded = replace(profile, capability_ids=names)
    assert expanded.grant().capability_ids == list(names)
    with pytest.raises(ValueError, match="explicit connector actions"):
        replace(profile, capability_ids=(*names, "start_digital_work"))


@pytest.mark.parametrize("variant", ["cumulative", "response_input", "response_both"])
def test_legacy_budget_is_exact_and_bound_to_current_release(variant):
    from eva.bootstrap.delegated_digital_profile import DIGITAL_PROFILE_ID
    from eva.contracts.canonical_json import canonical_sha256
    settings, bundle = configuration()
    profile = build_digital_profile(settings, bundle)
    current = profile.grant()
    input_tokens = 96000 if variant == "cumulative" else 12000
    output = 1200 if variant == "response_both" else 9600
    legacy = current.model_copy(update={"maximum_model_runs": 8,
        "maximum_completed_model_responses": 8, "maximum_tool_calls": 6,
        "maximum_input_tokens": input_tokens, "maximum_output_tokens": output,
        "maximum_total_tokens": input_tokens + output, "timeout_seconds":300})
    old_release = canonical_sha256({"profile": DIGITAL_PROFILE_ID, "grant": legacy.to_payload()})
    assert profile.accepts_grant(legacy)
    assert profile.accepts_grant(current)
    assert profile.accepts_prior_profile(legacy, old_release)
    assert not profile.accepts_prior_profile(current, old_release)
    for changed in ({"maximum_input_tokens": input_tokens + 1, "maximum_total_tokens": input_tokens + 1 + output},
                    {"maximum_output_tokens": output + 1, "maximum_total_tokens": input_tokens + output + 1},
                    {"maximum_total_tokens": current.maximum_total_tokens},
                    {"maximum_tool_calls": 7}, {"maximum_model_runs": 9}):
        assert not profile.accepts_grant(legacy.model_copy(update=changed))


def test_input_attempt_budget_tracks_bounded_model_runs_with_response_limit_unchanged():
    settings, bundle = configuration()
    settings.conversation_maximum_requests = 3
    profile = build_digital_profile(settings, bundle)
    assert profile.maximum_response_input_tokens == 12000
    assert profile.maximum_input_tokens == 36000
    assert profile.maximum_model_runs == profile.maximum_completed_model_responses == 3
    assert profile.maximum_tool_calls == 2 and profile.timeout_seconds == 1200
    with pytest.raises(ValueError, match="input budget"):
        replace(profile, maximum_input_tokens=12000)


@pytest.mark.parametrize("configured, runs", [(1, 1), (3, 3), (9, 9), (32, 32), (64, 32)])
def test_execution_envelope_uses_host_allowance_without_changing_response_limits(configured, runs):
    settings, bundle = configuration()
    settings.conversation_maximum_requests = configured
    profile = build_digital_profile(settings, bundle)
    assert profile.maximum_model_runs == profile.maximum_completed_model_responses == runs
    assert profile.maximum_tool_calls == max(1, runs - 1)
    assert profile.maximum_input_tokens == 12000 * runs
    assert profile.maximum_output_tokens == 1200 * runs
    assert profile.maximum_response_input_tokens == 12000
    assert profile.maximum_response_output_tokens == 1200
    assert profile.timeout_seconds == 1200
    assert all(grant.timeout_seconds == 300 for grant in profile.historical_grants)
    older = [grant for grant in profile.historical_grants if grant.maximum_tool_calls == 6]
    assert older and {grant.maximum_model_runs for grant in older} == {min(configured, 8)}
    previous = profile.historical_grants[0]
    assert previous.maximum_model_runs == runs
    assert previous.maximum_tool_calls == max(1,runs-1)
    # No fabricated intermediate grant or response-sized 32-run variant existed.
    if runs > 8:
        current = profile.grant()
        assert not profile.accepts_grant(current.model_copy(update={
            "maximum_input_tokens": 12000, "maximum_total_tokens": 12000 + current.maximum_output_tokens}))


@pytest.mark.parametrize("variant", [0, 1, 2])
def test_old_household_envelope_remains_readable_but_requires_current_release_for_resume(variant):
    from datetime import UTC, datetime
    from eva.bootstrap.household_digital_work import HouseholdDigitalAuthority
    from eva.contracts.delegated_digital_work import DelegatedHouseholdDigitalWorkSpec
    settings, bundle = configuration()
    descriptor = ConversationToolDescriptor("home_assistant_request", "Use HA", {
        "type": "object", "additionalProperties": False, "properties": {}, "required": []})
    bundle = replace(bundle, conversation_tools=(*bundle.conversation_tools, descriptor),
        external_tools=(*bundle.external_tools, SimpleNamespace(action_names=(descriptor.name,))))
    current = build_digital_profile(settings, bundle)
    grant = [g for g in current.historical_grants if g.maximum_tool_calls == 6][variant]
    legacy = replace(current, maximum_model_runs=grant.maximum_model_runs,
        maximum_completed_model_responses=grant.maximum_completed_model_responses,
        maximum_tool_calls=grant.maximum_tool_calls, maximum_input_tokens=grant.maximum_input_tokens,
        maximum_output_tokens=grant.maximum_output_tokens, maximum_response_input_tokens=None,
        maximum_response_output_tokens=None, timeout_seconds=grant.timeout_seconds, historical_grants=())
    policy = SimpleNamespace(devices={"assist_satellite.kitchen": "kitchen"}, owner_principal_id="owner",
        principal_binding_id="binding", policy_id="policy", household_policy_id="household-policy",
        household_policy_sha256="c" * 64, tenant_id="tenant", installation_id="installation",
        household_id="family", consent_operation_id="consent", effective_at="2026-09-01T00:00:00Z",
        expires_at="2026-10-01T00:00:00Z")
    parent = SimpleNamespace(policy=policy, sha256="d" * 64, validate_current=lambda: None,
        validate_device=lambda *args: None, clock=lambda: datetime(2026, 9, 28, tzinfo=UTC))
    import hashlib
    actor = "household-device:kitchen"
    authority = HouseholdDigitalAuthority(parent, current, lambda: SimpleNamespace(api_enabled=True,
        effect_principal_sha256_allowlist={hashlib.sha256(actor.encode()).hexdigest()}))
    origin = SimpleNamespace(actor_id=actor, satellite_id="assist_satellite.kitchen", device_id="kitchen",
        research_policy_id=policy.policy_id, research_policy_sha256=parent.sha256,
        **{key: getattr(policy, key) for key in ("household_policy_id", "household_policy_sha256",
            "tenant_id", "installation_id", "household_id", "consent_operation_id")})
    def release_fields(profile):
        release = profile.release
        return {name: getattr(release, name) for name in ("worker_profile_release_sha256",
            "model_profile_release_sha256", "worker_program_sha256", "engine_release_sha256",
            "output_schema_sha256")}
    old = DelegatedHouseholdDigitalWorkSpec.model_construct(origin=origin,
        grant=grant.model_copy(update={"capability_ids": ["home_assistant_request"]}),
        worker_profile_id="worker:digital-work:v4", model_profile_id=current.model_profile_id,
        owner_principal_id="owner", principal_binding_id="binding",
        created_at="2026-09-23T00:00:00Z", deadline="2026-09-23T00:05:00Z", **release_fields(legacy))
    authority.validate_read_spec(old)
    with pytest.raises(ValueError, match="current authority"):
        authority.validate_spec(old)
    forged = old.model_copy(update={"grant": old.grant.model_copy(update={"maximum_tool_calls": 7})})
    with pytest.raises(ValueError, match="current authority"):
        authority.validate_read_spec(forged)
    resumed = old.model_copy(update=release_fields(current))
    authority.validate_spec(resumed)
    assert resumed.grant == old.grant


def test_configured_programming_tools_are_admitted_and_bound_to_release(tmp_path, monkeypatch):
    import json
    settings, bundle = configuration()
    config = tmp_path / "programming.json"
    config.write_text(json.dumps({"url": "http://127.0.0.1:44719",
        "token_file": str(tmp_path / "token"), "timeout_seconds": 120}))
    monkeypatch.setenv("EVA_PROGRAMMING_WORKSPACE_CLIENT_FILE", str(config))
    first = build_digital_profile(settings, bundle)
    assert {"list_programming_tools", "use_programming_tool", "write_programming_file", "start_programming_process"} <= set(first.grant().capability_ids)
    config.write_text(json.dumps({"url": "http://127.0.0.1:44720",
        "token_file": str(tmp_path / "token"), "timeout_seconds": 120}))
    assert first.release != build_digital_profile(settings, bundle).release


def test_longer_future_tasks_preserve_exact_historical_grants_and_limits():
    settings, bundle = configuration()
    current = build_digital_profile(settings, bundle)
    old = replace(current, timeout_seconds=300, historical_grants=())
    assert current.timeout_seconds == 1200
    assert current.accepts_grant(old.grant())
    assert current.accepts_prior_profile(old.grant(), old.release.worker_profile_release_sha256)
    assert current.grant().maximum_model_runs == old.grant().maximum_model_runs == 32
    assert current.grant().maximum_tool_calls == old.grant().maximum_tool_calls == 31
    assert current.grant().maximum_total_tokens == old.grant().maximum_total_tokens
    older = [g for g in current.compatible_grants() if g.maximum_model_runs == 8]
    assert older and all(g.timeout_seconds == 300 for g in older)
