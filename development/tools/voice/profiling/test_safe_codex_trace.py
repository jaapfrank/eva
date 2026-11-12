import json
from safe_codex_trace import Projection

def test_sensitive_content_never_survives_projection():
 p=Projection();secret='PRIVATE sk-secret Bearer secret transcript audio@example.org'
 for kind in ['agentMessage','webSearch','reasoning',secret]:
  item={'id':secret,'type':kind,'text':secret,'query':secret,'status':secret,'phase':secret,'action':{'type':'search','query':secret,'url':secret}}
  for method in ['item/started','item/completed']:
   value=p.project({'method':method,'params':{'item':item,'threadId':secret}})
   assert secret not in json.dumps(value)
 assert p.project({'method':secret,'params':{'text':secret}}) is None
 assert p.project({'method':'error','params':{'error':{'message':secret,'code':secret}}})=={'method':'error'}

def test_first_delta_only_and_matching_item_indices():
 p=Projection();first=p.project({'method':'item/started','params':{'item':{'id':'a','type':'agentMessage'}}})
 delta={'method':'item/agentMessage/delta','params':{'itemId':'a','delta':'private'}}
 assert p.project(delta)['item_index']==first['item_index']
 assert p.project(delta) is None
 assert not p.backend_completed
 p.project({'method':'turn/completed','params':{'turn':{'status':'completed'}}})
 assert p.backend_completed

def test_usage_allowlist_rejects_non_numbers():
 p=Projection();v=p.project({'method':'thread/tokenUsage/updated','params':{'tokenUsage':{'total':{'inputTokens':12,'outputTokens':'secret','totalTokens':True,'text':'secret'}}}})
 assert v['total']=={'inputTokens':12}


def test_error_signals_are_counted_without_error_payloads():
 from safe_codex_trace import error_summary
 p=Projection();events=[]
 for method in ('error','thread/realtime/error'):
  safe=p.project({'method':method,'params':{'message':'PRIVATE TOKEN','code':'PRIVATE'}})
  assert safe=={'method':method}
  events.append({'stage':'profile_codex_notification',**safe})
 events.append({'stage':'profile_codex_notification','method':'turn/completed','status':'failed'})
 summary=error_summary(events)
 assert summary['codex_error_notifications']==1
 assert summary['realtime_error_notifications']==1
 assert summary['failed_backend_turns']==1
 assert summary['completed_backend_turns']==0
 assert 'PRIVATE' not in json.dumps(summary)
