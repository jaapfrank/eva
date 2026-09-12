import {createServer} from 'node:http';
import {pathToFileURL} from 'node:url';
import {readFile} from 'node:fs/promises';
import {timingSafeEqual} from 'node:crypto';
import {ChromeGate} from './gate.mjs';
import {removeStaleSocket} from './stale-socket.mjs';
import {ChromeWorker} from './process.mjs';

export async function serve(config) {
  if(config.mcpSocket)await removeStaleSocket(config.mcpSocket);
  const token=(await readFile(config.tokenFile,'utf8')).trim();
  if(token.length<32)throw Error('Private connector credential required');
  const factory=()=>new ChromeWorker(config.workerCommand,config.workerArgs,{...process.env,EVA_CHROME_PACKAGE:config.packagePath});
  const gate=new ChromeGate(factory);
  const catalogWorker=factory();
  try {gate.tools=await catalogWorker.ready;}finally {await catalogWorker.stop();}
  const json=(res,status,value)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
  const input=async req=>{let raw='';for await(const chunk of req){raw+=chunk;if(raw.length>65536)throw Error('Request too large');}return JSON.parse(raw);};
  const ui=await readFile(new URL('./owner.html',import.meta.url));
  const trial=await readFile(new URL('./trial.html',import.meta.url));
  const owner=createServer(async(req,res)=>{
    const host=req.headers.host || '';
    if(!['127.0.0.1','::1','::ffff:127.0.0.1'].includes(req.socket.remoteAddress) || !new RegExp(`^(localhost|127\\.0\\.0\\.1):${config.ownerPort}$`).test(host))return json(res,403,{error:'Local access only'});
    if(req.method==='GET' && (req.url==='/' || req.url==='/proef')){res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'"});return res.end(req.url==='/proef'?trial:ui);}
    if(req.method==='GET' && req.url==='/status')return json(res,200,gate.status());
    if(req.method!=='POST' || req.url!=='/control' || req.headers.origin!==`http://${host}` || !req.headers['content-type']?.startsWith('application/json'))return json(res,403,{error:'Invalid local request'});
    try {
      const {action,epoch}=await input(req);
      if(epoch!==gate.epoch)return json(res,409,{error:'Status changed; refresh first'});
      if(action==='takeover' || action==='disconnect')return json(res,200,await gate.stop(action==='takeover'?'owner':'disconnected'));
      if(action==='connect' || action==='resume') {
        if(!['owner','disconnected','recovery_required'].includes(gate.mode))throw Error('Connection already active');
        void gate.connect().catch(()=>{});
        return json(res,202,gate.status());
      }
      throw Error('Unknown control');
    }catch {return json(res,409,{error:'Control failed; refresh status'});}
  });
  const mcp=createServer(async(req,res)=>{
    const supplied=Buffer.from(req.headers.authorization || ''),expected=Buffer.from('Bearer '+token);
    if(supplied.length!==expected.length || !timingSafeEqual(supplied,expected))return json(res,401,{error:'Unauthorized'});
    if(req.url!=='/mcp' || req.method!=='POST')return json(res,405,{error:'POST /mcp required'});
    let msg;
    try {
      msg=await input(req);
      if(msg.jsonrpc!=='2.0' || typeof msg.method!=='string')throw Error('Invalid RPC');
      if(msg.method==='notifications/cancelled') {await gate.stop('disconnected');return json(res,202,{});}
      if(msg.id===undefined)return json(res,202,{});
      let result;
      if(msg.method==='initialize')result={protocolVersion:'2025-03-26',capabilities:{tools:{}},serverInfo:{name:'eva-shared-chrome',version:'1.0.0'}};
      else if(msg.method==='ping')result={};
      else if(msg.method==='tools/list')result={tools:gate.catalog()};
      else if(msg.method==='tools/call') {
        const epoch=gate.epoch;
        // Only the request that started the action can revoke it on transport loss.
        const ownsAction=gate.mode==='eva' && !gate.pending;
        const interrupted=()=>{if(ownsAction && gate.epoch===epoch)void gate.stop('disconnected');};
        res.once('close',interrupted);
        try {const call=gate.call(msg.params?.name,msg.params?.arguments);if(res.destroyed)interrupted();result=await call;}catch(error){result={isError:true,content:[{type:'text',text:error.message}]};}
        finally {res.removeListener('close',interrupted);}
      } else throw Error('Method unavailable');
      json(res,200,{jsonrpc:'2.0',id:msg.id,result});
    }catch {json(res,400,{jsonrpc:'2.0',id:msg?.id ?? null,error:{code:-32600,message:'Invalid request'}});}
  });
  await new Promise((resolve,reject)=>owner.once('error',reject).listen(config.ownerPort,'127.0.0.1',resolve));
  await new Promise((resolve,reject)=>{mcp.once('error',reject);if(config.mcpSocket)mcp.listen(config.mcpSocket,resolve);else mcp.listen(config.mcpPort,config.mcpHost,resolve);});
  return {gate,close:async()=>{await gate.stop('disconnected');owner.closeAllConnections();mcp.closeAllConnections();await Promise.all([new Promise(r=>owner.close(r)),new Promise(r=>mcp.close(r))]);}};
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href && process.argv[2]) {
  const app=await serve(JSON.parse(await readFile(process.argv[2],'utf8')));
  for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>void app.close().then(()=>process.exit(0)));
}
