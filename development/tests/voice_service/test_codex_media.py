import asyncio
import struct
from types import SimpleNamespace
import pytest
from livekit import rtc
from eva.voice_service.codex_media import MicrophoneTrack, CodexMediaSession
from eva.voice_service.codex_speaker_audio import SpeakerSource

def pcm(value=0,rate=48000):
    count=rate//50
    return rtc.AudioFrame(struct.pack('<'+'h'*count,*([value]*count)),rate,1,count)

@pytest.mark.asyncio
async def test_microphone_uses_silence_until_ready_and_bounds_stale_audio():
    source=MicrophoneTrack()
    source.push(pcm(500))
    assert not any(bytes((await source.recv()).planes[0]))
    source.enabled=True
    assert any(bytes((await source.recv()).planes[0]))
    for _ in range(10):source.push(pcm(500))
    source.push(pcm(900))
    assert source.frames.qsize() == 10 and source.dropped_frames == 1
    assert source.frames._queue[-1] == bytes(pcm(900).data)
    source.stop()

@pytest.mark.asyncio
async def test_microphone_recovers_after_capture_burst_without_closing():
    reports=[]
    source=MicrophoneTrack(lambda dropped,queued:reports.append((dropped,queued)))
    source.enabled=True;source.catching_up=False
    for value in range(100):source.push(pcm(value))
    assert source.frames.qsize()==10 and source.dropped_frames==90
    assert len(reports)==1
    assert bytes((await source.recv()).planes[0])==bytes(pcm(90).data)
    for _ in range(9):await source.recv()
    source.push(pcm(700))
    assert bytes((await source.recv()).planes[0])==bytes(pcm(700).data)
    source.stop()


@pytest.mark.asyncio
async def test_native_echo_gate_starts_before_first_output_and_drains_without_losing_prior_words():
    entered=asyncio.Event();release=asyncio.Event();played=asyncio.Event()
    class Sink:
        wire=object()
        segment=None
        async def capture_frame(self,frame):
            entered.set();await release.wait()
            if self.segment is None:
                self.segment=SimpleNamespace(task=asyncio.create_task(played.wait()))
        def clear_buffer(self):pass
    sink=Sink();output=SpeakerSource(sink)
    microphone=MicrophoneTrack(input_suppressed=lambda:output.input_suppressed)
    microphone.enabled=True
    microphone.push(pcm(400)) # Words captured before the greeting remain valid.
    sending=asyncio.create_task(output.capture_frame(pcm(700)))
    try:
        await asyncio.wait_for(entered.wait(),1)
        assert output.input_suppressed and sink.segment is None
        for _ in range(100):microphone.push(pcm(700))
        assert microphone.echo_frames_discarded==100 and microphone.frames.qsize()==1
        assert bytes((await microphone.recv()).planes[0])==bytes(pcm(400).data)
        assert not any(bytes((await microphone.recv()).planes[0]))
        release.set();await asyncio.wait_for(sending,1)
        assert output.input_suppressed
    finally:
        release.set();played.set();await asyncio.gather(sending,return_exceptions=True)
        if sink.segment:await sink.segment.task
        microphone.stop()


@pytest.mark.asyncio
async def test_browser_sink_does_not_disable_simultaneous_microphone_audio():
    class Sink:
        async def capture_frame(self,frame):pass
    output=SpeakerSource(Sink())
    microphone=MicrophoneTrack(input_suppressed=lambda:output.input_suppressed)
    microphone.enabled=True
    try:
        await output.capture_frame(pcm(700))
        microphone.push(pcm(400))
        assert not output.input_suppressed
        assert bytes((await microphone.recv()).planes[0])==bytes(pcm(400).data)
    finally:microphone.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['cancelled','exception'])
async def test_native_echo_gate_never_treats_failed_playback_as_a_physical_ack(failure):
    completion=asyncio.get_running_loop().create_future()
    class Sink:
        wire=object()
        segment=SimpleNamespace(task=completion)
        async def capture_frame(self,frame):pass
    output=SpeakerSource(Sink())
    microphone=MicrophoneTrack(input_suppressed=lambda:output.input_suppressed)
    microphone.enabled=True
    try:
        await output.capture_frame(pcm(700))
        if failure=='cancelled':completion.cancel()
        else:completion.set_exception(RuntimeError('Missing physical receipt'))
        await asyncio.sleep(.31)
        assert output.input_suppressed
        microphone.push(pcm(700))
        assert not microphone.frames.qsize()
        assert not any(bytes((await microphone.recv()).planes[0]))
    finally:microphone.stop()

