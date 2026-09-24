import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from eva.voice_service.agent import BridgeState, EvaAgent, HouseholdCoreClient

TEXT = "Een volledig opgeslagen antwoord.\nBron: https://example.org/source\n" + "Aanvulling. " * 200
RESULT = dict(work_id='work:test', operation_id='a'*64, generation=1,
    output=TEXT, output_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),
    result_sha256='b'*64, completed_at='2026-09-22T12:00:00Z')


class Backend:
    def __init__(self):
        self.operations = []
        self.available = False
        self.denied = False
    async def events(self, payload):
        yield {'type': 'final', 'reply': {'text': 'Opdracht aangenomen.' if payload['text']=='start' else 'Vier.'}}
    async def work_return(self, operation, session_id, **identity):
        self.operations.append(operation)
        if self.denied: raise ValueError('authority revoked')
        if operation == 'authorize': return {'status':'authorized', **{k:RESULT[k] for k in ('operation_id','output_sha256','result_sha256')}}
        if operation == 'poll':
            result = dict(RESULT) if self.available else None
            self.available = False
            return {'result': result}
        return {'status': 'acknowledged'}


class Session:
    def __init__(self): self.spoken = []; self.handles = []; self.chat_history = []; self.options = []
    def say(self, source, **kwargs):
        self.options.append(kwargs)
        handle = SimpleNamespace(interrupted=False, chat_items=[])
        async def play():
            if isinstance(source, str): self.spoken.append(source)
            else:
                async for part in source:
                    self.spoken.append(part)
                    if kwargs.get('add_to_chat_ctx', True): self.chat_history.append(part)
        task = asyncio.create_task(play())
        handle.done = task.done
        def interrupt(): handle.interrupted = True; task.cancel()
        handle.interrupt = interrupt
        self.handles.append(handle)
        return handle


@pytest.mark.asyncio
async def test_final_then_second_turn_then_late_saved_result_without_another_turn():
    backend = Backend(); events = []; state = BridgeState(backend, events.append)
    agent = EvaAgent(state); session = Session()
    class Context:
        async def update(self, *args, **kwargs): pass
    ctx = Context(); ctx.session = session
    await agent.eva_turn(ctx, 'first', 'start')
    await agent.eva_turn(ctx, 'second', 'next')
    await asyncio.sleep(0)
    assert not state.active and session.spoken == ['Opdracht aangenomen.', 'Vier.']
    await agent.return_saved_work(session, RESULT, lambda: None)
    assert ''.join(session.spoken[2:]) == TEXT
    parts = [e for e in events if e['type'] == 'background_result_part']
    assert ''.join(e['text'] for e in parts) == TEXT
    assert all(e['output_sha256'] == RESULT['output_sha256'] for e in parts)
    assert any(TEXT in value for value in state.results.values())
    await agent.return_saved_work(session, RESULT, lambda: None)
    assert ''.join(session.spoken[2:]) == TEXT
    assert backend.operations.count('ack') == 1
    session.handles[-1].interrupted = True
    agent.replay_unheard(session)
    assert len(session.handles) == 3  # Durable result never uses software replay.


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['hash', 'authority', 'closed'])
async def test_untrusted_or_revoked_result_never_reaches_output(failure):
    backend = Backend(); events=[]; state=BridgeState(backend, events.append)
    agent=EvaAgent(state); session=Session(); result=dict(RESULT)
    if failure == 'hash': result['output'] += 'forged'
    if failure == 'authority': backend.denied=True
    def check():
        if failure == 'closed': raise ValueError('closed')
    with pytest.raises(ValueError): await agent.return_saved_work(session, result, check)
    assert not events and not session.handles and 'ack' not in backend.operations


@pytest.mark.asyncio
async def test_revocation_during_playback_interrupts_and_never_acknowledges():
    backend=Backend(); state=BridgeState(backend); agent=EvaAgent(state)
    interrupted=[]
    class PendingSession:
        def say(self, source, **kwargs):
            backend.denied=True
            return SimpleNamespace(done=lambda:False, interrupt=lambda:interrupted.append(True))
    with pytest.raises(ValueError): await agent.return_saved_work(PendingSession(), RESULT, lambda:None)
    assert interrupted == [True] and 'ack' not in backend.operations


