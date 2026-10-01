from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest
from websockets.asyncio.server import serve

from eva.adapters.connectors.home_assistant_voice import (
    HomeAssistantSpeechInput, HomeAssistantVoiceReplyGuard, VoiceMediaError,
    NoSpeechRecognized,
)


@pytest.fixture
def reply_guard():
    start = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
    now = [start + timedelta(seconds=10)]
    guard = HomeAssistantVoiceReplyGuard(
        interruption_entities={"assist_satellite.room": "event.room_interrupt",
                               "assist_satellite.garden": "event.garden_interrupt"},
        connected_at=start, clock=lambda: now[0],
    )
    return guard, start, now


def _voice_query(at, query_id="q1", satellite="assist_satellite.room"):
    return {"event_type": "eva_voice_query", "time_fired": at.isoformat(),
            "data": {"id": query_id, "satellite": satellite, "sender": "Jaap"}}


def _begin_voice(guard, event, *, started_at=None):
    return guard.begin(event, turn_started_at=(started_at or datetime.fromisoformat(event["time_fired"])))


def _voice_interrupt(at, entity="event.room_interrupt", kind="resume_requested"):
    return {"event_type": "state_changed", "time_fired": at.isoformat(), "data": {
        "entity_id": entity,
        "old_state": {"entity_id": entity, "state": (at - timedelta(seconds=1)).isoformat()},
        "new_state": {"entity_id": entity, "state": at.isoformat(),
                      "attributes": {"event_type": kind}},
    }}


def test_device_interrupt_suppresses_old_reply_without_touching_other_speaker(reply_guard):
    guard, start, _ = reply_guard
    room = _begin_voice(guard, _voice_query(start + timedelta(seconds=2)))
    garden = _begin_voice(guard, _voice_query(start + timedelta(seconds=3), "q2", "assist_satellite.garden"))
    assert guard.interrupt(_voice_interrupt(start + timedelta(seconds=4))) is room
    assert not guard.may_reply(room)
    assert guard.may_reply(garden)
    fresh = _begin_voice(guard, _voice_query(start + timedelta(seconds=5), "q3"))
    assert guard.interrupt(_voice_interrupt(start + timedelta(seconds=4))) is None
    assert guard.may_reply(fresh)


def test_new_query_supersedes_reply_and_replayed_query_cannot_supersede_it(reply_guard):
    guard, start, _ = reply_guard
    first_event = _voice_query(start + timedelta(seconds=2))
    first = _begin_voice(guard, first_event)
    newer = _begin_voice(guard, _voice_query(start + timedelta(seconds=3), "q2"))
    with pytest.raises(ValueError):
        _begin_voice(guard, first_event)
    assert not guard.may_reply(first)
    assert guard.may_reply(newer)
    assert not guard.may_reply(deepcopy(newer))


@pytest.mark.parametrize("change", [
    "restored", "initial_state", "unknown_entity", "wrong_state_entity",
    "unknown_kind", "unchanged_state", "before_subscription", "future",
    "naive_timestamp", "future_state",
])
def test_unbound_or_restored_interrupt_cannot_cancel_reply(reply_guard, change):
    guard, start, _ = reply_guard
    query = _begin_voice(guard, _voice_query(start + timedelta(seconds=2)))
    event = _voice_interrupt(start + timedelta(seconds=4))
    data = event["data"]
    if change == "restored":
        data["new_state"]["attributes"]["restored"] = True
    elif change == "initial_state":
        data["old_state"] = None
    elif change == "unknown_entity":
        data["entity_id"] = "event.unknown"
    elif change == "wrong_state_entity":
        data["new_state"]["entity_id"] = "event.garden_interrupt"
    elif change == "unknown_kind":
        data["new_state"]["attributes"]["event_type"] = "button_pressed"
    elif change == "unchanged_state":
        data["old_state"]["state"] = data["new_state"]["state"]
    elif change == "before_subscription":
        event = _voice_interrupt(start - timedelta(seconds=1))
    elif change == "future":
        event = _voice_interrupt(start + timedelta(seconds=20))
    elif change == "naive_timestamp":
        event["time_fired"] = "2026-09-08T10:00:04"
    elif change == "future_state":
        data["new_state"]["state"] = (start + timedelta(seconds=5)).isoformat()
    assert guard.interrupt(event) is None
    assert guard.may_reply(query)


