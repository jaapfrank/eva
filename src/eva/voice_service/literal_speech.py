"""Render approved Eva text through the already configured HA TTS adapter."""
import asyncio
import re
from pathlib import Path
from uuid import uuid4
from livekit import rtc
from eva.adapters.connectors.home_assistant_voice import HomeAssistantSpeechOutput
from eva.voice_service.media import HomeAssistantTTS, InternalDigitalSilence


def tts_punctuation(text):
    """Give explicit ellipses sentence boundaries on the configured HA route.

    Fenna can merge ellipsis-separated phrases without audible pauses. Its HA
    text interface does not expose SSML breaks. Replace only the Unicode
    ellipsis, preserving every word, all other punctuation and the input bound.
    Captions and the approved core text retain their original punctuation.
    """
    return text.replace("\u2026", ".")


def literal_parts(text):
    """Fit the cloud TTS's 2048-character input without discarding any text.

    The current Nabu Casa process_tts implementation slices text[:2048]. Keep
    each request below that limit and validate all boundaries before speaking.
    A single oversized token cannot be split safely and is rejected upfront.
    """
    if not isinstance(text,str) or not text.strip() or len(text)>16384:
        raise ValueError('Literal speech text exceeds its bound')
    parts=[]
    while len(text)>2000:
        head=text[:2000]
        endings=list(re.finditer(r"[.!?][\"'’”)]*\s+",head))
        if not endings:endings=list(re.finditer(r"\s+",head))
        if not endings:raise ValueError('Literal speech has no safe word boundary')
        boundary=endings[-1].end()
        if not text[:boundary].strip():
            raise ValueError('Literal speech has no safe word boundary')
        parts.append(text[:boundary]);text=text[boundary:]
    if text:
        if not text.strip():
            if not parts or len(parts[-1])+len(text)>2000:
                raise ValueError('Literal speech has no safe word boundary')
            parts[-1]+=text
        else:parts.append(text)
    return tuple(parts)


def start_literal_renderer(session):
    session.current()
    source=session.speech_output
    if source is None:
        config=session.config
        source=HomeAssistantSpeechOutput(base_url=config['ha_url'],
            token=Path(config['ha_token_file']).read_text().strip(),
            pipeline_id=config['pipeline_id'],tts_engine=config['tts_engine'],
            tts_language=config['tts_language'],tts_voice=config.get('tts_voice'),
            audio_chunk_bytes=4096)
    session._literal_tts=HomeAssistantTTS(source,session.current)
    session.started.set()
    session.emit('agent_state_changed',new_state='listening')
    session.diagnostic('literal_renderer_ready')


async def speak_literal(session, text, handle):
    """Own one cancellable decoder stream and the existing acknowledged sink.

    This is text-to-speech, not a second conversational model. Cancellation
    closes the existing HA producer/decoder; a replacement never waits for a
    generative turn to finish. The caller serializes offers and owns STOP.
    """
    confirmed=True
    offered=False
    async def part(value):
        nonlocal confirmed,offered
        session.current()
        if not isinstance(value,str) or not value.strip() or len(value)>2000:
            raise ValueError('Literal speech text exceeds its bound')
        offered=True
        marker=(session.source.playback_marker()
                if hasattr(session.source,'playback_marker') else None)
        utterance_token=(session.source.begin_utterance()
                         if hasattr(session.source,"begin_utterance") else None)
        resampler=rtc.AudioResampler(24000,48000,num_channels=1)
        cadence=InternalDigitalSilence()
        session._speaking_handle=handle
        session.output_suppressed=False
        audible=False
        frames=0
        session.diagnostic('literal_speech_requested',words=len(value.split()))
        async def deliver(frame):
            nonlocal audible,frames
            session.current()
            if frame.sample_rate!=48000 or frame.num_channels!=1:
                raise ValueError('Literal speech output format differs')
            if not frames:
                session.emit('agent_state_changed',new_state='speaking')
                session.diagnostic('literal_speech_first_audio')
            frames+=1
            audible=audible or any(abs(sample)>80 for sample in frame.data)
            await session.source.capture_frame(frame)
            session.current()
        async with session._literal_tts.synthesize(tts_punctuation(value)) as stream:
            async for event in stream:
                session.current()
                frame=event.frame
                if frame.sample_rate!=24000 or frame.num_channels!=1:
                    raise ValueError('Literal TTS decoder format differs')
                data=cadence.push(bytes(frame.data))
                if data:
                    for output in resampler.push(rtc.AudioFrame(data,24000,1,len(data)//2)):
                        await deliver(output)
        for data in cadence.finish():
            session.current()
            for output in resampler.push(rtc.AudioFrame(data,24000,1,len(data)//2)):
                await deliver(output)
        for output in resampler.flush():await deliver(output)
        if not audible:raise ValueError('Literal TTS returned no audible speech')
        # Finish the same native source's 300 ms quiet-tail/FINISHED path. This
        # is submitted PCM, not an uninterruptible sleep or a guessed receipt.
        for _ in range(16):
            session.current()
            await session.source.capture_frame(rtc.AudioFrame(bytes(1920),48000,1,960))
        session.current()
        if utterance_token is not None:
            session.source.finish_utterance(utterance_token)
        played=False
        output_finished=False
        try:
            if marker is not None:
                played=await asyncio.wait_for(session.source.confirm_playout(marker),10)
                output_finished=played is True
            elif hasattr(session.source,'wait_for_playout'):
                await asyncio.wait_for(session.source.wait_for_playout(),10)
                output_finished=True
        except TimeoutError:
            # Preserve the existing unconfirmed-delivery semantics. A bounded
            # wait is not a failed connection or proof of physical silence;
            # the native sink still owns its FINISHED/STOPPED barrier.
            session.diagnostic('literal_playout_unconfirmed',reason='timeout')
        session.current()
        confirmed=confirmed and played is True
        if output_finished:await session.caption('assistant',value,str(uuid4()))
        session.diagnostic('literal_speech_completed',frames=frames,
                           internal_zero_samples_removed=cadence.removed_samples,
                           device_playout_confirmed=played is True)
        session.emit('agent_state_changed',new_state='listening')
    try:
        if isinstance(text,str):
            for value in literal_parts(text):await part(value)
        else:
            async for value in text:
                for chunk in literal_parts(value):await part(chunk)
        return offered and confirmed
    finally:
        session._speaking_handle=None
        if not session.closed:session.emit('agent_state_changed',new_state='listening')
