// Only the upstream extension-attachment boundary remains source-bound.
const {readFileSync}=require('node:fs');
const {createHash}=require('node:crypto');
const expected=require('./extension-attach.json');
async function attachExtension(req) {
 const file=req.resolve('playwright-core/lib/coreBundle');
 if(req('@playwright/mcp/package.json').version!==expected.packageVersion || req('playwright-core/package.json').version!==expected.playwrightCoreVersion || createHash('sha256').update(readFileSync(file)).digest('hex')!==expected.coreBundleSha256)throw Error('Unreviewed extension attachment implementation');
 const {tools}=req(file);
 const config=await tools.resolveCLIConfigForMCP({extension:true,browser:'chrome',timeoutAction:15000},{});
 config.saveSession=false;
 return tools.createBrowserWithInfo(config,{clientName:'Eva — gedeeld Chrome-tabblad',cwd:process.cwd()},{browser:'chrome'},{title:'Eva — gedeeld Chrome-tabblad',workspaceDir:process.cwd(),evaNoBind:true});
}
module.exports={attachExtension};
