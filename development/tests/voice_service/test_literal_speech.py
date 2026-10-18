"""Literal output uses one Eva script, a cancellable decoder and real sink ACKs."""
import asyncio
from types import SimpleNamespace
import pytest
from livekit import rtc
from eva.voice_service.codex_media import CodexMediaSession


def audio(value):
    import struct
    return rtc.AudioFrame(struct.pack('<480h',*([value]*480)),24000,1,480)


class TTS:
    def __init__(self):
        self.texts=[];self.streams=[];self.block=None
    def synthesize(self,text):
        self.texts.append(text)
        stream=Stream(self.block)
        self.streams.append(stream)
        return stream
    async def aclose(self):pass


class Stream:
    def __init__(self,block):self.block=block;self.closed=asyncio.Event()
    async def __aenter__(self):return self
    async def __aexit__(self,*_):self.closed.set()
    async def __aiter__(self):
        if self.block is not None:await self.block.wait()
        for value in (1000,1000,2000,2000):
            yield SimpleNamespace(frame=audio(value))


class Source:
    def __init__(self):
        self.frames=[];self.cleared=0;self.waiting=asyncio.Event()
        self.ack=asyncio.Event();self.ack.set()
    async def capture_frame(self,frame):self.frames.append(frame)
    def clear_queue(self):self.cleared+=1
    def playback_marker(self):return self.cleared
    async def confirm_playout(self,marker):
        self.waiting.set();await self.ack.wait()
        return marker==self.cleared
    async def aclose(self):pass


def renderer(check=lambda:None):
    session=CodexMediaSession({},None,check,render_only=True)
    session._literal_tts=TTS();session.source=Source();session.started.set()
    return session


@pytest.mark.asyncio
async def test_literal_start_opens_no_peer_account_or_generative_session(monkeypatch):
    from eva.voice_service import codex_media
    def forbidden(*a,**k):pytest.fail('Literal renderer must not open a model transport')
    monkeypatch.setattr(codex_media,'CodexVoiceSession',forbidden)
    monkeypatch.setattr(codex_media,'RTCPeerConnection',forbidden)
    provider=SimpleNamespace()
    session=CodexMediaSession({},None,lambda:None,render_only=True,speech_output=provider)
    session.prewarm()
    try:
        await session.start(SimpleNamespace(state=SimpleNamespace()))
        assert session.started.is_set() and session._literal_tts.source is provider
        assert session.voice is None and session.peer is None and session.microphone is None
    finally:await session.aclose()


@pytest.mark.asyncio
async def test_literal_whole_script_preserves_first_last_audio_and_waits_for_own_ack():
    session=renderer();session.source.ack.clear()
    captions=[]
    async def caption(role,text,identity):captions.append((role,text))
    session.caption=caption
    text=('Een volledig goedgekeurd antwoord met behoud van alle woorden. '*8).strip()
    try:
        handle=session.say(text)
        await asyncio.wait_for(session.source.waiting.wait(),1)
        assert session._literal_tts.texts==[text]  # No per-phrase model prompts or breaks.
        assert not handle.done() and handle.interrupted and not captions
        pcm=[v for frame in session.source.frames for v in frame.data]
        assert any(950<=v<=1050 for v in pcm[:4000])
        assert any(1950<=v<=2050 for v in pcm)
        assert all(v==0 for v in pcm[-14400:])
        assert all((f.sample_rate,f.num_channels)==(48000,1) for f in session.source.frames)
        session.source.ack.set();await asyncio.wait_for(handle.task,1)
        assert not handle.interrupted and captions==[('assistant',text)]
        assert session._literal_tts.streams[0].closed.is_set()
    finally:await session.aclose()


@pytest.mark.asyncio
async def test_cancelled_literal_fetch_closes_stream_before_next_offer_without_old_terminal():
    session=renderer();block=asyncio.Event();session._literal_tts.block=block
    try:
        old=session.say('De oude tekst.')
        while not session._literal_tts.streams:await asyncio.sleep(0)
        next_handle=session.say('Het nieuwe antwoord.')
        session._literal_tts.block=None
        old.interrupt();await asyncio.gather(old.task,return_exceptions=True)
        await asyncio.wait_for(next_handle.task,1)
        assert session._literal_tts.texts==['De oude tekst.','Het nieuwe antwoord.']
        assert session._literal_tts.streams[0].closed.is_set()
        assert old.interrupted and not next_handle.interrupted
        assert session.source.cleared==1 and not session._render_drain_needed
        assert not session._awaiting_speech_turn and not session._assistant_turns
    finally:await session.aclose()


