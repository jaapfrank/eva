import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const [root, renovate] = process.argv.slice(2);
const {GlobalConfig} = await import(pathToFileURL(path.join(renovate,'dist/config/global.js')));
const {extractPackageFile} = await import(pathToFileURL(path.join(renovate,'dist/modules/manager/npm/extract/index.js')));
GlobalConfig.set({localDir:root,allowScripts:false,allowedCommands:[]});
const config=JSON.parse(await fs.readFile(path.join(root,'renovate.json'),'utf8'));
assert(config.enabledManagers.includes('npm'));
assert.equal(config.ignoreScripts,true);
assert.equal(config.packageRules.find(r=>r.matchManagers?.includes('npm')).automerge,false);
assert.equal(config.dependencyDashboardApproval,true);
assert(!config.postUpgradeTasks);
const paths=['package.json','eva2/deploy/chrome-bridge/package.json','eva2/experiments/filesystem-mcp-mutation-v1/package.json','eva2/experiments/react-admin-dashboard-v1/package.json'];
const results=[];
for(const p of paths){
 const result=await extractPackageFile(await fs.readFile(path.join(root,p),'utf8'),p,{ignoreScripts:true,skipInstalls:true});
 assert(result?.deps.length>0,p);
 assert.equal(result.managerData.npmLock,p.replace('package.json','package-lock.json'));
 results.push({packageFile:p,lockFile:result.managerData.npmLock,dependencies:result.deps.length});
}
console.log(JSON.stringify({renovate:JSON.parse(await fs.readFile(path.join(renovate,'package.json'))).version,results,mode:'extraction only; no lookup/install/update/platform writes'},null,2));