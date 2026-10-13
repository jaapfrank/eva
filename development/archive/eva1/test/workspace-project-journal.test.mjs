import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import {spawn, spawnSync} from 'node:child_process';
import {once} from 'node:events';
import {resolveProjectJournal} from '../eva2/deploy/living-core/workspace-create-only/project-journal.mjs';
import {runBrokerResult} from '../eva2/deploy/living-core/workspace-create-only/broker-client.mjs';
const resolver = new URL('../eva2/deploy/living-core/workspace-create-only/project-journal.mjs', import.meta.url).href;
const flags = fs.constants.O_RDONLY | fs.constants.O_DIRECTORY;
async function fixture(fn) {
  const dir = fs.mkdtempSync(os.tmpdir() + '/eva-project-journal-');
  fs.mkdirSync(dir + '/workspace', {mode: 0o700}); fs.mkdirSync(dir + '/journals', {mode: 0o700});
  const workspaceFd = fs.openSync(dir + '/workspace', flags), rootFd = fs.openSync(dir + '/journals', flags);
  const binary = dir + '/broker';
  const compiled = spawnSync('/usr/bin/x86_64-linux-gnu-gcc-13', ['-std=c17', '-O2', '-Wall', '-Wextra', '-Werror', '-o', binary, new URL('../native/workspace_writer_broker.c', import.meta.url).pathname]);
  assert.equal(compiled.status, 0, String(compiled.stderr));
  const run = (request, observe = false) => {
    const journalFd = resolveProjectJournal(rootFd, request.path, {observe});
    try { return runBrokerResult({executable: binary, rootFd: workspaceFd, journalFd, request, observe}); }
    finally { fs.closeSync(journalFd); }
  };
  try { await fn({dir, rootFd, workspaceFd, binary, run}); }
  finally { fs.closeSync(rootFd); fs.closeSync(workspaceFd); fs.rmSync(dir, {recursive: true, force: true}); }
}
const request = (key, generation = 0) => ({operation_id: (generation ? 'e' : 'd').repeat(64), request_binding_sha256: 'f'.repeat(64), expected_generation: generation, path: key + '-' + generation + '.txt', content: 'private artifact'});
test('three projects reuse the unchanged broker with independent generations and restart readback', () => fixture(({dir, rootFd, run}) => {
  for (const digit of ['1', '2', '3']) {
    const key = digit.repeat(64);
    for (const generation of [0, 1]) {
      const req = request(key, generation), result = run(req);
      assert.equal(result.status, 'receipt'); assert.equal(result.receipt.effect, 'created');
      const before = fs.readFileSync(dir + '/journals/' + key + '/workspace-writer.journal');
      assert.deepEqual(run(req, true), result);
      const script = `import fs from 'node:fs'; import {resolveProjectJournal} from ${JSON.stringify(resolver)}; const root=fs.openSync(process.argv[1],fs.constants.O_RDONLY|fs.constants.O_DIRECTORY); const child=resolveProjectJournal(root,process.argv[2],{observe:true}); fs.closeSync(child);fs.closeSync(root);`;
      assert.equal(spawnSync(process.execPath, ['--input-type=module', '-e', script, dir + '/journals', req.path]).status, 0);
      assert.deepEqual(fs.readFileSync(dir + '/journals/' + key + '/workspace-writer.journal'), before);
    }
    const changed = {...request(key), content: 'replacement'};
    assert.equal(run(changed).status, 'blocked');
    assert.equal(fs.readFileSync(dir + '/workspace/' + changed.path, 'utf8'), 'private artifact');
  }
  assert.equal(fs.readdirSync(dir + '/journals').length, 3);
}));
test('missing observe, partial initialization, symlinks and foreign child inode fail closed', () => fixture(({dir, rootFd}) => {
  const key = '4'.repeat(64), artifact = key + '-output.txt';
  assert.throws(() => resolveProjectJournal(rootFd, artifact, {observe:true}));
  assert.deepEqual(fs.readdirSync(dir + '/journals'), []);
  fs.mkdirSync(dir + '/journals/' + key, {mode:0o700});
  assert.throws(() => resolveProjectJournal(rootFd, artifact));
  assert.deepEqual(fs.readdirSync(dir + '/journals/' + key), []);
  fs.rmdirSync(dir + '/journals/' + key);
  fs.symlinkSync(dir + '/workspace', dir + '/journals/' + key);
  assert.throws(() => resolveProjectJournal(rootFd, artifact));
  assert.deepEqual(fs.readdirSync(dir + '/workspace'), []);
  fs.unlinkSync(dir + '/journals/' + key);
  fs.closeSync(resolveProjectJournal(rootFd, artifact));
  fs.renameSync(dir + '/journals/' + key, dir + '/journals/displaced');
  fs.mkdirSync(dir + '/journals/' + key, {mode:0o700});
  fs.copyFileSync(dir + '/journals/displaced/.project-identity.json', dir + '/journals/' + key + '/.project-identity.json');
  assert.throws(() => resolveProjectJournal(rootFd, artifact), /identity_mismatch/);
}));
test('concurrent first creation only returns a validated durable shared binding', () => fixture(async ({dir, rootFd}) => {
  const key='5'.repeat(64), artifact=key+'-output.txt';
  const script=`import fs from 'node:fs';import {resolveProjectJournal} from ${JSON.stringify(resolver)};let input='';process.stdin.on('data',x=>{input+=x;if(input.includes('go')){try{const root=fs.openSync(process.argv[1],fs.constants.O_RDONLY|fs.constants.O_DIRECTORY);const child=resolveProjectJournal(root,process.argv[2]);process.stdout.write(String(fs.fstatSync(child).ino));process.exit(0);}catch{process.exit(2);}}});`;
  const children=Array.from({length:6},()=>spawn(process.execPath,['--input-type=module','-e',script,dir+'/journals',artifact],{stdio:['pipe','pipe','pipe']}));
  const outputs=children.map(()=> '');children.forEach((child,i)=>child.stdout.on('data',data=>{outputs[i]+=data;}));
  const completions=children.map(child=>once(child,'exit'));children.forEach(child=>child.stdin.end('go'));
  const exits=await Promise.all(completions);assert.ok(exits.some(([code])=>code===0));
  exits.forEach(([code])=>assert.ok(code===0||code===2));
  const fd=resolveProjectJournal(rootFd,artifact,{observe:true});const inode=String(fs.fstatSync(fd).ino);fs.closeSync(fd);
  outputs.filter(Boolean).forEach(value=>assert.equal(value,inode));
  assert.deepEqual(fs.readdirSync(dir+'/journals/'+key),['.project-identity.json']);
}));
test('legacy fixed journal still uses the unchanged client directly', () => fixture(({dir, binary, workspaceFd, rootFd}) => {
  const req={...request('6'.repeat(64)),path:'legacy.txt'};
  const result=runBrokerResult({executable:binary,rootFd:workspaceFd,journalFd:rootFd,request:req});
  assert.equal(result.status,'receipt');assert.equal(result.receipt.effect,'created');
  assert.deepEqual(fs.readdirSync(dir+'/journals'),['workspace-writer.journal']);
}));
