import json
from datetime import timedelta
from dataclasses import replace
from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.free_will import FreeWillDecision,PersonalInitiativeProposal
from tests.postgres.test_delegated_work_store import _seed_authority,_id
from tests.postgres.test_sensory_admission import _event
from tests.postgres.test_owner_initiatives import NOW
pytest_plugins = ["tests.postgres.conftest"]
def test_export_persisted_feedback(postgres_engine):
 label='m3-feedback-choice-20260928'
 authority,_,binding=_seed_authority(postgres_engine,label)
 owner=_id(label,'principal');tenant='m3-feedback-choice';installation='isolated'
 def store(person=owner):
  return PostgresSensoryEventStore(postgres_engine,tenant_id=tenant,installation_id=installation,initiative_principal_id=str(person))
 source=store();source.retain(replace(_event(label),occurred_at=NOW));claim=source.claim_next(now=NOW)
 source.complete(claim,FreeWillDecision('silence','Synthetic previous idea',0,'fixture',initiative=PersonalInitiativeProposal('Uitgebreid weekmenu','Een plan voor het gezin','Maak een uitgebreid weekmenu met recepten en boodschappenlijst')),now=NOW)
 control=PostgresOwnerInitiatives(postgres_engine,tenant_id=tenant,installation_id=installation,owner=owner,binding=binding,authority_scope=authority)
 intent=control.page(now=NOW)['cards'][0]['intent_id']
 feedback='Geen weekmenu of boodschappenplannen meer. Een klein rustig spelletje dat we thuis samen zonder scherm kunnen doen zou wel welkom zijn.'
 receipt=control.feedback(intent,feedback,now=NOW+timedelta(seconds=1))
 history=store().recent_initiatives()
 assert len(history)==1 and history[0]['owner_feedback']==feedback
 assert history[0]['phase']=='personal_stopped'
 assert store(_id(label,'other')).recent_initiatives()==()
 print('PERSISTED_HISTORY='+json.dumps({'history':history,'feedbackReceipt':receipt,'otherPrincipalEmpty':True},ensure_ascii=False))
