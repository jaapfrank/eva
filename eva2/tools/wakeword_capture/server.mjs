import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID, randomBytes, createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
export const prompts=[
 ['positive','Hé Eva.'],['positive','Hé Eva, hoe laat is het?'],['positive','Hé Eva, wil je me helpen?'],['positive','Hé Eva, vertel eens iets.'],['positive','Hé Eva, wat staat er op de planning?'],
 ['negative','Geef even de melk aan.'],['negative','Nee, even wachten.'],['negative','Heb je Eva vandaag gezien?'],['negative','Ik bel Eva morgen.'],['negative','Evelien komt straks langs.'],['negative','We eten vanavond samen.'],['negative','Zeven euro is voldoende.'],['negative','Ik ga even naar buiten.'],['negative','Wil je koffie of thee?'],['negative','De televisie staat nog aan.']
];
export const protocol=Array.from({length:30},(_,i)=>({id:String(i+1).padStart(2,'0'),label:prompts[i%15][0],text:prompts[i%15][1],position:i<15?'near':'normal',suggestedDistanceM:i<15?.5:2}));
export function inspectWav(b){
 if(b.length<44||b.toString('ascii',0,4)!=='RIFF'||b.toString('ascii',8,12)!=='WAVE'||b.toString('ascii',12,16)!=='fmt '||b.readUInt32LE(16)!==16||b.readUInt16LE(20)!==1||b.readUInt16LE(22)!==1||b.readUInt32LE(24)!==16000||b.readUInt32LE(28)!==32000||b.readUInt16LE(32)!==2||b.readUInt16LE(34)!==16||b.toString('ascii',36,40)!=='data'||b.readUInt32LE(40)!==b.length-44||b.readUInt32LE(4)!==b.length-8||(b.length-44)%2) throw Error('Ongeldig WAV-formaat; opnieuw opnemen.');
 const samples=(b.length-44)/2,duration=samples/16000;
 if(duration<1||duration>6.1)throw Error('De opname moet 1 tot 6 seconden duren.');
 let power=0,peak=0,clipped=0;
 for(let i=44;i<b.length;i+=2){const x=b.readInt16LE(i)/32768;power+=x*x;peak=Math.max(peak,Math.abs(x));if(Math.abs(x)>.995)clipped++;}
 const rms=Math.sqrt(power/samples);
 if(rms<.00001)throw Error('Geen hoorbaar signaal. Controleer de microfoon en neem opnieuw op.');
 return {sampleRate:16000,channels:1,encoding:'pcm_s16le',samples,durationSeconds:duration,rmsDbFS:20*Math.log10(rms),peak,clippedFraction:clipped/samples};
}
export function createCollector({port=55119,dataDir='E:/Projecten/wakeword/recordings',quiet=false}={}){
 const token=randomBytes(24).toString('hex'),uuid=/^[0-9a-f-]{36}$/; let actualPort=port,origin='http://127.0.0.1:'+port;
 fs.mkdirSync(dataDir,{recursive:true});
 const sessionPath=id=>{if(!uuid.test(id))throw Error('Ongeldige sessie.');return path.join(dataDir,id);};
 const read=id=>JSON.parse(fs.readFileSync(path.join(sessionPath(id),'manifest.json'),'utf8'));
 const persist=(id,m)=>{const dir=sessionPath(id);fs.writeFileSync(path.join(dir,'manifest.tmp'),JSON.stringify(m,null,2)+'\n');fs.renameSync(path.join(dir,'manifest.tmp'),path.join(dir,'manifest.json'));};
 const server=http.createServer(async(req,res)=>{
  res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','no-referrer');res.setHeader('Permissions-Policy','microphone=(self), camera=()');
  res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
  const send=(status,obj)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(obj));};
  if(req.headers.host!==`127.0.0.1:${actualPort}`)return send(403,{error:'Open de lokale 127.0.0.1-link.'});
  try{
   const url=new URL(req.url,origin);
   if(req.method==='GET'&&url.pathname==='/api/protocol')return send(200,{protocol,token,storage:dataDir,version:'wake-collector-v1'});
   if(req.method==='GET'&&url.pathname.startsWith('/api/session/'))return send(200,read(url.pathname.split('/').pop()));
   if(req.method==='GET'&&['/','/app.js','/capture-worklet.js','/style.css'].includes(url.pathname)){
    const name=url.pathname==='/'?'index.html':url.pathname.slice(1),mime=name.endsWith('.html')?'text/html; charset=utf-8':name.endsWith('.css')?'text/css':'text/javascript';res.writeHead(200,{'Content-Type':mime});return res.end(fs.readFileSync(path.join(here,name)));
   }
   if(req.method!=='POST')return send(404,{error:'Niet gevonden.'});
   if(req.headers.origin!==origin||req.headers['x-capture-token']!==token)return send(403,{error:'Herlaad de lokale proefpagina.'});
   if(Number(req.headers['content-length'])>210000)return send(413,{error:'Opname te groot.'});
   const parts=[];let size=0;for await(const chunk of req){size+=chunk.length;if(size>210000){send(413,{error:'Opname te groot.'});return;}parts.push(chunk);}const body=Buffer.concat(parts);
   if(url.pathname==='/api/session'){
    const id=randomUUID();fs.mkdirSync(sessionPath(id));const m={schema:'WakewordLabeledSession/v1',id,createdAt:new Date().toISOString(),person:'Jaap',purpose:'heldout_evaluation_only',captureSource:'browser_microphone_not_voice_pe',protocolVersion:'wake-collector-v1',expectedClips:30,protocol,clips:[]};persist(id,m);return send(201,m);
   }
   const match=url.pathname.match(/^\/api\/session\/([^/]+)\/clip\/(\d{2})$/);
   if(match){
    const [,id,clipId]=match,m=read(id),prompt=protocol.find(p=>p.id===clipId);
    if(!prompt||protocol[m.clips.length]?.id!==clipId)return send(409,{error:'Deze zin is al bewaard of niet de volgende. Herlaad de pagina.'});
    const distance=Number(req.headers['x-distance-m']);if(!Number.isFinite(distance)||distance<.1||distance>10)throw Error('Vul de afstand tot de microfoon in (0,1–10 meter).');
    if(req.headers['x-label-confirmed']!=='spoken-as-shown')throw Error('Bevestig eerst dat je alleen de getoonde zin uitsprak.');
    const audio=inspectWav(body),sha256=createHash('sha256').update(body).digest('hex');
    let settings;try{settings=JSON.parse(Buffer.from(req.headers['x-capture-settings']||'e30=','base64').toString('utf8'));}catch{throw Error('Ongeldige microfooninformatie.');}
    const captureSettings=Object.fromEntries(['sampleRate','channelCount','echoCancellation','noiseSuppression','autoGainControl'].filter(k=>['number','boolean'].includes(typeof settings[k])).map(k=>[k,settings[k]]));
    fs.writeFileSync(path.join(sessionPath(id),`${clipId}.wav`),body,{flag:'wx'});
    m.clips.push({...prompt,file:`${clipId}.wav`,sha256,distanceM:distance,labelConfirmed:'spoken-as-shown',capturedAt:new Date().toISOString(),captureSettings,...audio});
    if(m.clips.length===protocol.length){m.completedAt=new Date().toISOString();m.complete=true;}persist(id,m);return send(201,m);
   }
   send(404,{error:'Niet gevonden.'});
  }catch(e){send(e.code==='ENOENT'?404:400,{error:e.code==='ENOENT'?'Sessie niet gevonden. Start een nieuwe proef.':e.message});}
 });
 server.listen(port,'127.0.0.1',()=>{actualPort=server.address().port;origin='http://127.0.0.1:'+actualPort;if(!quiet)console.log(`Wakewordproef: ${origin} — uitsluitend lokale opslag: ${dataDir}`);});return server;
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))createCollector({port:Number(process.env.WAKE_CAPTURE_PORT||55119),dataDir:process.env.WAKE_CAPTURE_DATA_DIR||'E:/Projecten/wakeword/recordings'});

