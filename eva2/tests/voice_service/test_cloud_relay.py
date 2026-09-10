import asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest
from eva.voice_service.media import home_assistant_ice_servers


@pytest.mark.asyncio
async def test_existing_ha_cloud_credentials_are_ephemeral_and_redirects_denied():
    seen=[]
    redirect=False
    async def handler(request):
        if redirect:raise web.HTTPFound("/redirect-target")
        socket=web.WebSocketResponse();await socket.prepare(request)
        await socket.send_json({"type":"auth_required"})
        seen.append(await socket.receive_json())
        await socket.send_json({"type":"auth_ok"})
        query=await socket.receive_json();assert query=={"id":1,"type":"cloud/webrtc/ice_servers"}
        await socket.send_json({"id":1,"success":True,"result":[{"urls":"turn:example.invalid:3478","username":"short-lived-user","credential":"short-lived-secret"}]})
        await socket.close();return socket
    async def forbidden(request):raise AssertionError("Followed credential redirect")
    app=web.Application();app.router.add_get("/api/websocket",handler);app.router.add_get("/redirect-target",forbidden)
    async with TestServer(app) as backend:
        rows=await home_assistant_ice_servers(str(backend.make_url("/")),"existing-ha-token")
        assert rows[0]["urls"]==["turn:example.invalid:3478"]
        assert seen==[{"type":"auth","access_token":"existing-ha-token"}]
        redirect=True
        with pytest.raises(ValueError,match="redirect"):
            await home_assistant_ice_servers(str(backend.make_url("/")),"existing-ha-token")
        assert len(seen)==1
