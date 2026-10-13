import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import test, { after } from 'node:test';
import Database from 'better-sqlite3';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-workspace-generation-'));
process.env.EVA_DATA_DIR = dataDir;

const { db } = await import('../src/db.js');
const { config } = await import('../src/config.js');
const workspaceGenerationModule = await import('../src/mind/workspacegeneration.js');
const {
  createWorkspaceGenerationStore,
  OS_EXCLUSIVE_WRITER_UNAVAILABLE,
  __workspaceGenerationTest,
} = workspaceGenerationModule;
const { executeTool } = await import('../src/mind/tools.js');
const { canonicalJson } = await import('../src/util.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('een store claimt dirty vóór effect en finaliseert elke gewone uitkomst met een generatie', () => {
  const store = createWorkspaceGenerationStore(db);
  const target = path.join(config.workspaceDir, 'eerste.txt');
  const capability = store.issueWriteCapability({ target });
  let during;
  const result = store.runClaimedWrite(capability, () => {
    during = store.inspect();
    fs.writeFileSync(target, 'eerste\n', { flag: 'wx' });
    return { outcome: 'success', value: 'geschreven' };
  });

  assert.equal(during.dirty, true);
  assert.equal(typeof during.activeClaimId, 'string');
  assert.deepEqual(result, {
    outcome: 'success', value: 'geschreven', generation: 1,
    receiptSha256: result.receiptSha256,
  });
  assert.match(result.receiptSha256, /^[0-9a-f]{64}$/u);
  assert.deepEqual(store.inspect(), {
    contract: 'WorkspaceGenerationState/v1',
    lane: 'production',
    root: fs.realpathSync(config.workspaceDir),
    generation: 1,
    dirty: false,
    activeClaimId: null,
    lastOutcome: 'success',
  });
});

test('opaque capabilities zijn store-eigen, exact eenmalig en binden doel plus generatie', () => {
  const store = createWorkspaceGenerationStore(db);
  const sibling = createWorkspaceGenerationStore(db);
  const target = path.join(config.workspaceDir, 'tweede.txt');
  const capability = store.issueWriteCapability({ target });

  assert.throws(() => sibling.runClaimedWrite(capability, () => ({ outcome: 'noop' })),
    /opaque|capability/u);
  assert.throws(() => store.runClaimedWrite({}, () => ({ outcome: 'noop' })),
    /opaque|capability/u);
  assert.throws(() => store.runClaimedWrite(capability, () => ({ outcome: 'noop' }), {
    target: path.join(config.workspaceDir, 'vervalst.txt'),
  }), /doel|target/u);

  const acceptedCapability = store.issueWriteCapability({ target });
  const accepted = store.runClaimedWrite(acceptedCapability, () => ({ outcome: 'noop' }));
  assert.equal(accepted.generation, 2);
  assert.throws(() => store.runClaimedWrite(acceptedCapability, () => ({ outcome: 'noop' })),
    /gebruikt|one-shot|opaque/u);
});

test('echte synthetische schrijf_bestand create, overwrite, noop en exception verhogen ieder exact één keer', async () => {
  const store = createWorkspaceGenerationStore(db, { lane: 'synthetic' });
  const start = store.inspect().generation;
  const target = `test-artifacts/tool-generation-${Date.now()}/tool.txt`;
  const context = { mode: 'chat', messageContext: { syntheticTest: true } };

  const created = JSON.parse(await executeTool('schrijf_bestand', {
    pad: target, inhoud: 'een\n',
  }, context));
  assert.equal(created.ok, true);
  assert.equal(created.changed, true);
  assert.equal(created.data.workspace_generation, start + 1);
  assert.equal(fs.readFileSync(path.join(config.workspaceDir, target), 'utf8'), 'een\n');

  const overwritten = JSON.parse(await executeTool('schrijf_bestand', {
    pad: target, inhoud: 'twee\n',
  }, context));
  assert.equal(overwritten.ok, true);
  assert.equal(overwritten.changed, true);
  assert.equal(overwritten.data.workspace_generation, start + 2);

  const noop = JSON.parse(await executeTool('schrijf_bestand', {
    pad: target, inhoud: 'twee\n',
  }, context));
  assert.equal(noop.ok, true);
  assert.equal(noop.changed, false);
  assert.equal(noop.data.workspace_generation, start + 3);

  fs.writeFileSync(path.join(config.workspaceDir, path.posix.dirname(target), 'package.json'),
    'geen map\n');
  const failed = JSON.parse(await executeTool('schrijf_bestand', {
    pad: `${path.posix.dirname(target)}/package.json/kan-niet.txt`,
    inhoud: 'nooit\n',
  }, context));
  assert.equal(failed.ok, false);
  assert.equal(failed.effect_uncertain, true);
  assert.equal(fs.existsSync(path.join(
    config.workspaceDir, path.posix.dirname(target), 'package.json', 'kan-niet.txt',
  )), false);
  assert.equal(store.inspect().generation, start + 4);
  assert.equal(store.inspect().lastOutcome, 'uncertain');
});

test('de synthetische dirty claim omvat mkdir, temp-open, fsync, link/unlink, rename en chmod', async () => {
  const operations = [];
  const originals = new Map();
  const methods = ['mkdirSync', 'openSync', 'fsyncSync', 'linkSync', 'renameSync', 'unlinkSync', 'chmodSync'];
  for (const method of methods) {
    originals.set(method, fs[method]);
    fs[method] = function observed(...args) {
      const state = db.prepare(`SELECT dirty,active_claim_id FROM main.eva_workspace_generation_state_v1
        WHERE lane='synthetic'`).get();
      operations.push({ method, dirty: state?.dirty, active: state?.active_claim_id });
      return originals.get(method).apply(this, args);
    };
  }
  try {
    const create = JSON.parse(await executeTool('schrijf_bestand', {
      pad: 'test-artifacts/claimed/nested.txt', inhoud: 'create-only\n',
    }, { mode: 'chat', messageContext: { syntheticTest: true },
      artifactReceipts: [{ path: 'opaque-prior-receipt' }] }));
    assert.equal(create.ok, true, JSON.stringify(create));
    const overwrite = JSON.parse(await executeTool('schrijf_bestand', {
      pad: 'test-artifacts/claimed/nested.txt', inhoud: 'overwrite\n',
    }, { mode: 'chat', messageContext: { syntheticTest: true } }));
    assert.equal(overwrite.ok, true, JSON.stringify(overwrite));
  } finally {
    for (const [method, original] of originals) fs[method] = original;
  }
  for (const method of methods) {
    const seen = operations.filter((entry) => entry.method === method);
    assert.ok(seen.length > 0, `${method} is niet waargenomen`);
    assert.equal(seen.every((entry) => entry.dirty === 1 && typeof entry.active === 'string'), true,
      `${method} viel buiten de durable dirty claim: ${JSON.stringify(seen)}`);
  }
});

