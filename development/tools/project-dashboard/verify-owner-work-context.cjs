const assert = require('node:assert/strict');
const fs = require('node:fs'); const http = require('node:http'); const path = require('node:path');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
const base='/internal/v1/dashboard';
const at='2026-10-16T10:00:00Z';
const wid='work:synthetic-one', resumed='work:synthetic-two', done='work:synthetic-done';
const output='Een kort gecontroleerd resultaat.\n'+ 'Nadere uitleg. '.repeat(70)+'ONLY_FULL_TAIL';
let state='admitted', mutations=[], denied=false;
function work(id,detail=false){
 const current=id===done?'verified':id===wid?state:'admitted';
 return {work_id:id,state:current,generation:id===resumed?2:1,parent_work_id:id===resumed?wid:null,
  stop_requested:current==='stopped',result_available:id===done,created_at:at,
  ...(detail?{task:'Zoek de oorspronkelijke gegevens',result:id===done?{output,completed_at:at,verified:true}:null,
    progress:{contract:'EvaWorkProgress/v1',updated_at:at,steps:[{id:'read',title:'Bron teruglezen',state:'working',evidence:'Eigen werknotitie'}],handoff:'Nog bezig'}}:{task_preview:'Zoek de oorspronkelijke gegevens'}),
  delivery:{state:'ready',description:'Resultaat beschikbaar; aflevering afzonderlijk'},scope:'private_tool_free_worker'};
}
const status={contract:'EvaDashboardStatus/v1',read_only:true,runtime:{ready:true,state:'ready'}};
const identity={contract:'EvaDashboardIdentity/v1',read_only:true,identity:{display_name:'Eva',identity_id:'synthetic',language:'nl',pack_sha256:'0'.repeat(64),pack_version:1,state:'current',temperament:['warm'],voice_style:{maximum_paragraphs:3,name:'warm',qualities:['helder']}}};
(async()=>{
 let origin=process.env.EVA_DASHBOARD_ORIGIN, server;const errors=[],external=[];
 if(!origin){
 server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://127.0.0.1'), resource=url.pathname.slice(base.length+1);
  res.setHeader('cache-control','no-store');
  if(url.pathname===base||url.pathname===base+'/'){res.setHeader('content-type','text/html');res.end(fs.readFileSync(path.join(__dirname,'dist/index.html')));return;}
  if(resource.startsWith('assets/')){const file=path.join(__dirname,'dist/assets',path.basename(resource));res.setHeader('content-type',file.endsWith('.js')?'text/javascript':'text/css');res.end(fs.readFileSync(file));return;}
  if(resource==='conversation'){res.setHeader('content-type','text/html');res.end(fs.readFileSync(process.env.EVA_CONVERSATION_FIXTURE));return;}
  if(resource==='conversation/voice-client.js'){res.setHeader('content-type','text/javascript');res.end('// Existing voice is outside this isolated text test.');return;}
  res.setHeader('content-type','application/json');let payload;
  if(req.method==='POST'){let body='';for await(const chunk of req)body+=chunk;payload=JSON.parse(body);}
  if(resource==='status'){res.end(JSON.stringify(status));return;}
  if(resource==='identity'){res.end(JSON.stringify(identity));return;}
  if(denied&&['work','work/context','conversation/work'].includes(resource)){res.writeHead(403);res.end('{}');return;}
  if(resource==='conversation/work'){res.end(JSON.stringify({contract:'EvaConversationWork/v1',conversation_id:'dashboard:synthetic',jobs:[work(wid)],truncated:false}));return;}
  if(resource==='work/return/history'){res.end(JSON.stringify({contract:'EvaPrivateWorkReturn/v1',status:'history',results:[]}));return;}
  if(resource==='work/return/poll'){res.end(JSON.stringify({contract:'EvaPrivateWorkReturn/v1',result:null}));return;}
  if(resource==='work/context'){res.end(JSON.stringify({contract:'EvaOwnerWorkContext/v1',work_id:payload.work_id,origin:{state:'available',input:'Mijn echte oorspronkelijke vraag',reply:'Ik ga ermee aan het werk.',channel:'Website',message_id:'synthetic:original',occurred_at:at,root_work_id:wid}}));return;}
  if(resource==='work'){res.end(JSON.stringify({contract:'EvaDelegatedWorkOwnerResult/v1',operation:'list',jobs:[work(wid),work(resumed),work(done)],truncated:false}));return;}
  if(['work/get','work/stop','work/resume'].includes(resource)){
   const op=resource.split('/')[1];if(op!=='get')mutations.push({operation:op,payload});
   if(op==='stop')state='stopped';
   res.end(JSON.stringify({contract:'EvaDelegatedWorkOwnerResult/v1',operation:op,work:work(op==='resume'?resumed:payload.work_id,true),created:op==='resume'}));return;
  }
  res.writeHead(404);res.end('{}');
 });await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
 }
 const browser=await chromium.launch({executablePath:process.env.EVA_DASHBOARD_CHROMIUM,headless:true,args:['--no-sandbox','--disable-gpu']});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.route('**/*',route=>{
    // Installed verification must not claim or acknowledge real queued returns.
    if(!server && route.request().url()===origin+base+'/work/return/poll')
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({contract:'EvaPrivateWorkReturn/v1',result:null})});
    if(route.request().url().startsWith(origin+'/'))return route.continue();
    external.push(new URL(route.request().url()).origin);return route.abort();
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(20000);
  if(server){
   await page.goto(origin+base+'/conversation');
   const linked=page.getByRole('region',{name:'Opdrachten bij dit gesprek'});
   await linked.getByText('Zoek de oorspronkelijke gegevens',{exact:true}).waitFor();
   await linked.getByRole('link',{name:'Voortgang, gesprek en resultaat openen'}).click();
  }else{
   // Use a real stored own job; read only. No stop, resume, chat or task creation.
   await page.goto(origin+base+'/conversation');
   const linked=page.getByRole('region',{name:'Opdrachten bij dit gesprek'});
   await linked.getByRole('link',{name:'Voortgang, gesprek en resultaat openen'}).first().waitFor();
   await linked.getByRole('link',{name:'Voortgang, gesprek en resultaat openen'}).first().click();
  }
  const detail=page.getByRole('article',{name:'Geopende hulpopdracht'});await detail.waitFor();
  const source=detail.getByRole('region',{name:'Oorspronkelijk gesprek'});await source.waitFor();
  if(server){
   await source.getByText('Jij: Mijn echte oorspronkelijke vraag',{exact:true}).waitFor();
   await detail.getByRole('region',{name:'Uitvoerplan en voortgang'}).waitFor();
   await detail.getByRole('button',{name:'Stop deze hulpopdracht',exact:true}).click();
   await detail.getByRole('button',{name:'Hervatten met nieuwe poging'}).waitFor();
   await detail.getByRole('button',{name:'Hervatten met nieuwe poging'}).click();
   await detail.getByText('Ingepland · poging 2',{exact:true}).waitFor();
   assert.deepEqual(mutations,[{operation:'stop',payload:{work_id:wid}},{operation:'resume',payload:{work_id:wid}}]);
   await page.goto(origin+base+'#/?section=work&work_id='+encodeURIComponent(done));
   await detail.getByRole('button',{name:'Volledig resultaat tonen'}).waitFor();
   assert.equal(await page.getByText(/ONLY_FULL_TAIL/).count(),0);
   await detail.getByRole('button',{name:'Volledig resultaat tonen'}).click();
   await detail.getByText(/ONLY_FULL_TAIL/).waitFor();
   await detail.getByRole('button',{name:'Volledig resultaat verbergen'}).click();
   assert.equal(await page.getByText(/ONLY_FULL_TAIL/).count(),0);
  }
  if(process.env.EVA_DASHBOARD_SCREENSHOT)await page.screenshot({path:process.env.EVA_DASHBOARD_SCREENSHOT});
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  if(server){
   await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
   await page.waitForFunction(()=>!document.querySelector('.work-detail'));
   denied=true;
   await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
   await page.getByRole('alert').waitFor();assert.equal(await page.getByText('Jij: Mijn echte oorspronkelijke vraag',{exact:true}).count(),0);
  }
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log(JSON.stringify({contract:'EvaOwnerWorkContextBrowserProof/v1',status:'passed',mode:server?'isolated':'installed-read-only',conversationWorkLink:true,storedDetailAndOriginRead:true,stopResumeExactSelection:Boolean(server),fullResultOnlyOnRequest:Boolean(server),hiddenRevokedDataCleared:Boolean(server),mobileWidth:390,javascriptErrors:0,externalRequests:0,conversationalModelCalls:0}));
 }finally{await browser.close();if(server)await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
