"""Quiet onset preservation without delaying silence or crossing STOP epochs."""
import asyncio
import struct
from av import AudioFrame, AudioResampler
from livekit import rtc
from eva.voice_service.codex_speaker_audio import SpeakerSource


def frame(value):
    return rtc.AudioFrame(struct.pack('<960h', *([value]*960)),48000,1,960)


class Sink:
    def __init__(self): self.frames=[]
    async def capture_frame(self,value): self.frames.append(bytes(value.data))
    def clear_buffer(self): self.frames.clear()


def test_quiet_onset_matches_resampled_source_byte_for_byte():
    async def run():
        sink=Sink(); source=SpeakerSource(sink)
        inputs=[frame(40) for _ in range(12)]+[frame(700)]
        reference=AudioResampler(format='s16',layout='mono',rate=24000)
        expected=[]
        for value in inputs:
            pcm=AudioFrame(format='s16',layout='mono',samples=960)
            pcm.sample_rate=48000; pcm.planes[0].update(bytes(value.data))
            expected.extend(bytes(p.planes[0])[:p.samples*2] for p in reference.resample(pcm))
            await source.capture_frame(value)
        assert sink.frames[0]==bytes(4800)
        assert b''.join(sink.frames[1:])==b''.join(expected)
    asyncio.run(run())


def test_leading_audio_is_duration_bounded_and_silence_stays_short():
    async def run():
        for amplitude,limit in [(0,1440),(40,7200)]:
            sink=Sink(); source=SpeakerSource(sink)
            for _ in range(100): await source.capture_frame(frame(amplitude))
            assert not sink.frames
            assert sum(f.samples_per_channel for f in source.leading)==limit
            await source.capture_frame(frame(700))
            assert sum(len(f)//2 for f in sink.frames)==2400+limit+480
    asyncio.run(run())


def test_stop_discards_buffered_quiet_prefix():
    async def run():
        sink=Sink(); source=SpeakerSource(sink)
        for _ in range(12): await source.capture_frame(frame(40))
        source.clear_queue()
        assert not source.leading
        # Drain the resampler's small filter tail with new silence after STOP.
        for _ in range(20): await source.capture_frame(frame(0))
        await source.capture_frame(frame(700))
        assert sum(len(f)//2 for f in sink.frames)<=2400+1920
    asyncio.run(run())


def test_stop_overtakes_blocked_quiet_prefix_delivery():
    async def run():
        entered=asyncio.Event(); release=asyncio.Event()
        class BlockedSink(Sink):
            async def capture_frame(self,value):
                entered.set(); await release.wait()
                raise RuntimeError('cancelled old generation')
            def clear_buffer(self): release.set()
        source=SpeakerSource(BlockedSink())
        for _ in range(12): await source.capture_frame(frame(40))
        pending=asyncio.create_task(source.capture_frame(frame(700)))
        await entered.wait(); source.clear_queue()
        await asyncio.wait_for(pending,1)
        assert not source.leading and not source.active
    asyncio.run(run())
