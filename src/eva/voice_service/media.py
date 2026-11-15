"""Adapt the already permitted HA speech providers to LiveKit's media contracts."""
from uuid import uuid4
from livekit import rtc
from livekit.agents import stt, tts, tokenize
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from eva.adapters.connectors.home_assistant_voice import NoSpeechRecognized

class InternalDigitalSilence:
    """Cap only internal exact-zero PCM runs at 450 ms; preserve every other sample.

    The observed provider inserts >650 ms of digital zero between sentences.
    450 ms retains a sentence pause without accelerating speech or treating soft
    consonants as silence. Leading/trailing silence and chunk boundaries survive.
    """
    def __init__(self, sample_rate=24000, *, max_silence_ms: int = 450):
        self.limit = sample_rate * max_silence_ms // 1000
        self.seen_signal = False
        self.zeros = 0
        self.pending = 0
        self.removed_samples = 0

    def push(self, data):
        from array import array
        if len(data) % 2:
            raise ValueError("Speech PCM must contain whole int16 samples")
        samples = array('h'); samples.frombytes(data)
        output = array('h')
        for value in samples:
            if value or not self.seen_signal:
                if value:
                    self.seen_signal = True
                    self.removed_samples += self.pending
                    self.pending = self.zeros = 0
                output.append(value)
            else:
                self.zeros += 1
                if self.zeros <= self.limit:
                    output.append(0)
                else:
                    self.pending += 1
        return output.tobytes()

    def finish(self):
        # EOF proves excess zeros were the original tail, not an internal pause.
        # Yield bounded frames; cancellation must never flush this tail.
        while self.pending:
            count = min(self.pending, 480)
            self.pending -= count
            yield bytes(count * 2)


class HomeAssistantSTT(stt.STT):
    def __init__(self, source, check=lambda: None, *, vad=None):
        super().__init__(capabilities=stt.STTCapabilities(streaming=vad is not None, interim_results=False))
        self.source, self.check = source, check
        self._vad = vad

    def stream(self, *, language=None, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        if self._vad is None:
            return super().stream(language=language, conn_options=conn_options)
        from eva.voice_service.streaming_input import HomeAssistantInputStream
        return HomeAssistantInputStream(self, self._vad)

    async def _recognize_impl(self, buffer, *, language=None, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        self.check()
        frames = buffer if isinstance(buffer, list) else [buffer]
        resampler = rtc.AudioResampler(frames[0].sample_rate, 16000, num_channels=1)
        async def chunks():
            for frame in frames:
                for output in resampler.push(frame):
                    data = bytes(output.data)
                    for offset in range(0, len(data), 3200):
                        self.check()
                        yield data[offset:offset + 3200]
            for output in resampler.flush():
                data = bytes(output.data)
                for offset in range(0, len(data), 3200):
                    yield data[offset:offset + 3200]
        try:
            text = await self.source.transcribe(chunks())
        except NoSpeechRecognized:
            # A VAD segment may contain noise rather than speech. LiveKit
            # discards an empty recognition and keeps the same input stream.
            text = ""
        self.check()
        return stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language="nl", text=text)])

def clean_text_for_tts(text: str) -> str:
    """Strip markdown syntax and raw URLs before neural synthesis while keeping plain words."""
    import re
    cleaned = re.sub(r"```[\s\S]*?```", " ", text)
    cleaned = re.sub(r"\s*#+\s*(?:Bronnen|Sources)\b[\s\S]*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*https?://[^\s)]+\s*\)", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*(\S(?:[^*]*?\S)?)\*", r"\1", cleaned)
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([.,!?;:])", r"\1", cleaned)
    return cleaned or text


class HomeAssistantTTS(tts.TTS):
    def __init__(self, source, check=lambda: None):
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False), sample_rate=24000, num_channels=1)
        self.source, self.check = source, check

    def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        self.check()
        return HAChunkedStream(tts=self, input_text=clean_text_for_tts(text), conn_options=conn_options)


