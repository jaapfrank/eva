import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {fileURLToPath} from 'node:url';
import {ChromeWorker} from './process.mjs';
import {ChromeGate} from './gate.mjs';

test('real upstream browser: snapshot/click; takeover interrupts a waiting click before it fires',async()=>{
 let effects=0;
 const server=createServer((req,res)=>{
  if(req.url==='/effect'){effects++;res.end('ok');return;}
  res.setHeader('Content-Type','text/html');res.end('<button id="ready" onclick="fetch(\'/effect\')">Ready</button><button id="later" disabled onclick="fetch(\'/effect\')">Later</button><script>setTimeout(()=>document.querySelector("#later").disabled=false,4000)</script>');
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const env={...process.env,EVA_CHROME_FIXTURE:`http://127.0.0.1:${server.address().port}`};
 const gate=new ChromeGate(()=>new ChromeWorker(process.execPath,[fileURLToPath(new URL('./browser-fixture.cjs',import.meta.url))],env));
 try {
  await gate.connect();await gate.call('chrome_snapshot');
  await gate.call('chrome_click',{target:'#ready',observation:gate.observation});assert.equal(effects,1);
  await gate.call('chrome_snapshot');
  const action=gate.call('chrome_click',{target:'#later',observation:gate.observation});const rejected=assert.rejects(action);
  await new Promise(r=>setTimeout(r,100));await gate.stop();await rejected;
  assert.equal(gate.mode,'owner');await new Promise(r=>setTimeout(r,4300));assert.equal(effects,1,'pending click must not fire after takeover');
  await gate.connect();assert.equal(gate.observation,null);await gate.call('chrome_snapshot');
 } finally {await gate.stop('disconnected');server.closeAllConnections();await new Promise(r=>server.close(r));}
});
