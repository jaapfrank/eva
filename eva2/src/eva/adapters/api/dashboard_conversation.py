"""Small owner-only conversation page backed by Eva's real turn boundary."""

from __future__ import annotations

import json
from queue import SimpleQueue
from threading import Thread
from typing import Any, Iterator

from fastapi.responses import StreamingResponse

from eva.application.conversation import (
    ConversationTurn,
    GroundedReply,
    RecentConversationMessage,
    stream_conversation_text,
)


DASHBOARD_CONVERSATION_HTML = """<!doctype html><html lang="nl"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Eva — gesprek</title><style>
    :root{color-scheme:dark;--ink:#eef7ff;--muted:#91a8ba;--line:#244258;--card:#091827;--cyan:#62e7ff;--red:#ff8997}*{box-sizing:border-box}body{margin:0;background:#030912;color:var(--ink);font:16px/1.5 system-ui,sans-serif}.shell{max-width:760px;margin:auto;padding:clamp(1rem,5vw,4rem) 1rem}.eyebrow{color:var(--cyan);font-size:.75rem;font-weight:800;letter-spacing:.14em;text-transform:uppercase}h1{margin:.2rem 0;font-size:clamp(2rem,7vw,4rem)}p{color:var(--muted)}.notice,.chat,form{border:1px solid var(--line);border-radius:16px;background:var(--card)}.notice{padding:1rem;margin:1.5rem 0}.chat{min-height:260px;padding:1rem;display:grid;gap:.8rem;align-content:start}.message{max-width:85%;padding:.75rem 1rem;border-radius:14px;background:#10253a;white-space:pre-wrap}.message.eva{justify-self:start;border-bottom-left-radius:3px}.message.user{justify-self:end;background:#163a50;border-bottom-right-radius:3px}.state{color:var(--muted);font-size:.9rem}.error{color:var(--red)}form{margin-top:1rem;padding:.8rem;display:grid;gap:.7rem}textarea{width:100%;min-height:92px;resize:vertical;border:1px solid var(--line);border-radius:10px;background:#06111e;color:var(--ink);padding:.8rem;font:inherit}button{justify-self:end;border:0;border-radius:10px;background:var(--cyan);color:#00111b;padding:.7rem 1rem;font-weight:750;cursor:pointer}button:disabled{opacity:.55;cursor:wait}a{color:var(--cyan)}
  </style></head><body><main class="shell">
  <div class="eyebrow">Lokale eigenaarssessie</div><h1>Praat met Eva</h1>
  <p>Dit is Eva's echte gesprekroute. De server bindt ieder bericht aan de ingestelde eigenaar en privécontext; de browser kan die identiteit niet kiezen. Een antwoord is pas zichtbaar nadat de kern het heeft vastgelegd.</p>
  <div class="notice"><strong>Grenzen:</strong> Telegram, voice en huisacties zijn hier niet gekoppeld. Dit scherm doet geen externe handelingen.</div>
  <section id="chat" class="chat" aria-live="polite"><div class="state">Klaar voor een privébericht.</div></section>
  <form id="form"><label for="text">Jouw bericht</label><textarea id="text" maxlength="4096" required placeholder="Schrijf iets aan Eva…"></textarea><button id="send" type="submit">Verstuur</button></form>
  <p><a href="/internal/v1/dashboard">Terug naar cockpit</a></p>
</main><script>
const chat=document.getElementById('chat'),form=document.getElementById('form'),input=document.getElementById('text'),send=document.getElementById('send');
function message(role,text,messageId=null,userNode=null){const node=document.createElement('div');node.className='message '+role;node.textContent=text;if(role==='eva'&&messageId){const forget=document.createElement('button');forget.type='button';forget.textContent='Vergeet deze beurt';forget.addEventListener('click',async()=>{forget.disabled=true;try{const response=await fetch('/internal/v1/dashboard/conversation/erase',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message_id:messageId})});const data=await response.json();if(!response.ok||!data||data.contract!=='EvaDashboardConversationErasure/v1')throw new Error('unavailable');if(userNode)userNode.textContent='Deze beurt is verwijderd.';node.textContent='Deze beurt is verwijderd.';}catch(_error){forget.disabled=false;forget.textContent='Wissen lukte nog niet — probeer opnieuw';}});node.append(document.createElement('br'),forget);}chat.append(node);node.scrollIntoView({block:'end'});return node;}
function state(text,error=false){const node=document.createElement('div');node.className='state'+(error?' error':'');node.textContent=text;chat.append(node);node.scrollIntoView({block:'end'});return node;}
form.addEventListener('submit',async event=>{event.preventDefault();const text=input.value.trim();if(!text)return;const userNode=message('user',text);input.value='';send.disabled=true;const pending=state('Eva denkt na…');try{const response=await fetch('/internal/v1/dashboard/conversation/respond',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text})});const data=await response.json();if(!response.ok||!data||data.contract!=='EvaDashboardGroundedReply/v1'||typeof data.text!=='string'||typeof data.message_id!=='string')throw new Error('unavailable');pending.remove();message('eva',data.text,data.message_id,userNode);}catch(_error){pending.textContent='Eva kon nu geen antwoord geven. Er is geen antwoord of actie verzonnen.';pending.classList.add('error');}finally{send.disabled=false;input.focus();}});
</script></body></html>
"""


def grounded_reply_wire(reply: GroundedReply) -> dict[str, Any]:
    return {"contract": "EvaInternalGroundedReply/v1", "conversation_id": reply.conversation_id,
            "evidence_memory_ids": list(reply.evidence_memory_ids), "generated_at": reply.generated_at,
            "message_id": reply.message_id, "provider": reply.provider,
            "text": reply.text, "text_sha256": reply.text_sha256}


def conversation_stream_response(runtime: Any, turn: ConversationTurn,
                                 recent: tuple[RecentConversationMessage, ...]) -> StreamingResponse:
    queue: SimpleQueue[tuple[str, Any]] = SimpleQueue()

    def run() -> None:
        try:
            with stream_conversation_text(lambda delta: queue.put(("delta", delta))):
                reply = runtime.respond(turn, recent)
            queue.put(("final", reply))
            runtime.after_response(turn, reply)
        except Exception:
            queue.put(("error", None))

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

    return StreamingResponse(events(), media_type="application/x-ndjson")


__all__ = [
    "DASHBOARD_CONVERSATION_HTML",
    "conversation_stream_response",
    "grounded_reply_wire",
]
