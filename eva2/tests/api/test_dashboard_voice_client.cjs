const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../src/eva/adapters/api/dashboard_voice_client.py'), 'utf8').split('r"""')[1].split('"""')[0];
const sandbox = {URL};
vm.runInNewContext(source, sandbox);
const Client = sandbox.EvaVoiceClient;
const defer = () => {let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});return {promise,resolve,reject};};

function setup() {
  const sent=[],states=[];let starts=0,stops=0;
  const decoding=defer();
  const context={decodeAudioData:()=>decoding.promise,close:async()=>{},destination:{},
    createBufferSource:()=>({connect(){},disconnect(){},start(){starts++;},stop(){stops++;}})};
  const client=new Client({env:{atob:value=>Buffer.from(value,'base64').toString('binary')},onState:s=>states.push(s)});
  client.running=true;client.context=context;client.generation=1;
  client.socket={readyState:1,bufferedAmount:0,send:x=>sent.push(x),close(){}};
  return {client,sent,states,decoding,counts:()=>({starts,stops})};
}
function audio(client) {
  client.event({type:'audio',generation:1,mime_type:'audio/mpeg',data:Buffer.from('synthetic').toString('base64')});
}
const frame=()=>({bytes:new ArrayBuffer(640),rms:0.1});

test('interruption while decoding cannot start late audio',async()=>{
  const {client,decoding,counts,sent}=setup();audio(client);
  const playing=client.play(1);client.interrupt();decoding.resolve({});await playing;
  assert.deepEqual(counts(),{starts:0,stops:0});
  assert.equal(JSON.parse(sent[0]).type,'interrupt');
  audio(client);assert.equal(client.audioBytes,0);
});

test('speaker stops locally before server response',async()=>{
  const {client,decoding,counts,sent}=setup();audio(client);
  const playing=client.play(1);decoding.resolve({});await playing;
  client.interrupt();assert.deepEqual(counts(),{starts:1,stops:1});
  assert.equal(JSON.parse(sent[0]).type,'interrupt');
});

test('speech during model work is buffered once and sent after completion',()=>{
  const {client,sent}=setup();client.serverBusy=true;
  for(let i=0;i<6;i++)client.frame(frame());
  assert.deepEqual(sent.map(x=>JSON.parse(x).type),['interrupt']);
  client.finishCapture();assert.ok(client.capture.ended);
  client.event({type:'turn_finished',generation:1});
  assert.equal(JSON.parse(sent.at(-1)).type,'start');
  client.event({type:'listening',generation:2});
  assert.equal(sent.filter(x=>x instanceof ArrayBuffer).length,4);
  for(let sequence=1;sequence<=6;sequence++)client.event({type:'audio_ack',generation:2,sequence});
  assert.equal(sent.filter(x=>x instanceof ArrayBuffer).length,6);
  assert.equal(JSON.parse(sent.at(-1)).type,'end');
  assert.equal(client.capture,null);
});

test('disconnect stops microphone and clears buffered playback',()=>{
  const {client,counts}=setup();let stopped=0;
  client.stream={getTracks:()=>[{stop(){stopped++;}}]};audio(client);
  client.close();assert.equal(stopped,1);assert.equal(client.audioBytes,0);
  assert.equal(client.running,false);assert.deepEqual(counts(),{starts:0,stops:0});
});

test('socket backpressure ends capture instead of unbounded buffering',()=>{
  const {client,sent}=setup();client.socket.bufferedAmount=300000;
  client.sendFrame(new ArrayBuffer(640));
  assert.equal(client.running,false);assert.equal(sent.length,0);
});

test('audio mime changes and oversized messages fail closed',()=>{
  const {client}=setup();audio(client);
  assert.throws(()=>client.event({type:'audio',generation:1,mime_type:'audio/wav',data:'AA=='}));
  assert.throws(()=>client.event({type:'audio',generation:1,mime_type:'audio/mpeg',data:'A'.repeat(90001)}));
});

test('new session cannot revive pending old decode',async()=>{
  const {client,decoding,counts}=setup();audio(client);const playing=client.play(1);
  client.close();client.running=true;client.generation=1;
  decoding.resolve({});await playing;assert.equal(counts().starts,0);
});

