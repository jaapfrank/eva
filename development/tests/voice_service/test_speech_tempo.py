"""Check audible duration, pitch and streaming cleanup of the existing TTS path."""
import asyncio
import math
import struct
from uuid import uuid4

from livekit.agents import tts
from eva.voice_service.media import TempoTTS, clean_text_for_tts


class ToneTTS(tts.TTS):
    def __init__(self, *, hold=False):
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False),
                         sample_rate=24000, num_channels=1)
        self.hold = hold
        self.waiting = asyncio.Event()
        self.closed = asyncio.Event()

    def synthesize(self, text, *, conn_options=None):
        kwargs = {} if conn_options is None else {"conn_options": conn_options}
        return ToneStream(tts=self, input_text=text, **kwargs)


class ToneStream(tts.ChunkedStream):
    async def _run(self, output_emitter):
        output_emitter.initialize(request_id=str(uuid4()), sample_rate=24000,
                                  num_channels=1, mime_type="audio/pcm", frame_size_ms=20)
        try:
            for offset in range(0, 72000, 480):
                data = struct.pack("<480h", *(round(12000 * math.sin(2 * math.pi * 440 * n / 24000))
                                             for n in range(offset, offset + 480)))
                output_emitter.push(data)
                if self._tts.hold and offset == 24000:
                    self._tts.waiting.set()
                    await asyncio.Event().wait()
            output_emitter.flush()
        finally:
            self._tts.closed.set()


def test_faster_speech_preserves_pitch_and_drains_tail():
    async def run():
        source = ToneTTS()
        speech = TempoTTS(source, rate=1.15)
        async with speech.synthesize("Fixed tone") as stream:
            frames = [event.frame async for event in stream]
        data = b"".join(bytes(frame.data) for frame in frames)
        samples = struct.unpack("<%dh" % (len(data) // 2), data)
        duration = len(samples) / 24000
        assert abs(duration - 3 / 1.15) < .06
        assert all(f.sample_rate == 24000 and f.num_channels == 1 for f in frames)
        middle = samples[2400:-2400]
        crossings = sum(a <= 0 < b for a, b in zip(middle, middle[1:]))
        frequency = crossings * 24000 / len(middle)
        assert abs(frequency - 440) < 3
        # LiveKit pads its final 20 ms frame; the preceding speech tail remains.
        last_audible = max(i for i, sample in enumerate(samples) if abs(sample) > 1)
        assert len(samples) - last_audible - 1 <= 480
        assert max(abs(s) for s in samples[last_audible-480:last_audible]) > 5000
        assert source.closed.is_set()
        await speech.aclose()
    asyncio.run(run())


def test_tempo_streams_before_provider_ends_and_cancel_closes_provider():
    async def run():
        source = ToneTTS(hold=True)
        speech = TempoTTS(source, rate=1.15)
        stream = speech.synthesize("Fixed tone")
        try:
            first = await asyncio.wait_for(anext(stream), 3)
            assert first.frame.samples_per_channel > 0
            assert not source.closed.is_set()
            await asyncio.wait_for(source.waiting.wait(), 3)
        finally:
            await stream.aclose()
            await speech.aclose()
        assert source.closed.is_set()
    asyncio.run(run())


class GapToneStream(tts.ChunkedStream):
    async def _run(self, output_emitter):
        output_emitter.initialize(request_id=str(uuid4()), sample_rate=24000,
                                  num_channels=1, mime_type="audio/pcm", frame_size_ms=20)
        tone = struct.pack("<480h", *(round(10000 * math.sin(2 * math.pi * 440 * n / 24000)) for n in range(480)))
        zero = b"\x00" * 960
        for _ in range(25):  # 0.5s speech
            output_emitter.push(tone)
        for _ in range(45):  # 0.9s internal digital zero gap
            output_emitter.push(zero)
        for _ in range(25):  # 0.5s speech
            output_emitter.push(tone)
        output_emitter.flush()


class GapToneTTS(tts.TTS):
    def __init__(self):
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False),
                         sample_rate=24000, num_channels=1)

    def synthesize(self, text, *, conn_options=None):
        kwargs = {} if conn_options is None else {"conn_options": conn_options}
        return GapToneStream(tts=self, input_text=text, **kwargs)


def test_tempo_tts_trims_excessive_internal_digital_silence_and_cleans_urls():
    assert clean_text_for_tts("**Max Verstappen** staat op 1 (https://formula1.com/standings).") == "Max Verstappen staat op 1."
    assert clean_text_for_tts("Zie [Eredivisie](https://eredivisie.nl/stand) nu! ## Bronnen - 2026 STANDINGS") == "Zie Eredivisie nu!"

    async def run():
        source = GapToneTTS()
        untrimmed = TempoTTS(source, rate=1.09, trim_silence=False)
        trimmed = TempoTTS(source, rate=1.09, trim_silence=True, max_silence_ms=280)
        async with untrimmed.synthesize("Twee zinnen") as s1:
            raw_frames = [ev.frame async for ev in s1]
        async with trimmed.synthesize("Twee zinnen") as s2:
            fast_frames = [ev.frame async for ev in s2]
        raw_samples = sum(f.samples_per_channel for f in raw_frames)
        fast_samples = sum(f.samples_per_channel for f in fast_frames)
        # 0.9s gap capped at 0.28s saves ~0.55s of dead air while preserving both spoken halves
        assert (raw_samples - fast_samples) / 24000 > 0.45
        assert fast_samples / 24000 > 1.05
        await untrimmed.aclose()
        await trimmed.aclose()
    asyncio.run(run())


