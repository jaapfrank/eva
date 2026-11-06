const {createRequire}=require('node:module');
const fs=require('node:fs'); const path=require('node:path'); const assert=require('node:assert/strict');
const root=process.env.EVA_REPO_ROOT || '/home/administrator/project/Eva2';
const dashboard=path.join(root,'development/tools/project-dashboard');
const local=createRequire(path.join(dashboard,'package.json'));
const React=local('react'); const {renderToStaticMarkup}=local('react-dom/server');
(async()=>{process.chdir(dashboard);const {createServer}=await import(local.resolve('vite'));
const server=await createServer({mode:'project',server:{middlewareMode:true},appType:'custom'});
try{const {SmallTaskChecklist,default:ProjectWorkList}=await server.ssrLoadModule('/src/project-work-list.jsx');
const b=JSON.parse(fs.readFileSync(path.join(root,'development/design/product/execution-workboard-v1.json'),'utf8'));
const project=b.audit.projectDashboard;const render=filter=>renderToStaticMarkup(React.createElement(SmallTaskChecklist,{project,initialFilter:filter}));
const open=render('open'),all=render('all'),cancelled=render('cancelled'),done=render('done');
const count=html=>(html.match(/data-checklist-task-id=/g)||[]).length;
assert.equal(count(open),19);assert.equal(count(all),103);assert.equal(count(cancelled),1);assert.equal(count(done),83);
assert(!open.includes('data-checklist-task-id="E2.6"'));assert(!open.includes('data-checklist-task-id="C3.1"'));
assert(cancelled.includes('Door eigenaar ingetrokken'));assert(!cancelled.includes('Regel een apart telefoonnummer'));assert(done.includes('Door jou geaccepteerd'));
const parents=['FE5','C1','I1','FE2'];const actual=renderToStaticMarkup(React.createElement(ProjectWorkList,{project}));
for(const id of parents)assert(actual.includes('data-slice-id="'+id+'"'),id+' must remain open');
const terminal=structuredClone(project);
for(const slice of terminal.deliveryPlan.implementationSlices){if(parents.includes(slice.id)){
assert(!['completed','cancelled'].includes(slice.status));
for(const task of slice.remainingTasks || [])task.status='completed';}}
const isolated=renderToStaticMarkup(React.createElement(ProjectWorkList,{project:terminal}));
for(const id of parents)assert(isolated.includes('data-slice-id="'+id+'"'),id+' must stay open with all children terminal');
console.log(JSON.stringify({status:'passed',all:103,completed:83,cancelled:1,open:19,unfinishedParentsVisible:parents,unfinishedParentWithTerminalChildrenVisible:true,withdrawalHasNoAccountAction:true}));
}finally{await server.close()}})().catch(e=>{console.error(e);process.exitCode=1});