@pytest.mark.asyncio
async def test_cancelled_delivery_interrupts_without_claim_replay():
    backend=Backend(); state=BridgeState(backend); agent=EvaAgent(state); interrupted=[]
    class PendingSession:
        def say(self, source, **kwargs):
            return SimpleNamespace(done=lambda:False, interrupt=lambda:interrupted.append(True))
    task=asyncio.create_task(agent.return_saved_work(PendingSession(), RESULT, lambda:None))
    await asyncio.sleep(.02); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert interrupted == [True] and 'ack' not in backend.operations


@pytest.mark.asyncio
async def test_household_transport_never_falls_back_to_private_return():
    client=HouseholdCoreClient('http://unused','fixture',satellite_id='assist_satellite.test',device_id='a'*32)
    with pytest.raises(ValueError, match='household'):
        await client.work_return('poll','session')


@pytest.mark.asyncio
async def test_revoked_acknowledged_context_is_not_sent_in_a_later_turn():
    backend=Backend(); state=BridgeState(backend); agent=EvaAgent(state); session=Session()
    await agent.return_saved_work(session, RESULT, lambda:None)
    assert state.saved_result_context
    assert session.options[-1]['add_to_chat_ctx'] is False
    assert not session.chat_history  # SDK history cannot reintroduce revoked bytes as recent.
    payloads=[]
    async def events(payload):
        payloads.append(payload)
        yield {'type':'final','reply':{'text':'Vier.'}}
    backend.events=events
    backend.denied=True
    class Context:
        async def update(self,*args,**kwargs): pass
    ctx=Context();ctx.session=session
    await agent.eva_turn(ctx,'next','Wat is twee plus twee?')
    assert payloads[0]['work_results']==[]
    assert not state.saved_result_context
    assert not any(TEXT in value for value in state.results.values())


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['say','notify'])
async def test_failure_before_speech_handle_discards_context_and_queued_parts(failure):
    backend=Backend(); events=[]
    def notify(value):
        if failure=='notify':raise RuntimeError('transport failure')
        events.append(value)
    state=BridgeState(backend,notify);agent=EvaAgent(state)
    class BrokenSession:
        def say(self,*args,**kwargs):raise RuntimeError('speech failed')
    with pytest.raises(RuntimeError):await agent.return_saved_work(BrokenSession(),RESULT,lambda:None)
    assert not state.results and not state.saved_result_context
    assert 'background:'+RESULT['operation_id'] in state.invalid_background_returns
    if failure=='say':assert events[-1]['type']=='background_result_unavailable'


def test_unicode_result_parts_stay_within_reliable_packet_bound():
    import json
    value={'type':'background_result_part','text':'\U0001f600'*2000,
        'work_id':'w'*240,'operation_id':'a'*64,'output_sha256':'b'*64,
        'result_sha256':'c'*64,'message_id':'background:'+'a'*64,'part':0,'total':33}
    assert len(json.dumps(value,ensure_ascii=False).encode())<12000


@pytest.mark.asyncio
@pytest.mark.parametrize("output, expected", [
    ('{"summary":"Resultaat.","findings":["Eerste punt."]}', "Resultaat.\n\n- Eerste punt."),
    ('{"summary":"Resultaat.","findings":[]}', "Resultaat."),
    ('{"summary":"Resultaat.","findings":[],"extra":"Bewaren"}', None),
])
async def test_saved_result_speech_is_readable_but_transport_keeps_original_bytes(output, expected):
    result = dict(RESULT, output=output, output_sha256=hashlib.sha256(output.encode()).hexdigest())
    class StructuredBackend(Backend):
        async def work_return(self, operation, session_id, **identity):
            if operation == 'authorize':
                return {'status':'authorized', **{k:result[k] for k in ('operation_id','output_sha256','result_sha256')}}
            return await super().work_return(operation, session_id, **identity)
    events=[]; backend=StructuredBackend(); state=BridgeState(backend, events.append)
    session=Session()
    await EvaAgent(state).return_saved_work(session, result, lambda:None)
    assert ''.join(session.spoken) == (output if expected is None else expected)
    assert ''.join(e['text'] for e in events if e['type']=='background_result_part') == output
    assert all(e['output_sha256']==result['output_sha256'] for e in events if e['type']=='background_result_part')
    assert backend.operations.count('ack') == 1


