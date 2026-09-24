"""Small owner-only conversation page backed by Eva's real turn boundary."""

from __future__ import annotations

import json
import hashlib
import logging
from pathlib import Path
from queue import SimpleQueue
from threading import Thread
from typing import Any, Iterator

from fastapi.responses import StreamingResponse
from eva.application.conversation_receipt import RECEIVED_INPUT_TEXT

from eva.adapters.api.dashboard_work_return_client import DASHBOARD_WORK_RETURN_CLIENT_JS

from eva.application.conversation import (
    ConversationTurn,
    GroundedReply,
    RecentConversationMessage,
    stream_conversation_text,
)


_LOGGER = logging.getLogger(__name__)
_SOURCE_ROOT = Path(__file__).resolve().parents[2]


def _record_stream_failure(error: Exception, turn: ConversationTurn, stage: str) -> None:
    """Log code locations, never exception messages, locals or provider bodies."""
    frames: list[dict[str, Any]] = []
    trace = error.__traceback__
    while trace is not None:
        try:
            source = Path(trace.tb_frame.f_code.co_filename).relative_to(_SOURCE_ROOT)
        except ValueError:
            pass  # External paths may include private account/workspace information.
        else:
            frames.append({"source": source.as_posix(), "line": trace.tb_lineno})
        trace = trace.tb_next
    error_type = type(error)
    category = next((name for cls, name in (
        (TimeoutError, "timeout"), (ConnectionError, "connection"),
        (PermissionError, "permission"), (ValueError, "validation"),
    ) if isinstance(error, cls)), "unexpected")
    # Only identifiers enter the correlation hash; no text or exception repr is read.
    turn_hash = hashlib.sha256(json.dumps([
        turn.tenant_id, turn.installation_id, turn.conversation_id, turn.message_id,
    ], separators=(",", ":")).encode()).hexdigest()
    type_hash = hashlib.sha256(
        f"{error_type.__module__}.{error_type.__qualname__}".encode()
    ).hexdigest()
    _LOGGER.error("%s", json.dumps({
        "event": "conversation_stream_failure", "stage": stage,
        "turn_sha256": turn_hash, "category": category,
        "exception_type_sha256": type_hash, "eva_frames": frames[-8:],
    }, separators=(",", ":")))


