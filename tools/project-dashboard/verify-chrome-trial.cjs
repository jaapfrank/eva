const assert=require('node:assert/strict');
const {chromium}=require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE||'playwright');
(async()=>{
 const origin='http://127.0.0.1:44718';
 const before=await(await fetch(origin+'/status')).json();
 const invalid=await fetch(origin+'/control',{method:'POST',headers:{'Content-Type':'application/json',Origin:'https://other.invalid'},body:JSON.stringify({action:'connect',epoch:before.epoch})});
 assert.equal(invalid.status,403);
 assert.equal((await(await fetch(origin+'/status')).json()).epoch,before.epoch);
 const browser=await chromium.launch({headless:true,...(process.env.EVA_DASHBOARD_CHROMIUM?{executablePath:process.env.EVA_DASHBOARD_CHROMIUM}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  await page.goto('http://127.0.0.1:44712/');
  const card=page.locator('.practice-card').filter({has:page.getByRole('heading',{name:'Meekijken en overnemen',exact:true})});
  await card.getByText('Technisch getest · jouw Chrome-proef open',{exact:true}).waitFor();
  assert.equal(await card.getByRole('link',{name:'Open Chrome-proefpagina ↗'}).getAttribute('href'),origin+'/proef');
  assert.match(await card.innerText(),/precieze oorzaak is niet vastgesteld/);
  const state=await(await fetch('http://127.0.0.1:44712/project-state')).json();
  const row=state.project.capabilities.find(x=>x.id==='browser');assert.equal(row.product.complete,false);assert.notEqual(row.accepted,'yes');
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:844});await card.scrollIntoViewIfNeeded();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  }
  await page.goto(origin+'/proef');
  assert.equal(await page.title(),'Eva Chrome-bedieningsproef');
  assert.equal(await page.locator('#round-steps > li').count(),6);
  await page.getByText('Geen tabblad gedeeld — Eva kan niet bedienen',{exact:true}).waitFor();
  assert.match(await page.locator('#next').innerText(),/Begin bij stap 1/);
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:844});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.locator('#round-steps > li').last().scrollIntoViewIfNeeded();
   const box=await page.locator('#control').boundingBox();assert.ok(box.y>=0 && box.y+box.height<=844);
  }
  assert.equal(await page.locator('#pending').isDisabled(),true);
  await page.locator('#toggle').click();assert.equal(await page.locator('#detail').isVisible(),true);
  await page.locator('#toggle').click();assert.equal(await page.locator('#detail').isVisible(),false);
  assert.equal(await page.locator('#count').innerText(),'Proefklikken: 0');
  await page.route('**/status',route=>route.fulfill({json:{...before,state:'owner',busy:false,connected:false}}));
  await page.waitForFunction(()=>document.getElementById('state').textContent.includes('Jij hebt de bediening'));
  assert.equal(await page.locator('#release').isEnabled(),true);
  assert.match(await page.locator('#next').innerText(),/teller 0/);
  await page.locator('#release').click();
  await page.waitForFunction(()=>document.getElementById('next').textContent.includes('proefknop is vrijgegeven'));
  assert.equal(await page.locator('#pending').isEnabled(),true);
  await page.unroute('**/status');
  await page.route('**/status',route=>route.abort());
  await page.getByText('Verbindingsstatus niet beschikbaar',{exact:true}).waitFor();
  for(const id of ['takeover','resume','disconnect','release'])assert.equal(await page.locator('#'+id).isDisabled(),true);
  assert.equal((await(await fetch(origin+'/status')).json()).epoch,before.epoch);
  console.log('PASS Chrome trial: desktop/mobile dashboard, scoped evidence, no owner acceptance, local fixture toggle restored, denied foreign Origin.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
