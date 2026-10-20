"""Session-bound local Responses transport to EVA's existing voice core.

Codex's voice handoff uses this provider instead of a second reasoning model.
Only completed core replies and explicitly confirmed spoken updates are exposed.
The listener is loopback-only and carries a fresh, per-session bearer secret.
"""
from __future__ import annotations
import asyncio
from datetime import UTC, datetime
import hashlib
import json
import secrets
import time
import httpx
from uuid import UUID, uuid4, uuid5
import xml.etree.ElementTree as ET
from aiohttp import web


def delegation_input(body):
    items = body.get('input')
    if not isinstance(items, list) or len(items) > 2048:
        raise ValueError('Invalid voice handoff')
    users = [item for item in items if isinstance(item, dict) and item.get('role') == 'user']
    if not users:
        raise ValueError('Missing voice handoff')
    content = users[-1].get('content', [])
    if not isinstance(content, list):
        raise ValueError('Invalid voice handoff content')
    candidates = [item.get('text', '').strip() for item in content
                  if isinstance(item, dict) and item.get('type') == 'input_text']
    candidates = [text for text in candidates if text.startswith('<realtime_delegation>')]
    if len(candidates) != 1 or len(candidates[0]) > 20000:
        raise ValueError('Expected one bounded voice delegation')
    root = ET.fromstring(candidates[0])
    if root.tag != 'realtime_delegation' or root.find('source') is not None:
        raise ValueError('Transcript-tail replay is not a new request')
    text = root.findtext('input', '').strip()
    if not 1 <= len(text) <= 4096 or any(list(node) for node in root):
        raise ValueError('Invalid spoken input')
    # The complete input history differentiates repeated words in distinct turns;
    # retries of the same request retain exactly the same core message identity.
    digest = hashlib.sha256(json.dumps(items, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return text, digest


def response_events(text, identity):
    message = {'id': 'msg_' + identity, 'type': 'message', 'role': 'assistant',
               'phase': 'final_answer',
               'status': 'completed', 'content': [{'type':'output_text','text':text,'annotations':[]}]}
    response = {'id':'resp_' + identity, 'object':'response', 'status':'completed',
                'output':[message], 'usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0}}
    # These are transport counters, not a claim of zero EVA model consumption.
    return [
        {'type':'response.created','response':{**response,'status':'in_progress','output':[]}},
        {'type':'response.output_item.added','output_index':0,'item':{**message,'status':'in_progress','content':[]}},
        {'type':'response.content_part.added','item_id':message['id'],'output_index':0,'content_index':0,
         'part':{'type':'output_text','text':'','annotations':[]}},
        {'type':'response.output_text.delta','item_id':message['id'],'output_index':0,'content_index':0,'delta':text},
        {'type':'response.output_text.done','item_id':message['id'],'output_index':0,'content_index':0,'text':text},
        {'type':'response.output_item.done','output_index':0,'item':message},
        {'type':'response.completed','response':response}]


class EvaVoiceBackend:
    def __init__(self, backend, session_id, check, notify=lambda event: None, *, state=None, refresh_context=None):
        self.backend, self.session_id = backend, str(UUID(session_id))
        self.check, self.notify = check, notify
        self.secret = secrets.token_urlsafe(32)
        self.runner = None
        self.url = None
        self.closed = False
        self.requests = {}
        self.allow_handoffs = True
        self.active = {}
        self.results = {}
        self.recent = []
        self.started_at = time.monotonic()
        self.state = state
        self.refresh_context = refresh_context
        if state is not None:
            self.active, self.results = state.active, state.results

    def diagnostic(self, event, **fields):
        print(json.dumps({'event':'eva_voice_backend', 'session':self.session_id,
            'stage':event, 'elapsed_ms':round((time.monotonic()-self.started_at)*1000),
            **fields}), flush=True)

    def current(self):
        if self.closed:
            raise ValueError('Voice session closed')
        self.check()

    async def open(self):
        self.current()
        app = web.Application(client_max_size=2*1024*1024)
        app.router.add_post('/v1/responses', self.respond)
        self.runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f'http://127.0.0.1:{port}/v1'
        return self

    def provider_config(self):
        if not self.url:
            raise ValueError('Voice backend not started')
        return {
            'model_providers.eva_voice': {
                'name':'EVA voice bridge', 'base_url':self.url, 'wire_api':'responses',
                'requires_openai_auth':True, 'supports_websockets':False,
                'http_headers':{'X-Eva-Voice-Bridge':self.secret},
                'request_max_retries':0, 'stream_max_retries':0},
            'experimental_realtime_webrtc_call_base_url':'https://chatgpt.com/backend-api/codex',
            'features.shell_tool':False, 'features.apps':False,
            'features.plugins':False, 'features.remote_plugin':False,
            'web_search':'disabled'}

    async def respond(self, request):
        if not secrets.compare_digest(request.headers.get('X-Eva-Voice-Bridge',''), self.secret):
            raise web.HTTPUnauthorized(text='Voice session authentication required')
        if not self.allow_handoffs:
            self.diagnostic('handoff_denied', reason='input_transport_only')
            raise web.HTTPForbidden(text='Voice transport cannot originate core requests')
        try:
            self.current()
            body = await request.json()
            text, key = delegation_input(body)
        except (ValueError, ET.ParseError, TypeError, AttributeError) as error:
            self.diagnostic('handoff_rejected', error_type=type(error).__name__)
            raise web.HTTPBadRequest(text='Voice handoff unavailable') from None
        if key not in self.requests:
            if len(self.requests) >= 128 or len(self.active) >= 8:
                raise web.HTTPTooManyRequests(text='Voice session capacity reached')
            self.requests[key] = asyncio.create_task(self.dispatch(text, key))
        try:
            # A transport reconnect must not repeat an already admitted action.
            result = await asyncio.shield(self.requests[key])
            self.current()
        except (httpx.HTTPError, RuntimeError) as error:
            self.current()
            status = error.response.status_code if isinstance(error,httpx.HTTPStatusError) else None
            self.diagnostic('core_failure', error_type=type(error).__name__, status=status)
            self.notify({'type':'work_failed','message_id':str(uuid5(UUID(self.session_id),key))})
            result = 'Sorry, ik raakte de draad even kwijt. Wil je dat nog eens zeggen?'
        except ValueError:
            raise web.HTTPServiceUnavailable(text='EVA voice response unavailable') from None
        response = web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-store'})
        await response.prepare(request)
        for sequence, event in enumerate(response_events(result, key)):
            self.current()
            event['sequence_number'] = sequence
            await response.write(('event: '+event['type']+'\ndata: '+json.dumps(event,ensure_ascii=False)+'\n\n').encode())
        await response.write_eof()
        return response

    async def dispatch(self, text, key, *, spoken_update=None):
        self.current()
        # Freeze the selected context before authorization; a concurrently
        # arriving saved result requires its own check on a subsequent turn.
        suppressed = self.state.suppressed if self.state is not None else set()
        saved_context = self.state.saved_result_context if self.state is not None else {}
        candidates = [(key, value, dict(saved_context[key]) if key in saved_context else None)
                      for key, value in list(self.results.items())[-4:] if key not in suppressed]
        if self.refresh_context is not None:
            await self.refresh_context({key:saved for key, _, saved in candidates if saved is not None})
            self.current()
        work_results = [{'message_id':key, 'text':value} for key, value, saved in candidates
                        if key not in suppressed and self.results.get(key) == value
                        and saved_context.get(key) == saved]
        message_id = str(uuid5(UUID(self.session_id), key))
        active = [{'message_id':mid,'text':value} for mid,value in self.active.items()]
        self.active[message_id] = text
        payload = {'session_id':self.session_id,'message_id':message_id,'text':text,
                   'recent':self.recent[-24:], 'active_work':active,
                   'work_results':work_results, 'stream_deltas':False}
        self.diagnostic('core_request', recent_count=len(payload['recent']))
        self.notify({'type':'work_started','message_id':message_id,'text':text})
        try:
            final = None
            silent = False
            update_seen = False
            async for event in self.backend.events(payload):
                self.current()
                if event.get('type') in {'request_acknowledged','final'}:
                    self.diagnostic('core_'+event['type'])
                if event.get('type') == 'final':
                    final = event['reply']['text']
                    silent = event['reply'].get('response_mode') == 'silent'
                elif event.get('type') == 'spoken_update':
                    # Only explicit, final speech from Eva's core; never model deltas.
                    value = event.get('text')
                    if (not update_seen and isinstance(value, str) and value.strip()
                            and len(value) <= 1000 and len(value.split()) <= 35):
                        update_seen = True
                        if spoken_update is not None:
                            spoken_update(value)
                        self.notify({**event, 'message_id': message_id})
                elif event.get('type') == 'error':
                    raise RuntimeError('EVA core response failed')
                elif event.get('type') != 'delta':
                    if (self.state is not None and event.get('type') == 'action_finished'
                            and event.get('status') == 'verified'
                            and event.get('name') in {'start_background_research', 'start_digital_work', 'start_delegated_work'}):
                        self.state.return_poll_fast_until = time.monotonic() + 60
                    self.notify({**event,'message_id':message_id})
            if not isinstance(final,str) or not final.strip() or len(final)>16000:
                raise RuntimeError('EVA core did not confirm an answer')
            self.current()
            if not silent:
                self.results[message_id] = final
            self.notify({'type':'work_finished','message_id':message_id,
                         'text':'' if silent else final,
                         **({'response_mode':'silent'} if silent else {})})
            return '' if silent else final
        finally:
            self.active.pop(message_id,None)

    def transcript(self, role, text, *, interrupted=False):
        if role not in {'user','assistant'} or not isinstance(text,str) or not text.strip():
            return
        self.current()
        self.recent.append({'role':role,'text':text[:16000],
                            'occurred_at':datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            'interrupted':bool(interrupted),
                            **({'playback_status':'unconfirmed'} if role == 'assistant' and interrupted is None else {})})
        self.recent = self.recent[-24:]

    async def close(self):
        self.closed = True
        for task in self.requests.values():
            task.cancel()  # Closes media delivery, not accepted durable EVA work.
        await asyncio.gather(*self.requests.values(), return_exceptions=True)
        if self.runner:
            await self.runner.cleanup()
