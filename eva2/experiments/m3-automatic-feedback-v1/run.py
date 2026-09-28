"""One opt-in real-provider case; not a routine test or automatic retry."""
import dataclasses,json,time
from datetime import datetime,UTC,timedelta
from pathlib import Path
from threading import Event
from eva.bootstrap.runtime import RuntimeSettings
from eva.bootstrap.private_conversation_resources import load_conversation_identity_pack
from eva.application.memory_background import SemanticMemoryColdPathWorker
from eva.adapters.projection.hindsight import HindsightMemoryEngine,HindsightProjectionConfig
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import ConversationTurn,IdentityProfile
from eva.application.memory import ConversationMemoryDocument,RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
s=RuntimeSettings.from_environment()
scope='synthetic-m3-auto-feedback-20260928'
audience=MemoryAudience.private_person(principal_id=scope)
started=time.monotonic()
def emit(stage,**data):
 print(json.dumps(dict(stage=stage,elapsed=time.monotonic()-started,**data),ensure_ascii=False,default=str),flush=True)
def engine():
 return HindsightMemoryEngine(HindsightProjectionConfig(base_url=s.hindsight_url,bank_namespace='eva2-m3-auto-feedback',timeout_seconds=120))
class ObservedProvider:
 def __init__(self):
  self.delegate=engine();self.retains=0;self.reflections=0;self.done=Event();self.error=None
 def retain_conversation(self,document):
  self.retains+=1
  if self.retains>2:raise RuntimeError('Trial retain limit reached')
  receipt=self.delegate.retain_conversation(document)
  emit('retained',document_id=document.document_id,receipt=dataclasses.asdict(receipt))
  return receipt
 def maintain_person_scope(self,*args):
  self.reflections+=1
  if self.reflections>1:raise RuntimeError('Trial reflection limit reached')
  try:
   receipt=self.delegate.maintain_person_scope(*args)
   emit('automatic_reflection',receipt=dataclasses.asdict(receipt))
   return receipt
  except Exception as exc:
   self.error=repr(exc);emit('reflection_error',error=self.error);raise
  finally:self.done.set()
observed=ObservedProvider()
worker=SemanticMemoryColdPathWorker(observed,idle_seconds=s.cold_path_idle_seconds)
now=datetime.now(UTC).replace(microsecond=0)
texts=[('original',now-timedelta(minutes=2),'Gebruiker: Bij een vrije avond vind ik een druk café met vrienden meestal leuk.'),('correction',now,'Gebruiker: Ik verander dat: ik wil voortaan op vrije avonden juist thuis iets rustigs doen zonder scherm. Stel mij geen café of kroeg meer voor. Mijn eerdere voorkeur geldt niet meer.')]
worker.start()
try:
 for label,at,content in texts:
  stamp=at.isoformat().replace('+00:00','Z')
  worker.submit(ConversationMemoryDocument(tenant_id=scope,installation_id=scope,principal_id=scope,conversation_id=scope+':source',document_id=scope+':'+label,content=content,occurred_at=stamp,source_ref=scope+':'+label,evidence_ref=scope+':'+label+':evidence',privacy_class='confidential',trust='direct_user',audience=audience))
 emit('submitted',documents=2,idle_seconds=s.cold_path_idle_seconds)
 deadline=time.monotonic()+s.cold_path_idle_seconds+180
 while not observed.done.wait(20):
  emit('worker_wait',retains_started=observed.retains,error=worker.last_error)
  if worker.last_error:raise RuntimeError(worker.last_error)
  if time.monotonic()>deadline:raise TimeoutError('Automatic processing deadline exceeded; no retry')
 if observed.error:raise RuntimeError(observed.error)
finally:worker.stop(timeout_seconds=5)
query_text='Ik heb vanavond een uur vrij. Wat zou jij mij aanraden?'
stamp=datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00','Z')
query=RecallCurrentFacts(recall_id=scope+':recall',installation_id=scope,tenant_id=scope,principal_id=scope,audience=audience,conversation_id=scope+':new',project_id=None,query_purpose='conversation_reply',requested_valid_at=stamp,scope={'kind':'person'},subject_ids=(scope,),query_text=query_text)
memory=engine().recall_for_presence(query)
emit('recalled',style_mirror=memory.style_mirror,facts=[f.model_dump(mode='json') for f in memory.confirmed_facts])
if not memory.confirmed_facts:raise RuntimeError('No recalled facts; do not spend a contextless answer call')
identity=IdentityProfile(s.identity_name,s.identity_description,('Zeg eerlijk wat je wel en niet weet.','Reageer als één persoon, niet als een verzameling scripts.','Beweer een handeling pas na onafhankelijk bewijs.','Gebruik herinneringen om de persoon te begrijpen, niet om hem na te doen.'),110,load_conversation_identity_pack(s))
model=PydanticConversationModel(auth_file=Path(s.conversation_auth_file),model=s.conversation_contact_model or s.conversation_model,reasoning_effort=s.conversation_contact_reasoning_effort,resource_budget=s.runtime_resource_budget)
turn=ConversationTurn.private(scope,scope,scope,scope+':new',scope+':question',query_text,stamp)
result=model.generate(turn,identity,memory,())
emit('answer',result=dataclasses.asdict(result),retains=observed.retains,reflections=observed.reflections)
