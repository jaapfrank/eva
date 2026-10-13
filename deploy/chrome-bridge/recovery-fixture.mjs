// Controlled child process used only by recovery.test.mjs.
import {serve} from './server.mjs';
import {readFile} from 'node:fs/promises';
const app=await serve(JSON.parse(await readFile(process.argv[2],'utf8')));
process.on('message',async ({id,action})=>{
 try {
  if(action==='grant')await app.gate.connect();
  if(action==='revoke')await app.gate.stop('disconnected');
  if(action==='worker-loss')app.gate.worker.child.kill('SIGKILL');
  process.send({id,status:app.gate.status(),workerPid:app.gate.worker?.child.pid});
 }catch(error){process.send({id,error:error.message});}
});
process.send({ready:true,status:app.gate.status()});
for(const signal of ['SIGTERM','SIGINT'])process.on(signal,()=>void app.close().then(()=>process.exit(0)));