@pytest.mark.asyncio
async def test_cancel_during_literal_playout_never_confirms_old_or_cancels_queued_independent_offer():
    session=renderer();session.source.ack.clear()
    captions=[]
    async def caption(role,text,identity):captions.append(text)
    session.caption=caption
    try:
        old=session.say('De oude tekst.')
        await asyncio.wait_for(session.source.waiting.wait(),1)
        queued=session.say('Het nieuwe antwoord.')
        old.interrupt();await asyncio.gather(old.task,return_exceptions=True)
        session.source.ack.set()
        await asyncio.wait_for(queued.task,1)
        assert old.interrupted and not queued.interrupted
        assert captions==['Het nieuwe antwoord.']
    finally:await session.aclose()


@pytest.mark.asyncio
async def test_literal_browser_queue_completion_is_not_a_physical_delivery_receipt():
    session=renderer();waited=asyncio.Event()
    class Browser:
        async def capture_frame(self,frame):pass
        async def wait_for_playout(self):waited.set()
        def clear_queue(self):pass
        async def aclose(self):pass
    session.source=Browser()
    try:
        handle=session.say('Dit is de tekst.')
        await asyncio.wait_for(handle.task,1)
        assert waited.is_set() and handle.interrupted
    finally:await session.aclose()


@pytest.mark.asyncio
async def test_literal_playout_timeout_preserves_session_and_never_claims_delivery(monkeypatch):
    session=renderer();session.source.ack.clear()
    captions=[]
    async def caption(role,text,identity):captions.append(text)
    session.caption=caption
    original=asyncio.wait_for
    async def bounded(awaitable,timeout):
        return await original(awaitable,.01 if timeout==10 else timeout)
    monkeypatch.setattr(asyncio,'wait_for',bounded)
    try:
        handle=session.say('De afspeelbevestiging is nog niet binnen.')
        await original(handle.task,1)
        assert handle.interrupted and not session.closed
        assert session.source.cleared==0 and not session.source.ack.is_set()
        assert not captions
    finally:await session.aclose()


@pytest.mark.asyncio
async def test_authority_revocation_during_literal_fetch_never_reaches_output():
    allowed=True
    def check():
        if not allowed:raise ValueError('Authority revoked')
    session=renderer(check);block=asyncio.Event();session._literal_tts.block=block
    handle=session.say('Geannuleerde tekst.')
    while not session._literal_tts.streams:await asyncio.sleep(0)
    allowed=False;block.set()
    with pytest.raises(ValueError,match='Authority revoked'):await handle.task
    assert not session.source.frames and handle.interrupted
    assert session._literal_tts.streams[0].closed.is_set()
    await session.aclose()


@pytest.mark.asyncio
async def test_literal_decoder_failure_is_not_confirmed_or_retried_by_a_model():
    session=renderer()
    class Failing(Stream):
        async def __aiter__(self):
            yield SimpleNamespace(frame=audio(1000))
            raise ValueError('Decoder failed')
    session._literal_tts.synthesize=lambda text:Failing(None)
    handle=session.say('Niet voltooid.')
    with pytest.raises(ValueError,match='Decoder failed'):await handle.task
    assert handle.interrupted and session.voice is None
    await session.aclose()