test('same-process concurrentie en recovery zonder dode owner blijven fail-closed', () => {
  const store = createWorkspaceGenerationStore(db);
  const first = store.issueWriteCapability({
    target: path.join(config.workspaceDir, 'generation', 'parallel-a.txt'),
  });
  const second = store.issueWriteCapability({
    target: path.join(config.workspaceDir, 'generation', 'parallel-b.txt'),
  });
  const result = store.runClaimedWrite(first, () => {
    const sibling = createWorkspaceGenerationStore(db);
    assert.equal(sibling.issueRecoveryCapability(), null,
      'dezelfde levende PID/starttijd mag geen recoverybewijs krijgen');
    assert.throws(() => store.runClaimedWrite(second, () => ({ outcome: 'noop' })),
      /active dirty|expected-generation/u);
    assert.throws(() => sibling.recover(Object.freeze({})), /opaque host verifier/u);
    return { outcome: 'noop' };
  });
  assert.equal(result.outcome, 'noop');
});

test('onvolledige /proc owneridentiteit weigert vóór claim, dirty en effect', () => {
  const store = createWorkspaceGenerationStore(db);
  const before = store.inspect();
  const claimCount = db.prepare(`SELECT COUNT(*) AS count
    FROM main.eva_workspace_generation_claims_v1 WHERE lane='production'`).get().count;
  const target = path.join(config.workspaceDir, 'generation', 'owner-unavailable.txt');
  const capability = store.issueWriteCapability({ target });
  const originalRead = fs.readFileSync;
  let effectCalls = 0;
  fs.readFileSync = function failedOwnerRead(itemPath, ...rest) {
    if (String(itemPath) === '/proc/sys/kernel/random/boot_id') {
      throw Object.assign(new Error('boot id unavailable'), { code: 'EIO' });
    }
    return originalRead.call(fs, itemPath, ...rest);
  };
  try {
    assert.throws(() => store.runClaimedWrite(capability, () => {
      effectCalls += 1;
      return { outcome: 'success' };
    }), /owner identity is unavailable/u);
  } finally { fs.readFileSync = originalRead; }
  assert.equal(effectCalls, 0);
  assert.deepEqual(store.inspect(), before);
  assert.equal(db.prepare(`SELECT COUNT(*) AS count
    FROM main.eva_workspace_generation_claims_v1 WHERE lane='production'`).get().count,
  claimCount);
  assert.equal(fs.existsSync(target), false);
});

test('failure is een expliciete immutable uitkomst; een gevangen exception wordt uncertain', () => {
  const store = createWorkspaceGenerationStore(db);
  const before = store.inspect().generation;
  const failure = store.runClaimedWrite(store.issueWriteCapability({
    target: path.join(config.workspaceDir, 'generation', 'failure.txt'),
  }), () => ({ outcome: 'failure', value: 'geweigerd' }));
  assert.equal(failure.generation, before + 1);
  assert.equal(store.inspect().lastOutcome, 'failure');

  assert.throws(() => store.runClaimedWrite(store.issueWriteCapability({
    target: path.join(config.workspaceDir, 'generation', 'uncertain.txt'),
  }), () => { throw Object.assign(new Error('effect stopte'), { code: 'EFFECT_STOP' }); }),
  /effect stopte/u);
  assert.equal(store.inspect().generation, before + 2);
  assert.equal(store.inspect().lastOutcome, 'uncertain');
  assert.throws(() => db.prepare(`UPDATE main.eva_workspace_generation_outcomes_v1
    SET outcome='success' WHERE lane='production' AND generation_after=?`).run(before + 2),
  /immutable/u);
});

test('TEMP shadows, UPDATE/DELETE en OR REPLACE worden exact geweigerd', () => {
  const store = createWorkspaceGenerationStore(db);
  const before = store.inspect();
  db.exec('CREATE TEMP TABLE eva_workspace_generation_state_v1(fake TEXT)');
  assert.throws(() => store.inspect(), /TEMP schema shadow/u);
  db.exec('DROP TABLE temp.eva_workspace_generation_state_v1');

  db.exec('CREATE TEMP TABLE workspace_generation_shadow(x INTEGER)');
  db.exec(`CREATE TEMP TRIGGER eva_workspace_generation_claims_v1_no_delete
    BEFORE DELETE ON workspace_generation_shadow BEGIN SELECT RAISE(ABORT,'fake'); END`);
  assert.throws(() => store.inspect(), /TEMP schema shadow/u);
  db.exec('DROP TRIGGER temp.eva_workspace_generation_claims_v1_no_delete');
  db.exec('DROP TABLE temp.workspace_generation_shadow');

  assert.throws(() => db.exec(`INSERT OR REPLACE INTO main.eva_workspace_generation_state_v1
    SELECT * FROM main.eva_workspace_generation_state_v1 WHERE lane='production'`),
  /cannot be replaced/u);
  assert.throws(() => db.exec(`UPDATE main.eva_workspace_generation_state_v1
    SET root_path=root_path WHERE lane='production'`), /invalid .* transition/u);
  assert.throws(() => db.exec(`DELETE FROM main.eva_workspace_generation_state_v1
    WHERE lane='production'`), /cannot be deleted/u);
  assert.throws(() => db.exec(`INSERT OR REPLACE INTO main.eva_workspace_generation_claims_v1
    SELECT * FROM main.eva_workspace_generation_claims_v1
    WHERE lane='production' ORDER BY generation_before DESC LIMIT 1`), /replacement/u);
  assert.throws(() => db.exec(`INSERT OR REPLACE INTO main.eva_workspace_generation_outcomes_v1
    SELECT * FROM main.eva_workspace_generation_outcomes_v1
    WHERE lane='production' ORDER BY generation_after DESC LIMIT 1`), /replacement/u);
  assert.deepEqual(store.inspect(), before);
});

