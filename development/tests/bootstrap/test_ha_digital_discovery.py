"""The existing compact HA reader accompanies general API access in work."""
from dataclasses import replace
from types import SimpleNamespace

from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
from eva.adapters.connectors.home_assistant_admin import HomeAssistantHouseholdTool
from eva.application.conversation import ConversationToolDescriptor
from eva.bootstrap.delegated_digital_composition import build_digital_profile, digital_tools
from tests.bootstrap.test_delegated_digital_composition import configuration


def test_worker_can_discover_compactly_then_read_full_configuration():
    settings, bundle = configuration()
    ha = (*HomeAssistantApiTool.conversation_tools(), *HomeAssistantHouseholdTool.conversation_tools())
    names = tuple(tool.name for tool in ha)
    routes = tuple(SimpleNamespace(action_names=(name,)) for name in names)
    bundle = replace(bundle, conversation_tools=(*bundle.conversation_tools, *ha),
                     external_tools=(*bundle.external_tools, *routes))
    profile = build_digital_profile(settings, bundle)
    assert {"read_home_assistant_inventory", "home_assistant_request"} <= set(profile.grant().capability_ids)
    descriptors, selected = digital_tools(bundle)
    assert set(names) <= {tool.name for tool in descriptors}
    assert all(route in selected for route in routes)
    assert profile.grant().maximum_input_tokens == settings.runtime_resource_budget.max_input_tokens


def test_compact_discovery_does_not_admit_legacy_effect_or_management_tools():
    _, bundle = configuration()
    excluded = ConversationToolDescriptor("home_assistant_call_service", "Unadmitted legacy effect",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False})
    route = SimpleNamespace(action_names=(excluded.name,))
    expanded = replace(bundle, conversation_tools=(*bundle.conversation_tools, excluded),
                       external_tools=(*bundle.external_tools, route))
    descriptors, routes = digital_tools(expanded)
    assert excluded not in descriptors
    assert route not in routes
