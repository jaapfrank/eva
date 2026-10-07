/** Public fixtures. Ordinary native composition uses own auth with mocked upstream and no real inference. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { registerHooks } from 'node:module';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, readFileSync, writeFileSync, chmodSync, renameSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { dirname } from 'node:path';
import { OpenClawProcessLedger, OpenClawAccounting, NATIVE_PINS, installOpenClawAccounting } from '../../benchmark/inspect_ai_v1/p1_openclaw_parent_accounting.mjs';

class FakeLedger {
  constructor() { this.rows = []; this.stopHandlers = new Set(); this.stopReason = null; }
  check() { if (this.stopReason) throw new Error('stopped'); }
  stop(reason) { this.stopReason = reason; for (const f of this.stopHandlers) f(); }
  async begin(role, model, effort) { this.check(); const id = this.rows.length; this.rows.push({id,role,model,effort,status:'pending'}); return id; }
  async settle(id, usage) { this.rows[id].usage = usage; this.rows[id].status = usage ? 'completed' : 'unknown'; if (!usage) this.stop('unknown'); }
}
const usage = {input_tokens:17,output_tokens:3,total_tokens:20};
const event = {type:'response.completed',response:{id:'public-response',usage}};
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
await test('public ordinary actor composition binds native calendar, model bodies, tools and one-shot readbacks',
  {skip:process.env.OPENCLAW_PUBLIC_COMPOSITION !== '1'}, async()=>{
  const plugin=process.env.OPENCLAW_PINNED_PLUGIN;const host=process.env.OPENCLAW_PINNED_HOST;
  const workspace=process.env.OPENCLAW_PUBLIC_WORKSPACE;const clockFile=process.env.OPENCLAW_PUBLIC_CLOCK;
  const contexts=process.env.OPENCLAW_PUBLIC_CONTEXTS;const operatorState=process.env.OPENCLAW_OPERATOR_STATE_DIR;
  assert.ok(plugin && host && workspace && clockFile && contexts && operatorState && process.env.OPENCLAW_CONFIG_PATH);
  const {a:withPluginInstallRoots,i:resolvePluginInstallRoots}=await import(pathToFileURL(host+'/dist/install-root-context-DEOzYnOP.mjs'));
  assert.equal(process.env.HOME,dirname(operatorState),'native HOME must belong to the existing UID1000 operator');
  return await withPluginInstallRoots(resolvePluginInstallRoots({...process.env,OPENCLAW_STATE_DIR:operatorState}),async()=>{
  const ledger=new FakeLedger();const deadline=Number(process.hrtime.bigint())/1e9+120;
  const o=new OpenClawAccounting(ledger,deadline,{clockFile,privateModelContextDir:contexts,observeTools:true});
  const publishClock=(generation,offsetMilliseconds)=>{
    const path=clockFile+'.tmp';writeFileSync(path,JSON.stringify({contract:'EvaScenarioCalendar/v1',generation,offsetMilliseconds}),{mode:0o600});renameSync(path,clockFile);
  };
  const target=Date.parse('2020-01-02T12:00:00.000Z');const offset=target-Date.now();publishClock(0,offset);
  writeFileSync(workspace+'/MEMORY.md','Public persistent memory marker: blue notebook.\n',{mode:0o600});
  writeFileSync(workspace+'/USER.md','Public fixture owner.\n',{mode:0o600});
  const completed=[];let physical=0;let toolRequested=false;let counter=0;
  const fakeTerminal=body=>{
    const responseId='public-response-'+(++counter);const tool=body.tools?.find(t=>t.type==='function' && t.name==='exec_command');
    const output=!toolRequested && tool ? (toolRequested=true,Array.from({length:16},(_,i)=>({type:'function_call',id:'public-function-'+counter+'-'+i,call_id:'public-call-'+counter+'-'+i,name:tool.name,arguments:JSON.stringify({cmd:'cat MEMORY.md',workdir:workspace,login:false,max_output_tokens:50,yield_time_ms:1000})})))
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
      assert.ok(result);await o.nativeSettlement();completedNativeTurns++;
    }
    assert.deepEqual(o.scenarioReadbacks.map(row=>row.generation),[0,0,1]);
    assert.ok(physical>=3);assert.equal(ledger.rows.length,physical);
    assert.ok(ledger.rows.every(row=>row.status==='completed'));assert.ok(o.calls.every(row=>row.native_model_context));
    assert.ok(toolRequested,'offered native file reader must execute through the ordinary loop');
    assert.equal(o.tools.size,16);assert.equal([...o.tools.values()].filter(row=>row.dispatched).length,16);assert.equal(o.toolCoverage,true);
    for(const [index,{body}] of completed.entries()){
      const text=JSON.stringify(body);const applicationDates=[...text.matchAll(/Current date(?: and time)?: (\d{4}-\d{2}-\d{2})/g)].map(m=>m[1]);
      assert.ok(applicationDates.length,'serialized ordinary temporal context missing');
      const nativeDates=[...text.matchAll(/<current_date>(\d{4}-\d{2}-\d{2})<\/current_date>/g)].map(m=>m[1]);
      assert.ok(nativeDates.every(date=>applicationDates.includes(date)),'second contradictory native model-visible calendar');
    }
    // The same installed pre-execution observer must reject a 17th identity before a body callback.
    let deniedBodyCalls=0;assert.throws(()=>{o.nativeToolBefore({toolUseId:'public-cap-denied-17',toolName:'exec_command',rawPayload:{session_id:sessions[1]}});deniedBodyCalls++;},/original tool cap/);
    assert.equal(deniedBodyCalls,0);assert.equal(o.tools.size,16);capRefused=true;
    close=await o.closeNative(plugin);assert.equal(close.native_closed,true);assert.equal(close.pending_sdk,0);assert.equal(close.pending_tools,0);
  } finally {
    close=await o.closeNative(plugin);hook.close();fake.deregister();delete globalThis.__openclawPublicFakeFetch;delete globalThis.__openclawPublicFakeWebSocket;
    if(process.env.OPENCLAW_PUBLIC_TECHNICAL_OUTPUT)writeFileSync(process.env.OPENCLAW_PUBLIC_TECHNICAL_OUTPUT,JSON.stringify({schema:'OpenClawPublicOrdinaryComposition/v1',completedNativeTurns,physicalFakeCalls:physical,sdkRows:ledger.rows,scenarioReadbacks:o.scenarioReadbacks,modelBodyCaptures:o.calls.map(row=>row.native_model_context).filter(Boolean),nativeToolRows:o.tools.size,nativeDispatchedTools:[...o.tools.values()].filter(row=>row.dispatched).length,toolCoverage:o.toolCoverage,nativeCoverageFailure:o.nativeCoverageFailure,seventeenthSameObserverRefusedBeforeBody:capRefused,negativeCapScope:'same native pre-execution observer after the three ordinary completed turns; no extra provider request',close:{native_closed:close.native_closed,pending_sdk:close.pending_sdk,pending_tools:close.pending_tools,tool_coverage:close.tool_coverage,native_dispatched_tools:close.native_dispatched_tools,cancel_errors:close.cancel_errors,native_unregister_queues:close.native_unregister_queues,native_module_bindings:close.native_module_bindings}},null,2)+'\n',{mode:0o600});
  }
  });
});
