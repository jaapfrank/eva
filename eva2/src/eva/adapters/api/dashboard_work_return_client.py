"""Bounded result transport for the existing private conversation page."""

DASHBOARD_WORK_RETURN_CLIENT_JS = r"""
(() => {
  'use strict';
  class EvaWorkReturns {
    constructor({fetch, visible, render, clear, failed, digest, recovered=()=>{}, schedule=setTimeout, cancel=clearTimeout}) {
      Object.assign(this,{fetch,visible,render,clear,failed,digest,recovered,schedule,cancel});
      this.running=false; this.closed=false; this.generation=0; this.seen=new Set();
    }
    suspend() {
      this.generation++; this.cancel(this.timer); this.controller?.abort(); this.clear();
    }
    close() { this.closed=true; this.suspend(); }
    resume() { this.closed=false; return this.poll(); }
    async request(operation, payload, signal) {
      const response=await this.fetch('/internal/v1/dashboard/work/return/'+operation,{
        method:'POST',credentials:'same-origin',cache:'no-store',redirect:'error',signal,
        headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(!response.ok) throw new Error('return unavailable');
      const data=await response.json();
      if(data.contract!=='EvaPrivateWorkReturn/v1') throw new Error('return contract');
      return data;
    }
    async poll() {
      if(this.running || this.closed || !this.visible()) return;
      this.cancel(this.timer); this.running=true; const generation=this.generation;
      const controller=new AbortController(); this.controller=controller;
      const timeout=this.schedule(()=>controller.abort(),10000);
      const current=()=>!this.closed && this.visible() && generation===this.generation;
      try {
        const data=await this.request('poll',{},controller.signal);
        if(!current()) return;
        if(data.result===null) { if(data.status!=='idle') throw new Error('return state'); this.recovered(); }
        else {
          const result=data.result;
          if(data.status!=='claimed' || !result || typeof result.work_id!=='string'
              || !result.work_id.length || result.work_id.length>240
              || typeof result.output!=='string' || !result.output.trim()
              || new TextEncoder().encode(result.output).length>65536
              || !/^[a-f0-9]{64}$/.test(result.operation_id)
              || !/^[a-f0-9]{64}$/.test(result.output_sha256)
              || await this.digest(result.output)!==result.output_sha256) throw new Error('return integrity');
          if(!current()) return;
          if(!this.seen.has(result.operation_id)) {
            this.render(result); this.seen.add(result.operation_id);
          }
          const ack=await this.request('ack',{work_id:result.work_id,operation_id:result.operation_id},controller.signal);
          if(ack.status!=='acknowledged') throw new Error('return acknowledgement');
        }
      } catch(error) {
        if(current()) { this.clear(); this.failed(); }
      } finally {
        this.cancel(timeout); this.running=false;
        if(!this.closed && this.visible()) this.timer=this.schedule(()=>this.poll(),5000);
      }
    }
  }
  if(typeof module!=='undefined') module.exports={EvaWorkReturns};
  if(typeof document==='undefined') return;
  const chat=document.getElementById('chat');
  if(!chat) return;
  const nodes=[];
  const warning=document.createElement('p'); warning.className='state'; warning.setAttribute('role','status');
  chat.after(warning);
  const client=new EvaWorkReturns({
    fetch:(...args)=>fetch(...args), visible:()=>!document.hidden && navigator.onLine!==false,
    digest:async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text))),v=>v.toString(16).padStart(2,'0')).join(''),
    render:result=>{
      warning.textContent='';
      const node=document.createElement('div'); node.className='message eva';
      const title=document.createElement('strong');title.textContent='Je achtergrondopdracht is afgerond';
      const body=document.createElement('div');body.textContent=result.output;
      const link=document.createElement('a');link.href='/internal/v1/dashboard';link.textContent='Opgeslagen opdrachten bekijken';
      node.append(title,body,link);chat.append(node);nodes.push(node);
      if(nodes.length>20) nodes.shift().remove();
    },
    clear:()=>{for(const node of nodes.splice(0)) node.remove();},
    recovered:()=>{warning.textContent='';},
    failed:()=>{warning.textContent='Automatische terugmelding is nu niet beschikbaar. Je opgeslagen opdrachten blijven in het eigenaarsoverzicht staan.';}
  });
  document.addEventListener('visibilitychange',()=>{if(document.hidden)client.suspend();else client.poll();});
  window.addEventListener('offline',()=>client.suspend());
  window.addEventListener('online',()=>client.poll());
  window.addEventListener('pagehide',()=>client.close());
  window.addEventListener('pageshow',event=>{if(event.persisted)client.resume();});
  client.poll();
})();
"""
