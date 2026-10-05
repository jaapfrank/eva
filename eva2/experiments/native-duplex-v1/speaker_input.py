"""Microphone/control half of the existing authenticated speaker connection.

FramedSpeakerOutput remains the only socket reader and writer. No raw PCM is
admitted before a local WAKE and confirmed CAPTURE; mute cannot be overridden
by a server request. All buffer overflow and active sequence gaps fail closed.
"""
from __future__ import annotations

import asyncio
from collections import deque

from speaker_wire import Kind, Packet, WireError


class CaptureEnded(WireError):
    pass


class CaptureSource:
    def __init__(self, owner, epoch):
        self.owner, self.epoch = owner, epoch
        self.sequence = 0
        self.frames: deque[bytes] = deque()
        self.state = "opening"
        self.closer: asyncio.Task | None = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            changed = self.owner._changed
            self.owner.check()
            if self.state != "active":
                raise CaptureEnded("microphone capture ended")
            if self.frames:
                return self.frames.popleft()
            await changed.wait()

    def start_close(self):
        if self.closer is None:
            self.state = "closing"
            self.frames.clear()
            self.owner.notify()
            self.closer = asyncio.create_task(self.owner._end_capture(self))
        return self.closer

    async def aclose(self):
        await asyncio.shield(self.start_close())


class SpeakerMicrophone:
    def __init__(self, output):
        self._output = output
        self._wake = asyncio.Event()
        self.control_observer = None  # Optional synchronous admission barrier.
        self._controls: asyncio.Queue[Kind] = asyncio.Queue(1)
        self._changed = asyncio.Event()
        self._failure: BaseException | None = None
        self._capture: CaptureSource | None = None
        self._epoch = 0
        self._armed = self._muted = self._closed = self._closing = False

    def notify(self):
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    def check(self):
        if self._failure is not None:
            raise WireError("microphone connection failed") from self._failure
        if self._closed:
            raise CaptureEnded("microphone connection is closed")

    def fail(self, error):
        self._failure = self._failure or error
        if self._capture is not None:
            self._capture.frames.clear()
        self.notify()

    def receive(self, record: Packet) -> bool:
        """Nonblocking demux on the sole socket reader, before receipt delivery."""
        if record.kind not in {Kind.WAKE, Kind.MICROPHONE, Kind.LOCAL_STOP,
                               Kind.MUTED, Kind.END_SESSION, Kind.CAPTURED, Kind.CAPTURE_ENDED}:
            return False
        self.check()
        if record.kind == Kind.WAKE:
            opened = self._output._pending.get((Kind.OPENED, 0, 0))
            if not self._output._open and not (opened is not None and opened.done()):
                raise WireError("local wake preceded connection setup")
            if self._wake.is_set() or self._muted or record.generation != 0 or record.sequence != 0:
                raise WireError("unsolicited or repeated local wake")
            self._armed = True
            self._wake.set()
            self.notify()
            return True
        capture = self._capture
        if capture is None or record.generation != capture.epoch:
            raise WireError("microphone capture binding differs")
        if record.kind == Kind.CAPTURED:
            if capture.state != "opening" or record.sequence != 0:
                raise WireError("unexpected microphone start receipt")
            capture.state = "active"  # Before queued PCM can reach this reader.
            return False  # FramedSpeakerOutput must also satisfy its exchange.
        if record.kind == Kind.CAPTURE_ENDED:
            if capture.state != "closing" or record.sequence != 0:
                raise WireError("unexpected microphone end receipt")
            capture.state = "ended"
            self.notify()
            return False
        if record.kind == Kind.MICROPHONE:
            if capture.state not in {"active", "closing", "interrupted"}:
                raise WireError("microphone audio outside capture")
            if record.sequence != capture.sequence + 1:
                raise WireError("microphone sequence gap or replay")
            capture.sequence = record.sequence
            if capture.state == "active":
                if len(capture.frames) >= 100:  # Two seconds; never discard words.
                    raise WireError("microphone transport buffer overrun")
                capture.frames.append(record.payload)
                self.notify()
            return True
        terminal = record.kind in {Kind.MUTED, Kind.END_SESSION}
        states = {"active", "opening", "interrupted", "closing"} if terminal else {"active", "opening"}
        if capture.state not in states or record.sequence != capture.sequence:
            raise WireError("unexpected local microphone control")
        if capture.state != "closing":
            capture.state = "interrupted"
        capture.frames.clear()
        self._muted = self._muted or record.kind == Kind.MUTED
        self._closing = self._closing or terminal
        if self.control_observer is not None:
            self.control_observer(record.kind)
        self._armed = not self._closing and record.kind == Kind.LOCAL_STOP
        try:
            self._controls.put_nowait(record.kind)
        except asyncio.QueueFull:
            if not terminal:
                raise WireError("unconsumed microphone control") from None
            # A final stop may overtake a queued wakeword interruption.
            self._controls.get_nowait()
            self._controls.put_nowait(record.kind)
        self.notify()
        return True

    async def wait_wake(self):
        while not self._wake.is_set():
            changed = self._changed
            self.check()
            await changed.wait()
        self.check()

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def control_pending(self) -> bool:
        return not self._controls.empty()

    async def next_control(self) -> Kind:
        while True:
            changed = self._changed
            self.check()
            if not self._controls.empty():
                return self._controls.get_nowait()
            await changed.wait()

    async def begin_capture(self) -> CaptureSource:
        self.check()
        if (self._closing or not self._armed or self._muted or not self._wake.is_set()
                or (self._capture is not None and self._capture.state != "ended")
                or self._epoch >= 512):
            raise WireError("microphone capture lacks a fresh local grant")
        self._armed = False
        self._epoch += 1
        capture = self._capture = CaptureSource(self, self._epoch)
        try:
            await self._output._exchange(
                Packet(Kind.CAPTURE, self._output.session, capture.epoch), Kind.CAPTURED)
            self.check()
            if capture.state != "active":
                raise CaptureEnded("microphone was stopped during opening")
            return capture
        except BaseException:
            await capture.aclose()
            raise

    async def _end_capture(self, capture):
        try:
            if self._failure is None:
                await self._output._exchange(
                    Packet(Kind.CAPTURE_END, self._output.session, capture.epoch),
                    Kind.CAPTURE_ENDED)
        finally:
            capture.state = "ended"
            capture.frames.clear()
            self.notify()

    def start_close(self):
        self._closing = True
        if self._capture is not None:
            self._capture.start_close()

    async def close(self):
        self.start_close()
        if self._capture is not None:
            await self._capture.aclose()
        self._closed = True
        self.notify()
