"""Exercise the optional phone gateway in a separate Linux network namespace."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import aiohttp
from aiohttp import web
from render_household_voice_ingress import render


async def verify(nginx):
    if Path("/proc/self/ns/net").readlink() == Path("/proc/1/ns/net").readlink():
        raise RuntimeError("Run through unshare --net")
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    seen = []
    async def media(request):
        seen.append((request.path, request.headers.get("Authorization"), request.headers.get("Origin")))
        if request.headers.get("Authorization") != "Bearer test-owner":
            raise web.HTTPUnauthorized()
        return web.json_response({"ready": True})
    async def signal(request):
        if request.headers.get("Upgrade", "").lower() != "websocket":
            return web.Response(text="validated")
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        async for message in socket:
            if message.type == aiohttp.WSMsgType.BINARY: await socket.send_bytes(message.data)
            elif message.type == aiohttp.WSMsgType.TEXT: await socket.send_str(message.data)
        return socket
    runners = []
    process = None
    with tempfile.TemporaryDirectory(prefix="eva-owner-ingress-") as directory:
        runtime = Path(directory)
        for sub in ("body", "proxy"): (runtime / sub).mkdir()
        config = runtime / "nginx.conf"
        config.write_text(render(environment="EVA2_CANARY_URL=http://127.0.0.1:44710", listen="127.0.0.1",
            home_assistant="127.0.0.2", port=44714, runtime=directory, owner_voice=True))
        try:
            for port, handler in [(44716, media), (7880, signal)]:
                app = web.Application()
                app.router.add_route("*", "/{tail:.*}", handler)
                runner = web.AppRunner(app, access_log=None)
                await runner.setup(); runners.append(runner)
                await web.TCPSite(runner, "127.0.0.1", port).start()
            process = await asyncio.create_subprocess_exec(str(nginx), "-c", str(config), stderr=asyncio.subprocess.PIPE)
            connector = aiohttp.TCPConnector(local_addr=("127.0.0.2", 0))
            async with aiohttp.ClientSession(connector=connector) as client:
                for _ in range(50):
                    try:
                        async with client.get("http://127.0.0.1:44714/owner-voice/session/ready") as response:
                            assert response.status == 401
                        break
                    except aiohttp.ClientConnectorError: await asyncio.sleep(.05)
                else: raise AssertionError("nginx did not start")
                async with client.get("http://127.0.0.1:44714/owner-voice/session/ready", headers={"Authorization":"Bearer test-owner", "Origin":"https://ha.example"}) as response:
                    assert response.status == 200
                async with client.post("http://127.0.0.1:44714/owner-voice/session/end/eva-voice-" + "a"*32, headers={"Authorization":"Bearer test-owner"}) as response:
                    assert response.status == 200
                for path, status in [("/owner-voice/session?token=x",400),("/owner-voice/admin",404),("/owner-voice/livekit/admin",404)]:
                    async with client.get("http://127.0.0.1:44714"+path) as response: assert response.status == status
                async with client.get("http://127.0.0.1:44714/owner-voice/livekit/rtc/validate?access_token=test-jwt") as response:
                    assert response.status == 200 and await response.text() == "validated"
                async with client.ws_connect("http://127.0.0.1:44714/owner-voice/livekit/rtc?access_token=test-jwt") as socket:
                    await socket.send_bytes(b"binary-signaling")
                    assert (await socket.receive()).data == b"binary-signaling"
            async with aiohttp.ClientSession() as denied:
                async with denied.get("http://127.0.0.1:44714/owner-voice/session/ready", headers={"Authorization":"Bearer test-owner"}) as response:
                    assert response.status == 403
            assert seen[1] == ("/session/ready", "Bearer test-owner", "http://localhost:44716")
            return {"owner_auth_forwarded": True,"other_source_denied":True,"exact_room_close":True,"binary_websocket_roundtrip":True,"unexpected_paths_denied":True,"isolated_network_namespace":True}
        finally:
            if process is not None:
                process.terminate(); await process.wait()
                errors = await process.stderr.read()
                if errors: raise AssertionError(errors.decode())
            for runner in runners: await runner.cleanup()


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx", required=True, type=Path)
    args=parser.parse_args()
    print(json.dumps(asyncio.run(verify(args.nginx)), indent=2))
