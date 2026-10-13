"""Adapt the already permitted HA speech providers to LiveKit's media contracts."""
from uuid import uuid4
from livekit import rtc
from livekit.agents import stt, tts, tokenize
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from eva.adapters.connectors.home_assistant_voice import NoSpeechRecognized

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

class HomeAssistantTTS(tts.TTS):
    def __init__(self, source, check=lambda: None):
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False), sample_rate=24000, num_channels=1)
        self.source, self.check = source, check

    def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        self.check()
        return HAChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class TempoTTS(tts.TTS):
    """Apply pitch-preserving tempo using the installed FFmpeg audio filter."""

    def __init__(self, source, *, rate: float = 1.0):
        if source.num_channels != 1:
            raise ValueError("speech tempo requires a mono source")
        if not 0.8 <= rate <= 1.3:
            raise ValueError("speech tempo is outside the supported range")
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False),
                         sample_rate=source.sample_rate, num_channels=source.num_channels)
        self.source, self.rate = source, rate

    def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        return TempoChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self):
        await self.source.aclose()


class TempoChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter):
        import av
        from fractions import Fraction

        sample_rate = self._tts.sample_rate
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
        # Closing/cancelling this stream also closes the provider and decoder.
        async with self._tts.source.synthesize(
            self.input_text, conn_options=self._conn_options
        ) as source:
            async for event in source:
                audio = event.frame
                if audio.sample_rate != sample_rate or audio.num_channels != 1:
                    raise ValueError("speech tempo requires matching mono PCM")
                frame = av.AudioFrame(format="s16", layout="mono",
                                      samples=audio.samples_per_channel)
                frame.planes[0].update(bytes(audio.data))
                frame.sample_rate = sample_rate
                frame.time_base = Fraction(1, sample_rate)
                frame.pts = position
                position += audio.samples_per_channel
                graph.push(frame)
                drain()
        graph.push(None)
        drain()
        output_emitter.flush()

def streaming_speech_output(source, check=lambda: None):
    """Stream explicit complete first sentences without the default 20-char wait."""
    return tts.StreamAdapter(
        tts=HomeAssistantTTS(source, check),
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
