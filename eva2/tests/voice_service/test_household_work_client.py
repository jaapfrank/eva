"""Household transport remains on its own session/result boundary."""
import hashlib
from uuid import uuid4
import httpx
import pytest
from eva.voice_service.agent import HouseholdCoreClient, authorize_saved_return


@pytest.mark.asyncio
async def test_core_issued_session_and_exact_household_return_binding(monkeypatch):
    session = str(uuid4()); calls=[]; result="Public answer with https://example.org"
    binding=dict(work_id="work:household:test",generation=1,result_sha256=hashlib.sha256(result.encode()).hexdigest(),
        origin_message_id="household:test:conversation:example:request:original",session_generation=1,destination_sha256="d"*64)
    async def handle(request):
        import json
        body=json.loads(request.content); calls.append((request.url.path,body))
        assert request.headers["Authorization"]=="Bearer household-token"
        assert body["satellite_id"]=="assist_satellite.room" and body["device_id"]=="a"*32
        if request.url.path.endswith('/ready'):
            return httpx.Response(200,json={"contract":"EvaHouseholdVoiceSessionReady/v1","household_work_returns":True})
        op=request.url.path.rsplit('/',1)[-1]
        assert '/ha-voice/' in request.url.path
        if op=='open':
            assert 'session_id' not in body
            response={"state":"open","session_id":session}
        else:
            assert body['session_id']==session
            if op=='poll':response={"state":"claimed","result":{**binding,"result":result}}
            elif op in {'authorize','ack'}:
                assert body['result_sha256']==binding['result_sha256'] and 'operation_id' not in body
                response={"state":op,"result":binding}
            else:response={"state":"closed" if op=='close' else 'open',"session_id":session}
        return httpx.Response(200,json={"contract":"EvaHouseholdWorkReturn/v1",**response})
    factory=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:factory(transport=httpx.MockTransport(handle),**kwargs))
    client=HouseholdCoreClient('http://test','household-token',satellite_id='assist_satellite.room',device_id='a'*32)
    assert await client.ready() and client.work_returns and not client.private_work_returns
    assert (await client.work_return('open',str(uuid4())))['session_id']==session
    with pytest.raises(ValueError):client.request({'session_id':str(uuid4())})
    assert '/ha-voice/' in client.request({'session_id':session})[0]
    claimed=(await client.work_return('poll',session))['result']
    assert claimed['output']==result
    await authorize_saved_return(client,session,claimed,lambda:None)
    identity={k:claimed[k] for k in ('work_id','operation_id')}
    for disposition in ['offered','interrupted']:
        await client.work_return('ack',session,**identity,disposition=disposition)
    assert [b['disposition'] for p,b in calls if p.endswith('/ack')]==['offered','interrupted']
    await client.work_return('close',session)
    with pytest.raises(ValueError):await client.work_return('authorize',session,**identity)
    assert all('/voice-session/work/' not in p for p,b in calls)


@pytest.mark.asyncio
async def test_household_returns_remain_disabled_and_cannot_choose_private_identity():
    client=HouseholdCoreClient('http://unused','token',satellite_id='assist_satellite.room',device_id='a'*32)
    assert not client.work_returns
    with pytest.raises(ValueError):await client.work_return('poll',str(uuid4()))
    with pytest.raises(ValueError):client.request({'principal_id':'private'})


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [False, True])
async def test_household_saved_audio_has_explicit_disposition_and_no_sdk_history(interrupted):
    from tests.voice_service.test_private_work_return import Backend, Session, RESULT, TEXT
    from eva.voice_service.agent import EvaAgent, BridgeState
    class HouseholdBackend(Backend):
        household_work_returns = True
        def __init__(self):
            super().__init__()
            self.dispositions = []
        async def work_return(self, operation, session_id, **identity):
            if operation == "ack": self.dispositions.append(identity["disposition"])
            return await super().work_return(operation, session_id, **identity)
    class Output(Session):
        def say(self, source, **kwargs):
            handle = super().say(source, **kwargs)
            handle.interrupted = interrupted
            return handle
    backend = HouseholdBackend(); state = BridgeState(backend); session = Output()
    await EvaAgent(state).return_saved_work(session, RESULT, lambda: None)
    assert backend.dispositions == ["offered", "interrupted" if interrupted else "completed"]
    assert ''.join(session.spoken) == TEXT
    assert not session.chat_history and session.options[-1]["add_to_chat_ctx"] is False
