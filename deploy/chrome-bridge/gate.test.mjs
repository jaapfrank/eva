import {test} from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {ChromeGate} from './gate.mjs';
class Worker extends EventEmitter {
  constructor(){super();this.ready=Promise.resolve(['chrome_snapshot','chrome_click'].map(name=>({name,description:name,inputSchema:{properties:{}}})));this.calls=[];}
  async request(p){this.calls.push(p);if(p.method==='connect')return {};if(this.wait)return new Promise((resolve,reject)=>{this.reject=reject;this.resolve=resolve;});return {content:[{type:'text',text:'page'}]};}
  async stop(){this.stopped=true;this.reject?.(Error('terminated'));}
}
test('no implicit connect; every write needs a fresh one-use observation',async()=>{
 const worker=new Worker(),gate=new ChromeGate(()=>worker);
 await assert.rejects(gate.call('chrome_snapshot'),/no browser control/);
 await gate.connect();await assert.rejects(gate.call('chrome_click'),/Fresh/);
 await gate.call('chrome_snapshot');const observation=gate.observation;
 await gate.call('chrome_click',{observation});await assert.rejects(gate.call('chrome_click',{observation}),/Fresh/);
});
test('takeover terminates running action and rejects queued/stale work after resume',async()=>{
 const workers=[];const gate=new ChromeGate(()=>{const w=new Worker();workers.push(w);return w;});
 await gate.connect();await gate.call('chrome_snapshot');const observation=gate.observation;
 workers[0].wait=true;const call=gate.call('chrome_click',{observation});const rejected=assert.rejects(call,/terminated/);
 await assert.rejects(gate.call('chrome_snapshot'),/running/);
 await gate.stop();await rejected;assert.equal(gate.mode,'owner');assert.equal(workers[0].stopped,true);
 await assert.rejects(gate.call('chrome_snapshot'),/no browser control/);
 await gate.connect();await assert.rejects(gate.call('chrome_click',{observation}),/Fresh/);
});
test('disconnect blocks immediately and never reconnects automatically',async()=>{
 const worker=new Worker(),gate=new ChromeGate(()=>worker);await gate.connect();worker.emit('disconnected');
 await assert.rejects(gate.call('chrome_snapshot'),/no browser control/);
 await new Promise(r=>setImmediate(r));assert.equal(gate.mode,'disconnected');
});
test('unconfirmed process termination never claims owner control',async()=>{
 const worker=new Worker(),gate=new ChromeGate(()=>worker);await gate.connect();worker.stop=async()=>{throw Error('failed');};await gate.stop();assert.equal(gate.mode,'recovery_required');
 await assert.rejects(gate.call('chrome_snapshot'),/no browser control/);
 await assert.rejects(gate.connect(),/unconfirmed/);
});