function xinfoCounterfeit(database, transform) {
  return {
    get inTransaction() { return database.inTransaction; },
    exec(sql) { return database.exec(sql); },
    prepare(sql) {
      const statement = database.prepare(sql);
      if (!/^PRAGMA main\.index_xinfo\(/u.test(String(sql))) return statement;
      return {
        all(...args) { return transform(statement.all(...args), String(sql)); },
      };
    },
  };
}

test('volledige autoindex-xinfo weigert counterfeit collation, DESC en extra index', () => {
  const store = createWorkspaceGenerationStore(db);
  assert.equal(store.inspect().dirty, false);
  for (const [label, transform] of [
    ['collation', (rows) => rows.map((row, index) => (
      index === 0 ? { ...row, coll: 'NOCASE' } : row
    ))],
    ['descending', (rows) => rows.map((row, index) => (
      index === 0 ? { ...row, desc: 1 } : row
    ))],
    ['auxiliary-row', (rows) => rows.filter((row) => Number(row.key) === 1)],
  ]) {
    const counterfeit = xinfoCounterfeit(db, transform);
    assert.throws(() => createWorkspaceGenerationStore(counterfeit),
      /autoindex xinfo changed/u, label);
  }

  db.exec(`CREATE INDEX eva_workspace_generation_extra_index
    ON eva_workspace_generation_state_v1(last_outcome)`);
  try { assert.throws(() => store.inspect(), /schema object set changed/u); } finally {
    db.exec('DROP INDEX eva_workspace_generation_extra_index');
  }
  assert.equal(store.inspect().dirty, false);
});

function mutateThenRestore(table, trigger, column, value, order = 'DESC') {
  const row = db.prepare(`SELECT rowid AS row_id,* FROM main.${table}
    WHERE lane='production' ORDER BY rowid ${order} LIMIT 1`).get();
  const triggerSql = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(trigger).sql;
  db.exec(`DROP TRIGGER main.${trigger}`);
  db.prepare(`UPDATE main.${table} SET ${column}=? WHERE rowid=?`).run(value, row.row_id);
  db.exec(triggerSql);
  return () => {
    db.exec(`DROP TRIGGER main.${trigger}`);
    db.prepare(`UPDATE main.${table} SET ${column}=? WHERE rowid=?`).run(row[column], row.row_id);
    db.exec(triggerSql);
  };
}

test('inspect herverifieert zelfstandig claim- en outcome-bodies na exacte schemaherstel', () => {
  const store = createWorkspaceGenerationStore(db);
  const restoreOutcome = mutateThenRestore(
    'eva_workspace_generation_outcomes_v1',
    'eva_workspace_generation_outcomes_v1_no_update',
    'receipt_sha256', '0'.repeat(64),
  );
  try { assert.throws(() => store.inspect(), /outcome body hash mismatch/u); } finally { restoreOutcome(); }
  assert.equal(store.inspect().dirty, false);

  const restoreClaim = mutateThenRestore(
    'eva_workspace_generation_claims_v1',
    'eva_workspace_generation_claims_v1_no_update',
    'request_sha256', 'f'.repeat(64),
  );
  try { assert.throws(() => store.inspect(), /claim body hash mismatch/u); } finally { restoreClaim(); }
  assert.equal(store.inspect().dirty, false);

  const restoreHistorical = mutateThenRestore(
    'eva_workspace_generation_outcomes_v1',
    'eva_workspace_generation_outcomes_v1_no_update',
    'detail_sha256', 'a'.repeat(64), 'ASC',
  );
  try { assert.throws(() => store.inspect(), /outcome body hash mismatch/u); } finally {
    restoreHistorical();
  }
  assert.equal(store.inspect().dirty, false);
});

test('verkeerde root en counterfeit database krijgen geen productieauthority', () => {
  const store = createWorkspaceGenerationStore(db);
  assert.throws(() => store.issueWriteCapability({
    target: path.join(config.workspaceDir, 'generation', 'stale.txt'),
    expectedGeneration: store.inspect().generation - 1,
  }), /expected-generation mismatch/u);
  assert.throws(() => createWorkspaceGenerationStore(db, {
    workspaceRoot: path.join(config.workspaceDir, 'generation'),
  }), /non-configured root/u);
  const counterfeitDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-generation-counterfeit-'));
  const counterfeit = new Database(path.join(counterfeitDir, 'eva.db'));
  try {
    assert.throws(() => createWorkspaceGenerationStore(counterfeit),
      /configured production database/u);
  } finally {
    counterfeit.close();
    fs.rmSync(counterfeitDir, { recursive: true, force: true });
  }
});

test('synthetische writes gebruiken een aparte lane zonder productieauthority', async () => {
  const production = createWorkspaceGenerationStore(db);
  const synthetic = createWorkspaceGenerationStore(db, { lane: 'synthetic' });
  const productionBefore = production.inspect().generation;
  const syntheticBefore = synthetic.inspect().generation;
  const target = `test-artifacts/generation-${Date.now()}/bewijs.txt`;
  const result = JSON.parse(await executeTool('schrijf_bestand', {
    pad: target, inhoud: 'alleen test\n',
  }, { mode: 'chat', messageContext: { syntheticTest: true } }));
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.data.workspace_generation_lane, 'synthetic');
  assert.equal(production.inspect().generation, productionBefore);
  assert.equal(synthetic.inspect().generation, syntheticBefore + 1);
  assert.throws(() => production.issueWriteCapability({
    target: path.join(config.workspaceDir, target),
  }), /no production generation authority/u);
});

const projectRoot = process.cwd();
function processDataDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}
function childEnvironment(childDataDir) {
  return {
    ...process.env,
    EVA_DATA_DIR: childDataDir,
    OPENAI_API_KEY: '',
    TELEGRAM_BOT_TOKEN: '',
    TMPDIR: '/tmp',
  };
}
function runGenerationChild(childDataDir, body, options = {}) {
  const source = `
    import fs from 'node:fs';
    import path from 'node:path';
    const {db}=await import('./src/db.js');
    const {config}=await import('./src/config.js');
    const {createWorkspaceGenerationStore}=await import('./src/mind/workspacegeneration.js');
    ${body}
  `;
  return spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: projectRoot,
    env: childEnvironment(childDataDir),
    encoding: 'utf8',
    timeout: options.timeout || 10_000,
  });
}
function childJson(result) {
  assert.equal(result.status, 0, `child status=${result.status}\nstdout=${result.stdout}\nstderr=${result.stderr}`);
  const lines = String(result.stdout || '').trim().split(/\r?\n/u).filter(Boolean);
  return JSON.parse(lines.at(-1));
}

