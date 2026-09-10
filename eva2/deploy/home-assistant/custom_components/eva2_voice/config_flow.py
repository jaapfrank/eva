"""Owner configuration for the candidate native voice connection."""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import DOMAIN
from .client import EvaVoiceClient


class EvaVoiceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EvaVoiceOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                EvaVoiceClient.validate_config(
                    url=user_input["url"], token=user_input["token"],
                    satellite_ids=frozenset(user_input["satellite_ids"]),
                )
            except (ValueError, TypeError, KeyError):
                errors["base"] = "invalid_connection"
            else:
                return self.async_create_entry(title="Eva2", data=user_input)
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema({
            vol.Required("url"): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
            vol.Required("token"): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Required("satellite_ids"): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="assist_satellite", multiple=True)),
        }))


class EvaVoiceOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required("owner_voice_panel", default=self.config_entry.options.get("owner_voice_panel", False)): bool,
        }))
