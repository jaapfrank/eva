import asyncio
import importlib.util
from pathlib import Path

import pytest

from eva.adapters.connectors.home_assistant_voice import VoiceMediaError

spec = importlib.util.spec_from_file_location("local_onset_under_test", Path(__file__).parents[3] / "experiments/native-duplex-v1/local_onset.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
LocalInput = module.LocalOnsetSpeechInput


class Speech:
    def Process10ms(self, audio):
        return 0.95


def test_local_onset_precedes_ha_setup_and_preserves_every_audio_byte():
    async def run():
        stop_called, recording_closed = asyncio.Event(), asyncio.Event()
        called, received = [], []
        payload = b"ab" * (320 * 20 + 31)
        async def audio():
            try:
                # Irregular boundaries, including a final partial 10ms frame.
                for i in range(0, len(payload), 966):
                    yield payload[i:i+966]
                    await asyncio.sleep(0)
            finally:
                recording_closed.set()
        async def on_start():
            called.append("onset")
            stop_called.set()
        class HA:
            async def transcribe(self, chunks, *, on_speech_start):
                await stop_called.wait()  # HA setup deliberately waits on local VAD.
                async for frame in chunks:
                    received.append(frame)
                await on_speech_start()  # Later HA VAD must not notify twice.
                return "new question"
        adapter = LocalInput(HA(), detector_factory=Speech)
        assert await adapter.transcribe(audio(), on_speech_start=on_start) == "new question"
        assert called == ["onset"]
        assert b"".join(received) == payload
        assert recording_closed.is_set()
    asyncio.run(asyncio.wait_for(run(), 2))


def test_bounded_microphone_queue_fails_without_silently_losing_first_words():
    async def run():
        closed, stt_closed = asyncio.Event(), asyncio.Event()
        async def audio():
            try:
                for _ in range(40):
                    yield bytes(320)
                    await asyncio.sleep(0)
            finally:
                closed.set()
        class HA:
            async def transcribe(self, chunks, **kwargs):
                try:
                    await asyncio.Event().wait()
                finally:
                    stt_closed.set()
        async def onset(): pass
        adapter = LocalInput(HA(), detector_factory=Speech, buffer_frames=10)
        with pytest.raises(VoiceMediaError):
            await adapter.transcribe(audio(), on_speech_start=onset)
        assert closed.is_set() and stt_closed.is_set()
    asyncio.run(asyncio.wait_for(run(), 2))


@pytest.mark.parametrize("ending", ["cancel", "stop_failure"])
def test_cancellation_or_stop_failure_closes_input_and_ha_transport(ending):
    async def run():
        recording, closed, stt_closed, onset_started = (asyncio.Event() for _ in range(4))
        async def audio():
            try:
                recording.set()
                for _ in range(20):
                    yield bytes(320)
                    await asyncio.sleep(0)
                await asyncio.Event().wait()
            finally:
                closed.set()
        class HA:
            async def transcribe(self, chunks, **kwargs):
                try:
                    async for _ in chunks:
                        pass
                finally:
                    stt_closed.set()
        async def onset():
            onset_started.set()
            if ending == "stop_failure":
                raise OSError("speaker stop failed")
            await asyncio.Event().wait()
        adapter = LocalInput(HA(), detector_factory=Speech)
        task = asyncio.create_task(adapter.transcribe(audio(), on_speech_start=onset))
        await recording.wait()
        await onset_started.wait()
        if ending == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if ending == "cancel" else VoiceMediaError):
            await task
        assert closed.is_set() and stt_closed.is_set()
    asyncio.run(asyncio.wait_for(run(), 2))


def test_failed_detector_initialization_closes_already_active_source():
    async def run():
        class Source:
            closed = False
            def __aiter__(self): return self
            async def __anext__(self): return bytes(320)
            async def aclose(self): self.closed = True
        def broken(): raise OSError("model unavailable")
        async def onset(): pass
        source = Source()
        with pytest.raises(VoiceMediaError):
            await LocalInput(None, detector_factory=broken).transcribe(source, on_speech_start=onset)
        assert source.closed
    asyncio.run(run())
