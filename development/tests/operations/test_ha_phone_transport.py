import asyncio
import importlib.util
from pathlib import Path
import sys
import time
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

MODULE = Path(__file__).parents[3] / "deploy/home-assistant/custom_components/eva2_voice/phone_transport.py"
spec = importlib.util.spec_from_file_location("eva_phone_transport_test", MODULE)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_owner_transport_and_bound_signaling_over_real_http():
    async def run():
        seen = []
        allowed = {"owner"}
        async def user_allowed(user_id): return user_id in allowed
        async def control(request):
            seen.append((request.method, request.path, request.headers.get("Authorization")))
            if request.headers.get("Authorization") != "Bearer existing-owner-secret":
                raise web.HTTPUnauthorized()
            return web.json_response({"token": "room-jwt", "url": "ws://localhost:7880", "room": "eva-voice-" + "a"*32} if request.path.endswith("/session/leased") else {"ready": True})
        async def signal(request):
            if request.headers.get("Upgrade", "").lower() != "websocket":
                return web.Response(text="valid")
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            async for message in socket:
                if message.type == aiohttp.WSMsgType.BINARY: await socket.send_bytes(message.data)
                elif message.type == aiohttp.WSMsgType.TEXT: await socket.send_str(message.data)
            return socket
        upstream = web.Application()
        upstream.router.add_route("*", "/owner-voice/session{tail:.*}", control)
        upstream.router.add_get("/owner-voice/livekit/{tail:.*}", signal)
        async with TestServer(upstream) as backend, aiohttp.ClientSession() as http:
            transport = module.PhoneTransport(http, str(backend.make_url("/")), user_allowed)
            assert (await transport.request("owner", "session/ready", "wrong-owner-secret"))["status"] == 401
            try: await transport.request("guest", "session", "existing-owner-secret")
            except web.HTTPForbidden: pass
            else: raise AssertionError("Guest must not open owner session")
            reply = await transport.request("owner", "session", "existing-owner-secret")
            cap = reply["body"]["url"].rsplit("/", 1)[1]
            assert len(cap) >= 40 and "room-jwt" not in cap
            proxy = web.Application()
            async def forward(request):
                return await transport.signal(request, request.match_info["cap"], request.match_info["tail"])
            proxy.router.add_get("/{cap}/{tail:.*}", forward)
            async with TestClient(TestServer(proxy)) as client:
                assert (await client.get(f"/{cap}/rtc/validate?access_token=wrong" )).status == 403
                assert (await client.get(f"/{cap}/admin?access_token=room-jwt")).status == 404
                assert (await client.get(f"/{cap}/rtc/validate?access_token=room-jwt")).status == 200
                assert (await client.get(f"/{cap}/rtc/validate?access_token=room-jwt&access_token=other")).status == 403
                async with client.ws_connect(f"/{cap}/rtc?access_token=room-jwt") as socket:
                    await socket.send_bytes(b"real-protobuf-frame")
                    assert (await socket.receive()).data == b"real-protobuf-frame"
                    await socket.send_str("text-frame")
                    assert (await socket.receive()).data == "text-frame"
                    allowed.clear()
                    message = await asyncio.wait_for(socket.receive(), timeout=3)
                    assert message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED}
                assert (await client.get(f"/{cap}/rtc/validate?access_token=room-jwt")).status == 403
                assert cap not in transport.leases
                assert any(path == "/owner-voice/session/end/eva-voice-" + "a"*32 for _, path, _ in seen)
                allowed.add("owner")
                reply = await transport.request("owner", "session", "existing-owner-secret")
                cap = reply["body"]["url"].rsplit("/", 1)[1]
                lease = transport.leases[cap]
                lease.expires = time.monotonic()-1
                assert (await client.get(f"/{cap}/rtc/validate?access_token=room-jwt")).status == 403
            await transport.close()
            assert seen[1] == ("POST", "/owner-voice/session/leased", "Bearer existing-owner-secret")
    asyncio.run(run())


