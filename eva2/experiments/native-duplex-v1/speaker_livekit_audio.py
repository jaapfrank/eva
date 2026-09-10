"""LiveKit audio I/O over the existing authenticated speaker wire candidate.

No listener, core credential or new device authority is created here. The caller
must bind a household backend and disable replay based on empty played text.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from livekit import rtc
from livekit.agents import io
from speaker_input import CaptureEnded
from speaker_wire import MAX_PCM_BYTES, PlaybackInterrupted, WireError


class SpeakerAudioInput(io.AudioInput):
    def __init__(self, capture):
        super().__init__(label="eva-authorized-speaker-microphone")
        self.capture = capture

    async def __anext__(self):
        try:
            pcm = await self.capture.__anext__()
        except CaptureEnded:
            raise StopAsyncIteration from None
        if len(pcm) != 640:
            raise WireError("Speaker microphone frame shape differs")
        return rtc.AudioFrame(pcm, sample_rate=16000, num_channels=1, samples_per_channel=320)

    async def aclose(self):
        await self.capture.aclose()


@dataclass
class Segment:
    generation: int
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(100))
    task: asyncio.Task | None = None
    flushed: bool = False


class SpeakerAudioOutput(io.AudioOutput):
    def __init__(self, wire):
        super().__init__(label="eva-confirmed-speaker-output", sample_rate=24000,
                         capabilities=io.AudioOutputCapabilities(pause=False))
        self.wire = wire
        self.segment = None
        self.generation = 0
        self.barrier = None
        self.tasks = set()
        self.failure = None
        self.closed = False

    def _own(self, task):
        self.tasks.add(task)
        def done(finished):
            self.tasks.discard(finished)
            if not finished.cancelled(): finished.exception()
        task.add_done_callback(done)
        return task

    async def capture_frame(self, frame):
        if self.closed or self.failure is not None: raise WireError("Speaker output unavailable")
        if frame.sample_rate != 24000 or frame.num_channels != 1:
            raise WireError("Speaker output requires PCM16 mono at 24kHz")
        if self.barrier is not None: await asyncio.shield(self.barrier)
        if self.closed or self.failure is not None: raise WireError("Speaker output unavailable")
        previous = self.segment
        if previous is None or previous.flushed:
            if previous is not None: await asyncio.shield(previous.task)
            if self.closed or self.failure is not None: raise WireError("Speaker output unavailable")
            self.generation += 1
            self.segment = Segment(self.generation)
            self.segment.task = self._own(asyncio.create_task(self._render(self.segment)))
        await super().capture_frame(frame)
        segment = self.segment
        pcm = bytes(frame.data)
        for offset in range(0, len(pcm), MAX_PCM_BYTES):
            await self._put(segment, pcm[offset:offset+MAX_PCM_BYTES])
            if segment is not self.segment or segment.flushed:
                raise PlaybackInterrupted("Speaker output was interrupted while buffering")

    async def _put(self, segment, item):
        put = asyncio.create_task(segment.queue.put(item))
        try:
            await asyncio.wait([put, segment.task], return_when=asyncio.FIRST_COMPLETED)
            if segment.task.done():
                segment.task.result()  # Surface renderer failure to a blocked producer.
                if not put.done():
                    raise WireError("Speaker renderer ended before buffered audio")
            await put
        finally:
            if not put.done(): put.cancel()
            await asyncio.gather(put, return_exceptions=True)

    async def _render(self, segment):
        async def chunks():
            while True:
                chunk = await segment.queue.get()
                if chunk is None: return
                yield chunk
        try:
            try:
                await self.wire.speak_pcm(generation=segment.generation, source=chunks())
            except PlaybackInterrupted:
                await asyncio.shield(self.wire.start_interrupt(generation=segment.generation))
            result = self.wire.playback_result(segment.generation)
            if result.status not in {"completed", "interrupted"} or result.confirmed_frames is None:
                raise WireError("Speaker playback has no confirmed endpoint")
            # STOPPED is a lower bound. Do not provide an exact synchronized
            # transcript and never infer from zero that the answer was unheard.
            self.on_playback_finished(playback_position=result.confirmed_frames/24000,
                                      interrupted=result.status == "interrupted")
        except BaseException as error:
            self.failure = error
            raise

    def flush(self):
        super().flush()
        segment = self.segment
        if segment is None or segment.flushed: return
        segment.flushed = True
        self._own(asyncio.create_task(self._put(segment, None)))

    def clear_buffer(self):
        super().flush()
        segment = self.segment
        if segment is None or segment.task.done(): return
        segment.flushed = True
        self.barrier = self.wire.start_interrupt(generation=segment.generation)
        while not segment.queue.empty(): segment.queue.get_nowait()
        segment.queue.put_nowait(None)

    async def wait_for_playout(self):
        segment = self.segment
        if segment is not None: await asyncio.shield(segment.task)
        if self.failure is not None: raise WireError("Speaker playout failed") from self.failure
        return await super().wait_for_playout()

    async def aclose(self):
        self.closed = True
        self.clear_buffer()
        if self.barrier is not None: await asyncio.shield(self.barrier)
        if self.tasks: await asyncio.gather(*self.tasks, return_exceptions=True)
