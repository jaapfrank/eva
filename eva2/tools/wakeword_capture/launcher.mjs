import {createCollector} from './server.mjs';
import {execFile} from 'node:child_process';
const url='http://127.0.0.1:55119';
const open=()=>execFile('cmd.exe',['/c','start','',url],{windowsHide:true},()=>{});
const server=createCollector();
server.once('listening',open);
server.on('error',async error=>{
 if(error.code==='EADDRINUSE'){
  try{const r=await fetch(url+'/api/protocol');const data=await r.json();if(data.version==='wake-collector-v1'){console.log('De opnameproef draait al.');open();return;}}catch{}
 }
 console.error('Starten lukt niet:',error.message);process.exitCode=1;
});

