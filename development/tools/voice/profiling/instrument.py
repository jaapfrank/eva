"""Content-free timing hooks for a synthetic installed-route probe only."""
import asyncio
import json
import time
from eva.voice_service.codex_media import CodexMediaSession, MicrophoneTrack
from eva.voice_service.codex_session import CodexVoiceSession
from eva.voice_service.agent import HouseholdCoreClient
from aiortc.rtcrtpreceiver import RTCRtpReceiver

active = None
def mark(stage, **fields):
    if active is not None:
        active.diagnostic('profile_' + stage, **fields)

old_prewarm = CodexMediaSession.prewarm
def prewarm(self):
    global active
    active = self
    fresh = self.peer is None
    old_prewarm(self)
    if not fresh: return
    self.profile_turn = 'greeting'
    self.profile_decoded = set()
    self.profile_sink = set()
    for event in ('icegatheringstatechange', 'iceconnectionstatechange', 'connectionstatechange', 'signalingstatechange'):
        def state(event=event):
            mark(event, ice=self.peer.iceConnectionState,
                 connection=self.peer.connectionState, signaling=self.peer.signalingState)
        self.peer.on(event, state)
    self.peer.sctp.transport.on('statechange', lambda: mark('dtls', state=self.peer.sctp.transport.state))
    self.channel.on('open', lambda: mark('datachannel_open'))
    def event(raw):
        data = json.loads(raw)
        if data.get('type') == 'session.started':
            session = data.get('session', {})
            mark('provider_session', model=session.get('model'), top_model=data.get('model'))
        if data.get('type') == 'turn.created' and data.get('turn', {}).get('role') == 'assistant':
            self.profile_turn = data['turn'].get('id')
    self.channel.on('message', event)
CodexMediaSession.prewarm = prewarm

old_start = CodexMediaSession.start
async def start(self, *args, **kwargs):
    sink = self.output.audio
    if sink is not None:
        capture = sink.capture_frame
        async def traced(frame):
            key = self.profile_turn
            if max((abs(v) for v in frame.data), default=0) > 80 and key not in self.profile_sink:
                self.profile_sink.add(key)
                mark('first_sink_signal', turn=key)
            return await capture(frame)
        sink.capture_frame = traced
    return await old_start(self, *args, **kwargs)
CodexMediaSession.start = start

old_play = CodexMediaSession.play
async def play(self, track):
    recv = track.recv
    first = True
    async def receive():
        nonlocal first
        frame = await recv()
        if first:
            first = False
            mark('first_decoded_frame')
        return frame
    track.recv = receive
    return await old_play(self, track)
CodexMediaSession.play = play

old_recv = MicrophoneTrack.recv
async def recv(self):
    frame = await old_recv(self)
    if self.enabled and not getattr(self, 'profile_sent', False):
        import struct
        data = bytes(frame.planes[0])[:frame.samples*2]
        if max(map(abs, struct.unpack('<' + str(frame.samples) + 'h', data)), default=0) > 80:
            self.profile_sent = True
            mark('first_microphone_signal_sent', queued_frames=self.frames.qsize())
    return frame
MicrophoneTrack.recv = recv

old_rtp = RTCRtpReceiver._handle_rtp_packet
async def rtp(self, *args, **kwargs):
    if not getattr(self, 'profile_received', False):
        self.profile_received = True
        mark('first_rtp_received')
    return await old_rtp(self, *args, **kwargs)
RTCRtpReceiver._handle_rtp_packet = rtp

old_request = CodexVoiceSession.request
async def request(self, method, params):
    started = time.monotonic()
    mark('rpc_start', method=method)
    try: return await old_request(self, method, params)
    finally: mark('rpc_end', method=method, duration_ms=round((time.monotonic()-started)*1000))
CodexVoiceSession.request = request

old_return = HouseholdCoreClient.work_return
async def work_return(self, operation, *args, **kwargs):
    started = time.monotonic()
    result = await old_return(self, operation, *args, **kwargs)
    if operation == 'poll' and result.get('result'):
        mark('saved_result_claimed', duration_ms=round((time.monotonic()-started)*1000), work_id=result['result'].get('work_id'))
    elif operation == 'authorize':
        if not getattr(self, 'profile_authorized', False):
            self.profile_authorized = True
            mark('saved_result_authorized', duration_ms=round((time.monotonic()-started)*1000))
    return result
HouseholdCoreClient.work_return = work_return

old_events = HouseholdCoreClient.events
async def events(self, payload):
    async for event in old_events(self, payload):
        if event.get('type') != 'delta':
            fields = {'kind':event.get('type')}
            if event.get('type') == 'final':
                reply=event.get('reply', {})
                fields['model_timing']=reply.get('model_timing')
                fields['reply_keys']=list(reply)
            mark('core_stream', **fields)
        yield event
HouseholdCoreClient.events = events
