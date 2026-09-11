// Separate headless test browser, never the owner's extension session.
const {createRequire}=require('node:module');
const req=createRequire(process.env.EVA_CHROME_PACKAGE);
require('./worker.cjs').main(async()=>{
 const browser=await req('playwright').chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext();const page=await context.newPage();
 await page.goto(process.env.EVA_CHROME_FIXTURE);
 return {browser};
}).catch(()=>process.exit(1));
