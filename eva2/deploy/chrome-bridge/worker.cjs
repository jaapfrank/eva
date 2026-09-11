// Pinned upstream extension transport and browser tools. No browser is opened
// until the owner sends connect. The parent kills this process on takeover.
const { createInterface } = require('node:readline');
const { createRequire } = require('node:module');
const packagePath=process.argv[2] || process.env.EVA_CHROME_PACKAGE;
const req = packagePath ? createRequire(require('node:path').resolve(packagePath)) : require;
if (req('@playwright/mcp/package.json').version !== '0.0.80') throw Error('Unsupported Playwright MCP version');
const { tools } = req('playwright-core/lib/coreBundle');
const { z } = req('playwright-core/lib/utilsBundle');
const allowed = new Set(['browser_snapshot','browser_navigate','browser_click','browser_hover','browser_type','browser_press_key','browser_select_option']);
let browser, backend, page;
const send = value => process.stdout.write(JSON.stringify(value)+'\n');
async function main(connectBrowser) {
  // Deliberately do not inherit MCP settings, profile overrides or approval tokens.
  for (const name of Object.keys(process.env)) if (/^(PLAYWRIGHT_|PWTEST_)/.test(name)) delete process.env[name];
  const config = await tools.resolveCLIConfigForMCP({extension:true,browser:'chrome'}, {});
  config.saveSession = false;
  const selected = tools.filteredTools(config).filter(tool=>allowed.has(tool.schema.name));
  const schemas = selected.map(tool=>({name:tool.schema.name.replace('browser_','chrome_'),description:tool.schema.description,inputSchema:z.toJSONSchema(tool.schema.inputSchema),annotations:{readOnlyHint:tool.schema.name==='browser_snapshot',destructiveHint:tool.schema.name!=='browser_snapshot',idempotentHint:false,openWorldHint:true}}));
  for(const schema of schemas)delete schema.inputSchema.properties.filename;
  send({event:'catalog',tools:schemas});
  const failClosed = () => { send({event:'disconnected'}); process.exit(0); };
  createInterface({input:process.stdin}).on('line',async line=>{
    let message;try {message=JSON.parse(line);}catch {return;}
    try {
      if(message.method==='connect') {
        if(browser) throw Error('Already connected');
        ({browser} = await (connectBrowser ? connectBrowser() : tools.createBrowserWithInfo(config,{clientName:'Eva — gedeeld Chrome-tabblad',cwd:process.cwd()},{browser:'chrome'})));
        browser.once('disconnected',failClosed);
        const context=browser.contexts()[0];
        if(!context || context.pages().length!==1) throw Error('Exactly one explicitly shared tab required');
        page=context.pages()[0];
        if(!/^https?:/.test(page.url())) throw Error('No existing web tab selected');
        page.once('close',failClosed);
        context.on('page',failClosed); // A new tab is outside this connection's grant.
        backend=new tools.BrowserBackend(config,context,selected);
        await backend.initialize({clientName:'Eva Chrome',cwd:process.cwd()});
        backend.once('disconnected',failClosed);
        send({id:message.id,result:{connected:true}});
      } else if(message.method==='call') {
        if(!backend || !browser.isConnected() || page.isClosed()) throw Error('Shared tab unavailable');
        const name=message.name.replace('chrome_','browser_');
        if(!allowed.has(name) || message.arguments?._meta || message.arguments?.filename) throw Error('Tool unavailable');
        if(name==='browser_navigate' && !/^https?:\/\//.test(message.arguments?.url || ''))throw Error('Web navigation only');
        if(name==='browser_click' && (message.arguments?.modifiers?.length || ['middle','right'].includes(message.arguments?.button)))throw Error('Only ordinary in-page clicks available');
        // Browser-level shortcuts could switch or create an unshared tab.
        if(name==='browser_press_key' && /Control|Meta|Alt|F[1-9]/i.test(message.arguments?.key || '')) throw Error('Browser shortcuts unavailable');
        const result=await backend.callTool(name,message.arguments || {});
        send({id:message.id,result});
      } else throw Error('Unknown command');
    } catch { send({id:message.id,error:'Chrome action failed; refresh or explicitly reconnect.'}); }
  });
  process.stdin.on('end',()=>process.exit(0));
}
module.exports={main};
if(require.main===module)main().catch(()=>process.exit(1));
