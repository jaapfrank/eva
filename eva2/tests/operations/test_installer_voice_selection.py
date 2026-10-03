import asyncio
import json
from types import SimpleNamespace
import pytest
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_secret_store import InstallerSecretStore
from eva.operations.installer_provider_configuration import InstallerProviderConfigurationStore
from eva.operations.installer_voice_selection import voice_inventory, confirm_voice, load_voice_selection, _save

@pytest.fixture
def selected(tmp_path):
    for name in ('caps','secrets','config','evidence'): (tmp_path/name).mkdir(mode=0o700)
    caps=InstallerCapabilityWorkflow(tmp_path/'caps'); secrets=InstallerSecretStore(tmp_path/'secrets')
    config=InstallerProviderConfigurationStore(tmp_path/'config')
    caps.select(step_id='home_assistant',wanted=True);caps.select(step_id='voice',wanted=True)
    secrets.store(slot_id='home_assistant',secret='synthetic-only-ha-credential')
    c=config.configure(slot_id='home_assistant',value='http://ha.invalid:8123')
    secret=next(s for s in secrets.status() if s.slot_id=='home_assistant')
    verifier=SimpleNamespace(capability_workflow=caps,secret_store=secrets,evidence_workspace=tmp_path/'evidence')
    digest=_save(verifier,{'contract':'EvaInstallerProviderVerificationReceipt/v4','slot_id':'home_assistant',
        'outcome':'ready','reason_code':'verified','configuration_sha256':c.configuration_sha256,
        'secret_reference':secret.reference,'secret_generation':secret.generation})
    caps.record_verification(step_id='home_assistant',outcome='ready',evidence_sha256=digest)
    return verifier,config


def test_multiple_pipelines_require_exact_confirmed_choice_and_no_audio(selected):
    verifier,store=selected
    async def discover(url, token):
        assert url=='http://ha.invalid:8123' and token=='synthetic-only-ha-credential'
        return [dict(id=str(i),name=f'Pipeline {i}',language='nl',stt_engine='stt.home_assistant_cloud',tts_engine='tts.home_assistant_cloud',tts_language='nl-NL') for i in (1,2)]
    inventory=asyncio.run(voice_inventory(verifier,store,discover))
    assert len(inventory['pipelines'])==2 and 'Cloud' in inventory['pipelines'][0]['processing']
    with pytest.raises(ValueError):confirm_voice(verifier,store,inventory_sha256=inventory['inventory_sha256'],pipeline_id='2',allow_provider_audio=False)
    digest=confirm_voice(verifier,store,inventory_sha256=inventory['inventory_sha256'],pipeline_id='2',allow_provider_audio=True)
    plan={'capabilities':[{'step_id':'voice','state':'ready','evidence_sha256':digest}]}
    assert load_voice_selection(plan,verifier,store)['pipeline']['id']=='2'
    store.configure(slot_id='home_assistant',value='http://changed.invalid:8123')
    with pytest.raises(ValueError):load_voice_selection(plan,verifier,store)


def test_skipped_voice_never_discovers_provider(selected):
    verifier,store=selected
    verifier.capability_workflow.select(step_id='voice',wanted=False)
    async def forbidden(*args):pytest.fail('skipped voice contacted HA')
    with pytest.raises(ValueError):asyncio.run(voice_inventory(verifier,store,forbidden))


def test_unknown_provider_cannot_be_promoted_to_verified_privacy(selected):
    verifier,store=selected
    async def discover(*args):
        return [dict(id='unknown',name='Other',language='nl',stt_engine='other',tts_engine='other',tts_language='nl')]
    inventory=asyncio.run(voice_inventory(verifier,store,discover))
    with pytest.raises(ValueError,match='bewaarbeleidbasis'):
        confirm_voice(verifier,store,inventory_sha256=inventory['inventory_sha256'],pipeline_id='unknown',allow_provider_audio=True)
    assert verifier.capability_workflow.status().step('voice').state=='selected'


def test_discovery_rejects_redirect_before_authentication(monkeypatch):
    from eva.operations.installer_voice_selection import discover_pipelines
    attempts=[]
    class FakeConnect:
        def __init__(self,address,**kwargs):attempts.append(address)
        async def __aenter__(self):
            error=ValueError('redirected handshake')
            outcome=self.process_redirect(error)
            assert outcome is error
            raise outcome
        async def __aexit__(self,*args):pass
    monkeypatch.setattr('websockets.asyncio.client.connect',FakeConnect)
    with pytest.raises(ValueError,match='redirected handshake'):
        asyncio.run(discover_pipelines('https://ha.invalid','must-not-be-sent'))
    assert attempts==['wss://ha.invalid/api/websocket']


def test_ready_voice_can_be_rediscovered_and_skipped_after_stale_ha(selected):
    verifier,store=selected
    async def discover(*args):
        return [dict(id='one',name='One',language='nl',stt_engine='stt.home_assistant_cloud',tts_engine='tts.home_assistant_cloud',tts_language='nl')]
    inventory=asyncio.run(voice_inventory(verifier,store,discover))
    confirm_voice(verifier,store,inventory_sha256=inventory['inventory_sha256'],pipeline_id='one',allow_provider_audio=True)
    store.configure(slot_id='home_assistant',value='http://changed.invalid:8123')
    with pytest.raises(ValueError,match='current verified'):
        asyncio.run(voice_inventory(verifier,store,discover))
    assert verifier.capability_workflow.status().step('voice').state=='blocked'
    verifier.capability_workflow.select(step_id='voice',wanted=False)
    assert verifier.capability_workflow.status().step('voice').state=='skipped'
