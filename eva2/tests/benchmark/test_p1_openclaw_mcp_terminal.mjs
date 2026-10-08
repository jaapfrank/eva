/** Public event-order regressions. No provider, private query, or native execution. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtempSync, chmodSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {OpenClawAccounting} from '../../benchmark/inspect_ai_v1/p1_openclaw_parent_accounting.mjs';

const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const normalize = value => value.trim().toLowerCase();
function fixture(options={}) {
  const ledger = {stopReason:null, stopHandlers:new Set(), check(){}, stop(reason){this.stopReason=reason;}};
  const observer = new OpenClawAccounting(ledger, 20, {now:()=>10, observeTools:true, ...options});
  observer.bindNativeHookToolNormalizer(normalize);
  const hook = {toolUseId:'public-mcp', toolName:'mcp__codex_apps__plugin_management__search_plugins', turnId:'public-turn',
    rawPayload:{session_id:'public-thread'}};
  const notification = {method:'item/completed', params:{threadId:'public-thread', turnId:'public-turn',
    item:{id:hook.toolUseId, type:'mcpToolCall', status:'completed', server:'codex_apps', tool:'plugin_management.search_plugins',
      arguments:{query:'PRIVATE_ARGUMENT'}, result:{content:'PRIVATE_RESULT'}, error:null}}};
  return {ledger, observer, hook, notification};
}

await test('Observed native MCP terminal before post settles the same admitted call once and preserves failed status', () => {
  for (const status of ['completed','failed']) {
    const {ledger,observer,hook,notification}=fixture();
    const row=observer.nativeToolBefore(hook);
    notification.params.item.status=status;
    if(status==='failed')notification.params.item.error={message:'PRIVATE_FAILURE'};
    observer.nativeNotification({},notification);
    assert.equal(ledger.stopReason,null);
    assert.equal(row.status,'settled');
    assert.equal(row.dispatched,true);
    assert.equal(row.native_terminal.status,status);
    assert.equal(row.native_terminal.arguments_sha256,digest(notification.params.item.arguments));
    observer.nativeToolAfter(hook);
    observer.nativeNotification({},notification);
    assert.equal(observer.tools.size,1);
    assert.equal(observer.toolCoverage,true);
    assert.equal(ledger.stopReason,null);
  }
});

await test('MCP terminal rejects changed admission, server, tool, thread, turn, status and missing arguments', () => {
  for(const changed of ['unreserved','cancelled','name','server','tool','thread','turn','status','arguments','normalizer']) {
    const {ledger,observer,hook,notification}=fixture();
    if(changed==='name')hook.toolName='mcp__another_server__plugin_management__search_plugins';
    const row=changed==='unreserved'?null:observer.nativeToolBefore(hook);
    if(changed==='cancelled')observer.cancelNativeTool(hook);
    const p=notification.params;
    if(changed==='server')p.item.server='another_server';
    if(changed==='tool')p.item.tool='another_tool';
    if(changed==='thread')p.threadId='another_thread';
    if(changed==='turn')p.turnId='another_turn';
    if(changed==='status')p.item.status='inProgress';
    if(changed==='arguments')delete p.item.arguments;
    if(changed==='normalizer')observer.nativeHookToolNormalizer=null;
    observer.nativeNotification({},notification);
    assert.equal(observer.toolCoverage,false,changed);
    assert.ok(ledger.stopReason,changed);
    assert.equal(row?.dispatched??false,false,changed);
  }
});

await test('MCP terminal rejects changed receipt even after the post hook has settled', () => {
  for(const changed of ['server','tool','thread','turn','status','arguments','result','error']) {
    const {ledger,observer,hook,notification}=fixture();
    observer.nativeToolBefore(hook);
    observer.nativeNotification({},notification);
    observer.nativeToolAfter(hook);
    const p=notification.params;
    if(changed==='thread')p.threadId='another_thread';
    else if(changed==='turn')p.turnId='another_turn';
    else if(changed==='arguments')p.item.arguments={query:'changed'};
    else if(changed==='result')p.item.result={content:'changed'};
    else if(changed==='error')p.item.error={message:'changed'};
    else p.item[changed]='changed';
    observer.nativeNotification({},notification);
    assert.equal(observer.toolCoverage,false,changed);
    assert.ok(ledger.stopReason,changed);
  }
});

await test('MCP observation retains hashes and terminal status without raw arguments or results', () => {
  const dir=mkdtempSync(tmpdir()+'/openclaw-mcp-terminal-');chmodSync(dir,0o700);
  try {
    const {ledger,observer,hook,notification}=fixture({privateModelContextDir:dir});
    observer.nativeToolBefore(hook);
    observer.nativeNotification({},notification);
    const records=observer.nativeObservationReceipts.map(r=>JSON.parse(readFileSync(r.path)));
    assert.equal(ledger.stopReason,null);
    assert.deepEqual(records.map(r=>r.phase),['pre_observer_entry','reserved','item_completed','native_terminal_settled']);
    assert.equal(records.at(-1).arguments_sha256,digest(notification.params.item.arguments));
    assert.equal(JSON.stringify(records).includes('PRIVATE_'),false);
  } finally {rmSync(dir,{recursive:true,force:true});}
});

await test('unknown native MCP rendering stays unsupported even when its post hook settles', () => {
  const {ledger,observer,hook,notification}=fixture();
  hook.toolName='mcp__public_server__public_lookup';
  notification.params.item.server='public_server';notification.params.item.tool='public_lookup';
  const row=observer.nativeToolBefore(hook);observer.nativeToolAfter(hook);
  observer.nativeNotification({},notification);
  assert.equal(row.status,'settled');assert.equal(observer.toolCoverage,false);
  assert.ok(ledger.stopReason);assert.equal(row.native_terminal,undefined);
});
