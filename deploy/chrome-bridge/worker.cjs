// Public MCP tools around one explicitly owned shared tab.
const {createInterface}=require('node:readline');
const {createRequire}=require('node:module');
const packagePath=process.argv[2] || process.env.EVA_CHROME_PACKAGE;
const req=packagePath?createRequire(require('node:path').resolve(packagePath)):require;
const {createConnection}=req('@playwright/mcp');
const {Client}=req('@modelcontextprotocol/sdk/client/index.js');
const {InMemoryTransport}=req('@modelcontextprotocol/sdk/inMemory.js');
const {attachExtension}=require('./extension-attach.cjs');
const allowed=new Set(['browser_snapshot','browser_navigate','browser_click','browser_hover','browser_type','browser_press_key','browser_select_option']);
const send=value=>process.stdout.write(JSON.stringify(value)+'\n');
let browser,page,context,connected=false;
async function main(connectBrowser) {
 for(const name of Object.keys(process.env))if(/^(PLAYWRIGHT_|PWTEST_)/.test(name))delete process.env[name];
 const server=await createConnection({saveSession:false,webmcp:false,ignoreRoots:true,timeouts:{action:15000},browser:{isolated:false}},async()=>{if(!context)throw Error('Explicit shared-tab connection required');return context;});
 const [clientTransport,serverTransport]=InMemoryTransport.createLinkedPair();
 const client=new Client({name:'Eva Chrome',version:'1.0.0'},{capabilities:{}});
 await server.connect(serverTransport);await client.connect(clientTransport);
 const catalog=(await client.listTools()).tools.filter(tool=>allowed.has(tool.name));
 if(catalog.length!==allowed.size || new Set(catalog.map(t=>t.name)).size!==allowed.size)throw Error('Required public browser tools unavailable');
 const schemas=catalog.map(tool=>({name:tool.name.replace('browser_','chrome_'),description:tool.description,inputSchema:structuredClone(tool.inputSchema),annotations:{readOnlyHint:tool.name==='browser_snapshot',destructiveHint:tool.name!=='browser_snapshot',idempotentHint:false,openWorldHint:true}}));
 for(const schema of schemas)delete schema.inputSchema.properties.filename;
 send({event:'catalog',tools:schemas});
 const failClosed=()=>{send({event:'disconnected'});process.exit(0);};
 createInterface({input:process.stdin}).on('line',async line=>{
  let message;try{message=JSON.parse(line);}catch{return;}
  try{
   if(message.method==='connect'){
    if(browser)throw Error('Already connected');
    ({browser}=await(connectBrowser?connectBrowser():attachExtension(req)));
    browser.once('disconnected',failClosed);context=browser.contexts()[0];
    if(!context || context.pages().length!==1)throw Error('Exactly one explicitly shared tab required');
    page=context.pages()[0];if(!/^https?:/.test(page.url()))throw Error('No existing web tab selected');
    page.once('close',failClosed);context.on('page',failClosed);connected=true;
    send({id:message.id,result:{connected:true}});
   }else if(message.method==='call'){
    if(!connected || !context || !browser.isConnected() || page.isClosed() || context.pages().length!==1 || context.pages()[0]!==page)throw Error('Shared tab unavailable');
    const name=message.name.replace('chrome_','browser_');
    if(!allowed.has(name)||message.arguments?._meta||message.arguments?.filename)throw Error('Tool unavailable');
    if(name==='browser_navigate'&&!/^https?:\/\//.test(message.arguments?.url||''))throw Error('Web navigation only');
    if(name==='browser_click'&&(message.arguments?.modifiers?.length||['middle','right'].includes(message.arguments?.button)))throw Error('Only ordinary in-page clicks available');
    if(name==='browser_press_key'&&/Control|Meta|Alt|F[1-9]/i.test(message.arguments?.key||''))throw Error('Browser shortcuts unavailable');
    const result=await client.callTool({name,arguments:message.arguments||{}});send({id:message.id,result});
   }else throw Error('Unknown command');
  }catch{send({id:message.id,error:'Chrome action failed; refresh or explicitly reconnect.'});}
 });
 process.stdin.on('end',()=>process.exit(0));
}
module.exports={main};if(require.main===module)main().catch(()=>process.exit(1));
