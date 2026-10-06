/** Public native transport fixtures only: no account, Codex process or model request. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { registerHooks } from 'node:module';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
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
  const fake=registerHooks({resolve(specifier,context,next){
    if(specifier==='openclaw/plugin-sdk/ssrf-runtime' && context.parentURL?.endsWith('/inference-proxy-b-aJMSxm.mjs')) {
      return {url:'data:text/javascript,'+encodeURIComponent('export const fetchWithSsrFGuard=(p)=>globalThis.__openclawPublicFakeFetch(p);export const isBlockedHostnameOrIp=()=>false;export const resolvePinnedHostnameWithPolicy=()=>({lookup:()=>{throw new Error("External DNS forbidden")}});'),shortCircuit:true};
    }
    if(wsSDK && specifier==='openclaw/plugin-sdk/websocket-runtime' && context.parentURL?.endsWith('/inference-proxy-b-aJMSxm.mjs')) {
      return {url:'data:text/javascript,'+encodeURIComponent(`export {WebSocketServer,rejectWebSocketUpgrade} from ${JSON.stringify(wsSDK)};export const WebSocket=globalThis.__openclawPublicFakeWebSocket;`),shortCircuit:true};
    }
    if(wsSDK && specifier==='openclaw/plugin-sdk/fetch-runtime' && context.parentURL?.endsWith('/inference-proxy-b-aJMSxm.mjs')) {
      return {url:'data:text/javascript,'+encodeURIComponent('export const createNodeProxyAgent=()=>null;'),shortCircuit:true};
    }
    return next(specifier,context);
  }});
  const hook=installOpenClawAccounting(o,plugin,NATIVE_PINS);let proxy;
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
