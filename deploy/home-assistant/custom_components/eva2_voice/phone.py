"""Optional owner panel using HA authentication and Eva's separate owner token."""
from pathlib import Path
from aiohttp import web
import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .phone_transport import PhoneTransport

KEY = "eva2_owner_voice_panel"
ASSETS = Path(__file__).with_name("phone-assets")


async def setup_phone(hass, entry):
    if not all((ASSETS / name).is_file() for name in ("panel.js", "livekit-client.js")):
        raise ValueError("Build the pinned phone assets before enabling the owner panel")
    holder = hass.data.setdefault(KEY, {"transport": None, "entry_id": None, "registered": False})
    if holder["transport"] is not None:
        raise ValueError("Only one owner voice gateway may be configured")
    async def user_allowed(user_id):
        user = await hass.auth.async_get_user(user_id)
        return user is not None and user.is_active and user.is_admin
    transport = PhoneTransport(async_get_clientsession(hass), entry.data["url"], user_allowed)
    holder.update(transport=transport, entry_id=entry.entry_id)
    if not holder["registered"]:
        class Assets(HomeAssistantView):
            url = "/api/eva2_voice/assets/{filename}"
            name = "api:eva2_voice:assets"
            requires_auth = False
            async def get(self, request, filename):
                if filename not in {"panel.js", "livekit-client.js"}:
                    raise web.HTTPNotFound()
                return web.FileResponse(ASSETS / filename, headers={"Cache-Control": "no-store"})
        class Signaling(HomeAssistantView):
            url = "/api/eva2_voice/rtc/{capability}/{tail:.*}"
            name = "api:eva2_voice:signaling"
            requires_auth = False
            async def get(self, request, capability, tail):
                current = holder["transport"]
                if current is None:
                    raise web.HTTPServiceUnavailable()
                return await current.signal(request, capability, tail)
        @websocket_api.websocket_command({
            vol.Required("type"): "eva2_voice/owner_session",
            vol.Required("path"): vol.In(["session/ready", "session", "session/end"]),
            vol.Required("owner_token"): vol.All(str, vol.Length(min=16, max=256)),
            vol.Optional("capability", default=None): vol.Any(None, vol.All(str, vol.Length(min=40, max=64))),
        })
        @websocket_api.require_admin
        @websocket_api.async_response
        async def owner_session(hass, connection, msg):
            current = holder["transport"]
            if current is None:
                connection.send_error(msg["id"], "unavailable", "Eva's stempaneel is uitgeschakeld.")
                return
            try:
                result = await current.request(connection.user.id, msg["path"], msg["owner_token"], msg["capability"])
            except (web.HTTPException, TimeoutError):
                connection.send_error(msg["id"], "unavailable", "Eva's stemverbinding kon niet worden geopend.")
            else:
                connection.send_result(msg["id"], result)
        hass.http.register_view(Assets())
        hass.http.register_view(Signaling())
        websocket_api.async_register_command(hass, owner_session)
        holder["registered"] = True
    try:
        await panel_custom.async_register_panel(hass, frontend_url_path="eva-owner-voice",
            webcomponent_name="eva-owner-voice-panel", sidebar_title="Praten met Eva",
            sidebar_icon="mdi:account-voice", module_url="/api/eva2_voice/assets/panel.js",
            embed_iframe=False, require_admin=True)
    except BaseException:
        holder.update(transport=None, entry_id=None)
        await transport.close()
        raise


async def unload_phone(hass, entry):
    holder = hass.data.get(KEY)
    if holder is not None and holder["entry_id"] == entry.entry_id:
        transport = holder["transport"]
        holder.update(transport=None, entry_id=None)
        frontend.async_remove_panel(hass, "eva-owner-voice")
        await transport.close()