for (const crashPoint of ['before-filesystem-effect', 'during-filesystem-effect']) {
  test(`procescrash ${crashPoint} laat dirty duurzaam staan en vereist expliciet herstel`, () => {
    const childDataDir = processDataDir(`bottie-generation-${crashPoint}-`);
    try {
      const crashed = runGenerationChild(childDataDir, `
        const store=createWorkspaceGenerationStore(db);
        const target=path.join(config.workspaceDir,'crash.txt');
        const capability=store.issueWriteCapability({target});
        store.runClaimedWrite(capability,()=>{
          ${crashPoint === 'during-filesystem-effect'
            ? "fs.writeFileSync(target,'mogelijk effect\\n',{flag:'wx'});" : ''}
          process.exit(73);
        });
      `);
      assert.equal(crashed.status, 73, crashed.stderr);

      const dirty = childJson(runGenerationChild(childDataDir, `
        const store=createWorkspaceGenerationStore(db);
        console.log(JSON.stringify(store.inspect()));
        db.close();
      `));
      assert.equal(dirty.dirty, true);
      assert.equal(dirty.generation, 0);
      assert.equal(typeof dirty.activeClaimId, 'string');

      const denied = childJson(runGenerationChild(childDataDir, `
        const store=createWorkspaceGenerationStore(db);
        let error=null;
        try { store.issueWriteCapability({target:path.join(config.workspaceDir,'later.txt')}); }
        catch (caught) { error=String(caught.message); }
        console.log(JSON.stringify({error,state:store.inspect()}));
        db.close();
      `));
      assert.match(denied.error, /active dirty/u);
      assert.equal(denied.state.dirty, true, 'restart/tijd mag de claim niet automatisch overnemen');

      const recovered = childJson(runGenerationChild(childDataDir, `
        const store=createWorkspaceGenerationStore(db);
        const forged={};
        let forgedError=null;
        try { store.recover(forged); } catch (caught) { forgedError=String(caught.message); }
        const verifier=store.issueRecoveryCapability();
        if (!verifier) throw new Error('dode owner kreeg geen host verifier');
        const recovery=store.recover(verifier);
        const next=store.runClaimedWrite(store.issueWriteCapability({
          target:path.join(config.workspaceDir,'later.txt'),
        }),()=>({outcome:'noop'}));
        console.log(JSON.stringify({forgedError,recovery,next,state:store.inspect()}));
        db.close();
      `));
      assert.match(recovered.forgedError, /opaque host verifier/u);
      assert.equal(recovered.recovery.outcome, 'uncertain');
      assert.match(recovered.recovery.observationSha256, /^[0-9a-f]{64}$/u);
      assert.equal(recovered.recovery.generation, 1);
      assert.equal(recovered.next.generation, 2);
      assert.equal(recovered.state.dirty, false);
      assert.equal(recovered.state.generation, 2);
    } finally {
      fs.rmSync(childDataDir, { recursive: true, force: true });
    }
  });
}

function waitForOutput(child, marker) {
  return new Promise((resolve, reject) => {
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => reject(new Error(
      `child output timeout; stdout=${stdout}; stderr=${stderr}`,
    )), 5_000);
    child.stdout.on('data', (chunk) => {
      stdout += String(chunk);
      if (!stdout.includes(marker)) return;
      clearTimeout(timer);
      resolve({ stdout, stderr });
    });
    child.stderr.on('data', (chunk) => { stderr += String(chunk); });
    child.once('exit', (code, signal) => {
      if (stdout.includes(marker)) return;
      clearTimeout(timer);
      reject(new Error(`child exited before marker: code=${code} signal=${signal}; ${stderr}`));
    });
  });
}

test('twee processen kunnen niet tegelijk dezelfde generation claimen', async () => {
  const childDataDir = processDataDir('bottie-generation-two-process-');
  let owner = null;
  try {
    const source = `
      import fs from 'node:fs';
      import path from 'node:path';
      const {db}=await import('./src/db.js');
      const {config}=await import('./src/config.js');
      const {createWorkspaceGenerationStore}=await import('./src/mind/workspacegeneration.js');
      const store=createWorkspaceGenerationStore(db);
      const target=path.join(config.workspaceDir,'owner.txt');
      store.runClaimedWrite(store.issueWriteCapability({target}),()=>{
        fs.writeFileSync(target,'owner-effect\\n',{flag:'wx'});
        process.stdout.write('CLAIMED\\n');
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,30000);
        return {outcome:'success'};
      });
    `;
    owner = spawn(process.execPath, ['--input-type=module', '-e', source], {
      cwd: projectRoot,
      env: childEnvironment(childDataDir),
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    await waitForOutput(owner, 'CLAIMED');

    const contender = childJson(runGenerationChild(childDataDir, `
      const store=createWorkspaceGenerationStore(db);
      let error=null;
      try { store.issueWriteCapability({target:path.join(config.workspaceDir,'contender.txt')}); }
      catch (caught) { error=String(caught.message); }
      console.log(JSON.stringify({error,state:store.inspect()}));
      db.close();
    `));
    assert.match(contender.error, /active dirty/u);
    assert.equal(contender.state.generation, 0);
    assert.equal(contender.state.dirty, true);

    const exited = new Promise((resolve) => owner.once('exit', resolve));
    owner.kill('SIGKILL');
    await exited;
    owner = null;
    const recovered = childJson(runGenerationChild(childDataDir, `
      const store=createWorkspaceGenerationStore(db);
      const verifier=store.issueRecoveryCapability();
      if (!verifier) throw new Error('missing verifier after SIGKILL');
      const result=store.recover(verifier);
      console.log(JSON.stringify({result,state:store.inspect()}));
      db.close();
    `));
    assert.equal(recovered.result.generation, 1);
    assert.equal(recovered.state.dirty, false);
  } finally {
    if (owner) owner.kill('SIGKILL');
    fs.rmSync(childDataDir, { recursive: true, force: true });
  }
});

test('restart plus VACUUM bewaart de generatie en gebruikt haar nooit opnieuw', () => {
  const childDataDir = processDataDir('bottie-generation-vacuum-');
  try {
    const first = childJson(runGenerationChild(childDataDir, `
      const store=createWorkspaceGenerationStore(db);
      const result=store.runClaimedWrite(store.issueWriteCapability({
        target:path.join(config.workspaceDir,'vacuum.txt'),
      }),()=>({outcome:'noop'}));
      console.log(JSON.stringify({result,state:store.inspect()}));
      db.close();
    `));
    assert.equal(first.result.generation, 1);
    const vacuumDb = new Database(path.join(childDataDir, 'eva.db'));
    vacuumDb.exec('VACUUM');
    vacuumDb.close();
    const second = childJson(runGenerationChild(childDataDir, `
      const store=createWorkspaceGenerationStore(db);
      const before=store.inspect();
      const result=store.runClaimedWrite(store.issueWriteCapability({
        target:path.join(config.workspaceDir,'vacuum.txt'),expectedGeneration:before.generation,
      }),()=>({outcome:'noop'}));
      console.log(JSON.stringify({before,result,state:store.inspect()}));
      db.close();
    `));
    assert.equal(second.before.generation, 1);
    assert.equal(second.result.generation, 2);
    assert.equal(second.state.generation, 2);
  } finally {
    fs.rmSync(childDataDir, { recursive: true, force: true });
  }
});

test('een gedeeltelijk bestaand schema wordt bij restart niet stil gerepareerd', () => {
  const childDataDir = processDataDir('bottie-generation-partial-schema-');
  try {
    const initialized = runGenerationChild(childDataDir, `
      createWorkspaceGenerationStore(db);
      db.close();
    `);
    assert.equal(initialized.status, 0, initialized.stderr);
    const raw = new Database(path.join(childDataDir, 'eva.db'));
    raw.exec('DROP TRIGGER eva_workspace_generation_claims_v1_no_delete');
    raw.close();
    const restarted = runGenerationChild(childDataDir, `
      createWorkspaceGenerationStore(db);
      db.close();
    `);
    assert.notEqual(restarted.status, 0);
    assert.match(restarted.stderr, /schema object set changed|DDL changed/u);
  } finally {
    fs.rmSync(childDataDir, { recursive: true, force: true });
  }
});

test('schrijf_bestand faalt vóór filesystemmutatie als geen coordinator kan initialiseren', () => {
  const childDataDir = processDataDir('bottie-generation-no-coordinator-');
  try {
    fs.mkdirSync(childDataDir, { recursive: true });
    const raw = new Database(path.join(childDataDir, 'eva.db'));
    raw.exec('CREATE TABLE eva_workspace_generation_state_v1(fake TEXT)');
    raw.close();
    const source = `
      import fs from 'node:fs';
      import path from 'node:path';
      const {config}=await import('./src/config.js');
      const {db}=await import('./src/db.js');
      const {executeTool}=await import('./src/mind/tools.js');
      const target=path.join(config.workspaceDir,'must-not-exist.txt');
      const result=JSON.parse(await executeTool('schrijf_bestand',{
        pad:'must-not-exist.txt',inhoud:'nooit\\n',
      },{mode:'chat'}));
      console.log(JSON.stringify({result,exists:fs.existsSync(target)}));
      db.close();
    `;
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
      cwd: projectRoot, env: childEnvironment(childDataDir), encoding: 'utf8', timeout: 10_000,
    });
    const parsed = childJson(result);
    assert.equal(parsed.result.ok, false);
    assert.match(parsed.result.error, /coordinator.*niet beschikbaar/u);
    assert.equal(parsed.exists, false);
  } finally {
    fs.rmSync(childDataDir, { recursive: true, force: true });
  }
});

