"""Observe an isolated public research request without changing its payload."""
import json
import time
import httpx2
started = time.monotonic()
seen = set()
def mark(stage, **fields):
    print(json.dumps({'event':'research_profile','stage':stage,
        'elapsed_ms':round((time.monotonic()-started)*1000),**fields}),flush=True)
original_send = httpx2.AsyncClient.send
async def send(self, request, **kwargs):
    global started
    started = time.monotonic()
    mark('request_sent')
    response = await original_send(self, request, **kwargs)
    mark('headers_received',status=response.status_code)
    return response
httpx2.AsyncClient.send = send
original_bytes = httpx2.Response.aiter_bytes
async def aiter_bytes(self, *args, **kwargs):
    pending = b''
    async for chunk in original_bytes(self,*args,**kwargs):
        pending += chunk
        while b'\n' in pending:
            line,pending = pending.split(b'\n',1)
            if not line.startswith(b'data:'):continue
            try: event=json.loads(line[5:])
            except ValueError:continue
            kind=event.get('type','')
            if kind.endswith('.delta'):
                if kind in seen:continue
                seen.add(kind)
            fields={'kind':kind}
            item=event.get('item',{})
            if item:
                fields.update(item_type=item.get('type'),item_id=item.get('id'))
                if item.get('type')=='web_search_call':fields['action_type']=item.get('action',{}).get('type')
            if kind=='response.completed':
                response=event.get('response',{})
                fields.update(model=response.get('model'),usage=response.get('usage'))
            mark('sse',**fields)
        yield chunk
httpx2.Response.aiter_bytes=aiter_bytes
exec(compile(open('/original_probe.py').read(),'/original_probe.py','exec'))
