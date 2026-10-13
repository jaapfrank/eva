// Test-only cross-process barrier for the durable PostgreSQL recovery test.
// Uses the existing real browser, bridge and proxy fixtures; no owner browser.
import assert from 'node:assert/strict';
import {createServer, request} from 'node:http';
import {fork, spawn} from 'node:child_process';
import {readFile, writeFile, lstat, realpath, rename} from 'node:fs/promises';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {once} from 'node:events';
import {createInterface} from 'node:readline';

const here = fileURLToPath(new URL('.', import.meta.url));
const directory = resolve(process.argv[2] || '');
assert.ok(process.argv[2], 'explicit isolated proof directory required');
assert.ok((await lstat(directory)).isDirectory());
assert.equal(await realpath(directory), directory, 'proof directory must not contain symlinks');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let bridge, proxy, page, serial = 0, effects = 0, released = false, port;
const path = name => join(directory, name);
async function read(name) {
  try { return await readFile(path(name), 'utf8'); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}
async function publish(name, value) { await writeFile(path(name+'.tmp'), JSON.stringify(value), {flag:'wx', mode:0o644}); await rename(path(name+'.tmp'),path(name)); }
async function waitFor(name) {
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    const failure = await read('core-failed.json');
    assert.equal(failure, null, failure || 'core failed');
    const value = await read(name);
    if (value !== null) return name.endsWith('.json') ? JSON.parse(value) : value;
    await delay(50);
  }
  throw Error('core barrier timed out: ' + name);
}
async function until(readValue, predicate) {
  for (let i=0; i<100; i++) { const value=await readValue(); if (predicate(value)) return value; await delay(50); }
  throw Error('browser state barrier timed out');
}
function control(action='status') {
  const id=++serial, child=bridge;
  return new Promise((resolve,reject) => {
    const listener=message => {
      if(message.id===id) { clearTimeout(timer); child.removeListener('message',listener);
        message.error ? reject(Error(message.error)) : resolve(message); }
    };
    const timer=setTimeout(() => { child.removeListener('message',listener); reject(Error('bridge control timeout')); },10000);
    child.on('message',listener); child.send({id,action});
  });
}
function call(name,args={}) {
  return new Promise(resolve => {
    const req=request({host:'127.0.0.1',port,path:'/mcp',method:'POST',headers:{'content-type':'application/json'}}, res => {
      let body=''; res.on('data',data=>body+=data); res.on('end',()=>{
        try {resolve({status:res.statusCode,...JSON.parse(body)});} catch {resolve({status:res.statusCode});}
      }); res.on('error',()=>resolve({lost:true}));
    });
    req.setTimeout(20000,()=>req.destroy()); req.on('error',()=>resolve({lost:true}));
    req.end(JSON.stringify({jsonrpc:'2.0',id:++serial,method:'tools/call',params:{name,arguments:args}}));
  });
}
async function snapshot() {
  const response=await call('chrome_snapshot');
  assert.equal(response.result?.isError,undefined,JSON.stringify(response));
  const text=response.result.content.map(item=>item.text||'').join('\n');
  const nonce=text.match(/Fresh Chrome observation: ([\w-]+)/)?.[1];
  assert.ok(nonce); return {text,nonce};
}
async function bounded(promise,label) {
  let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label+' timeout')),10000);})]);}finally{clearTimeout(timer);}
}
async function stop(child) {
  if(child && child.exitCode===null && child.signalCode===null) {
    const done=once(child,'exit'); child.kill('SIGTERM');
    const timer=setTimeout(()=>child.kill('SIGKILL'),3000);
    try {await done;} finally {clearTimeout(timer);}
  }
}
try {
  assert.equal(await read('browser-ready.json'),null,'proof directory must be fresh');
  page=createServer(async(req,res)=>{
    if(req.url==='/effect') {effects++; return res.end('unexpected effect');}
    if(req.url==='/release') return res.end(released?'yes':'no');
    if(req.url==='/result') {res.setHeader('content-type','application/json');return res.end(await read('expired.json') || '{}');}
    res.setHeader('content-type','text/html');
    res.end(`<p id="result">Waiting for durable recovery</p><button id="later" disabled onclick="fetch('/effect')">Later</button><script>setInterval(async()=>{if(await(await fetch('/release')).text()==='yes')document.querySelector('#later').disabled=false;const r=await(await fetch('/result')).json();if(r.result_sha256)document.querySelector('#result').textContent='Durable result '+r.result_sha256+'; memory '+r.memory_sha256},50)</script>`);
  });
  await new Promise(resolve=>page.listen(0,'127.0.0.1',resolve));
  const config={tokenFile:path('bridge-token'),addressFile:path('gateway-address'),callerAddressFile:path('caller-address'),
    mcpSocket:path('mcp.sock'),ownerPort:0,workerCommand:process.execPath,
    workerArgs:[join(here,'browser-fixture.cjs'),join(here,'package.json'),`http://127.0.0.1:${page.address().port}`],packagePath:join(here,'package.json')};
  await writeFile(config.tokenFile,'isolated-combined-proof-credential-0000000000000000',{mode:0o600,flag:'wx'});
  await writeFile(config.addressFile,'172.30.0.2'); await writeFile(config.callerAddressFile,'172.30.0.2');
  await writeFile(path('bridge-config.json'),JSON.stringify(config),{mode:0o600});
  bridge=fork(join(here,'recovery-fixture.mjs'),[path('bridge-config.json')],{stdio:['ignore','ignore','pipe','ipc'],env:process.env});
  bridge.stderr.on('data',()=>{});
  const initial=await bounded(Promise.race([once(bridge,'message').then(([value])=>value),once(bridge,'exit').then(()=>{throw Error('bridge exited before ready');})]),'bridge startup');
  assert.equal(initial.status.state,'disconnected');
  proxy=spawn('python3',[join(here,'recovery-proxy-fixture.py'),path('bridge-config.json')],{stdio:['ignore','pipe','pipe']});
  proxy.stderr.on('data',()=>{});
  const lines=createInterface({input:proxy.stdout});
  port=JSON.parse((await bounded(Promise.race([once(lines,'line'),once(proxy,'exit').then(()=>{throw Error('proxy exited before ready');})]),'proxy startup'))[0]).port;
  const grant=await control('grant');
  const before=await snapshot();
  await publish('browser-ready.json',{bridgePid:bridge.pid,proxyPid:proxy.pid,epoch:grant.status.epoch});
  // Start the real disabled click only after the core's durable claim exists;
  // otherwise fixture migration time could consume Playwright's action timeout.
  await waitFor('claim-ready');
  const pending=call('chrome_click',{target:'#later',observation:before.nonce});
  await until(control,value=>value.status.busy);
  await publish('browser-pending.json',{nonce:before.nonce,epoch:grant.status.epoch,busy:true});
  const crashed=await waitFor('core-crashed.json');
  assert.equal(crashed.signal,'SIGKILL');
  assert.equal((await control()).status.busy,true,'browser action must overlap actual core process death');
  await writeFile(config.addressFile,'172.30.0.4');
  await pending;
  await until(control,value=>value.status.state==='disconnected');
  assert.equal((await call('chrome_snapshot')).status,403,'old logical caller loses authorization');
  await writeFile(config.callerAddressFile,'172.30.0.4');
  assert.equal((await call('chrome_click',{target:'#later',observation:before.nonce})).result?.isError,true);
  released=true; await delay(400); assert.equal(effects,0,'revoked pending action must never click later');
  const freshGrant=await control('grant');
  assert.notEqual(freshGrant.status.epoch,grant.status.epoch);
  assert.equal((await call('chrome_click',{target:'#later',observation:before.nonce})).result?.isError,true);
  const fresh=await snapshot(); assert.notEqual(fresh.nonce,before.nonce);
  await publish('browser-authority-restored.json',{epoch:freshGrant.status.epoch,nonce:fresh.nonce,oldCallerStatus:403,lateClicks:effects});
  const recovered=await waitFor('expired.json');
  // Read the recovered durable receipt through the real page. Expected hashes
  // are loaded from that receipt, never reconstructed from synthetic constants.
  const observed=await until(snapshot,value=>value.text.includes(recovered.result_sha256) && value.text.includes(recovered.memory_sha256));
  assert.notEqual(observed.nonce,before.nonce); assert.equal(effects,0);
  await publish('browser-complete.json',{result_sha256:recovered.result_sha256,memory_sha256:recovered.memory_sha256,
    claimantPid:crashed.claimant_pid,recoveryPid:recovered.recovery_pid,lateClicks:effects,
    oldCallerStatus:403,oldNonceRejected:true,freshGrant:true,durableReceiptReadThroughBrowser:true,
    scope:'real isolated Chromium and production bridge; logical gateway address, application services, no physical microphone or full HTTP runtime'});
} catch(error) {
  await publish('browser-failed.json',{error:String(error),stack:error.stack}).catch(()=>{});
  console.error(error); process.exitCode=1;
} finally {
  await stop(bridge); await stop(proxy);
  if(page) {page.closeAllConnections();await new Promise(resolve=>page.close(resolve));}
}