class TempoTTS(tts.TTS):
    """Apply pitch-preserving tempo using the installed FFmpeg audio filter."""

    def __init__(self, source, *, rate: float = 1.0, trim_silence: bool = False, max_silence_ms: int = 280):
        if source.num_channels != 1:
            raise ValueError("speech tempo requires a mono source")
        if not 0.8 <= rate <= 1.3:
            raise ValueError("speech tempo is outside the supported range")
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False),
                         sample_rate=source.sample_rate, num_channels=source.num_channels)
        self.source, self.rate = source, rate
        self.trim_silence = trim_silence
        self.max_silence_ms = max_silence_ms

    def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        return TempoChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self):
        await self.source.aclose()


class TempoChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter):
        import av
        from fractions import Fraction

        sample_rate = self._tts.sample_rate
        silence = (
            InternalDigitalSilence(sample_rate=sample_rate, max_silence_ms=self._tts.max_silence_ms)
            if self._tts.trim_silence
            else None
        )
        graph = av.filter.Graph()
        inlet = graph.add("abuffer", sample_rate=str(sample_rate),
                          sample_fmt="s16", channel_layout="mono",
                          time_base=f"1/{sample_rate}")
        tempo = graph.add("atempo", str(self._tts.rate))
        format_node = graph.add("aformat", "sample_fmts=s16:channel_layouts=mono")
        outlet = graph.add("abuffersink")
        inlet.link_to(tempo)
        tempo.link_to(format_node)
        format_node.link_to(outlet)
        graph.configure()

        output_emitter.initialize(request_id=str(uuid4()), sample_rate=sample_rate,
                                  num_channels=1, mime_type="audio/pcm",
                                  frame_size_ms=20)
        def drain():
            while True:
                try:
                    frame = graph.pull()
                except (av.error.BlockingIOError, av.error.EOFError):
                    return
                # Filter planes can include aligned padding beyond actual PCM.
                output_emitter.push(bytes(frame.planes[0])[:frame.samples * 2])

        position = 0
        def push_pcm(pcm_bytes: bytes):
            nonlocal position
            if not pcm_bytes:
                return
            sample_count = len(pcm_bytes) // 2
            if sample_count <= 0:
                return
            frame = av.AudioFrame(format="s16", layout="mono", samples=sample_count)
            frame.planes[0].update(pcm_bytes)
            frame.sample_rate = sample_rate
            frame.time_base = Fraction(1, sample_rate)
            frame.pts = position
            position += sample_count
            graph.push(frame)
            drain()

        # Closing/cancelling this stream also closes the provider and decoder.
        async with self._tts.source.synthesize(
            self.input_text, conn_options=self._conn_options
        ) as source:
            async for event in source:
                audio = event.frame
                if audio.sample_rate != sample_rate or audio.num_channels != 1:
                    raise ValueError("speech tempo requires matching mono PCM")
                raw_pcm = bytes(audio.data)
                push_pcm(silence.push(raw_pcm) if silence is not None else raw_pcm)
        if silence is not None:
            for tail_pcm in silence.finish():
                push_pcm(tail_pcm)
        graph.push(None)
        drain()
        output_emitter.flush()

class _ClosingStreamAdapter(tts.StreamAdapter):
    """Ensure `aclose()` cascades to `_wrapped_tts` so Realtime sessions never leak."""

    async def aclose(self) -> None:
        try:
            await super().aclose()
        finally:
            await self._wrapped_tts.aclose()


