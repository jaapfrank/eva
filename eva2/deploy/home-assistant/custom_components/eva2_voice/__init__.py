"""Native HA voice candidate. Requires the corresponding Eva2 core endpoint."""
from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import EvaVoiceClient

DOMAIN = "eva2_voice"


async def async_setup_entry(hass, entry):
    client = EvaVoiceClient(
        async_get_clientsession(hass), url=entry.data["url"],
        token=entry.data["token"], satellite_ids=frozenset(entry.data["satellite_ids"]),
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client
    try:
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.CONVERSATION])
    except BaseException:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await client.close()
        raise
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, [Platform.CONVERSATION]):
        return False
    await hass.data[DOMAIN].pop(entry.entry_id).close()
    return True