@pytest.mark.asyncio
async def test_existing_ha_decoder_renders_encoded_audio_without_network_or_model():
    import io,wave
    from eva.application.voice import SpeechAudioChunk
    from eva.voice_service.media import HomeAssistantTTS
    encoded=io.BytesIO()
    with wave.open(encoded,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000)
        wav.writeframes(bytes(audio(1000).data)*10+bytes(audio(2000).data)*10)
    submitted=[]
    class Provider:
        async def synthesize(self,text):
            submitted.append(text)
            data=encoded.getvalue()
            for offset in range(0,len(data),4096):
                yield SpeechAudioChunk(data[offset:offset+4096],'audio/wav')
    session=renderer();session._literal_tts=HomeAssistantTTS(Provider(),session.current)
    try:
        handle=session.say('Uitsluitend deze goedgekeurde tekst.')
        await asyncio.wait_for(handle.task,5)
        assert submitted==['Uitsluitend deze goedgekeurde tekst.']
        assert not handle.interrupted and session.source.frames
        pcm=[v for frame in session.source.frames for v in frame.data]
        assert any(950<=v<=1050 for v in pcm[:15000])
        assert any(1950<=v<=2050 for v in pcm[15000:])
    finally:await session.aclose()


@pytest.mark.asyncio
async def test_cancelling_existing_ha_decoder_closes_its_waiting_provider():
    from eva.voice_service.media import HomeAssistantTTS
    from eva.application.voice import SpeechAudioChunk
    entered=asyncio.Event();closed=asyncio.Event()
    class Provider:
        async def synthesize(self,text):
            try:
                entered.set();await asyncio.Event().wait()
                yield SpeechAudioChunk(b'','audio/wav')
            finally:closed.set()
    session=renderer();session._literal_tts=HomeAssistantTTS(Provider(),session.current)
    try:
        handle=session.say('Een onderbroken download.')
        await asyncio.wait_for(entered.wait(),1)
        handle.interrupt();await asyncio.wait_for(asyncio.gather(handle.task,return_exceptions=True),1)
        assert closed.is_set() and not session.source.frames and handle.interrupted
    finally:await session.aclose()


@pytest.mark.parametrize('text', [
    'Dit is één zin met aanhalingstekens: “Éva zegt nee.”\n  '*100,
    'volledig woord zonder een zinsgrens '*100,
    'Korte gewone tekst. '*20,
    'x'*2000,
])
def test_literal_provider_sized_parts_preserve_every_character_and_whole_words(text):
    from eva.voice_service.literal_speech import literal_parts
    parts=literal_parts(text)
    assert ''.join(parts)==text
    assert all(0<len(part)<=2000 for part in parts)
    assert all(part[-1].isspace() for part in parts[:-1])
    if len(text)<=2000:assert parts==(text,)


@pytest.mark.asyncio
async def test_long_literal_text_offers_and_captions_only_completed_provider_sized_parts():
    from eva.voice_service.literal_speech import literal_parts
    session=renderer();session.source.ack.clear();captions=[]
    async def caption(role,text,identity):captions.append(text)
    session.caption=caption
    text='Dit is precies de goedgekeurde zin, inclusief spaties en leestekens.  '*70
    parts=literal_parts(text)
    try:
        handle=session.say(text)
        await asyncio.wait_for(session.source.waiting.wait(),1)
        assert session._literal_tts.texts==[parts[0]] and not captions
        session.source.ack.set();await asyncio.wait_for(handle.task,1)
        assert session._literal_tts.texts==list(parts) and captions==list(parts)
        assert ''.join(captions)==text and not handle.interrupted
    finally:await session.aclose()


@pytest.mark.asyncio
async def test_cancel_before_chunk_ack_never_offers_or_captions_unspoken_remainder():
    from eva.voice_service.literal_speech import literal_parts
    session=renderer();session.source.ack.clear();captions=[]
    async def caption(role,text,identity):captions.append(text)
    session.caption=caption
    text='Een ongewijzigd deel van een lang goedgekeurd antwoord. '*90
    try:
        handle=session.say(text)
        await asyncio.wait_for(session.source.waiting.wait(),1)
        handle.interrupt();await asyncio.gather(handle.task,return_exceptions=True)
        assert session._literal_tts.texts==[literal_parts(text)[0]]
        assert handle.interrupted and not captions and session.source.cleared==1
    finally:await session.aclose()


@pytest.mark.asyncio
async def test_oversized_late_token_is_rejected_before_any_partial_output():
    session=renderer()
    text='Een geldige korte zin. '*100+'x'*2001
    try:
        handle=session.say(text)
        with pytest.raises(ValueError,match='safe word boundary'):await handle.task
        assert not session._literal_tts.texts and not session.source.frames
        assert handle.interrupted
    finally:await session.aclose()
