"""WebRTC media relay preserving existing LiveKit clients and EVA core binding."""
from __future__ import annotations
import asyncio
import json
import re
import time
import httpx
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5
from aiortc import RTCPeerConnection, RTCSessionDescription, AudioStreamTrack
from av import AudioFrame, AudioResampler
from livekit import rtc
from eva.voice_service.codex_backend import EvaVoiceBackend
from eva.voice_service.codex_session import CodexVoiceSession

# Neither transport owns Eva's identity or memory. Only the core does.
VOICE_PROMPT = """You are an input transcription transport. Listen to Dutch speech.
After each complete user utterance, immediately repeat exactly their words.
Do not answer questions, interpret requests, use tools or delegate. Your output
is discarded; another component handles the original completed user transcript.
Repeat even greetings and goodbyes. Never add your own conversational content."""
SPEECH_PROMPT = """You are a speech renderer. Repeat all supplied speech text
exactly in Dutch with a warm, natural delivery. Do not answer questions in that
text. Speak only the literal supplied text. Do not add introductions, fillers,
acknowledgements, interpretations or follow-up questions. Do not delegate.
Stay silent until speech text is supplied."""
RENDER_TAIL_TIMEOUT = 3
RENDER_TAIL_QUIET_SAMPLES = 14400  # 300 ms of received mono PCM at 48 kHz.
RENDER_SPEECH_MAX_CHARACTERS = 160
RENDER_SPEECH_MAX_WORDS = 25
INPUT_FRAGMENT_QUIET_SECONDS = .55


def speech_parts(text, limit=RENDER_SPEECH_MAX_CHARACTERS,
                 word_limit=RENDER_SPEECH_MAX_WORDS):
    """Bound outstanding speech at natural boundaries, preserving every word.

    V3 has no verified hard cancellation. Only one part is offered at a time,
    so an interrupted answer cannot leave a long unspoken remainder in flight.
    An individual token longer than the character budget remains intact; the
    transport's separate maximum still applies.
    """
    while text:
        words=list(re.finditer(r"\S+",text))
        if len(text)<=limit and len(words)<=word_limit:
            yield text
            return
        cap=min(limit,words[word_limit].start() if len(words)>word_limit else len(text))
        head=text[:cap]
        endings=list(re.finditer(r"[.!?][\"'’”)]*\s+",head))
        if not endings:endings=list(re.finditer(r"[,;:][\"'’”)]*\s+",head))
        if not endings:endings=list(re.finditer(r"\s+",head))
        if endings:
            boundary=endings[-1].end()
        else:
            # No word boundary fits: preserve the entire exceptional token.
            ending=re.search(r"\s+",text)
            boundary=ending.end() if ending else len(text)
        yield text[:boundary]
        text=text[boundary:]


def access_lease(path):
    """Read the same protected, rotating access lease already used by EVA."""
    import os
    import stat
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 1000 or info.st_gid != 65532 or stat.S_IMODE(info.st_mode) != 0o640:
            raise ValueError('EVA model access lease permissions differ')
        with os.fdopen(descriptor, 'r', closefd=False) as stream:
            value = json.loads(stream.read(16385))
    finally:
        os.close(descriptor)
    if set(value) != {'contract','access_token','account_id','source_sha256'} or value['contract'] != 'EvaModelAccessLease/v1':
        raise ValueError('EVA model access lease contract differs')
    import re
    if not isinstance(value['source_sha256'],str) or not re.fullmatch(r'[0-9a-f]{64}',value['source_sha256']):
        raise ValueError('EVA model access lease source binding differs')
    if not all(isinstance(value[k],str) and value[k] for k in ('access_token','account_id')):
        raise ValueError('EVA model access lease unavailable')
    return value['access_token'], value['account_id']


class MicrophoneTrack(AudioStreamTrack):
    def __init__(self, on_overrun=None, input_suppressed=None):
        super().__init__()
        # Native capture starts immediately after the wakeword, before ICE and
        # account negotiation. Retain at most 45 seconds (~4.3 MB) during setup.
        self.frames = asyncio.Queue(maxsize=2250)
        self.catching_up = True
        self.started = None
        self.samples = 0
        self.enabled = False
        self.dropped_frames = 0
        self.on_overrun = on_overrun
        self._last_overrun_report = None
        self.input_suppressed=input_suppressed or (lambda:False)
        self.echo_frames_discarded=0

    async def recv(self):
        if self.started is None:
            self.started = time.monotonic()
        await asyncio.sleep(max(0,self.started + self.samples/48000 - time.monotonic()))
        data = None
        if self.enabled:
            if self.catching_up:
                import struct
                # Preserve speech captured during connection setup. Catch up only
                # across quiet frames, never by discarding the opening utterance.
                while self.frames.qsize()>3:
                    values=struct.unpack('<960h',self.frames._queue[0])
                    if max(abs(v) for v in values)>40:break
                    self.frames.get_nowait()
                if self.frames.qsize()<=3:self.catching_up=False
            try:data = self.frames.get_nowait()
            except asyncio.QueueEmpty:pass
        frame = AudioFrame(format='s16',layout='mono',samples=960)
        frame.planes[0].update(data if data is not None else bytes(1920))
        frame.sample_rate=48000
        from fractions import Fraction
        frame.time_base=Fraction(1,48000);frame.pts=self.samples;self.samples+=960
        return frame

    def push(self, frame):
        data=bytes(frame.data)
        if frame.sample_rate!=48000 or frame.num_channels!=1 or len(data)!=1920:
            raise ValueError('Microphone format differs')
        if self.input_suppressed():
            # Keep draining the physical capture, but never queue its playback
            # echo for later recognition. Already captured opening words remain.
            self.echo_frames_discarded+=1
            return
        # A short capture/transport scheduling burst must not terminate the
        # conversation. Bound live latency by dropping the oldest queued frame,
        # keeping the newest microphone audio. Startup speech remains buffered.
        limit = 10 if self.enabled and not self.catching_up else self.frames.maxsize
        dropped = False
        while self.frames.qsize() >= limit:
            self.frames.get_nowait()
            self.dropped_frames += 1
            dropped = True
        self.frames.put_nowait(data)
        now = time.monotonic()
        if dropped and self.on_overrun is not None and (
                self._last_overrun_report is None or now-self._last_overrun_report >= 1):
            self._last_overrun_report = now
            self.on_overrun(self.dropped_frames, self.frames.qsize())


