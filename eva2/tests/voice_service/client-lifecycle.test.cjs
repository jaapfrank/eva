// Deterministic browser-boundary tests. No physical microphone or screen-lock claim.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../..');
const html=fs.readFileSync(path.join(root,'src/eva/voice_service/client.html'),'utf8');
const desktop=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const panelSource=execFileSync('python3',['-c',`import importlib.util,pathlib
p=pathlib.Path(${JSON.stringify(root)})
s=importlib.util.spec_from_file_location('builder',p/'tools/build_ha_phone_panel.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
print(m.panel_script((p/'src/eva/voice_service/client.html').read_text()))`],{encoding:'utf8'});
function target(){const listeners=new Map();return {addEventListener(k,f){if(!listeners.has(k))listeners.set(k,new Set());listeners.get(k).add(f);},removeEventListener(k,f){listeners.get(k)?.delete(f);},emit(k){for(const f of listeners.get(k)||[])f();}};}
function deferred(){let resolve;return {promise:new Promise(r=>resolve=r),release(v){resolve(v);}};}
async function flush(){for(let i=0;i<12;i++)await Promise.resolve();}
function fixture(mode,phase){
 const elements=new Map(),rooms=[],closed=[],requests=[],tracks=[],gate=deferred();let count=0,failedClose=false,sdkLoads=0;
 const element=()=>({value:'',hidden:false,disabled:false,textContent:'',children:[],append(n){this.children.push(n);},replaceChildren(){this.children=[];}});
 const dom={getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);}};
 const document=Object.assign(target(),dom,{hidden:false,head:{append(s){sdkLoads++;if(phase==='sdk-fail'&&sdkLoads===1)s.onerror(Error('asset unavailable'));else if(phase==='sdk')gate.promise.then(()=>s.onload());else s.onload();}},createElement:element});
 const window=target();
 const pause=async name=>{if(phase===name)await gate.promise;};
 async function request(route,token,cap){
  requests.push(route);
  if(route==='session/ready')return {status:200,body:{ready:true}};
  if(route.startsWith('session/end')){if(failedClose)throw Error('offline');closed.push(cap||route.split('/').at(-1));return {status:200,body:{closed:true}};}
  if(route==='session'){const n=++count;await pause('create');return {status:200,body:{room:'room-'+n,capability:'cap-'+n,url:'https://ha/api/rtc/cap-'+n,token:'jwt-'+n}};}
  throw Error(route);
 }
 class Room{
  constructor(){rooms.push(this);this.events={};this.disconnects=0;this.publishes=0;this.localParticipant={publishTrack:async()=>{this.publishes++;await pause('publish');},sendText:async()=>{}};}
  on(k,f){this.events[k]=f;}
  async connect(){await pause('connect');this.connected=true;}
  async startAudio(){await pause('audio');}
  async disconnect(){this.disconnects++;this.connected=false;this.events.Disconnected?.();}
 }
 let Panel;const context=vm.createContext({document,window,navigator:{onLine:true,mediaDevices:{getUserMedia:async()=>{await pause('microphone');const track={stopped:false,stop(){this.stopped=true;}};tracks.push(track);return {getTracks:()=>[track],getAudioTracks:()=>[track]};}}},LivekitClient:{Room,Track:{Source:{Microphone:'microphone'}},RoomEvent:new Proxy({},{get:(_,k)=>k})},TextDecoder,URL,location:{origin:'https://ha',protocol:'https:'},crypto:{randomUUID:()=>String(Math.random())},fetch:async(route,options)=>{const r=await request(route.slice(1),options.headers.Authorization);return {ok:r.status===200,json:async()=>r.body};},HTMLElement:class{attachShadow(){return dom;}},customElements:{define(name,value){Panel=value;}}});
 let panel;
 if(mode==='panel'){vm.runInContext(panelSource,context);panel=new Panel();panel.hass={callWS:async q=>q.type==='cloud/webrtc/ice_servers'?(await pause('ice'),[]):request(q.path,q.owner_token,q.capability)};panel.connectedCallback();}
 else vm.runInContext(desktop,context);
 const $=dom.getElementById;
 return {context,document,window,rooms,closed,requests,tracks,gate,$,panel,get sdkLoads(){return sdkLoads;},failClose(v){failedClose=v;},async login(){ $('owner-key').value='owner-secret-123456';await $('login').onsubmit({preventDefault(){}});},start(){return $('start').onclick();},stop(kind){if(kind==='hidden'){document.hidden=true;document.emit('visibilitychange');}else if(kind==='offline'){context.navigator.onLine=false;window.emit('offline');}else if(kind==='dispose'){if(panel)panel.disconnectedCallback();else window.emit('pagehide');}else $('end').onclick();},resume(){document.hidden=false;context.navigator.onLine=true;if(panel)panel.connectedCallback();window.emit('online');document.emit('visibilitychange');}};
}
for(const mode of ['desktop','panel']){
 for(const phase of ['create',...(mode==='panel'?['ice']:[]),'connect','audio','microphone','publish'])for(const reason of ['hidden','offline','dispose','end']){
  test(`${mode}: ${reason} during ${phase} cannot reactivate capture`,async()=>{
   const f=fixture(mode,phase);await f.login();const start=f.start();await flush();f.stop(reason);f.gate.release();await start;await flush();
   assert.ok(f.tracks.every(t=>t.stopped));assert.ok(f.rooms.every(r=>!r.connected));
   assert.equal(f.closed.length,1);assert.equal(f.closed[0],mode==='panel'?'cap-1':'room-1');
   f.resume();await flush();assert.equal(f.rooms.length,phase==='create'?0:1);assert.ok(f.$('text').disabled);
   assert.ok(!f.requests.some(r=>r.includes('task_stop')));
  });
 }
 for(const event of ['Reconnecting','SignalReconnecting','Disconnected'])test(`${mode}: ${event} stops capture without auto-reconnect`,async()=>{
  const f=fixture(mode);await f.login();await f.start();f.rooms[0].events[event]();await flush();assert.ok(f.tracks[0].stopped);assert.equal(f.closed.length,1);f.resume();assert.equal(f.rooms.length,1);
 });
 test(`${mode}: stale room events leave replacement usable; work is retained`,async()=>{
  const f=fixture(mode);await f.login();await f.start();const old=f.rooms[0];
  old.events.DataReceived(Buffer.from(JSON.stringify({type:'work_started',message_id:'work',text:'Onderzoek'})),{identity:'eva'},null,'eva.work');
  f.stop('hidden');assert.ok(f.tracks[0].stopped);assert.equal(f.$('work').children.length,1);
  f.resume();await f.start();old.events.Disconnected();old.events.ParticipantDisconnected({identity:'eva'});
  assert.equal(f.rooms.length,2);assert.equal(f.$('text').disabled,false);assert.equal(f.tracks[1].stopped,false);f.stop('end');await flush();
 });
 test(`${mode}: failed cleanup retains exact room for online retry`,async()=>{
  const f=fixture(mode);await f.login();await f.start();f.failClose(true);f.stop('offline');await flush();assert.ok(f.tracks[0].stopped);assert.equal(f.closed.length,0);
  f.failClose(false);f.resume();await flush();assert.deepEqual(f.closed,[mode==='panel'?'cap-1':'room-1']);assert.equal(f.rooms.length,1);
 });
 test(`${mode}: disposed pending create never steals replacement credentials`,async()=>{
  const f=fixture(mode,'create');await f.login();const old=f.start();await flush();f.stop('dispose');f.resume();await f.login();const fresh=f.start();await flush();f.gate.release();await Promise.all([old,fresh]);
  assert.deepEqual(f.closed,[mode==='panel'?'cap-1':'room-1']);assert.equal(f.$('text').disabled,false);assert.equal(f.rooms.length,1);f.stop('end');await flush();
 });
}

test('panel: failed SDK load can be retried by explicit start',async()=>{
 const f=fixture('panel','sdk-fail');await flush();await f.login();await f.start();assert.equal(f.sdkLoads,2);assert.equal(f.$('text').disabled,false);f.stop('end');await flush();
});
test('panel: SDK completion after dispose does not create a session',async()=>{
 const f=fixture('panel','sdk');await f.login();const start=f.start();f.stop('dispose');f.gate.release();await start;assert.equal(f.rooms.length,0);assert.ok(!f.requests.includes('session'));f.resume();await f.login();await f.start();assert.equal(f.rooms.length,1);f.stop('end');await flush();
});