def test_codex_realtime_tts_falls_back_seamlessly_when_realtime_channel_unavailable():
    from eva.voice_service.realtime_tts import CodexRealtimeTTS

    async def run():
        fallback = GapToneTTS()
        rt_tts = CodexRealtimeTTS(
            fallback_tts=fallback,
            codex_executable=None,
            codex_auth_file=None,
            backend=None,
            voice="juniper",
            ready_wait_seconds=0.05,
        )
        rt_tts.prewarm()
        async with rt_tts.synthesize("**Hoi Jaap!** (https://example.org)") as stream:
            frames = [ev.frame async for ev in stream]
        assert sum(f.samples_per_channel for f in frames) > 24000
        await rt_tts.aclose()

    asyncio.run(run())


def test_closing_stream_adapter_closes_wrapped_realtime_tts_and_partial_remainder_falls_back():
    from eva.voice_service.media import _ClosingStreamAdapter
    from eva.voice_service.realtime_tts import CodexRealtimeTTS
    from livekit.agents import tokenize

    seen_fallback_texts: list[str] = []
    closed_wrapped = False

    class RecordingFallbackTTS(GapToneTTS):
        def synthesize(self, text: str, *, conn_options=None):
            seen_fallback_texts.append(text)
            return super().synthesize(text, conn_options=conn_options)

        async def aclose(self) -> None:
            nonlocal closed_wrapped
            closed_wrapped = True
            await super().aclose()

    class PartialEngine:
        usable = True

        def ensure_warming(self) -> None:
            return None

        async def wait_ready(self, _timeout: float) -> bool:
            return True

        async def stream_speech(self, _text: str, push_pcm) -> str:
            push_pcm(struct.pack("<480h", *([4000] * 480)))
            return "Tweede zin via fallback."

        async def aclose(self) -> None:
            return None

    async def run():
        fb = RecordingFallbackTTS()
        rt_tts = CodexRealtimeTTS(
            fallback_tts=fb,
            codex_executable="/bin/true",
            codex_auth_file="/etc/hostname",
            backend=object(),
            voice="juniper",
            ready_wait_seconds=0.05,
        )
        rt_tts._engine = PartialEngine()  # type: ignore[assignment]
        adapter = _ClosingStreamAdapter(
            tts=rt_tts,
            sentence_tokenizer=tokenize.blingfire.SentenceTokenizer(min_sentence_len=90),
        )
        async with rt_tts.synthesize("Eerste zin via realtime. Tweede zin via fallback.") as stream:
            frames = [ev.frame async for ev in stream]
        assert seen_fallback_texts == ["Tweede zin via fallback."]
        assert sum(f.samples_per_channel for f in frames) > 24000
        await adapter.aclose()
        assert rt_tts._closed is True
        assert closed_wrapped is True

    asyncio.run(run())


def test_open_webrtc_speech_wire_cleans_up_resources_on_setup_failure(monkeypatch):
    import pytest
    from eva.voice_service import codex_media

    closed_order: list[str] = []

    class FakeBridge:
        allow_handoffs = True

        async def open(self):
            return self

        async def close(self):
            closed_order.append("bridge")

    class FakeChannel:
        readyState = "connecting"

        def on(self, *_args):
            return None

    class FakePeer:
        connectionState = "new"

        def __init__(self, *_args, **_kwargs):
            self.localDescription = type("Desc", (), {"sdp": "v=0"})()

        def addTrack(self, _track):
            return None

        def createDataChannel(self, _name):
            return FakeChannel()

        def on(self, *_args):
            return lambda fn: fn

        async def createOffer(self):
            return self.localDescription

        async def setLocalDescription(self, _desc):
            return None

        async def close(self):
            closed_order.append("peer")

    class FailingVoiceSession:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def start(self, *_args, **_kwargs):
            raise RuntimeError("SDP HTTP 503")

        async def close(self):
            closed_order.append("voice_session")

    monkeypatch.setattr(codex_media, "EvaVoiceBackend", lambda *_a, **_kw: FakeBridge())
    monkeypatch.setattr(codex_media, "RTCPeerConnection", FakePeer)
    monkeypatch.setattr(codex_media, "CodexVoiceSession", FailingVoiceSession)

    async def run():
        with pytest.raises(RuntimeError, match="SDP HTTP 503"):
            await codex_media.open_webrtc_speech_wire(
                codex_executable="/bin/true",
                codex_auth_file="/etc/hostname",
                backend=object(),
                session_id="s-fail",
                check=lambda: None,
                voice="juniper",
                on_message=lambda _m: None,
                on_track=lambda _t: None,
                on_disconnect=lambda: None,
            )
        assert closed_order == ["voice_session", "peer", "bridge"]

    asyncio.run(run())
