"""Isolated repeated component timings. No model-proposed action replay."""
import asyncio,json,time,dataclasses,os
from uuid import uuid4
import httpx
import eva.bootstrap.runtime as module
import eva.adapters.api.household_voice_session as route
from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter
from eva.adapters.connectors.home_assistant_api import HomeAssistantApiTool
rows=[]
def emit(component,attempt,ms,**extra):
 row={'component':component,'attempt':attempt,'elapsed_ms':round(ms,2),**extra};rows.append(row);print(json.dumps({'component_probe':row}),flush=True)
instances=[]
original_init=HomeAssistantAdminAdapter.__init__
def init(self,*a,**kw):
 original_init(self,*a,**kw);instances.append(self)
HomeAssistantAdminAdapter.__init__=init
captured={};original=route.install_household_voice_session
def capture(app,**kw):captured.update(kw);return original(app,**kw)
route.install_household_voice_session=capture
start=time.monotonic();app=module.create_runtime_app();emit('runtime_construction',1,(time.monotonic()-start)*1000)
conversation=captured['runtime']._conversation
memory=conversation._memory
original_request=memory._request
request_counter=0
def request(method,path,*a,**kw):
 global request_counter
 st=time.monotonic()
 try:return original_request(method,path,*a,**kw)
 finally:
  request_counter+=1;emit('memory_http',request_counter,(time.monotonic()-st)*1000,route='recall' if path.endswith('/recall') else 'source_or_bank')
memory._request=request
async def main():
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
   variant_builder=memory._recall_query_variants
   for i,index in enumerate([0,0,0,0,1,1]):
    single = os.environ.get('EVA_PROFILE_SINGLE_QUERY') == '1' and i%2==1
    memory._recall_query_variants = (lambda text:(text,)) if single else variant_builder
    st=time.monotonic();await asyncio.to_thread(conversation._memory.recall_for_presence,dataclasses.replace(q,query_text=questions[index]));emit('household_memory',i+1,(time.monotonic()-st)*1000,question_group=index,single_query=single)
   turn=service._make_turn(str(uuid4()),'Read-only component inspection')
   assert 'read_home_assistant_inventory' in conversation._actions.available_action_names(turn)
   assert instances, 'No HA admin adapter captured'
   reader=instances[0]
   for i,domain in enumerate(['weather','weather','weather','light','light','weather']):
    st=time.monotonic();result=await reader.household_page(section='entities',domain=domain);emit('ha_inventory_read',i+1,(time.monotonic()-st)*1000,domain=domain,item_count=len(result.get('items',[])))
   print(json.dumps({'component_summary':rows}),flush=True)
  finally:
   renewal.cancel();await asyncio.gather(renewal,return_exceptions=True)
   await client.post('/internal/v1/ha-voice/session/work/close',json={**base,'session_id':session})
asyncio.run(main())