@pytest.mark.asyncio
async def test_native_speaker_does_not_play_endless_silence_and_honors_stop():
    class Sink:
        def __init__(self):self.frames=[];self.flushed=0;self.stopped=0
        async def capture_frame(self,frame):self.frames.append(frame)
        def flush(self):self.flushed+=1
        def clear_buffer(self):self.stopped+=1
    sink=Sink();source=SpeakerSource(sink)
    for _ in range(30):await source.capture_frame(pcm())
    assert not sink.frames
    for _ in range(8):await source.capture_frame(pcm(700))
    assert sink.frames and all(x.sample_rate==24000 and x.num_channels==1 for x in sink.frames)
    for _ in range(30):await source.capture_frame(pcm())
    assert sink.flushed==1
    size=len(sink.frames)
    for _ in range(30):await source.capture_frame(pcm())
    assert len(sink.frames)==size
    source.clear_queue()
    assert sink.stopped==1 and not source.active and not source.leading

@pytest.mark.asyncio
async def test_speaker_microphone_resamples_existing_16khz_frames():
    session=CodexMediaSession({},None,lambda:None)
    session.microphone=MicrophoneTrack();session.microphone.enabled=True
    async def frames():
        for _ in range(5):yield pcm(500,16000)
    await session.capture_native(frames())
    assert 3<=session.microphone.frames.qsize()<=5
    assert all(len(x)==1920 for x in session.microphone.frames._queue)
    await session.aclose()

@pytest.mark.asyncio
async def test_native_capture_burst_keeps_session_and_next_audio_usable():
    session=CodexMediaSession({},None,lambda:None)
    session.microphone=MicrophoneTrack()
    session.microphone.enabled=True;session.microphone.catching_up=False
    async def frames():
        for _ in range(100):yield pcm(500,16000)
    await session.capture_native(frames())
    assert not session.closed and session.microphone.dropped_frames > 0
    assert session.microphone.frames.qsize()==10
    session.clear_user_turn()
    session.microphone.push(pcm(700))
    assert bytes((await session.microphone.recv()).planes[0])==bytes(pcm(700).data)
    await session.aclose()

@pytest.mark.asyncio
async def test_native_opening_speech_survives_slow_connection_setup():
    source=MicrophoneTrack()
    for _ in range(100):source.push(pcm(700))
    for _ in range(400):source.push(pcm())
    source.enabled=True
    # Connection setup may take longer than five seconds. Preserve every voiced
    # frame, then catch up over buffered silence to the live microphone.
    for _ in range(100):
        assert any(bytes((await source.recv()).planes[0]))
    await source.recv()
    assert source.frames.qsize()<=2 and not source.catching_up
    source.stop()

@pytest.mark.asyncio
async def test_native_capture_is_drained_before_account_negotiation(monkeypatch):
    from eva.voice_service import codex_media
    from eva.voice_service.agent import BridgeState, EvaAgent
    consumed=asyncio.Event()
    class NegotiationReached(Exception):pass
    class Voice:
        def __init__(self,*args,**kwargs):pass
        async def __aenter__(self):
            await asyncio.wait_for(consumed.wait(),.5)
            raise NegotiationReached()
        async def close(self):pass
    class Sink:
        def clear_buffer(self):pass
    async def microphone():
        consumed.set()
        yield pcm(700,16000)
    monkeypatch.setattr(codex_media,'CodexVoiceSession',Voice)
    state=BridgeState(None)
    session=CodexMediaSession({'codex_executable':'unused','codex_auth_file':'unused'},state,lambda:None)
    session.prewarm()
    session.output.audio=Sink();session.input.audio=microphone()
    await asyncio.sleep(0)
    assert not consumed.is_set()
    try:
        with pytest.raises(NegotiationReached):await session.start(EvaAgent(state))
        assert consumed.is_set()
    finally:await session.aclose()

