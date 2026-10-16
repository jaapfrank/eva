"""Links the existing owner chat to its exact durable work episode."""
DASHBOARD_CONVERSATION_WORK_JS = r"""
(() => {
  'use strict';
  const panel=document.getElementById('conversation-work'); if(!panel) return;
  const labels={admitted:'Ingepland',call_started:'Bezig',stopped:'Gestopt',succeeded:'Resultaat klaar',verified:'Resultaat gecontroleerd',failed:'Mislukt',needs_help:'Hulp nodig',ambiguous:'Uitkomst onzeker',expired:'Verlopen'};
  let running=false,controller,timer,generation=0,closed=false;
  function clear(){panel.replaceChildren();}
  async function refresh(){
    if(running||closed||document.hidden) return;
    running=true; const version=generation; controller=new AbortController();clearTimeout(timer);
    try {
      const response=await fetch('/internal/v1/dashboard/conversation/work',{credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal});
      if(!response.ok)throw new Error();const data=await response.json();
      if(data.contract!=='EvaConversationWork/v1'||typeof data.conversation_id!=='string'||!Array.isArray(data.jobs)||data.jobs.length>20
        ||!data.jobs.every(job=>typeof job.work_id==='string'&&job.work_id.length<=240&&typeof job.task_preview==='string'&&Object.hasOwn(labels,job.state)))throw new Error();
      if(version!==generation||document.hidden||closed)return;clear();
      const title=document.createElement('h2');title.textContent='Opdrachten bij dit gesprek';panel.append(title);
      if(!data.jobs.length){const text=document.createElement('p');text.textContent='Nog geen achtergrondopdracht bij dit gesprek.';panel.append(text);}
      for(const job of data.jobs){
        const card=document.createElement('article');card.className='notice';card.dataset.workId=job.work_id;
        const heading=document.createElement('strong');heading.textContent=job.task_preview;
        const status=document.createElement('p');status.textContent=labels[job.state]+(job.stop_requested?' · stopverzoek opgeslagen':'');
        const link=document.createElement('a');link.href='/internal/v1/dashboard#/?section=work&work_id='+encodeURIComponent(job.work_id);link.textContent='Voortgang, gesprek en resultaat openen';
        card.append(heading,status,link);panel.append(card);
        // Exact source IDs may highlight the originating live turn; no fuzzy text match.
        if(typeof job.origin_message_id==='string'){
          const original=document.querySelector('[data-message-id="'+CSS.escape(job.origin_message_id)+'"]');
          if(original)original.dataset.workId=job.work_id;
        }
      }
      if(data.truncated){const note=document.createElement('p');note.textContent='Twintig meest recente of lopende opdrachten; zoek oudere opdrachten in Eva-beheer.';panel.append(note);}
    } catch(error){if(version===generation&&!closed&&!document.hidden){clear();const note=document.createElement('p');note.textContent='Opdrachten zijn nu niet beschikbaar. Eerdere gegevens zijn verborgen; open Eva-beheer om de opgeslagen toestand te lezen.';note.setAttribute('role','status');panel.append(note);}}
    finally{running=false;if(!closed&&!document.hidden)timer=setTimeout(refresh,5000);}
  }
  function visibility(){generation++;controller?.abort();clearTimeout(timer);clear();if(!document.hidden)refresh();}
  document.addEventListener('visibilitychange',visibility);
  window.addEventListener('eva-reply-saved',()=>{clearTimeout(timer);if(!running)refresh();});
  window.addEventListener('pagehide',()=>{closed=true;generation++;controller?.abort();clearTimeout(timer);clear();});
  window.addEventListener('pageshow',event=>{if(event.persisted){closed=false;refresh();}});
  refresh();
})();
"""
