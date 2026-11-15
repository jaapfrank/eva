"""Expressive human speech renderer backed by Codex Realtime WebRTC (Single-Core Eva).

Eva's authenticated core owns 100% of identity, memory, tools, and reasoning.
This adapter uses the provisioned Codex Realtime v3 audio channel strictly as
vocal cords (`SPEECH_PROMPT` + `media_only=True`, with zero microphone audio
sent to the renderer), falling back seamlessly to Home Assistant neural TTS if
the WebRTC speech channel is still warming up or unavailable.
"""
from __future__ import annotations

import asyncio
from fractions import Fraction
import json
import struct
import time
from typing import Callable
from uuid import uuid4

import av
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from livekit.agents import tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

from eva.voice_service.agent import BridgeState
from eva.voice_service.codex_backend import EvaVoiceBackend
from eva.voice_service.codex_media import (
     MicrophoneTrack,
    SPEECH_PROMPT,
    access_lease,
    speech_parts,
)
from eva.voice_service.codex_session import CodexVoiceSession, normalize_realtime_v3_voice
from eva.voice_service.media import clean_text_for_tts

_QUIET_DRAIN_SAMPLES_24K = 3360  # 140 ms of quiet 24 kHz mono PCM
_POST_DONE_QUIET_SAMPLES_24K = 3360  # 140 ms trailing RTP quiet grace after SCTP turn.done
_POST_DONE_MAX_GRACE_SECONDS = 0.45
_QUIET_SIGNAL_THRESHOLD = 90


