"""Explicit lazy registry for optional providers shipped in Eva.

Provider modules own their settings, behavior and pure presentation metadata.
A missing optional provider is not a missing general Eva dependency.
"""
from __future__ import annotations

from importlib import import_module
import os

_REGISTRY = (
    ("agentmail", "eva.providers.agentmail", "AgentMailProviderFactory"),
    ("home_assistant", "eva.providers.home_assistant", "HomeAssistantProviderFactory"),
    ("mcp_gateway", "eva.providers.mcp_gateway", "McpGatewayProviderFactory"),
    ("mcp_autonomy", "eva.providers.mcp_autonomy", "McpAutonomyProviderFactory"),
    ("document_goal_actions", "eva.providers.document_goal_actions", "DocumentGoalActionsProviderFactory"),
    ("procedure_references", "eva.providers.procedure_reference_actions", "ProcedureReferenceProviderFactory"),
)
_PRESENTATIONS = (("home_assistant", "eva.providers.home_assistant_policy"),)


def bundled_provider_ids():
    return frozenset(item[0] for item in _REGISTRY)


def optional_module(name):
    try:
        return import_module(name)
    except ModuleNotFoundError as error:
        # Only the absent optional module itself may be skipped. A broken
        # installed module with a missing transitive dependency remains an error.
        if error.name == name:
            return None
        raise


def bundled_provider_factories(*, exclude=()):
    factories = []
    for provider_id, module_name, factory_name in _REGISTRY:
        if provider_id in exclude:
            continue
        module = optional_module(module_name)
        if module is not None:
            factory = getattr(module, factory_name)()
            if factory.provider_id != provider_id:
                raise RuntimeError("bundled provider identity differs")
            factories.append(factory)
    return tuple(factories)


def bundled_presentations():
    # Pure, installed metadata also supports retained old tool-call names.
    # It neither configures a provider nor exposes any absent tool to the model.
    disabled = {value.strip() for value in os.environ.get(
        "EVA_EXTERNAL_PROVIDERS_DISABLED", "").split(",") if value.strip()}
    return tuple(module for provider_id, name in _PRESENTATIONS
                 if provider_id not in disabled
                 and (module := optional_module(name)) is not None)
