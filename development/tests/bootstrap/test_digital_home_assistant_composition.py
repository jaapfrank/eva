"""Explicit Home Assistant admission preserves the existing authorized route."""
from types import SimpleNamespace

import pytest

from eva.bootstrap.delegated_digital_composition import digital_tools, _DIGITAL_PROVIDERS, _EXECUTION_SOURCES


def descriptor(name):
    return SimpleNamespace(name=name)


def bundle(descriptors, routes):
    return SimpleNamespace(conversation_tools=tuple(map(descriptor, descriptors)), external_tools=tuple(routes))


def test_only_explicit_general_home_assistant_action_is_added():
    general = SimpleNamespace(action_names=("home_assistant_request",))
    legacy = SimpleNamespace(action_names=("home_assistant_light_on",))
    future = SimpleNamespace(action_names=("home_assistant_unreviewed",))
    connector = SimpleNamespace(action_names=("connect_service",))
    descriptors, routes = digital_tools(bundle(
        ["home_assistant_request", "home_assistant_light_on", "home_assistant_unreviewed", "connect_service"],
        [general, legacy, future, connector]))
    names = {tool.name for tool in descriptors}
    assert {"home_assistant_request", "connect_service"} <= names
    assert "home_assistant_light_on" not in names
    assert "home_assistant_unreviewed" not in names
    assert routes == (general, connector)
    assert routes[0] is general
    assert "home_assistant" in _DIGITAL_PROVIDERS
    assert {"providers/home_assistant.py", "adapters/connectors/home_assistant_api.py",
            "adapters/connectors/home_assistant_admin.py"} <= set(_EXECUTION_SOURCES)


@pytest.mark.parametrize("has_descriptor,has_route", [(True, False), (False, True)])
def test_general_home_assistant_mismatch_fails_closed(has_descriptor, has_route):
    names = ["connect_service"] + (["home_assistant_request"] if has_descriptor else [])
    routes = [SimpleNamespace(action_names=("connect_service",))]
    if has_route:
        routes.append(SimpleNamespace(action_names=("home_assistant_request",)))
    with pytest.raises(ValueError, match="descriptions and routes differ"):
        digital_tools(bundle(names, routes))


def test_background_dispatch_preserves_route_authorization():
    from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions
    from eva.application.conversation import ConversationConflict

    calls = []
    route = SimpleNamespace(action_names=("home_assistant_request",),
        available_for=lambda turn: False,
        execute=lambda *args, **kwargs: calls.append((args, kwargs)))
    _, routes = digital_tools(bundle(["home_assistant_request"], [route]))
    authorization = []
    actions = BackgroundConnectorActions(routes, ("home_assistant_request",),
        authorize=lambda turn: authorization.append(turn), maximum_calls=1)
    turn = object()
    assert actions.available_action_names(turn) == ()
    with pytest.raises(ConversationConflict, match="not admitted"):
        actions.execute_step(turn, SimpleNamespace(name="home_assistant_request"), step_index=0)
    assert authorization
    assert not calls
    assert actions.calls == 0


def test_general_home_assistant_profile_grant_accepts_only_explicit_action():
    from dataclasses import replace
    from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile

    profile = DelegatedDigitalProfile(
        model_profile_id="model:configured", configuration_sha256="a" * 64,
        engine_release_sha256="b" * 64,
        capability_ids=("home_assistant_request",),
        execution_source_sha256=(("providers/home_assistant.py", "c" * 64),))
    grant = profile.grant()
    assert grant.capability_ids == ["home_assistant_request"]
    assert grant.child_network_access is False
    assert grant.context_refs == []
    with pytest.raises(ValueError, match="explicit connector actions"):
        replace(profile, capability_ids=("home_assistant_unreviewed",))
