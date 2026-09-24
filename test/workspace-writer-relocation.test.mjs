import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import {once} from 'node:events';
import {createHash} from 'node:crypto';
import {requestFrame, runBrokerResult} from '../eva2/deploy/living-core/workspace-create-only/broker-client.mjs';
import {resolveProjectJournal} from '../eva2/deploy/living-core/workspace-create-only/project-journal.mjs';
import {prepareRelocation, validateRelocationBinding, openRelocationSnapshot} from '../eva2/deploy/living-core/workspace-create-only/relocation.mjs';

const hash = b => createHash('sha256').update(b).digest('hex');
const key = 'a'.repeat(64);
const first = {operation_id:'1'.repeat(64), request_binding_sha256:'2'.repeat(64), expected_generation:0, path:key+'-first.md', content:'Private result café\n'};
const second = {...first, operation_id:'3'.repeat(64), expected_generation:1, path:key+'-second.md', content:'Missing second result\n'};
const flags = fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW;
const statId = file => { const s = fs.statSync(file); return {dev:s.dev, ino:s.ino}; };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function fixture(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-relocation-'));
  const bin = dir+'/broker';
  const compiled = spawnSync('gcc', ['-std=c17','-O2','-Wall','-Wextra','-Werror','-DEVWW_TEST_PHASE_BARRIERS','-o',bin,new URL('../native/workspace_writer_broker.c',import.meta.url).pathname]);
  assert.equal(compiled.status, 0, String(compiled.stderr));
  for (const name of ['source','source-journal']) fs.mkdirSync(dir+'/'+name, {mode:0o700});
  const held = [];
  const hold = name => { const fd=fs.openSync(dir+'/'+name,flags); held.push(fd); return fd; };
  const rootFd=hold('source'), journalFd=hold('source-journal');
  const childFd=resolveProjectJournal(journalFd,first.path); held.push(childFd);
  const originalBindingBytes=Buffer.from(JSON.stringify({version:2,journal_mode:'per_path_prefix_v1',project_journal_identity:'c'.repeat(64),binary_sha256:hash(fs.readFileSync(bin)),client_sha256:'d'.repeat(64),resolver_sha256:'e'.repeat(64),root:statId(dir+'/source'),journal:statId(dir+'/source-journal')}));
  const run=(request,observe=false,relocationFd=null,fds=[rootFd,childFd])=>spawnSync(bin,relocationFd===null?(observe?['--observe']:[]):[observe?'--observe-relocated':'--relocated'],{input:requestFrame(request),stdio:['pipe','pipe','pipe',...fds,...(relocationFd===null?[]:[relocationFd])]});
  async function crash(phase, request=first) {
    const child=spawn(bin,[],{stdio:['pipe','pipe','pipe',rootFd,childFd],env:{PATH:'/usr/bin:/bin',EVWW_TEST_STOP_PHASE:phase}});
    child.stdin.end(requestFrame(request));
    let stopped=false;
    for(let i=0;i<300;i++) {try{if(/^State:\s+T/m.test(fs.readFileSync('/proc/'+child.pid+'/status','utf8'))){stopped=true;break;}}catch{}await delay(10);}
    assert.ok(stopped,phase);const exited=once(child,'exit');child.kill('SIGKILL');await exited;
  }
  function copy() {
    fs.cpSync(dir+'/source',dir+'/target',{recursive:true});fs.cpSync(dir+'/source-journal',dir+'/target-journal',{recursive:true});
    for(const name of ['target','target-journal','target-journal/'+key]) fs.chmodSync(dir+'/'+name,0o700);
    const targetRoot=hold('target'), targetJournal=hold('target-journal');
    assert.notDeepEqual(statId(dir+'/source'),statId(dir+'/target'));
    const packageResult=prepareRelocation({executable:bin,rootFd:targetRoot,journalFd:targetJournal,originalBindingBytes,logicalCatalogIdentity:'f'.repeat(64),projectKeys:[key]});
    const validated=validateRelocationBinding(packageResult.bindingBytes,{executable:bin,rootFd:targetRoot,journalFd:targetJournal});
    assert.equal(validated.binding.logical_provider_identity,hash(originalBindingBytes));
    assert.deepEqual(validated.originalBytes,originalBindingBytes);
    const project=validated.binding.projects[key];
    const targetChild=resolveProjectJournal(targetJournal,first.path,{observe:true,relocation:project});held.push(targetChild);
    const authority=dir+'/authority';fs.writeFileSync(authority,packageResult.snapshots[key],{mode:0o400});
    const relocationFd=openRelocationSnapshot(project,authority);held.push(relocationFd);
    return {rootFd:targetRoot,journalFd:targetJournal,childFd:targetChild,relocationFd,project,authority,packageResult,run:(request,observe=false)=>run(request,observe,relocationFd,[targetRoot,targetChild]),unbound:(request,observe=false)=>run(request,observe,null,[targetRoot,targetChild])};
  }
  try {await fn({dir,bin,rootFd,journalFd,childFd,run,crash,copy,originalBindingBytes});}
  finally {held.forEach(fd=>fs.closeSync(fd));fs.rmSync(dir,{recursive:true,force:true});}
}
test('read-only preparation recheck preserves exact authority and detects changed project material',()=>fixture(async({dir,run,copy,bin,originalBindingBytes})=>{
  const receipt=run(first);assert.equal(receipt.status,0,String(receipt.stderr));
  const target=copy();
  const journal=dir+'/target-journal/'+key+'/workspace-writer.journal';
  const before=fs.readFileSync(journal), beforeStat=fs.statSync(journal);
  const recheck=()=>prepareRelocation({executable:bin,rootFd:target.rootFd,journalFd:target.journalFd,
    originalBindingBytes,logicalCatalogIdentity:'f'.repeat(64),projectKeys:[key]});
  const verified=recheck();
  assert.deepEqual(verified.bindingBytes,target.packageResult.bindingBytes);
  assert.deepEqual(verified.snapshots[key],target.packageResult.snapshots[key]);
  assert.deepEqual(fs.readFileSync(journal),before);
  assert.equal(fs.statSync(journal).mtimeMs,beforeStat.mtimeMs);
  assert.equal(fs.statSync(journal).ctimeMs,beforeStat.ctimeMs);
  fs.writeFileSync(dir+'/target/'+first.path,'Changed retained result\n');
  assert.throws(recheck,/relocation_snapshot_refused/);
  assert.deepEqual(fs.readFileSync(journal),before);
}));
test('copied completed project keeps exact receipts and journal prefix; observe writes nothing; only missing step executes once',()=>fixture(async({dir,run,copy,bin,originalBindingBytes})=>{
  const receipt=run(first);assert.equal(receipt.status,0,String(receipt.stderr));
  const original=fs.readFileSync(dir+'/source-journal/'+key+'/workspace-writer.journal');
  const target=copy();
  const journal=dir+'/target-journal/'+key+'/workspace-writer.journal';
  const before=fs.statSync(journal);
  assert.notEqual(target.unbound(first,true).status,0);
  const missingAuthority=spawnSync(bin,['--relocated'],{input:requestFrame(first),stdio:['pipe','pipe','pipe',target.rootFd,target.childFd]});
  assert.notEqual(missingAuthority.status,0);assert.match(String(missingAuthority.stderr),/relocation_authority_refused/);
  const observed=target.run(first,true);assert.equal(observed.status,0,String(observed.stderr));assert.deepEqual(observed.stdout,receipt.stdout);
  assert.deepEqual(fs.readFileSync(journal),original);assert.equal(fs.statSync(journal).mtimeMs,before.mtimeMs);assert.equal(fs.statSync(journal).ctimeMs,before.ctimeMs);
  const next=target.run(second);assert.equal(next.status,0,String(next.stderr));const after=fs.readFileSync(journal);assert.deepEqual(after.subarray(0,original.length),original);assert.ok(after.length>original.length);
  const replay=target.run(second);assert.equal(replay.status,0,String(replay.stderr));assert.deepEqual(replay.stdout,next.stdout);assert.deepEqual(fs.readFileSync(journal),after);
  assert.deepEqual(target.run(first,true).stdout,receipt.stdout);
  const client=runBrokerResult({executable:bin,rootFd:target.rootFd,journalFd:target.childFd,request:first,observe:true,relocationFd:target.relocationFd});assert.equal(client.status,'receipt');
  assert.equal(fs.readdirSync(dir+'/target').length,2);
  assert.throws(()=>resolveProjectJournal(target.journalFd,first.path,{observe:true}),/identity_mismatch/);
  assert.throws(()=>prepareRelocation({executable:bin,rootFd:target.rootFd,journalFd:target.journalFd,originalBindingBytes:target.packageResult.bindingBytes,logicalCatalogIdentity:'f'.repeat(64),projectKeys:[key]}),/source_binding_refused/);
  assert.throws(()=>prepareRelocation({executable:bin,rootFd:target.rootFd,journalFd:target.journalFd,originalBindingBytes,logicalCatalogIdentity:'f'.repeat(64),projectKeys:[key]}),/chain_unsupported/);
}));
test('created and noop receipts sharing a copied material retain their separate original bytes',()=>fixture(async({run,copy})=>{
  const created=run(first);assert.equal(created.status,0,String(created.stderr));
  const noopRequest={...first,operation_id:'4'.repeat(64),expected_generation:1};
  const noop=run(noopRequest);assert.equal(noop.status,0,String(noop.stderr));assert.equal(noop.stdout[8],3);
  const target=copy();
  const observedCreated=target.run(first,true),observedNoop=target.run(noopRequest,true);
  assert.equal(observedCreated.status,0,String(observedCreated.stderr));assert.equal(observedNoop.status,0,String(observedNoop.stderr));
  assert.deepEqual(observedCreated.stdout,created.stdout);assert.deepEqual(observedNoop.stdout,noop.stdout);
}));
for(const phase of ['post-P','post-L'])test('copied '+phase+' uses existing recovery and keeps old prefix',()=>fixture(async({dir,run,crash,copy})=>{
  await crash(phase);const oldObserve=run(first,true);const prefix=fs.readFileSync(dir+'/source-journal/'+key+'/workspace-writer.journal');
  const target=copy();const observed=target.run(first,true);assert.equal(observed.status===0,phase==='post-L');
  if(phase==='post-L')assert.deepEqual(observed.stdout,oldObserve.stdout);
  assert.deepEqual(fs.readFileSync(dir+'/target-journal/'+key+'/workspace-writer.journal'),prefix);
  const recovered=target.run(first);assert.equal(recovered.status,0,String(recovered.stderr));
  const after=fs.readFileSync(dir+'/target-journal/'+key+'/workspace-writer.journal');assert.deepEqual(after.subarray(0,prefix.length),prefix);
  const replay=target.run(first);assert.equal(replay.status,0,String(replay.stderr));assert.deepEqual(replay.stdout,recovered.stdout);assert.deepEqual(fs.readFileSync(dir+'/target-journal/'+key+'/workspace-writer.journal'),after);
}));
test('uncertain P-only publication cannot be adopted by offline snapshot',()=>fixture(async({dir,crash,copy})=>{
  await crash('postlink-pre-L');const prefix=fs.readFileSync(dir+'/source-journal/'+key+'/workspace-writer.journal');assert.throws(copy,/pending_effect_uncertain/);assert.deepEqual(fs.readFileSync(dir+'/source-journal/'+key+'/workspace-writer.journal'),prefix);
}));
test('tampered authority, prefix, material, project marker and wrong package identity fail closed',()=>fixture(async({dir,run,copy,bin})=>{
  assert.equal(run(first).status,0);const target=copy();const journal=dir+'/target-journal/'+key+'/workspace-writer.journal';const prefix=fs.readFileSync(journal);
  const altered=JSON.parse(target.packageResult.bindingBytes);altered.logical_provider_identity='0'.repeat(64);
  assert.throws(()=>validateRelocationBinding(Buffer.from(JSON.stringify(altered)),{executable:bin,rootFd:target.rootFd,journalFd:target.journalFd}),/binding_refused/);
  fs.chmodSync(target.authority,0o600);assert.notEqual(target.run(first,true).status,0);fs.chmodSync(target.authority,0o400);
  const snapshot=fs.readFileSync(target.authority);fs.chmodSync(target.authority,0o600);const corrupt=Buffer.from(snapshot);corrupt[48]^=1;fs.writeFileSync(target.authority,corrupt);fs.chmodSync(target.authority,0o400);assert.notEqual(target.run(first,true).status,0);assert.throws(()=>openRelocationSnapshot(target.project,target.authority),/binding_mismatch/);
  fs.chmodSync(target.authority,0o600);fs.writeFileSync(target.authority,snapshot);fs.chmodSync(target.authority,0o400);
  const changed=Buffer.from(prefix);changed[changed.length-1]^=1;fs.writeFileSync(journal,changed);assert.notEqual(target.run(first,true).status,0);fs.writeFileSync(journal,prefix);
  fs.writeFileSync(dir+'/target/'+first.path,'different');assert.notEqual(target.run(first,true).status,0);assert.deepEqual(fs.readFileSync(journal),prefix);
  const marker=dir+'/target-journal/'+key+'/.project-identity.json';fs.chmodSync(marker,0o600);fs.writeFileSync(marker,'{}');fs.chmodSync(marker,0o400);assert.throws(()=>resolveProjectJournal(target.journalFd,first.path,{observe:true,relocation:target.project}),/relocation_mismatch/);
}));
test('authority and copied private material owned by another uid are refused',async t=>{
  if(spawnSync('sudo',['-n','true']).status!==0){t.skip('requires noninteractive chown for a real foreign-owner fixture');return;}
  await fixture(async({dir,run,copy})=>{
    assert.equal(run(first).status,0);const target=copy();
    const chown=(uid,file)=>assert.equal(spawnSync('sudo',['-n','chown',String(uid),'--',file]).status,0);
    chown(process.getuid()+1,target.authority);
    const denied=target.run(first,true);assert.notEqual(denied.status,0);assert.match(String(denied.stderr),/relocation_authority_refused/);
    assert.throws(()=>openRelocationSnapshot(target.project,target.authority),/authority_refused|EACCES/);
    chown(process.getuid(),target.authority);
    chown(process.getuid()+1,dir+'/target/'+first.path);
    assert.notEqual(target.run(first,true).status,0);
  });
});
