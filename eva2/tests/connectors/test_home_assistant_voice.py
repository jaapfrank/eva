from __future__ import annotations

import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from eva.adapters.connectors.home_assistant_voice import HomeAssistantSpeechInput, VoiceMediaError


async def _audio(*chunks):
    for chunk in chunks:
        yield chunk


async def _handshake(socket):
    await socket.send(json.dumps({"type": "auth_required"}))
    assert json.loads(await socket.recv()) == {"type": "auth", "access_token": "synthetic"}
    await socket.send(json.dumps({"type": "auth_ok"}))
    request = json.loads(await socket.recv())
    assert request["start_stage"] == request["end_stage"] == "stt"
    assert request["pipeline"] == "test-pipeline"
    assert request["input"] == {"sample_rate": 16000}
    assert "conversation_id" not in request and "device_id" not in request
    await socket.send(json.dumps({"id": 1, "type": "result", "success": True}))
    await _event(socket, "run-start", {"runner_data": {"stt_binary_handler_id": 7}})


async def _event(socket, kind, data=None):
    await socket.send(json.dumps({"id": 1, "type": "event", "event": {
        "type": kind, "data": None if kind == "run-end" else (data or {})}}))


def test_real_wire_stt_only_stream_and_final_text():
    async def run():
        observed = []
        async def handler(socket):
            await _handshake(socket)
            await _event(socket, "stt-start")
            while True:
                frame = await socket.recv()
                observed.append(frame)
                if frame == b"\x07":
                    break
            await _event(socket, "stt-end", {"stt_output": {"text": "Dag Eva"}})
            await _event(socket, "run-end")
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = HomeAssistantSpeechInput(base_url=f"http://127.0.0.1:{port}",
                                               token="synthetic", pipeline_id="test-pipeline")
            assert await adapter.transcribe(_audio(b"12", b"3456")) == "Dag Eva"
        assert observed == [b"\x0712", b"\x073456", b"\x07"]
    asyncio.run(run())


@pytest.mark.parametrize("unexpected", ["intent-start", "intent-end", "tts-start", "error"])
def test_native_intent_and_provider_errors_never_return_transcript(unexpected):
    async def run():
        async def handler(socket):
            await _handshake(socket)
            await _event(socket, unexpected)
            await socket.wait_closed()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = HomeAssistantSpeechInput(base_url=f"http://127.0.0.1:{port}",
                                               token="synthetic", pipeline_id="test-pipeline")
            with pytest.raises(VoiceMediaError, match="STT-only"):
                await adapter.transcribe(_audio(b"12"))
    asyncio.run(run())


@pytest.mark.parametrize("chunks", [[], [b""], [b"1"], [b"12", b"34"]])
def test_invalid_or_excess_audio_closes_transport(chunks):
    async def run():
        async def handler(socket):
            await _handshake(socket)
            await _event(socket, "stt-start")
            await socket.wait_closed()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = HomeAssistantSpeechInput(base_url=f"http://127.0.0.1:{port}",
                                               token="synthetic", pipeline_id="test-pipeline",
                                               max_audio_bytes=2)
            with pytest.raises(VoiceMediaError):
                await asyncio.wait_for(adapter.transcribe(_audio(*chunks)), 2)
    asyncio.run(run())


def test_cancel_closes_microphone_iterator_and_transport():
    async def run():
        producing, closed, disconnected = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def audio():
            try:
                producing.set()
                await asyncio.Event().wait()
                yield b"12"
            finally:
                closed.set()
        async def handler(socket):
            await _handshake(socket)
            await _event(socket, "stt-start")
            await socket.wait_closed()
            disconnected.set()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = HomeAssistantSpeechInput(base_url=f"http://127.0.0.1:{port}",
                                               token="synthetic", pipeline_id="test-pipeline")
            task = asyncio.create_task(adapter.transcribe(audio()))
            await producing.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(disconnected.wait(), 1)
            assert closed.is_set()
    asyncio.run(run())


