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
scope='synthetic-m3-style-20260928'
audience=MemoryAudience.private_person(principal_id=scope)
def engine():return HindsightMemoryEngine(HindsightProjectionConfig(base_url=settings.hindsight_url,bank_namespace='eva2-m3-style',timeout_seconds=120))
original='Gebruiker: Bij praktische vragen heb ik graag een uitgebreid antwoord met genummerde stappen en een uitleg per stap. Dat is mijn vaste communicatievoorkeur.'
correction='Gebruiker: Ik verander mijn communicatievoorkeur: voortaan wil ik korte, vriendelijke antwoorden van hooguit 40 woorden zonder genummerde lijst. Mijn eerdere voorkeur voor uitgebreide uitleg vervalt. Zeg niet telkens dat je een AI bent of geen lichaam hebt.'
for stage,hour,content in [('before','10',original),('after','11',original+'\n'+correction)]:
 date='2026-09-28T'+hour+':00:00Z';mem=engine()
 doc=ConversationMemoryDocument(tenant_id=scope,installation_id=scope,principal_id=scope,conversation_id=scope+':source',document_id=scope+':doc',content=content,occurred_at=date,source_ref=scope+':source',evidence_ref=scope+':evidence',privacy_class='confidential',trust='direct_user',audience=audience)
 t=time.monotonic();receipt=mem.retain_conversation(doc);print(json.dumps({'stage':stage,'step':'retained','seconds':time.monotonic()-t,'receipt':dataclasses.asdict(receipt)},default=str),flush=True)
 t=time.monotonic();reflection=mem.maintain_person_scope(scope,scope,scope);print(json.dumps({'stage':stage,'step':'reflected','seconds':time.monotonic()-t,'receipt':dataclasses.asdict(reflection)},default=str),flush=True)
 question='Hoe pak ik het opruimen van mijn bureau aan?'
 query=RecallCurrentFacts(recall_id=scope+':recall:'+stage,installation_id=scope,tenant_id=scope,principal_id=scope,audience=audience,conversation_id=scope+':new:'+stage,project_id=None,query_purpose='conversation_reply',requested_valid_at=date,scope={'kind':'person'},subject_ids=(scope,),query_text=question)
 memory=engine().recall_for_presence(query)
 print(json.dumps({'stage':stage,'step':'recall','style_mirror':memory.style_mirror,'facts':[f.model_dump(mode='json') for f in memory.confirmed_facts]},ensure_ascii=False,default=str),flush=True)
 if not memory.style_mirror:raise RuntimeError('No evidenced style mirror; no empty-context model attempt')
 turn=ConversationTurn.private(scope,scope,scope,scope+':new:'+stage,scope+':question:'+stage,question,date)
 t=time.monotonic();result=model.generate(turn,identity,memory,())
 print(json.dumps({'stage':stage,'step':'answer','seconds':time.monotonic()-t,'result':dataclasses.asdict(result)},ensure_ascii=False,default=str),flush=True)