def test_disconnect_and_deadline_prevent_reply_even_when_worker_finishes(reply_guard):
    guard, start, now = reply_guard
    query = _begin_voice(guard, _voice_query(start + timedelta(seconds=2)))
    now[0] = start + timedelta(seconds=34)
    assert not guard.may_reply(query)
    guard.close()
    with pytest.raises(ValueError):
        _begin_voice(guard, _voice_query(now[0], "q2"))
    assert guard.interrupt(_voice_interrupt(now[0])) is None


def test_stop_before_delayed_query_prevents_admitting_that_query(reply_guard):
    guard, start, _ = reply_guard
    assert guard.interrupt(_voice_interrupt(start + timedelta(seconds=4), kind="stop_requested")) is None
    with pytest.raises(ValueError):
        _begin_voice(guard, _voice_query(start + timedelta(seconds=3)))


def test_old_pipeline_publishing_after_interrupt_does_not_become_a_new_turn(reply_guard):
    guard, start, _ = reply_guard
    assert guard.interrupt(_voice_interrupt(start + timedelta(seconds=4))) is None
    with pytest.raises(ValueError):
        _begin_voice(guard, _voice_query(start + timedelta(seconds=5)),
                     started_at=start + timedelta(seconds=2))
    fresh = _begin_voice(guard, _voice_query(start + timedelta(seconds=7), "fresh"),
                         started_at=start + timedelta(seconds=6))
    assert guard.may_reply(fresh)


def test_republished_query_id_cannot_replace_newer_question(reply_guard):
    guard, start, _ = reply_guard
    _begin_voice(guard, _voice_query(start + timedelta(seconds=2)))
    fresh = _begin_voice(guard, _voice_query(start + timedelta(seconds=3), "fresh"))
    with pytest.raises(ValueError):
        _begin_voice(guard, _voice_query(start + timedelta(seconds=5)))
    assert guard.may_reply(fresh)


def test_long_spoken_input_does_not_consume_the_ha_reply_window(reply_guard):
    guard, start, now = reply_guard
    now[0] = start + timedelta(seconds=50)
    query = _begin_voice(guard, _voice_query(start + timedelta(seconds=45)),
                         started_at=start + timedelta(seconds=2))
    assert guard.may_reply(query)
    now[0] = start + timedelta(seconds=77)
    assert not guard.may_reply(query)


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


@pytest.mark.parametrize("boundary", [None, True, -10, 15, "10", "missing", "duplicate"])
def test_requested_audio_boundary_must_be_valid_unique_and_present(boundary):
    async def run():
        observed = []
        async def end(timestamp):
            observed.append(timestamp)
        async def handler(socket):
            await _handshake(socket)
            await _event(socket, "stt-start")
            await socket.recv()
            await _event(socket, "stt-vad-start", {"timestamp": 0})
            if boundary != "missing":
                await _event(socket, "stt-vad-end", {
                    "timestamp": 10 if boundary == "duplicate" else boundary})
                if boundary == "duplicate":
                    await _event(socket, "stt-vad-end", {"timestamp": 10})
            await _event(socket, "stt-end", {"stt_output": {"text": "Dag Eva"}})
            await _event(socket, "run-end")
            await socket.wait_closed()
        async with serve(handler, "127.0.0.1", 0) as server:
            adapter = HomeAssistantSpeechInput(
                base_url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                token="synthetic", pipeline_id="test-pipeline")
            with pytest.raises(VoiceMediaError):
                await adapter.transcribe(_audio(b"12"), on_speech_end=end)
        assert observed == ([10] if boundary == "duplicate" else [])
    asyncio.run(run())


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


@pytest.mark.parametrize("unexpected", ["intent-start", "intent-end", "tts-start"])
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


@pytest.mark.parametrize("code", ["stt-no-text-recognized", "stt-stream-failed", "cloud-auth-failed", None])
@pytest.mark.parametrize("started", [True, False])
def test_only_admitted_empty_recognition_is_recoverable(code, started):
    async def run():
        async def handler(socket):
            await _handshake(socket)
            if started:
                await _event(socket, "stt-start")
            await _event(socket, "error", {"code": code, "message": "private provider detail"})
            await socket.wait_closed()
        async with serve(handler, "127.0.0.1", 0) as server:
            adapter = HomeAssistantSpeechInput(
                base_url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                token="synthetic", pipeline_id="test-pipeline")
            with pytest.raises(VoiceMediaError) as caught:
                await adapter.transcribe(_audio(b"12"))
            assert isinstance(caught.value, NoSpeechRecognized) == (
                started and code == "stt-no-text-recognized")
            assert "private provider detail" not in str(caught.value)
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


