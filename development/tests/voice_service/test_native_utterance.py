"""Literal sentence pauses stay inside one acknowledged native playback."""
import asyncio
from pathlib import Path
import struct
import sys

from livekit import rtc
from eva.voice_service.codex_speaker_audio import SpeakerSource

sys.path.insert(0, str(Path(__file__).parents[1] / 'operations'))
from test_native_duplex_speaker_wire import SpeakerPeer, opened, Kind
from speaker_livekit_audio import SpeakerAudioOutput


def frame(value):
    return rtc.AudioFrame(struct.pack('<960h', *([value] * 960)), 48000, 1, 960)


def test_literal_pause_and_soft_continuation_keep_one_native_generation():
    async def run():
        peer=SpeakerPeer()
        async with opened(peer, timeout=2) as wire:
            sink=SpeakerAudioOutput(wire); source=SpeakerSource(sink)
            marker=source.playback_marker(); token=source.begin_utterance()
            try:
                for value in [700]*5 + [0]*40 + [30]*20 + [800]*5 + [0]*16:
                    await source.capture_frame(frame(value))
                # Previously the 800 ms pause ended the first generation and
                # stalled the next sentence waiting for FINISHED before EOF.
                assert source.active and sink.generation == 1
                assert not sink.segment.flushed and not peer.end_received.is_set()
                assert source.input_suppressed
                assert source.finish_utterance(token)
                await asyncio.wait_for(peer.end_received.wait(), 1)
                assert source.input_suppressed
                peer.finished.set()
                assert await asyncio.wait_for(source.confirm_playout(marker), 1)
                assert len([p for p in peer.packets if p.kind == Kind.BEGIN]) == 1
                # Quiet continuation samples are delivered instead of falling
                # through the 300 ms pre-roll buffer between sentence segments.
                assert source.segment_samples > 43000
            finally:
                peer.stopped.set(); peer.finished.set(); await sink.aclose()
    asyncio.run(run())


def test_cancelled_utterance_cannot_flush_its_successor():
    async def run():
        class Sink:
            flushed=0
            async def capture_frame(self, value):pass
            def clear_buffer(self):pass
            def flush(self):self.flushed+=1
        sink=Sink(); source=SpeakerSource(sink)
        old=source.begin_utterance()
        await source.capture_frame(frame(700))
        source.clear_queue()
        current=source.begin_utterance()
        await source.capture_frame(frame(800))
        assert not source.finish_utterance(old)
        assert source.active and sink.flushed == 0
        assert source.finish_utterance(current)
        assert not source.active and sink.flushed == 1
        assert not source.finish_utterance(current)
    asyncio.run(run())
