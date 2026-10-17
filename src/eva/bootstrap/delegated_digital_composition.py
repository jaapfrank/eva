"""Shared admission/worker composition for private connector-backed work."""
from dataclasses import asdict, is_dataclass, replace
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.bootstrap.delegated_work_result_reader import DelegatedWorkResultReader
from eva.bootstrap.background_browser_context import configured_browser_client, BackgroundBrowserContext
from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.postgres.background_work_progress import progress_descriptor
from eva.adapters.postgres.component_improvement_actions import component_descriptors
from eva.adapters.postgres.programming_actions import programming_descriptors
from eva.adapters.connectors.programming_workspace import configured_programming_workspace
from eva.adapters.connectors.component_evaluation import configured_component_evaluator
from eva.adapters.connectors.procedure_execution import configured_procedure_executor

_CONNECTORS = frozenset({"connect_service", "discover_connector_tools",
                        "manage_connected_services", "use_connector_tool"})
_PROCEDURES = frozenset({"list_reusable_procedures", "resolve_reusable_procedure",
                         "designate_reusable_procedure", "save_workspace_document", "run_reusable_procedure",
                         "register_own_contribution"})
_HOME_ASSISTANT = frozenset({"home_assistant_request", "read_home_assistant_inventory"})
_DIGITAL_PROVIDERS = frozenset({"mcp_autonomy", "procedure_references", "mcp_gateway", "home_assistant"})
_ROOT = Path(__file__).resolve().parents[1]
_EXECUTION_SOURCES = (
    "bootstrap/completed_contributions.py",
    "adapters/postgres/completed_contribution.py",
    "adapters/postgres/initiative_adoption.py",
    "bootstrap/delegated_digital_composition.py",
    "bootstrap/background_research.py",
    "adapters/model/research_tools.py",
    "adapters/model/research_transport.py",
    "bootstrap/household_digital_work.py",
    "bootstrap/household_work.py",
    "bootstrap/delegated_work_result_reader.py",
    "bootstrap/delegated_work_owner_service.py",
    "adapters/postgres/delegated_work_actions.py",
    "bootstrap/delegated_digital_runner.py", "bootstrap/native_initiative_work.py",
    "adapters/connectors/delegated_completion.py",
    "adapters/connectors/home_assistant_completion.py",
    "contracts/tool_deadlines.py",
    "contracts/component_target.py",
    "adapters/postgres/initiative_execution.py", "application/initiative_execution.py",
    "bootstrap/background_browser_context.py",
    "adapters/connectors/browser_session.py",
    "adapters/connectors/browser_autostart.py",
    "adapters/postgres/background_work_progress.py",
    "adapters/postgres/work_corrections.py",
    "adapters/postgres/component_improvement_actions.py",
    "adapters/postgres/programming_actions.py",
    "adapters/connectors/programming_workspace.py",
    "application/component_improvements.py",
    "adapters/connectors/component_evaluation.py",
    "adapters/connectors/procedure_execution.py",
    "bootstrap/private_conversation_resources.py",
    "adapters/model/pydantic_conversation.py",
    "providers/mcp_autonomy.py",
    "providers/mcp_discovery.py",
    "providers/mcp_registration.py",
    "providers/mcp_owner_authority.py",
    "adapters/postgres/mcp_connector_registry.py",
    "adapters/postgres/connector_results.py",
    "providers/procedure_reference_actions.py",
    "adapters/postgres/procedure_reference_host.py",
    "application/procedure_references.py",
    "adapters/postgres/memory_outbox.py",
    "adapters/postgres/external_conversation_tools.py",
    "adapters/connectors/mcp_gateway.py",
    "providers/mcp_gateway.py",
    "providers/home_assistant.py",
    "adapters/connectors/home_assistant_api.py",
    "adapters/connectors/home_assistant_admin.py",
    "adapters/connectors/home_assistant_automations.py",
)


def _digital_document_descriptor(tool):
    if tool.name != "save_workspace_document":
        return tool
    schema = deepcopy(tool.input_schema)
    schema["properties"]["content"]["type"] = ["string", "null"]
    schema["properties"]["component_step"] = {"type": ["integer", "null"], "minimum": 0}
    schema["required"] = list(dict.fromkeys([*schema.get("required", []), "content", "component_step"]))
    return replace(tool, input_schema=schema, description=tool.description +
        " Gebruik precies één inhoudsbron: content, of component_step van een eerder "
        "eigen componentvoorstel, meetrapport of installatiepakket in deze taak. "
        "Zet het ongebruikte veld op null; een lege content-tekenreeks is geldige inhoud. "
        "component_step bewaart dat exacte resultaat zonder het opnieuw uit te schrijven. "
        "Bestaande documentlimieten en toegangsregels blijven gelden.")