class _RealtimeSpeechEngine:
    """Single-session WebRTC literal speech renderer with zero user-audio ingress."""

    def __init__(
        self,
        *,
        codex_executable: str,
        codex_auth_file: str,
        backend,
        check: Callable[[], None],
        voice: str = "juniper",
    ) -> None:
        self._executable = codex_executable
        self._auth_file = codex_auth_file
        self._backend = backend
        self._check = check
        self._voice_name = normalize_realtime_v3_voice(voice)
        self._bridge: EvaVoiceBackend | None = None
        self._peer: RTCPeerConnection | None = None
        self._mic: MicrophoneTrack | None = None
        self._channel = None
        self._voice: CodexVoiceSession | None = None
        self._ready = asyncio.Event()
        self._failed = False
        self._closed = False
        self._boot_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._track_task: asyncio.Task | None = None
        self._speak_lock = asyncio.Lock()

        self._assistant_turns: set[str] = set()
        self._retired_turns: set[str] = set()
        self._awaiting_turn = False
        self._pending_retired = False
        self._assistant_idle = asyncio.Event()
        self._assistant_idle.set()
        self._drain_needed = False
        self._quiet_samples_24k = 0
        self._quiet_event = asyncio.Event()

        self._active_pcm_queue: asyncio.Queue[bytes | None] | None = None
        self._active_turn_id: str | None = None
        self._active_turn_done = False
        self._active_turn_done_at = 0.0
        self._post_done_quiet_samples = 0
        self._post_done_flushed = False

    @property
    def usable(self) -> bool:
        return not self._closed and not self._failed

    def ensure_warming(self) -> None:
        if self._closed or self._failed or self._boot_task is not None:
            return
        self._boot_task = asyncio.create_task(self._boot())

    async def wait_ready(self, timeout: float) -> bool:
        if self._closed or self._failed:
            return False
        self.ensure_warming()
        if self._ready.is_set():
            return True
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return self.usable and self._ready.is_set()
        except TimeoutError:
            return False

    async def _boot(self) -> None:
        try:
            self._check()
            dummy_state = BridgeState(self._backend, lambda _: None, replay_unheard=False)
            self._bridge = await EvaVoiceBackend(
                self._backend, dummy_state.session_id, self._check
            ).open()
            # Renderer never originates backend handoffs or receives user speech.
            self._bridge.allow_handoffs = False

            try:
                self._peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            except TypeError:
                self._peer = RTCPeerConnection()

            self._mic = MicrophoneTrack()
            self._mic.enabled = False
            self._peer.addTrack(self._mic)
            self._channel = self._peer.createDataChannel("oai-events")
            self._channel.on("message", self._on_provider_message)

            @self._peer.on("track")
            def _on_track(track):
                if self._track_task is None:
                    self._track_task = asyncio.create_task(self._consume_track(track))

            @self._peer.on("connectionstatechange")
            def _on_conn():
                if self._peer is not None and self._peer.connectionState in {"failed", "closed", "disconnected"}:
                    self._mark_failed()

            self._voice = CodexVoiceSession(
                self._executable,
                lambda: access_lease(self._auth_file),
                self._bridge,
                media_only=True,
                context_sender=self._send_context,
                voice=self._voice_name,
            )
            offer_task = asyncio.create_task(self._prepare_offer())
            await self._voice.__aenter__()
            await offer_task
            self._check()
            assert self._peer is not None and self._peer.localDescription is not None
            answer_sdp = await self._voice.start(
                self._peer.localDescription.sdp,
                prompt=SPEECH_PROMPT,
                output_modality="audio",
            )
            await self._peer.setRemoteDescription(
                RTCSessionDescription(sdp=answer_sdp, type="answer")
            )
            await asyncio.wait_for(self._ready.wait(), timeout=15.0)
            self._watch_task = asyncio.create_task(self._watch_voice())
        except asyncio.CancelledError:
            raise
        except Exception:
            self._mark_failed()
            await self.aclose()

    async def _prepare_offer(self) -> None:
        assert self._peer is not None
        offer = await self._peer.createOffer()
        await self._peer.setLocalDescription(offer)

    def _send_context(self, event: dict) -> None:
        self._check()
        if (
            self._closed
            or self._failed
            or not self._ready.is_set()
            or self._channel is None
            or self._channel.readyState != "open"
        ):
            raise RuntimeError("Realtime speech data channel is not open")
        self._channel.send(json.dumps(event, ensure_ascii=False))

    def _mark_failed(self) -> None:
        self._failed = True
        q = self._active_pcm_queue
        if q is not None:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._assistant_idle.set()
        self._quiet_event.set()

    async def _watch_voice(self) -> None:
        if self._voice is None:
            return
        try:
            while not self._closed:
                ev = await self._voice.events.get()
                if ev.get("method") in {"thread/realtime/error", "thread/realtime/closed"}:
                    self._mark_failed()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._mark_failed()

    def _on_provider_message(self, raw: str) -> None:
        if self._closed:
            return
        try:
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "session.started":
                self._ready.set()
                return
            if kind in {"error", "session.closed"}:
                self._mark_failed()
                return
            if kind == "turn.created":
                turn = event.get("turn") or {}
                if turn.get("role") == "assistant":
                    turn_id = turn.get("id")
                    if isinstance(turn_id, str) and turn_id:
                        if turn_id in self._retired_turns:
                            return
                        self._assistant_turns.add(turn_id)
                        self._assistant_idle.clear()
                        if self._awaiting_turn:
                            self._awaiting_turn = False
                            if self._pending_retired:
                                self._pending_retired = False
                                self._retired_turns.add(turn_id)
                                return
                            self._active_turn_id = turn_id
            elif kind == "turn.done":
                turn = event.get("turn") or {}
                if turn.get("role") == "assistant":
                    turn_id = turn.get("id")
                    if isinstance(turn_id, str) and turn_id:
                        self._retired_turns.add(turn_id)
                        while len(self._retired_turns) > 128:
                            self._retired_turns.pop()
                        self._assistant_turns.discard(turn_id)
                    if not self._assistant_turns and not self._awaiting_turn:
                        self._assistant_idle.set()
                    if turn_id and turn_id == self._active_turn_id:
                        self._active_turn_id = None
                        self._active_turn_done = True
                        self._active_turn_done_at = time.monotonic()
                        self._post_done_quiet_samples = 0
        except Exception:
            self._mark_failed()

    async def _consume_track(self, track) -> None:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
        try:
            while not self._closed:
                frame = await track.recv()
                for pcm_frame in resampler.resample(frame):
                    n_samples = pcm_frame.samples
                    if n_samples <= 0:
                        continue
                    pcm_bytes = bytes(pcm_frame.planes[0])[: n_samples * 2]
                    vals = struct.unpack(f"<{n_samples}h", pcm_bytes)
                    peak = max((abs(v) for v in vals), default=0)
                    has_signal = peak > _QUIET_SIGNAL_THRESHOLD

                    q = self._active_pcm_queue
                    if q is not None and not self._post_done_flushed:
                        try:
                            q.put_nowait(pcm_bytes)
                        except asyncio.QueueFull:
                            pass
                        if self._active_turn_done:
                            if has_signal:
                                self._post_done_quiet_samples = 0
                            else:
                                self._post_done_quiet_samples += n_samples
                            if (
                                self._post_done_quiet_samples >= _POST_DONE_QUIET_SAMPLES_24K
                                or (time.monotonic() - self._active_turn_done_at) >= _POST_DONE_MAX_GRACE_SECONDS
                            ):
                                self._post_done_flushed = True
                                try:
                                    q.put_nowait(None)
                                except asyncio.QueueFull:
                                    pass
                    elif self._drain_needed:
                        if has_signal:
                            self._quiet_samples_24k = 0
                        else:
                            self._quiet_samples_24k += n_samples
                        self._quiet_event.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closed:
                self._mark_failed()

    def _retire_active_speech(self) -> None:
        self._active_pcm_queue = None
        self._active_turn_id = None
        self._active_turn_done = False
        self._post_done_flushed = False
        if self._assistant_turns or self._awaiting_turn:
            self._drain_needed = True
            self._retired_turns.update(self._assistant_turns)
            if self._awaiting_turn:
                self._pending_retired = True

    async def _drain_interrupted_tail(self) -> bool:
        try:
            await asyncio.wait_for(self._assistant_idle.wait(), timeout=0.35)
        except TimeoutError:
            return False
        if not self._drain_needed:
            return True
        self._quiet_samples_24k = 0
        deadline = time.monotonic() + 0.35
        while self._quiet_samples_24k < _QUIET_DRAIN_SAMPLES_24K:
            rem = deadline - time.monotonic()
            if rem <= 0:
                return False
            self._quiet_event.clear()
            try:
                await asyncio.wait_for(self._quiet_event.wait(), timeout=min(0.12, rem))
            except TimeoutError:
                # No incoming RTP packets means silence on the wire as well.
                break
        self._drain_needed = False
        return True

    async def stream_speech(self, text: str, push_pcm: Callable[[bytes], None]) -> str:
        """Stream literal `text` as 24 kHz s16 mono PCM; returns unspoken remainder ('' on full success)."""
        parts = [p.strip() for p in speech_parts(text) if p and p.strip()]
        if not parts:
            return ""
        if not self.usable or not self._ready.is_set() or self._voice is None:
            return " ".join(parts)

        async with self._speak_lock:
            if not await self._drain_interrupted_tail():
                self._mark_failed()
                return " ".join(parts)

            for idx, part in enumerate(parts):
                self._check()
                if not self.usable:
                    return " ".join(parts[idx:])
                try:
                    await asyncio.wait_for(self._assistant_idle.wait(), timeout=0.6)
                except TimeoutError:
                    self._mark_failed()
                    return " ".join(parts[idx:])

                pcm_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=512)
                self._active_pcm_queue = pcm_queue
                self._active_turn_id = None
                self._active_turn_done = False
                self._active_turn_done_at = 0.0
                self._post_done_quiet_samples = 0
                self._post_done_flushed = False
                self._awaiting_turn = True
                self._pending_retired = False
                self._assistant_idle.clear()
                got_part_audio = False
                try:
                    await self._voice.say(part)
                    first_chunk_deadline = time.monotonic() + 3.2
                    overall_deadline = time.monotonic() + max(18.0, min(90.0, len(part) / 6.0 + 8.0))
                    while True:
                        if self._failed or self._closed:
                            self._retire_active_speech()
                            return " ".join(parts[idx + 1 :]) if got_part_audio else " ".join(parts[idx:])
                        if self._post_done_flushed and pcm_queue.empty():
                            break
                        now = time.monotonic()
                        base_deadline = first_chunk_deadline if not got_part_audio else overall_deadline
                        if self._active_turn_done:
                            done_grace_deadline = self._active_turn_done_at + _POST_DONE_MAX_GRACE_SECONDS
                            if now >= done_grace_deadline and pcm_queue.empty():
                                break
                            wait_s = min(
                                0.16 if got_part_audio else _POST_DONE_MAX_GRACE_SECONDS,
                                max(0.01, min(base_deadline, done_grace_deadline) - now),
                            )
                        else:
                            wait_s = base_deadline - now
                            if wait_s <= 0:
                                self._retire_active_speech()
                                self._mark_failed()
                                return " ".join(parts[idx + 1 :]) if got_part_audio else " ".join(parts[idx:])
                        try:
                            chunk = await asyncio.wait_for(pcm_queue.get(), timeout=wait_s)
                        except TimeoutError:
                            if self._active_turn_done:
                                break
                            self._retire_active_speech()
                            self._mark_failed()
                            return " ".join(parts[idx + 1 :]) if got_part_audio else " ".join(parts[idx:])
                        if chunk is None:
                            break
                        n_s = len(chunk) // 2
                        if n_s > 0:
                            if not got_part_audio:
                                vals = struct.unpack(f"<{n_s}h", chunk)
                                if max((abs(v) for v in vals), default=0) > _QUIET_SIGNAL_THRESHOLD:
                                    got_part_audio = True
                            if got_part_audio:
                                push_pcm(chunk)
                    if not got_part_audio:
                        self._retire_active_speech()
                        self._mark_failed()
                        return " ".join(parts[idx:])
                except asyncio.CancelledError:
                    self._retire_active_speech()
                    raise
                except Exception:
                    self._retire_active_speech()
                    self._mark_failed()
                    return " ".join(parts[idx + 1 :]) if got_part_audio else " ".join(parts[idx:])
                finally:
                    self._active_pcm_queue = None
                    self._active_turn_id = None
                    self._active_turn_done = False
                    self._post_done_flushed = False

            return ""

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._ready.set()
        self._assistant_idle.set()
        self._quiet_event.set()
        q = self._active_pcm_queue
        if q is not None:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        for t in (self._boot_task, self._watch_task, self._track_task):
            if t is not None and not t.done() and t is not asyncio.current_task():
                t.cancel()
        for t in (self._boot_task, self._watch_task, self._track_task):
            if t is not None and t is not asyncio.current_task():
                await asyncio.gather(t, return_exceptions=True)
        if self._voice is not None:
            try:
                await self._voice.close()
            except Exception:
                pass
            self._voice = None
        if self._peer is not None:
            try:
                await self._peer.close()
            except Exception:
                pass
            self._peer = None
        if self._bridge is not None:
            try:
                await self._bridge.close()
            except Exception:
                pass
            self._bridge = None


