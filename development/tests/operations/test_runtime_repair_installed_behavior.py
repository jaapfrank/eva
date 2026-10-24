"""Actual source/image readback in a disposable container; no family installation."""
from copy import deepcopy
import hashlib
import json
import os
import subprocess

import pytest

from eva.operations.component_evaluation import EvaluationConfig
from eva.operations.runtime_repair_application import verify_installed_repair
from eva.operations.runtime_repair_development import _put


@pytest.mark.skipif(not os.environ.get('EVA_RUNTIME_REPAIR_TEST_IMAGE'),reason='explicit local immutable image required')
def test_actual_container_source_and_image_behavior_are_checked_without_live_credentials(tmp_path):
    image=os.environ['EVA_RUNTIME_REPAIR_TEST_IMAGE'];path='application/channel_routes.py'
    def command(argv):return subprocess.check_output(argv).decode()
    identifier=command(['docker','run','--detach','--network','none','--read-only','--entrypoint','python',image,
        '-I','-c','import time;time.sleep(90)']).strip()
    try:
        raw=subprocess.check_output(['docker','exec',identifier,'python','-I','-c',
            'import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())','/opt/eva/src/eva/'+path])
        digest=hashlib.sha256(raw).hexdigest();identity='a'*64
        def case(name,value,expected):return {'id':name,'driver_source':
            'from eva.application.channel_routes import is_whatsapp_conversation\ndef run():\n    return is_whatsapp_conversation('+repr(value)+')\n',
            'expected':{'kind':'value','value':expected}}
        oracle={'contract':'EvaIndependentRuntimeRepairCases/v1','target_id':identity,'original_case_id':'valid',
            'cases':[case('valid','whatsapp:31612345678',True),case('other','telegram:1',False)]}
        oracle_ref=_put(tmp_path/'oracle.json',oracle)
        criteria=_put(tmp_path/'criteria.json',{'oracle':oracle_ref})
        target={'target_id':identity,'sources':{path:'b'*64},'criteria':criteria,
            'failure':{'observation':{'error_type_sha256':'c'*64}},
            'installation':{s:{'image':image,'configuration_sha256':'d'*64} for s in ('core','delegated-work-worker')}}
        candidate={'files':[{'path':path,'candidate_sha256':digest}]}
        release={'repair_target':target,'repair_candidate':candidate,
            'candidate_images':{s:image for s in target['installation']},'behavior_verifier':criteria}
        observation={'target':'candidate','images':release['candidate_images'],
            'containers':{s:identifier for s in target['installation']}}
        config=EvaluationConfig(image=image,command=('python',),deadline_seconds=30)
        proof=verify_installed_repair(observation,release=release,config=config,run=command)
        assert proof['verified'] is True and proof['external_actions_replayed'] is False
        assert proof['verification_scope']=='installed_container_source_and_isolated_original_behavior'
        assert all(v['source_hashes']=={path:digest} for v in proof['services'].values())
        wrong=deepcopy(release);wrong['repair_candidate']['files'][0]['candidate_sha256']='0'*64
        with pytest.raises(ValueError,match='live container source differs'):
            verify_installed_repair(observation,release=wrong,config=config,run=command)
        oracle['cases'][0]['expected']['value']=False
        wrong_oracle=_put(tmp_path/'wrong-oracle.json',oracle)
        wrong_criteria=_put(tmp_path/'wrong-criteria.json',{'oracle':wrong_oracle})
        wrong=deepcopy(release);wrong['repair_target']['criteria']=wrong_criteria
        assert verify_installed_repair(observation,release=wrong,config=config,run=command)['verified'] is False
    finally:subprocess.run(['docker','rm','--force',identifier],stdout=subprocess.DEVNULL,check=True)
