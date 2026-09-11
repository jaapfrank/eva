import assert from 'node:assert/strict';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'node:http';
import {readDecisions,saveDecision,processDecision,decisionMiddleware,optionalOptions,authorizeOwner} from './scope-decisions.mjs';
import {applyProductCompletion} from './project-completion.mjs';
const root=await mkdtemp(join(tmpdir(),'eva-scope-test-'));const path=join(root,'decisions.json');
const localOwner=process.env.EVA_TEST_LOCAL_OWNER==='1';
const token='test-owner-token-never-a-real-secret-1234';
const board=JSON.parse(await readFile(process.env.EVA_PROJECT_BOARD_FILE,'utf8'));
const snapshot=await (await fetch('http://127.0.0.1:44712/project-state')).json();
const base=structuredClone(snapshot.project);
const state=async()=>{
 const ledger=await readDecisions(path);const project=structuredClone(base);const testBoard=structuredClone(board);
 testBoard.audit.optionalScopeDecisions=ledger.data.decisions;
 project.scopeDecisions={localOwner,revision:ledger.revision,events:ledger.data.events,options:optionalOptions};
 applyProductCompletion(project,testBoard);
 return {...snapshot,project};
};
const middleware=decisionMiddleware({path,localOwner,authorize:async value=>value===`Bearer ${token}`});
const server=createServer(async(req,res)=>{
 if(req.url==='/scope-decisions')return middleware(req,res);
 if(req.url==='/project-state'){res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(await state()));}
 const asset=new URL(req.url,'http://localhost').pathname;
 if(asset!=='/' && !/^\/assets\/[a-zA-Z0-9_.-]+$/.test(asset)){res.writeHead(404);return res.end();}
 try{res.setHeader('Content-Type',asset.endsWith('.js')?'application/javascript':asset.endsWith('.css')?'text/css':'text/html');res.end(await readFile(new URL(`./dist/project${asset==='/'?'/index.html':asset}`,import.meta.url)));}catch{res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin=`http://127.0.0.1:${server.address().port}`;
try {
 const empty=await readDecisions(path);
 const headers={'Content-Type':'application/json',Origin:origin};
 const input={optionId:'CO-004',decision:'included',requestId:'test-decision-id-0001',revision:empty.revision};
 if(!localOwner)assert.equal((await fetch(origin+'/scope-decisions',{method:'POST',headers,body:JSON.stringify(input)})).status,401);
 assert.equal((await fetch(origin+'/scope-decisions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input)})).status,403);
 assert.equal((await fetch(origin+'/scope-decisions',{method:'POST',headers:{...headers,Origin:'https://other.invalid',Authorization:`Bearer ${token}`},body:JSON.stringify(input)})).status,403);
 assert.equal((await readDecisions(path)).data.events.length,0);
 assert.equal(await authorizeOwner('http://test','Bearer fake',async()=>({status:200})),false,'unguarded owner endpoint must fail');
 let probes=0;assert.equal(await authorizeOwner('http://test','Bearer fake',async()=>++probes===1?{status:401,headers:new Headers({'www-authenticate':'Basic realm="Eva cockpit"'})}:{ok:true,json:async()=>({contract:'WrongApp'})}),false);
 const {chromium}=await import(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE+'/index.mjs');
 const browser=await chromium.launch({executablePath:process.env.EVA_DASHBOARD_CHROMIUM,headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100}});await page.goto(origin);
  await page.locator('.matrix-toggle').filter({hasText:'Geheugen'}).click();
  const choice=page.locator('[data-option-id="CO-004"]');await choice.getByText(/Onbeslist/).waitFor();
  if(!localOwner)await page.getByPlaceholder('Bestaand eigenaar-dashboardtoken').fill(token);
  else assert.equal(await page.locator('input[type=password]').count(),0);
  await choice.getByRole('button',{name:'Opnemen',exact:true}).click();await choice.getByText(/Opgenomen — moet/).waitFor();
  const first=(await readDecisions(path)).data.events[0];assert.equal(first.processing.status,'pending');
  await page.reload();await page.locator('.matrix-toggle').filter({hasText:'Geheugen'}).click();await choice.getByText(/Opgenomen — moet/).waitFor();
  if(!localOwner)assert.equal(await page.getByPlaceholder('Bestaand eigenaar-dashboardtoken').inputValue(),'','token not persisted');
  const selected=(await state()).project.capabilities.find(r=>r.id==='memory');assert.equal(selected.product.complete,false);const count=selected.product.total;
  if(!localOwner)await page.getByPlaceholder('Bestaand eigenaar-dashboardtoken').fill(token);
  else assert.equal(await page.locator('input[type=password]').count(),0);
  await choice.getByRole('button',{name:'Afwijzen',exact:true}).click();await choice.waitFor({state:'detached'});
  const excluded=(await state()).project.capabilities.find(r=>r.id==='memory');assert.equal(excluded.product.total,count-1);assert.ok(!excluded.product.steps.some(s=>s.id==='CO-004'));
  await page.locator('.scope-history > summary').click();await page.getByRole('button',{name:'Keuze heropenen'}).click();await choice.getByText(/Onbeslist/).waitFor();
  assert.equal((await state()).project.capabilities.find(r=>r.id==='memory').product.total,count);
  if(!localOwner)await page.getByPlaceholder('Bestaand eigenaar-dashboardtoken').fill('');
  if(process.env.EVA_PROJECT_SCREENSHOTS)await page.screenshot({path:join(process.env.EVA_PROJECT_SCREENSHOTS,'scope-decisions-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
 }finally{await browser.close();}
 const ledger=await readDecisions(path);const newest=ledger.data.events.at(-1);
 await saveDecision(path,{optionId:newest.optionId,decision:newest.decision,requestId:newest.id,revision:'old'});assert.equal((await readDecisions(path)).data.events.length,ledger.data.events.length,'same request cannot duplicate');
 await assert.rejects(saveDecision(path,{...input,requestId:'test-stale-request-9999'}),error=>error.status===409);
 await processDecision(path,{id:newest.id,expectedStatus:'pending',status:'acknowledged',consumer:'test-coordinator'});
 await assert.rejects(processDecision(path,{id:newest.id,expectedStatus:'pending',status:'acknowledged',consumer:'duplicate-consumer'}),error=>error.status===409);
 await assert.rejects(processDecision(path,{id:newest.id,expectedStatus:'acknowledged',status:'queued',consumer:'test-coordinator'}),error=>error.status===409,'undecided is not a build instruction');
 await processDecision(path,{id:newest.id,expectedStatus:'acknowledged',status:'done',consumer:'test-coordinator'});
 const next=await saveDecision(path,{optionId:'CO-005',decision:'included',requestId:'test-process-positive-12345',revision:(await readDecisions(path)).revision});
 await processDecision(path,{id:next.event.id,expectedStatus:'pending',status:'acknowledged',consumer:'test-coordinator'});
 await processDecision(path,{id:next.event.id,expectedStatus:'acknowledged',status:'queued',consumer:'test-coordinator',taskId:'existing-test-task'});
 await assert.rejects(processDecision(path,{id:next.event.id,expectedStatus:'acknowledged',status:'queued',consumer:'test-coordinator'}),error=>error.status===409);
 await processDecision(path,{id:next.event.id,expectedStatus:'queued',status:'done',consumer:'test-coordinator'});
 assert.equal((await state()).project.capabilities.find(row=>row.id==='memory').product.complete,false);
 console.log('PASS: isolated owner choices, auth/origin rejection, durable refresh, exclusion/count, restore, idempotency, claim-once, no acceptance promotion. Production choices untouched.');
}finally{await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});}