@pytest.mark.asyncio
async def test_stop_overtaking_blocked_audio_does_not_kill_the_conversation():
    entered,release=asyncio.Event(),asyncio.Event()
    class Sink:
        async def capture_frame(self,frame):
            entered.set();await release.wait()
            raise RuntimeError('Old wire generation was interrupted')
        def clear_buffer(self):release.set()
    source=SpeakerSource(Sink())
    pending=asyncio.create_task(source.capture_frame(pcm(700)))
    await entered.wait();source.clear_queue()
    await asyncio.wait_for(pending,1)
    assert not source.active

@pytest.mark.asyncio
async def test_actual_speaker_failure_without_local_stop_still_fails_closed():
    class Sink:
        async def capture_frame(self,frame):raise RuntimeError('Speaker disconnected')
    source=SpeakerSource(Sink())
    with pytest.raises(RuntimeError,match='Speaker disconnected'):
        await source.capture_frame(pcm(700))

@pytest.mark.asyncio
async def test_cached_wake_greeting_plays_without_a_model_connection():
    class Sink:
        def __init__(self):self.frames=0;self.flushed=False;self.confirmed=False
        async def capture_frame(self,frame):self.frames+=frame.samples_per_channel
        def flush(self):self.flushed=True
        async def wait_for_playout(self):self.confirmed=True
        def clear_buffer(self):pass
    session=CodexMediaSession({},None,lambda:None)
    sink=Sink();session.output.audio=sink;session.source=SpeakerSource(sink)
    await session.greet()
    assert sink.frames>24000 and sink.flushed and sink.confirmed
    assert session.voice is None
    await session.aclose()


def provider_turn(session, kind, identity, role='assistant', text=''):
    import json
    session.provider_message(json.dumps({'type':kind,'turn':{'id':identity,'role':role,'transcript':text}}))


@pytest.mark.asyncio
async def test_speech_waits_for_its_own_completion_and_serializes_queued_offers():
    sent=asyncio.Queue()
    class Voice:
        async def say(self,text):await sent.put(text)
        async def close(self):pass
    session=CodexMediaSession({},None,lambda:None);session.voice=Voice()
    first=session.say('First');second=session.say('Second')
    assert await asyncio.wait_for(sent.get(),1)=='First'
    provider_turn(session,'turn.done','older')
    await asyncio.sleep(0)
    assert not first.done() and sent.empty()
    provider_turn(session,'turn.created','first')
    provider_turn(session,'turn.done','older')
    await asyncio.sleep(0)
    assert not first.done()
    provider_turn(session,'turn.done','first')
    await asyncio.wait_for(first.task,1)
    assert await asyncio.wait_for(sent.get(),1)=='Second'
    provider_turn(session,'turn.done','first')
    await asyncio.sleep(0)
    assert not second.done()
    provider_turn(session,'turn.created','second')
    provider_turn(session,'turn.done','second')
    await asyncio.wait_for(second.task,1)
    # Provider completion alone still does not attest physical playback.
    assert first.interrupted and second.interrupted
    await session.aclose()


@pytest.mark.asyncio
async def test_user_interruption_cancels_old_offer_without_poisoning_next_offer():
    sent=asyncio.Queue()
    class Voice:
        async def say(self,text):await sent.put(text)
        async def close(self):pass
    session=CodexMediaSession({},None,lambda:None);session.voice=Voice()
    old=session.say('Old');await asyncio.wait_for(sent.get(),1)
    provider_turn(session,'turn.created','old')
    provider_turn(session,'turn.created','user','user')
    await asyncio.gather(old.task,return_exceptions=True)
    # An accepted stop is not a provider terminal event. A new append before
    # that terminal event can extend the interrupted response instead.
    new=session.say('New')
    await asyncio.sleep(0)
    assert sent.empty()
    provider_turn(session,'turn.done','old')
    assert await asyncio.wait_for(sent.get(),1)=='New'
    provider_turn(session,'turn.created','old')
    assert session._speech_turn is None
    provider_turn(session,'turn.created','new')
    assert not session.output_suppressed
    provider_turn(session,'turn.done','old')
    assert not new.done()
    provider_turn(session,'turn.done','new')
    await asyncio.wait_for(new.task,1)
    assert not session.closed
    await session.aclose()


