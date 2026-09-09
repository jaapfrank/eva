"""Complete service composition on real local TLS/HTTP; explicit HA/device doubles."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import ssl
import wave

from aiohttp import web
import pytest

from test_native_duplex_speaker_input import NativePeer, Detector, Kind
from test_native_duplex_speaker_endpoint import tls_pair, client
from test_native_duplex_speaker_wire import run_async
from reply_lane import HouseholdBinding
from speaker_endpoint import SpeakerGrant
import speaker_service as service


TOKEN = "A" * 43
DEVICE = "a" * 32


def config(tmp_path, origin):
    return service.ServiceConfig(host="127.0.0.1", port=0,
        certificate=tmp_path / "cert.pem", private_key=tmp_path / "key.pem",
        model=tmp_path / "model.onnx", ha_url=origin, ha_token="ha-test-secret",
        pipeline_id="pipeline-test", pipeline_language="nl", stt_engine="stt.cloud",
        tts_engine="tts.cloud", tts_language="nl-NL", eva_url=origin, eva_token="eva-test-secret",
        grants=(SpeakerGrant(HouseholdBinding("assist_satellite.test", DEVICE,
                   "configured-household-conversation", "nl-NL"), hashlib.sha256(TOKEN.encode()).hexdigest()),))


@asynccontextmanager
async def upstream(*, fault=None):
    observed = {"stt": [], "tts": [], "core": [], "interrupts": []}
    async def websocket(request):
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        await socket.send_json({"type": "auth_required"})
        assert await socket.receive_json() == {"type": "auth", "access_token": "ha-test-secret"}
        await socket.send_json({"type": "auth_ok"})
        async for message in socket:
            data = json.loads(message.data)
            kind = data["type"]
            if kind == "assist_pipeline/run":
                assert data["pipeline"] == "pipeline-test" and data["start_stage"] == data["end_stage"] == "stt"
                assert data["input"] == {"sample_rate": 16000}
                await socket.send_json({"id": data["id"], "type": "result", "success": True})
                async def event(kind, value):
                    await socket.send_json({"id": data["id"], "type": "event", "event": {"type": kind, "data": value}})
                await event("run-start", {"pipeline": "pipeline-test", "language": "nl", "runner_data": {"stt_binary_handler_id": 7}})
                await event("stt-start", {"engine": "stt.cloud"})
                frames = []
                while len(frames) < 20:
                    message = await socket.receive()
                    if message.type != web.WSMsgType.BINARY:
                        return socket
                    assert message.data[0] == 7
                    frames.append(message.data[1:])
                observed["stt"].append(frames)
                await event("stt-vad-start", {"timestamp": 0})
                await event("stt-vad-end", {"timestamp": 180})
                await event("stt-end", {"stt_output": {"text": "vraag " + str(len(observed["stt"]))}})
                await event("run-end", None)
                await socket.close()
                return socket
            if kind == "config/entity_registry/list":
                result = [{"entity_id": "assist_satellite.test", "device_id": "b" * 32 if fault == "device" else DEVICE,
                           "platform": "esphome", "disabled_by": None}]
            elif kind == "config/device_registry/list":
                result = [{"id": DEVICE, "disabled_by": "user" if fault == "disabled" else None}]
            elif kind == "assist_pipeline/pipeline/get":
                assert data["pipeline_id"] == "pipeline-test"
                result = {"id": "pipeline-test", "language": "nl", "stt_engine": "stt.cloud",
                          "tts_engine": "tts.cloud", "tts_language": "nl-NL", "tts_voice": "other" if fault == "voice" else None}
            else:
                raise AssertionError(kind)
            await socket.send_json({"id": data["id"], "type": "result", "success": True, "result": result})
        return socket

    async def core(request):
        assert request.headers["Authorization"] == "Bearer eva-test-secret"
        data = await request.json()
        observed["core"].append(data)
        return web.json_response({"request_id": data["request_id"], "reply": "antwoord " + str(len(observed["core"])),
                                  "continue_conversation": True})

    async def interrupt(request):
        observed["interrupts"].append(await request.json())
        return web.json_response({"status": "interrupted"})

    async def tts(request):
        assert request.headers["Authorization"] == "Bearer ha-test-secret"
        data = await request.json()
        assert data["engine_id"] == "tts.cloud" and data["language"] == "nl-NL" and data["cache"] is False
        observed["tts"].append(data)
        return web.json_response({"path": "/api/tts_proxy/example.wav"})

    async def audio(request):
        assert request.headers["Authorization"] == "Bearer ha-test-secret"
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000)
            stream.writeframes(bytes([32, 0]) * 2400)
        return web.Response(body=buffer.getvalue(), content_type="audio/wav")

    app = web.Application()
    app.router.add_get("/api/websocket", websocket)
    app.router.add_post("/api/tts_get_url", tts)
    app.router.add_get("/api/tts_proxy/example.wav", audio)
    app.router.add_post("/internal/v1/ha-voice/turns", core)
    app.router.add_post("/internal/v1/ha-voice/turns/{request_id}/interrupt", interrupt)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}", observed
    finally:
        await runner.cleanup()


@run_async
async def test_complete_composition_reuses_real_adapters_and_native_client(tls_pair, tmp_path, monkeypatch):
    # Only the acoustic detector/model is replaced. HA and core are network
    # doubles; their actual adapters/client plus the actual FFmpeg process run.
    monkeypatch.setattr(service, "verify_model", lambda path: path)
    monkeypatch.setattr(service, "verified_detector", lambda path: Detector())
    async with upstream() as (origin, observed):
        cfg = config(tmp_path, origin)
        async with service.open_service(cfg) as (server, endpoint):
            async def answer(peer):
                try:
                    return await asyncio.wait_for(peer.answers.get(), 3)
                except TimeoutError:
                    pytest.fail(json.dumps({"endpoint": endpoint.diagnostics(),
                        "sttRuns": len(observed["stt"]), "coreTurns": len(observed["core"]),
                        "ttsRuns": len(observed["tts"]),
                        "packetKinds": [p.kind.name for p in peer.packets][-10:]}))
            uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
            async with client(uri, TOKEN, tls_pair[1]) as socket:
                peer = NativePeer()
                device = asyncio.create_task(peer.handle(socket))
                await peer.wake()
                assert await asyncio.wait_for(peer.captures.get(), 2) == 1
                await peer.question(1)
                assert await answer(peer) == 1
                await peer.send(Kind.LOCAL_STOP)
                assert await asyncio.wait_for(peer.captures.get(), 2) == 2
                await peer.question(2)
                assert await answer(peer) == 2
                await peer.send(Kind.MUTED)
                await asyncio.wait_for(device, 3)
        assert endpoint._closed and not endpoint._active
        assert not endpoint.diagnostics()["recentFailures"]
    assert len(observed["core"]) == len(observed["stt"]) == len(observed["tts"]) == 2
    assert {r["device_id"] for r in observed["core"]} == {DEVICE}
    assert {r["conversation_id"] for r in observed["core"]} == {"configured-household-conversation"}
    assert [r["message"] for r in observed["tts"]] == ["antwoord 1", "antwoord 2"]
    assert all(set(r) == {"request_id", "satellite_id", "device_id", "conversation_id", "language", "text"} for r in observed["core"])


@pytest.mark.parametrize("fault", ["device", "disabled", "voice"])
@run_async
async def test_preflight_refuses_wrong_ha_binding_without_stt_or_core(tmp_path, fault):
    async with upstream(fault=fault) as (origin, observed):
        with pytest.raises(service.SpeakerServiceError, match="binding differs"):
            await service.verify_home_assistant(config(tmp_path, origin))
        assert observed == {"stt": [], "tts": [], "core": [], "interrupts": []}


@run_async
async def test_service_shutdown_waits_for_both_receipts_after_repeated_cancel(tls_pair, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "verify_model", lambda path: path)
    monkeypatch.setattr(service, "verified_detector", lambda path: Detector())
    async with upstream() as (origin, _):
        opened, stop = asyncio.Queue(), asyncio.Event()
        async def running():
            async with service.open_service(config(tmp_path, origin)) as pair:
                opened.put_nowait(pair)
                await stop.wait()
        owner = asyncio.create_task(running())
        server, endpoint = await opened.get()
        uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        async with client(uri, TOKEN, tls_pair[1]) as socket:
            peer = NativePeer()
            peer.stop_gate, peer.capture_end_gate = asyncio.Event(), asyncio.Event()
            device = asyncio.create_task(peer.handle(socket))
            await peer.wake(); await peer.captures.get(); await peer.question(1)
            await asyncio.wait_for(peer.answers.get(), 3)
            owner.cancel()
            async with asyncio.timeout(2):
                while not any(p.kind == Kind.CAPTURE_END for p in peer.packets):
                    await asyncio.sleep(.001)
            owner.cancel()
            await asyncio.sleep(.02)
            assert not owner.done() and not device.done()
            peer.capture_end_gate.set(); peer.stop_gate.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(owner, 3)
            await asyncio.wait_for(device, 3)
            assert not endpoint._active


def test_private_config_does_not_accept_public_files_or_leak_repr(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"secret":"never-print-this"}')
    path.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        service.ServiceConfig.load(path)
    path.chmod(0o600)
    link = tmp_path / "link.json"; link.symlink_to(path)
    with pytest.raises(OSError):
        service.ServiceConfig.load(link)
    cfg = config(tmp_path, "http://127.0.0.1:1")
    assert "ha-test-secret" not in repr(cfg) and "eva-test-secret" not in repr(cfg)
    with pytest.raises(ValueError, match="device IDs|unique"):
        replace(cfg, grants=cfg.grants * 2)


def test_endpoint_diagnostics_are_bounded_and_omit_exception_text(tmp_path):
    cfg = config(tmp_path, "http://127.0.0.1:1")
    endpoint = service.SpeakerEndpoint(grants=cfg.grants, pcm=None, session=None)
    for _ in range(12):
        try:
            raise RuntimeError("private-provider-token-or-transcript")
        except RuntimeError as error:
            endpoint._record_failure("conversation", error)
    status = endpoint.diagnostics()
    assert len(status["recentFailures"]) == 8
    assert "private-provider-token-or-transcript" not in json.dumps(status)
    assert status["recentFailures"][0]["frames"][-1]["function"] == "test_endpoint_diagnostics_are_bounded_and_omit_exception_text"


@run_async
async def test_final_native_client_cleanup_survives_cancel_after_endpoint_closed(tls_pair, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "verify_model", lambda path: path)
    monkeypatch.setattr(service, "verified_detector", lambda path: Detector())
    entered, release, cancelled_cleanup = asyncio.Event(), asyncio.Event(), []
    base = service.native_client_class()
    clients = []
    class Client(base):
        async def close(self):
            async def pending_interrupt():
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled_cleanup.append(True)
                    raise
            self._cleanups.add(asyncio.create_task(pending_interrupt()))
            clients.append(self)
            await super().close()
    monkeypatch.setattr(service, "native_client_class", lambda: Client)
    async with upstream() as (origin, _):
        endpoint_holder = []
        async def running():
            async with service.open_service(config(tmp_path, origin)) as (_, endpoint):
                endpoint_holder.append(endpoint)
        owner = asyncio.create_task(running())
        await asyncio.wait_for(entered.wait(), 2)
        assert endpoint_holder[0]._closed
        owner.cancel(); await asyncio.sleep(.01); owner.cancel(); await asyncio.sleep(.01)
        assert not owner.done() and not cancelled_cleanup and not clients[0]._session.closed
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(owner, 2)
        assert clients[0]._session.closed and not cancelled_cleanup