@pytest.mark.parametrize("engine", [None, "stt.other", "stt.allowed"])
def test_stt_engine_pin_precedes_first_audio_byte(engine):
    async def run():
        received = []
        async def handler(socket):
            await _handshake(socket)
            await _event(socket, "stt-start", {"engine": engine})
            if engine == "stt.allowed":
                received.append(await socket.recv())
                received.append(await socket.recv())
                await _event(socket, "stt-end", {"stt_output": {"text": "Dag Eva"}})
                await _event(socket, "run-end")
            else:
                await socket.wait_closed()
        async with serve(handler, "127.0.0.1", 0) as server:
            adapter = HomeAssistantSpeechInput(
                base_url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                token="synthetic", pipeline_id="test-pipeline", expected_stt_engine="stt.allowed",
            )
            if engine == "stt.allowed":
                assert await adapter.transcribe(_audio(b"12")) == "Dag Eva"
                assert received == [b"\x0712", b"\x07"]
            else:
                with pytest.raises(VoiceMediaError, match="engine differs"):
                    await adapter.transcribe(_audio(b"12"))
                assert received == []
    asyncio.run(run())


@pytest.mark.parametrize("path", ["/api/tts_proxy/result.mp3", "https://other.test/audio", "/api/tts_proxy/../secret"])
def test_direct_tts_pins_engine_disables_disk_cache_and_ignores_external_url(monkeypatch, path):
    import httpx2 as httpx
    from eva.adapters.connectors.home_assistant_voice import HomeAssistantSpeechOutput
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer synthetic"
        assert request.url.host == "ha.test"
        if request.method == "POST":
            assert request.url.path == "/api/tts_get_url"
            assert json.loads(request.content) == {
                "engine_id": "tts.allowed", "message": "Dag Eva", "language": "nl-NL", "cache": False,
            }
            return httpx.Response(200, json={"path": path, "url": "https://untrusted.test/ignored"})
        assert request.url.path == "/api/tts_proxy/result.mp3"
        return httpx.Response(200, content=b"synthetic", headers={"content-type": "audio/mpeg"})
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(**kw, transport=httpx.MockTransport(handler)))
    async def run():
        adapter = HomeAssistantSpeechOutput(base_url="http://ha.test", token="synthetic",
                    pipeline_id="unused-direct-tts", tts_engine="tts.allowed", tts_language="nl-NL")
        if path == "/api/tts_proxy/result.mp3":
            result = [chunk async for chunk in adapter.synthesize("Dag Eva")]
            assert result[0].data == b"synthetic" and len(requests) == 2
        else:
            with pytest.raises(VoiceMediaError, match="reference"):
                await anext(adapter.synthesize("Dag Eva"))
            assert len(requests) == 1
    asyncio.run(run())
def test_vad_end_stops_sender_before_segment_callback_can_close_the_source():
    """Late EOF must not overwrite a complete, bound STT result."""
    async def run():
        released, first_audio = asyncio.Event(), asyncio.Event()
        writes = []
        class Chunks:
            first = True
            def __aiter__(self):
                return self
            async def __anext__(self):
                if self.first:
                    self.first = False
                    return b"12"
                await released.wait()
                raise StopAsyncIteration
        class Socket:
            def __init__(self):
                self.messages = iter([
                    {"type": "auth_required"}, {"type": "auth_ok"},
                    {"id": 1, "type": "result", "success": True},
                    *[{"id": 1, "type": "event", "event": {"type": kind, "data": data}} for kind, data in [
                        ("run-start", {"runner_data": {"stt_binary_handler_id": 7}}),
                        ("stt-start", {}), ("stt-vad-start", {"timestamp": 0}),
                        ("stt-vad-end", {"timestamp": 0}),
                        ("stt-end", {"stt_output": {"text": "Geldig antwoord"}}),
                        ("run-end", None),
                    ]],
                ])
            async def recv(self):
                message = next(self.messages)
                if message.get("event", {}).get("type") == "stt-vad-start":
                    await first_audio.wait()
                return json.dumps(message)
            async def send(self, value):
                if isinstance(value, bytes):
                    writes.append(value)
                    first_audio.set()
                    if value == b"\x07":
                        raise RuntimeError("provider already ended the completed run")
        async def end(timestamp):
            assert timestamp == 0
            released.set()
            # Deterministically let the old sender attempt its obsolete EOF.
            for _ in range(3):
                await asyncio.sleep(0)
        adapter = HomeAssistantSpeechInput(base_url="http://127.0.0.1", token="test", pipeline_id="test")
        text = await adapter._transcribe(Socket(), Chunks(), on_speech_end=end)
        assert text == "Geldig antwoord" and writes == [b"\x0712"]
    asyncio.run(run())
