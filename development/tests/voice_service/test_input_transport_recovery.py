"""Failed native input is observable without autonomous reasoning or replay."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from eva.voice_service.codex_session import CodexVoiceSession
from test_single_core_voice import fixture, turn, settle


def notification(method, identity='app-turn', **values):
    params={'threadId':'our-thread', **values}
    if method=='error':params['turnId']=identity
    else:params['turn']={'id':identity, **values};params.pop('status',None);params.pop('error',None)
    return {'method':method,'params':params}


def transport(media):
    diagnostics=[]
    voice=CodexVoiceSession('unused',lambda:None,
        SimpleNamespace(diagnostic=lambda stage,**fields:diagnostics.append((stage,fields))),
        on_input_turn=media.input_transport_turn)
    voice.thread='our-thread'
    return voice,diagnostics


def failed(voice, identity='app-turn', retry=False):
    voice.input_turn_notification(notification('error',identity,willRetry=retry,error={
        'message':'PRIVATE text token=secret',
        'additionalDetails':'Bearer secret',
        'codexErrorInfo':{'httpConnectionFailed':{'httpStatusCode':403}}}))


@pytest.mark.asyncio
async def test_bound_terminal_error_prompts_once_then_same_listener_accepts_new_question():
    media,payloads,spoken=fixture();voice,logs=transport(media)
    original_bridge=media.bridge
    try:
        turn(media,'turn.created','lost')
        voice.input_turn_notification(notification('turn/started'))
        failed(voice)
        voice.input_turn_notification(notification('turn/completed',status='failed',error={'message':'same failure'}))
        await settle(media)
        assert not media.closed and media.bridge is original_bridge
        assert not media._input_open and not payloads and len(spoken)==1
        assert 'nog eens' in spoken[0]
        # Late transcript is not replayed after the user was asked to repeat.
        turn(media,'turn.done','lost','Late old question')
        turn(media,'turn.created','fresh');turn(media,'turn.done','fresh','Een nieuwe vraag')
        await settle(media)
        assert [p['text'] for p in payloads]==['Een nieuwe vraag']
        assert spoken[-1]=='Kern: Een nieuwe vraag' and len(spoken)==2
        encoded=json.dumps(logs)
        assert 'PRIVATE' not in encoded and 'secret' not in encoded
        assert logs[0][1]['http_status']==403
    finally:await media.aclose()


@pytest.mark.asyncio
async def test_failure_revokes_waiting_old_answer_before_releasing_orphan_input():
    media,payloads,spoken=fixture();voice,_=transport(media)
    entered=asyncio.Event();release=asyncio.Event();original=media.bridge.dispatch
    async def slow(*args,**kwargs):
        entered.set();await release.wait();return await original(*args,**kwargs)
    media.bridge.dispatch=slow
    try:
        turn(media,'turn.done','accepted','Eerste vraag');await entered.wait()
        turn(media,'turn.created','lost');voice.input_turn_notification(notification('turn/started'))
        release.set();await asyncio.sleep(.01)
        failed(voice);await settle(media)
        assert [p['text'] for p in payloads]==['Eerste vraag']
        assert not any(t=='Kern: Eerste vraag' for t in spoken)
        assert len(spoken)==1 and not media._input_open
    finally:release.set();await media.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['retry','foreign','already_admitted','explicit_stop','newer_input'])
async def test_unrelated_retry_or_retired_failure_does_not_revoke_new_work(kind):
    media,payloads,spoken=fixture();voice,_=transport(media)
    try:
        turn(media,'turn.created','old');voice.input_turn_notification(notification('turn/started'))
        if kind=='already_admitted':
            turn(media,'turn.done','old','Vraag');await settle(media)
        elif kind=='explicit_stop':media.interrupt()
        elif kind=='newer_input':turn(media,'turn.created','new')
        if kind=='foreign':
            message=notification('error',willRetry=False,error={});message['params']['threadId']='other';voice.input_turn_notification(message)
        else:failed(voice,retry=kind=='retry')
        if kind in {'retry','foreign'}:
            assert 'old' in media._input_open
            turn(media,'turn.done','old','Vraag')
        elif kind=='newer_input':
            assert 'new' in media._input_open
            turn(media,'turn.done','new','Nieuwe vraag')
        await settle(media)
        assert not any('nog eens' in t for t in spoken)
        assert not media.closed
        if kind=='newer_input':assert spoken==['Kern: Nieuwe vraag']
    finally:await media.aclose()


@pytest.mark.asyncio
async def test_actual_stdio_reader_forwards_bound_failed_completion_and_redacts_error():
    media,payloads,spoken=fixture();voice,logs=transport(media)
    turn(media,'turn.created','lost')
    reader=asyncio.StreamReader()
    for message in [notification('turn/started'),notification('turn/completed',status='failed',error={'message':'secret'})]:
        reader.feed_data(json.dumps(message).encode()+b'\n')
    reader.feed_eof();voice.process=SimpleNamespace(stdout=reader,returncode=0);voice.closing=True
    try:
        await voice.read();await settle(media)
        assert len(spoken)==1 and not payloads and not media._input_open
        assert 'secret' not in json.dumps(logs)
    finally:await media.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary',['stop','revoked'])
async def test_stop_or_authority_loss_before_repeat_offer_never_speaks(boundary):
    media,payloads,spoken=fixture();voice,_=transport(media)
    try:
        turn(media,'turn.created','lost');voice.input_turn_notification(notification('turn/started'))
        failed(voice)
        if boundary=='stop':media.interrupt()
        else:
            def denied():raise ValueError('Authority revoked')
            media.check=denied
        await settle(media)
        assert not spoken and not payloads
    finally:await media.aclose()


@pytest.mark.asyncio
async def test_terminal_failure_retires_bound_unadmitted_fragment_without_partial_execution():
    media,payloads,spoken=fixture();voice,_=transport(media)
    try:
        turn(media,'turn.created','open')
        # A finalized prefix is not yet an independently admitted question.
        media._input_fragments.append(('prefix','Een gedeelte',media._generation))
        voice.input_turn_notification(notification('turn/started'))
        failed(voice)
        await settle(media)
        assert not media._input_open and not media._input_fragments
        assert not payloads and len(spoken)==1
        turn(media,'turn.done','prefix','Een gedeelte')
        await settle(media)
        assert not payloads and len(spoken)==1
    finally:await media.aclose()