@pytest.mark.asyncio
async def test_outbound_context_authorizes_only_snapshot_without_backfill_or_concurrent_leak():
    backend = Backend()
    state = BridgeState(backend)
    agent = EvaAgent(state)
    payloads, authorized = [], []
    for index in range(12):
        key = f"saved-{index}"
        state.results[key] = f"Saved text {index}"
        state.saved_result_context[key] = dict(RESULT, work_id=key)
    state.suppressed.add("saved-11")
    async def work_return(operation, session_id, **identity):
        assert operation == "authorize"
        key = identity["work_id"]
        authorized.append(key)
        if key == "saved-8":
            # A result can arrive while an earlier authority lookup is pending.
            await asyncio.sleep(0)
            state.results["concurrent"] = "Must not enter this request"
            state.saved_result_context["concurrent"] = dict(RESULT, work_id="concurrent")
        if key == "saved-9":
            raise ValueError("revoked")
        if key == "saved-10":
            state.results[key] = "Unvalidated replacement"
            state.saved_result_context[key] = dict(RESULT, work_id="replacement")
        return {"status": "authorized", **{field: RESULT[field] for field in
                ("operation_id", "output_sha256", "result_sha256")}}
    async def events(payload):
        payloads.append(payload)
        yield {"type": "final", "reply": {"text": "Done."}}
    backend.work_return = work_return
    backend.events = events
    class Context:
        session = Session()
        async def update(self, *args, **kwargs): pass
    await agent.eva_turn(Context(), "new-turn", "Hello")
    assert authorized == ["saved-8", "saved-9", "saved-10"]
    assert payloads[0]["work_results"] == [{"message_id": "saved-8", "text": "Saved text 8"}]
    assert "saved-9" not in state.results
    assert "saved-0" in state.saved_result_context
    assert state.results["saved-10"] == "Unvalidated replacement"
    assert "concurrent" in state.results
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_saved_result_waits_for_in_flight_question_without_blocking_it():
    backend = Backend(); state = BridgeState(backend); agent = EvaAgent(state)
    session = Session(); entered = asyncio.Event(); release = asyncio.Event()
    async def events(payload):
        entered.set()
        await release.wait()
        yield {"type": "final", "reply": {"text": "Met mij gaat het goed."}}
    backend.events = events
    class Context:
        async def update(self, *args, **kwargs): pass
    ctx = Context(); ctx.session = session
    question = asyncio.create_task(agent.eva_turn(ctx, "question", "Hoe gaat het?"))
    await entered.wait()
    returned = asyncio.create_task(agent.return_saved_work(session, RESULT, lambda: None))
    await asyncio.sleep(.15)
    assert not session.handles and "ack" not in backend.operations
    release.set()
    await asyncio.wait_for(question, 2)
    await asyncio.wait_for(returned, 2)
    assert session.spoken[0] == "Met mij gaat het goed."
    assert "".join(session.spoken[1:]) == TEXT
    assert backend.operations.count("ack") == 1


@pytest.mark.asyncio
async def test_waiting_result_revocation_never_starts_speech():
    backend = Backend(); state = BridgeState(backend); agent = EvaAgent(state)
    session = Session(); state.active["question"] = "Hoe gaat het?"
    returned = asyncio.create_task(agent.return_saved_work(session, RESULT, lambda: None))
    await asyncio.sleep(.05)
    backend.denied = True
    with pytest.raises(ValueError):
        await asyncio.wait_for(returned, 2)
    assert not session.handles and "ack" not in backend.operations
    assert not state.saved_result_context