@pytest.mark.asyncio
async def test_native_playout_requires_new_audio_and_its_device_ack():
    acknowledged=asyncio.Event()
    class Sink:
        def __init__(self):
            self.segment=SimpleNamespace(generation=7)
            self.wire=SimpleNamespace(playback_result=lambda generation:SimpleNamespace(status='completed',confirmed_frames=480))
        async def capture_frame(self,frame):pass
        def flush(self):pass
        async def wait_for_playout(self):await acknowledged.wait()
        def clear_buffer(self):pass
    source=SpeakerSource(Sink());marker=source.playback_marker()
    wait=asyncio.create_task(source.confirm_playout(marker))
    for _ in range(4):await source.capture_frame(pcm(700))
    for _ in range(20):await source.capture_frame(pcm())
    await asyncio.sleep(0)
    assert not wait.done()  # Silence and provider completion cannot replace ACK.
    acknowledged.set()
    assert await asyncio.wait_for(wait,1)
    next_wait=asyncio.create_task(source.confirm_playout(source.playback_marker()))
    await asyncio.sleep(0)
    assert not next_wait.done()  # The previous completed segment is not new audio.
    source.clear_queue()
    assert await asyncio.wait_for(next_wait,1) is False


@pytest.mark.asyncio
async def test_speech_handle_finishes_only_after_current_native_playout_confirmation():
    sent,ack=asyncio.Event(),asyncio.Event()
    class Voice:
        async def say(self,text):sent.set()
        async def close(self):pass
    class Source:
        def playback_marker(self):return (7,20)
        async def confirm_playout(self,marker):
            assert marker==(7,20)
            await ack.wait();return True
        def clear_queue(self):pass
        async def aclose(self):pass
    session=CodexMediaSession({},None,lambda:None)
    session.voice=Voice();session.source=Source()
    handle=session.say('Confirmed only after audio')
    await sent.wait()
    provider_turn(session,'turn.created','answer')
    provider_turn(session,'turn.done','answer')
    await asyncio.sleep(0)
    assert not handle.done() and handle.interrupted
    ack.set();await asyncio.wait_for(handle.task,1)
    assert handle.done() and not handle.interrupted
    await session.aclose()


@pytest.mark.asyncio
async def test_revoking_active_offer_stops_audio_but_cancelling_queued_offer_does_not():
    sent=asyncio.Event();stops=[]
    class Voice:
        thread='test-thread'
        async def say(self,text):sent.set()
        async def request(self,method,params):stops.append(method)
        async def close(self):pass
    class Source:
        cleared=0
        def clear_queue(self):self.cleared+=1
        async def aclose(self):pass
    session=CodexMediaSession({},None,lambda:None)
    session.voice=Voice();session.source=Source()
    active=session.say('Current saved result');await sent.wait()
    provider_turn(session,'turn.created','active')
    queued=session.say('Another saved result')
    queued.interrupt();await asyncio.gather(queued.task,return_exceptions=True)
    assert session.source.cleared==0 and not session.output_suppressed
    active.interrupt();await asyncio.gather(active.task,return_exceptions=True)
    await asyncio.sleep(0)
    assert session.source.cleared==1 and session.output_suppressed
    assert stops==['thread/realtime/appendText']
    assert not session.closed
    await session.aclose()


@pytest.mark.asyncio
async def test_revoked_offer_does_not_cancel_independent_queued_result():
    sent=asyncio.Queue()
    class Voice:
        thread='test'
        async def say(self,text):await sent.put(text)
        async def request(self,*args):pass
        async def close(self):pass
    session=CodexMediaSession({},None,lambda:None);session.voice=Voice()
    old=session.say('Old');await sent.get();provider_turn(session,'turn.created','old')
    queued=session.say('Independent')
    old.interrupt();await asyncio.gather(old.task,return_exceptions=True)
    assert not queued.done() and sent.empty()
    provider_turn(session,'turn.done','old')
    assert await asyncio.wait_for(sent.get(),1)=='Independent'
    provider_turn(session,'turn.created','next');provider_turn(session,'turn.done','next')
    await asyncio.wait_for(queued.task,1)
    assert not session.closed
    await session.aclose()

