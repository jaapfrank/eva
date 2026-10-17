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
        # An already-finished task can make gather return synchronously before
        # its done callback removes it from the session's owned task set.
        await asyncio.sleep(0)


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
    async def delayed(text,key,**kwargs):
        entered.set();await release.wait()
        return await original(text,key,**kwargs)
    s.bridge.dispatch=delayed
    turn(s,'turn.done','one','Begin onderzoek')
    await entered.wait()
    if interrupt=='speech':
        turn(s,'turn.created','two')
        turn(s,'turn.done','two','Een nieuwe vraag')
    else:s.interrupt()
    release.set();await settle(s)
    assert payloads[0]['text']=='Begin onderzoek'
    assert not any(value=='Kern: Begin onderzoek' for value in spoken)
    assert len(payloads)==len(s.bridge.results)==(2 if interrupt=='speech' else 1)
    if interrupt=='speech':assert spoken==['Kern: Een nieuwe vraag']
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


@pytest.mark.asyncio
async def test_explicit_core_update_is_spoken_before_final_without_blocking_core():
    s,payloads,spoken=fixture();waiting=asyncio.Event();release=asyncio.Event()
    async def events(payload):
        yield {'type':'delta','text':'Unapproved draft'}
        yield {'type':'spoken_update','text':'Het eerste deel staat vast.'}
        yield {'type':'spoken_update','text':'Duplicate update must not speak.'}
        waiting.set();await release.wait()
        yield {'type':'final','reply':{'text':'Dit is het vervolg.'}}
    s.bridge.backend.events=events
    task=asyncio.create_task(s.core_turn('Vertel verder','test',0))
    await waiting.wait();await asyncio.sleep(0)
    assert spoken==['Het eerste deel staat vast.'] and not task.done()
    release.set();await task
    assert spoken==['Het eerste deel staat vast.','Dit is het vervolg.']
    assert [r['text'] for r in s.bridge.recent]==['Vertel verder',*spoken]
    await s.aclose()


@pytest.mark.asyncio
async def test_interruption_suppresses_update_and_final_but_core_finishes():
    s,payloads,spoken=fixture();waiting=asyncio.Event();release=asyncio.Event()
    async def events(payload):
        waiting.set();await release.wait()
        yield {'type':'spoken_update','text':'Dit mag niet meer klinken.'}
        yield {'type':'final','reply':{'text':'Ook dit niet.'}}
    s.bridge.backend.events=events
    task=asyncio.create_task(s.core_turn('Werk verder','test',0))
    await waiting.wait();s.interrupt();release.set();await task
    assert not spoken and len(s.bridge.results)==1
    await s.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('core_finishes_first', [False, True])
async def test_empty_noise_preserves_pending_unspoken_core_answer(core_finishes_first):
    s,payloads,spoken=fixture();entered=asyncio.Event();release=asyncio.Event()
    original=s.bridge.dispatch
    async def delayed(text,key,**kwargs):
        entered.set();await release.wait()
        return await original(text,key,**kwargs)
    s.bridge.dispatch=delayed
    try:
        turn(s,'turn.created','question');turn(s,'turn.done','question','Eerste vraag')
        await asyncio.wait_for(entered.wait(),1)
        turn(s,'turn.created','noise')
        if core_finishes_first:
            release.set()
            async with asyncio.timeout(1):
                while not s.bridge.results:await asyncio.sleep(0)
            assert not spoken
        turn(s,'turn.done','noise','')
        release.set();await asyncio.wait_for(settle(s),1)
        assert [p['text'] for p in payloads]==['Eerste vraag']
        assert spoken==['Kern: Eerste vraag']
        assert not s.closed
    finally:
        release.set();await s.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('revocation', ['words','stop','clear_audio'])
@pytest.mark.parametrize('core_finishes_first', [False, True])
async def test_empty_noise_cannot_restore_answer_after_new_words_or_stop(revocation,core_finishes_first):
    s,payloads,spoken=fixture();entered=asyncio.Event();release=asyncio.Event()
    s.renderer.stop_current_audio=lambda:None
    original=s.bridge.dispatch
    async def delayed(text,key,**kwargs):
        entered.set();await release.wait()
        return await original(text,key,**kwargs)
    s.bridge.dispatch=delayed
    try:
        turn(s,'turn.created','question');turn(s,'turn.done','question','Eerste vraag')
        await asyncio.wait_for(entered.wait(),1)
        turn(s,'turn.created','noise')
        if core_finishes_first:
            release.set()
            async with asyncio.timeout(1):
                while not s.bridge.results:await asyncio.sleep(0)
        if revocation=='words':
            turn(s,'turn.created','correction')
            turn(s,'turn.done','correction','Nieuwe vraag')
        elif revocation=='stop':s.interrupt()
        else:s.stop_current_audio()
        turn(s,'turn.done','noise','')
        release.set();await asyncio.wait_for(settle(s),2)
        assert payloads[0]['text']=='Eerste vraag'
        assert spoken==(['Kern: Nieuwe vraag'] if revocation=='words' else [])
        assert not s.closed
    finally:
        release.set();await s.aclose()


