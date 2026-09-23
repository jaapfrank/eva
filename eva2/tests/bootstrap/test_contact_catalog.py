"""Only identical installed connector bindings permit deferred contact schemas."""
from types import SimpleNamespace as NS
from eva.bootstrap.contact_catalog import discoverable_contact_actions


def settings(gateway, mirrored):
    return NS(providers=(NS(provider_id="mcp_gateway", settings=gateway),
                         NS(provider_id="mcp_autonomy", settings=NS(configured_catalog=mirrored))))


def test_exact_catalog_can_be_discovered_and_missing_provider_keeps_direct_tools():
    gateway = NS(specs=(NS(descriptor=NS(name="read_file")),), principal_policy="owner")
    assert discoverable_contact_actions(settings(gateway, gateway)) == ("read_file",)
    assert discoverable_contact_actions(None) == ()
    assert discoverable_contact_actions(NS(providers=())) == ()
    assert discoverable_contact_actions(settings(gateway, None)) == ()


def test_different_principal_policy_does_not_hide_original_tool():
    gateway = NS(specs=(NS(descriptor=NS(name="read_file")),), principal_policy="owner")
    other = NS(specs=gateway.specs, principal_policy="another-owner")
    assert discoverable_contact_actions(settings(gateway, other)) == ()
