"""Choose duplicate schemas that the contact model can discover on demand."""


def discoverable_contact_actions(provider_settings):
    """Defer only the exact gateway catalog exposed by the connector registry.

    Matching all settings retains the original endpoint and principal policy.
    The model additionally requires authorized discovery tools on the current
    turn before omitting any direct schema.
    """
    if provider_settings is None:
        return ()
    providers = {item.provider_id: item.settings for item in provider_settings.providers}
    gateway = providers.get("mcp_gateway")
    autonomy = providers.get("mcp_autonomy")
    if gateway is None or getattr(autonomy, "configured_catalog", None) != gateway:
        return ()
    return tuple(spec.descriptor.name for spec in gateway.specs)