@pytest.mark.asyncio
async def test_empty_noise_never_replays_an_already_offered_answer():
    s,payloads,spoken=fixture();offered=asyncio.Event();playing=[]
    def say(text):
        spoken.append(text);offered.set()
        handle=SimpleNamespace(task=asyncio.create_task(asyncio.Event().wait()),interrupted=True)
        playing.append(handle)
        return handle
    s.renderer.say=say
    s.renderer.interrupt=lambda:[handle.task.cancel() for handle in playing]
    try:
        turn(s,'turn.created','question');turn(s,'turn.done','question','Eerste vraag')
        await asyncio.wait_for(offered.wait(),1)
        turn(s,'turn.created','noise');turn(s,'turn.done','noise','')
        await asyncio.wait_for(settle(s),1)
        assert spoken==['Kern: Eerste vraag'] and len(payloads)==1
        assert playing[0].task.cancelled() and not s.closed
    finally:await s.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('noise_during', ['core','playback'])
@pytest.mark.parametrize('transcript', ['', 'Wacht, nog een vraag'])
async def test_farewell_waits_for_provisional_input_and_closes_only_after_empty_noise(noise_during,transcript):
    s,payloads,spoken=fixture();entered=asyncio.Event();release=asyncio.Event()
    offered=asyncio.Event();played=asyncio.Event();playing=[]
    original=s.bridge.dispatch
    async def delayed(text,key,**kwargs):
        entered.set();await release.wait()
        return await original(text,key,**kwargs)
    def say(text):
        spoken.append(text);offered.set()
        handle=SimpleNamespace(task=asyncio.create_task(played.wait()),interrupted=False)
        playing.append(handle)
        return handle
    s.bridge.dispatch=delayed;s.renderer.say=say
    s.renderer.interrupt=lambda:[handle.task.cancel() for handle in playing]
    try:
        turn(s,'turn.created','bye');turn(s,'turn.done','bye','Dankje doei')
        await asyncio.wait_for(entered.wait(),1)
        if noise_during=='playback':
            release.set();await asyncio.wait_for(offered.wait(),1)
        turn(s,'turn.created','noise')
        release.set()
        async with asyncio.timeout(1):
            while not s.bridge.results:await asyncio.sleep(0)
        for _ in range(10):await asyncio.sleep(0)
        assert not s.closed
        if noise_during=='core':assert not spoken
        turn(s,'turn.done','noise',transcript)
        played.set();await asyncio.wait_for(settle(s),2)
        assert s.closed is (not transcript)
        assert spoken.count('Kern: Dankje doei')==(1 if not transcript or noise_during=='playback' else 0)
        if transcript:assert spoken[-1]=='Kern: '+transcript
    finally:
        release.set();played.set();await s.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('noise_settled_before_update', [False,True])
@pytest.mark.parametrize('outcome', ['empty','words','stop'])
async def test_unoffered_core_update_survives_only_empty_noise(noise_settled_before_update,outcome):
    s,payloads,spoken=fixture();entered=asyncio.Event();release=asyncio.Event()
    updated=asyncio.Event()
    async def events(payload):
        if payload['text']=='Nieuwe vraag':
            yield {'type':'final','reply':{'text':'Nieuw antwoord.'}}
            return
        entered.set();await release.wait()
        yield {'type':'spoken_update','text':'Het eerste deel staat vast.'}
        updated.set()
        yield {'type':'final','reply':{'text':'Dit is het vervolg.'}}
    s.bridge.backend.events=events
    task=s.task(s.core_turn('Vertel verder','test',0))
    try:
        await asyncio.wait_for(entered.wait(),1)
        turn(s,'turn.created','noise')
        def resolve():
            if outcome=='words':
                turn(s,'turn.created','next');turn(s,'turn.done','next','Nieuwe vraag')
            elif outcome=='stop':s.interrupt()
            turn(s,'turn.done','noise','')
        if noise_settled_before_update:resolve()
        release.set();await asyncio.wait_for(updated.wait(),1)
        if not noise_settled_before_update:
            assert not spoken
            resolve()
        await asyncio.wait_for(settle(s),2)
        expected=(['Het eerste deel staat vast.','Dit is het vervolg.'] if outcome=='empty'
                  else ['Nieuw antwoord.'] if outcome=='words' else [])
        assert spoken==expected
        assert not task.cancelled() and not s.closed
    finally:
        release.set();await s.aclose()


@pytest.mark.asyncio
async def test_session_close_cancels_pending_response_waiting_for_noise():
    s,payloads,spoken=fixture()
    try:
        turn(s,'turn.created','noise')
        task=s.task(s.core_turn('Eerste vraag','test',0))
        async with asyncio.timeout(1):
            while not s.bridge.results:await asyncio.sleep(0)
        assert not spoken and not task.done()
        await asyncio.wait_for(s.aclose(),1)
        assert task.cancelled() and s.closed and not spoken
    finally:await s.aclose()
