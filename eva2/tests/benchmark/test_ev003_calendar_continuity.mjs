import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,readFileSync,chmodSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {OpenClawAccounting,nativeCalendarScope,bindOriginalFamilyLimits} from '../../benchmark/inspect_ai_v1/p1_openclaw_parent_accounting.mjs';
const ROOT=resolve(dirname(fileURLToPath(import.meta.url)),'../../benchmark/inspect_ai_v1');
const SOURCE=join(ROOT,'p1_openclaw_parent_accounting.mjs');
const usage={input_tokens:17,output_tokens:3,total_tokens:20};
class Ledger {
 constructor(){this.control={request_id:'public-original-entry'};this.rows=[];this.stopHandlers=new Set();this.stopReason=null;}
 check(){if(this.stopReason)throw Error('stopped');}
 stop(reason){this.stopReason=reason;}
 async begin(role,model,effort){const id=this.rows.length;this.rows.push({id,sample_id:this.control.request_id,role,model,reasoning_effort:effort,status:'pending',usage:null});return id;}
 async settle(id,usage){this.rows[id].status=usage?'completed':'unknown';this.rows[id].usage=usage;}
 report(){return {sample_id:this.control.request_id,complete:this.rows.every(r=>r.status==='completed'),stop:this.stopReason,stop_requested:false,attempts:this.rows};}
}
function body(turn='first-turn'){
 const temporal='## Temporal Context\nCurrent date: 2020-01-02\nTime zone: Europe/Amsterdam\n';
 const msg=(role,kind,text)=>({type:'message',role,internal_chat_message_metadata_passthrough:{turn_id:turn,create_time:42.5,content_item_kinds:[kind]},content:[{type:'input_text',text}]});
 return {temporal,body:{model:'gpt-6-sol',client_metadata:{turn_id:turn,'x-codex-turn-metadata':JSON.stringify({turn_id:turn,thread_id:'public-thread',request_kind:'turn'})},input:[
  msg('user','user.text','Public synthetic request'),msg('developer','additional_content.openclaw_temporal_context','<openclaw_temporal_context>'+temporal+'</openclaw_temporal_context>'),
  msg('user','environments.environment_context','<environment_context><current_date>2026-10-07</current_date><timezone>Europe/Amsterdam</timezone></environment_context>')]}};
}
function setup(mainRole='contact'){
 const dir=mkdtempSync(join(tmpdir(),'calendar-continuity-'));chmodSync(dir,0o700);const clock=join(dir,'clock.json');
 const state={contract:'EvaScenarioCalendar/v1',generation:0,offsetMilliseconds:Date.parse('2020-01-02T12:00:00Z')-Date.now()};writeFileSync(clock,JSON.stringify(state),{mode:0o600});
 const ledger=new Ledger();let saved=[];const options={now:()=>10,clockFile:clock,save:rows=>{saved=structuredClone(rows);writeFileSync(join(dir,'rows.json'),JSON.stringify(rows));}};
 if(mainRole==='work'){options.mainRequestRole='work';options.originalFamilyLimits=bindOriginalFamilyLimits(process.env.EVA_ORIGINAL_PLAN,'cross_domain_actions');}
 const first=new OpenClawAccounting(ledger,20,options);
 const native={sessionId:'public-session',chatType:'direct',delivery:{origin:{nativeDirectUserId:'public-person',from:'public-from',to:'public-to',nativeChannelId:'public-channel',accountId:'public-account',surface:'public-surface',chatType:'direct'}}};
 const deadline=Buffer.from(JSON.stringify({monotonic_deadline:20}));const key='agent:main:public-context';
 const scope=nativeCalendarScope(ledger.control.request_id,deadline,native,key,mainRole);first.bindNativeCalendarScope(scope);
 const initial=body();first.temporalContext(()=>({openclaw_temporal_context:{kind:'application',value:initial.temporal}}));
 const bound=first.prepareBody(initial.body,Buffer.from(JSON.stringify(initial.body)));
 const next=structuredClone(initial.body);next.client_metadata.turn_id='next-turn';next.client_metadata['x-codex-turn-metadata']=JSON.stringify({turn_id:'next-turn',thread_id:'public-thread',request_kind:'turn'});
 const current=structuredClone(next.input[1]);current.internal_chat_message_metadata_passthrough.turn_id='next-turn';next.input.push(current);
 function fresh(){const o=new OpenClawAccounting(ledger,20,options);o.temporalContext(()=>({openclaw_temporal_context:{kind:'application',value:initial.temporal}}));return o;}
 async function complete(event={type:'response.completed',response:{id:'public-response',status:'completed',usage}}){
  const roles=mainRole==='work'?['helper','gpt-6-astra','medium']:['contact','gpt-6-sol','none'];const id=await ledger.begin(...roles);const row={attempt_id:id,role:roles[0],model:roles[1],reasoning_effort:roles[2],status:'pending',calendar_binding:bound.accounting.calendarBinding};first.calls.push(row);await first.event(row,event);return row;
 }
 function extract(rows=saved,parent=ledger.report(),expectedScope=scope){
  const program=`import sys,json\nsys.path.insert(0,sys.argv[1])\nfrom ev003_openclaw_calendar_continuity import completed_calendar_snapshot,native_calendar_scope\nx=json.load(sys.stdin)\ns=native_calendar_scope(x['scope']['entry'],bytes(x['deadline']),x['native'],x['key'],sys.argv[2],x['scope']['main_request_role'])\nassert s==x['scope']\nprint(json.dumps(completed_calendar_snapshot(x['rows'],x['parent'],s)))`;
  const result=spawnSync('python3',['-B','-c',program,ROOT,SOURCE],{input:JSON.stringify({rows,parent,scope:expectedScope,deadline:[...deadline],native,key}),encoding:'utf8'});
  if(result.status!==0)throw Error('parent continuity rejected');return JSON.parse(result.stdout);
 }
 return {dir,clock,state,ledger,first,scope,native,deadline,key,initial,next,fresh,complete,extract,get saved(){return saved;},cleanup:()=>rmSync(dir,{recursive:true,force:true})};
}
await test('same completed reference crosses two actual accounting instances with unchanged input bytes',async()=>{
 const f=setup();try{
  const nextBytes=Buffer.from(JSON.stringify(f.next));assert.throws(()=>f.fresh().prepareBody(f.next,nextBytes),/reference unknown/);
  await f.complete();assert.equal(f.saved[0].completed_calendar_reference.scope.actor_sha256,f.scope.actor_sha256);
  const snapshot=f.extract();const second=f.fresh();second.restoreNativeCompletedResponses(snapshot,f.scope);
  const result=second.prepareBody(f.next,nextBytes);assert.deepEqual(result.accounting.nativeOriginalBodyBytes,nextBytes);
  assert.equal(result.accounting.calendarBinding.environment_turn_id,'first-turn');assert.ok(result.accounting.calendarBinding.environment_source_response_id_sha256);
  assert.equal(second.calls.length,0);assert.equal(f.ledger.rows.length,1);assert.equal(second.deadline,20);
 }finally{f.cleanup();}
});
await test('authoritative parent rejects pending, foreign, absent marker, role, usage and duplicate provenance',async()=>{
 const f=setup();try{await f.complete();
  for(const kind of ['pending','foreign','marker','terminal','role','usage','duplicate','scope','binding','stopped']){
   const rows=structuredClone(f.saved),parent=structuredClone(f.ledger.report());
   if(kind==='pending'){rows[0].status='pending';parent.attempts[0].status='pending';}
   if(kind==='foreign')parent.attempts[0].sample_id='foreign-entry';
   if(kind==='marker')delete rows[0].completed_calendar_reference;
   if(kind==='terminal')rows[0].native_terminal_type='response.incomplete';
   if(kind==='role'){rows[0].role='helper';parent.attempts[0].role='helper';}
   if(kind==='usage')rows[0].usage.total_tokens++;
   if(kind==='duplicate'){rows.push(structuredClone(rows[0]));parent.attempts.push(structuredClone(parent.attempts[0]));}
   if(kind==='scope')rows[0].completed_calendar_reference.scope.actor_sha256='0'.repeat(64);
   if(kind==='binding')rows[0].completed_calendar_reference.binding.generation++;
   if(kind==='stopped')parent.stop_requested=true;
   assert.throws(()=>f.extract(rows,parent),/parent continuity rejected/,kind);
  }
 }finally{f.cleanup();}
});
await test('restore rejects wrong actor/session/key/role/source/deadline/entry and consumes no permits',async()=>{
 const f=setup();try{await f.complete();const snapshot=f.extract();
  for(const key of Object.keys(f.scope)){
   const wrong=structuredClone(snapshot);wrong.scope[key]=key==='monotonic_deadline'?21: key.endsWith('_sha256')?'0'.repeat(64):'other';
   const second=f.fresh();assert.throws(()=>second.restoreNativeCompletedResponses(wrong,f.scope));assert.equal(second.nativeCompletedResponses.size,0);
  }
  for(const kind of ['unknown-field','duplicate-response','duplicate-attempt','wrong-role','malformed-hash','empty','pending-field']){
   const wrong=structuredClone(snapshot);const r=wrong.records[0];
   if(kind==='unknown-field')wrong.extra='untrusted';if(kind==='duplicate-response'){wrong.records.push({...r,attempt_id:1});}
   if(kind==='duplicate-attempt')wrong.records.push({...r,response_id_sha256:'0'.repeat(64)});
   if(kind==='wrong-role')r.reasoning_effort='high';if(kind==='malformed-hash')r.binding.native_thread_id_sha256='bad';
   if(kind==='empty')wrong.records=[];if(kind==='pending-field')r.status='pending';
   const second=f.fresh();assert.throws(()=>second.restoreNativeCompletedResponses(wrong,f.scope));assert.equal(second.nativeCompletedResponses.size,0);
  }
 }finally{f.cleanup();}
});
await test('existing resumed and incremental guards reject unknown references, actor-thread or calendar drift',async()=>{
 const f=setup();try{await f.complete();const snapshot=f.extract();
  for(const kind of ['thread','environment','generation','duplicate']){
   const second=f.fresh();second.restoreNativeCompletedResponses(snapshot,f.scope);const next=structuredClone(f.next);
   if(kind==='thread')next.client_metadata['x-codex-turn-metadata']=JSON.stringify({turn_id:'next-turn',thread_id:'foreign-thread',request_kind:'turn'});
   if(kind==='environment')next.input[2].content[0].text+=' changed';if(kind==='duplicate')next.input.push(structuredClone(next.input[2]));
   if(kind==='generation'){writeFileSync(f.clock,JSON.stringify({...f.state,generation:1}));second.temporalContext(()=>({openclaw_temporal_context:{kind:'application',value:f.initial.temporal}}));}
   assert.throws(()=>second.prepareBody(next,Buffer.from(JSON.stringify(next))));writeFileSync(f.clock,JSON.stringify(f.state));
  }
  const second=f.fresh();second.restoreNativeCompletedResponses(snapshot,f.scope);
  const delta={model:'gpt-6-sol',client_metadata:f.initial.body.client_metadata,previous_response_id:'public-response',input:[{type:'function_call_output',call_id:'public-tool',output:'public output'}]};
  assert.doesNotThrow(()=>second.prepareBody(delta,Buffer.from(JSON.stringify(delta))));delta.previous_response_id='unknown';assert.throws(()=>second.prepareBody(delta,Buffer.from(JSON.stringify(delta))));
 }finally{f.cleanup();}
});
await test('only accepted completed terminal creates a durable marker',async()=>{
 for(const kind of ['incomplete','rejected','status-missing']){
  const f=setup();try{
   if(kind==='rejected')f.ledger.settle=async()=>false;
   await f.complete({type:kind==='incomplete'?'response.incomplete':'response.completed',response:{id:'public-response',status:kind==='incomplete'?'incomplete':kind==='status-missing'?undefined:'completed',usage}});
   assert.equal(f.saved[0].completed_calendar_reference,undefined);assert.throws(()=>f.extract());
  }finally{f.cleanup();}
 }
});
await test('restore is single-use and refuses work already started, expired deadline and stop',async()=>{
 const f=setup();try{await f.complete();const snapshot=f.extract();
  const second=f.fresh();second.restoreNativeCompletedResponses(snapshot,f.scope);assert.throws(()=>second.restoreNativeCompletedResponses(snapshot,f.scope));
  for(const kind of ['calls','active','tools','expired','stop']){
   const o=f.fresh();if(kind==='calls')o.calls.push({});if(kind==='active')o.active.add(()=>{});if(kind==='tools')o.tools.set('x',{});
   if(kind==='expired')o.now=()=>20;if(kind==='stop')f.ledger.stop('owner stop');
   assert.throws(()=>o.restoreNativeCompletedResponses(snapshot,f.scope));assert.equal(o.nativeCompletedResponses.size,0);f.ledger.stopReason=null;
  }
 }finally{f.cleanup();}
});

await test('ordinary work main remains Astra medium across both phases and rejects contact substitution',async()=>{
 const f=setup('work');try{await f.complete();const snapshot=f.extract();const second=f.fresh();second.restoreNativeCompletedResponses(snapshot,f.scope);
  const bound=second.prepareBody(f.next,Buffer.from(JSON.stringify(f.next)));
  assert.equal(bound.accounting.role,'helper');assert.equal(bound.body.model,'gpt-6-astra');assert.equal(bound.body.reasoning.effort,'medium');
  const rows=structuredClone(f.saved),parent=structuredClone(f.ledger.report());
  for(const row of [rows[0],parent.attempts[0]])Object.assign(row,{role:'contact',model:'gpt-6-sol',reasoning_effort:'none'});
  assert.throws(()=>f.extract(rows,parent));
 }finally{f.cleanup();}
});
