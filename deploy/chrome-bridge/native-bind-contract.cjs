const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const {tools}=require('playwright-core/lib/coreBundle');const pw=require('playwright');const net=require('node:net');let browserPipeListenCalls=0;const originalListen=net.Server.prototype.listen;net.Server.prototype.listen=function(...args){if(typeof args[0]==='string'&&/pw-[0-9a-f]{8}-browser-/.test(args[0]))browserPipeListenCalls++;return originalListen.apply(this,args)};
async function main(){
 const registry=fs.mkdtempSync(path.join(os.tmpdir(),'eva-native-bind-proof-'));process.env.PWTEST_SERVER_REGISTRY=registry;process.env.PWTEST_SOCKETS_DIR=registry;
 const suffix='-'+crypto.createHash('sha1').update(registry).digest('hex').slice(0,8);const pipes=()=>fs.readdirSync(String.fromCharCode(92,92)+'.'+String.fromCharCode(92)+'pipe'+String.fromCharCode(92)).filter(n=>/^pw-[0-9a-f]{8}-browser-/.test(n)&&n.endsWith(suffix));const rows=()=>fs.readdirSync(registry).filter(n=>fs.statSync(path.join(registry,n)).isFile());const results=[];
 try{
  for(const noBind of [false,true]){
   assert.deepEqual(rows(),[]);assert.deepEqual(pipes(),[]);
   const beforePipeCalls=browserPipeListenCalls;const config=await tools.resolveCLIConfigForMCP({browser:'chrome',headless:true,isolated:true},{});config.saveSession=false;
   let info;try{
    info=await tools.createBrowserWithInfo(config,{clientName:'Synthetic no-bind contract',cwd:process.cwd()},{browser:'chrome'},{title:'Synthetic no-bind contract',workspaceDir:process.cwd(),evaNoBind:noBind});
    const context=await info.browser.newContext();const page=await context.newPage();await page.setContent('<button>Synthetic owned tab</button>');assert.equal(await page.locator('button').textContent(),'Synthetic owned tab');
    if(noBind){assert.equal(browserPipeListenCalls-beforePipeCalls,0);assert.equal(info.endpoint,undefined);assert.deepEqual(rows(),[]);assert.deepEqual(pipes(),[]);}
    else{assert.equal(browserPipeListenCalls-beforePipeCalls,1);assert.equal(typeof info.endpoint,'string');assert.equal(rows().length,1);assert.equal(pipes().length,1);const other=await pw.chromium.connect(info.endpoint);assert.equal(other.contexts().length,1);assert.equal(other.contexts()[0].pages().length,1);await other.close();}
    results.push({noBind,passed:true,actualBrowserPipeListenCalls:browserPipeListenCalls-beforePipeCalls,endpointReturned:typeof info.endpoint==='string',registryRecords:rows().length,discoverableBrowserPipes:pipes().length,syntheticPageRead:true});
   }finally{await info?.browser.close();}
   for(let i=0;i<100&&(rows().length||pipes().length);i++)await new Promise(r=>setTimeout(r,20));assert.deepEqual(rows(),[]);assert.deepEqual(pipes(),[]);results.at(-1).afterCloseRegistryEmpty=true;results.at(-1).afterClosePipeGone=true;
  }
  console.log(JSON.stringify({passed:true,results,actualWindowsNativeFactory:true,extensionOwnerSessionUsed:false,modelCalls:0,upstreamDefaultBranchStillWorks:true}));
 }finally{net.Server.prototype.listen=originalListen;assert.deepEqual(rows(),[]);assert.ok(path.resolve(registry).startsWith(path.resolve(os.tmpdir())+path.sep+'eva-native-bind-proof-'));fs.rmSync(registry,{recursive:true});}
}
main().catch(error=>{console.error(error.stack);process.exitCode=1});