test('recovery regular-naar-FIFO race blokkeert niet en levert geen verifier', () => {
  const childDataDir = processDataDir('bottie-generation-fifo-race-');
  try {
    const prepared = runGenerationChild(childDataDir, `
      fs.writeFileSync(path.join(config.workspaceDir,'swap.txt'),'regular\\n');
      const store=createWorkspaceGenerationStore(db);
      store.runClaimedWrite(store.issueWriteCapability({
        target:path.join(config.workspaceDir,'claim.txt'),
      }),()=>process.exit(91));
    `);
    assert.equal(prepared.status, 91, prepared.stderr);
    const raced = childJson(runGenerationChild(childDataDir, `
      const {spawnSync}=await import('node:child_process');
      const store=createWorkspaceGenerationStore(db);
      const target=path.join(config.workspaceDir,'swap.txt');
      const originalOpen=fs.openSync;
      let swapped=false;
      fs.openSync=function(itemPath,flags,...rest){
        if (!swapped && String(itemPath).startsWith('/proc/self/fd/')
          && String(itemPath).endsWith('/swap.txt')) {
          swapped=true;
          fs.unlinkSync(target);
          const made=spawnSync('mkfifo',[target],{encoding:'utf8'});
          if (made.status!==0) throw new Error(made.stderr);
        }
        return originalOpen.call(fs,itemPath,flags,...rest);
      };
      const started=Date.now();
      const verifier=store.issueRecoveryCapability();
      const elapsed=Date.now()-started;
      fs.openSync=originalOpen;
      console.log(JSON.stringify({swapped,elapsed,verifier:verifier!==null,state:store.inspect()}));
      db.close();
    `));
    assert.equal(raced.swapped, true);
    assert.equal(raced.verifier, false);
    assert.ok(raced.elapsed < 1_000, `O_NONBLOCK race duurde ${raced.elapsed}ms`);
    assert.equal(raced.state.dirty, true);

    fs.unlinkSync(path.join(childDataDir, 'workspace', 'swap.txt'));
    fs.writeFileSync(path.join(childDataDir, 'workspace', 'swap.txt'), 'regular-restored\n');
    const recovered = childJson(runGenerationChild(childDataDir, `
      const store=createWorkspaceGenerationStore(db);
      const verifier=store.issueRecoveryCapability();
      if (!verifier) throw new Error('restored scan produced no verifier');
      console.log(JSON.stringify({recovery:store.recover(verifier),state:store.inspect()}));
      db.close();
    `));
    assert.equal(recovered.recovery.generation, 1);
    assert.equal(recovered.state.dirty, false);
  } finally {
    fs.rmSync(childDataDir, { recursive: true, force: true });
  }
});

test('recovery directory-naar-buitensymlink race leest niet buiten de workspace', () => {
  const childDataDir = processDataDir('bottie-generation-dir-race-');
  const workspace = path.join(childDataDir, 'workspace');
  const outside = path.join(childDataDir, 'outside-secret');
  try {
    const prepared = runGenerationChild(childDataDir, `
      fs.mkdirSync(path.join(config.workspaceDir,'swapdir'));
      fs.writeFileSync(path.join(config.workspaceDir,'swapdir','inside.txt'),'inside\\n');
      fs.mkdirSync(path.join(config.dataDir,'outside-secret'));
      fs.writeFileSync(path.join(config.dataDir,'outside-secret','secret.txt'),'must-not-be-read\\n');
      const store=createWorkspaceGenerationStore(db);
      store.runClaimedWrite(store.issueWriteCapability({
        target:path.join(config.workspaceDir,'claim.txt'),
      }),()=>process.exit(92));
    `);
    assert.equal(prepared.status, 92, prepared.stderr);
    const raced = childJson(runGenerationChild(childDataDir, `
      const store=createWorkspaceGenerationStore(db);
      const target=path.join(config.workspaceDir,'swapdir');
      const moved=path.join(config.workspaceDir,'swapdir.moved');
      const outside=path.join(config.dataDir,'outside-secret');
      const originalOpen=fs.openSync;
      let swapped=false;
      let outsideOpened=false;
      fs.openSync=function(itemPath,flags,...rest){
        const value=String(itemPath);
        if (!swapped && value.startsWith('/proc/self/fd/') && value.endsWith('/swapdir')) {
          swapped=true;
          fs.renameSync(target,moved);
          fs.symlinkSync(outside,target);
        } else if (swapped && value.startsWith('/proc/self/fd/')
          && value.endsWith('/secret.txt')) outsideOpened=true;
        return originalOpen.call(fs,itemPath,flags,...rest);
      };
      const verifier=store.issueRecoveryCapability();
      fs.openSync=originalOpen;
      console.log(JSON.stringify({swapped,outsideOpened,verifier:verifier!==null,state:store.inspect()}));
      db.close();
    `));
    assert.equal(raced.swapped, true);
    assert.equal(raced.outsideOpened, false);
    assert.equal(raced.verifier, false);
    assert.equal(raced.state.dirty, true);

    fs.unlinkSync(path.join(workspace, 'swapdir'));
    fs.renameSync(path.join(workspace, 'swapdir.moved'), path.join(workspace, 'swapdir'));
    assert.equal(fs.readFileSync(path.join(outside, 'secret.txt'), 'utf8'), 'must-not-be-read\n');
    const recovered = childJson(runGenerationChild(childDataDir, `
      const store=createWorkspaceGenerationStore(db);
      const verifier=store.issueRecoveryCapability();
      if (!verifier) throw new Error('restored scan produced no verifier');
      console.log(JSON.stringify({recovery:store.recover(verifier),state:store.inspect()}));
      db.close();
    `));
    assert.equal(recovered.recovery.generation, 1);
    assert.equal(recovered.state.dirty, false);
  } finally {
    fs.rmSync(childDataDir, { recursive: true, force: true });
  }
});

