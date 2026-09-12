import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {serve} from './server.mjs';
import {fileURLToPath} from 'node:url';
import {dirname,join} from 'node:path';
const base=dirname(fileURLToPath(import.meta.url));
if(!process.argv[2])throw Error('Pass the private bridge configuration path for this isolated test.');
const config=JSON.parse(await readFile(process.argv[2],'utf8'));
const windowsCommand=config.workerCommand,windowsPackage=config.packagePath,windowsFixture=config.workerArgs[0].replace(/worker\.cjs$/, 'browser-fixture.cjs');
if(!windowsFixture.endsWith('/browser-fixture.cjs'))throw Error('Isolated fixture entry point required');
Object.assign(config,{ownerPort:44719,mcpSocket:join(dirname(config.mcpSocket),'test-mcp.sock'),workerCommand:'/usr/bin/node',workerArgs:[base+'/browser-fixture.cjs'],packagePath:base+'/package.json'});
process.env.EVA_CHROME_PACKAGE=base+'/package.json';
// For Linux, optionally provide EVA_CHROME_TEST_EXECUTABLE; otherwise installed Chrome is used.
process.env.EVA_CHROME_FIXTURE='http://127.0.0.1:44720/';
if(process.env.EVA_TEST_WINDOWS==='1'){
 config.workerCommand=windowsCommand;config.workerArgs=[windowsFixture,windowsPackage,'http://127.0.0.1:44720/'];config.packagePath=windowsPackage;process.env.EVA_CHROME_PACKAGE=config.packagePath;delete process.env.EVA_CHROME_TEST_EXECUTABLE;
}
let count=0,enabled=false;
const page=createServer((req,res)=>{
 if(req.url==='/effect'){count++;res.end('ok');return;}
 if(req.url==='/release'){enabled=true;res.end('ok');return;}
 if(req.url==='/reset'){enabled=false;count=0;res.end('ok');return;}
 if(req.url==='/evidence'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({count,enabled}));return;}
 res.setHeader('Content-Type','text/html; charset=utf-8');
 res.end(`<!doctype html><html lang="nl"><title>Eva eigen Chromeproef</title><h1>Eva eigen Chromeproef</h1><button id="toggle" aria-expanded="false">Toon proeftekst</button><p id="detail" hidden>De proeftekst is zichtbaar</p><button id="pending" disabled>Tel één proefklik</button><p id="count">Proefklikken: 0</p><script>const t=document.getElementById('toggle'),d=document.getElementById('detail'),p=document.getElementById('pending');t.onclick=()=>{d.hidden=!d.hidden;t.setAttribute('aria-expanded',String(!d.hidden));t.textContent=d.hidden?'Toon proeftekst':'Verberg proeftekst';};p.onclick=()=>fetch('/effect');setInterval(async()=>{const s=await(await fetch('/evidence')).json();p.disabled=!s.enabled;document.getElementById('count').textContent='Proefklikken: '+s.count;},100);</script></html>`);
});
await new Promise(r=>page.listen(44720,'127.0.0.1',r));
const app=await serve(config);
await app.gate.connect();
console.log(JSON.stringify({ready:true,state:app.gate.status(),testPage:'http://127.0.0.1:44720/'}));
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>void app.close().then(()=>{page.closeAllConnections();page.close(()=>process.exit(0));}));
