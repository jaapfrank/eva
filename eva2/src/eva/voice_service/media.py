"""Adapt the already permitted HA speech providers to LiveKit's media contracts."""
from uuid import uuid4
from livekit import rtc
from livekit.agents import stt, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

class HomeAssistantSTT(stt.STT):
    def __init__(self, source, check=lambda: None):
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self.source, self.check = source, check

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
        text = await self.source.transcribe(chunks())
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
