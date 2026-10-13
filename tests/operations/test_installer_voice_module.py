import json
from datetime import UTC,datetime
from uuid import uuid4
import pytest
from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.bootstrap.dashboard_voice import DashboardVoicePolicy
from eva.contracts.canonical_json import canonical_json_bytes
from tools.installer_voice_module import prepare_voice_module


def arguments(tmp_path):
    identity=load_current_identity_pack(); path=tmp_path/'identity.json'
    path.write_bytes(canonical_json_bytes(identity))
    return dict(workspace=tmp_path,namespace='eva-test-voice',images={'voice-media':'sha256:'+'a'*64,'livekit':'sha256:'+'b'*64},
      selection={'home_assistant':{'url':'http://ha.invalid:8123'},'pipeline':{'id':'test-pipeline','name':'Test',
        'language':'nl','stt_engine':'stt.home_assistant_cloud','tts_engine':'tts.home_assistant_cloud','tts_language':'nl-NL'}},
      identifiers={k:str(uuid4()) for k in ('tenant','installation','principal','binding')},identity=identity,
      identity_file=path,core_url='http://127.0.0.1:46111',media_port=46112,livekit_port=46113,rtc_udp_port=46114,
      now=datetime(2026,10,3,tzinfo=UTC))


def test_preparation_binds_existing_policy_and_requires_real_images(tmp_path):
    args=arguments(tmp_path)
    bad={**args,'images':{}}
    with pytest.raises(ValueError):prepare_voice_module(**bad)
    assert not (tmp_path/'voice').exists()
    plan=prepare_voice_module(**args)
    policy=DashboardVoicePolicy.model_validate_json((tmp_path/'voice/policy/policy.json').read_bytes())
    assert str(policy.owner_principal_id)==args['identifiers']['principal']
    assert policy.pipeline_id=='test-pipeline' and policy.allowed_origin==plan['voice_url']
    assert plan['voice_url']=='http://127.0.0.1:46112'
    assert policy.expires_at=='2026-11-02T00:00:00Z'
    assert policy.privacy.raw_audio_retention=='none'
    assert not (tmp_path/'voice/runtime').exists()


def test_start_uses_materialized_credentials_loopback_rtc_and_core_readiness(tmp_path, monkeypatch):
    import io
    from tools.installer_voice_module import start_voice_module
    plan=prepare_voice_module(**arguments(tmp_path))
    env={'EVA_DASHBOARD_VOICE_POLICY_FILE':'/run/eva/voice/policy.json'};volumes=[{'type':'bind','source':plan['policy_directory'],'target':'/run/eva/voice','read_only':True}]
    for setting,name in [('EVA_DASHBOARD_ACCESS_TOKEN_FILE','owner-token'),
                         ('EVA_HOME_ASSISTANT_TOKEN_FILE','ha-token')]:
        path=tmp_path/'voice'/name
        if name!='policy.json':path.write_text('test-secret')
        destination='/run/core/'+name;env[setting]=destination
        volumes.append({'type':'bind','source':str(path),'target':destination,'read_only':True})
    commands=[];requests=[]
    def command(args):
        commands.append(args)
        if args[1]=='inspect':return json.dumps([
            {'Id':'media-id','Image':plan['images']['voice-media'],'State':{'Running':True}},
            {'Id':'livekit-id','Image':plan['images']['livekit'],'State':{'Running':True}}])
        if 'ps' in args:return 'media-id livekit-id'
        return ''
    def ready(url,timeout):
        requests.append(url)
        return io.BytesIO(b'{"ready":true}')
    monkeypatch.setattr('urllib.request.urlopen',ready)
    result=start_voice_module(plan,{'services':{'core':{'environment':env,'volumes':volumes}}},command=command)
    assert result['phase']=='ready' and result['physical_audio_tested'] is False
    assert requests==[plan['voice_url']+'/session/ready']
    runtime=tmp_path/'voice/runtime'
    rtc=json.loads((runtime/'livekit.json').read_bytes())['rtc']
    assert rtc['tcp_port']==0 and rtc['ips']['includes']==['127.0.0.1/32']
    assert rtc['interfaces']['includes']==['lo'] and rtc['use_external_ip'] is False
    media=json.loads((runtime/'media-config.json').read_bytes())
    assert media['public_livekit_url']=='ws://127.0.0.1:46113'
    assert media['cloud_relay'] is False and media['authority_sha256']==plan['policy_sha256']
    compose=json.loads((tmp_path/'voice/compose.json').read_bytes())
    assert all(row['network_mode']=='host' for row in compose['services'].values())
    assert len(commands)==3

    policy_mount=next(m for m in compose['services']['media']['volumes'] if m['target']=='/run/eva/voice')
    assert policy_mount['source']==plan['policy_directory']
    assert policy_mount['read_only'] is True
    # Atomic replacement and deletion resolve through the shared directory,
    # never through an old bound file inode.
    from pathlib import Path
    bound=Path(policy_mount['source'])/'policy.json'
    previous=bound.stat().st_ino
    replacement=bound.with_suffix('.replacement');replacement.write_text('{"state":"disabled"}')
    replacement.replace(bound)
    assert bound.stat().st_ino!=previous and json.loads(bound.read_bytes())['state']=='disabled'
    bound.unlink();assert not bound.exists()
