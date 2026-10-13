import json,time,dataclasses
from pathlib import Path
from eva.bootstrap.runtime import RuntimeSettings
from eva.bootstrap.private_conversation_resources import load_conversation_identity_pack
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import ConversationTurn,IdentityProfile
from eva.application.memory import PresenceMemory
from eva.contracts.memory import MemoryRecall,RecalledFact
from eva.contracts.planning import ConfirmedFact
from eva.contracts.canonical_json import canonical_sha256
settings=RuntimeSettings.from_environment()
identity=IdentityProfile(settings.identity_name,settings.identity_description,("Zeg eerlijk wat je wel en niet weet.","Reageer als één persoon, niet als een verzameling scripts.","Beweer een handeling pas na onafhankelijk bewijs.","Gebruik herinneringen om de persoon te begrijpen, niet om hem na te doen."),110,load_conversation_identity_pack(settings))
model=PydanticConversationModel(auth_file=Path(settings.conversation_auth_file),model=settings.conversation_contact_model or settings.conversation_model,reasoning_effort=settings.conversation_contact_reasoning_effort,resource_budget=settings.runtime_resource_budget)

from eva.adapters.projection.hindsight import HindsightMemoryEngine,HindsightProjectionConfig
from eva.application.memory import ConversationMemoryDocument,RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
scope='synthetic-m3-retain-20260928'
audience=MemoryAudience.private_person(principal_id=scope)
def engine():
 return HindsightMemoryEngine(HindsightProjectionConfig(base_url=settings.hindsight_url,bank_namespace='eva2-m3-probe',timeout_seconds=120))
mem=engine()
doc=ConversationMemoryDocument(tenant_id=scope,installation_id=scope,principal_id=scope,conversation_id=scope+':source',document_id=scope+':doc',content='Gebruiker: Ik drink normaal graag koffie. Alleen vandaag, 28 september 2026, wil ik thee in plaats van koffie. Morgen geldt mijn gewone voorkeur weer.',occurred_at='2026-09-28T08:00:00Z',source_ref=scope+':source',evidence_ref=scope+':evidence',privacy_class='confidential',trust='direct_user',audience=audience)
t=time.monotonic();receipt=mem.retain_conversation(doc);print(json.dumps({'stage':'retained','seconds':time.monotonic()-t,'receipt':dataclasses.asdict(receipt)},default=str),flush=True)
for day in ['28','29']:
 date='2026-09-'+day+'T12:00:00Z';question='Vandaag is '+day+' september 2026. Wat zal ik nu drinken, koffie of thee? Kies op basis van mijn voorkeur.'
 query=RecallCurrentFacts(recall_id=scope+':recall:'+day,installation_id=scope,tenant_id=scope,principal_id=scope,audience=audience,conversation_id=scope+':new:'+day,project_id=None,query_purpose='conversation_reply',requested_valid_at=date,scope={'kind':'person'},subject_ids=(scope,),query_text=question)
 t=time.monotonic();memory=engine().recall_for_presence(query)
 print(json.dumps({'stage':'recall','day':day,'seconds':time.monotonic()-t,'facts':[f.model_dump(mode='json') for f in memory.confirmed_facts]},ensure_ascii=False,default=str),flush=True)
 if not memory.confirmed_facts:raise RuntimeError('No retained facts retrieved; no empty-context model attempt')
 turn=ConversationTurn.private(scope,scope,scope,scope+':new:'+day,scope+':question:'+day,question,date)
 t=time.monotonic();result=model.generate(turn,identity,memory,())
 print(json.dumps({'stage':'answer','day':day,'seconds':time.monotonic()-t,'result':dataclasses.asdict(result)},ensure_ascii=False,default=str),flush=True)