def streaming_speech_output(
    source,
    check=lambda: None,
    *,
    tempo: float = 1.0,
    trim_silence: bool = False,
    realtime_config: dict | None = None,
    backend=None,
):
    """Stream explicit complete sentences; use pre-warmed Realtime human voice when available."""
    from pathlib import Path

    base_tts = HomeAssistantTTS(source, check)
    effective_tts = (
        TempoTTS(base_tts, rate=tempo, trim_silence=trim_silence)
        if (abs(tempo - 1.0) > 1e-3 or trim_silence)
        else base_tts
    )
    use_realtime = False
    if isinstance(realtime_config, dict) and realtime_config.get("realtime_tts_enabled", True) is not False:
        exe = realtime_config.get("codex_executable")
        auth = realtime_config.get("codex_auth_file")
        if (
            backend is not None
            and isinstance(exe, str)
            and exe
            and isinstance(auth, str)
            and auth
            and Path(exe).is_file()
            and Path(auth).is_file()
        ):
            use_realtime = True
    if use_realtime and isinstance(realtime_config, dict):
        from eva.voice_service.realtime_tts import CodexRealtimeTTS

        rt_tts = CodexRealtimeTTS(
            fallback_tts=effective_tts,
            codex_executable=realtime_config.get("codex_executable"),
            codex_auth_file=realtime_config.get("codex_auth_file"),
            backend=backend,
            check=check,
            voice=str(realtime_config.get("realtime_voice") or "juniper"),
            ready_wait_seconds=float(realtime_config.get("realtime_tts_ready_wait", 2.4)),
        )
        rt_tts.prewarm()
        return _ClosingStreamAdapter(
            tts=rt_tts,
            sentence_tokenizer=tokenize.blingfire.SentenceTokenizer(
                min_sentence_len=90, stream_context_len=1, retain_format=True
            ),
        )
    return _ClosingStreamAdapter(
        tts=effective_tts,
        sentence_tokenizer=tokenize.blingfire.SentenceTokenizer(
            min_sentence_len=1, stream_context_len=1, retain_format=True),
    )

class HAChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter):
        initialized = False
        async for chunk in self._tts.source.synthesize(self.input_text):
            self._tts.check()
            if not initialized:
                output_emitter.initialize(request_id=str(uuid4()), sample_rate=24000,
                                          num_channels=1, mime_type=chunk.mime_type)
                initialized = True
            output_emitter.push(chunk.data)
        if not initialized:
            raise RuntimeError("speech provider returned no audio")
        output_emitter.flush()


async def home_assistant_ice_servers(base_url, token):
    """Fetch short-lived ICE credentials from the already configured HA Cloud."""
    import asyncio
    import aiohttp
    from urllib.parse import urlsplit, urlunsplit
    origin=urlsplit(base_url)
    url=urlunsplit(("wss" if origin.scheme=="https" else "ws",origin.netloc,"/api/websocket","",""))
    trace=aiohttp.TraceConfig()
    async def no_redirect(*args): raise ValueError("HA ICE redirect refused")
    trace.on_request_redirect.append(no_redirect)
    async with asyncio.timeout(12), aiohttp.ClientSession(trust_env=False,trace_configs=[trace]) as client:
        async with client.ws_connect(url,max_msg_size=65536) as socket:
            if (await socket.receive_json()).get("type")!="auth_required": raise ValueError("HA ICE authentication unavailable")
            await socket.send_json({"type":"auth","access_token":token})
            if (await socket.receive_json()).get("type")!="auth_ok": raise ValueError("HA ICE authentication unavailable")
            await socket.send_json({"id":1,"type":"cloud/webrtc/ice_servers"})
            reply=await socket.receive_json()
            if reply.get("id")!=1 or reply.get("success") is not True: raise ValueError("HA Cloud relay unavailable")
    rows=reply.get("result")
    if not isinstance(rows,list) or not 1<=len(rows)<=8: raise ValueError("HA ICE servers invalid")
    result=[]
    for row in rows:
        urls=row.get("urls");urls=[urls] if isinstance(urls,str) else urls
        if not isinstance(urls,list) or not 1<=len(urls)<=8 or any(not isinstance(url,str) or len(url)>1024 or not url.startswith(("stun:","stuns:","turn:","turns:")) for url in urls):
            raise ValueError("HA ICE servers invalid")
        if any(key in row and (not isinstance(row[key],str) or len(row[key])>8192) for key in ("username","credential")):
            raise ValueError("HA ICE credential invalid")
        result.append({"urls":urls,**{key:row[key] for key in ("username","credential") if key in row}})
    return result
