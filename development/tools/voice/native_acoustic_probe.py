"""Explicit, short private PCM observation for a supervised native speaker test.

Importing this module does nothing. Call install(root, device_id, arm_seconds=600) only in
a temporary diagnostic service process for an agreed listening test. It observes
already-authorized capture; it never opens a microphone or changes the echo gate.
Use a fresh private directory. Remove the temporary diagnostic overlay afterward.
The microphone WAV is room audio; keep it outside Git and public evidence.
Submitted output PCM is a reference, NOT proof of physical playback or alignment.
The optional trigger="input" begins on the first unsuppressed input frame.
Whitelisted provider events/text are private, capped at 512 events of 4096 characters
within the same recording window; they are not evidence of physical acoustics.
"""
from pathlib import Path
import asyncio
import json
import os
import time
import wave
from weakref import WeakKeyDictionary


class Recording:
    def __init__(self, root, deadline, seconds, clock):
        self.root=root; self.deadline=deadline; self.seconds=seconds; self.clock=clock
        self.streams={}; self.files={}; self.samples={}; self.closed=False;self.timer=None
        self.provider_events=0
        descriptor=os.open(root/'timing.private.jsonl', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        self.events=os.fdopen(descriptor, 'w')

    def close(self):
        if self.closed:return
        self.closed=True
        if self.timer is not None:self.timer.cancel()
        for stream in self.streams.values():
            try:stream.close()
            except OSError:pass
        for file in self.files.values():
            try:file.close()
            except OSError:pass
        try:self.events.close()
        except OSError:pass

    def provider(self, event):
        if self.closed:return
        if self.clock()>=self.deadline:self.close();return
        if self.provider_events>=512 or not isinstance(event,dict):return
        kind=event.get('type')
        if kind not in {'session.started','session.closed','error','turn.created','turn.done',
                        'input_transcript.added','output_transcript.added'}:return
        value={'kind':'provider','monotonic':self.clock(),'type':kind}
        turn=event.get('turn')
        if isinstance(turn,dict):
            if turn.get('role') in {'user','assistant'}:value['role']=turn['role']
            text=turn.get('transcript')
            if isinstance(text,str):value['text']=text[:4096]
        if kind in {'input_transcript.added','output_transcript.added'}:
            text=event.get('transcript')
            item=event.get('item')
            expected='input_transcript' if kind=='input_transcript.added' else 'output_transcript'
            if isinstance(item,dict) and item.get('type')==expected:
                text=item.get('text')
            if isinstance(text,str):value['text']=text[:4096]
        try:
            self.events.write(json.dumps(value)+'\n')
            self.events.flush()
            self.provider_events+=1
        except Exception:self.close()

    def push(self, kind, frame, gated=False):
        if self.closed:return
        if self.clock()>=self.deadline:self.close();return
        try:
            rate=frame.sample_rate
            if rate not in (16000,24000,48000) or frame.num_channels!=1:
                self.close();return
            data=bytes(frame.data)
            if len(data)!=frame.samples_per_channel*2 or len(data)>rate*2:
                self.close();return
            if kind not in self.streams:
                fd=os.open(self.root/(kind+'.private.wav'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                self.files[kind]=os.fdopen(fd,'wb')
                stream=wave.open(self.files[kind],'wb')
                stream.setparams((1,2,rate,0,'NONE','not compressed'))
                self.streams[kind]=stream;self.samples[kind]=0
            stream=self.streams[kind]
            if stream.getframerate()!=rate:self.close();return
            remaining=rate*self.seconds-self.samples[kind]
            if remaining<=0:return
            data=data[:remaining*2]
            self.events.write(json.dumps({'kind':kind,'monotonic':self.clock(),
                'first_sample':self.samples[kind],'samples':len(data)//2,
                'echo_gate':bool(gated)})+'\n')
            stream.writeframesraw(data);self.samples[kind]+=len(data)//2
        except Exception:
            # A diagnostic disk/format failure must never change live media.
            self.close()


class Probe:
    def __init__(self, root, *, arm_seconds=600, seconds=30, clock=time.monotonic, trigger='answer'):
        if not 1<=arm_seconds<=600 or not 1<=seconds<=30:
            raise ValueError('Acoustic probe exceeds its time bound')
        if trigger not in {'answer','input'}:raise ValueError('Unknown acoustic probe trigger')
        self.trigger=trigger
        self.root=Path(root);self.root.mkdir(mode=0o700,parents=False,exist_ok=False)
        self.clock=clock;self.expires=clock()+arm_seconds;self.seconds=seconds
        self.recordings=WeakKeyDictionary();self.count=0

    def microphone(self, source, frame):
        if source is None:return
        gated=bool(getattr(source,'input_suppressed',False))
        recording=self.recordings.get(source)
        if recording is None:
            # Start the short acoustic window with the literal answer, not
            # the wake chime or time spent waiting for the core's response.
            if self.trigger=='answer' and getattr(source,'_utterance',None) is None:return
            if self.trigger=='input' and gated:return
            if self.clock()>=self.expires or self.count>=2:return
            self.count+=1
            root=self.root/('capture-'+str(self.count));root.mkdir(mode=0o700)
            recording=Recording(root,min(self.expires,self.clock()+self.seconds),self.seconds,self.clock)
            self.recordings[source]=recording
            try:loop=asyncio.get_running_loop()
            except RuntimeError:pass
            else:recording.timer=loop.call_later(max(0,recording.deadline-self.clock()),recording.close)
        recording.push('microphone',frame,gated)

    def provider(self, source, event):
        if source is None:return
        recording=self.recordings.get(source)
        if recording is not None:recording.provider(event)

    def output(self, source, frame):
        recording=self.recordings.get(source)
        if recording is not None:recording.push('submitted-output',frame)

    def finish(self, source):
        if source is None:return
        recording=self.recordings.get(source)
        if recording is not None:recording.close()


def install(root, device_id, *, arm_seconds=600, trigger='answer'):
    """Install bounded observers in this process; no network or capture action."""
    from eva.voice_service.codex_media import CodexMediaSession
    from eva.voice_service.codex_speaker_audio import SpeakerSource
    if not isinstance(device_id,str) or not device_id:
        raise ValueError('Acoustic probe requires one existing device binding')
    if getattr(CodexMediaSession.capture_native,'_acoustic_probe',False):
        raise ValueError('Acoustic probe is already installed')
    probe=Probe(root,arm_seconds=arm_seconds,trigger=trigger)
    capture=CodexMediaSession.capture_native;output=SpeakerSource.capture_frame
    input_event=CodexMediaSession.input_event

    def selected(session):
        backend=getattr(getattr(session,'state',None),'backend',None)
        return getattr(backend,'_device',{}).get('device_id')==device_id

    async def capture_native(session, frames):
        async def observed():
            async for frame in frames:
                session.current() # Same authority check as the original consumer.
                try:
                    if selected(session):
                        probe.microphone(session.source,frame)
                except Exception:pass
                yield frame
        iterator=observed()
        try:return await capture(session,iterator)
        finally:
            await iterator.aclose()
            try:probe.finish(session.source)
            except Exception:pass

    async def capture_frame(source, frame):
        try:probe.output(source,frame)
        except Exception:pass
        return await output(source,frame)

    def observed_input_event(session, event):
        result=input_event(session,event)
        try:
            if selected(session):probe.provider(session.source,event)
        except Exception:pass
        return result

    capture_native._acoustic_probe=True
    CodexMediaSession.capture_native=capture_native
    SpeakerSource.capture_frame=capture_frame
    CodexMediaSession.input_event=observed_input_event
    return probe