DASHBOARD_CONVERSATION_HTML = """<!doctype html><html lang="nl"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Eva — gesprek</title><style>
    :root{color-scheme:dark;--ink:#eef7ff;--muted:#91a8ba;--line:#244258;--card:#091827;--cyan:#62e7ff;--red:#ff8997}*{box-sizing:border-box}body{margin:0;background:#030912;color:var(--ink);font:16px/1.5 system-ui,sans-serif}.shell{max-width:760px;margin:auto;padding:clamp(1rem,5vw,4rem) 1rem}.eyebrow{color:var(--cyan);font-size:.75rem;font-weight:800;letter-spacing:.14em;text-transform:uppercase}h1{margin:.2rem 0;font-size:clamp(2rem,7vw,4rem)}p{color:var(--muted)}.notice,.chat,form{border:1px solid var(--line);border-radius:16px;background:var(--card)}.notice{padding:1rem;margin:1.5rem 0}.chat{min-height:260px;padding:1rem;display:grid;gap:.8rem;align-content:start}.message{max-width:85%;padding:.75rem 1rem;border-radius:14px;background:#10253a;white-space:pre-wrap}.message.eva{justify-self:start;border-bottom-left-radius:3px}.message.user{justify-self:end;background:#163a50;border-bottom-right-radius:3px}.state{color:var(--muted);font-size:.9rem}.error{color:var(--red)}form{margin-top:1rem;padding:.8rem;display:grid;gap:.7rem}textarea{width:100%;min-height:92px;resize:vertical;border:1px solid var(--line);border-radius:10px;background:#06111e;color:var(--ink);padding:.8rem;font:inherit}button{justify-self:end;border:0;border-radius:10px;background:var(--cyan);color:#00111b;padding:.7rem 1rem;font-weight:750;cursor:pointer}button:disabled{opacity:.55;cursor:wait}a{color:var(--cyan)}
  </style></head><body><main class="shell">
  <div class="eyebrow">Lokale eigenaarssessie</div><h1>Praat met Eva</h1>
  <p>Dit is Eva's echte gesprekroute. Antwoorden verschijnen tijdens het schrijven. Zolang er ‘Eva schrijft…’ staat, is het antwoord nog niet definitief opgeslagen.</p>
  <div class="notice">Je kunt een opdracht stoppen. Een stap die al in behandeling is, kan nog afronden; Eva start daarna geen vervolgstappen.</div>
  <section class="notice" aria-label="Stemgesprek"><button id="voice-toggle" type="button">Praten met Eva</button> <button id="voice-interrupt" type="button">Onderbreek Eva</button><p id="voice-state" role="status">Microfoon uit. Je browser vraagt toestemming wanneer je begint.</p></section>
  <section id="chat" class="chat" aria-live="polite"><div class="state">Klaar voor een privébericht.</div></section>
  <form id="form"><label for="text">Jouw bericht</label><textarea id="text" maxlength="4096" required placeholder="Schrijf iets aan Eva…"></textarea><button id="send" type="submit">Verstuur</button><button id="stop" type="button">Stop deze opdracht</button></form>
  <p><a href="/internal/v1/dashboard">Terug naar cockpit</a></p>
</main><script>
const chat=document.getElementById('chat'),form=document.getElementById('form'),input=document.getElementById('text'),send=document.getElementById('send'),stop=document.getElementById('stop');
let activeRequests=0;
function message(role,text,messageId=null,userNode=null){const node=document.createElement('div');node.className='message '+role;node.textContent=text;if(role==='eva'&&messageId){const forget=document.createElement('button');forget.type='button';forget.textContent='Vergeet deze beurt';forget.addEventListener('click',async()=>{forget.disabled=true;try{const response=await fetch('/internal/v1/dashboard/conversation/erase',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message_id:messageId})});const data=await response.json();if(!response.ok||!data||data.contract!=='EvaDashboardConversationErasure/v1')throw new Error('unavailable');if(userNode)userNode.textContent='Deze beurt is verwijderd.';node.textContent='Deze beurt is verwijderd.';}catch(_error){forget.disabled=false;forget.textContent='Wissen lukte nog niet — probeer opnieuw';}});node.append(document.createElement('br'),forget);}if(userNode)userNode.after(node);else chat.append(node);node.scrollIntoView({block:'end'});return node;}
function state(text,error=false){const node=document.createElement('div');node.className='state'+(error?' error':'');node.textContent=text;chat.append(node);node.scrollIntoView({block:'end'});return node;}
stop.addEventListener('click',async()=>{stop.disabled=true;try{const response=await fetch('/internal/v1/dashboard/conversation/stop',{method:'POST',headers:{'content-type':'application/json'},body:'{}'});const data=await response.json();if(!response.ok||data.contract!=='EvaDashboardConversationStop/v1'||!Number.isInteger(data.requested_count)||data.requested_count<0)throw new Error('unavailable');state(data.requested_count?'Stopverzoek opgeslagen. Eva start geen vervolgstappen.':'Er is nu geen onafgeronde opdracht in dit gesprek gevonden.');}catch(_error){state('Het stopverzoek is niet bevestigd. Probeer opnieuw.',true);}finally{stop.disabled=false;}});
async function readReplyStream(response, onDelta) {
  if(!response.ok || !response.body || !response.headers.get('content-type')?.includes('application/x-ndjson')) throw new Error('stream unavailable');
  const reader=response.body.getReader(), decoder=new TextDecoder();
  let buffer='', size=0;
  try {
    while(true) {
      const {value,done}=await reader.read();
      if(value) {size+=value.byteLength;if(size>1048576)throw new Error('stream too large');}
      buffer+=decoder.decode(value,{stream:!done});
      let boundary;
      while((boundary=buffer.indexOf('\\n'))>=0) {
        const line=buffer.slice(0,boundary);buffer=buffer.slice(boundary+1);
        if(!line.trim())continue;
        const data=JSON.parse(line);
        if(data.contract==='EvaTextDelta/v1' && typeof data.text==='string')onDelta(data.text);
        else if(data.contract==='EvaInternalGroundedReply/v1' && typeof data.text==='string' && typeof data.message_id==='string')return data;
        else throw new Error('stream interrupted');
      }
      if(done)throw new Error('stream ended without saved reply');
    }
  } finally {await reader.cancel().catch(()=>{});reader.releaseLock();}
}
form.addEventListener('submit',async event=>{
  event.preventDefault();const text=input.value.trim();if(!text||activeRequests>=8)return;
  activeRequests++;const userNode=message('user',text);input.value='';input.focus();send.disabled=activeRequests>=8;
  const pending=state('Eva denkt na…');userNode.after(pending);let draft='';
  try {
    const response=await fetch('/internal/v1/dashboard/conversation/respond',{
      method:'POST',headers:{'content-type':'application/json','accept':'application/x-ndjson'},body:JSON.stringify({text})});
    const receipt=response.ok ? response.headers.get('X-Eva-Input-Receipt') : null;
    if(receipt && receipt.length<=120)pending.textContent=receipt;
    const data=await readReplyStream(response,delta=>{
      draft+=delta;pending.className='message eva';pending.textContent=draft+'\\n\\nEva schrijft…';
    });
    const saved=message('eva',data.text,data.message_id,userNode);pending.replaceWith(saved);
  } catch(_error) {
    pending.textContent=(draft?draft+'\\n\\n':'')+'Dit antwoord is niet bevestigd. Bekijk de taakstatus voordat je een opdracht opnieuw verstuurt.';
    pending.classList.add('error');
  } finally {activeRequests--;send.disabled=activeRequests>=8;}
});
</script>""" + "<script>" + DASHBOARD_WORK_RETURN_CLIENT_JS + "</script>" + """<script src="/internal/v1/dashboard/conversation/voice-client.js" defer></script></body></html>
"""