test('recoverycapability herverifieert dezelfde volledige observatie vlak voor finalisatie', () => {
  const childDataDir = processDataDir('bottie-generation-recovery-rebind-');
  try {
    const prepared = runGenerationChild(childDataDir, `
      fs.writeFileSync(path.join(config.workspaceDir,'observed.txt'),'voor\\n');
      const store=createWorkspaceGenerationStore(db);
      store.runClaimedWrite(store.issueWriteCapability({
        target:path.join(config.workspaceDir,'claim.txt'),
      }),()=>process.exit(93));
    `);
    assert.equal(prepared.status, 93, prepared.stderr);
    const changed = childJson(runGenerationChild(childDataDir, `
      const store=createWorkspaceGenerationStore(db);
      const verifier=store.issueRecoveryCapability();
      if (!verifier) throw new Error('missing first verifier');
      fs.writeFileSync(path.join(config.workspaceDir,'observed.txt'),'na verifier\\n');
      let error=null;
      try { store.recover(verifier); } catch (caught) { error=String(caught.message); }
      console.log(JSON.stringify({error,state:store.inspect()}));
      db.close();
    `));
    assert.match(changed.error, /observation changed/u);
    assert.equal(changed.state.dirty, true);
    assert.equal(changed.state.generation, 0);

    const rebound = childJson(runGenerationChild(childDataDir, `
      const store=createWorkspaceGenerationStore(db);
      const verifier=store.issueRecoveryCapability();
      if (!verifier) throw new Error('missing rebound verifier');
      console.log(JSON.stringify({recovery:store.recover(verifier),state:store.inspect()}));
      db.close();
    `));
    assert.equal(rebound.recovery.generation, 1);
    assert.equal(rebound.state.dirty, false);
  } finally {
    fs.rmSync(childDataDir, { recursive: true, force: true });
  }
});

test('production schrijf_bestand weigert zonder OS-exclusieve writer vóór een parent-rebind', async () => {
  const productionStore = createWorkspaceGenerationStore(db);
  const generationBefore = productionStore.inspect().generation;
  const parent = path.join(config.workspaceDir, `os-boundary-${Date.now()}`);
  const moved = path.join(dataDir, `moved-parent-${Date.now()}`);
  const outside = path.join(dataDir, `outside-parent-${Date.now()}`);
  fs.mkdirSync(parent, { recursive: true });
  fs.mkdirSync(outside, { recursive: true });
  const target = path.join(parent, 'resultaat.txt');
  const originalOpen = fs.openSync;
  let swapped = false;
  fs.openSync = function swapBeforeTempOpen(itemPath, flags, ...rest) {
    if (!swapped && String(itemPath).startsWith(parent)
      && String(itemPath).includes('.eva-tmp-')) {
      swapped = true;
      fs.renameSync(parent, moved);
      fs.symlinkSync(outside, parent);
    }
    return originalOpen.call(fs, itemPath, flags, ...rest);
  };
  let result;
  try {
    result = JSON.parse(await executeTool('schrijf_bestand', {
      pad: path.relative(config.workspaceDir, target), inhoud: 'mag nergens landen\n',
    }, { mode: 'chat' }));
  } finally {
    fs.openSync = originalOpen;
  }
  const outsideExists = fs.existsSync(path.join(outside, 'resultaat.txt'));
  const movedExists = fs.existsSync(path.join(moved, 'resultaat.txt'));
  if (fs.existsSync(parent) && fs.lstatSync(parent).isSymbolicLink()) fs.unlinkSync(parent);
  fs.rmSync(parent, { recursive: true, force: true });
  fs.rmSync(moved, { recursive: true, force: true });
  fs.rmSync(outside, { recursive: true, force: true });
  assert.equal(result.ok, false);
  assert.match(result.error, /os_exclusive_writer_unavailable/u);
  assert.equal(result.error_code, 'os_exclusive_writer_unavailable');
  assert.equal(result.failure_class, 'unavailable');
  assert.equal(result.effect_uncertain, false);
  assert.equal(result.retryable, false);
  assert.equal(productionStore.inspect().generation, generationBefore);
  assert.equal(swapped, false, 'zonder broker wordt geen filesystemstap bereikt');
  assert.equal(outsideExists, false);
  assert.equal(movedExists, false);
});

for (const race of ['existing-fifo', 'regular-to-fifo']) {
  test(`schrijf_bestand ${race} blokkeert nooit op een target`, () => {
    const childDataDir = processDataDir(`bottie-generation-${race}-`);
    try {
      const source = `
        import fs from 'node:fs';
        import path from 'node:path';
        import {spawnSync} from 'node:child_process';
        const {db}=await import('./src/db.js');
        const {config}=await import('./src/config.js');
        const target=path.join(config.workspaceDir,'test-artifacts','fifo-target.txt');
        fs.mkdirSync(path.dirname(target),{recursive:true});
        ${race === 'existing-fifo'
          ? "{ const made=spawnSync('mkfifo',[target],{encoding:'utf8'}); if(made.status!==0) throw new Error(made.stderr); }"
          : "fs.writeFileSync(target,'regular\\n');"}
        const {executeTool}=await import('./src/mind/tools.js');
        ${race === 'regular-to-fifo' ? `
          const originalOpen=fs.openSync;
          let swapped=false;
          fs.openSync=function(itemPath,flags,...rest){
            if (!swapped && path.resolve(String(itemPath))===target) {
              swapped=true;
              fs.unlinkSync(target);
              const made=spawnSync('mkfifo',[target],{encoding:'utf8'});
              if(made.status!==0) throw new Error(made.stderr);
            }
            return originalOpen.call(fs,itemPath,flags,...rest);
          };
        ` : ''}
        const started=Date.now();
        const result=JSON.parse(await executeTool('schrijf_bestand',{
          pad:'test-artifacts/fifo-target.txt',inhoud:'nieuw\\n',
        },{mode:'chat',messageContext:{syntheticTest:true}}));
        console.log(JSON.stringify({result,elapsed:Date.now()-started}));
        db.close();
      `;
      const run = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
        cwd: projectRoot, env: childEnvironment(childDataDir), encoding: 'utf8', timeout: 1_500,
      });
      const parsed = childJson(run);
      assert.equal(parsed.result.ok, false);
      assert.match(parsed.result.error, /regulier bestand|regular file/u);
      assert.equal(parsed.result.workspace_generation.lane, 'synthetic');
      assert.equal(parsed.result.workspace_generation.outcome, 'uncertain');
      assert.ok(parsed.elapsed < 1_000, `targetcontrole duurde ${parsed.elapsed}ms`);
    } finally {
      fs.rmSync(childDataDir, { recursive: true, force: true });
    }
  });
}