@pytest.mark.asyncio
async def test_farewell_waits_for_own_answer_and_device_playout():
    import json
    session=CodexMediaSession({},None,lambda:None)
    played=asyncio.Event();closed=asyncio.Event()
    class Source:
        def playback_marker(self):return (0,0)
        async def confirm_playout(self,marker):await played.wait();return True
        def clear_queue(self):pass
    session.source=Source()
    async def close():session.closed=True;closed.set()
    session.aclose=close
    session.provider_message(json.dumps({'type':'turn.created','turn':{'role':'user','id':'u'}}))
    session.provider_message(json.dumps({'type':'turn.done','turn':{'role':'user','id':'u','transcript':'Doei Eva!'}}))
    session.provider_message(json.dumps({'type':'turn.done','turn':{'role':'assistant','id':'old'}}))
    await asyncio.sleep(0)
    assert not closed.is_set()
    session.provider_message(json.dumps({'type':'turn.created','turn':{'role':'assistant','id':'bye'}}))
    session.provider_message(json.dumps({'type':'turn.done','turn':{'role':'assistant','id':'bye'}}))
    await asyncio.sleep(0)
    assert not closed.is_set()
    played.set();await session._farewell_task
    assert closed.is_set()

@pytest.mark.asyncio
async def test_farewell_is_cancelled_when_speaker_continues():
    import json
    session=CodexMediaSession({},None,lambda:None)
    session.user_transcript('Tot ziens.')
    task=session._farewell_task
    session.provider_message(json.dumps({'type':'turn.created','turn':{'role':'user','id':'more'}}))
    await asyncio.gather(task,return_exceptions=True)
    assert task.cancelled() and not session.closed
    await session.aclose()

@pytest.mark.parametrize('text',['Dag Eva', 'Hoe zeg je doei in het Frans?', 'Zeg maar doei tegen die taak', 'Stop het onderzoek', 'Nog niet, doei zeg ik straks', '"doei"', 'Dag Eva, wat heb je gedaan?'])
def test_farewell_does_not_match_mentions_or_task_cancellation(text):
    session=CodexMediaSession({},None,lambda:None)
    session.user_transcript(text)
    assert session._farewell_task is None

@pytest.mark.asyncio
async def test_farewell_closes_when_playback_confirmation_is_missing():
    session=CodexMediaSession({},None,lambda:None)
    events=[]
    class Source:
        async def confirm_playout(self,marker):raise TimeoutError
        def clear_queue(self):pass
        async def aclose(self):pass
    session.source=Source()
    session.diagnostic=lambda stage,**fields:events.append(stage)
    completed=asyncio.Event();completed.set()
    await session.finish_farewell(completed,(0,0))
    assert session.closed
    assert 'farewell_playout_timeout' in events
    assert 'farewell_playout' not in events


@pytest.mark.asyncio
async def test_standby_time_does_not_expire_newly_connected_session(monkeypatch):
    from eva.voice_service import codex_media
    now = [0.0]
    monkeypatch.setattr(codex_media, "time", SimpleNamespace(monotonic=lambda: now[0]))
    session = CodexMediaSession({}, None, lambda: None)
    session.microphone = MicrophoneTrack()
    now[0] = 1000.0  # Long local wakeword standby before an actual connection.
    session.provider_message('{"type":"session.started"}')
    assert session.last_activity == 1000.0
    await session.aclose()


@pytest.mark.asyncio
async def test_connected_session_still_expires_after_idle_limit(monkeypatch):
    from eva.voice_service import codex_media
    now = [1000.0]
    monkeypatch.setattr(codex_media, "time", SimpleNamespace(monotonic=lambda: now[0]))
    session = CodexMediaSession({}, None, lambda: None)
    session.last_activity = 879.0
    events = []
    session.diagnostic = lambda stage, **fields: events.append((stage, fields))
    await asyncio.wait_for(session.watch_lifetime(), 2)
    assert session.closed
    assert any(stage == "lifetime_expired" and fields["reason"] == "idle_timeout"
               for stage, fields in events)

