"""Send VAD-delimited PCM to the existing STT provider while speech arrives."""
import asyncio
import time

from livekit import rtc
from livekit.agents import stt, utils, vad
from livekit.agents.types import APIConnectOptions


class HomeAssistantInputStream(stt.RecognizeStream):
    """One provider run at a time; a final transcript requires local input EOF.

    Local VAD owns segmentation. A provider ending before consuming that
    segment fails explicitly instead of admitting a possibly truncated command.
    Native VAD keeps following input while the preceding final is pending.
    """

    def __init__(self, owner, detector):
        self._pending_seconds = 0.0
        self._detector = detector
        self._closed_input = False
        self._input_anchor = None
        self._failure = None
        self._failed = asyncio.Event()
        super().__init__(stt=owner, conn_options=APIConnectOptions(max_retry=0))

    def push_frame(self, frame):
        try:
            self._stt.check()
            if self._closed_input:
                raise RuntimeError("speech input is closed")
            duration = frame.samples_per_channel / frame.sample_rate
            if self._pending_seconds + duration > 6:
                raise RuntimeError("speech input processing backlog exceeded its bound")
            self._pending_seconds += duration
            if self._input_anchor is None:
                self._input_anchor = time.time()
            super().push_frame(frame)
        except Exception as error:
            self._closed_input = True
            self._failure = error
            self._failed.set()
            raise

    async def aclose(self):
        self._closed_input = True
        await super().aclose()

    async def _run(self):
        detector = self._detector.stream()
        queue = None
        recognition = None
        input_consumed = False

        async def forward():
            async for item in self._input_ch:
                self._stt.check()
                if isinstance(item, self._FlushSentinel):
                    detector.flush()
                else:
                    detector.push_frame(item)
            detector.end_input()

        async def chunks(segment):
            nonlocal input_consumed
            resampler = None
            while True:
                frame = await segment.get()
                self._stt.check()
                if frame is None:
                    if resampler is not None:
                        for output in resampler.flush():
                            yield bytes(output.data)
                    input_consumed = True
                    return
                if frame.sample_rate == 16000 and frame.num_channels == 1:
                    yield bytes(frame.data)
                    continue
                if resampler is None:
                    resampler = rtc.AudioResampler(frame.sample_rate, 16000, num_channels=1)
                for output in resampler.push(frame):
                    data = bytes(output.data)
                    for offset in range(0, len(data), 3200):
                        yield data[offset:offset + 3200]

        def enqueue(frames):
            for frame in frames:
                try:
                    queue.put_nowait(frame)
                except asyncio.QueueFull:
                    raise RuntimeError("speech provider backlog exceeded its bound") from None

        def provider_done(task):
            if task.cancelled():
                return
            error = task.exception()
            if error is None and not input_consumed:
                error = RuntimeError("speech provider ended before consuming the local segment")
            if error is not None:
                self._failure = error
                self._closed_input = True
                self._failed.set()

        async def recognize():
            nonlocal queue, recognition, input_consumed
            async for event in detector:
                self._stt.check()
                if event.type == vad.VADEventType.INFERENCE_DONE:
                    self._pending_seconds = max(0.0, self._pending_seconds - sum(
                        frame.samples_per_channel / frame.sample_rate for frame in event.frames))
                    if queue is not None:
                        enqueue(event.frames)
                elif event.type == vad.VADEventType.START_OF_SPEECH:
                    if queue is not None:
                        raise RuntimeError("speech detector repeated segment start")
                    queue = asyncio.Queue(maxsize=128)
                    input_consumed = False
                    enqueue(event.frames)
                    recognition = asyncio.create_task(self._stt.source.transcribe(chunks(queue)))
                    recognition.add_done_callback(provider_done)
                    self._event_ch.send_nowait(stt.SpeechEvent(stt.SpeechEventType.START_OF_SPEECH))
                elif event.type == vad.VADEventType.END_OF_SPEECH:
                    if queue is None:
                        raise RuntimeError("speech detector ended without a segment")
                    end_time = (self._input_anchor or self.start_time) + event.timestamp - event.silence_duration
                    self._event_ch.send_nowait(stt.SpeechEvent(
                        stt.SpeechEventType.END_OF_SPEECH, speech_end_time=end_time))
                    enqueue([None])
                    text = await recognition
                    self._stt.check()
                    if self._closed_input or not input_consumed:
                        raise RuntimeError("speech provider ended before consuming the local segment")
                    queue = recognition = None
                    self._event_ch.send_nowait(stt.SpeechEvent(
                        stt.SpeechEventType.FINAL_TRANSCRIPT,
                        alternatives=[stt.SpeechData(language="nl", text=text)],
                        speech_end_time=end_time))

        async def failed():
            await self._failed.wait()
            raise self._failure

        fatal = asyncio.create_task(failed())
        tasks = [asyncio.create_task(forward()), asyncio.create_task(recognize())]
        try:
            completed = asyncio.gather(*tasks)
            await asyncio.wait((completed, fatal), return_when=asyncio.FIRST_COMPLETED)
            if fatal.done():
                await fatal
            await completed
        finally:
            self._closed_input = True
            await utils.aio.cancel_and_wait(fatal)
            await utils.aio.cancel_and_wait(*tasks)
            if 'completed' in locals():
                await asyncio.gather(completed, return_exceptions=True)
            if recognition is not None:
                await utils.aio.cancel_and_wait(recognition)
            if queue is not None:
                while not queue.empty():
                    queue.get_nowait()
            await detector.aclose()