test('runClaimedWrite accepteert alleen een exact synchroon plain resultaat', () => {
  const store = createWorkspaceGenerationStore(db);
  const target = (name) => path.join(config.workspaceDir, 'generation', name);
  const beforeAsync = store.inspect();
  let asyncCalls = 0;
  assert.throws(() => store.runClaimedWrite(store.issueWriteCapability({
    target: target('async-function.txt'),
  }), async () => {
    asyncCalls += 1;
    return { outcome: 'noop' };
  }), /exact synchronous plain result/u);
  assert.equal(asyncCalls, 0);
  assert.deepEqual(store.inspect(), beforeAsync);
  const rejected = [
    ['Promise', () => Promise.resolve({ outcome: 'noop' })],
    ['thenable', () => ({ outcome: 'noop', then() {} })],
    ['extra key', () => ({ outcome: 'noop', extra: true })],
    ['object value', () => ({ outcome: 'noop', value: { mutable: true } })],
    ['null prototype', () => Object.assign(Object.create(null), { outcome: 'noop' })],
  ];
  for (const [label, effect] of rejected) {
    const before = store.inspect().generation;
    let error;
    try {
      store.runClaimedWrite(store.issueWriteCapability({ target: target(`${label}.txt`) }), effect);
    } catch (caught) { error = caught; }
    assert.ok(error instanceof Error, `${label} werd niet geweigerd`);
    assert.match(error.message, /exact synchronous plain result/u, label);
    assert.deepEqual(error.workspaceGeneration, {
      contract: 'WorkspaceGenerationFailure/v1', lane: 'production',
      generation: before + 1, outcome: 'uncertain',
      receiptSha256: error.workspaceGeneration.receiptSha256,
    });
    assert.match(error.workspaceGeneration.receiptSha256, /^[0-9a-f]{64}$/u);
  }
});

test('accessors worden niet uitgevoerd en Proxy-normalisatie raakt nooit een schone generatie', () => {
  const store = createWorkspaceGenerationStore(db);
  let getterCalls = 0;
  const accessor = {};
  Object.defineProperty(accessor, 'outcome', {
    enumerable: true,
    get() { getterCalls += 1; return 'noop'; },
  });
  assert.throws(() => store.runClaimedWrite(store.issueWriteCapability({
    target: path.join(config.workspaceDir, 'generation', 'getter.txt'),
  }), () => accessor), /exact synchronous plain result/u);
  assert.equal(getterCalls, 0, 'resultaataccessors mogen nooit worden uitgevoerd');

  const dirtyObservations = [];
  const proxy = new Proxy({ outcome: 'noop', value: 'genormaliseerd' }, {
    getPrototypeOf(target) {
      dirtyObservations.push(store.inspect().dirty);
      return Reflect.getPrototypeOf(target);
    },
    ownKeys(target) {
      dirtyObservations.push(store.inspect().dirty);
      return Reflect.ownKeys(target);
    },
    getOwnPropertyDescriptor(target, property) {
      dirtyObservations.push(store.inspect().dirty);
      return Reflect.getOwnPropertyDescriptor(target, property);
    },
    get(target, property, receiver) {
      dirtyObservations.push(store.inspect().dirty);
      return Reflect.get(target, property, receiver);
    },
  });
  const accepted = store.runClaimedWrite(store.issueWriteCapability({
    target: path.join(config.workspaceDir, 'generation', 'proxy.txt'),
  }), () => proxy);
  assert.deepEqual(accepted, {
    outcome: 'noop', value: 'genormaliseerd', generation: accepted.generation,
    receiptSha256: accepted.receiptSha256,
  });
  assert.equal(dirtyObservations.length > 0, true);
  assert.equal(dirtyObservations.every(Boolean), true,
    `Proxytrap liep na clean: ${JSON.stringify(dirtyObservations)}`);

  const thrownObservations = [];
  const hostileError = new Proxy(Object.assign(new Error('proxied failure'), {
    code: 'PROXIED_FAILURE',
  }), {
    ownKeys(target) {
      thrownObservations.push(store.inspect().dirty);
      return Reflect.ownKeys(target);
    },
    getOwnPropertyDescriptor(target, property) {
      thrownObservations.push(store.inspect().dirty);
      return Reflect.getOwnPropertyDescriptor(target, property);
    },
  });
  let thrown;
  try {
    store.runClaimedWrite(store.issueWriteCapability({
      target: path.join(config.workspaceDir, 'generation', 'proxied-error.txt'),
    }), () => { throw hostileError; });
  } catch (error) { thrown = error; }
  assert.match(thrown.message, /proxied failure/u);
  assert.equal(thrown.code, 'PROXIED_FAILURE');
  assert.equal(thrown.workspaceGeneration.outcome, 'uncertain');
  assert.equal(thrownObservations.length > 0, true);
  assert.equal(thrownObservations.every(Boolean), true,
    `geworpen Proxytrap liep na clean: ${JSON.stringify(thrownObservations)}`);
});

test('owner-death bewijs vereist exact UUID boot-id en robuuste proc-stat parsing', () => {
  assert.equal(typeof __workspaceGenerationTest?.ownerProvenDead, 'function');
  assert.equal(typeof __workspaceGenerationTest?.parseProcStatStartTicks, 'function');
  const pid = 4321;
  const bootId = '123e4567-e89b-42d3-a456-426614174000';
  const fields = Array.from({ length: 50 }, (_unused, index) => String(index + 3));
  fields[0] = 'S';
  fields[19] = '998877';
  const stat = `${pid} (worker ) with)paren) ${fields.join(' ')}`;
  assert.equal(__workspaceGenerationTest.parseProcStatStartTicks(stat, pid), '998877');

  const originalRead = fs.readFileSync;
  let boot = bootId;
  let processStat = stat;
  fs.readFileSync = function ownerFixture(itemPath, ...rest) {
    if (String(itemPath) === '/proc/sys/kernel/random/boot_id') return `${boot}\n`;
    if (String(itemPath) === `/proc/${pid}/stat`) return processStat;
    return originalRead.call(fs, itemPath, ...rest);
  };
  const owner = {
    contract: 'WorkspaceWriterOwner/v1', host: 'linux', pid,
    bootId, startTicks: '998877',
  };
  try {
    assert.equal(__workspaceGenerationTest.ownerProvenDead(owner), false);
    assert.equal(__workspaceGenerationTest.ownerProvenDead({
      ...owner, bootId: owner.bootId.toUpperCase(),
    }), false, 'een niet-canoniek boot-id mag nooit dood-bewijs worden');
    processStat = stat.replace('998877', '998878');
    assert.equal(__workspaceGenerationTest.ownerProvenDead(owner), true);
    processStat = `${pid} malformed`;
    assert.equal(__workspaceGenerationTest.ownerProvenDead(owner), false);
    boot = 'not-a-uuid';
    assert.equal(__workspaceGenerationTest.ownerProvenDead(owner), false);
    boot = '223e4567-e89b-42d3-a456-426614174000';
    assert.equal(__workspaceGenerationTest.ownerProvenDead({ ...owner, bootId: 'bad' }), false);
  } finally { fs.readFileSync = originalRead; }
});

