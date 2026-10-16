"""Adapt continuous Codex audio to the existing acknowledged speaker wire sink."""
from collections import deque
import asyncio
import time
from av import AudioFrame, AudioResampler
from livekit import rtc

class SpeakerSource:
    def __init__(self,sink,diagnostic=None):
        self.sink=sink
        self.diagnostic=diagnostic or (lambda *args, **kwargs: None)
        self.last_frame_at=None
        self.max_gap_ms=0
        self.max_capture_ms=0
        self.segment_samples=0
        self.resampler=AudioResampler(format='s16',layout='mono',rate=24000)
        self.leading=deque()
        self.active=False
        self.quiet_samples=0
        self.epoch=0
        self.audible_frames=0
        self.changed=asyncio.Event()

    def _buffer_leading(self, output):
        self.leading.append(output)
        # Preserve quiet consonants before the amplitude gate opens. Digital
        # silence needs only the existing short lead, not a longer playback delay.
        limit=7200 if any(any(frame.data) for frame in self.leading) else 1440
        excess=sum(frame.samples_per_channel for frame in self.leading)-limit
        while excess>0:
            first=self.leading.popleft()
            count=first.samples_per_channel
            if count>excess:
                self.leading.appendleft(rtc.AudioFrame(
                    bytes(first.data)[excess*2:],24000,1,count-excess))
            excess-=count

    async def capture_frame(self,frame):
        epoch=self.epoch
        now=time.monotonic()
        if self.active and self.last_frame_at is not None:
            self.max_gap_ms=max(self.max_gap_ms,(now-self.last_frame_at)*1000)
        self.last_frame_at=now
        async def deliver(output):
            if epoch != self.epoch:return False
            started=time.monotonic()
            try:
                await self.sink.capture_frame(output)
            except Exception:
                # A local STOP can overtake a blocked wire producer. That old
                # frame is cancelled, not a fatal failure of the conversation.
                if epoch == self.epoch:raise
                return False
            if epoch != self.epoch:return False
            self.max_capture_ms=max(self.max_capture_ms,(time.monotonic()-started)*1000)
            self.segment_samples+=output.samples_per_channel
            return True
        value=AudioFrame(format='s16',layout='mono',samples=frame.samples_per_channel)
        value.sample_rate=48000;value.planes[0].update(bytes(frame.data))
        for pcm in self.resampler.resample(value):
            output=rtc.AudioFrame(bytes(pcm.planes[0])[:pcm.samples*2],24000,1,pcm.samples)
            audible=max((abs(v) for v in output.data),default=0)>80
            if audible:
                self.audible_frames+=1;self.changed.set()
            if not self.active:
                if not audible:
                    self._buffer_leading(output);continue
                self.max_gap_ms=0;self.max_capture_ms=0;self.segment_samples=0
                # Prime the physical playback path before any spoken samples.
                # Silence is transmitted, not slept away; STOP still fences it.
                if not await deliver(rtc.AudioFrame(bytes(4800),24000,1,2400)):return
                leading_samples=sum(previous.samples_per_channel for previous in self.leading)
                for previous in tuple(self.leading):
                    if not await deliver(previous):return
                self.leading.clear();self.active=True
                first=True
            else:first=False
            if not await deliver(output):return
            if first:
                self.diagnostic('speaker_audio_started',
                    leading_ms=round(leading_samples/24,2),
                    capture_wait_ms=round(self.max_capture_ms,2))
            self.quiet_samples=0 if audible else self.quiet_samples+pcm.samples
            if self.quiet_samples>=7200:
                self.diagnostic('speaker_audio_flushed', audio_ms=round(self.segment_samples/24,2),
                    max_input_gap_ms=round(self.max_gap_ms,2),
                    max_capture_wait_ms=round(self.max_capture_ms,2))
                self.sink.flush();self.active=False;self.quiet_samples=0;self.changed.set()

    def clear_queue(self):
        if self.active:
            self.diagnostic('speaker_audio_cleared',
                submitted_audio_ms=round(self.segment_samples/24,2))
        self.epoch+=1
        self.last_frame_at=None
        self.leading.clear();self.active=False;self.quiet_samples=0
        self.sink.clear_buffer();self.changed.set()

    def playback_marker(self):
        return self.epoch,self.audible_frames

    async def confirm_playout(self, marker):
        """Require new audio, a quiet tail and the native speaker's own ACK.

        Provider turn completion is insufficient. This confirms delivery to the
        device, not that someone heard it or that generated speech was accurate.
        """
        epoch,audible_before=marker
        if not hasattr(self.sink,'wire'):return False
        while True:
            self.changed.clear()
            if epoch!=self.epoch:return False
            if self.audible_frames>audible_before and not self.active:break
            await self.changed.wait()
        segment=self.sink.segment
        if segment is None:return False
        await self.sink.wait_for_playout()
        result=self.sink.wire.playback_result(segment.generation)
        return (epoch==self.epoch and not self.active and self.sink.segment is segment
            and result.status=='completed' and result.confirmed_frames is not None)

    async def aclose(self):
        self.clear_queue()
        # The existing speaker session owns sink closure and its STOPPED barrier.