def grounded_reply_wire(reply: GroundedReply) -> dict[str, Any]:
    return {"contract": "EvaInternalGroundedReply/v1", "conversation_id": reply.conversation_id,
            "evidence_memory_ids": list(reply.evidence_memory_ids), "generated_at": reply.generated_at,
            "message_id": reply.message_id, "provider": reply.provider,
            "text": reply.text, "text_sha256": reply.text_sha256}


def dashboard_stream_response(runtime: Any, text: str) -> StreamingResponse:
    """Stream the existing owner turn without a second call or client-owned scope."""
    queue: SimpleQueue[tuple[str, Any]] = SimpleQueue()

    def run() -> None:
        turn = None
        stage = "respond"
        try:
            with stream_conversation_text(lambda delta: queue.put(("delta", delta))):
                turn, reply = runtime.dashboard_turn(text)
            if not isinstance(turn, ConversationTurn) or not isinstance(reply, GroundedReply):
                raise ValueError("owner conversation result is invalid")
            queue.put(("final", reply))
            stage = "after_response"
            runtime.after_response(turn, reply)
        except Exception as error:
            if stage == "respond":
                queue.put(("error", None))
            if isinstance(turn, ConversationTurn):
                _record_stream_failure(error, turn, stage)
            else:
                _LOGGER.error("Owner conversation stream ended before a saved reply")

    def events() -> Iterator[bytes]:
        Thread(target=run, name="eva-owner-text-stream", daemon=True).start()
        while True:
            kind, value = queue.get()
            payload = ({"contract": "EvaTextDelta/v1", "text": value} if kind == "delta"
                       else grounded_reply_wire(value) if kind == "final"
                       else {"contract": "EvaTextStreamError/v1"})
            yield json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
            if kind != "delta":
                return

    return StreamingResponse(events(), media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Eva-Input-Receipt": RECEIVED_INPUT_TEXT})


def conversation_stream_response(runtime: Any, turn: ConversationTurn,
                                 recent: tuple[RecentConversationMessage, ...]) -> StreamingResponse:
    queue: SimpleQueue[tuple[str, Any]] = SimpleQueue()

    def run() -> None:
        stage = "respond"
        try:
            with stream_conversation_text(lambda delta: queue.put(("delta", delta))):
                reply = runtime.respond(turn, recent)
            queue.put(("final", reply))
            stage = "after_response"
            runtime.after_response(turn, reply)
        except Exception as error:
            # Preserve the terminal contract even if the diagnostic sink fails.
            if stage == "respond":
                queue.put(("error", None))
            try:
                _record_stream_failure(error, turn, stage)
            except Exception:
                pass

    def events() -> Iterator[bytes]:
        Thread(target=run, name="eva-text-stream", daemon=True).start()
        while True:
            kind, value = queue.get()
            payload = ({"contract": "EvaTextDelta/v1", "text": value} if kind == "delta"
                       else grounded_reply_wire(value) if kind == "final"
                       else {"contract": "EvaTextStreamError/v1"})
            yield json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
            if kind != "delta":
                return

    return StreamingResponse(events(), media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no",
                 "X-Eva-Input-Receipt": RECEIVED_INPUT_TEXT})


__all__ = [
    "DASHBOARD_CONVERSATION_HTML",
    "conversation_stream_response",
    "grounded_reply_wire",
]
