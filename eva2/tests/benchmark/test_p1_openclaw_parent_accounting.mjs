import {existsSync} from 'node:fs';
import {OpenClawNativeProcessScope} from '../../benchmark/inspect_ai_v1/p1_openclaw_parent_accounting.mjs';
/** Public fixtures. Ordinary native composition uses own auth with mocked upstream and no real inference. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { registerHooks } from 'node:module';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, readFileSync, writeFileSync, chmodSync, renameSync } from 'node:fs';
import { randomUUID, createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { dirname } from 'node:path';
import { OpenClawProcessLedger, OpenClawAccounting, NATIVE_PINS, installOpenClawAccounting } from '../../benchmark/inspect_ai_v1/p1_openclaw_parent_accounting.mjs';

class FakeLedger {
  constructor(limits=null) {
    this.rows = []; this.stopHandlers = new Set(); this.stopReason = null;
    this.limits = limits ? Object.freeze({...limits}) : null;
    this.pendingUsage = limits ? Object.freeze({...limits.pendingUsage}) : null;
  }
  check() { if (this.stopReason) throw new Error('stopped'); }
  stop(reason) { this.stopReason = reason; for (const f of this.stopHandlers) f(); }
  validUsage(value) {
    return value && ['input_tokens','output_tokens','total_tokens'].every(key=>Number.isSafeInteger(value[key]) && value[key]>=0)
      && value.total_tokens===value.input_tokens+value.output_tokens;
  }
  totals() {
    return this.rows.reduce((sum,row)=>{
      const value=row.status==='pending'?this.pendingUsage:row.usage;
      if (this.limits && !this.validUsage(value)) throw new Error('unknown synthetic usage');
      if(value){sum.input_tokens+=value.input_tokens;sum.output_tokens+=value.output_tokens;}
      return sum;
    },{input_tokens:0,output_tokens:0});
  }
  async begin(role, model, effort) {
    this.check();
    if(this.limits){
      if(this.rows.length>=this.limits.attempts){this.stop('synthetic attempt cap');throw new Error('synthetic attempt cap');}
      const totals=this.totals();
      if(totals.input_tokens+this.pendingUsage.input_tokens>this.limits.inputTokens
        || totals.output_tokens+this.pendingUsage.output_tokens>this.limits.outputTokens){
        this.stop('synthetic token cap');throw new Error('synthetic token cap');
      }
    }
    const id = this.rows.length; this.rows.push({id,role,model,effort,status:'pending'}); return id;
  }
  async settle(id, value) {
    const row=this.rows[id];
    if(!row || row.status!=='pending') return;
    row.usage=value;row.status=value?'completed':'unknown';
    if(!value || (this.limits && !this.validUsage(value))){row.status='unknown';this.stop('unknown');return false;}
    if(this.limits){
      const totals=this.totals();
      if(totals.input_tokens>this.limits.inputTokens || totals.output_tokens>this.limits.outputTokens){
        this.stop('synthetic token overshoot');return false;
      }
    }
  }
}
const usage = {input_tokens:17,output_tokens:3,total_tokens:20};
const event = {type:'response.completed',response:{id:'public-response',usage}};
await test('public synthetic ledger enforces shared attempts, reserved tokens, unknown usage and stop',async()=>{
  const limits={attempts:16,inputTokens:1048576,outputTokens:19200,pendingUsage:usage};
  const capped=new FakeLedger(limits);
  for(let i=0;i<16;i++)await capped.settle(await capped.begin('contact','gpt-6-sol','none'),usage);
  await assert.rejects(capped.begin('helper','gpt-6-astra','medium'),/synthetic attempt cap/);
  assert.equal(capped.rows.length,16);
  for(const limit of ['inputTokens','outputTokens']){
    const pending=new FakeLedger({...limits,[limit]:limit==='inputTokens'?17:3});
    await pending.begin('contact','gpt-6-sol','none');
    await assert.rejects(pending.begin('helper','gpt-6-astra','medium'),/synthetic token cap/);
    assert.equal(pending.rows.length,1);
  }
  const overshoot=new FakeLedger({...limits,inputTokens:17});
  const overshootId=await overshoot.begin('contact','gpt-6-sol','none');
  const actual={input_tokens:18,output_tokens:3,total_tokens:21};
  assert.equal(await overshoot.settle(overshootId,actual),false);
  assert.deepEqual(overshoot.rows[0].usage,actual);
  await assert.rejects(overshoot.begin('contact','gpt-6-sol','none'),/stopped/);
  for(const value of [null,{input_tokens:17,output_tokens:3}]){
    const unknown=new FakeLedger(limits);
    assert.equal(await unknown.settle(await unknown.begin('contact','gpt-6-sol','none'),value),false);
    assert.equal(unknown.rows[0].status,'unknown');
    await assert.rejects(unknown.begin('contact','gpt-6-sol','none'),/stopped/);
  }
  const stopped=new FakeLedger(limits);stopped.stop('owner stop');
  await assert.rejects(stopped.begin('contact','gpt-6-sol','none'),/stopped/);
});

const nativeBody = (kind='turn', extra={}) => ({model:'gpt-6-sol',reasoning:{effort:'low'},input:[{role:'user',content:'Public fixture.'}],
  client_metadata:{'x-codex-turn-metadata':JSON.stringify({thread_id:'public-thread',request_kind:kind,...extra})}});
const observer = ledger => new OpenClawAccounting(ledger, 20, {now:()=>10});

await test('native operation controls select explicit contact/helper without altering input', () => {
  const o=observer(new FakeLedger());
  const input=nativeBody();const contact=o.prepareBody(input);
  assert.equal(contact.body.input,input.input);assert.equal(contact.body.client_metadata,input.client_metadata);
  assert.deepEqual(contact.accounting,{role:'contact',model:'gpt-6-sol',effort:'none'});
  for (const [kind, extra] of [['memory',{}],['compaction',{}],['turn',{parent_thread_id:'public-parent'}]]) {
    assert.deepEqual(o.prepareBody(nativeBody(kind,extra)).accounting,{role:'helper',model:'gpt-6-astra',effort:'medium'});
  }
  assert.throws(()=>o.prepareBody(nativeBody('unrecognized')));
});
// Public serialized native shape; intentionally not positioned at fixed input indices.
const publicCalendarBody = (turnId='public-turn') => {
  const temporal='## Temporal Context\nCurrent date: 2020-01-02\nTime zone: Europe/Amsterdam\n';
  const message=(role,kind,text,id=turnId)=>({type:'message',role,
    internal_chat_message_metadata_passthrough:{turn_id:id,create_time:42.5,content_item_kinds:[kind]},
    content:[{type:'input_text',text}]});
  return {body:{...nativeBody(),client_metadata:{turn_id:turnId,'x-codex-turn-metadata':JSON.stringify({turn_id:turnId,request_kind:'turn'})},
    input:[message('user','user.text','Leave my example <current_date>1999-12-31</current_date> alone.'),
      message('developer','additional_content.openclaw_temporal_context','<openclaw_temporal_context>'+temporal+'</openclaw_temporal_context>'),
      message('user','environments.environment_context','<environment_context><current_date>2026-10-07</current_date><timezone>Europe/Amsterdam</timezone></environment_context>'),
      message('user','environments.environment_context','Historical environment stays unchanged.','old-turn')]},temporal};
};
await test('same native turn binds only its environment date, retains original bytes and acknowledged calendar', async()=>{
  const dir=mkdtempSync(tmpdir()+'/openclaw-calendar-');chmodSync(dir,0o700);const clock=dir+'/clock.json';
  const state={contract:'EvaScenarioCalendar/v1',generation:0,offsetMilliseconds:Date.parse('2020-01-02T12:00:00Z')-Date.now()};
  writeFileSync(clock,JSON.stringify(state),{mode:0o600});
  const o=new OpenClawAccounting(new FakeLedger(),20,{now:()=>10,clockFile:clock,privateModelContextDir:dir});
  const {body,temporal}=publicCalendarBody();const original=Buffer.from(JSON.stringify(body,null,2));const unchanged=JSON.stringify(body);
  try{
    o.temporalContext(()=>({openclaw_temporal_context:{kind:'application',value:temporal}}));
    const result=o.prepareBody(body,original);const after=structuredClone(result.body);
    assert.equal(after.input[2].content[0].text,body.input[2].content[0].text.replace('2026-10-07','2020-01-02'));
    after.input[2].content[0].text=body.input[2].content[0].text;
    assert.deepEqual(after,{...body,reasoning:{effort:'none'}});assert.equal(JSON.stringify(body),unchanged);
    const row={};o.captureContext(row,Buffer.from(JSON.stringify(result.body)),result.accounting);
    assert.deepEqual(readFileSync(row.native_original_model_context.path),original);
    assert.deepEqual(JSON.parse(readFileSync(row.native_calendar_prebinding_context.path)),{...body,reasoning:{effort:'none'}});
    assert.equal(row.calendar_binding.generation,0);assert.equal(row.calendar_binding.native_date_changed,true);
    assert.equal(row.calendar_binding.changed_field,'/input/2/content/0/text/current_date');
    assert.ok(Date.now()>Date.parse('2026-01-01'));
  }finally{rmSync(dir,{recursive:true,force:true});}
});
await test('calendar refuses mismatched turn/zone, duplicate carrier, stale generation and absent originals before permits',()=>{
  const dir=mkdtempSync(tmpdir()+'/openclaw-calendar-');const clock=dir+'/clock.json';
  const state={contract:'EvaScenarioCalendar/v1',generation:0,offsetMilliseconds:Date.parse('2020-01-02T12:00:00Z')-Date.now()};
  writeFileSync(clock,JSON.stringify(state),{mode:0o600});const ledger=new FakeLedger();
  const o=new OpenClawAccounting(ledger,20,{now:()=>10,clockFile:clock});
  const {body,temporal}=publicCalendarBody();
  try{
    o.temporalContext(()=>({openclaw_temporal_context:{kind:'application',value:temporal}}));
    const refuses=changed=>assert.throws(()=>o.prepareBody(changed,Buffer.from(JSON.stringify(changed))));
    const differentTurn=structuredClone(body);differentTurn.input[2].internal_chat_message_metadata_passthrough.turn_id='wrong';refuses(differentTurn);
    const zone=structuredClone(body);zone.input[2].content[0].text=zone.input[2].content[0].text.replace('Europe/Amsterdam','UTC');refuses(zone);
    const duplicate=structuredClone(body);duplicate.input.push(duplicate.input[2]);refuses(duplicate);
    const alteredTemporal=structuredClone(body);alteredTemporal.input[1].content[0].text+='instructions';refuses(alteredTemporal);
    assert.throws(()=>o.prepareBody(body));
    writeFileSync(clock,JSON.stringify({...state,generation:1}));refuses(body);
    assert.equal(ledger.rows.length,0);
  }finally{rmSync(dir,{recursive:true,force:true});}
});
await test('calendar resumes only the exact settled same-thread original environment',async()=>{
  const dir=mkdtempSync(tmpdir()+'/openclaw-calendar-resume-');const clock=dir+'/clock.json';
  const state={contract:'EvaScenarioCalendar/v1',generation:0,offsetMilliseconds:Date.parse('2020-01-02T12:00:00Z')-Date.now()};
  writeFileSync(clock,JSON.stringify(state),{mode:0o600});
  const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,clockFile:clock});
  const first=publicCalendarBody('first-turn');first.body.client_metadata['x-codex-turn-metadata']=JSON.stringify({turn_id:'first-turn',thread_id:'resume-thread',request_kind:'turn'});
  // The captured ordinary resume has one initial environment; the helper's extra ambiguous historical carrier belongs to other tests.
  first.body.input.pop();
  try{
    o.temporalContext(()=>({openclaw_temporal_context:{kind:'application',value:first.temporal}}));
    const bound=o.prepareBody(first.body,Buffer.from(JSON.stringify(first.body)));
    const next=structuredClone(first.body);
    next.client_metadata.turn_id='next-turn';
    next.client_metadata['x-codex-turn-metadata']=JSON.stringify({turn_id:'next-turn',thread_id:'resume-thread',request_kind:'turn'});
    const current=structuredClone(next.input[1]);current.internal_chat_message_metadata_passthrough.turn_id='next-turn';next.input.push(current);
    const bytes=Buffer.from(JSON.stringify(next));
    assert.throws(()=>o.prepareBody(next,bytes),/resumed native environment reference unknown/);
    const id=await ledger.begin('contact','gpt-6-sol','none');
    await o.event({attempt_id:id,status:'pending',calendar_binding:bound.accounting.calendarBinding},
      {type:'response.completed',response:{id:'settled-initial-environment',status:'completed',usage}});
    const before=JSON.stringify(next);const resumed=o.prepareBody(next,bytes);
    assert.equal(JSON.stringify(next),before);assert.deepEqual(resumed.accounting.nativeOriginalBodyBytes,bytes);
    assert.equal(resumed.accounting.calendarBinding.environment_turn_id,'first-turn');
    assert.ok(resumed.accounting.calendarBinding.environment_source_response_id_sha256);
    assert.equal(resumed.body.input[2].content[0].text.includes('<current_date>2020-01-02</current_date>'),true);
    assert.deepEqual(resumed.body.input[1],next.input[1]);
    for(const change of ['thread','environment','duplicate','generation']){
      const wrong=structuredClone(next);
      if(change==='thread')wrong.client_metadata['x-codex-turn-metadata']=JSON.stringify({turn_id:'next-turn',thread_id:'other-thread',request_kind:'turn'});
      if(change==='environment')wrong.input[2].content[0].text+=' changed';
      if(change==='duplicate')wrong.input.push(structuredClone(wrong.input[2]));
      if(change==='generation'){
        writeFileSync(clock,JSON.stringify({...state,generation:1}));
        o.temporalContext(()=>({openclaw_temporal_context:{kind:'application',value:first.temporal}}));
      }
      assert.throws(()=>o.prepareBody(wrong,Buffer.from(JSON.stringify(wrong))),/resumed native environment reference unknown/);
    }
  }finally{rmSync(dir,{recursive:true,force:true});}
});
await test('calendar supports native typed tool history and only settled same-thread-turn incremental responses',async()=>{
  const dir=mkdtempSync(tmpdir()+'/openclaw-calendar-continuation-');const clock=dir+'/clock.json';
  const state={contract:'EvaScenarioCalendar/v1',generation:0,offsetMilliseconds:Date.parse('2020-01-02T12:00:00Z')-Date.now()};
  writeFileSync(clock,JSON.stringify(state),{mode:0o600});const ledger=new FakeLedger();
  const o=new OpenClawAccounting(ledger,20,{now:()=>10,clockFile:clock});
  const {body,temporal}=publicCalendarBody();
  const native={turn_id:'public-turn',thread_id:'public-thread',request_kind:'turn'};
  body.client_metadata['x-codex-turn-metadata']=JSON.stringify(native);
  const output={type:'function_call_output',call_id:'public-tool',output:'Public result',
    internal_chat_message_metadata_passthrough:{turn_id:'public-turn',create_time:43.5}};
  body.input.push({type:'function_call',call_id:'public-tool',name:'exec_command',arguments:'{}',
    internal_chat_message_metadata_passthrough:{turn_id:'public-turn'}},output);
  const delta={...nativeBody(),client_metadata:body.client_metadata,previous_response_id:'public-settled-response',input:[output]};
  try{
    o.temporalContext(()=>({openclaw_temporal_context:{kind:'application',value:temporal}}));
    const full=o.prepareBody(body,Buffer.from(JSON.stringify(body)));
    assert.deepEqual(full.body.input.slice(-2),body.input.slice(-2));
    assert.throws(()=>o.prepareBody(delta,Buffer.from(JSON.stringify(delta))),/reference unknown/);
    const id=await ledger.begin('contact','gpt-6-sol','none');
    const row={attempt_id:id,status:'pending',calendar_binding:full.accounting.calendarBinding};
    await o.event(row,{type:'response.completed',response:{id:delta.previous_response_id,status:'completed',usage}});
    const inherited=o.prepareBody(delta,Buffer.from(JSON.stringify(delta)));
    assert.deepEqual(inherited.body,{...delta,reasoning:{effort:'none'}});assert.equal(inherited.accounting.calendarBinding.changed_field,null);
    assert.equal(inherited.accounting.calendarBinding.native_date_changed,false);
    for(const changed of ['unknown','thread','turn','item-turn','missing-thread','non-output','typed-carrier']){
      const request=structuredClone(delta);const metadata=JSON.parse(request.client_metadata['x-codex-turn-metadata']);
      if(changed==='unknown')request.previous_response_id='not-observed';
      if(changed==='thread')metadata.thread_id='other-thread';
      if(changed==='turn'){metadata.turn_id='other-turn';request.client_metadata.turn_id='other-turn';}
      if(changed==='item-turn')request.input[0].internal_chat_message_metadata_passthrough.turn_id='other-turn';
      if(changed==='missing-thread')delete metadata.thread_id;
      if(changed==='non-output')request.input.push({type:'message',role:'user',content:'New input'});
      if(changed==='typed-carrier')request.input[0].internal_chat_message_metadata_passthrough.content_item_kinds=['environments.environment_context'];
      request.client_metadata['x-codex-turn-metadata']=JSON.stringify(metadata);
      assert.throws(()=>o.prepareBody(request,Buffer.from(JSON.stringify(request))),undefined,changed);
    }
    writeFileSync(clock,JSON.stringify({...state,generation:1}));assert.throws(()=>o.prepareBody(delta,Buffer.from(JSON.stringify(delta))));
    assert.equal(ledger.rows.length,1);
  }finally{rmSync(dir,{recursive:true,force:true});}
});


await test('calendar reference cannot be seeded by replayed or rejected terminal settlement',async()=>{
  const binding={turn_id:'public-turn',native_thread_id_sha256:'public-thread-hash',generation:0,
    canonical_clock_state_sha256:'public-clock-hash',native_temporal_context_sha256:'public-temporal-hash'};
  for(const reason of ['replayed','settled-without-terminal','rejected','incomplete']){
    const ledger=new FakeLedger();const o=observer(ledger);
    const id=await ledger.begin('contact','gpt-6-sol','none');const row={attempt_id:id,status:'pending',calendar_binding:binding};
    const completed={type:'response.completed',response:{id:'public-original-response',status:'completed',usage}};
    if(reason==='settled-without-terminal')await o.finish(row,usage);
    if(reason==='rejected')ledger.settle=async()=>false;
    if(reason==='incomplete')await o.event(row,{type:'response.incomplete',response:{id:'public-original-response',status:'incomplete',usage}});
    if(reason==='replayed'){
      await o.event(row,completed);await o.event(row,completed);
      assert.equal(o.nativeCompletedResponses.size,1);
      await assert.rejects(o.event(row,{...completed,response:{...completed.response,id:'public-other-response'}}),/terminal identity changed/);
      assert.equal(o.nativeCompletedResponses.size,1);assert.equal(ledger.stopReason,'native terminal identity changed');
    }else{
      if(reason==='incomplete')await assert.rejects(o.event(row,completed),/terminal identity changed/);
      else await o.event(row,completed);
      assert.equal(o.nativeCompletedResponses.size,0,reason);
    }
  }
});

await test('foreground native turn/start overrides both actual carriers without catalog edits', () => {
  const o=observer(new FakeLedger());const original={model:'gpt-6-sol',effort:null,collaborationMode:{mode:'default',settings:{model:'gpt-6-sol',reasoning_effort:null}}};
  const result=o.turnParams('turn/start',original);
  assert.equal(result.effort,'none');assert.equal(result.collaborationMode.settings.reasoning_effort,'none');
  assert.equal(original.effort,null);assert.equal(o.turnParams('model/list',original),original);
});
await test('every HTTP retry gets parent admission; raw split SSE usage settles before stream closes', async () => {
  const ledger=new FakeLedger();const o=observer(ledger);let physical=0;
  const original=async()=>{
    assert.equal(ledger.rows.at(-1).status,'pending');physical++;
    const text='data: '+JSON.stringify(event)+'\n\n';
    const bytes=new TextEncoder().encode(text);
    return {response:new Response(new ReadableStream({start(c){c.enqueue(bytes.slice(0,7));c.enqueue(bytes.slice(7));c.close();}}),{headers:{'content-type':'text/event-stream'}}),release:async()=>{}};
  };
  for(let n=0;n<2;n++){
    const guarded=await o.fetch(original,{url:'https://chatgpt.com/backend-api/codex/responses',auditContext:'codex-parent-local-inference',init:{}},o.prepareBody(nativeBody()).accounting);
    assert.match(await guarded.response.text(),/response.completed/);await guarded.release();
  }
  assert.equal(physical,2);assert.equal(ledger.rows.length,2);assert.deepEqual(ledger.rows.map(r=>r.usage),[usage,usage]);
  assert.equal(await o.settled(),true);
});
await test('parent refusal and absolute deadline prevent any physical transport', async()=>{
  const ledger=new FakeLedger();const o=observer(ledger);let sent=0;
  ledger.stop('owner stop');
  await assert.rejects(o.fetch(async()=>{sent++;},{url:'https://chatgpt.com/backend-api/codex/responses',auditContext:'codex-parent-local-inference',init:{}},o.prepareBody(nativeBody()).accounting));
  assert.equal(sent,0);assert.equal(ledger.rows.length,0);
  const fresh=new FakeLedger();const expired=new OpenClawAccounting(fresh,9,{now:()=>10});
  await assert.rejects(expired.begin(o.prepareBody(nativeBody()).accounting,'http'));assert.equal(fresh.rows.length,0);
});
await test('stop or deadline during durable context retention prevents both HTTP and WS dispatch',async()=>{
  for(const transport of ['http','websocket']) for(const stop of ['deadline','owner']){
    const ledger=new FakeLedger();let now=10;let physical=0;
    const o=new OpenClawAccounting(ledger,20,{now:()=>now});
    o.captureContext=()=>{if(stop==='deadline')now=21;else ledger.stop('owner stop during retention');};
    const prepared=o.prepareBody(nativeBody());prepared.bytes=Buffer.from(JSON.stringify(prepared.body));
    if(transport==='http'){
      await assert.rejects(o.fetch(async()=>{physical++;},{url:'https://chatgpt.com/backend-api/codex/responses',auditContext:'codex-parent-local-inference',init:{}},prepared.accounting));
    }else{
      class Remote extends EventEmitter{send(){physical++;}terminate(){this.emit('close');}}
      await assert.rejects(o.wsSend(new Remote(),prepared,{},()=>{}));
      await new Promise(resolve=>setImmediate(resolve));
    }
    assert.equal(physical,0);assert.equal(ledger.rows.length,1);assert.equal(ledger.rows[0].usage,null);
    assert.notEqual(o.calls[0].dispatched,true);
  }
});
await test('missing raw usage stops before a retry; rate-limit summaries are not usage', async()=>{
  const ledger=new FakeLedger();const o=observer(ledger);let sent=0;
  const fake=async()=>{sent++;return {response:new Response(JSON.stringify({rateLimits:{remaining:999}}),{status:503}),release:async()=>{}};};
  const params={url:'https://chatgpt.com/backend-api/codex/responses',auditContext:'codex-parent-local-inference',init:{}};
  const guarded=await o.fetch(fake,params,o.prepareBody(nativeBody()).accounting);
  await guarded.response.text();await guarded.release();
  assert.equal(ledger.rows[0].usage,null);assert.equal(ledger.rows[0].status,'unknown');
  await assert.rejects(o.fetch(fake,params,o.prepareBody(nativeBody()).accounting));assert.equal(sent,1);
  assert.equal(JSON.stringify(o.calls).includes('remaining'),false);
});
await test('WebSocket each response frame guarded, terminal usage persisted before next frame; close unknown stops',async()=>{
  const ledger=new FakeLedger();const o=observer(ledger);
  class Remote extends EventEmitter { send(bytes,options,cb){assert.equal(ledger.rows.at(-1).status,'pending');this.sent=(this.sent??0)+1;cb();}terminate(){this.emit('close');} }
  const r=new Remote();const prepared=o.prepareBody(nativeBody('memory'));prepared.bytes=Buffer.from(JSON.stringify(prepared.body));
  for(let n=0;n<2;n++){
    await o.wsSend(r,prepared,{binary:false},()=>{});
    await o.wsEvent(r,Buffer.from(JSON.stringify(event)),false);
    assert.equal(ledger.rows[n].status,'completed');
  }
  await o.wsSend(r,prepared,{binary:false},()=>{});r.emit('close');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(r.sent,3);assert.equal(ledger.rows.length,3);assert.equal(ledger.rows[2].status,'unknown');
  await assert.rejects(o.wsSend(r,prepared,{binary:false},()=>{}));assert.equal(r.sent,3);
});

await test('raw SDK usage survives a rejected parent settlement without replacement by UNKNOWN', async()=>{
  const ledger=new FakeLedger();ledger.settle=async(id,raw)=>{ledger.rows[id].usage=raw;ledger.rows[id].status='rejected';ledger.stop('original cap');return false;};
  const o=observer(ledger);const row=await o.begin(o.prepareBody(nativeBody()).accounting,'http');
  await o.event(row,event);
  assert.deepEqual(o.calls[0].usage,usage);assert.equal(o.calls[0].status,'parent_settlement_failed');
  await assert.rejects(o.begin(o.prepareBody(nativeBody()).accounting,'http'));assert.equal(o.calls.length,1);
});
await test('real existing ParentProcessAccounting protocol owns every fixture attempt and acknowledges closure', async()=>{
  const directory=mkdtempSync(tmpdir()+'/oc-ipc-');
  const bench=new URL('../../benchmark/inspect_ai_v1/',import.meta.url).pathname;
  const source=`
import json,sys,time
sys.path.insert(0,sys.argv[1])
from cp001_accounting import ProviderSampleLedger
from cp001_process_accounting import ParentProcessAccounting
limits={'providerAttempts':16,'providerInputTokens':1048576,'providerOutputTokens':19200}
ledger=ProviderSampleLedger('public_fixture_only',limits,{'contact':('gpt-6-sol','none'),'helper':('gpt-6-astra','medium')})
parent=ParentProcessAccounting(ledger,'public-fixture',sys.argv[2],workers={'openclaw':('contact','helper')}).start()
print(json.dumps(parent.credentials('openclaw')),flush=True)
end=time.monotonic()+10
while parent.workers['openclaw']['terminal'] is None and time.monotonic()<end:time.sleep(.01)
report=parent.report()
parent.close()
print(json.dumps({'attempts':len(ledger.attempts),'complete':report['ledger']['complete'],'errors':report['errors'],'terminal':parent.workers['openclaw']['terminal']}),flush=True)
`;
  const child=spawn('python3',['-u','-c',source,bench,directory+'/p.sock'],{stdio:['ignore','pipe','pipe']});
  let text='';let first;const ready=new Promise(resolve=>{first=resolve;});
  child.stdout.on('data',bytes=>{text+=bytes; if(text.includes('\n')) first(JSON.parse(text.slice(0,text.indexOf('\n'))));});
  let stderr='';child.stderr.on('data',bytes=>stderr+=bytes);
  const done=new Promise(resolve=>child.once('close',resolve));
  const ledger=new OpenClawProcessLedger(await ready);
  try {
    await ledger.connect();
    for(const [role,model,effort] of [['contact','gpt-6-sol','none'],['helper','gpt-6-astra','medium']]) {
      const id=await ledger.begin(role,model,effort);assert.equal(await ledger.settle(id,usage),true);
    }
    const failed=await ledger.begin('helper','gpt-6-astra','medium');await ledger.incomplete(failed);
    await assert.rejects(ledger.begin('contact','gpt-6-sol','none'));
    assert.equal(await ledger.close(),true);assert.equal(await done,0);assert.equal(stderr,'');
    const report=JSON.parse(text.trim().split('\n').at(-1));
    assert.equal(report.attempts,3);assert.equal(report.complete,false);assert.deepEqual(report.errors,[]);
    assert.equal(report.terminal.tracked_http_settled,true);assert.equal(report.terminal.pending_local,0);
  } finally {child.kill();rmSync(directory,{recursive:true,force:true});}
});
await test('native transport failure class/code/status retained; message/body excluded in HTTP and WS',async()=>{
  const error=Object.assign(new Error('PRIVATE message must never be retained'),{name:'ConnectError',code:'ECONNRESET',statusCode:502,body:'PRIVATE body'});
  const ledger=new FakeLedger();const o=observer(ledger);
  await assert.rejects(o.fetch(async()=>{throw error;},{url:'https://chatgpt.com/backend-api/codex/responses',auditContext:'codex-parent-local-inference',init:{}},o.prepareBody(nativeBody()).accounting));
  assert.deepEqual(o.calls[0].transport_failure,{error_class:'ConnectError',error_code:'ECONNRESET',http_status:502});
  assert.equal(JSON.stringify(o.calls).includes('PRIVATE'),false);
  const other=observer(new FakeLedger());class Remote extends EventEmitter {send(bytes,options,cb){cb(error);}terminate(){this.emit('close');}}
  const remote=new Remote();const prepared=other.prepareBody(nativeBody());prepared.bytes=Buffer.from(JSON.stringify(prepared.body));
  await other.wsSend(remote,prepared,{},()=>{});await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(other.calls[0].transport_failure,{error_class:'ConnectError',error_code:'ECONNRESET',http_status:502});
  assert.equal(JSON.stringify(other.calls).includes('PRIVATE'),false);
});
await test('native callback evidence survives before terminal and excludes raw tool or result contents',()=>{
  const dir=mkdtempSync(tmpdir()+'/openclaw-observation-');chmodSync(dir,0o700);
  const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,observeTools:true,privateModelContextDir:dir});
  const invocation={toolUseId:'public-tool',toolName:'exec',turnId:'public-turn',rawPayload:{session_id:'public-thread',arguments:'PRIVATE_BODY_MUST_NOT_APPEAR'}};
  const registration={sessionId:'public-registration',relayId:'public-relay',generation:'public-generation',signal:{aborted:false}};
  try{
    o.nativeToolBefore(invocation,registration);
    o.nativeNotification({}, {method:'item/completed',params:{threadId:'public-thread',turnId:'public-turn',item:{id:'public-tool',type:'commandExecution',status:'completed',exitCode:0,aggregatedOutput:'PRIVATE_RESULT_MUST_NOT_APPEAR'}}});
    assert.equal(ledger.stopReason,null);
    const records=o.nativeObservationReceipts.map(r=>JSON.parse(readFileSync(r.path)));
    assert.deepEqual(records.map(r=>r.phase),['pre_observer_entry','reserved','item_completed','native_terminal_settled']);
    assert.deepEqual(records.map(r=>r.sequence),[1,2,3,4]);
    assert.equal(records[2].tool_rows[0].status,'reserved');assert.equal(records[2].exit_code,0);
    assert.equal(records[3].tool_rows[0].status,'settled');assert.equal(records[3].first_coverage_failure,null);
    assert.equal(JSON.stringify(records).includes('PRIVATE_'),false);
    assert.equal(JSON.stringify(records).includes('public-thread'),false);
    assert.equal(o.calls.length,0);
  }finally{rmSync(dir,{recursive:true,force:true});}
});

await test('native preparation failure retains exact private bytes and only hashes the error text',()=>{
  const dir=mkdtempSync(tmpdir()+'/openclaw-preparation-error-');chmodSync(dir,0o700);
  const o=new OpenClawAccounting(new FakeLedger(),20,{now:()=>10,observeTools:true,privateModelContextDir:dir});
  const body=Buffer.from(JSON.stringify({input:'PRIVATE_NATIVE_INPUT'}));
  try{
    o.nativePreparationFailure('evaluator_body_bind',new Error('PRIVATE_ERROR_DETAIL'),body);
    const record=JSON.parse(readFileSync(o.nativeObservationReceipts[0].path));
    assert.equal(record.phase,'native_preparation_failure');assert.equal(record.stage,'evaluator_body_bind');
    assert.deepEqual(readFileSync(record.failed_native_body.path),body);
    assert.equal(JSON.stringify(record).includes('PRIVATE_'),false);
    assert.equal(o.calls.length,0);assert.equal(o.ledger.stopReason,null);
  }finally{rmSync(dir,{recursive:true,force:true});}
});

await test('stop or deadline during durable native reservation refuses the dynamic body',async()=>{
  for(const cause of ['deadline','stop']){
    const dir=mkdtempSync(tmpdir()+'/openclaw-reservation-');let now=10;const ledger=new FakeLedger();
    const o=new OpenClawAccounting(ledger,20,{now:()=>now,observeTools:true,privateModelContextDir:dir});
    const original=o.retainPrivate.bind(o);let bodies=0;
    o.retainPrivate=(bytes,label)=>{
      const retained=original(bytes,label);
      if(label==='native-observation' && JSON.parse(bytes).phase==='reserved'){
        if(cause==='deadline')now=21;else ledger.stop('public stop during retention');
      }
      return retained;
    };
    try{
      await assert.rejects(o.dynamicTool({callId:'public-denied',tool:'exec',threadId:'public-thread'},async()=>{bodies++;}),/deadline|stopped/);
      assert.equal(bodies,0);assert.equal(o.tools.get('public-denied').dispatched,false);
      assert.equal(o.tools.get('public-denied').status,'cancelled');
      assert.equal(JSON.parse(readFileSync(o.nativeObservationReceipts.at(-1).path)).phase,'cancelled_before_dispatch');
    }finally{rmSync(dir,{recursive:true,force:true});}
  }
});

// Mock only an already pinned native proxy's imports after ordinary registry resolution.
// Captured plugins can resolve SDK aliases before the test's resolve hook is consulted.
const mockNativeProxy = (wsSDK, observer) => registerHooks({load(url,context,next){
  const result=next(url,context);
  if(!new URL(url).pathname.endsWith('/inference-proxy-b-aJMSxm.mjs')) return result;
  if(!observer.nativeModuleBindings.has(url))throw new Error('unverified native mock proxy');
  let source=Buffer.from(result.source).toString('utf8');
  const mocks={'ssrf-runtime':'export const fetchWithSsrFGuard=p=>globalThis.__openclawPublicFakeFetch(p);export const isBlockedHostnameOrIp=()=>false;export const resolvePinnedHostnameWithPolicy=()=>({lookup:()=>{throw new Error("External DNS forbidden")}});'};
  if(wsSDK){
    mocks['websocket-runtime']='export {WebSocketServer,rejectWebSocketUpgrade} from '+JSON.stringify(wsSDK)+';export const WebSocket=globalThis.__openclawPublicFakeWebSocket;';
    mocks['fetch-runtime']='export const createNodeProxyAgent=()=>null;';
  }
  for(const [name,implementation] of Object.entries(mocks)){
    const original='from "openclaw/plugin-sdk/'+name+'";';
    if(source.split(original).length!==2)throw new Error('native mock import differs');
    source=source.replace(original,'from '+JSON.stringify('data:text/javascript,'+encodeURIComponent(implementation))+';');
  }
  return {...result,source};
}});
const plugin=process.env.OPENCLAW_PINNED_PLUGIN;

await test('pre-retirement native readback survives close; physical exit never relabels uncertain cleanup',async()=>{
  const directory=mkdtempSync(tmpdir()+'/openclaw-retirement-');chmodSync(directory,0o700);
  const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,privateModelContextDir:directory,observeTools:true});
  let exitHandler;const order=[];
  const client={closed:false,transportExited:false,serverRequests:{active:new Set()},pending:new Map(),
    addTransportExitHandler(handler){exitHandler=handler;},
    async request(method){order.push(method);assert.equal(this.closed,false);
      return method==='thread/read'?{thread:{id:'public-thread',status:{type:'idle'},turns:[{status:'completed'}]}}:{data:[]};},
    async closeAndWait(){order.push('close');this.closed=true;this.transportExited=true;exitHandler(this);return {cleanup:'uncertain'};}
  };
  try{
    o.turnParams('turn/start',{threadId:'public-thread'},client);
    o.nativeQueues.set('public-module',{packageRoot:'public-package',queue:{async flush(){}}});
    await o.beforeNativeClose(client);const nativeResult=await client.closeAndWait();o.nativeClosed.set(client,nativeResult);
    assert.deepEqual(order,['thread/read','thread/backgroundTerminals/list','close']);
    const retained=o.nativeCloseReads.get(client);assert.equal(retained.length,1);assert.equal(retained[0].active_turns,0);
    assert.equal(JSON.parse(readFileSync(retained[0].private_native_thread.path)).thread.turns[0].status,'completed');
    await assert.rejects(o.nativeSettlement(),/native close\/readback was not confirmed/);
    const closure=await o.closeNative();assert.equal(closure.native_closed,false);assert.equal(closure.physical_transports_exited,true);
    assert.equal(closure.transport_results[0].cleanup,'uncertain');assert.equal(closure.pending_sdk,0);assert.equal(closure.pending_tools,0);
    const phases=o.nativeObservationReceipts.map(r=>JSON.parse(readFileSync(r.path)));
    assert.deepEqual(phases.map(r=>r.phase),['native_pre_retirement_readback','native_transport_exit']);
    assert.equal(phases[0].client_closed,false);assert.equal(phases[1].matched_client,true);
    assert.equal(ledger.rows.length,0);
  }finally{rmSync(directory,{recursive:true,force:true});}
});
await test('retirement observation refuses pending/background work and requires the actual matching exit event',async()=>{
  for(const reason of ['pending','background','closed-without-exit','mismatched-exit']){
    const directory=mkdtempSync(tmpdir()+'/openclaw-retirement-negative-');chmodSync(directory,0o700);
    const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,privateModelContextDir:directory,observeTools:true});
    let handler;const client={closed:false,transportExited:false,serverRequests:{active:new Set()},pending:new Map(),
      addTransportExitHandler(h){handler=h;},async request(method){return method==='thread/read'
        ?{thread:{id:'public-thread',status:{type:'idle'},turns:[{status:'completed'}]}}
        :{data:reason==='background'?[{id:'public-active-terminal'}]:[]};}};
    try{
      o.turnParams('turn/start',{threadId:'public-thread'},client);
      o.nativeQueues.set('public-module',{packageRoot:'public-package',queue:{async flush(){}}});
      if(reason==='pending')client.pending.set('public-unresolved',{});
      if(['pending','background'].includes(reason)){
        await assert.rejects(o.beforeNativeClose(client));assert.equal(o.nativeCloseReads.has(client),false);
      }else await o.beforeNativeClose(client);
      client.closed=true;o.nativeClosed.set(client,{cleanup:'uncertain'});
      if(reason==='mismatched-exit'){client.transportExited=true;handler({transportExited:true});}
      const closure=await o.closeNative();assert.equal(closure.native_closed,false);assert.equal(closure.physical_transports_exited,false);
      assert.equal(closure.transport_results[0].cleanup,'uncertain');assert.equal(ledger.rows.length,0);
    }finally{rmSync(directory,{recursive:true,force:true});}
  }
});

await test('actual installed proxy and native client load exact overlay; public fake HTTP and WS pass that ordinary boundary', {skip:!plugin}, async()=>{
  const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,Number(process.hrtime.bigint())/1e9+15);
  let physical=0;let actualWire;
  const host=process.env.OPENCLAW_PINNED_HOST;
  const wsSDK=host && pathToFileURL(host+'/dist/plugin-sdk/websocket-runtime.js').href;
  class FakeRemote extends EventEmitter {
    static OPEN=1;
    constructor(target){super();assert.equal(target.protocol,'wss:');this.readyState=0;this.bufferedAmount=0;queueMicrotask(()=>{this.readyState=1;this.emit('open');});}
    send(bytes,options,callback){assert.equal(ledger.rows.at(-1).status,'pending');physical++;actualWire=JSON.parse(bytes);callback();queueMicrotask(()=>this.emit('message',Buffer.from(JSON.stringify(event)),false));}
    terminate(){if(this.readyState===3)return;this.readyState=3;this.emit('close');}
  }
  globalThis.__openclawPublicFakeWebSocket=FakeRemote;
  globalThis.__openclawPublicFakeFetch=async params=>{
    assert.equal(ledger.rows.at(-1).status,'pending');
    physical++;actualWire=JSON.parse(await new Response(params.init.body).text());
    return {response:new Response('data: '+JSON.stringify(event)+'\n\n',{headers:{'content-type':'text/event-stream'}}),release:async()=>{}};
  };
  const hook=installOpenClawAccounting(o,plugin,NATIVE_PINS);let proxy;
  const fake=mockNativeProxy(wsSDK,o);
  try {
    const {createCodexInferenceProxy}=await import(pathToFileURL(plugin+'/dist/.setup/inference-proxy-b-aJMSxm.mjs'));
    const {t:Client}=await import(pathToFileURL(plugin+'/dist/.setup/client-Cs08OXVQ.mjs'));
    const fakeClient=Object.create(Client.prototype);fakeClient.requestWithOverloadRetry=async(method,params)=>params;
    const turn=await fakeClient.request('turn/start',{effort:null,model:'gpt-6-sol'});
    assert.equal(turn.effort,'none');
    proxy=await createCodexInferenceProxy({upstream:'https://chatgpt.com/backend-api/codex',assertCurrent:()=>{}});
    const body=nativeBody('memory');
    const result=await fetch(proxy.baseUrl+'/responses',{method:'POST',body:JSON.stringify(body)});
    assert.equal(result.status,200);await result.text();
    assert.equal(physical,1);assert.equal(actualWire.model,'gpt-6-astra');assert.equal(actualWire.reasoning.effort,'medium');
    assert.deepEqual(actualWire.input,body.input);assert.deepEqual(ledger.rows[0].usage,usage);
    assert.deepEqual([...hook.loaded].sort(),Object.keys(NATIVE_PINS).sort());
    if(wsSDK){
      const {WebSocket:Local}=await import(wsSDK);const local=new Local(proxy.baseUrl.replace('http:','ws:')+'/responses');
      await new Promise((resolve,reject)=>{local.once('open',resolve);local.once('error',reject);});
      try {
        for(let n=0;n<2;n++){
          const received=new Promise((resolve,reject)=>{local.once('message',resolve);local.once('error',reject);});
          local.send(JSON.stringify(body));await received;
          assert.equal(ledger.rows.at(-1).status,'completed');
        }
        assert.equal(physical,3);assert.equal(ledger.rows.length,3);
      }finally{local.terminate();}
    }
  } finally {await proxy?.close();hook.close();fake.deregister();delete globalThis.__openclawPublicFakeFetch;delete globalThis.__openclawPublicFakeWebSocket;}
});

await test('native dynamic identity is recorded privately without granting unbound terminal coverage', () => {
  const directory=mkdtempSync(tmpdir()+'/openclaw-dynamic-identity-');chmodSync(directory,0o700);
  const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,observeTools:true,privateModelContextDir:directory});
  const invocation={toolUseId:'public-dynamic-call',toolName:'mcp__public_namespace__public_lookup',
    turnId:'public-dynamic-turn',rawPayload:{session_id:'public-dynamic-thread'}};
  try {
    const row=o.nativeToolBefore(invocation);
    o.nativeNotification({}, {method:'item/completed',params:{threadId:'public-dynamic-thread',turnId:'public-dynamic-turn',
      item:{id:invocation.toolUseId,type:'dynamicToolCall',status:'failed',tool:'public_lookup',namespace:'public_namespace',success:false}}});
    assert.equal(o.toolCoverage,false);assert.ok(ledger.stopReason);
    assert.equal(row.dispatched,false);assert.equal(row.status,'reserved');assert.equal(row.native_terminal,undefined);
    const receipts=o.nativeObservationReceipts.map(r=>readFileSync(r.path,'utf8'));
    const terminal=receipts.map(x=>JSON.parse(x)).find(x=>x.phase==='item_completed');
    const hash=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
    assert.equal(terminal.native_tool_name_sha256,hash('public_lookup'));
    assert.equal(terminal.native_tool_namespace_sha256,hash('public_namespace'));
    assert.equal(terminal.native_tool_namespace_is_null,false);
    assert.equal(receipts.join('').includes('public_lookup'),false);
    assert.equal(receipts.join('').includes('public_namespace'),false);
  } finally { rmSync(directory,{recursive:true,force:true}); }
});

await test('correlated native terminal before post settles once and keeps result separate from dispatch', () => {
  const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,observeTools:true});
  const invocation={toolUseId:'public-order-tool',toolName:'exec',turnId:'public-order-turn',rawPayload:{session_id:'public-order-thread'}};
  const row=o.nativeToolBefore(invocation);
  assert.equal(row.dispatched,false);assert.equal(row.status,'reserved');
  const notification={method:'item/completed',params:{threadId:'public-order-thread',turnId:'public-order-turn',
    item:{id:invocation.toolUseId,type:'commandExecution',status:'completed',exitCode:2}}};
  o.nativeNotification({},notification);
  assert.equal(row.dispatched,true);assert.equal(row.status,'settled');assert.equal(o.toolCoverage,true);
  assert.equal(ledger.stopReason,null);assert.equal(row.native_terminal.exit_code,2);
  const settled=row.settled_monotonic;o.nativeNotification({},notification);
  o.nativeToolAfter(invocation);
  assert.equal(o.tools.size,1);assert.equal(row.native_terminal.exit_code,2);
  assert.equal(row.settled_monotonic,settled);assert.equal(ledger.stopReason,null);
});

await test('native patch terminal before post settles once without requiring command metadata', () => {
  const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,observeTools:true});
  const invocation={toolUseId:'public-patch',toolName:'apply_patch',turnId:'public-patch-turn',rawPayload:{session_id:'public-patch-thread'}};
  const row=o.nativeToolBefore(invocation);
  const p={threadId:'public-patch-thread',turnId:'public-patch-turn',
    item:{id:invocation.toolUseId,type:'fileChange',status:'completed',changes:[{path:'public-note.txt',kind:'add',diff:'+public fixture'}]}};
  o.nativeNotification({}, {method:'item/completed',params:p});
  assert.equal(row.dispatched,true);assert.equal(row.status,'settled');
  assert.equal(o.toolCoverage,true);assert.equal(ledger.stopReason,null);
  assert.equal(row.native_terminal.item_type,'fileChange');
  assert.equal('exit_code' in row.native_terminal,false);
  assert.match(row.native_terminal.changes_sha256,/^[a-f0-9]{64}$/);
  assert.equal(JSON.stringify(row).includes('public-note.txt'),false);
  const settled=row.settled_monotonic;
  o.nativeNotification({}, {method:'item/completed',params:p});o.nativeToolAfter(invocation);
  assert.equal(o.tools.size,1);assert.equal(row.settled_monotonic,settled);
  assert.equal(ledger.stopReason,null);
  p.item.changes=[{path:'changed.txt',kind:'add',diff:'+different'}];
  o.nativeNotification({}, {method:'item/completed',params:p});
  assert.equal(o.toolCoverage,false);assert.equal(ledger.stopReason,'native tool terminal changed');
});

await test('native failed terminal settles the admitted attempt and preserves failure before post', () => {
  for(const type of ['commandExecution','fileChange']) {
    const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,observeTools:true});
    const invocation={toolUseId:'public-failed',toolName:type==='commandExecution'?'exec':'apply_patch',
      turnId:'public-failed-turn',rawPayload:{session_id:'public-failed-thread'}};
    const row=o.nativeToolBefore(invocation);
    const item={id:invocation.toolUseId,type,status:'failed',...(type==='commandExecution'?{exitCode:127}:{changes:[]})};
    const notification={method:'item/completed',params:{threadId:'public-failed-thread',turnId:'public-failed-turn',item}};
    o.nativeNotification({},notification);
    assert.equal(row.dispatched,true);assert.equal(row.status,'settled');
    assert.equal(row.native_terminal.status,'failed');assert.equal(o.toolCoverage,true);
    assert.equal(ledger.stopReason,null);
    if(type==='commandExecution')assert.equal(row.native_terminal.exit_code,127);
    const settled=row.settled_monotonic;o.nativeNotification({},notification);o.nativeToolAfter(invocation);
    assert.equal(row.native_terminal.status,'failed');assert.equal(row.settled_monotonic,settled);
    assert.equal(o.tools.size,1);assert.equal(ledger.stopReason,null);
    // A failed operation is accounted, so a following independently admitted tool may recover.
    assert.equal(o.nativeToolBefore({...invocation,toolUseId:'public-recovery'}).status,'reserved');
  }
});

await test('native failed terminal cannot bypass identity or required typed metadata', () => {
  for(const type of ['commandExecution','fileChange'])for(const changed of ['unreserved','thread','turn','name','metadata','status','cancelled']) {
    const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,observeTools:true});
    const invocation={toolUseId:'public-failed-negative',toolName:type==='commandExecution'?'exec':'apply_patch',
      turnId:'public-turn',rawPayload:{session_id:'public-thread'}};
    if(changed==='name')invocation.toolName='read';
    const row=changed==='unreserved'?null:o.nativeToolBefore(invocation);
    if(changed==='cancelled')o.cancelNativeTool(invocation);
    const item={id:invocation.toolUseId,type,status:'failed',...(type==='commandExecution'?{exitCode:127}:{changes:[]})};
    const params={threadId:'public-thread',turnId:'public-turn',item};
    if(changed==='thread')params.threadId='other';
    if(changed==='turn')params.turnId='other';
    if(changed==='metadata')delete item[type==='commandExecution'?'exitCode':'changes'];
    if(changed==='status')item.status='declined';
    o.nativeNotification({}, {method:'item/completed',params});
    assert.equal(o.toolCoverage,false,changed);assert.ok(ledger.stopReason,changed);
    assert.equal(row?.dispatched??false,false,changed);
  }
});

await test('native patch terminal rejects missing or mismatched admission before and after post', () => {
  for(const afterPost of [false,true])for(const changed of ['unreserved','cancelled','thread','turn','name','status','changes','type','missing-turn']) {
    const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,observeTools:true});
    const invocation={toolUseId:'public-negative-patch',toolName:'apply_patch',turnId:'public-patch-turn',rawPayload:{session_id:'public-patch-thread'}};
    if(changed==='name')invocation.toolName='exec';
    if(changed==='missing-turn')delete invocation.turnId;
    const row=changed==='unreserved'?null:o.nativeToolBefore(invocation);
    if(changed==='cancelled')o.cancelNativeTool(invocation);
    if(afterPost && row && changed!=='cancelled')o.nativeToolAfter(invocation);
    const p={threadId:'public-patch-thread',turnId:'public-patch-turn',
      item:{id:invocation.toolUseId,type:'fileChange',status:'completed',changes:[]}};
    if(changed==='thread')p.threadId='other-thread';
    if(changed==='turn')p.turnId='other-turn';
    if(changed==='status')p.item.status='inProgress';
    if(changed==='changes')delete p.item.changes;
    if(changed==='type')p.item.type='commandExecution';
    o.nativeNotification({}, {method:'item/completed',params:p});
    assert.equal(o.toolCoverage,false,changed);assert.ok(ledger.stopReason,changed);
    if(!afterPost)assert.equal(row?.dispatched??false,false,changed);
  }
});

await test('native terminal requires exact admitted command identity and real terminal metadata', () => {
  for(const changed of ['unreserved','cancelled','thread','turn','name','status','exit','type','missing-turn']) {
    const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,observeTools:true});
    const invocation={toolUseId:'public-negative-tool',toolName:'exec',turnId:'public-negative-turn',rawPayload:{session_id:'public-negative-thread'}};
    if(changed==='name')invocation.toolName='read';
    if(changed==='missing-turn')delete invocation.turnId;
    const row=changed==='unreserved'?null:o.nativeToolBefore(invocation);
    if(changed==='cancelled')o.cancelNativeTool(invocation);
    const p={threadId:'public-negative-thread',turnId:'public-negative-turn',
      item:{id:invocation.toolUseId,type:'commandExecution',status:'completed',exitCode:0}};
    if(changed==='thread')p.threadId='different-thread';
    if(changed==='turn')p.turnId='different-turn';
    if(changed==='status')p.item.status='inProgress';
    if(changed==='exit')delete p.item.exitCode;
    if(changed==='type')p.item.type='futureNativeOperation';
    o.nativeNotification({}, {method:'item/completed',params:p});
    assert.equal(o.toolCoverage,false,changed);assert.ok(ledger.stopReason,changed);
    assert.equal(row?.dispatched??false,false,changed);
  }
});

await test('settled command still rejects altered terminal identity and post cannot promote a cancelled or mismatched row', () => {
  for(const changed of ['thread','turn','status','exit']) {
    const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,observeTools:true});
    const invocation={toolUseId:'public-settled',toolName:'exec',turnId:'public-turn',rawPayload:{session_id:'public-thread'}};
    o.nativeToolBefore(invocation);
    const p={threadId:'public-thread',turnId:'public-turn',item:{id:'public-settled',type:'commandExecution',status:'completed',exitCode:0}};
    o.nativeNotification({}, {method:'item/completed',params:p});
    if(changed==='thread')p.threadId='other-thread';
    if(changed==='turn')p.turnId='other-turn';
    if(changed==='status')p.item.status='inProgress';
    if(changed==='exit')delete p.item.exitCode;
    o.nativeNotification({}, {method:'item/completed',params:p});
    assert.equal(o.toolCoverage,false,changed);assert.ok(ledger.stopReason,changed);
  }
  for(const changed of ['cancelled','thread','turn','name']) {
    const ledger=new FakeLedger();const o=new OpenClawAccounting(ledger,20,{now:()=>10,observeTools:true});
    const invocation={toolUseId:'public-post',toolName:'exec',turnId:'public-turn',rawPayload:{session_id:'public-thread'}};
    const row=o.nativeToolBefore(invocation);
    if(changed==='cancelled')o.cancelNativeTool(invocation);
    if(changed==='thread')invocation.rawPayload.session_id='other-thread';
    if(changed==='turn')invocation.turnId='other-turn';
    if(changed==='name')invocation.toolName='read';
    assert.throws(()=>o.nativeToolAfter(invocation),/without matching admission/);
    assert.equal(row.dispatched,false,changed);assert.equal(o.toolCoverage,false,changed);
    assert.ok(ledger.stopReason,changed);
  }
});

// Actual owned relay planning, with fresh public state; no native actor/model request.
await test('actual owned relay activates synchronous pre and post planning without changing ordinary public relay policy',
  {skip:!process.env.OPENCLAW_PINNED_PLUGIN || !process.env.OPENCLAW_PINNED_HOST}, async()=>{
  const plugin=process.env.OPENCLAW_PINNED_PLUGIN;const host=process.env.OPENCLAW_PINNED_HOST;
  const state=mkdtempSync(tmpdir()+'/openclaw-owned-relay-');const previous=process.env.OPENCLAW_STATE_DIR;
  process.env.OPENCLAW_STATE_DIR=state;
  const o=new OpenClawAccounting(new FakeLedger(),20,{now:()=>10,observeTools:true});
  const overlay=installOpenClawAccounting(o,plugin,NATIVE_PINS,{hostRoot:host});const relays=[];
  try{
    const {i:owned,r:ordinary}=await import(pathToFileURL(host+'/dist/native-hook-relay-Bfyv8cuh.mjs'));
    const params={provider:'codex',sessionId:randomUUID(),allowedEvents:['pre_tool_use','post_tool_use'],preToolUseLoopDetection:false};
    const observed=owned(params);relays.push(observed);await observed.ready;
    assert.equal(observed.shouldRelayEvent('pre_tool_use'),true);
    assert.equal(observed.shouldRelayEvent('post_tool_use'),true);
    assert.equal(observed.toolMatcherForEvent('pre_tool_use'),undefined);
    assert.equal(observed.toolMatcherForEvent('post_tool_use'),undefined);
    assert.deepEqual(observed.allowedEvents,params.allowedEvents);
    const unchanged=ordinary({...params,sessionId:randomUUID(),__evaToolAccounting:true});relays.push(unchanged);await unchanged.ready;
    assert.equal(unchanged.shouldRelayEvent('pre_tool_use'),false);
    assert.equal(unchanged.shouldRelayEvent('post_tool_use'),false);
    assert.equal(o.tools.size,0);assert.equal(o.calls.length,0);
  }finally{
    for(const relay of relays)relay.unregister();
    for(const relay of relays)await relay.drain();
    overlay.close();
    if(previous===undefined)delete process.env.OPENCLAW_STATE_DIR;else process.env.OPENCLAW_STATE_DIR=previous;
    rmSync(state,{recursive:true,force:true});
  }
});

// One bundled actual ordinary CLI fixture. Its profile is supplied by the private
// preparation owner; all input/tool responses below are public and upstream is fake.
await test('native frozen scope proves equivalent retirement without relabeling uncertain cleanup',async()=>{
  const ledger=new FakeLedger();const o=observer(ledger);o.observeTools=true;let inspections=0;
  const client={closed:true,serverRequests:{active:new Map()},pending:new Map()};
  o.clients.set(client,new Set(['scope-thread']));o.nativeCloseReads.set(client,[{thread_id:'scope-thread',turn_count:1}]);
  o.nativeClosed.set(client,{cleanup:'uncertain'});o.nativeTransportExits.set(client,{transport_exited:true,matched_client:true});
  o.nativeSourceAuthorities.set(client,undefined);o.nativeQueues.set('scope-unit',{packageRoot:'/public',queue:{flush:async()=>{}}});
  o.nativeProcessScope={inspect:async binding=>{inspections++;return {contract:'RootOwnedNativeProcessScope/v1',phase:'retirement',
    frozen_confirmed:true,only_registered_controllers:true,native_workload_processes:0,binding};}};
  assert.equal((await o.nativeSettlement()).length,1);
  const result=await o.closeNative();assert.equal(result.native_closed,false);assert.equal(result.equivalent_native_settled,true);
  assert.equal(result.transport_results[0].cleanup,'uncertain');assert.equal(inspections,2);
  for(const fault of ['active-source','missing-source','live-workload','binding-change']){
    const bad=observer(new FakeLedger());bad.observeTools=true;bad.clients.set(client,new Set(['scope-thread']));
    bad.nativeCloseReads.set(client,[{thread_id:'scope-thread'}]);bad.nativeClosed.set(client,{cleanup:'uncertain'});
    bad.nativeQueues.set('scope-unit',{packageRoot:'/public',queue:{flush:async()=>{}}});
    bad.nativeTransportExits.set(client,{transport_exited:true});
    if(fault!=='missing-source')bad.nativeSourceAuthorities.set(client,fault==='active-source'?{holds:1}:null);
    bad.nativeProcessScope={inspect:async binding=>({contract:'RootOwnedNativeProcessScope/v1',phase:'retirement',
      frozen_confirmed:true,only_registered_controllers:true,native_workload_processes:fault==='live-workload'?1:0,
      binding:fault==='binding-change'?{}:binding})};
    await assert.rejects(bad.nativeSettlement(),/native (process\/source scope not settled|frozen scope receipt differs)/);
  }
});

async function registeredRootScope(){
  const control=JSON.parse(process.env.OPENCLAW_ROOT_SCOPE_CONTROL);
  const stop=control.deadline_monotonic;
  while(!existsSync(control.socket_path)){
    assert.ok(Number(process.hrtime.bigint())/1e9<stop,'root scope controller unavailable');
    await new Promise(resolve=>setTimeout(resolve,10));
  }
  const scope=new OpenClawNativeProcessScope(control);await scope.register();return scope;
}
await test('real root frozen scope rejects a surviving local descendant',
  {skip:process.env.OPENCLAW_ROOT_SCOPE_NEGATIVE!=='1'},async()=>{
    const scope=await registeredRootScope();
    const binding={public_fixture:'no-native-work'};
    assert.equal((await scope.inspect(binding)).native_workload_processes,0);
    const {spawn}=await import('node:child_process');const {once}=await import('node:events');
    const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});
    try{await assert.rejects(scope.inspect({public_fixture:'surviving-descendant'}),/scope controller closed without receipt/);}
    finally{child.kill();await once(child,'exit');}
});

await test('public ordinary actor composition binds native calendar, model bodies, tools and one-shot readbacks',
  {skip:process.env.OPENCLAW_PUBLIC_COMPOSITION !== '1'}, async()=>{
  const plugin=process.env.OPENCLAW_PINNED_PLUGIN;const host=process.env.OPENCLAW_PINNED_HOST;
  const workspace=process.env.OPENCLAW_PUBLIC_WORKSPACE;const clockFile=process.env.OPENCLAW_PUBLIC_CLOCK;
  const contexts=process.env.OPENCLAW_PUBLIC_CONTEXTS;const operatorState=process.env.OPENCLAW_OPERATOR_STATE_DIR;
  assert.ok(plugin && host && workspace && clockFile && contexts && operatorState && process.env.OPENCLAW_CONFIG_PATH);
  const {a:withPluginInstallRoots,i:resolvePluginInstallRoots}=await import(pathToFileURL(host+'/dist/install-root-context-DEOzYnOP.mjs'));
  assert.equal(process.env.HOME,dirname(operatorState),'native HOME must belong to the existing UID1000 operator');
  return await withPluginInstallRoots(resolvePluginInstallRoots({...process.env,OPENCLAW_STATE_DIR:operatorState}),async()=>{
  const ledger=new FakeLedger({attempts:16,inputTokens:1048576,outputTokens:19200,pendingUsage:usage});const deadline=process.env.OPENCLAW_ROOT_SCOPE_CONTROL?JSON.parse(process.env.OPENCLAW_ROOT_SCOPE_CONTROL).deadline_monotonic:Number(process.hrtime.bigint())/1e9+120;
  const nativeProcessScope=process.env.OPENCLAW_ROOT_SCOPE_CONTROL?await registeredRootScope():null;
  const o=new OpenClawAccounting(ledger,deadline,{clockFile,privateModelContextDir:contexts,observeTools:true,nativeProcessScope});
  const publishClock=(generation,offsetMilliseconds)=>{
    const path=clockFile+'.tmp';writeFileSync(path,JSON.stringify({contract:'EvaScenarioCalendar/v1',generation,offsetMilliseconds}),{mode:0o600});renameSync(path,clockFile);
  };
  const target=Date.parse('2020-01-02T12:00:00.000Z');const offset=target-Date.now();publishClock(0,offset);
  writeFileSync(workspace+'/MEMORY.md','Public persistent memory marker: blue notebook.\n',{mode:0o600});
  writeFileSync(workspace+'/USER.md','Public fixture owner.\n',{mode:0o600});
  const failedToolRecovery=process.env.OPENCLAW_PUBLIC_FAILED_TOOL_RECOVERY==='1';
  const completed=[];let physical=0;let toolRequested=false;let counter=0;
  const fakeTerminal=body=>{
    const responseId='public-response-'+(++counter);const tool=body.tools?.find(t=>t.type==='function' && t.name==='exec_command');
    const output=!toolRequested && tool ? (toolRequested=true,Array.from({length:16},(_,i)=>({type:'function_call',id:'public-function-'+counter+'-'+i,call_id:'public-call-'+counter+'-'+i,name:tool.name,arguments:JSON.stringify({cmd:failedToolRecovery&&i===15?'exit 127':'cat MEMORY.md',workdir:workspace,login:false,max_output_tokens:50,yield_time_ms:1000})})))
      : [{type:'message',id:'public-message-'+counter,role:'assistant',status:'completed',content:[{type:'output_text',text:'Public fixture reply.',annotations:[]}]}];
    const response={id:responseId,object:'response',created_at:1,status:'completed',model:body.model,output,usage};
    return [{type:'response.created',response:{...response,status:'in_progress',output:[]}},
      ...output.flatMap((item,i)=>[{type:'response.output_item.added',output_index:i,item},...(item.type==='message'?
        [{type:'response.content_part.added',item_id:item.id,output_index:i,content_index:0,part:item.content[0]},
         {type:'response.output_text.delta',item_id:item.id,output_index:i,content_index:0,delta:item.content[0].text},
         {type:'response.output_text.done',item_id:item.id,output_index:i,content_index:0,text:item.content[0].text},
         {type:'response.content_part.done',item_id:item.id,output_index:i,content_index:0,part:item.content[0]}]:[]),
        {type:'response.output_item.done',output_index:i,item}]),{type:'response.completed',response}];
  };
  globalThis.__openclawPublicFakeFetch=async params=>{
    assert.equal(ledger.rows.at(-1).status,'pending');physical++;
    const bytes=Buffer.from(await new Response(params.init.body).arrayBuffer());const body=JSON.parse(bytes);
    const capture=o.calls.at(-1).native_model_context;assert.deepEqual(readFileSync(capture.path),bytes);
    completed.push({body,transport:'http'});
    return {response:new Response(fakeTerminal(body).map(value=>'data: '+JSON.stringify(value)+'\n\n').join(''),{headers:{'content-type':'text/event-stream'}}),release:async()=>{}};
  };
  class FakeRemote extends EventEmitter {
    static OPEN=1;
    constructor(target){super();assert.equal(target.protocol,'wss:');this.readyState=0;this.bufferedAmount=0;queueMicrotask(()=>{this.readyState=1;this.emit('open');});}
    send(bytes,options,callback){assert.equal(ledger.rows.at(-1).status,'pending');physical++;
      assert.deepEqual(readFileSync(o.calls.at(-1).native_model_context.path),bytes);
      const body=JSON.parse(bytes);completed.push({body,transport:'websocket'});callback();
      for(const value of fakeTerminal(body)) queueMicrotask(()=>this.emit('message',Buffer.from(JSON.stringify(value)),false));}
    terminate(){if(this.readyState===3)return;this.readyState=3;this.emit('close');}
  }
  globalThis.__openclawPublicFakeWebSocket=FakeRemote;
  const wsSDK=pathToFileURL(host+'/dist/plugin-sdk/websocket-runtime.js').href;
  const hook=installOpenClawAccounting(o,plugin,NATIVE_PINS,{hostRoot:host});
  const fake=mockNativeProxy(wsSDK,o);
  const logs=[];const runtime={log:line=>logs.push(line),error:line=>logs.push(line),exit:code=>{if(code)throw new Error('native fixture exit');}};
  const sessions=[randomUUID(),randomUUID()];let completedNativeTurns=0;let close=null;let capRefused=false;
  try {
    const {agentCliCommand}=await import(pathToFileURL(host+'/dist/agent-via-gateway-Canl_hfN.mjs'));
    for(let ordinal=0;ordinal<3;ordinal++){
      if(ordinal===2)publishClock(1,offset+86400000);
      const sessionId=sessions[ordinal===2?1:0];
      const result=await agentCliCommand({local:true,json:true,agent:'main',message:'Public fixture conversation.',sessionId,
        sessionKey:'agent:main:public-fixture:'+sessionId,runId:'public-fixture-turn-'+ordinal,timeout:120,deliver:false},runtime);
      assert.ok(result);o.recordNativeObservation('public_cli_returned',{ordinal,scenario_generation:o.scenarioReadbacks.at(-1)?.generation,
        sdk_rows:o.calls.map(row=>({status:row.status,usage:row.usage,native_terminal_type:row.native_terminal_type}))});
      await o.nativeSettlement();completedNativeTurns++;
      o.recordNativeObservation('public_turn_readback_settled',{ordinal,completed_native_turns:completedNativeTurns});
    }
    assert.deepEqual(o.scenarioReadbacks.map(row=>row.generation),[0,0,1]);
    assert.ok(physical>=3);assert.equal(ledger.rows.length,physical);
    assert.ok(ledger.rows.every(row=>row.status==='completed'));assert.ok(o.calls.every(row=>row.native_model_context));
    assert.ok(toolRequested,'offered native file reader must execute through the ordinary loop');
    assert.equal(o.tools.size,16);assert.equal([...o.tools.values()].filter(row=>row.dispatched).length,16);assert.equal(o.toolCoverage,true);
    if(failedToolRecovery){
      const failed=[...o.tools.values()].filter(row=>row.native_terminal?.status==='failed');
      assert.equal(failed.length,1);assert.equal(failed[0].native_terminal.exit_code,127);
      assert.equal(ledger.stopReason,null,'native failure must permit ordinary follow-up and readback');
    }
    for(const [index,{body}] of completed.entries()){
      const text=JSON.stringify(body);const applicationDates=[...text.matchAll(/Current date(?: and time)?: (\d{4}-\d{2}-\d{2})/g)].map(m=>m[1]);
      const nativeDates=[...text.matchAll(/<current_date>(\d{4}-\d{2}-\d{2})<\/current_date>/g)].map(m=>m[1]);
      const binding=o.calls[index].calendar_binding;
      if(body.previous_response_id && !applicationDates.length){
        assert.ok(binding.previous_response_id_sha256,'incremental calendar must inherit the exact settled response');
        assert.equal(binding.changed_field,null);assert.equal(binding.native_date_changed,false);
        assert.equal(nativeDates.length,0);assert.ok(body.input.every(item=>item.type==='function_call_output'));
      }else{
        assert.ok(applicationDates.length,'serialized ordinary temporal context missing');
        assert.ok(nativeDates.every(date=>applicationDates.includes(date)),'second contradictory native model-visible calendar');
      }
    }
    // The same installed pre-execution observer must reject a 17th identity before a body callback.
    let deniedBodyCalls=0;assert.throws(()=>{o.nativeToolBefore({toolUseId:'public-cap-denied-17',toolName:'exec_command',rawPayload:{session_id:sessions[1]}});deniedBodyCalls++;},/original tool cap/);
    assert.equal(deniedBodyCalls,0);assert.equal(o.tools.size,16);capRefused=true;
    close=await o.closeNative(plugin);assert.equal(nativeProcessScope?close.equivalent_native_settled:close.native_closed,true);assert.equal(close.pending_sdk,0);assert.equal(close.pending_tools,0);
  } finally {
    close=await o.closeNative(plugin);hook.close();fake.deregister();delete globalThis.__openclawPublicFakeFetch;delete globalThis.__openclawPublicFakeWebSocket;
    if(process.env.OPENCLAW_PUBLIC_TECHNICAL_OUTPUT)writeFileSync(process.env.OPENCLAW_PUBLIC_TECHNICAL_OUTPUT,JSON.stringify({schema:'OpenClawPublicOrdinaryComposition/v1',failedToolRecoveryFixture:failedToolRecovery,failedNativeTerminals:[...o.tools.values()].filter(row=>row.native_terminal?.status==='failed').map(row=>row.native_terminal),completedNativeTurns,physicalFakeCalls:physical,syntheticCaps:ledger.limits,syntheticUsagePaidOrRealModelUsage:false,sdkRows:ledger.rows,scenarioReadbacks:o.scenarioReadbacks,modelBodyCaptures:o.calls.map(row=>row.native_model_context).filter(Boolean),nativeToolRows:o.tools.size,nativeDispatchedTools:[...o.tools.values()].filter(row=>row.dispatched).length,toolCoverage:o.toolCoverage,nativeCoverageFailure:o.nativeCoverageFailure,seventeenthSameObserverRefusedBeforeBody:capRefused,negativeCapScope:'same native pre-execution observer after the three ordinary completed turns; no extra provider request',close:{native_closed:close.native_closed,equivalent_native_settled:close.equivalent_native_settled,native_process_scope_receipts:close.native_process_scope_receipts,final_native_process_scope_receipt:close.final_native_process_scope_receipt,physical_transports_exited:close.physical_transports_exited,physical_transport_exit_receipts:close.physical_transport_exit_receipts,pending_sdk:close.pending_sdk,pending_tools:close.pending_tools,tool_coverage:close.tool_coverage,native_dispatched_tools:close.native_dispatched_tools,cancel_errors:close.cancel_errors,native_unregister_queues:close.native_unregister_queues,native_module_bindings:close.native_module_bindings}},null,2)+'\n',{mode:0o600});
  }
  });
});
