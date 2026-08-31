"""Build configured installer probes at the connector boundary."""

from __future__ import annotations

from eva.adapters.connectors.installer_agentmail_probe import (
    AgentMailInstallerReadOnlyProbe,
)
from eva.adapters.connectors.installer_home_assistant_probe import (
    HomeAssistantInstallerReadOnlyProbe,
)
from eva.application.installer import InstallerProviderProbe


def configured_installer_probe(slot_id: str, value: str) -> InstallerProviderProbe:
    """Validate configuration and return the matching read-only probe."""

    if slot_id == "home_assistant":
        return HomeAssistantInstallerReadOnlyProbe(base_url=value)
    if slot_id == "email":
        return AgentMailInstallerReadOnlyProbe(inbox_id=value)
    raise ValueError("installer provider configuration is unsupported")


__all__ = ["configured_installer_probe"]
