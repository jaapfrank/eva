"""Real HTTP transport tests; these do not load or certify Home Assistant."""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
from pathlib import Path
import sys

import aiohttp
from aiohttp import web
import pytest

_PATH = Path(__file__).parents[2] / "deploy/home-assistant/custom_components/eva2_voice/client.py"
_SPEC = importlib.util.spec_from_file_location("eva_native_voice_client_under_test", _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
EvaVoiceClient = _MODULE.EvaVoiceClient
EvaVoiceUnavailable = _MODULE.EvaVoiceUnavailable


@asynccontextmanager
async def server(handler, interrupt):
    app = web.Application()
    app.router.add_post("/internal/v1/ha-voice/turns", handler)
    app.router.add_post("/internal/v1/ha-voice/turns/{request_id}/interrupt", interrupt)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    async with aiohttp.ClientSession() as session:
        client = EvaVoiceClient(session, url=f"http://127.0.0.1:{port}", token="synthetic",
                               satellite_ids=frozenset({"assist_satellite.room", "assist_satellite.garden"}))
        try:
            yield client
        finally:
            await client.close()
    await runner.cleanup()


def ask(client, text="Hoi", satellite="assist_satellite.room"):
    return client.respond(satellite_id=satellite, device_id="a" * 32,
                          conversation_id="ha-conversation", text=text, language="nl")


def test_native_request_preserves_conversation_but_does_not_invent_a_person():
    async def run():
        async def handler(request):
            data = await request.json()
            assert request.headers["Authorization"] == "Bearer synthetic"
            assert set(data) == {"request_id", "satellite_id", "device_id", "conversation_id", "text", "language"}
            assert data["conversation_id"] == "ha-conversation"
            return web.json_response({"request_id": data["request_id"], "reply": "Dag!",
                                      "continue_conversation": True})
        async def interrupt(request):
            pytest.fail("successful request must not interrupt")
        async with server(handler, interrupt) as client:
            answer = await ask(client)
            assert answer.text == "Dag!" and answer.continue_conversation
    asyncio.run(run())


def test_ha_cancellation_sends_exact_request_and_speaker_to_core():
    async def run():
        seen = []
        entered, released = asyncio.Event(), asyncio.Event()
        async def handler(request):
            data = await request.json()
            seen.append(data)
            entered.set()
            await released.wait()
            return web.json_response({"request_id": data["request_id"], "reply": "Oud antwoord",
                                      "continue_conversation": False})
        async def interrupt(request):
            assert request.match_info["request_id"] == seen[0]["request_id"]
            assert await request.json() == {"satellite_id": "assist_satellite.room", "device_id": "a" * 32}
            released.set()
            return web.json_response({"status": "interrupted"})
        async with server(handler, interrupt) as client:
            task = asyncio.create_task(ask(client))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert released.is_set()
    asyncio.run(run())


def test_new_room_question_cancels_old_room_but_not_garden():
    async def run():
        entered = {text: asyncio.Event() for text in ("old", "garden")}
        releases = {text: asyncio.Event() for text in ("old", "garden")}
        requests, interrupted = {}, []
        async def handler(request):
            data = await request.json()
            requests[data["request_id"]] = data["text"]
            if data["text"] in entered:
                entered[data["text"]].set()
                await releases[data["text"]].wait()
            return web.json_response({"request_id": data["request_id"], "reply": data["text"],
                                      "continue_conversation": False})
        async def interrupt(request):
            text = requests[request.match_info["request_id"]]
            interrupted.append(text)
            releases[text].set()
            return web.json_response({"status": "interrupted"})
        async with server(handler, interrupt) as client:
            old = asyncio.create_task(ask(client, "old"))
            garden = asyncio.create_task(ask(client, "garden", "assist_satellite.garden"))
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in entered.values())), 2)
            assert (await ask(client, "new")).text == "new"
            with pytest.raises(asyncio.CancelledError):
                await old
            assert not garden.done()
            releases["garden"].set()
            assert (await garden).text == "garden"
            assert interrupted == ["old"]
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["wrong_id", "redirect", "oversized", "malformed"])
def test_invalid_response_never_becomes_speech(failure):
    async def run():
        cancels = []
        async def handler(request):
            if failure == "redirect":
                return web.Response(status=302, headers={"Location": "http://127.0.0.1:1/never"})
            if failure == "oversized":
                return web.Response(body=b"x" * 131073)
            if failure == "malformed":
                return web.Response(text="not json")
            return web.json_response({"request_id": "wrong", "reply": "No", "continue_conversation": False})
        async def interrupt(request):
            cancels.append(request.match_info["request_id"])
            return web.Response(status=204)
        async with server(handler, interrupt) as client:
            with pytest.raises(EvaVoiceUnavailable):
                await ask(client)
            assert len(cancels) == 1
    asyncio.run(run())


def test_unconfigured_satellite_is_rejected_before_network():
    async def run():
        async def forbidden(request):
            pytest.fail("unconfigured speaker must not reach transport")
        async with server(forbidden, forbidden) as client:
            with pytest.raises(EvaVoiceUnavailable):
                await ask(client, satellite="assist_satellite.unknown")
    asyncio.run(run())


def test_cancellation_during_error_cleanup_never_becomes_an_error_utterance():
    async def run():
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def handler(request):
            return web.Response(status=503)
        async def interrupt(request):
            entered.set()
            await release.wait()
            finished.set()
            return web.Response(status=204)
        async with server(handler, interrupt) as client:
            task = asyncio.create_task(ask(client))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            await asyncio.wait_for(finished.wait(), 2)
    asyncio.run(run())


@pytest.mark.parametrize("url,token,satellites", [
    ("https://example.test/path", "token", {"assist_satellite.room"}),
    ("https://example.test?x=1", "token", {"assist_satellite.room"}),
    ("https://user:pass@example.test", "token", {"assist_satellite.room"}),
    ("https://example.test:99999", "token", {"assist_satellite.room"}),
    ("https://example.test", "token\nextra", {"assist_satellite.room"}),
    ("https://example.test", "", {"assist_satellite.room"}),
    ("https://example.test", "token", set()),
    ("https://example.test", "token", {"media_player.room"}),
])
def test_invalid_settings_rejected_by_shared_configflow_validation(url, token, satellites):
    with pytest.raises(ValueError):
        EvaVoiceClient.validate_config(url=url, token=token, satellite_ids=frozenset(satellites))