test('permission granted after closing releases the late microphone',async()=>{
  const permission=defer(),requested=defer();let tracksStopped=0;
  const env={isSecureContext:true,location:{href:'http://localhost/'},URL,Blob,
    setTimeout,clearTimeout,navigator:{mediaDevices:{getUserMedia:()=>{requested.resolve();return permission.promise;}}},
    AudioContext:class {async resume(){} async close(){}},
    WebSocket:class {constructor(){this.readyState=1;queueMicrotask(()=>this.onmessage({data:JSON.stringify({type:'ready',sample_rate:16000,channels:1,encoding:'pcm_s16le'})}));}close(){}}};
  const client=new Client({env});const starting=client.start();await requested.promise;
  client.close();permission.resolve({getTracks:()=>[{stop(){tracksStopped++;}}]});await starting;
  assert.equal(tracksStopped,1);assert.equal(client.stream,null);
});

for(const rate of [16000,44100,48000])test(`worklet emits 16kHz little endian PCM at ${rate}Hz`,()=>{
  const worklet=source.split('const WORKLET = `')[1].split('`;')[0];
  const frames=[];let Processor;
  vm.runInNewContext(worklet,{sampleRate:rate,AudioWorkletProcessor:class {constructor(){this.port={postMessage:x=>frames.push(x)};}},
    registerProcessor:(_name,cls)=>{Processor=cls;}});
  const processor=new Processor();
  processor.process([[new Float32Array(rate).fill(0.5)]]);
  assert.equal(frames.length,50);
  for(const frame of frames){assert.equal(frame.bytes.byteLength,640);assert.equal(new DataView(frame.bytes).getInt16(0,true),16384);assert.equal(frame.rms,0.5);}
});


test('early STT completion retires current capture without sending late frames',()=>{
  const {client,sent}=setup();
  for(let i=0;i<6;i++)client.frame(frame());
  client.event({type:'listening',generation:2});
  client.event({type:'turn_finished',generation:2});
  assert.equal(client.capture,null);
  const count=sent.length;client.frame({bytes:new ArrayBuffer(640),rms:0});
  assert.equal(sent.length,count);
});

test('old decode rejection cannot close a newer session',async()=>{
  const {client,decoding}=setup();audio(client);client.event({type:'audio_end',generation:1});
  client.close();client.running=true;
  decoding.reject(new Error('old decode failed'));await new Promise(setImmediate);
  assert.equal(client.running,true);
});

test('long queued utterance never exceeds four unacknowledged frames',()=>{
  const {client,sent}=setup();client.serverBusy=true;
  for(let i=0;i<400;i++)client.frame(frame());
  client.finishCapture();client.event({type:'turn_finished',generation:1});
  client.event({type:'listening',generation:2});
  let sequence=0;
  while(client.capture){
    assert.ok(sent.filter(x=>x instanceof ArrayBuffer).length-sequence<=4);
    sequence++;client.event({type:'audio_ack',generation:2,sequence});
  }
  assert.equal(sequence,400);assert.equal(JSON.parse(sent.at(-1)).type,'end');
});