class CodexRealtimeTTS(tts.TTS):
    """Pre-warmed OpenAI Realtime human voice TTS with seamless HA Neural fallback."""

    def __init__(
        self,
        *,
        fallback_tts: tts.TTS,
        codex_executable: str | None,
        codex_auth_file: str | None,
        backend,
        check: Callable[[], None] = lambda: None,
        voice: str = "juniper",
        ready_wait_seconds: float = 1.25,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=24000,
            num_channels=1,
        )
        self.fallback_tts = fallback_tts
        self._executable = codex_executable
        self._auth_file = codex_auth_file
        self._backend = backend
        self._check = check
        self._voice = normalize_realtime_v3_voice(voice)
        self._ready_wait_seconds = ready_wait_seconds
        self._closed = False
        self._engine: _RealtimeSpeechEngine | None = None

    def prewarm(self) -> None:
        if self._closed or not self._executable or not self._auth_file:
            return
        if self._engine is None or not self._engine.usable:
            self._engine = _RealtimeSpeechEngine(
                codex_executable=self._executable,
                codex_auth_file=self._auth_file,
                backend=self._backend,
                check=self._check,
                voice=self._voice,
            )
        self._engine.ensure_warming()

    def _get_or_rotate_engine(self) -> _RealtimeSpeechEngine | None:
        if self._closed or not self._executable or not self._auth_file:
            return None
        if self._engine is None or not self._engine.usable:
            old = self._engine
            if old is not None:
                asyncio.create_task(old.aclose())
            self._engine = _RealtimeSpeechEngine(
                codex_executable=self._executable,
                codex_auth_file=self._auth_file,
                backend=self._backend,
                check=self._check,
                voice=self._voice,
            )
            self._engine.ensure_warming()
        return self._engine

    def synthesize(self, text: str, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        self._check()
        return _RealtimeChunkedStream(
            tts=self,
            input_text=clean_text_for_tts(text),
            conn_options=conn_options,
        )

    async def aclose(self) -> None:
        self._closed = True
        eng, self._engine = self._engine, None
        if eng is not None:
            await eng.aclose()
        await self.fallback_tts.aclose()


class _RealtimeChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter) -> None:
        text = self.input_text.strip()
        output_emitter.initialize(
            request_id=str(uuid4()),
            sample_rate=24000,
            num_channels=1,
            mime_type="audio/pcm",
            frame_size_ms=20,
        )
        if not text:
            output_emitter.flush()
            return

        def push_pcm(pcm: bytes) -> None:
            if pcm:
                output_emitter.push(pcm)

        remainder = text
        engine = self._tts._get_or_rotate_engine()
        if engine is not None:
            ready = await engine.wait_ready(self._tts._ready_wait_seconds)
            if ready:
                remainder = await engine.stream_speech(text, push_pcm)
                if not remainder:
                    output_emitter.flush()
                    return
                # Rotate failed engine in background so the next utterance gets a fresh channel.
                self._tts._get_or_rotate_engine()

        if remainder:
            async with self._tts.fallback_tts.synthesize(
                remainder, conn_options=self._conn_options
            ) as fb_stream:
                async for ev in fb_stream:
                    frame = ev.frame
                    if frame.sample_rate == 24000 and frame.num_channels == 1:
                        output_emitter.push(bytes(frame.data))
                    else:
                        resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
                        af = av.AudioFrame(format="s16", layout="mono", samples=frame.samples_per_channel)
                        af.sample_rate = frame.sample_rate
                        af.time_base = Fraction(1, frame.sample_rate)
                        af.planes[0].update(bytes(frame.data))
                        for out_f in resampler.resample(af):
                            output_emitter.push(bytes(out_f.planes[0])[: out_f.samples * 2])
        output_emitter.flush()
