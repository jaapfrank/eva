"""Let HA automations ask the same device-bound Eva without handling secrets."""
from uuid import uuid4

import probatio as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry

from . import DOMAIN
from .client import EvaVoiceBusy, EvaVoiceUnavailable


SERVICE_ASK = "ask"
ASK_SCHEMA = vol.Schema({
    vol.Required("satellite_id"): vol.All(str, vol.Length(max=200), vol.Match(r"^assist_satellite\.[a-z0-9_]+$")),
    vol.Required("text"): vol.All(str, vol.Length(min=1, max=16_384)),
    vol.Optional("conversation_id"): vol.All(str, vol.Length(min=1, max=200)),
    vol.Optional("language"): vol.All(str, vol.Length(min=1, max=32)),
})


def _client_for_satellite(hass, satellite_id):
    satellite = entity_registry.async_get(hass).async_get(satellite_id)
    if satellite is None or satellite.disabled_by is not None or not satellite.device_id:
        raise ServiceValidationError("Deze speaker is niet actief aan Eva gekoppeld.")
    clients = hass.data.get(DOMAIN, {})
    matching = [clients[entry.entry_id]
                for entry in hass.config_entries.async_entries(DOMAIN)
                if entry.state is ConfigEntryState.LOADED
                and entry.entry_id in clients
                and satellite_id in entry.data.get("satellite_ids", ())]
    if len(matching) != 1:
        raise ServiceValidationError("Deze speaker vereist precies één geladen Eva-koppeling.")
    return matching[0], satellite.device_id


@callback
def async_register_services(hass):
    """Register once; unloaded entries fail at invocation rather than hiding the action."""
    if hass.services.has_service(DOMAIN, SERVICE_ASK):
        return

    async def ask(call):
        text = call.data["text"]
        if not text.strip():
            raise ServiceValidationError("De vraag aan Eva mag niet leeg zijn.")
        satellite_id = call.data["satellite_id"]
        client, device_id = _client_for_satellite(hass, satellite_id)
        conversation_id = call.data.get("conversation_id", "automation:" + uuid4().hex)
        language = call.data.get("language", hass.config.language)
        try:
            # Await the existing client directly so stop/unload cancellation
            # reaches its request-specific remote interrupt handling.
            answer = await client.respond(satellite_id=satellite_id, device_id=device_id,
                conversation_id=conversation_id, text=text, language=language, interrupt_existing=False,
                delivery_mode="automation")
        except EvaVoiceBusy:
            raise ServiceValidationError("Eva is op deze speaker al met een aanvraag bezig.") from None
        except EvaVoiceUnavailable:
            raise HomeAssistantError("Eva kon deze aanvraag niet bevestigen.") from None
        current_client, current_device = _client_for_satellite(hass, satellite_id)
        if current_client is not client or current_device != device_id:
            raise ServiceValidationError("De speakerkoppeling wijzigde tijdens de aanvraag.")
        if call.return_response:
            return {"text": answer.text, "conversation_id": conversation_id,
                    "continue_conversation": answer.continue_conversation}
        return None

    hass.services.async_register(DOMAIN, SERVICE_ASK, ask, schema=ASK_SCHEMA,
                                 supports_response=SupportsResponse.OPTIONAL)