def test_create_cancellation_and_revocation_close_only_the_created_room():
    async def run():
        allowed = True
        entered, release = asyncio.Event(), asyncio.Event()
        closed = []
        counter = 0
        async def user_allowed(user_id): return allowed
        async def control(request):
            nonlocal counter
            if request.path.startswith("/owner-voice/session/end/"):
                closed.append(request.path.rsplit("/", 1)[1])
                return web.json_response({"closed": True})
            counter += 1
            room = "eva-voice-" + format(counter, "032x")
            entered.set()
            await release.wait()
            return web.json_response({"token": "jwt-" + str(counter), "room": room, "url": "ws://localhost:7880"})
        upstream = web.Application()
        upstream.router.add_post("/owner-voice/session{tail:.*}", control)
        async with TestServer(upstream) as backend, aiohttp.ClientSession() as http:
            transport = module.PhoneTransport(http, str(backend.make_url("/")), user_allowed)
            task = asyncio.create_task(transport.request("owner", "session", "existing-owner-secret"))
            await entered.wait()
            task.cancel()
            release.set()
            try: await task
            except asyncio.CancelledError: pass
            else: raise AssertionError("Caller cancellation must propagate")
            assert closed == ["eva-voice-" + format(1, "032x")]
            assert not transport.leases
            entered.clear(); release.clear()
            task = asyncio.create_task(transport.request("owner", "session", "existing-owner-secret"))
            await entered.wait()
            allowed = False
            release.set()
            try: await task
            except web.HTTPForbidden: pass
            else: raise AssertionError("Revoked during creation")
            assert closed[-1] == "eva-voice-" + format(2, "032x")
            assert not transport.leases
            allowed = True
            first = await transport.request("owner", "session", "existing-owner-secret")
            first_cap = first["body"]["url"].rsplit("/", 1)[1]
            second = await transport.request("owner", "session", "existing-owner-secret")
            second_cap = second["body"]["url"].rsplit("/", 1)[1]
            await asyncio.shield(transport._revoke(first_cap))
            assert closed[-1] == "eva-voice-" + format(3, "032x")
            assert second_cap in transport.leases
            # No signaling socket was ever opened; access revocation must still
            # close the exact media room, not merely discard a capability.
            allowed = False
            for _ in range(30):
                if second_cap not in transport.leases: break
                await asyncio.sleep(.1)
            assert closed[-1] == "eva-voice-" + format(4, "032x")
            await transport.close()
    asyncio.run(run())


def test_cleanup_retry_and_unload_during_creation():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        closed = []
        attempts = 0
        async def allowed(user): return True
        async def control(request):
            nonlocal attempts
            if "/end/" in request.path:
                attempts += 1
                if attempts == 1: raise web.HTTPServiceUnavailable()
                closed.append(request.path.rsplit("/",1)[1])
                return web.json_response({"closed":True})
            entered.set(); await release.wait()
            return web.json_response({"token":"jwt", "room":"eva-voice-"+"b"*32})
        app=web.Application();app.router.add_post("/owner-voice/{tail:.*}",control)
        async with TestServer(app) as backend, aiohttp.ClientSession() as http:
            transport=module.PhoneTransport(http,str(backend.make_url("/")),allowed)
            create=asyncio.create_task(transport.request("owner","session","existing-owner-secret"))
            await entered.wait()
            unload=asyncio.create_task(transport.close())
            await asyncio.sleep(0)
            release.set()
            try: await create
            except web.HTTPForbidden: pass
            else: raise AssertionError("Late create must not publish after unload")
            await unload
            assert attempts >= 2
            assert closed == ["eva-voice-"+"b"*32]
            assert not transport.leases and transport.closed
            try: await transport.request("owner","session","existing-owner-secret")
            except web.HTTPServiceUnavailable: pass
            else: raise AssertionError("Closed transport admitted a request")
    asyncio.run(run())



def test_refresh_scope_allows_only_validity_and_standard_identity_defaults():
    import base64,json
    def jwt(claims):
        def encode(value):return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
        return encode({"alg":"HS256"})+"."+encode(claims)+".signature"
    original={"iss":"test-key","sub":"owner","video":{"room":"room-one","roomJoin":True},"exp":1,"nbf":0}
    expected=module.token_scope(jwt(original))
    refreshed={**original,"exp":999,"nbf":10,"iat":10,"identity":"owner","kind":"standard"}
    assert expected is not None and module.token_scope(jwt(refreshed))==expected
    for delta in ({"sub":"other"},{"identity":"other"},{"iss":"other-key"},
                  {"video":{"room":"room-two","roomJoin":True}},
                  {"video":{**original["video"],"roomAdmin":True}}):
        assert module.token_scope(jwt({**refreshed,**delta}))!=expected
    assert module.token_scope("not.a.jwt") is None
