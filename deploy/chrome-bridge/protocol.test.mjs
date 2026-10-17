import {test} from 'node:test';
import assert from 'node:assert/strict';
import {request} from 'node:http';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {serve} from './server.mjs';

test('standard MCP and explicit legacy aliases share one receiver without replay', async()=>{
 const dir=await mkdtemp(join(tmpdir(),'eva-chrome-protocol-'));
 let app;
 try {
  const token='synthetic-protocol-token-0000000000000000';
  const tokenFile=join(dir,'token');await writeFile(tokenFile,token,{mode:0o600});
  // A synthetic stdio worker has no browser, secrets or external effects.
  const worker=join(dir,'worker.cjs');
  await writeFile(worker,String.raw`process.stdout.write(JSON.stringify({event:'catalog',tools:[{name:'chrome_snapshot',description:'fixture',inputSchema:{type:'object',properties:{}}}]})+'\n');require('node:readline').createInterface({input:process.stdin}).on('line',line=>{const m=JSON.parse(line);process.stdout.write(JSON.stringify({id:m.id,result:{content:[{type:'text',text:'fixture'}]}})+'\n');});`);
  const socket=join(dir,'mcp.sock');
  app=await serve({tokenFile,mcpSocket:socket,ownerPort:0,workerCommand:process.execPath,workerArgs:[worker],packagePath:dir});
  let count=0;
  app.gate.call=async(name,args)=>{count++;assert.equal(name,'fixture');assert.deepEqual(args,{value:1});return {content:[{type:'text',text:'accepted'}]};};
  let serial=0;
  const rpc=method=>new Promise((resolve,reject)=>{
   const id=++serial;
   const req=request({socketPath:socket,path:'/mcp',method:'POST',headers:{authorization:'Bearer '+token,'content-type':'application/json'}},res=>{
    let raw='';res.on('data',c=>raw+=c);res.on('end',()=>resolve({status:res.statusCode,...JSON.parse(raw)}));
   });req.on('error',reject);req.end(JSON.stringify({jsonrpc:'2.0',id,method,params:{name:'fixture',arguments:{value:1}}}));
  });
  for(const method of ['tools/list','src/tools/list']){const result=await rpc(method);assert.equal(result.status,200);assert.equal(result.result.tools[0].name,'chrome_snapshot');}
  for(const method of ['tools/call','src/tools/call']){const before=count,result=await rpc(method);assert.equal(result.status,200);assert.equal(count,before+1);assert.equal(result.result.content[0].text,'accepted');}
  assert.equal(count,2);
  assert.equal((await rpc('tools/unknown')).status,400);assert.equal(count,2);
 } finally {await app?.close();await rm(dir,{recursive:true,force:true});}
});
