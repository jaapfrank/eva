import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {once} from 'node:events';
import {createCollector,inspectWav} from './server.mjs';
export function fixture(){const b=Buffer.alloc(44+32000);b.write('RIFF');b.writeUInt32LE(b.length-8,4);b.write('WAVEfmt ',8);b.writeUInt32LE(16,16);b.writeUInt16LE(1,20);b.writeUInt16LE(1,22);b.writeUInt32LE(16000,24);b.writeUInt32LE(32000,28);b.writeUInt16LE(2,32);b.writeUInt16LE(16,34);b.write('data',36);b.writeUInt32LE(32000,40);for(let n=0;n<16000;n++)b.writeInt16LE(Math.round(4000*Math.sin(n*.13)),44+n*2);return b;}
test('WAV rejects false rate, truncation and silence',()=>{const b=fixture();assert.equal(inspectWav(b).durationSeconds,1);const wrong=Buffer.from(b);wrong.writeUInt32LE(48000,24);assert.throws(()=>inspectWav(wrong));assert.throws(()=>inspectWav(b.subarray(0,-1)));b.fill(0,44);assert.throws(()=>inspectWav(b),/signaal/);});
test('local API stores canonical labels, preserves holdout and prevents overwrite/cross-origin writes',async()=>{
 const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'wake-collector-test-')),port=0,server=createCollector({port,dataDir,quiet:true});await once(server,'listening');const base='http://127.0.0.1:'+server.address().port;
 try{const setup=await (await fetch(base+'/api/protocol')).json();assert.equal(setup.protocol.filter(x=>x.label==='positive').length,10);const headers={Origin:base,'X-Capture-Token':setup.token};assert.equal((await fetch(base+'/api/session',{method:'POST',headers:{...headers,Origin:'https://example.org'}})).status,403);const session=await (await fetch(base+'/api/session',{method:'POST',headers})).json();assert.equal(session.purpose,'heldout_evaluation_only');assert.equal(session.clips.length,0);
 const wav=fixture();const opts={method:'POST',headers:{...headers,'X-Distance-M':'.5','X-Label-Confirmed':'spoken-as-shown'},body:wav};let response=await fetch(`${base}/api/session/${session.id}/clip/01`,opts);assert.equal(response.status,201);const saved=await response.json();assert.equal(saved.clips[0].label,'positive');assert.equal(saved.clips[0].text,'Hé Eva.');assert.equal(saved.clips[0].sampleRate,16000);assert.equal(saved.clips[0].sha256.length,64);assert.equal((await fetch(`${base}/api/session/${session.id}/clip/01`,opts)).status,409);assert.equal((await fetch(`${base}/api/session/${session.id}/clip/02`,{...opts,headers:{...opts.headers,'X-Label-Confirmed':'no'}})).status,400);assert.equal(fs.readdirSync(path.join(dataDir,session.id)).filter(x=>x.endsWith('.wav')).length,1);
 }finally{await new Promise(resolve=>server.close(resolve));}
});