def test_auth_failure_closes_owned_audio_source_before_first_frame():
    async def run():
        class Source:
            closed = False
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise AssertionError("audio must not be consumed before authentication")
            async def aclose(self):
                self.closed = True
        async def handler(socket):
            await socket.send(json.dumps({"type": "auth_required"}))
            await socket.recv()
            await socket.send(json.dumps({"type": "auth_invalid"}))
        source = Source()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = HomeAssistantSpeechInput(base_url=f"http://127.0.0.1:{port}",
                                               token="synthetic", pipeline_id="test-pipeline")
            with pytest.raises(VoiceMediaError, match="authentication"):
                await adapter.transcribe(source)
        assert source.closed
    asyncio.run(run())


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_early_final_transcript_closes_recording_and_does_not_hide_cleanup_failure(cleanup_fails):
    async def run():
        recording, closed = asyncio.Event(), asyncio.Event()
        async def audio():
            try:
                yield b"12"
                recording.set()
                await asyncio.Event().wait()
            finally:
                closed.set()
                if cleanup_fails:
                    raise OSError("synthetic microphone close failure")
        async def handler(socket):
            await _handshake(socket)
            await _event(socket, "stt-start")
            await socket.recv()
            await recording.wait()
            await _event(socket, "stt-end", {"stt_output": {"text": "Dag Eva"}})
            await _event(socket, "run-end")
            await socket.wait_closed()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = HomeAssistantSpeechInput(base_url=f"http://127.0.0.1:{port}",
                                               token="synthetic", pipeline_id="test-pipeline")
            if cleanup_fails:
                with pytest.raises(VoiceMediaError, match="producer failed"):
                    await adapter.transcribe(audio())
            else:
                assert await adapter.transcribe(audio()) == "Dag Eva"
            assert closed.is_set()
    asyncio.run(run())


@pytest.mark.parametrize("path", ["https://other.invalid/a.mp3", "//other.invalid/a.mp3",
                                  "/api/tts_proxy/%2e%2e/secret", "/api/tts_proxy/a?token=x", "/api/tts_proxy/..",
                                  "/api/tts_proxy/.", "/api/tts_proxy/a\n.mp3"])
def test_tts_rejects_untrusted_audio_references(path):
    from eva.adapters.connectors.home_assistant_voice import HomeAssistantSpeechOutput
    async def run():
        async def handler(socket):
            await _tts_handshake(socket)
            await _event(socket, "tts-end", {"tts_output": {"url": path, "mime_type": "audio/mpeg"}})
            await socket.wait_closed()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = HomeAssistantSpeechOutput(base_url=f"http://127.0.0.1:{port}",
                                                token="synthetic", pipeline_id="test-pipeline")
            with pytest.raises(VoiceMediaError, match="reference"):
                async for _ in adapter.synthesize("Dag Eva"):
                    raise AssertionError("untrusted output must not play")
    asyncio.run(run())


async def _tts_handshake(socket):
    await socket.send(json.dumps({"type": "auth_required"}))
    assert json.loads(await socket.recv())["access_token"] == "synthetic"
    await socket.send(json.dumps({"type": "auth_ok"}))
    request = json.loads(await socket.recv())
    assert request["start_stage"] == request["end_stage"] == "tts"
    assert request["input"] == {"text": "Dag Eva"}
    await socket.send(json.dumps({"id": 1, "type": "result", "success": True}))
    await _event(socket, "run-start")
    await _event(socket, "tts-start")


@pytest.mark.parametrize("status", [200, 302])
def test_tts_bounded_audio_relay_closes_fetch_on_interruption(monkeypatch, status):
    import httpx2 as httpx
    from eva.adapters.connectors.home_assistant_voice import HomeAssistantSpeechOutput
    async def run():
        closed = asyncio.Event()
        requested = []
        class Audio(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"a" * 16_384
                yield b"b" * 16_384
                await asyncio.Event().wait()
            async def aclose(self):
                closed.set()
        async def handler(socket):
            await _tts_handshake(socket)
            await _event(socket, "tts-end", {"tts_output": {
                "url": "/api/tts_proxy/proof.mp3", "mime_type": "audio/mpeg"}})
            await _event(socket, "run-end")
        def request(request):
            requested.append(request)
            assert request.headers["Authorization"] == "Bearer synthetic"
            assert request.url.path == "/api/tts_proxy/proof.mp3"
            return httpx.Response(status, headers={"content-type": "audio/mpeg",
                                  "location": "https://other.invalid/"}, stream=Audio())
        client = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(
            **kw, transport=httpx.MockTransport(request)))
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = HomeAssistantSpeechOutput(base_url=f"http://127.0.0.1:{port}",
                                                token="synthetic", pipeline_id="test-pipeline")
            audio = adapter.synthesize("Dag Eva")
            if status == 200:
                first = await asyncio.create_task(anext(audio))
                assert first.mime_type == "audio/mpeg" and len(first.data) == 16_384
                second = await asyncio.create_task(anext(audio))
                assert second.data == b"b" * 16_384
                await asyncio.create_task(audio.aclose())
            else:
                with pytest.raises(VoiceMediaError, match="retrieval"):
                    await anext(audio)
            assert closed.is_set()
            assert len(requested) == 1 and requested[0].url.port == port
    asyncio.run(run())