def digital_tools(bundle):
    if bundle is None:
        raise ValueError("digital work requires existing connector tools")
    admitted = _CONNECTORS | _PROCEDURES | _HOME_ASSISTANT
    descriptors = tuple(_digital_document_descriptor(tool) for tool in bundle.conversation_tools if tool.name in admitted)
    routes = tuple(tool for tool in bundle.external_tools if set(tool.action_names) & admitted)
    names = {tool.name for tool in descriptors}
    if not names or names != {name for tool in routes for name in tool.action_names if name in admitted}:
        raise ValueError("digital connector descriptions and routes differ")
    return (*descriptors, *DelegatedWorkResultReader.conversation_tools(),
            progress_descriptor(), *component_descriptors(), *programming_descriptors()), routes


def _configuration_value(value):
    if is_dataclass(value):
        return _configuration_value(asdict(value))
    if isinstance(value, dict):
        return {key: _configuration_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_configuration_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("digital provider settings contain unsupported values")


def build_digital_profile(settings, provider_bundle):
    """The core and worker seal identical settings; no model chooses its profile."""
    from eva.bootstrap.delegated_work_provider import ENGINE_RELEASE_SHA256
    descriptors, _ = digital_tools(provider_bundle)
    budget = settings.runtime_resource_budget
    if budget is None or settings.external_provider is None:
        raise ValueError("digital work requires the installed resource budget and providers")
    # Provider settings are hashed in memory, never recorded or emitted. Current
    # provider authority still checks rotating credentials/mandates at execution.
    providers = []
    for loaded in settings.external_provider.providers:
        if loaded.provider_id not in _DIGITAL_PROVIDERS:
            continue
        if loaded.provider_id == "mcp_autonomy" and not is_dataclass(loaded.settings):
            raise ValueError("digital work requires serializable provider settings")
        providers.append({"provider": loaded.provider_id,
                          "configuration": _configuration_value(loaded.settings)})
    if sum(item["provider"] == "mcp_autonomy" for item in providers) != 1:
        raise ValueError("digital work requires the existing MCP connector provider")
    fingerprint = canonical_sha256({
        "contract": "EvaPrivateDigitalComposition/v1",
        "model": settings.conversation_model,
        "reasoning_effort": settings.conversation_reasoning_effort,
        "budget": asdict(budget), "providers": providers,
        "browser_availability": _configuration_value(configured_browser_client()),
        "component_evaluator": _configuration_value(configured_component_evaluator()),
        "programming_workspace": _configuration_value(configured_programming_workspace()),
        "procedure_executor": _configuration_value(configured_procedure_executor()),
        "descriptors": [asdict(tool) for tool in descriptors],
        "memory": {"url": settings.hindsight_url, "namespace": settings.hindsight_bank_namespace,
                   "budget": settings.hindsight_recall_budget, "tokens": settings.hindsight_recall_max_tokens,
                   "nodes": settings.hindsight_recall_node_budget},
    })
    historical = DelegatedDigitalProfile(
        model_profile_id="model:conversation:" + settings.conversation_model,
        configuration_sha256=fingerprint, engine_release_sha256=ENGINE_RELEASE_SHA256,
        capability_ids=(*tuple(tool.name for tool in descriptors), "research_current_options"),
        execution_source_sha256=tuple((name, hashlib.sha256((_ROOT / name).read_bytes()).hexdigest())
                                      for name in _EXECUTION_SOURCES),
        maximum_input_tokens=budget.max_input_tokens * min(settings.conversation_maximum_requests, 8),
        maximum_response_input_tokens=budget.max_input_tokens,
        maximum_output_tokens=budget.max_output_tokens * min(settings.conversation_maximum_requests, 8),
        maximum_response_output_tokens=budget.max_output_tokens,
        maximum_model_runs=min(settings.conversation_maximum_requests, 8),
        maximum_completed_model_responses=min(settings.conversation_maximum_requests, 8),
    )
    # Use the host's existing request allowance, within the digital v4 schema.
    # One model response must remain available to return the verified result.
    # Only new tasks receive this envelope; read/resume retains exact old grants.
    runs = min(settings.conversation_maximum_requests, 32)
    current = replace(historical,
        maximum_model_runs=runs, maximum_completed_model_responses=runs,
        maximum_tool_calls=max(1, runs - 1),
        maximum_input_tokens=budget.max_input_tokens * runs,
        maximum_output_tokens=budget.max_output_tokens * runs,
        historical_grants=historical.compatible_grants())
    # A new general-work task may contain several dependent model/tool rounds.
    # Preserve exact previous envelopes for reading/resuming old work; never
    # extend an already admitted task or increase its request/token allowance.
    return replace(current, timeout_seconds=1200,
                   historical_grants=current.compatible_grants())


def build_digital_runner(settings, store, *, provider_bundle=None, household_authority=None):
    """Reuse the foreground identity, memory, connector and owner authorities."""
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    from eva.adapters.postgres.telegram_group_consent import PostgresTelegramGroupConsentStore
    from eva.bootstrap.delegated_digital_runner import DelegatedDigitalRunner
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    from eva.bootstrap.private_conversation_resources import (
        load_conversation_identity_pack, build_private_conversation_resources)
    from eva.bootstrap.telegram_ingress import load_principal_bindings
    from eva.bootstrap.household_voice import load_household_voice_authority
    from eva.providers.mcp_autonomy import _SourceTurnLoader
    from eva.providers.runtime import build_external_provider_bundle
    from eva.providers.contracts import ExternalProviderSettings

    engine = store._engine
    selected_providers = ExternalProviderSettings(tuple(loaded for loaded in
        (() if settings.external_provider is None else settings.external_provider.providers)
        if loaded.provider_id in _DIGITAL_PROVIDERS))
    from eva.bootstrap.completed_contributions import with_completed_contributions
    bundle = with_completed_contributions(settings, engine,
        provider_bundle or build_external_provider_bundle(selected_providers, engine))
    profile = build_digital_profile(settings, bundle)
    descriptors, routes = digital_tools(bundle)
    owner, binding = UUID(settings.background_principal_id), UUID(settings.dashboard_principal_binding_id)
    def bridge_principal():
        return load_principal_bindings(settings.telegram_principal_bindings_file).private_bridge_principal(
            principal_id=str(owner), principal_binding_id=binding)
    service = DelegatedWorkOwnerService(store, owner_principal_id=owner,
        principal_binding_id=binding, digital_profile=profile,
        conversation_scope=external_timeline_scope(settings.background_tenant_id,
            settings.background_installation_id, "digital conversation scope is required"),
        private_bridge_principal_loader=(bridge_principal if settings.telegram_principal_bindings_file else None))
    group_consent = PostgresTelegramGroupConsentStore(engine,
        tenant_id=settings.background_tenant_id, installation_id=settings.background_installation_id)
    identity_pack = load_conversation_identity_pack(settings)
    household_voice = load_household_voice_authority(settings, runtime_identity=identity_pack,
        identity_loader=lambda: load_conversation_identity_pack(settings))
    memory, identity = build_private_conversation_resources(settings, engine,
        group_consent=group_consent, household_voice=household_voice,
        identity_pack=identity_pack)
    # Foreground-style defaults apply outside an admitted task. The runner
    # binds each background response to that task's remaining deadline.
    model = PydanticConversationModel(auth_file=Path(settings.conversation_auth_file),
        model=settings.conversation_model, reasoning_effort=settings.conversation_reasoning_effort,
        timeout_seconds=120.0, research_timeout_seconds=120.0,
        resource_budget=settings.runtime_resource_budget, conversation_tools=descriptors)
    def initiative_authority():
        from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
        from eva.bootstrap.native_initiative_work import configured_initiative_authority
        return configured_initiative_authority(PostgresOwnerInitiatives(engine,
            tenant_id=settings.background_tenant_id, installation_id=settings.background_installation_id,
            owner=owner, binding=binding, authority_scope=store.scope))
    store._initiative_authority_loader = initiative_authority
    source_loader = _SourceTurnLoader(engine, SimpleNamespace(
        scope=service.conversation_scope, authorize_turn=service.validate_authority))
    if household_authority is not None:
        from eva.bootstrap.household_digital_work import with_household_digital, HouseholdDigitalSourceLoader
        authority = with_household_digital(household_authority, profile)
        if getattr(authority, "digital_profile", None) is not None:
            store._household_authorizer = authority.validate_spec
            private_loader = source_loader
            shared_loader = HouseholdDigitalSourceLoader(engine, service.conversation_scope, authority)
            def source_loader(source):
                loader = shared_loader if source.get("audience", {}).get("kind") == "household" else private_loader
                return loader(source)
    return DelegatedDigitalRunner(store, profile, source_loader=source_loader,
        memory=memory, identity=identity, model=model, connector_tools=(*routes, DelegatedWorkResultReader(service)),
        browser_context=BackgroundBrowserContext(configured_browser_client()))


class DigitalEnabledDelegatedRunner:
    """Dispatch one extra task contract within the existing worker lifecycle."""
    def __init__(self, existing, digital):
        self.existing, self.digital = existing, digital

    async def run(self, spec, *, call_id, cancellation_event=None):
        from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
        runner = self.digital if isinstance(spec, DelegatedDigitalWorkSpec) else self.existing
        return await runner.run(spec, call_id=call_id, cancellation_event=cancellation_event)
