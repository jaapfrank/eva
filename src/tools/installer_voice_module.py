"""Prepare the existing optional media/LiveKit services for one new local Eva."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from datetime import UTC, datetime, timedelta
from eva.bootstrap.dashboard_voice import DashboardVoicePolicy
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.operations.installer_identity_storage import write_private_once


def _write(path, content, *, runtime=False):
    write_private_once(path, content)
    if runtime and os.geteuid() == 0: os.chown(path, 65532, 65532)


def prepare_voice_module(*, workspace, namespace, images, selection, identifiers, identity,
                         identity_file, core_url, media_port, livekit_port, rtc_udp_port, now=None):
    for role in ('voice-media','livekit'):
        if role not in images or not __import__('re').fullmatch(r'sha256:[0-9a-f]{64}', images[role]):
            raise ValueError('selected voice requires existing pinned media and LiveKit images')
    ports = (media_port,livekit_port,rtc_udp_port)
    if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports) or len(set(ports)) != 3:
        raise ValueError('distinct available voice ports required')
    moment = (now or datetime.now(UTC)).replace(microsecond=0)
    pipeline = selection['pipeline']
    from eva.operations.installer_voice_selection import require_supported_privacy
    require_supported_privacy(pipeline)
    origin = f'http://127.0.0.1:{media_port}'
    policy = DashboardVoicePolicy.model_validate_json(canonical_json_bytes({
        'contract':'DashboardVoicePolicy/v1','schema_version':1,'policy_id':namespace+'-voice','policy_version':1,
        'state':'enabled','tenant_id':identifiers['tenant'],'installation_id':identifiers['installation'],
        'dashboard_tenant_id':identifiers['tenant'],'dashboard_installation_id':identifiers['installation'],
        'owner_principal_id':identifiers['principal'],'principal_binding_id':identifiers['binding'],
        'identity_sha256':canonical_sha256(identity),'allowed_origin':origin,'provider':'home_assistant',
        'endpoint_url':selection['home_assistant']['url'],'pipeline_id':pipeline['id'],
        'pipeline_language':pipeline['language'],'stt_engine':pipeline['stt_engine'],
        'tts_engine':pipeline['tts_engine'],'tts_language':pipeline['tts_language'],
        'privacy':{'contract':'VoicePrivacy/v1','schema_version':1,'partial_transcript_retention':'ephemeral',
            'raw_audio_retention':'none','external_provider_audio_retention':'disabled',
            'final_transcript_admission':'conversation_admission_required'},
        'privacy_evidence_ref':'installer-voice-selection:'+canonical_sha256(selection),
        'effective_at':moment.isoformat().replace('+00:00','Z'),
        'expires_at':(moment+timedelta(days=30)).isoformat().replace('+00:00','Z')}))
    root=Path(workspace)/'voice'
    root.mkdir(mode=0o700,exist_ok=False)
    raw=canonical_json_bytes(policy.model_dump(mode='json'))
    policy_dir=root/'policy';policy_dir.mkdir(mode=0o700)
    _write(policy_dir/'policy.json',raw)
    plan={'contract':'EvaInstallerVoiceModule/v1','namespace':namespace,'workspace':str(root),
        'images':{key:images[key] for key in ('voice-media','livekit')},
        'policy_directory':str(policy_dir),'policy_file':str(policy_dir/'policy.json'),'policy_sha256':hashlib.sha256(raw).hexdigest(),
        'identity_file':str(identity_file),'identity_sha256':hashlib.sha256(Path(identity_file).read_bytes()).hexdigest(),
        'core_url':core_url,'voice_url':origin,'media_port':media_port,'livekit_port':livekit_port,
        'rtc_udp_port':rtc_udp_port,'pipeline':pipeline,
        'ha_url':selection['home_assistant']['url'],'expires_at':policy.expires_at}
    _write(root/'plan.json',canonical_json_bytes(plan))
    return plan


def start_voice_module(plan, graph, *, command=None):
    """Start only this prepared optional module after the fresh core is healthy.

    Failures retain the module graph and phases; the caller retains core/data.
    No voice session, microphone, model or speech-provider operation is opened.
    """
    run=command or (lambda args: subprocess.check_output(args, text=True, stderr=subprocess.PIPE))
    root=Path(plan['workspace'])
    if json.loads((root/'plan.json').read_bytes()) != plan: raise ValueError('voice plan changed')
    if hashlib.sha256(Path(plan['policy_file']).read_bytes()).hexdigest()!=plan['policy_sha256']:
        raise ValueError('voice policy changed')
    core=graph['services']['core']
    def source(setting):
        destination=core['environment'][setting]
        matches=[m for m in core['volumes'] if m.get('type')=='bind' and m.get('target')==destination and m.get('read_only') is True]
        if len(matches)!=1: raise ValueError('voice needs exact materialized core credential')
        return matches[0]['source']
    policy_target='/run/eva/voice'
    policy_mounts=[m for m in core['volumes'] if m.get('type')=='bind' and m.get('target')==policy_target and m.get('read_only') is True]
    if len(policy_mounts)!=1 or core['environment'].get('EVA_DASHBOARD_VOICE_POLICY_FILE')!=policy_target+'/policy.json':
        raise ValueError('voice needs exact materialized policy directory')
    policy_directory=Path(policy_mounts[0]['source'])
    paths={policy_target:str(policy_directory),
        '/run/eva/owner-token':source('EVA_DASHBOARD_ACCESS_TOKEN_FILE'),
        '/run/eva/ha-token':source('EVA_HOME_ASSISTANT_TOKEN_FILE'),
        '/run/eva/identity.json':plan['identity_file']}
    if hashlib.sha256((policy_directory/'policy.json').read_bytes()).hexdigest()!=plan['policy_sha256']:
        raise ValueError('runtime voice policy changed')
    runtime=root/'runtime';runtime.mkdir(mode=0o700,exist_ok=False)
    if os.geteuid()==0: os.chown(runtime,65532,65532)
    # Copy identity into a private runtime-owned file; package input is not assumed readable.
    identity=Path(plan['identity_file']).read_bytes()
    if hashlib.sha256(identity).hexdigest()!=plan['identity_sha256']:raise ValueError('voice identity changed')
    _write(runtime/'identity.json',identity,runtime=True);paths['/run/eva/identity.json']=str(runtime/'identity.json')
    key='eva'+secrets.token_hex(12);secret=secrets.token_urlsafe(48)
    _write(runtime/'livekit-secret',secret.encode(),runtime=True)
    livekit={'port':plan['livekit_port'],'bind_addresses':['127.0.0.1'],
        'rtc':{'tcp_port':0,'udp_port':plan['rtc_udp_port'],
               'use_external_ip':False,'node_ip':'127.0.0.1','enable_loopback_candidate':True,
               'interfaces':{'includes':['lo']},'ips':{'includes':['127.0.0.1/32']}},'keys':{key:secret}}
    _write(runtime/'livekit.json',canonical_json_bytes(livekit),runtime=True)
    pipeline=plan['pipeline']
    config={'owner_token_file':'/run/eva/owner-token','ha_token_file':'/run/eva/ha-token',
        'livekit_api_key':key,'livekit_secret_file':'/run/eva/runtime/livekit-secret',
        'core_url':plan['core_url'],'ha_url':plan['ha_url'],'pipeline_id':pipeline['id'],
        'stt_engine':pipeline['stt_engine'],'tts_engine':pipeline['tts_engine'],'language':pipeline['language'],
        'tts_language':pipeline['tts_language'],'tts_voice':pipeline.get('tts_voice'),
        'authority_file':'/run/eva/voice/policy.json','authority_sha256':plan['policy_sha256'],
        'identity_file':'/run/eva/identity.json','identity_sha256':plan['identity_sha256'],
        'local_browser_access':True,'origin':plan['voice_url'],'cloud_relay':False,
        'livekit_url':f"ws://127.0.0.1:{plan['livekit_port']}",
        'public_livekit_url':f"ws://127.0.0.1:{plan['livekit_port']}",
        'client_js':'/opt/eva/voice-assets/livekit-client.js'}
    _write(runtime/'media-config.json',canonical_json_bytes(config),runtime=True)
    def mount(src,dst):return {'type':'bind','source':str(src),'target':dst,'read_only':True,'bind':{'create_host_path':False}}
    base={'network_mode':'host','user':'65532:65532','read_only':True,'cap_drop':['ALL'],
          'security_opt':['no-new-privileges:true'],'restart':'unless-stopped','tmpfs':['/tmp']}
    services={
      'livekit':{**base,'image':plan['images']['livekit'],'pull_policy':'never',
        'command':['--config','/run/eva/livekit.json'],'volumes':[mount(runtime/'livekit.json','/run/eva/livekit.json')]},
      'media':{**base,'image':plan['images']['voice-media'],'pull_policy':'never',
        'environment':{'EVA_VOICE_CONFIG':'/run/eva/runtime/media-config.json'},
        'entrypoint':['/usr/local/bin/python'],'command':['-m','uvicorn','eva.voice_service.server:create_app','--factory',
           '--host','127.0.0.1','--port',str(plan['media_port']),'--no-access-log'],
        'volumes':[mount(runtime,'/run/eva/runtime'),*[mount(src,dst) for dst,src in paths.items()]]}}
    compose={'name':plan['namespace']+'-voice','services':services}
    _write(root/'compose.json',canonical_json_bytes(compose))
    receipt={'contract':'EvaInstallerVoiceStart/v1','phase':'prepared','voice_url':plan['voice_url'],
             'expires_at':plan['expires_at'],'physical_audio_tested':False}
    from eva.operations.installer_identity_storage import atomic_write_private
    def save():atomic_write_private(root/'start-receipt.json',canonical_json_bytes(receipt))
    save()
    try:
        run(['docker','compose','-f',str(root/'compose.json'),'up','-d','--pull','never'])
        receipt['phase']='containers_started';save()
        ids=run(['docker','compose','-f',str(root/'compose.json'),'ps','-q']).split()
        rows=json.loads(run(['docker','inspect',*ids])) if len(ids)==2 else []
        if len(rows)!=2 or any(row['State'].get('Running') is not True for row in rows):
            raise ValueError('voice containers not running')
        if sorted(row['Image'] for row in rows)!=sorted(plan['images'].values()):
            raise ValueError('voice container image differs')
        receipt['containers']=[{'id':row['Id'],'image':row['Image']} for row in rows]
        import urllib.request
        deadline=time.monotonic()+30
        while True:
            try:
                with urllib.request.urlopen(plan['voice_url']+'/session/ready',timeout=2) as response:
                    ready=json.load(response)
                if ready.get('ready') is True:break
            except Exception:
                if time.monotonic()>=deadline:raise ValueError('voice core readiness unavailable') from None
            if time.monotonic()>=deadline:raise ValueError('voice core readiness unavailable')
            time.sleep(.25)
        receipt['phase']='ready';save();return receipt
    except Exception as error:
        receipt['failure_type']=type(error).__name__;save();raise
