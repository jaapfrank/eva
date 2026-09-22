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
    def __init__(self): self.spoken = []; self.handles = []
    def say(self, source, **kwargs):
        handle = SimpleNamespace(interrupted=False, chat_items=[])
        async def play():
            if isinstance(source, str): self.spoken.append(source)
            else:
                async for part in source: self.spoken.append(part)
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
