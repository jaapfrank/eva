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
    assert first.grant().maximum_input_tokens == 12000
    assert "get_delegated_work" in first.capability_ids
    assert "use_connector_tool" in first.capability_ids
    assert first.maximum_model_runs == 8
    assert first.maximum_response_output_tokens == 1200
    assert first.grant().maximum_output_tokens == 9600
    assert first.maximum_tool_calls == 6
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
    assert set(profile.capability_ids) == {"use_connector_tool", "get_delegated_work",
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


def test_legacy_budget_is_exact_and_bound_to_current_release():
    settings, bundle = configuration()
    profile = build_digital_profile(settings, bundle)
    current = profile.grant()
    legacy = current.model_copy(update={"maximum_output_tokens": 1200,
                                      "maximum_total_tokens": current.maximum_input_tokens + 1200})
    assert profile.accepts_grant(legacy)
    assert profile.accepts_grant(current)
    for changed in ({"maximum_output_tokens": 1201, "maximum_total_tokens": current.maximum_input_tokens + 1201},
                    {"maximum_total_tokens": current.maximum_total_tokens},
                    {"maximum_tool_calls": 7}, {"maximum_model_runs": 9}):
        assert not profile.accepts_grant(legacy.model_copy(update=changed))
