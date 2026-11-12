"""Isolated repeated component timings. No model-proposed action replay."""
import asyncio,json,time,dataclasses
from uuid import uuid4
import httpx
import eva.bootstrap.runtime as module
import eva.adapters.api.household_voice_session as route
from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter
from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
rows=[]
def emit(component,attempt,ms,**extra):
 row={'component':component,'attempt':attempt,'elapsed_ms':round(ms,2),**extra};rows.append(row);print(json.dumps({'component_probe':row}),flush=True)
# Repeat only reads. Results remain inside this private runtime.
for cls,name in ((HomeAssistantAdminAdapter,'_inventory_sync'),(HomeAssistantApiTool,'_request')):
 original=getattr(cls,name)
 def wrap(self,*a,__fn=original,__name=name,**kw):
  repeat=__name=='_inventory_sync' or (a and isinstance(a[0],dict) and a[0].get('method')=='GET')
  count=3 if repeat and not getattr(self,'_component_measured_'+__name,False) else 1
  setattr(self,'_component_measured_'+__name,True)
  first=None
  for i in range(count):
   st=time.monotonic();out=__fn(self,*a,**kw)
   if count>1:emit('ha'+__name,i+1,(time.monotonic()-st)*1000)
   if i==0:first=out
  return first
 setattr(cls,name,wrap)
captured={};original=route.install_household_voice_session
def capture(app,**kw):captured.update(kw);return original(app,**kw)
route.install_household_voice_session=capture
start=time.monotonic();app=module.create_runtime_app();emit('runtime_construction',1,(time.monotonic()-start)*1000)
conversation=captured['runtime']._conversation
model=conversation._contact_model
original_generate=model.generate
model_group=0
repeat_next=False
def generate(*args,**kwargs):
 global repeat_next,model_group
 count=3 if repeat_next else 1;repeat_next=False
 if count==3:model_group+=1
 first=None
 for i in range(count):
  st=time.monotonic();out=original_generate(*args,**kwargs)
  emit('contact_model',i+1,(time.monotonic()-st)*1000,group=model_group,identical_input_repetition=count==3,usage=dataclasses.asdict(out.usage),timing=dataclasses.asdict(out.timing),selected_action=None if out.action_call is None else out.action_call.name)
  if i==0:first=out
 return first
model.generate=generate
async def main():
 global repeat_next
 from eva.application.memory import RecallCurrentFacts
 authority=captured['authority'];satellite,device=next(iter(authority.policy.devices.items()));base={'satellite_id':satellite,'device_id':device}
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://eva',headers={'Authorization':'Bearer '+authority.token},timeout=180) as client:
  r=await client.post('/internal/v1/ha-voice/session/work/open',json=base);r.raise_for_status();session=r.json()['session_id']
  async def renew():
   while True:
    await asyncio.sleep(4);r=await client.post('/internal/v1/ha-voice/session/work/renew',json={**base,'session_id':session});r.raise_for_status()
  renewal=asyncio.create_task(renew())
  try:
   service=captured['work_server'].session(token=authority.token,satellite_id=satellite,device_id=device,session_id=session)
   questions=['Welke temperatuur geeft onze lokale weerbron nu aan? Alleen lezen.','Welke lampen zijn er beschikbaar in huis? Alleen de inventaris lezen, niets veranderen.']
   t=service._make_turn(str(uuid4()),questions[0])
   q=RecallCurrentFacts(recall_id='conversation:'+t.message_id,installation_id=t.installation_id,tenant_id=t.tenant_id,principal_id=t.principal_id,audience=t.audience,conversation_id=t.conversation_id,project_id=None,query_purpose='conversation_reply',requested_valid_at=t.occurred_at,scope={'kind':'household','household_id':t.audience.household_id},subject_ids=(t.principal_id,),query_text=questions[0],maximum_facts=12)
   for i,index in enumerate([0,0,0,1,1,0]):
    st=time.monotonic();await asyncio.to_thread(conversation._memory.recall_for_presence,dataclasses.replace(q,query_text=questions[index]));emit('household_memory',i+1,(time.monotonic()-st)*1000,question_group=index)
   for index,question in enumerate(questions):
    repeat_next=True
    r=await client.post('/internal/v1/ha-voice/session/respond',json={**base,'session_id':session,'message_id':str(uuid4()),'text':question,'recent':[],'active_work':[],'work_results':[]});r.raise_for_status()
    events=[json.loads(l) for l in r.text.splitlines()];final=next((x for x in events if x['type']=='final'),None)
    print(json.dumps({'probe_case':index,'completed':final is not None,'actions':[{'name':x.get('name'),'status':x.get('status')} for x in events if x['type']=='action_finished']}),flush=True)
   print(json.dumps({'component_summary':rows}),flush=True)
  finally:
   renewal.cancel();await asyncio.gather(renewal,return_exceptions=True)
   await client.post('/internal/v1/ha-voice/session/work/close',json={**base,'session_id':session})
asyncio.run(main())