class OfferedSpeech:
    def __init__(self, on_interrupt=lambda:None):
        self.task=None
        self.on_interrupt=on_interrupt
        # Offering context does not prove physical playback. Never claim otherwise.
        self.interrupted=True
        self.chat_items=[]
    def done(self):return self.task is not None and self.task.done()
    def interrupt(self, *, notify_provider=True):
        if self.task and not self.task.done():
            try:
                if notify_provider:self.on_interrupt()
            finally:self.task.cancel()


class AudioInputPort:
    def __init__(self,session):self.session=session;self._audio=None;self.capture=None
    @property
    def audio(self):return self._audio
    @audio.setter
    def audio(self,value):
        self._audio=value;self.restart()
    def restart(self):
        if self.capture:self.capture.cancel();self.capture=None
        if self._audio is not None and self.session.microphone is not None and self.session.capture_ready:
            self.capture=self.session.task(self.session.capture_native(self._audio))


class CodexMediaSession:
    def __init__(self, config, state, check, *, render_only=False, speech_output=None):
        self.config,self.state,self.check=config,state,check
        self.render_only=render_only
        self.speech_output=speech_output
        self._literal_tts=None
        self.renderer=None
        self._turns={}
        self._input_generations={}
        self._input_fragments=[]
        self._input_empty_generation=0
        self._input_open=set()
        self._transport_input_turns={}
        self._failed_input_turns=set()
        self._input_changed=asyncio.Event()
        self._input_flush=None
        self._generation=0
        # Provisional microphone input interrupts playback immediately. Only
        # actual words or explicit STOP revoke an answer not yet spoken.
        self._response_floor=0
        self._core_lock=asyncio.Lock()
        self._core_pending=0
        self.handlers={};self.tasks=set();self.closed=False
        self.input=AudioInputPort(self)
        self.output=SimpleNamespace(audio=None)
        self.speech_handles=set()
        self.output_turn_done=asyncio.Event()
        self._speech_lock=asyncio.Lock()
        self._speech_waiter=None
        self._speaking_handle=None
        self._speech_turn=None
        self._speech_text=None
        self._awaiting_speech_turn=False
        self._pending_speech_retired=False
        self._assistant_turns=set()
        self._assistant_turn_revision=0
        self._ended_assistant_turns={}
        self._assistant_idle=asyncio.Event();self._assistant_idle.set()
        self._render_drain_needed=False
        self._render_quiet_samples=0
        self._render_audio_changed=asyncio.Event()
        self.output_suppressed=render_only
        self.last_activity=time.monotonic()
        self.peer=None;self.voice=None;self.bridge=None;self.room=None
        self.prepared=None;self.channel=None
        self.capture_ready=False
        self.source=None;self.microphone=None;self.owner_track=None
        self.started=asyncio.Event();self._closing=None
        self._heard_ids={}
        self.opened_at=time.monotonic()
        self.audio_received=0;self.audio_suppressed=0
        self.suppressed_signal_frames=0
        self.last_suppressed_signal_at=None
        self.output_gate_opened_at=None
        self.turn_first_audio=False
        self.first_audio=False
        self.greeting=None
        self._farewell_task=None
        self._farewell_done=None
        self._farewell_turn=None
        self._user_playback_marker=None
        self._user_assistant_turn=None
        self._user_assistant_done=False

    def diagnostic(self, stage, **fields):
        print(json.dumps({'event':'eva_voice_media',
            'session':getattr(self.state,'session_id',None), 'stage':stage,
            'elapsed_ms':round((time.monotonic()-self.opened_at)*1000), **fields}),flush=True)

    def transport_diagnostic(self, stage):
        # Only protocol state enums: never SDP, addresses, tokens or provider text.
        allowed={'new','checking','connecting','connected','completed',
                 'disconnected','failed','closed','stable','have-local-offer',
                 'have-remote-offer','have-local-pranswer','have-remote-pranswer'}
        fields={}
        for name in ('connectionState','iceConnectionState','signalingState'):
            value=getattr(self.peer,name,None)
            fields[name]=value if isinstance(value,str) and value in allowed else 'unknown'
        self.diagnostic(stage,**fields)

    def send_speech_context(self, event):
        """Send the pinned renderer protocol on its existing ordered channel."""
        self.current()
        if (not self.render_only or not self.started.is_set() or self.channel is None
                or self.channel.readyState!='open'):
            raise ValueError('Renderer context transport is not ready')
        self.channel.send(json.dumps(event,ensure_ascii=False))

    def on(self, name, callback=None):
        def register(fn):self.handlers.setdefault(name,[]).append(fn);return fn
        return register(callback) if callback else register

    def emit(self,name,**fields):
        for callback in self.handlers.get(name,[]):callback(SimpleNamespace(**fields))

    def current(self):
        if self.closed:raise ValueError('Voice media closed')
        self.check()

    def task(self, awaitable):
        task=asyncio.create_task(awaitable);self.tasks.add(task)
        def finished(t):
            self.tasks.discard(t)
            if not t.cancelled() and t.exception() is not None and not self.closed:
                self.transport_diagnostic('task_transport_failure')
                self.diagnostic('task_failed', task=t.get_coro().__qualname__, error_type=type(t.exception()).__name__)
                asyncio.create_task(self.aclose())
        task.add_done_callback(finished)
        return task

    def prewarm(self):
        """Gather network candidates while waiting for a local wakeword.

        This opens no model session and reads no microphone or account lease.
        """
        if self.render_only:return  # Literal TTS has no model account or peer.
        if self.peer is not None:return
        if not self.render_only and self.renderer is None:
            self.renderer=CodexMediaSession(self.config,self.state,self.current,
                render_only=True,speech_output=self.speech_output)
            self.renderer.prewarm()
        self.peer=RTCPeerConnection();self.microphone=MicrophoneTrack(
            lambda dropped, queued: self.diagnostic('microphone_overrun',
                dropped_frames=dropped, queued_frames=queued),
            input_suppressed=lambda: bool(getattr(self.source,'input_suppressed',False)))
        self.peer.addTrack(self.microphone)
        self.channel=self.peer.createDataChannel('oai-events')
        async def gather():
            self.current()
            await self.peer.setLocalDescription(await self.peer.createOffer())
            self.diagnostic('ice_ready')
        self.prepared=self.task(gather())

    def retire_interrupted_output(self):
        # Mute locally now, but keep the provider busy until its terminal event.
        # appendSpeech during that interval can extend the old response instead
        # of creating a fresh turn, even after a stop instruction was accepted.
        self._ended_assistant_turns.update(dict.fromkeys(self._assistant_turns,True))
        if self._awaiting_speech_turn:self._pending_speech_retired=True
        while len(self._ended_assistant_turns)>128:
            self._ended_assistant_turns.pop(next(iter(self._ended_assistant_turns)))

    def end_assistant_turn(self, identity):
        if isinstance(identity,str) and identity:
            self._ended_assistant_turns[identity]=True
            if len(self._ended_assistant_turns)>128:
                self._ended_assistant_turns.pop(next(iter(self._ended_assistant_turns)))
            self._assistant_turns.discard(identity)
        if not self._assistant_turns and not self._awaiting_speech_turn:
            self._assistant_idle.set()

    def speech_fidelity(self, transcript):
        """Check the current render's words without retaining provider content.

        This is confirmation provenance, not an acoustic verifier or a buffer:
        a mismatch cannot undo audio already played by the streaming transport.
        """
        expected=re.findall(r'\w+', (self._speech_text or '').casefold())
        state=('missing' if not isinstance(transcript,str)
               else 'over_limit' if len(transcript)>16000
               else 'present' if transcript.strip() else 'missing')
        actual=re.findall(r'\w+',transcript.casefold()) if state=='present' else None
        matched=bool(expected) and actual==expected
        self.diagnostic('render_fidelity',matched=matched,transcript_state=state,
            expected_words=len(expected),actual_words=None if actual is None else len(actual))
        return matched

    def provider_message(self, raw):
        try:
            event=json.loads(raw)
            self.current()
            kind=event.get('type')
            if not self.render_only and self.renderer is not None:
                self.input_event(event)
                return
            if kind in {'session.started','input_transcript.added','output_transcript.added','turn.created'}:
                self.last_activity=time.monotonic()
            if kind in {'session.started','turn.created','turn.done','error','session.closed','delegation.created','delegation.context.appended'}:
                self.diagnostic('provider_event', kind=kind, role=event.get('turn',{}).get('role'))
            if kind=='session.started':
                self.started.set();self.microphone.enabled=True
                self.emit('agent_state_changed',new_state='listening')
            elif kind in {'error','session.closed'}:
                self.task(self.aclose())
            elif kind=='turn.created':
                turn=event.get('turn',{})
                if turn.get('role')=='user':
                    self._user_assistant_turn=None;self._user_assistant_done=False
                    if self._farewell_task and not self._farewell_task.done():
                        self.diagnostic('farewell_cancelled_by_speech')
                        self._farewell_task.cancel()
                    self._farewell_done=None
                    self._farewell_turn=None
                    # RTP audio and turn events travel independently. A normal
                    # listening turn must not mute a reply arriving before its
                    # turn.created event. Keep the gate for actual interruptions
                    # and preserve an explicit STOP until a fresh assistant turn.
                    interrupted=(bool(self._assistant_turns)
                        or bool(self.greeting and not self.greeting.done())
                        or self._speaking_handle is not None
                        or bool(getattr(self.source,'active',False))
                        or getattr(self.source,'queued_duration',0)>0)
                    if self.greeting and not self.greeting.done():self.greeting.cancel()
                    if self.source:self.source.clear_queue()
                    self._user_playback_marker=(self.source.playback_marker()
                        if hasattr(self.source,'playback_marker') else None)
                    self.output_suppressed=self.output_suppressed or interrupted
                    self.diagnostic('user_turn_output_gate',
                        interrupted=interrupted, suppressed=self.output_suppressed)
                    self.retire_interrupted_output()
                    for handle in list(self.speech_handles):handle.interrupt(notify_provider=False)
                    self.emit('user_state_changed',new_state='speaking')
                elif turn.get('role')=='assistant':
                    identity=turn.get('id')
                    if identity in self._ended_assistant_turns:return
                    if isinstance(identity,str) and identity:
                        self._assistant_turn_revision+=1
                        if self._render_drain_needed:self._render_audio_changed.set()
                        self._assistant_turns.add(identity);self._assistant_idle.clear()
                        if self._awaiting_speech_turn:
                            self._awaiting_speech_turn=False
                            if self._pending_speech_retired:
                                # A local interruption can beat turn.created.
                                # This is still the old admitted render, never
                                # the next queued offer's response identity.
                                self._pending_speech_retired=False
                                self._ended_assistant_turns[identity]=True
                                self.diagnostic('retired_output_turn_started')
                                return
                        self._user_assistant_turn=identity;self._user_assistant_done=False
                        if self._farewell_done is not None and self._farewell_turn is None:
                            self._farewell_turn=identity
                        if self._speech_waiter is not None and self._speech_turn is None:
                            self._speech_turn=identity
                    now=time.monotonic()
                    self.diagnostic('output_gate_open',
                        suppressed_signal_frames=self.suppressed_signal_frames,
                        last_suppressed_signal_age_ms=(None if self.last_suppressed_signal_at is None
                            else round((now-self.last_suppressed_signal_at)*1000)))
                    self.suppressed_signal_frames=0;self.last_suppressed_signal_at=None
                    self.output_gate_opened_at=now;self.turn_first_audio=False
                    if not self.render_only or self._speaking_handle is not None:
                        self.output_suppressed=False
                        self.emit('agent_state_changed',new_state='speaking')
            elif kind=='turn.done':
                turn=event.get('turn',{})
                role=turn.get('role');text=turn.get('transcript','')
                if isinstance(text,str) and text and (not self.render_only or self._speaking_handle is not None):
                    self.task(self.caption(role,text,str(turn.get('id',uuid4()))))
                if role=='user':
                    if isinstance(text,str):self.user_transcript(text)
                    self.emit('user_state_changed',new_state='listening')
                    self.emit('user_input_transcribed',is_final=True)
                elif role=='assistant':
                    identity=turn.get('id')
                    self.end_assistant_turn(identity)
                    if isinstance(identity,str) and identity and identity==self._user_assistant_turn:
                        self._user_assistant_done=True
                    if self._farewell_done is not None and identity and identity==self._farewell_turn:
                        self._farewell_done.set()
                    if (self._speech_waiter is not None and not self._speech_waiter.done()
                            and identity is not None and identity==self._speech_turn):
                        self._speech_waiter.set_result(self.speech_fidelity(text))
                    self.output_turn_done.set()
                    self.emit('agent_state_changed',new_state='listening')
        except (ValueError,TypeError,AttributeError):self.task(self.aclose())

    def input_transport_turn(self, phase, identity):
        """Settle failed transcription without inventing or repeating a core turn."""
        self.current()
        if phase == 'started':
            if identity not in self._transport_input_turns:
                pending=set(self._input_open) | {row[0] for row in self._input_fragments}
                self._transport_input_turns[identity]=(pending,self._generation)
                while len(self._transport_input_turns)>128:
                    self._transport_input_turns.pop(next(iter(self._transport_input_turns)))
            return
        bound=self._transport_input_turns.pop(identity,None)
        if phase!='failed' or bound is None:return
        identities,generation=bound
        pending=self._input_open | {row[0] for row in self._input_fragments}
        failed=identities & pending
        if not failed:return  # Already admitted work and newer input are untouched.
        announce=(generation==self._generation and generation>=self._response_floor)
        # Revoke stale answers BEFORE waking response_allowed waiters.
        self._response_floor=max(self._response_floor,generation+1)
        self._generation=max(self._generation,self._response_floor)
        self._failed_input_turns.update(failed)
        self._input_open.difference_update(failed)
        self._input_fragments=[row for row in self._input_fragments if row[0] not in failed]
        self._input_empty_generation=0
        self._input_changed.set()
        self.last_activity=time.monotonic()
        self.diagnostic('input_transport_settled',retired_inputs=len(failed),
            repeat_prompt=announce,generation=self._generation,response_floor=self._response_floor)
        if announce:
            self.renderer.interrupt()
            self.task(self.input_repeat_prompt(self._generation))
        if self._input_flush and not self._input_flush.done():self._input_flush.cancel()
        if self._input_fragments and not self._input_open:
            self._input_flush=self.task(self.flush_input())

    async def input_repeat_prompt(self, generation):
        if not await self.response_allowed(generation):return
        text='Die laatste zin kwam niet door. Wil je hem nog eens zeggen?'
        handle=self.say(text)
        await asyncio.gather(handle.task,return_exceptions=True)
        if not self.closed:
            self.bridge.transcript('assistant',text,interrupted=(
                True if handle.task.cancelled() else None if handle.interrupted else False))

    def input_event(self, event):
        """The input transport has no audio route and cannot initiate work."""
        kind=event.get('type');turn=event.get('turn',{})
        if kind in {'session.started','turn.created','turn.done','error','session.closed'}:
            self.diagnostic('input_event',kind=kind,role=turn.get('role'))
        if kind=='session.started':
            self.started.set();self.microphone.enabled=True
            self.emit('agent_state_changed',new_state='listening')
        elif kind in {'error','session.closed'}:
            self.task(self.aclose())
        elif turn.get('role')=='user' and kind=='turn.created':
            identity=turn.get('id')
            if not isinstance(identity,str) or not 1<=len(identity)<=256:
                raise ValueError('Missing input turn identity')
            if identity in self._failed_input_turns:return
            if identity in self._input_generations:return
            if len(self._input_generations)>=128:raise ValueError('Voice capacity reached')
            self.last_activity=time.monotonic()
            self._generation+=1
            self._input_generations[identity]=self._generation
            self._input_open.add(identity)
            if self._input_flush and not self._input_flush.done():self._input_flush.cancel()
            if self.greeting and not self.greeting.done():self.greeting.cancel()
            self.renderer.interrupt()
            self.emit('user_state_changed',new_state='speaking')
        elif turn.get('role')=='user' and kind=='turn.done':
            identity=turn.get('id');text=turn.get('transcript')
            if isinstance(identity,str) and identity in self._failed_input_turns:return
            if not isinstance(identity,str) or not 1<=len(identity)<=256:
                raise ValueError('Missing input turn identity')
            if not isinstance(text,str):raise ValueError('Invalid input transcript')
            if len(text)>4096:raise ValueError('Input turn too long')
            if identity in self._turns:
                if self._turns[identity]!=text:raise ValueError('Conflicting input turn')
                return
            if len(self._turns)>=128 or self._core_pending>=8:
                raise ValueError('Voice capacity reached')
            self._turns[identity]=text
            self._input_open.discard(identity)
            self._input_changed.set()
            if not text.strip():
                # Settled noise may release a buffered sentence, but an explicit
                # STOP after that noise must still revoke its eventual speech.
                generation=self._input_generations.get(identity,-1)
                if self._input_fragments and generation==self._generation:
                    self._input_empty_generation=max(self._input_empty_generation,generation)
                if self._input_flush and not self._input_flush.done():self._input_flush.cancel()
                if self._input_fragments and not self._input_open:
                    self._input_flush=self.task(self.flush_input())
                return
            self._response_floor=max(self._response_floor,
                self._input_generations.get(identity,self._generation))
            self._input_fragments.append((identity,text,self._input_generations.get(identity,self._generation)))
            if sum(len(row[1]) for row in self._input_fragments)>4096:
                raise ValueError('Input utterance too long')
            self.last_activity=time.monotonic()
            self.task(self.caption('user',text,identity))
            if self._input_flush and not self._input_flush.done():self._input_flush.cancel()
            if not self._input_open:self._input_flush=self.task(self.flush_input())
            self.emit('user_state_changed',new_state='listening')
            self.emit('user_input_transcribed',is_final=True)

    async def flush_input(self):
        # The provider can finalize a clause just before recognizing its last
        # word or a continuation after a natural pause. Keep a bounded quiet
        # window before admitting work; new speech cancels this pending flush.
        await asyncio.sleep(INPUT_FRAGMENT_QUIET_SECONDS)
        self.current()
        if self._input_open:return
        fragments=sorted(self._input_fragments,key=lambda row:row[2])
        self._input_fragments=[]
        if not fragments:return
        if self._core_pending>=8:raise ValueError('Voice capacity reached')
        text=' '.join(row[1].strip() for row in fragments)
        key='voice-turn:'+json.dumps([row[0] for row in fragments],separators=(',',':'))
        self._core_pending+=1
        generation=max(fragments[-1][2],self._input_empty_generation)
        self._input_empty_generation=0
        self.task(self.core_turn(text,key,generation))

    async def response_allowed(self, generation):
        # Unsettled microphone input may still prove to be empty noise. Session
        # closure cancels this wait; STOP or actual words revoke the response.
        while self._input_open and generation>=self._response_floor:
            self._input_changed.clear()
            await self._input_changed.wait()
        self.current()
        return generation>=self._response_floor

    async def core_turn(self, text, key, generation):
        self.current()
        # Each recognized turn, including social contact, enters the same core.
        # A new utterance revokes playback, never previously accepted work.
        updates=[]
        def spoken_update(value):
            self.current()
            if generation>=self._response_floor:
                deferred=self._input_open or any(handle is None for _,handle in updates)
                updates.append((value,None if deferred else self.say(value)))
        try:
            async with self._core_lock:
                try:
                    answer=await self.bridge.dispatch(text,key,spoken_update=spoken_update)
                except (httpx.HTTPError,RuntimeError) as error:
                    self.current()
                    self.diagnostic('core_reply_failed',error_type=type(error).__name__)
                    self.bridge.notify({'type':'work_failed','message_id':str(uuid5(UUID(self.bridge.session_id),key))})
                    answer='Sorry, ik raakte de draad even kwijt. Wil je dat nog eens zeggen?'
                self.current()
                self.bridge.transcript('user',text)
        finally:
            self._core_pending=max(0,self._core_pending-1)
        # Rendering the approved first part overlaps core work. Drain it before
        # the final answer, preserving interruption and actual playback status.
        for value,update in updates:
            if update is None:
                if not await self.response_allowed(generation):continue
                update=self.say(value)
            await asyncio.gather(update.task,return_exceptions=True)
            if not self.closed:
                self.bridge.transcript('assistant',value,interrupted=(
                    True if update.task.cancelled() else None if update.interrupted else False))
        # A provisional input turn may be background noise. Wait for its final
        # transcript before discarding an answer that has never been offered.
        # A cancelled speech handle is never replayed by this path.
        if not answer:
            self.last_activity=time.monotonic()
            return  # Verified silent completion keeps listening; no empty TTS.
        if not await self.response_allowed(generation):return
        handle=self.say(answer)
        try:
            await handle.task
        except asyncio.CancelledError:
            # A renderer interruption may only cancel this offered handle. Keep
            # farewell settlement alive, without swallowing session shutdown.
            if not handle.task.cancelled() or asyncio.current_task().cancelling():raise
        finally:
            if not self.closed:
                self.bridge.transcript('assistant',answer,interrupted=(
                    True if handle.task.cancelled() else None if handle.interrupted else False))
        self.current()
        normalized=re.sub(r"[.!?,]+$", "", text.strip().casefold()).strip()
        farewell=re.fullmatch(r"(?:(?:dank\s*je(?:\s*wel)?|bedankt)(?:[,\s.!]+h[eèé])?[,\s.!]+)?(?:(?:tot\s+)?do\s*ei|tot ziens|tot later|tot morgen|bye|goodbye)(?:[, ]+eva)?",normalized)
        if farewell and await self.response_allowed(generation):
            self.diagnostic('farewell_session_close')
            await self.aclose()

    def user_transcript(self, text):
        # Session controls match a complete standalone utterance, never a word
        # inside a question, quotation, cancellation of work or longer sentence.
        normalized=re.sub(r"[.!?,]+$", "", text.strip().casefold()).strip()
        if not re.fullmatch(r"(?:(?:dank\s*je(?:\s*wel)?|bedankt)(?:[,\s.!]+h[eèé])?[,\s.!]+)?(?:(?:tot\s+)?do\s*ei|tot ziens|tot later|tot morgen|bye|goodbye)(?:[, ]+eva)?", normalized):
            return
        if self._farewell_task and not self._farewell_task.done():return
        self.diagnostic('farewell_detected')
        self._farewell_done=asyncio.Event()
        current=self._assistant_turns-self._ended_assistant_turns.keys()
        self._farewell_turn=next(iter(current)) if len(current)==1 else self._user_assistant_turn
        if self._user_assistant_done and self._farewell_turn==self._user_assistant_turn:
            self._farewell_done.set()
        # Reply audio may already precede the final user transcript. Measure
        # from user-turn start, so its early prefix counts toward device ACK.
        marker=self._user_playback_marker
        if marker is None and hasattr(self.source,'playback_marker'):
            marker=self.source.playback_marker()
        self._farewell_task=self.task(self.finish_farewell(self._farewell_done,marker))

    async def finish_farewell(self, completed, marker):
        # Give the provider its farewell turn and let the audio finish before
        # releasing the microphone. A new utterance cancels this pending close.
        try:
            await asyncio.wait_for(completed.wait(),10)
        except TimeoutError:
            self.diagnostic('farewell_response_timeout')
        else:
            try:
                # Once the short farewell turn is terminal, missing audio or a
                # missing device ACK must not leave the microphone open for 15s.
                async with asyncio.timeout(3):
                    if marker is not None:
                        confirmed=await self.source.confirm_playout(marker)
                        self.diagnostic('farewell_playout',confirmed=confirmed)
                    else:
                        await asyncio.sleep(.5)
                        await self.source.wait_for_playout()
            except TimeoutError:
                self.diagnostic('farewell_playout_timeout')
        self.diagnostic('farewell_session_close')
        await self.aclose()

    async def start(self, agent, *, room=None, room_options=None):
        self.current();self.room=room
        self.opened_at=time.monotonic()
        self.last_activity=self.opened_at
        if self.state is None:self.state=agent.state
        if self.render_only:
            from eva.voice_service.literal_speech import start_literal_renderer
            start_literal_renderer(self)
            return
        if not self.render_only:
            self.bridge=await EvaVoiceBackend(self.state.backend,self.state.session_id,self.current,
            self.state.notify,state=self.state,refresh_context=agent.refresh_saved_context).open()
            self.bridge.allow_handoffs=False
        self.voice=CodexVoiceSession(self.config['codex_executable'],
            lambda:access_lease(self.config['codex_auth_file']),self.bridge,media_only=True,
            context_sender=self.send_speech_context if self.render_only else None,
            on_input_turn=self.input_transport_turn if not self.render_only else None)
        self.prewarm()
        self.channel.on('message', self.provider_message)
        @self.peer.on('track')
        def output(track):
            self.task(self.play(track) if self.render_only else self.discard_audio(track))
        @self.peer.on('connectionstatechange')
        def connection():
            self.transport_diagnostic('connection_state')
            if self.peer.connectionState in {'failed','closed','disconnected'} and not self.closed:
                self.task(self.aclose())
        @self.peer.on('iceconnectionstatechange')
        def ice_connection():self.transport_diagnostic('ice_state')
        if self.render_only:
            pass  # Shared output, no microphone input or independent core.
        elif room is None:
            if self.output.audio is None:raise ValueError('Speaker output missing')
            from eva.voice_service.codex_speaker_audio import SpeakerSource
            self.source=SpeakerSource(self.output.audio,self.diagnostic)
            self.capture_ready=True
            self.input.restart()
            if self.config.get('wake_greeting',False):
                self.greeting=self.task(self.greet())
        else:
            self.source=rtc.AudioSource(48000,1,queue_size_ms=80)
            track=rtc.LocalAudioTrack.create_audio_track('Eva',self.source)
            self.output_publication = await room.local_participant.publish_track(track,rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
            @room.on('track_subscribed')
            def subscribed(track,publication,participant):
                if participant.identity=='owner' and track.kind==rtc.TrackKind.KIND_AUDIO:
                    if self.owner_track is not None:
                        self.task(self.aclose());return
                    self.owner_track=track
                    self.task(self.capture(track))
            async def typed(reader,participant_identity):
                if participant_identity!='owner':return
                text=await reader.read_all()
                if not isinstance(text,str) or not 1<=len(text)<=4096:return
                self.current()
                # Typed messages go directly to the same core, never a second model.
                if self._core_pending>=8:raise ValueError('Voice capacity reached')
                self._core_pending+=1
                self.interrupt()
                await self.core_turn(text,uuid4().hex,self._generation)
            room.register_text_stream_handler('lk.chat',lambda reader,participant_identity:self.task(typed(reader,participant_identity)))
        # Drain native capture before account negotiation: the provisioned wire
        # intentionally permits only two seconds of unread microphone audio.
        if not self.render_only:
            self.renderer.state=self.state
            self.renderer.bridge=self.bridge;self.renderer.source=self.source
            self.renderer.room=room
            for name in ('agent_state_changed',):
                self.renderer.on(name,lambda event,n=name:self.emit(n,**vars(event)))
            if room is not None:
                self.renderer.output_publication=self.output_publication
            self.renderer.on('close',lambda _:self.task(self.aclose()) if not self.closed else None)
            renderer_start=self.task(self.renderer.start(agent,room=room))
        self.diagnostic('account_start')
        await self.voice.__aenter__()
        self.diagnostic('account_ready')
        await self.prepared
        self.current()
        answer=await self.voice.start(self.peer.localDescription.sdp,
            prompt=SPEECH_PROMPT if self.render_only else VOICE_PROMPT,
            output_modality='audio')
        self.diagnostic('voice_answer_ready')
        await self.peer.setRemoteDescription(RTCSessionDescription(sdp=answer,type='answer'))
        await asyncio.wait_for(self.started.wait(),15)
        if not self.render_only:await renderer_start
        self.diagnostic('media_ready')
        self.task(self.watch_voice())
        if not self.render_only:self.task(self.watch_lifetime())

    async def greet(self):
        import secrets
        import wave
        paths=sorted((Path(__file__).parent/'greetings').glob('*.wav'))
        if not paths:return
        with wave.open(str(secrets.choice(paths)),'rb') as recording:
            if (recording.getframerate(),recording.getnchannels(),recording.getsampwidth()) != (48000,1,2):
                raise ValueError('Wake greeting format differs')
            self.diagnostic('wake_greeting_started')
            while data:=recording.readframes(960):
                self.current()
                await self.source.capture_frame(rtc.AudioFrame(data,48000,1,len(data)//2))
            self.output.audio.flush()
            await self.output.audio.wait_for_playout()
            self.diagnostic('wake_greeting_finished')

    async def watch_lifetime(self):
        started=time.monotonic()
        while True:
            await asyncio.sleep(1)
            self.current()
            now=time.monotonic()
            # Waiting for an admitted answer or its playback is not inactivity.
            # The absolute session limit and authority checks still apply.
            output=self.renderer if self.renderer is not None else self
            if (getattr(self.state, 'active', None)
                    or any(not handle.done() for handle in output.speech_handles)):
                self.last_activity=now
            if now-started>=1800 or now-self.last_activity>=120:
                self.diagnostic('lifetime_expired',
                    reason='session_limit' if now-started>=1800 else 'idle_timeout',
                    idle_ms=round((now-self.last_activity)*1000))
                await self.aclose();return

    async def watch_voice(self):
        while True:
            event=await self.voice.events.get()
            if event['method'] in {'thread/realtime/error','thread/realtime/closed'}:
                self.diagnostic('codex_terminal', kind=event['method'])
                self.transport_diagnostic('codex_terminal_transport')
                await self.aclose();return

    async def capture(self,track):
        stream=rtc.AudioStream(track,sample_rate=48000,num_channels=1,frame_size_ms=20)
        try:
            async for event in stream:
                self.current();self.microphone.push(event.frame)
        finally:
            await stream.aclose()

    async def discard_audio(self,track):
        # Even unexpected provider audio has no path to the user's speaker.
        while True:
            await track.recv()
            self.current()

    async def play(self,track):
        if self.greeting:
            try:await asyncio.shield(self.greeting)
            except asyncio.CancelledError:
                if self.closed or not self.greeting.cancelled():raise
        resampler=AudioResampler(format='s16',layout='mono',rate=48000)
        while True:
            frame=await track.recv();self.current()
            for pcm in resampler.resample(frame):
                self.audio_received+=1
                output=rtc.AudioFrame(bytes(pcm.planes[0])[:pcm.samples*2],48000,1,pcm.samples)
                signal=max((abs(v) for v in output.data),default=0)>80
                if self.output_suppressed:
                    self.audio_suppressed+=1
                    if self._render_drain_needed:
                        self._render_quiet_samples=(0 if signal else
                            self._render_quiet_samples+output.samples_per_channel)
                        self._render_audio_changed.set()
                    if signal:
                        self.suppressed_signal_frames+=1
                        self.last_suppressed_signal_at=time.monotonic()
                if not self.output_suppressed:
                    if signal and not self.turn_first_audio:
                        self.turn_first_audio=True
                        self.diagnostic('turn_first_output_audio',gate_to_audio_ms=(
                            None if self.output_gate_opened_at is None else
                            round((time.monotonic()-self.output_gate_opened_at)*1000)))
                    if not self.first_audio and max((abs(v) for v in output.data),default=0)>200:
                        self.first_audio=True
                        self.diagnostic('first_output_audio')
                    await self.source.capture_frame(output)

    async def caption(self,role,text,identity):
        self.current()
        if self.room is None:return
        participant='owner' if role=='user' else 'eva'
        track_id=self.owner_track.sid if role=='user' and self.owner_track is not None else self.output_publication.sid
        await self.room.local_participant.publish_transcription(rtc.Transcription(participant_identity=participant,
            track_sid=track_id,segments=[rtc.TranscriptionSegment(id=identity,text=text,start_time=0,end_time=0,final=True,language='nl')]))

    def speech_settled(self, task):
        # Give the listener its full idle window after playback or interruption,
        # including completion between watchdog ticks. Never reopen a session.
        if not self.closed:self.last_activity=time.monotonic()

    async def drain_interrupted_output(self):
        if not self._render_drain_needed:return
        # A data-channel terminal can precede the last RTP audio. Keep it muted
        # until actual consecutive silence arrives after that terminal. Earlier
        # silence, STOP admission and a missing audio stream prove nothing.
        self._render_quiet_samples=0
        self._render_audio_changed.clear()
        revision=self._assistant_turn_revision
        self.diagnostic('render_tail_drain_started')
        async with asyncio.timeout(RENDER_TAIL_TIMEOUT):
            while True:
                if (not self._assistant_idle.is_set()
                        or self._assistant_turn_revision!=revision):
                    raise ValueError('Unexpected renderer turn during output drain')
                if self._render_quiet_samples>=RENDER_TAIL_QUIET_SAMPLES:break
                self._render_audio_changed.clear()
                await self._render_audio_changed.wait()
                self.current()
        self._render_drain_needed=False
        self.diagnostic('render_tail_drain_finished')

    def say(self,text,**_):
        if self.renderer is not None:
            handle=self.renderer.say(text,**_)
            handle.task.add_done_callback(self.speech_settled)
            return handle
        def interrupted():
            # Revoking a saved result must stop its media, not just its waiter.
            # Cancelling a queued offer must not silence somebody else's answer.
            if self._speaking_handle is handle:self.stop_current_audio()
        handle=OfferedSpeech(interrupted)
        async def send():
            confirmed=True;offered=False
            async def part(value):
                nonlocal confirmed,offered
                for speech in speech_parts(value):
                    self.current();offered=True
                    await asyncio.wait_for(self._assistant_idle.wait(),30)
                    self.current()
                    await self.drain_interrupted_output()
                    marker=(self.source.playback_marker()
                        if hasattr(self.source,'playback_marker') else None)
                    self._speech_turn=None
                    self._speech_text=speech
                    self._speech_waiter=asyncio.get_running_loop().create_future()
                    try:
                        self._speaking_handle=handle
                        # RPC admission precedes turn.created and its RTP. An
                        # interruption in that gap must still fence the next
                        # append until the old provider turn actually ends.
                        self._awaiting_speech_turn=True
                        self._pending_speech_retired=False
                        self._assistant_idle.clear()
                        # Renderer receives no user audio; open before RPC so
                        # early RTP cannot clip the first words.
                        self.output_suppressed=False
                        await self.voice.say(speech)
                        faithful=await asyncio.wait_for(self._speech_waiter,
                            max(30,min(180,len(speech)/8+10)))
                        played=False
                        if marker is not None:
                            try:played=await asyncio.wait_for(self.source.confirm_playout(marker),10)
                            except TimeoutError:pass
                        confirmed=confirmed and faithful is True and played
                    finally:
                        self._speech_waiter=None;self._speech_turn=None
                        self._speech_text=None
                        self._speaking_handle=None
            # One request owns its response identity. A stale completion cannot
            # advance a new part or an independently queued saved result.
            async with self._speech_lock:
                try:
                    if self.render_only:
                        await asyncio.wait_for(self.started.wait(),30)
                        self.current()
                    if self._literal_tts is not None:
                        from eva.voice_service.literal_speech import speak_literal
                        if await speak_literal(self,text,handle):handle.interrupted=False
                        return
                    if isinstance(text,str):await part(text)
                    else:
                        async for value in text:await part(value)
                    if offered and confirmed:handle.interrupted=False
                finally:
                    self._speaking_handle=None
                    if self.render_only:self.output_suppressed=True
        handle.task=self.task(send());self.speech_handles.add(handle)
        handle.task.add_done_callback(lambda _:self.speech_handles.discard(handle))
        handle.task.add_done_callback(self.speech_settled)
        return handle

    def clear_user_turn(self):
        if self.microphone:
            while not self.microphone.frames.empty():self.microphone.frames.get_nowait()

    def stop_current_audio(self):
        if self.renderer is not None:
            self._generation+=1
            self._response_floor=self._generation
            self._input_changed.set()
            return self.renderer.stop_current_audio()
        if self.render_only and self._literal_tts is None and (self._assistant_turns or
                self._awaiting_speech_turn or self._speaking_handle is not None):
            self._render_drain_needed=True
        self.output_suppressed=True
        self.retire_interrupted_output()
        if self.source:self.source.clear_queue()
        # A V3 appendText "STOP" is speakable context, not a cancel operation.
        # Mute and retire locally, then retain the terminal/tail fence. Never
        # append control prose that can extend or contaminate the old response.

    def interrupt(self):
        if self.renderer is not None:
            self._generation+=1
            self._response_floor=self._generation
            self._input_changed.set()
            return self.renderer.interrupt()
        self.stop_current_audio()
        for handle in list(self.speech_handles):handle.interrupt(notify_provider=False)

    async def capture_native(self, source):
        resampler=AudioResampler(format='s16',layout='mono',rate=48000)
        pending=bytearray()
        async for frame in source:
            self.current()
            value=AudioFrame(format='s16',layout='mono',samples=frame.samples_per_channel)
            value.sample_rate=frame.sample_rate;value.planes[0].update(bytes(frame.data))
            for pcm in resampler.resample(value):
                pending.extend(bytes(pcm.planes[0])[:pcm.samples*2])
                while len(pending)>=1920:
                    self.microphone.push(rtc.AudioFrame(bytes(pending[:1920]),48000,1,960))
                    del pending[:1920]

    async def aclose(self):
        if self._closing is None:
            self.transport_diagnostic('closing_transport')
            self.diagnostic('closing', audio_received=self.audio_received, audio_suppressed=self.audio_suppressed)
            self.closed=True
            self.capture_ready=False
            if self.microphone:self.microphone.enabled=False;self.microphone.stop()
            if self.source:self.source.clear_queue()
            current=asyncio.current_task()
            owned=[task for task in self.tasks if task is not current]
            for task in owned:task.cancel()
            async def cleanup():
                await asyncio.gather(*owned,return_exceptions=True)
                try:
                    if self.renderer:await self.renderer.aclose()
                    if self.voice:await self.voice.close()
                    if self._literal_tts:await self._literal_tts.aclose()
                finally:
                    try:
                        if self.peer:await self.peer.close()
                    finally:
                        try:
                            if self.bridge and not self.render_only:await self.bridge.close()
                        finally:
                            if self.source and not self.render_only:await self.source.aclose()
                            self.emit('close')
            self._closing=asyncio.create_task(cleanup())
        if self._closing is not asyncio.current_task():await asyncio.shield(self._closing)
