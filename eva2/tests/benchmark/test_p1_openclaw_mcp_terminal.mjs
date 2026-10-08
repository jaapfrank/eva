/** Public native-carrier regressions. No provider or sealed evaluation input. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtempSync, chmodSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {OpenClawAccounting} from '../../benchmark/inspect_ai_v1/p1_openclaw_parent_accounting.mjs';
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
function fixture(options={}) {
  const ledger = {stopReason:null, stopHandlers:new Set(), check(){}, stop(reason){this.stopReason=reason;}};
  const observer = new OpenClawAccounting(ledger, 20, {now:()=>10, observeTools:true, ...options});
  const hook = {toolUseId:'public-mcp', toolName:'native_arbitrary_hook_name', turnId:'public-turn',
    rawPayload:{session_id:'public-thread',tool_input:{query:'PRIVATE_ARGUMENT'}}};
  const started = {method:'item/started', params:{threadId:'public-thread', turnId:'public-turn',
    item:{id:hook.toolUseId, type:'mcpToolCall', status:'inProgress', server:'public.server', tool:'public.tool',
      arguments:{query:'PRIVATE_ARGUMENT'}}}};
  const completed=structuredClone(started);completed.method='item/completed';
  Object.assign(completed.params.item,{status:'completed',result:{content:'PRIVATE_RESULT'},error:null});
  return {ledger,observer,hook,started,completed};
}
function admit(f,order='pre-first') {
  if(order==='start-first')f.observer.nativeNotification({},f.started);
  const row=f.observer.nativeToolBefore(f.hook);
  if(order==='pre-first')f.observer.nativeNotification({},f.started);
  return row;
}
await test('generic MCP carrier settles one counted admission in both orders and preserves failed status',()=>{
  for(const order of ['pre-first','start-first'])for(const status of ['completed','failed']) {
    const f=fixture();const row=admit(f,order);f.completed.params.item.status=status;
    if(status==='failed')f.completed.params.item.error={message:'PRIVATE_FAILURE'};
    f.observer.nativeNotification({},f.completed);
    assert.equal(f.ledger.stopReason,null);assert.equal(row.status,'settled');assert.equal(row.dispatched,true);
    assert.equal(row.native_terminal.status,status);assert.equal(row.native_terminal.arguments_sha256,digest(f.completed.params.item.arguments));
    f.observer.nativeToolAfter(f.hook);f.observer.nativeNotification({},f.completed);
    assert.equal(f.observer.tools.size,1);assert.equal(f.observer.toolCoverage,true);
  }
});
await test('raw JSON scalar, null, array and object types match without wrapper conversion or key-order dependence',()=>{
  for(const value of [null,'public',4,true,[1,{b:2,a:3}],{b:2,a:3}]) {
    const f=fixture();f.hook.rawPayload.tool_input=structuredClone(value);
    f.started.params.item.arguments=structuredClone(value);f.completed.params.item.arguments=structuredClone(value);
    if(value&& !Array.isArray(value)&&typeof value==='object')f.completed.params.item.arguments={a:3,b:2};
    const row=admit(f,'start-first');f.observer.nativeNotification({},f.completed);
    assert.equal(f.ledger.stopReason,null);assert.equal(row.status,'settled');
    const bad=fixture();bad.hook.rawPayload.tool_input=structuredClone(value);
    bad.started.params.item.arguments={value:structuredClone(value)};
    bad.observer.nativeNotification({},bad.started);
    assert.throws(()=>bad.observer.nativeToolBefore(bad.hook),/started\/pre identity differs/);
    assert.equal(bad.observer.tools.size,1);assert.equal(bad.observer.toolCoverage,false);
  }
});
await test('MCP completion rejects missing started/pre and changed ID, server, tool, arguments, thread, turn or status',()=>{
  for(const changed of ['no-start','no-pre','cancelled','id','server','tool','arguments','missing-arguments','thread','turn','status']) {
    const f=fixture();let row;
    if(changed!=='no-pre')row=f.observer.nativeToolBefore(f.hook);
    if(changed!=='no-start')f.observer.nativeNotification({},f.started);
    if(changed==='cancelled')f.observer.cancelNativeTool(f.hook);
    const p=f.completed.params;
    if(['id','server','tool'].includes(changed))p.item[changed]='changed';
    if(changed==='arguments')p.item.arguments={query:'changed'};
    if(changed==='missing-arguments')delete p.item.arguments;
    if(changed==='thread')p.threadId='changed';if(changed==='turn')p.turnId='changed';
    if(changed==='status')p.item.status='inProgress';
    f.observer.nativeNotification({},f.completed);
    assert.equal(f.observer.toolCoverage,false,changed);assert.ok(f.ledger.stopReason,changed);
    assert.equal(row?.dispatched??false,false,changed);
  }
});
await test('MCP started/pre rejects changed carrier in either event order before any body',()=>{
  for(const order of ['pre-first','start-first'])for(const changed of ['thread','turn','arguments','missing-input']) {
    const f=fixture();if(changed==='thread')f.hook.rawPayload.session_id='changed';
    if(changed==='turn')f.hook.turnId='changed';if(changed==='arguments')f.hook.rawPayload.tool_input={query:'changed'};
    if(changed==='missing-input')delete f.hook.rawPayload.tool_input;
    if(order==='start-first') {
      f.observer.nativeNotification({},f.started);
      assert.throws(()=>f.observer.nativeToolBefore(f.hook),/started\/pre identity differs/);
    } else {f.observer.nativeToolBefore(f.hook);f.observer.nativeNotification({},f.started);}
    assert.equal(f.observer.toolCoverage,false);assert.ok(f.ledger.stopReason);
    assert.equal([...f.observer.tools.values()][0].dispatched,false);
  }
  for(const changed of ['server','tool','arguments','thread','turn','missing-arguments']) {
    const f=fixture();admit(f);const changedStart=structuredClone(f.started);
    if(['server','tool'].includes(changed))changedStart.params.item[changed]='changed';
    if(changed==='arguments')changedStart.params.item.arguments=[];
    if(changed==='missing-arguments')delete changedStart.params.item.arguments;
    if(changed==='thread')changedStart.params.threadId='changed';if(changed==='turn')changedStart.params.turnId='changed';
    f.observer.nativeNotification({},changedStart);assert.equal(f.observer.toolCoverage,false);assert.ok(f.ledger.stopReason);
  }
});
await test('MCP terminal cannot bypass correlation after PostToolUse and rejects changed terminal receipts',()=>{
  for(const changed of ['no-start','server','tool','thread','turn','status','arguments','result','error']) {
    const f=fixture();f.observer.nativeToolBefore(f.hook);
    if(changed!=='no-start')f.observer.nativeNotification({},f.started);
    f.observer.nativeToolAfter(f.hook);
    if(changed!=='no-start')f.observer.nativeNotification({},f.completed);
    const p=f.completed.params;
    if(changed==='thread')p.threadId='changed';else if(changed==='turn')p.turnId='changed';
    else if(changed==='arguments')p.item.arguments={query:'changed'};
    else if(changed==='result')p.item.result={content:'changed'};
    else if(changed==='error')p.item.error={message:'changed'};
    else if(changed!=='no-start')p.item[changed]='changed';
    f.observer.nativeNotification({},f.completed);assert.equal(f.observer.toolCoverage,false,changed);assert.ok(f.ledger.stopReason,changed);
  }
});
await test('MCP observation retains typed hashes and terminal status without raw arguments/results',()=>{
  const dir=mkdtempSync(tmpdir()+'/openclaw-mcp-terminal-');chmodSync(dir,0o700);
  try {
    const f=fixture({privateModelContextDir:dir});admit(f);f.observer.nativeNotification({},f.completed);
    const records=f.observer.nativeObservationReceipts.map(r=>JSON.parse(readFileSync(r.path)));
    assert.equal(f.ledger.stopReason,null);
    assert.deepEqual(records.map(r=>r.phase),['pre_observer_entry','reserved','native_mcp_started','item_completed','native_terminal_settled']);
    assert.equal(records.at(-1).arguments_sha256,digest(f.completed.params.item.arguments));
    assert.equal(JSON.stringify(records).includes('PRIVATE_'),false);
  }finally{rmSync(dir,{recursive:true,force:true});}
});
