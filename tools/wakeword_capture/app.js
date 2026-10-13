const $=id=>document.getElementById(id);
let setup,session,capture,epoch=0,preview,objectUrl,settings={},busy=false;
const setError=text=>{$('error').textContent=text;};
async function api(url,options={}){const response=await fetch(url,{...options,headers:{'X-Capture-Token':setup?.token,...options.headers}});const data=await response.json();if(!response.ok)throw Error(data.error||'Bewaren lukt niet.');return data;}
function clearPreview(){preview=null;if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=null;$('listen').pause();$('listen').removeAttribute('src');$('listen').hidden=true;$('save').hidden=true;$('redo').hidden=true;}
function render(){
 const n=session.clips.length;localStorage.setItem('wake-session',session.id);$('intro').hidden=true;$('trial').hidden=n===30;$('finished').hidden=n!==30;
 if(n===30){$('folder').textContent=`${setup.storage}/${session.id}`;return;}
 const p=setup.protocol[n];$('progress').textContent=`Zin ${n+1} van 30`;$('bar').value=n;$('kind').textContent=p.label==='positive'?'Eva bewust aanspreken':'Gewone zin, géén oproep';$('phrase').textContent=p.text;$('position').textContent=p.position==='near'?'Dichtbij de microfoon':'Op je gewone praatplek';if(n===0||n===15)$('distance').value=p.suggestedDistanceM;
 $('state').textContent='Microfoon uit. Klaar voor deze zin.';$('record').disabled=false;$('stop').disabled=true;$('level').value=0;
}
function wav(parts){const length=parts.reduce((n,a)=>n+a.length,0),b=new ArrayBuffer(44+length*2),v=new DataView(b);const text=(offset,s)=>[...s].forEach((c,i)=>v.setUint8(offset+i,c.charCodeAt(0)));text(0,'RIFF');v.setUint32(4,36+length*2,true);text(8,'WAVE');text(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,16000,true);v.setUint32(28,32000,true);v.setUint16(32,2,true);v.setUint16(34,16,true);text(36,'data');v.setUint32(40,length*2,true);let index=44;for(const a of parts)for(const x of a){v.setInt16(index,Math.round(Math.max(-1,Math.min(1,x))*32767),true);index+=2;}return new Blob([b],{type:'audio/wav'});}
async function stop(discard=false){
 epoch++;const c=capture;capture=null;if(c){clearTimeout(c.timer);clearTimeout(c.speakTimer);c.stream?.getTracks().forEach(t=>t.stop());c.processor?.disconnect();c.source?.disconnect();c.context?.close().catch(()=>{});}
 $('record').disabled=false;$('stop').disabled=true;$('level').value=0;
 if(!discard&&c?.parts.length){preview=wav(c.parts);objectUrl=URL.createObjectURL(preview);$('listen').src=objectUrl;$('listen').hidden=false;$('save').hidden=false;$('redo').hidden=false;$('record').disabled=true;$('state').textContent='Microfoon uit. Alleen de getoonde zin gezegd? Bewaar hem, of neem opnieuw op.';}
 else{$('state').textContent='Microfoon uit. Niets uit deze poging bewaard.';clearPreview();}
}
async function start(){
 if(capture||busy)return;setError('');clearPreview();const mark=++epoch,c={parts:[]};capture=c;$('record').disabled=true;$('stop').disabled=false;$('state').textContent='Microfoon openen…';
 try{
  if(!navigator.mediaDevices?.getUserMedia||!window.AudioWorkletNode)throw Error('Open deze lokale proef in Chrome of Edge.');
  c.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:false,noiseSuppression:false,autoGainControl:false},video:false});
  if(mark!==epoch){c.stream.getTracks().forEach(t=>t.stop());return;}
  settings=c.stream.getAudioTracks()[0].getSettings();c.context=new AudioContext({sampleRate:16000});if(c.context.sampleRate!==16000)throw Error('Deze browser levert geen 16 kHz-opname. Probeer Chrome of Edge.');await c.context.resume();await c.context.audioWorklet.addModule('/capture-worklet.js');if(mark!==epoch)return;
  c.source=c.context.createMediaStreamSource(c.stream);c.processor=new AudioWorkletNode(c.context,'wake-capture');const mute=c.context.createGain();mute.gain.value=0;c.source.connect(c.processor);c.processor.connect(mute);mute.connect(c.context.destination);
  c.processor.port.onmessage=({data})=>{if(mark!==epoch)return;if(data.values){c.parts.push(data.values);let power=0;for(const x of data.values)power+=x*x;$('level').value=Math.min(1,Math.sqrt(power/data.values.length)*8);}if(data.done)stop();};
  c.processor.onprocessorerror=()=>{stop(true);setError('Audioverwerking stopte. Neem deze zin opnieuw op.');};
  $('state').textContent='Even wachten…';c.speakTimer=setTimeout(()=>{if(mark===epoch)$('state').textContent='Spreek nu — alleen de getoonde zin.';},700);c.timer=setTimeout(()=>{if(mark===epoch)stop();},6500);
 }catch(error){if(mark!==epoch)return;await stop(true);setError(error.name==='NotAllowedError'?'Microfoon niet toegestaan. Sta de microfoon toe voor deze lokale pagina en probeer opnieuw.':error.message);}
}
$('begin').onclick=async()=>{try{setError('');session=await api('/api/session',{method:'POST'});clearPreview();render();}catch(e){setError(e.message);}};
$('resume').onclick=async()=>{try{session=await api('/api/session/'+localStorage.getItem('wake-session'));clearPreview();render();}catch(e){setError(e.message);}};
$('record').onclick=start;$('stop').onclick=()=>stop();$('redo').onclick=()=>{clearPreview();render();start();};
$('save').onclick=async()=>{
 if(!preview||busy)return;busy=true;$('save').disabled=true;setError('');
 try{session=await api(`/api/session/${session.id}/clip/${setup.protocol[session.clips.length].id}`,{method:'POST',headers:{'Content-Type':'audio/wav','X-Distance-M':$('distance').value,'X-Label-Confirmed':'spoken-as-shown','X-Capture-Settings':btoa(JSON.stringify(Object.fromEntries(['sampleRate','channelCount','echoCancellation','noiseSuppression','autoGainControl'].map(k=>[k,settings[k]]))))},body:preview});clearPreview();render();}
 catch(e){setError(e.message);}finally{busy=false;$('save').disabled=false;}
};
$('pause').onclick=async()=>{await stop(true);$('trial').hidden=true;$('intro').hidden=false;$('resume').hidden=false;setError('Je bewaarde zinnen blijven staan. Ga later verder met dezelfde proef.');};
document.addEventListener('visibilitychange',()=>{if(document.hidden&&capture)stop(true);});
window.addEventListener('beforeunload',()=>{epoch++;capture?.stream?.getTracks().forEach(t=>t.stop());});
try{setup=await api('/api/protocol');$('begin').disabled=false;$('storage').textContent=setup.storage;if(localStorage.getItem('wake-session'))$('resume').hidden=false;}catch(e){setError(e.message);$('begin').disabled=true;}