@pytest.mark.asyncio
async def test_pending_foreground_answer_is_not_idle_but_idle_close_returns(monkeypatch):
    from eva.voice_service import codex_media
    now=[1000.0]
    monkeypatch.setattr(codex_media, 'time', SimpleNamespace(monotonic=lambda:now[0]))
    state=SimpleNamespace(active={'pending':'question'})
    session=CodexMediaSession({},state,lambda:None)
    session.last_activity=879.0
    task=asyncio.create_task(session.watch_lifetime())
    try:
        await asyncio.sleep(1.1)
        assert not session.closed and session.last_activity==1000.0
        state.active.clear();now[0]=1121.0
        await asyncio.wait_for(task,2)
        assert session.closed
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        await session.aclose()


def test_transport_diagnostics_only_log_known_protocol_states():
    session = CodexMediaSession({}, None, lambda: None)
    session.peer = SimpleNamespace(connectionState="connected", iceConnectionState="failed",
                                   signalingState="Bearer secret private transcript")
    events = []
    session.diagnostic = lambda stage, **fields: events.append(fields)
    session.transport_diagnostic("test")
    assert events == [{"connectionState": "connected", "iceConnectionState": "failed",
                       "signalingState": "unknown"}]


@pytest.mark.asyncio
@pytest.mark.parametrize('ending', ['completed', 'cancelled', 'absolute_limit'])
async def test_renderer_playback_keeps_listener_alive_until_settled(monkeypatch, ending):
    from eva.voice_service import codex_media
    now=[1000.0]
    monkeypatch.setattr(codex_media, 'time', SimpleNamespace(monotonic=lambda:now[0]))
    session=CodexMediaSession({}, SimpleNamespace(active={}), lambda:None)
    renderer=CodexMediaSession({}, session.state, session.current, render_only=True)
    session.renderer=renderer
    renderer.started.set()
    entered=asyncio.Event()
    class Voice:
        thread='test'
        async def say(self, text):entered.set()
        async def close(self):pass
    renderer.voice=Voice()
    handle=session.say('Een toegelaten antwoord.')
    await entered.wait()
    session.last_activity=879.0
    watcher=asyncio.create_task(session.watch_lifetime())
    try:
        await asyncio.sleep(1.1)
        assert not session.closed
        assert session.last_activity==1000.0
        if ending=='absolute_limit':
            now[0]=2801.0
            await asyncio.wait_for(watcher,2)
            assert session.closed
            assert handle.task.cancelled()
            return
        # Completion between watchdog ticks must start a fresh listening window.
        now[0]=1110.0
        if ending=='cancelled':
            session.interrupt()
        else:
            renderer._speech_waiter.set_result(None)
        await asyncio.gather(handle.task,return_exceptions=True)
        await asyncio.sleep(0)
        assert session.last_activity==1110.0
        assert not renderer.speech_handles
        now[0]=1229.0
        await asyncio.sleep(1.1)
        assert not session.closed
        now[0]=1231.0
        await asyncio.wait_for(watcher,2)
        assert session.closed
    finally:
        watcher.cancel()
        await asyncio.gather(watcher,return_exceptions=True)
        await session.aclose()


@pytest.mark.parametrize('text', [
    ('Dit is een afgeronde zin.  '*90),
    ('woord zonder zinsgrens '*130),
    ('x'*1600),
])
def test_speech_parts_preserve_exact_text_and_transport_bound(text):
    from eva.voice_service.codex_media import speech_parts
    parts=list(speech_parts(text))
    assert ''.join(parts)==text
    assert all(0<len(part)<=1500 for part in parts)
    if ' ' in text:
        assert all(part[-1].isspace() for part in parts[:-1])
    if '.' in text:
        assert all(part.rstrip().endswith('.') for part in parts[:-1])


@pytest.mark.asyncio
async def test_long_speech_uses_sentence_boundaries_and_each_completion():
    session=CodexMediaSession({},None,lambda:None,render_only=True)
    session.started.set();sent=[]
    class Voice:
        async def say(self,text):
            sent.append(text)
            asyncio.get_running_loop().call_soon(session._speech_waiter.set_result,None)
        async def close(self):pass
    session.voice=Voice()
    text='Een complete zin met haar oorspronkelijke spaties.  '*70
    try:
        handle=session.say(text)
        await asyncio.wait_for(handle.task,1)
        assert len(sent)>1 and ''.join(sent)==text
        assert all(part.rstrip().endswith('.') for part in sent)
    finally:await session.aclose()
