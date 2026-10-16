"""Voice transport cannot bypass Eva's authoritative conversation."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from eva.voice_service.codex_backend import EvaVoiceBackend
from eva.voice_service.codex_media import CodexMediaSession
from eva.voice_service.codex_session import CodexVoiceSession


def turn(session, kind, identity, text='', role='user'):
    session.provider_message(json.dumps({'type':kind,'turn':{
        'id':identity,'role':role,'transcript':text}}))


async def settle(session):
    while session.tasks:
        await asyncio.gather(*tuple(session.tasks),return_exceptions=True)


def fixture():
    payloads=[];spoken=[]
    class Core:
        async def events(self,payload):
            payloads.append(payload)
            yield {'type':'final','reply':{'text':'Kern: '+payload['text']}}
    session=CodexMediaSession({},None,lambda:None)
    session.bridge=EvaVoiceBackend(Core(),str(uuid4()),session.current)
    def say(text,**kwargs):
        spoken.append(text)
        return SimpleNamespace(task=asyncio.create_task(asyncio.sleep(0)),interrupted=False)
    session.renderer=SimpleNamespace(interrupt=lambda:None,say=say,aclose=AsyncMock())
    return session,payloads,spoken


@pytest.mark.asyncio
async def test_every_kind_of_spoken_turn_enters_the_same_core_once():
    s,payloads,spoken=fixture()
    try:
        for i,text in enumerate(['Hoi Eva','Wat heb je vandaag gedaan?',
                                 'Nee, ik bedoelde gisteren','Doe het licht aan']):
            turn(s,'turn.created',str(i))
            turn(s,'turn.done',str(i),text)
            turn(s,'turn.done',str(i),text)
            await settle(s)
        assert [p['text'] for p in payloads]==['Hoi Eva','Wat heb je vandaag gedaan?',
            'Nee, ik bedoelde gisteren','Doe het licht aan']
        assert spoken==['Kern: '+p['text'] for p in payloads]
        assert len({p['message_id'] for p in payloads})==4
        assert len({p['session_id'] for p in payloads})==1
        assert any(r['text']=='Hoi Eva' for r in payloads[-1]['recent'])
    finally:await s.aclose()


@pytest.mark.asyncio
async def test_conflicting_duplicate_cannot_start_second_action():
    s,payloads,spoken=fixture()
    turn(s,'turn.done','one','Doe het licht aan')
    await settle(s)
    turn(s,'turn.done','one','Doe de deur open')
    await asyncio.sleep(.02)
    assert s.closed and len(payloads)==1
    await s.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('interrupt', ['speech','stop'])
async def test_interruption_revokes_delayed_answer_not_accepted_core_work(interrupt):
    s,payloads,spoken=fixture();entered=asyncio.Event();release=asyncio.Event()
    original=s.bridge.dispatch
    async def delayed(text,key):
        entered.set();await release.wait()
        return await original(text,key)
    s.bridge.dispatch=delayed
    turn(s,'turn.done','one','Begin onderzoek')
    await entered.wait()
    if interrupt=='speech':turn(s,'turn.created','two')
    else:s.interrupt()
    release.set();await settle(s)
    assert len(payloads)==1 and not spoken
    assert len(s.bridge.results)==1
    await s.aclose()


@pytest.mark.asyncio
async def test_unapproved_input_assistant_never_speaks_or_enters_memory():
    s,payloads,spoken=fixture()
    turn(s,'turn.created','unexpected',role='assistant')
    turn(s,'turn.done','unexpected','Ik ben een andere Eva',role='assistant')
    await asyncio.sleep(0)
    assert not spoken and not payloads and not s.bridge.recent
    await s.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('farewell',['Dankje doei','Dank je, tot doei','Dank je, hè. Do ei'])
async def test_farewell_waits_for_core_and_playout_before_closing(farewell):
    s,payloads,spoken=fixture();played=asyncio.Event()
    def say(text):
        spoken.append(text)
        return SimpleNamespace(task=asyncio.create_task(played.wait()),interrupted=False)
    s.renderer.say=say
    turn(s,'turn.done','bye',farewell)
    await asyncio.sleep(.5)
    assert len(payloads)==1 and spoken and not s.closed
    played.set()
    for _ in range(10):await asyncio.sleep(0)
    assert s.closed
    await s.aclose()


@pytest.mark.asyncio
async def test_renderer_cannot_close_shared_core_or_output():
    s=CodexMediaSession({},None,lambda:None,render_only=True)
    s.bridge=SimpleNamespace(close=AsyncMock())
    s.source=SimpleNamespace(clear_queue=lambda:None,aclose=AsyncMock())
    await s.aclose()
    s.bridge.close.assert_not_awaited();s.source.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_handoff_rejected_before_body_or_core_processing():
    s,payloads,spoken=fixture();s.bridge.allow_handoffs=False
    request=SimpleNamespace(headers={'X-Eva-Voice-Bridge':s.bridge.secret},json=AsyncMock())
    with pytest.raises(web.HTTPForbidden):await s.bridge.respond(request)
    request.json.assert_not_awaited()
    assert not payloads
    await s.aclose()


@pytest.mark.asyncio
async def test_media_only_protocol_does_not_automatically_forward_core_results():
    s=CodexVoiceSession('unused',lambda:None,None,media_only=True)
    s.thread='one';s.request=AsyncMock()
    for method,params in [('thread/realtime/started',{}),('thread/realtime/sdp',{'sdp':'answer'})]:
        s.events.put_nowait({'method':method,'params':{'threadId':'one',**params}})
    assert await s.start('v=0\r\n',prompt='listen',output_modality='audio')=='answer'
    params=s.request.call_args.args[1]
    assert params['outputModality']=='audio'
    assert params['clientManagedHandoffs'] is True
    assert params['delegationAckFiller'] is False

@pytest.mark.asyncio
async def test_late_old_transcript_cannot_inherit_new_turn_generation():
    s,payloads,spoken=fixture()
    turn(s,'turn.created','old')
    turn(s,'turn.created','new')
    generation=s._generation
    turn(s,'turn.created','old')
    assert s._generation==generation
    turn(s,'turn.done','old','Oude vraag')
    await settle(s)
    assert not payloads and not spoken
    turn(s,'turn.done','new','Nieuwe vraag')
    await settle(s)
    assert spoken==['Kern: Oude vraag Nieuwe vraag']
    await s.aclose()


@pytest.mark.asyncio
async def test_transient_core_error_keeps_voice_alive_without_claiming_success():
    s,payloads,spoken=fixture()
    s.bridge.dispatch=AsyncMock(side_effect=RuntimeError('private detail'))
    turn(s,'turn.done','one','Voer de taak uit')
    await settle(s)
    assert not s.closed and len(spoken)==1
    assert 'geen betrouwbaar antwoord' in spoken[0] and 'private detail' not in spoken[0]
    await s.aclose()


@pytest.mark.asyncio
async def test_revoked_authority_prevents_admission():
    s,payloads,spoken=fixture()
    def revoked():raise ValueError('revoked')
    s.check=revoked
    turn(s,'turn.done','one','Doe iets')
    await asyncio.sleep(.02)
    assert s.closed and not payloads and not spoken
    await s.aclose()


@pytest.mark.asyncio
async def test_unknown_playback_is_not_reported_as_heard_or_interrupted():
    s,payloads,spoken=fixture()
    def say(text):
        return SimpleNamespace(task=asyncio.create_task(asyncio.sleep(0)),interrupted=True)
    s.renderer.say=say
    turn(s,'turn.done','one','Hoi')
    await settle(s)
    assert s.bridge.recent[-1]['playback_status']=='unconfirmed'
    assert s.bridge.recent[-1]['interrupted'] is False
    await s.aclose()

@pytest.mark.asyncio
async def test_early_core_answer_waits_for_renderer_readiness_and_can_be_stopped():
    s=CodexMediaSession({},None,lambda:None,render_only=True)
    s.voice=SimpleNamespace(say=AsyncMock(),close=AsyncMock())
    handle=s.say('Antwoord van de kern')
    await asyncio.sleep(0)
    s.voice.say.assert_not_awaited()
    s.interrupt()
    s.started.set()
    await asyncio.gather(handle.task,return_exceptions=True)
    s.voice.say.assert_not_awaited()
    await s.aclose()


@pytest.mark.asyncio
async def test_adjacent_asr_fragments_form_one_authoritative_request():
    s,payloads,spoken=fixture()
    turn(s,'turn.created','one')
    turn(s,'turn.done','one','Mijn testwoord is')
    await asyncio.sleep(.1)
    turn(s,'turn.created','two')
    await asyncio.sleep(.5)
    assert not payloads
    turn(s,'turn.done','two','paraplu')
    await settle(s)
    assert [p['text'] for p in payloads]==['Mijn testwoord is paraplu']
    turn(s,'turn.done','one','Mijn testwoord is')
    turn(s,'turn.done','two','paraplu')
    await settle(s)
    assert len(payloads)==1
    await s.aclose()


@pytest.mark.asyncio
async def test_empty_followup_transcript_releases_previous_fragment():
    s,payloads,spoken=fixture()
    turn(s,'turn.created','one')
    turn(s,'turn.done','one','Hoi')
    turn(s,'turn.created','noise')
    turn(s,'turn.done','noise','')
    await settle(s)
    assert [p['text'] for p in payloads]==['Hoi']
    assert spoken==['Kern: Hoi']
    await s.aclose()


@pytest.mark.asyncio
async def test_empty_final_replay_cannot_undo_explicit_stop():
    s,payloads,spoken=fixture()
    turn(s,'turn.created','one');turn(s,'turn.done','one','Hoi')
    turn(s,'turn.created','noise');turn(s,'turn.done','noise','')
    flush=s._input_flush
    s.interrupt()
    turn(s,'turn.done','noise','')
    assert s._input_flush is flush
    await settle(s)
    assert len(payloads)==1 and not spoken
    await s.aclose()
