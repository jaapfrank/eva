import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer,request} from 'node:http';
import {fork,spawn} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {once} from 'node:events';
import {createInterface} from 'node:readline';
const here=fileURLToPath(new URL('.',import.meta.url));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(read,predicate){for(let i=0;i<100;i++){const value=await read();if(predicate(value))return value;await delay(50);}throw Error('condition not reached');}

test('isolated real browser: transport loss, address change and bridge crash revoke pending actions and grants', {timeout:90000},async(t)=>{
 const dir=await mkdtemp(join(tmpdir(),'eva-chrome-recovery-'));
 let effects=0,released=false,bridge,proxy,serial=0,completionAttempts=0;
 const resultFile=join(dir,'completed-browser-task.json');
 const resultLabel='Taakrapport task-browser-001: afgerond';
 let completedBytes;
 const page=createServer(async(req,res)=>{
  if(req.url==='/complete'){
   completionAttempts++;
   try{await writeFile(resultFile,JSON.stringify({taskId:'task-browser-001',status:'completed',result:'synthetic browser task result'}),{flag:'wx'});}catch(error){if(error.code!=='EEXIST')throw error;}
   return res.end(resultLabel);
  }
  let saved=false;try{await readFile(resultFile);saved=true;}catch(error){if(error.code!=='ENOENT')throw error;}
  if(req.url==='/effect'){effects++;return res.end('ok');}
  if(req.url==='/release')return res.end(released?'yes':'no');
  res.setHeader('content-type','text/html');res.end(`<p id="result">${saved?resultLabel:'Nog geen taakresultaat'}</p><button id="complete" onclick="fetch('/complete').then(r=>r.text()).then(text=>document.querySelector('#result').textContent=text)">Maak taakrapport</button><button id="ready" onclick="fetch('/effect')">Ready</button><button id="later" disabled onclick="fetch('/effect')">Later</button><script>setInterval(async()=>{if(await (await fetch('/release')).text()==='yes')document.querySelector('#later').disabled=false},50)</script>`);
 });
 await new Promise(r=>page.listen(0,'127.0.0.1',r));
 const tokenFile=join(dir,'token'),addressFile=join(dir,'gateway-address'),callerAddressFile=join(dir,'caller-address'),configFile=join(dir,'config.json');
 const packagePath=join(here,'package.json');
 await writeFile(tokenFile,'isolated-test-credential-0000000000000000000000');
 await writeFile(addressFile,'172.30.0.2');
 await writeFile(callerAddressFile,'172.30.0.2');
 await writeFile(configFile,JSON.stringify({tokenFile,addressFile,callerAddressFile,mcpSocket:join(dir,'mcp.sock'),ownerPort:0,workerCommand:process.execPath,workerArgs:[join(here,'browser-fixture.cjs'),packagePath,`http://127.0.0.1:${page.address().port}`],packagePath}));
 async function startBridge(){
  bridge=fork(join(here,'recovery-fixture.mjs'),[configFile],{stdio:['ignore','ignore','pipe','ipc'],env:process.env});
  bridge.stderr.on('data',()=>{});
  return new Promise((resolve,reject)=>{bridge.once('error',reject);bridge.once('exit',()=>reject(Error('fixture exited before ready')));bridge.once('message',resolve);});
 }
 async function control(action='status'){
  const id=++serial,child=bridge;
  return new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>{child.removeListener('message',listener);reject(Error('control timed out'));},10000);
   const listener=msg=>{if(msg.id===id){clearTimeout(timer);child.removeListener('message',listener);msg.error?reject(Error(msg.error)):resolve(msg);}};
   child.on('message',listener);child.send({id,action});
  });
 }
 let port;
 async function startProxy(){
  proxy=spawn('python3',[join(here,'recovery-proxy-fixture.py'),configFile],{stdio:['ignore','pipe','pipe']});proxy.stderr.on('data',()=>{});
  const lines=createInterface({input:proxy.stdout});port=JSON.parse((await once(lines,'line'))[0]).port;
 }
 function call(name,args={}){
  let req;
  const result=new Promise(resolve=>{
   req=request({host:'127.0.0.1',port,path:'/mcp',method:'POST',headers:{'content-type':'application/json'}},res=>{
    let body='';res.on('data',c=>body+=c);res.on('end',()=>{try{resolve({status:res.statusCode,...JSON.parse(body)});}catch{resolve({status:res.statusCode});}});res.on('error',()=>resolve({lost:true}));
   });
   req.on('error',()=>resolve({lost:true}));
   req.end(JSON.stringify({jsonrpc:'2.0',id:++serial,method:'src/tools/call',params:{name,arguments:args}}));
  });
  return {result,abort:()=>req.destroy()};
 }
 async function snapshot(expectedText){
  const value=await call('chrome_snapshot').result;
  assert.equal(value.result?.isError,undefined,JSON.stringify(value));
  const observation=value.result.content.map(c=>c.text||'').join('\n').match(/Fresh Chrome observation: ([\w-]+)/)?.[1];
  assert.ok(observation);
  if(expectedText)assert.ok(value.result.content.map(c=>c.text||'').join('\n').includes(expectedText),'saved result must be visible through a fresh browser snapshot');
  return observation;
 }
 const denied=async nonce=>assert.equal((await call('chrome_click',{target:'#ready',observation:nonce}).result).result?.isError,true);
 async function pending(){
  released=false;const grant=await control('grant');const observation=await snapshot();
  const action=call('chrome_click',{target:'#later',observation});
  await until(control,x=>x.status.busy);await delay(150);
  return {grant,observation,action};
 }
 async function noLateEffect(before){released=true;await delay(400);assert.equal(effects,before);if(completedBytes)assert.deepEqual(await readFile(resultFile),completedBytes);}
 async function checkRestored(old){
  await denied(old.observation);
  const grant=await control('grant');assert.notEqual(grant.status.epoch,old.grant.status.epoch);
  await denied(old.observation);
  const observation=await snapshot(completedBytes?resultLabel:undefined);assert.notEqual(observation,old.observation);
  if(completedBytes){assert.deepEqual(await readFile(resultFile),completedBytes);assert.equal(completionAttempts,1,'completed task must not be replayed');}
  const before=effects;const clicked=await call('chrome_click',{target:'#ready',observation}).result;
  assert.equal(clicked.result?.isError,undefined);await until(async()=>effects,x=>x===before+1);
 }
 try {
  const initial=await startBridge();assert.equal(initial.status.state,'disconnected');
  await startProxy();
  await denied('old-grant');
  // Complete one browser task, revoke a different pending action, then restore the
  // gateway at its new address. Completed remote bytes must survive without replay.
  const completedGrant=await control('grant');
  const completion=await call('chrome_click',{target:'#complete',observation:await snapshot()}).result;
  assert.equal(completion.result?.isError,undefined);
  await until(async()=>{try{return (await readFile(resultFile)).length>0;}catch{return false;}},Boolean);
  completedBytes=await readFile(resultFile);
  const savedObservation=await snapshot(resultLabel);
  const revoked={grant:completedGrant,observation:savedObservation,action:call('chrome_click',{target:'#later',observation:savedObservation})};
  await until(control,x=>x.status.busy);await delay(150);
  const revokeBefore=effects;await control('revoke');await revoked.action.result;
  await until(control,x=>x.status.state==='disconnected');await noLateEffect(revokeBefore);
  await writeFile(addressFile,'172.30.0.4');
  assert.equal((await call('chrome_snapshot').result).status,403);
  await writeFile(callerAddressFile,'172.30.0.4');
  await checkRestored(revoked);
  assert.deepEqual(await readFile(resultFile),completedBytes);
  await control('revoke');
  // Reset only isolated logical addresses for the existing independent loss cases.
  await writeFile(addressFile,'172.30.0.2');await writeFile(callerAddressFile,'172.30.0.2');
  // Browser worker loss: production worker process exit invalidates an active grant.
  let old=await pending(),before=effects;
  await control('worker-loss');await old.action.result;
  await until(control,x=>x.status.state==='disconnected');await noLateEffect(before);
  await checkRestored(old);
  // Drop only the HTTP caller while a real browser action is pending.
  let nonce;released=false;
  // Start a new page so the button is disabled again.
  await call('chrome_navigate',{url:`http://127.0.0.1:${page.address().port}`,observation:await snapshot()}).result;
  nonce=await snapshot();old={grant:await control(),observation:nonce,action:call('chrome_click',{target:'#later',observation:nonce})};
  await until(control,x=>x.status.busy);await delay(150);before=effects;
  old.action.abort();await until(control,x=>x.status.state==='disconnected');await noLateEffect(before);await checkRestored(old);
  // DNS changes while the socket remains open: old caller loses authorization.
  released=false;await call('chrome_navigate',{url:`http://127.0.0.1:${page.address().port}`,observation:await snapshot()}).result;
  nonce=await snapshot();old={grant:await control(),observation:nonce,action:call('chrome_click',{target:'#later',observation:nonce})};
  await until(control,x=>x.status.busy);await delay(150);before=effects;
  assert.equal((await call('chrome_snapshot').result).result?.isError,true);
  assert.equal((await control()).status.busy,true,'rejected concurrent caller must not cancel active action');
  await writeFile(addressFile,'172.30.0.4');await old.action.result;
  await until(control,x=>x.status.state==='disconnected');await noLateEffect(before);
  assert.equal((await call('chrome_snapshot').result).status,403);
  await writeFile(addressFile,'172.30.0.2');await checkRestored(old);
  // Hard kill only the isolated proxy: host observes Unix EOF and revokes.
  released=false;await call('chrome_navigate',{url:`http://127.0.0.1:${page.address().port}`,observation:await snapshot()}).result;
  nonce=await snapshot();old={grant:await control(),observation:nonce,action:call('chrome_click',{target:'#later',observation:nonce})};
  await until(control,x=>x.status.busy);await delay(150);before=effects;
  const proxyExited=once(proxy,'exit');proxy.kill('SIGKILL');await proxyExited;await old.action.result;
  await until(control,x=>x.status.state==='disconnected');await noLateEffect(before);
  await startProxy();await checkRestored(old);
  // Hard kill only the isolated bridge: EOF closes its worker; boot never grants.
  released=false;await call('chrome_navigate',{url:`http://127.0.0.1:${page.address().port}`,observation:await snapshot()}).result;
  nonce=await snapshot();old={grant:await control(),observation:nonce,action:call('chrome_click',{target:'#later',observation:nonce})};
  await until(control,x=>x.status.busy);await delay(150);before=effects;
  const workerPid=(await control()).workerPid;
  const exited=once(bridge,'exit');bridge.kill('SIGKILL');await exited;await old.action.result;
  await until(async()=>{try{process.kill(workerPid,0);return false;}catch{return true;}},Boolean);
  await noLateEffect(before);
  const restarted=await startBridge();assert.equal(restarted.status.state,'disconnected');assert.notEqual(restarted.status.epoch,old.grant.status.epoch);
  await checkRestored(old);
  assert.deepEqual(await readFile(resultFile),completedBytes);assert.equal(completionAttempts,1);
  t.diagnostic(JSON.stringify({chain:'completed-result -> explicit-revoke -> new-gateway-address -> explicit-grant -> fresh-read -> process-recovery',resultSha256:createHash('sha256').update(completedBytes).digest('hex'),completionAttempts,completedResultPreserved:true,newLogicalGatewayAddress:'172.30.0.4',lateClicks:0,scope:'isolated browser result; no core/helper/memory/voice restart claim'}));
 }finally{
  if(bridge?.exitCode===null && bridge?.signalCode===null){const done=once(bridge,'exit');bridge.kill('SIGTERM');await done;}
  if(proxy?.exitCode===null && proxy?.signalCode===null){const done=once(proxy,'exit');proxy.kill('SIGTERM');await done;}
  page.closeAllConnections();await new Promise(r=>page.close(r));await rm(dir,{recursive:true,force:true});
 }
});
