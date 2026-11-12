"""Allowlisted, content-free projection of Codex notifications for latency probes."""
import re

METHODS = frozenset({'turn/started','turn/completed','item/started','item/completed',
 'item/agentMessage/delta','thread/tokenUsage/updated','error','thread/realtime/error'})
ITEMS = frozenset({'userMessage','agentMessage','reasoning','webSearch','commandExecution',
 'mcpToolCall','dynamicToolCall','contextCompaction'})
STATUSES = frozenset({'inProgress','completed','failed','interrupted'})
TOKEN_FIELDS = ('inputTokens','cachedInputTokens','outputTokens','reasoningOutputTokens','totalTokens')

class Projection:
 def __init__(self):
  self.ids={};self.deltas=set();self.backend_completed=False
 def item_index(self, value):
  if not isinstance(value,str):return None
  if value not in self.ids:self.ids[value]=len(self.ids)+1
  return self.ids[value]
 def project(self, message):
  method=message.get('method')
  if method not in METHODS:return None
  p=message.get('params',{})
  if not isinstance(p,dict):return None
  result={'method':method}
  if method.startswith('item/'):
   item=p.get('item',{})
   if not isinstance(item,dict):return None
   index=self.item_index(item.get('id',p.get('itemId')))
   result['item_index']=index
   if method.endswith('/delta'):
    if index in self.deltas:return None
    self.deltas.add(index)
   else:
    kind=item.get('type');result['item_type']=kind if kind in ITEMS else 'other'
    if item.get('status') in STATUSES:result['status']=item['status']
    if item.get('phase') in {'commentary','partial_answer','final_answer'}:result['phase']=item['phase']
    action=item.get('action')
    if kind=='webSearch' and isinstance(action,dict) and action.get('type') in {'search','openPage','findInPage'}:result['action_type']=action['type']
    if kind=='agentMessage' and method=='item/completed':
     text=item.get('text','')
     if isinstance(text,str):result['citation_marker_count']=len(re.findall(r'cite',text));result['url_count']=len(re.findall(r'https?://',text))
  elif method.startswith('turn/'):
   turn=p.get('turn',{})
   if not isinstance(turn,dict):return None
   if turn.get('status') in STATUSES:result['status']=turn['status']
   if method=='turn/completed':self.backend_completed=True
  elif method=='thread/tokenUsage/updated':
   usage=p.get('tokenUsage',{})
   if isinstance(usage,dict):
    for scope in ('last','total'):
     values=usage.get(scope,{})
     if isinstance(values,dict):result[scope]={k:values[k] for k in TOKEN_FIELDS if type(values.get(k)) is int and values[k]>=0}
  # Error text, codes, IDs, queries, URLs and reasoning content never pass through.
  return result


def error_summary(events):
 """Count observed signals; absence is not proof of provider health."""
 notes=[e for e in events if e.get('stage')=='profile_codex_notification']
 return {
  'codex_error_notifications':sum(e.get('method')=='error' for e in notes),
  'realtime_error_notifications':sum(e.get('method')=='thread/realtime/error' for e in notes),
  'voice_error_events':sum(e.get('stage')=='provider_event' and e.get('kind')=='error' for e in events),
  'failed_backend_turns':sum(e.get('method')=='turn/completed' and e.get('status')=='failed' for e in notes),
  'completed_backend_turns':sum(e.get('method')=='turn/completed' and e.get('status')=='completed' for e in notes),
  'local_task_failures':sum(e.get('stage')=='task_failed' for e in events),
  'interpretation':'Counts are observed signals, not an assertion that every internal provider failure is exposed.'}
