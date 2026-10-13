"""Explicit registry for provider plug-ins shipped in the Eva distribution.

The generic runtime imports only this registry.  Provider-specific settings,
tool descriptions, protocols and composition stay in their own plug-in module.
"""

from __future__ import annotations

from eva.providers.agentmail import AgentMailProviderFactory
from eva.providers.contracts import RuntimeProviderFactory
from eva.providers.home_assistant import HomeAssistantProviderFactory
from eva.providers.mcp_gateway import McpGatewayProviderFactory
from eva.providers.mcp_autonomy import McpAutonomyProviderFactory
from eva.providers.document_goal_actions import DocumentGoalActionsProviderFactory
from eva.providers.procedure_reference_actions import ProcedureReferenceProviderFactory


def bundled_provider_factories() -> tuple[RuntimeProviderFactory, ...]:
    return (
        AgentMailProviderFactory(),
        HomeAssistantProviderFactory(),
        McpGatewayProviderFactory(),
        McpAutonomyProviderFactory(),
        DocumentGoalActionsProviderFactory(),
        ProcedureReferenceProviderFactory(),
    )


__all__ = ["bundled_provider_factories"]