test('real Chromium AudioWorklet and authenticated Uvicorn voice roundtrip', {
  skip: !process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE, timeout:45000,
}, async()=>{
  const os=require('node:os');
  const {spawn}=require('node:child_process');
  const {once}=require('node:events');
  const {chromium}=require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'eva2-voice-browser-'));
  const wav=path.join(directory,'synthetic.wav');
  const rate=16000, samples=rate*4;
  const bytes=Buffer.alloc(44+samples*2);
  bytes.write('RIFF');bytes.writeUInt32LE(bytes.length-8,4);bytes.write('WAVEfmt ',8);
  bytes.writeUInt32LE(16,16);bytes.writeUInt16LE(1,20);bytes.writeUInt16LE(1,22);
  bytes.writeUInt32LE(rate,24);bytes.writeUInt32LE(rate*2,28);bytes.writeUInt16LE(2,32);
  bytes.writeUInt16LE(16,34);bytes.write('data',36);bytes.writeUInt32LE(samples*2,40);
  for(let i=0;i<rate;i++)bytes.writeInt16LE(Math.round(Math.sin(i*2*Math.PI*440/rate)*8000),44+i*2);
  fs.writeFileSync(wav,bytes);
  const root=path.resolve(__dirname,'../..');
  const python=process.env.EVA_VOICE_TEST_PYTHON;
  assert.ok(python && path.isAbsolute(python));
  const fixture=`import runpy,socket,sys
from pathlib import Path
from fastapi import Request
from fastapi.responses import HTMLResponse
import uvicorn
from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_conversation import DASHBOARD_CONVERSATION_HTML
from eva.adapters.connectors.home_assistant_voice import SpeechAudioChunk
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen()
port=sock.getsockname()[1]
mod=runpy.run_path('tests/api/test_dashboard_voice.py')
mod['setup'].__globals__['ORIGIN']=f'http://127.0.0.1:{port}'
audio=Path(sys.argv[1]).read_bytes()
async def synthesize(self,text):
 for offset in range(0,len(audio),16384):
  yield SpeechAudioChunk(audio[offset:offset+16384],'audio/wav')
mod['Media'].synthesize=synthesize
client,runtime,media,_,_=mod['setup']()
app=client.app
@app.get('/')
def page(request:Request):
 DashboardAccess(mod['TOKEN']).require(request)
 return HTMLResponse(DASHBOARD_CONVERSATION_HTML)
print(port,flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False)).run(sockets=[sock])
`;
  let browser;
  const child=spawn(python,['-c',fixture,wav],{cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'src')},stdio:['ignore','pipe','pipe']});
  let errors='';child.stderr.on('data',b=>{errors=(errors+b).slice(-4000);});
  try {
    const origin=await new Promise((resolve,reject)=>{
      let data='';const timer=setTimeout(()=>reject(new Error('fixture startup '+errors)),10000);
      child.once('exit',()=>{clearTimeout(timer);reject(new Error('fixture exited '+errors));});
      child.stdout.on('data',b=>{data+=b;if(data.includes('\n')){clearTimeout(timer);resolve('http://127.0.0.1:'+Number(data.split('\n')[0]));}});
    });
    browser=await chromium.launch({headless:true,executablePath:process.env.EVA_DASHBOARD_CHROMIUM,
      args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream',`--use-file-for-fake-audio-capture=${wav}`]});
    const context=await browser.newContext({httpCredentials:{username:'eva-owner',password:'v'.repeat(40)},permissions:['microphone']});
    const page=await context.newPage();const pageErrors=[],received=[];
    page.on('pageerror',e=>pageErrors.push(e.message));
    page.on('websocket',socket=>socket.on('framereceived',event=>{
      try{received.push(JSON.parse(String(event.payload)).type);}catch(_){}
    }));
    await page.addInitScript(()=>{
      const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia=async(...args)=>{
        const stream=await original(...args);window.voiceTestTracks=stream.getTracks();return stream;
      };
    });
    await page.goto(origin);
    await page.getByRole('button',{name:'Praten met Eva',exact:true}).click();
    await page.waitForFunction(()=>document.getElementById('voice-state').textContent.includes('Eva spreekt'),{},{timeout:20000});
    assert.ok(received.includes('ready'),'Basic authentication must reach actual WebSocket handshake');
    assert.ok(received.includes('audio_ack'),'actual Worklet PCM reaches and is consumed by endpoint');
    assert.ok(received.includes('reply')&&received.includes('audio_end'));
    assert.equal(await page.locator('#chat').innerText(),'Klaar voor een privébericht.\nIk help je graag.\nVergeet deze beurt');
    await page.getByRole('button',{name:'Onderbreek Eva',exact:true}).click();
    await page.waitForFunction(()=>document.getElementById('voice-state').textContent.includes('Onderbroken'));
    assert.equal(await page.evaluate(()=>voiceTestTracks[0].readyState),'live');
    await page.getByRole('button',{name:'Microfoon uit',exact:true}).click();
    assert.equal(await page.evaluate(()=>voiceTestTracks[0].readyState),'ended');
    assert.deepEqual(pageErrors,[]);
    console.log('Synthetic browser proof: real Chromium capture/worklet/PCM, Basic WS handshake, Uvicorn admission, WAV decode/play scheduling, interrupt and track cleanup. No human microphone/speaker or provider/model claim.');
  } finally {
    if(browser)await browser.close();
    if(child.exitCode===null){child.kill('SIGTERM');await once(child,'exit');}
    fs.rmSync(directory,{recursive:true,force:true});
  }
});
