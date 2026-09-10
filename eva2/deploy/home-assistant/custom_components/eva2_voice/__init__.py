"""Native HA voice candidate. Requires the corresponding Eva2 core endpoint."""
import logging
from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import EvaVoiceClient

DOMAIN = "eva2_voice"
_LOGGER = logging.getLogger(__name__)


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
    if entry.options.get("owner_voice_panel", False):
        try:
            from .phone import setup_phone
            await setup_phone(hass, entry)
        except Exception:
            _LOGGER.exception("Owner voice panel unavailable; household conversation remains enabled")
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, [Platform.CONVERSATION]):
        return False
    from .phone import unload_phone
    await unload_phone(hass, entry)
    await hass.data[DOMAIN].pop(entry.entry_id).close()
    return True