test('de production-brokerblokkade heeft één geëxporteerde machinecode', () => {
  assert.equal(OS_EXCLUSIVE_WRITER_UNAVAILABLE, 'os_exclusive_writer_unavailable');
});

test('recovery bewaart de canonieke observatiebody en inspect herverifieert inhoud en aanwezigheid', () => {
  const childDataDir = processDataDir('bottie-generation-observation-body-');
  try {
    const crashed = runGenerationChild(childDataDir, `
      fs.writeFileSync(path.join(config.workspaceDir,'body.txt'),'body\\n');
      const store=createWorkspaceGenerationStore(db);
      store.runClaimedWrite(store.issueWriteCapability({
        target:path.join(config.workspaceDir,'claim.txt'),
      }),()=>process.exit(94));
    `);
    assert.equal(crashed.status, 94, crashed.stderr);
    const recovered = childJson(runGenerationChild(childDataDir, `
      const store=createWorkspaceGenerationStore(db);
      const verifier=store.issueRecoveryCapability();
      if(!verifier) throw new Error('missing verifier');
      console.log(JSON.stringify({recovery:store.recover(verifier),state:store.inspect()}));
      db.close();
    `));
    assert.equal(recovered.recovery.generation, 1);

    const raw = new Database(path.join(childDataDir, 'eva.db'));
    const outcome = raw.prepare(`SELECT observation_sha256,observation_json
      FROM eva_workspace_generation_outcomes_v1 WHERE lane='production'`).get();
    assert.equal(typeof outcome.observation_json, 'string');
    const body = JSON.parse(outcome.observation_json);
    assert.equal(canonicalJson(body), outcome.observation_json);
    assert.equal(createHash('sha256').update(outcome.observation_json).digest('hex'),
      outcome.observation_sha256);
    assert.equal(body.contract, 'WorkspaceRecoveryObservation/v1');
    assert.equal(body.lane, 'production');
    assert.equal(body.generationBefore, 0);
    assert.equal(typeof body.claimId, 'string');
    const trigger = raw.prepare(`SELECT sql FROM sqlite_master WHERE type='trigger'
      AND name='eva_workspace_generation_outcomes_v1_no_update'`).get().sql;
    raw.exec('DROP TRIGGER eva_workspace_generation_outcomes_v1_no_update');
    raw.prepare(`UPDATE eva_workspace_generation_outcomes_v1 SET observation_json=?
      WHERE lane='production'`).run('{}');
    raw.exec(trigger);
    raw.close();
    const tampered = runGenerationChild(childDataDir, `
      createWorkspaceGenerationStore(db);
      db.close();
    `);
    assert.notEqual(tampered.status, 0);
    assert.match(tampered.stderr, /observation body|hash mismatch/u);

    const missingRaw = new Database(path.join(childDataDir, 'eva.db'));
    missingRaw.exec('DROP TRIGGER eva_workspace_generation_outcomes_v1_no_update');
    missingRaw.prepare(`UPDATE eva_workspace_generation_outcomes_v1
      SET observation_json=?,observation_sha256=? WHERE lane='production'`)
      .run(outcome.observation_json, outcome.observation_sha256);
    missingRaw.exec(trigger);
    missingRaw.close();
    const restored = runGenerationChild(childDataDir, `
      createWorkspaceGenerationStore(db);
      db.close();
    `);
    assert.equal(restored.status, 0, restored.stderr);

    const absentRaw = new Database(path.join(childDataDir, 'eva.db'));
    absentRaw.exec('DROP TRIGGER eva_workspace_generation_outcomes_v1_no_update');
    absentRaw.exec(`UPDATE eva_workspace_generation_outcomes_v1
      SET observation_json=NULL,observation_sha256=NULL WHERE lane='production'`);
    absentRaw.exec(trigger);
    absentRaw.close();
    const absent = runGenerationChild(childDataDir, `
      createWorkspaceGenerationStore(db);
      db.close();
    `);
    assert.notEqual(absent.status, 0);
    assert.match(absent.stderr, /outcome body hash mismatch|observation body/u);
  } finally {
    fs.rmSync(childDataDir, { recursive: true, force: true });
  }
});

test('caught coordinatorfout en ActionResult dragen exact hetzelfde generation receipt', async () => {
  const store = createWorkspaceGenerationStore(db);
  const before = store.inspect().generation;
  let caught;
  try {
    store.runClaimedWrite(store.issueWriteCapability({
      target: path.join(config.workspaceDir, 'generation', 'direct-error.txt'),
    }), () => { throw Object.assign(new Error('direct effect failure'), { code: 'DIRECT_FAIL' }); });
  } catch (error) { caught = error; }
  assert.deepEqual(caught.workspaceGeneration, {
    contract: 'WorkspaceGenerationFailure/v1', lane: 'production',
    generation: before + 1, outcome: 'uncertain',
    receiptSha256: caught.workspaceGeneration.receiptSha256,
  });
  assert.match(caught.workspaceGeneration.receiptSha256, /^[0-9a-f]{64}$/u);

  let frozenCaught;
  try {
    store.runClaimedWrite(store.issueWriteCapability({
      target: path.join(config.workspaceDir, 'generation', 'frozen-error.txt'),
    }), () => { throw Object.freeze(new Error('frozen effect failure')); });
  } catch (error) { frozenCaught = error; }
  assert.match(frozenCaught.message, /frozen effect failure/u);
  assert.equal(frozenCaught.workspaceGeneration.contract, 'WorkspaceGenerationFailure/v1');
  assert.equal(frozenCaught.workspaceGeneration.lane, 'production');
  assert.match(frozenCaught.workspaceGeneration.receiptSha256, /^[0-9a-f]{64}$/u);

  const namespace = `test-artifacts/failure-binding-${Date.now()}`;
  const blockingParent = path.join(config.workspaceDir, namespace, 'bestand');
  fs.mkdirSync(path.dirname(blockingParent), { recursive: true });
  fs.writeFileSync(blockingParent, 'geen map\n');
  const result = JSON.parse(await executeTool('schrijf_bestand', {
    pad: `${namespace}/bestand/kind.txt`, inhoud: 'kan niet\n',
  }, { mode: 'chat', messageContext: { syntheticTest: true } }));
  assert.equal(result.ok, false);
  assert.deepEqual(result.workspace_generation, {
    contract: 'WorkspaceGenerationFailure/v1', lane: 'synthetic',
    generation: result.workspace_generation.generation, outcome: 'uncertain',
    receiptSha256: result.workspace_generation.receiptSha256,
  });
  assert.match(result.workspace_generation.receiptSha256, /^[0-9a-f]{64}$/u);
});
