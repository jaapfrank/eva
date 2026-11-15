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
    assert clean_text_for_tts("Zie [Eredivisie](https://eredivisie.nl/stand) nu!") == "Zie Eredivisie nu!"

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
