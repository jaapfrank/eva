"""Experimental generation-bound output over an already authenticated socket.

The caller binds/authenticates the satellite and owns the connection. A new
connection gets a fresh session nonce. This module does not authorize a device,
start a listener, or claim that stock va_pipecat understands this extension.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import IntEnum
import secrets
import math
import struct
from typing import Protocol


class Kind(IntEnum):
    OPEN = 1
    BEGIN = 2
    AUDIO = 3
    END = 4
    STOP = 5
    OPENED = 129
    BEGUN = 130
    ACCEPTED = 131
    FINISHED = 132
    STOPPED = 133
    ERROR = 134


HEADER = struct.Struct("!4sB3x8sIII")
MAX_PCM_BYTES = 960  # 20 ms PCM16 mono at 24 kHz
MAX_REPLY_FRAMES = 24_000 * 180


class WireError(RuntimeError):
    pass


class PlaybackInterrupted(WireError):
    pass


@dataclass(frozen=True)
class Packet:
    kind: Kind
    session: bytes
    generation: int = 0
    sequence: int = 0
    payload: bytes = b""

    def encode(self) -> bytes:
        if (not isinstance(self.kind, Kind) or not isinstance(self.session, bytes)
                or not isinstance(self.payload, bytes)
                or len(self.session) != 8 or self.session == bytes(8)
                or type(self.generation) is not int or not 0 <= self.generation <= 0xFFFFFFFF
                or type(self.sequence) is not int or not 0 <= self.sequence <= 0xFFFFFFFF
                or len(self.payload) > MAX_PCM_BYTES):
            raise WireError("invalid output record")
        if self.kind == Kind.AUDIO:
            if not self.payload or len(self.payload) % 2:
                raise WireError("invalid PCM record")
        elif self.kind in {Kind.ACCEPTED, Kind.FINISHED, Kind.STOPPED}:
            if len(self.payload) != 4:
                raise WireError("receipt must contain a frame count")
        elif self.payload:
            raise WireError("unexpected control payload")
        return HEADER.pack(b"EVA2", self.kind, self.session, self.generation,
                           self.sequence, len(self.payload)) + self.payload

    @classmethod
    def decode(cls, raw: bytes) -> Packet:
        if not isinstance(raw, bytes) or not HEADER.size <= len(raw) <= HEADER.size + MAX_PCM_BYTES:
            raise WireError("invalid binary record size")
        magic, kind, session, generation, sequence, size = HEADER.unpack_from(raw)
        if magic != b"EVA2" or raw[5:8] != bytes(3) or len(raw) != HEADER.size + size:
            raise WireError("invalid output record header")
        try:
            packet = cls(Kind(kind), session, generation, sequence, raw[HEADER.size:])
        except ValueError as error:
            raise WireError("unsupported output record") from error
        packet.encode()  # apply the same shape validation in both directions
        return packet


class Socket(Protocol):
    async def send(self, data: bytes) -> None: ...
    async def recv(self) -> bytes: ...
    async def close(self) -> None: ...


class FramedSpeakerOutput:
    """MediaOutput: synthesize through the supplied existing HA PCM adapter.

    One PCM batch is outstanding at a time. Acceptance is flow control only;
    speak returns only on FINISHED with the exact supplied PCM frame count.
    The receiver must issue that receipt from actual downstream playback and
    must reject every late batch of a stopped generation indefinitely.
    """
    def __init__(self, *, socket: Socket, pcm: Callable[[str], AsyncIterator[bytes]],
                 session: bytes | None = None, receipt_timeout: float = 3.5,
                 send_timeout: float = 0.5, retired_sessions: tuple[bytes, ...] = ()) -> None:
        self._socket, self._pcm = socket, pcm
        self.session = session if session is not None else secrets.token_bytes(8)
        Packet(Kind.OPEN, self.session).encode()
        if (not all(math.isfinite(t) and t > 0 for t in (receipt_timeout, send_timeout))
                or len(retired_sessions) > 8 or self.session in retired_sessions):
            raise ValueError("invalid speaker session bounds")
        for retired in retired_sessions:
            Packet(Kind.OPEN, retired).encode()
        self._retired_sessions = frozenset(retired_sessions)
        self._retired_receipts = 0
        self._receipt_timeout, self._send_timeout = receipt_timeout, send_timeout
        self._writer = asyncio.Lock()
        self._pending: dict[tuple[Kind, int, int], asyncio.Future] = {}
        self._recent: deque[tuple[Kind, int, int]] = deque(maxlen=64)
        self._stops: dict[int, asyncio.Task] = {}
        self._renderers: dict[int, asyncio.Task] = {}
        self._sent_frames: dict[int, int] = {}
        self._reader: asyncio.Task | None = None
        self._closer: asyncio.Task | None = None
        self._failure: BaseException | None = None
        self._highest = self._cancelled_through = 0
        self._active_generation = 0
        self._open = False
        self._closing = False

    async def start(self) -> None:
        if self._reader is not None or self._closing:
            raise WireError("speaker connection cannot be reused")
        self._reader = asyncio.create_task(self._read())
        try:
            await self._exchange(Packet(Kind.OPEN, self.session), Kind.OPENED)
            if self._closing:
                raise WireError("speaker connection closed during startup")
            self._open = True
        except BaseException:
            await self.close()
            raise

    def _fail(self, error: BaseException) -> None:
        self._failure = self._failure or error
        self._open = False
        for task in self._renderers.values():
            if task is not asyncio.current_task() and not task.done() and not task.cancelling():
                task.cancel()
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(WireError("speaker connection failed"))

    async def _read(self) -> None:
        try:
            while True:
                reply = Packet.decode(await self._socket.recv())
                if reply.session != self.session:
                    # Only the authenticated device owner supplies this history.
                    # A queued receipt may cross reconnect, but can never satisfy
                    # a new operation. Unknown nonces still fail the connection.
                    if (reply.session in self._retired_sessions and self._retired_receipts < 8
                            and reply.kind in {Kind.OPENED, Kind.BEGUN, Kind.ACCEPTED,
                                               Kind.FINISHED, Kind.STOPPED, Kind.ERROR}):
                        self._retired_receipts += 1
                        continue
                    raise WireError("speaker receipt belongs to another session")
                if reply.kind == Kind.ERROR:
                    raise WireError("speaker reported output failure")
                key = reply.kind, reply.generation, reply.sequence
                future = self._pending.get(key)
                if future is not None and not future.done():
                    future.set_result(reply)
                elif key in self._recent:
                    continue
                elif (reply.generation <= self._cancelled_through and reply.generation > 0
                      and reply.kind in {Kind.BEGUN, Kind.ACCEPTED, Kind.FINISHED}):
                    continue  # stale completion cannot reopen a cancelled answer
                else:
                    raise WireError("unsolicited speaker receipt")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._fail(error)
            await self._socket.close()

    async def _exchange(self, packet: Packet, expected: Kind, *, playback_seconds: float = 0) -> Packet:
        key = expected, packet.generation, packet.sequence
        if key in self._pending:
            raise WireError("duplicate pending output operation")
        future = asyncio.get_running_loop().create_future()
        self._pending[key] = future
        try:
            async with self._writer:
                if self._failure is not None:
                    raise WireError("speaker connection has failed")
                if (packet.kind in {Kind.BEGIN, Kind.AUDIO, Kind.END}
                        and packet.generation <= self._cancelled_through):
                    raise PlaybackInterrupted("answer was interrupted")
                async with asyncio.timeout(self._send_timeout):
                    await self._socket.send(packet.encode())
            async with asyncio.timeout(self._receipt_timeout + playback_seconds):
                return await asyncio.shield(future)
        except TimeoutError as error:
            self._fail(error)
            await self._socket.close()
            raise WireError("speaker operation was not confirmed") from error
        except PlaybackInterrupted:
            raise
        except Exception as error:
            self._fail(error)
            await self._socket.close()
            raise WireError("speaker transport failed") from error
        finally:
            self._pending.pop(key, None)
            self._recent.append(key)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # consume a failure delivered during send/cancellation

    def _owned_stop(self, generation: int) -> asyncio.Task:
        self._cancelled_through = max(self._cancelled_through, generation)
        if generation not in self._stops:
            task = asyncio.create_task(self._stop(generation))
            self._stops[generation] = task
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        return self._stops[generation]

    async def _stop(self, generation: int) -> None:
        self._cancelled_through = max(self._cancelled_through, generation)
        renderers = [task for current, task in self._renderers.items() if current <= generation]
        for task in renderers:
            if not task.done() and not task.cancelling():
                task.cancel()
        for (kind, current, _), future in tuple(self._pending.items()):
            if current <= generation and kind in {Kind.BEGUN, Kind.ACCEPTED, Kind.FINISHED} and not future.done():
                future.set_exception(PlaybackInterrupted("answer was interrupted"))
        try:
            reply = await self._exchange(Packet(Kind.STOP, self.session, generation), Kind.STOPPED)
            if struct.unpack("!I", reply.payload)[0] > self._sent_frames.get(generation, 0):
                raise WireError("speaker stop frame count exceeds supplied audio")
        finally:
            # Stop the hardware before joining decoder/HA cleanup. The owned
            # renderer never waits on this stop, so this cannot cancel itself.
            await asyncio.gather(*renderers, return_exceptions=True)

    async def interrupt(self, *, generation: int) -> None:
        if type(generation) is not int or not 1 <= generation <= 0xFFFFFFFF:
            raise WireError("invalid stop generation")
        # Shield the physical stop from cancellation of speak/new-speech/close.
        await asyncio.shield(self._owned_stop(generation))

    async def speak(self, *, generation: int, text: str) -> None:
        if not self._open or self._closing:
            raise WireError("speaker connection is not ready")
        if type(generation) is not int or not self._highest < generation <= 0xFFFFFFFF:
            raise WireError("answer generation must increase")
        if generation <= self._cancelled_through:
            raise PlaybackInterrupted("answer was already interrupted")
        if self._active_generation > self._cancelled_through:
            raise WireError("previous answer is still active")
        if not isinstance(text, str) or not text.strip() or len(text) > 16_384:
            raise WireError("invalid reply text")
        self._highest = self._active_generation = generation
        renderer = asyncio.create_task(self._render(generation, text))
        self._renderers[generation] = renderer
        try:
            await asyncio.shield(renderer)
        except BaseException as error:
            await asyncio.shield(self._owned_stop(generation))
            if isinstance(error, asyncio.CancelledError) and not asyncio.current_task().cancelling():
                raise PlaybackInterrupted("answer was interrupted") from error
            raise
        finally:
            if self._active_generation == generation:
                self._active_generation = 0
            if renderer.done():
                self._renderers.pop(generation, None)

    async def _render(self, generation: int, text: str) -> None:
        # A previous incomplete physical stop must never be bypassed.
        source = None
        try:
            await asyncio.gather(*(asyncio.shield(t) for t in self._stops.values()))
            await self._exchange(Packet(Kind.BEGIN, self.session, generation), Kind.BEGUN)
            source = self._pcm(text).__aiter__()
            frames = sequence = 0
            async for chunk in source:
                frames += len(chunk) // 2
                if frames > MAX_REPLY_FRAMES:
                    raise WireError("reply audio exceeds its duration bound")
                sequence += 1
                self._sent_frames[generation] = frames
                reply = await self._exchange(Packet(Kind.AUDIO, self.session, generation, sequence, chunk), Kind.ACCEPTED)
                if struct.unpack("!I", reply.payload)[0] != frames:
                    raise WireError("speaker accepted frame count differs")
            if not frames:
                raise WireError("reply audio is empty")
            # Queue acceptance may run ahead of the DAC. Allow the known audio
            # duration for normal completion, without extending STOP's deadline.
            reply = await self._exchange(Packet(Kind.END, self.session, generation, sequence),
                                         Kind.FINISHED, playback_seconds=frames / 24_000)
            if struct.unpack("!I", reply.payload)[0] != frames:
                raise WireError("speaker played frame count differs")
        finally:
            if source is not None and hasattr(source, "aclose"):
                await source.aclose()

    async def close(self) -> None:
        self._closing = True
        if self._closer is None:
            self._closer = asyncio.create_task(self._close())
            self._closer.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        await asyncio.shield(self._closer)

    async def _close(self) -> None:
        self._closing = True
        try:
            if self._highest:
                self._owned_stop(self._highest)
            await asyncio.gather(*(asyncio.shield(t) for t in self._stops.values()))
        finally:
            self._open = False
            for task in self._renderers.values():
                if not task.done() and not task.cancelling():
                    task.cancel()
            await asyncio.gather(*self._renderers.values(), return_exceptions=True)
            if self._reader is not None:
                self._reader.cancel()
                await asyncio.gather(self._reader, return_exceptions=True)
            await self._socket.close()
