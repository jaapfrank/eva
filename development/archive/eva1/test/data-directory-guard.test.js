import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test, { after } from 'node:test';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const suiteDataDir = fs.mkdtempSync('/tmp/bottie-data-guard-suite-');
const previousDataDir = process.env.EVA_DATA_DIR;
process.env.EVA_DATA_DIR = suiteDataDir;
assert.equal(path.isAbsolute(suiteDataDir), true);
assert.equal(path.relative('/tmp', suiteDataDir).startsWith('..'), false);
assert.equal(process.env.EVA_DATA_DIR, suiteDataDir);

const configModule = await import('../src/config.js');

after(() => {
  if (previousDataDir === undefined) delete process.env.EVA_DATA_DIR;
  else process.env.EVA_DATA_DIR = previousDataDir;
  fs.rmSync(suiteDataDir, { recursive: true, force: true });
});

function copiedProjectFixture(t, label) {
  const isolatedRoot = fs.mkdtempSync(`/tmp/bottie-data-guard-${label}-`);
  t.after(() => fs.rmSync(isolatedRoot, { recursive: true, force: true }));
  fs.cpSync(path.join(projectRoot, 'src'), path.join(isolatedRoot, 'src'), { recursive: true });
  fs.copyFileSync(path.join(projectRoot, 'package.json'), path.join(isolatedRoot, 'package.json'));
  fs.symlinkSync(path.join(projectRoot, 'node_modules'), path.join(isolatedRoot, 'node_modules'), 'dir');
  return isolatedRoot;
}

function cleanChildEnvironment(isolatedRoot, extra = {}) {
  const environment = {
    ...process.env,
    DOTENV_CONFIG_PATH: path.join(isolatedRoot, 'missing.env'),
    ...extra,
  };
  delete environment.EVA_DATA_DIR;
  delete environment.NODE_TEST_CONTEXT;
  return environment;
}

function dbImportProgram(specifier = './src/db.js') {
  return `const loaded=await import(${JSON.stringify(specifier)}); loaded.db.close(); console.log('DB_OPENED');`;
}

test('ieder proces eist EVA_DATA_DIR en expliciete productiepaden blijven zuiver', () => {
  assert.equal(typeof configModule.resolveEvaDataDir, 'function');
  assert.throws(() => configModule.resolveEvaDataDir({
    projectRoot: '/opt/eva', environment: {}, cwd: '/srv/runtime',
    argv: ['/usr/bin/node', '/opt/eva/src/index.js'],
  }), /EVA_DATA_DIR.*expliciet/iu, 'ook het echte hoofdproces heeft expliciete authority nodig');
  assert.throws(() => configModule.resolveEvaDataDir({
    projectRoot: '/opt/eva', environment: {}, cwd: '/opt/eva',
    argv: ['/usr/bin/node', 'src/index.js'],
  }), /EVA_DATA_DIR.*expliciet/iu, 'de relatieve systemd-vorm krijgt geen defaultuitzondering');
  assert.equal(configModule.resolveEvaDataDir({
    projectRoot: '/opt/eva', environment: { EVA_DATA_DIR: 'state/eva' }, cwd: '/srv/runtime',
  }), '/srv/runtime/state/eva');
  assert.equal(configModule.resolveEvaDataDir({
    projectRoot: '/opt/eva', environment: { EVA_DATA_DIR: '/var/lib/eva' }, cwd: '/srv/runtime',
  }), '/var/lib/eva');
  assert.throws(() => configModule.resolveEvaDataDir({
    projectRoot: '/opt/eva', environment: { NODE_ENV: 'test' }, cwd: '/srv/runtime',
  }), /EVA_DATA_DIR.*expliciet/iu,
  'NODE_ENV alleen verleent geen testpadsemantiek of default-data-authority');
});

test('testdetectie omvat Node-testflags en een direct uitgevoerd .test.js-bestand', () => {
  assert.equal(typeof configModule.nodeTestContextPresent, 'function');
  assert.equal(configModule.nodeTestContextPresent({
    environment: {}, execArgv: ['--test', '--test-isolation=none'], argv: ['/usr/bin/node'],
  }), true);
  assert.equal(configModule.nodeTestContextPresent({
    projectRoot: '/srv/eva', environment: {}, execArgv: [],
    argv: ['/usr/bin/node', '/srv/eva/test/guard-direct.test.js'],
  }), true);
  assert.equal(configModule.nodeTestContextPresent({
    projectRoot: '/srv/eva', environment: {}, execArgv: [],
    argv: ['/usr/bin/node', '/srv/eva/scripts/guard-direct.test.mjs'],
  }), true);
  assert.equal(configModule.nodeTestContextPresent({
    projectRoot: '/srv/eva', environment: {}, execArgv: [],
    argv: ['/usr/bin/node', '/srv/eva/scripts/nested/guard-direct.test.mjs'],
  }), false, 'alleen de directe scripts/*.test.*-glob verleent raw-node teststatus');
  assert.equal(configModule.nodeTestContextPresent({
    environment: { NODE_ENV: 'test' }, execArgv: [], argv: ['/usr/bin/node', '/srv/eva.js'],
  }), false);
  assert.equal(configModule.nodeTestContextPresent({
    projectRoot: '/srv/eva', environment: {}, execArgv: [],
    argv: ['/usr/bin/node', '/srv/eva/src/index.js', '/srv/eva/test/guard-direct.test.js'],
  }), false, 'een willekeurig later argv-item classificeert productie niet als test');
});

test('testpadresolutie eist een expliciete absolute afstammeling van /tmp', () => {
  const resolve = (EVA_DATA_DIR) => configModule.resolveEvaDataDir({
    projectRoot: '/opt/eva',
    environment: { NODE_TEST_CONTEXT: 'child-v8', ...(EVA_DATA_DIR == null ? {} : { EVA_DATA_DIR }) },
    cwd: '/srv/runtime',
  });
  assert.throws(() => resolve(null), /EVA_DATA_DIR.*absolute.*\/tmp/iu);
  assert.throws(() => resolve(''), /EVA_DATA_DIR.*absolute.*\/tmp/iu);
  assert.throws(() => resolve('relative-test-data'), /EVA_DATA_DIR.*absolute.*\/tmp/iu);
  assert.throws(() => resolve('/var/tmp/eva-test'), /EVA_DATA_DIR.*absolute.*\/tmp/iu);
  assert.throws(() => resolve('/tmp'), /EVA_DATA_DIR.*absolute.*\/tmp/iu);
  assert.throws(() => resolve('/tmp/../var/lib/eva'), /EVA_DATA_DIR.*absolute.*\/tmp/iu);
  assert.equal(resolve('/tmp/eva-test-explicit'), '/tmp/eva-test-explicit');
  assert.throws(() => configModule.resolveEvaDataDir({
    projectRoot: '/opt/eva', environment: { NODE_TEST_CONTEXT: '' }, cwd: '/srv/runtime',
  }), /EVA_DATA_DIR.*absolute.*\/tmp/iu, 'aanwezigheid, niet truthiness, bepaalt testcontext');
});

test('een symlink onder /tmp kan de testgrens niet naar buiten verleggen', () => {
  const directory = fs.mkdtempSync('/tmp/bottie-data-guard-link-');
  const alias = path.join(directory, 'outside');
  fs.symlinkSync('/dev', alias, 'dir');
  try {
    assert.throws(() => configModule.assertTestDataDirRealPath({
    dataDir: alias,
      environment: { NODE_TEST_CONTEXT: 'child-v8', EVA_DATA_DIR: alias },
    }), /EVA_DATA_DIR.*(?:private|werkelijk).*directory/iu);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('een testdatapad moet vooraf bestaan, eigen zijn en mode 0700 hebben', () => {
  const dataDir = fs.mkdtempSync('/tmp/bottie-data-guard-private-');
  const environment = { NODE_TEST_CONTEXT: 'child-v8', EVA_DATA_DIR: dataDir };
  try {
    fs.chmodSync(dataDir, 0o755);
    assert.throws(() => configModule.assertTestDataDirRealPath({
      dataDir, environment,
    }), /EVA_DATA_DIR.*private.*0700/iu);
    fs.chmodSync(dataDir, 0o700);
    const missing = `${dataDir}-missing`;
    assert.throws(() => configModule.assertTestDataDirRealPath({
      dataDir: missing, environment: { ...environment, EVA_DATA_DIR: missing },
    }), /EVA_DATA_DIR.*vooraf bestaan/iu);
  } finally {
    fs.chmodSync(dataDir, 0o700);
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('node --test kan zonder EVA_DATA_DIR geen default-DB openen', (t) => {
  const isolatedRoot = copiedProjectFixture(t, 'node-test');
  const runner = path.join(isolatedRoot, 'test', 'guard-runner.test.js');
  fs.mkdirSync(path.dirname(runner));
  fs.writeFileSync(runner, `import assert from 'node:assert/strict';
    import test from 'node:test';
    test('guard', async () => {
      const loaded=await import('../src/db.js'); loaded.db.close();
      console.log('DB_OPENED'); assert.fail('guard werd omzeild');
    });\n`);
  const child = spawnSync(process.execPath, [
    '--test', runner,
  ], {
    cwd: isolatedRoot,
    env: cleanChildEnvironment(isolatedRoot),
    encoding: 'utf8',
    timeout: 30_000,
  });

  assert.notEqual(child.status, 0, child.stdout || child.stderr);
  assert.match(`${child.stdout}\n${child.stderr}`, /EVA_DATA_DIR.*absolute.*\/tmp/iu);
  assert.doesNotMatch(child.stdout, /DB_OPENED/u);
  assert.equal(fs.existsSync(path.join(isolatedRoot, 'data', 'eva.db')), false);
});

test('direct node guard-direct.test.js kan zonder EVA_DATA_DIR geen default-DB openen', (t) => {
  const isolatedRoot = copiedProjectFixture(t, 'direct-test-file');
  const runner = path.join(isolatedRoot, 'test', 'guard-direct.test.js');
  fs.mkdirSync(path.dirname(runner));
  fs.writeFileSync(runner, `${dbImportProgram('../src/db.js')}\n`);
  const child = spawnSync(process.execPath, [runner], {
    cwd: isolatedRoot,
    env: cleanChildEnvironment(isolatedRoot),
    encoding: 'utf8',
    timeout: 30_000,
  });

  assert.notEqual(child.status, 0, child.stdout || child.stderr);
  assert.match(child.stderr, /EVA_DATA_DIR.*absolute.*\/tmp/iu);
  assert.doesNotMatch(child.stdout, /DB_OPENED/u);
  assert.equal(fs.existsSync(path.join(isolatedRoot, 'data', 'eva.db')), false);
});

test('direct node scripts/guard-direct.test.mjs weigert een geerfde relatieve productieroot', (t) => {
  const isolatedRoot = copiedProjectFixture(t, 'direct-script-test-file');
  const runner = path.join(isolatedRoot, 'scripts', 'guard-direct.test.mjs');
  fs.mkdirSync(path.dirname(runner));
  fs.writeFileSync(runner, `${dbImportProgram('../src/db.js')}\n`);
  const environment = cleanChildEnvironment(isolatedRoot);
  environment.EVA_DATA_DIR = './data';
  const child = spawnSync(process.execPath, [runner], {
    cwd: isolatedRoot,
    env: environment,
    encoding: 'utf8',
    timeout: 30_000,
  });

  assert.notEqual(child.status, 0, child.stdout || child.stderr);
  assert.match(child.stderr, /EVA_DATA_DIR.*absolute.*\/tmp/iu);
  assert.doesNotMatch(child.stdout, /DB_OPENED/u);
  assert.equal(fs.existsSync(path.join(isolatedRoot, 'data', 'eva.db')), false);
});

test('een ongemarkeerde copied-repo import kan zonder EVA_DATA_DIR geen default-DB openen', (t) => {
  const isolatedRoot = copiedProjectFixture(t, 'unmarked-import');
  const child = spawnSync(process.execPath, [
    '--input-type=module', '--eval', dbImportProgram(),
  ], {
    cwd: isolatedRoot,
    env: cleanChildEnvironment(isolatedRoot),
    encoding: 'utf8',
    timeout: 30_000,
  });

  assert.notEqual(child.status, 0, child.stdout || child.stderr);
  assert.match(child.stderr, /EVA_DATA_DIR.*expliciet/iu);
  assert.doesNotMatch(child.stdout, /DB_OPENED/u);
  assert.equal(fs.existsSync(path.join(isolatedRoot, 'data', 'eva.db')), false);
  assert.equal(fs.existsSync(path.join(isolatedRoot, 'data', 'workspace', 'package.json')), false);
});

test('ook het exacte copied-repo productie-entrypoint kan zonder EVA_DATA_DIR geen default openen', (t) => {
  const isolatedRoot = copiedProjectFixture(t, 'production-entrypoint');
  const entrypoint = path.join(isolatedRoot, 'src', 'index.js');
  fs.writeFileSync(entrypoint, `${dbImportProgram('./db.js')}\n`);
  const child = spawnSync(process.execPath, ['src/index.js'], {
    cwd: isolatedRoot,
    env: cleanChildEnvironment(isolatedRoot),
    encoding: 'utf8',
    timeout: 30_000,
  });

  assert.notEqual(child.status, 0, child.stdout || child.stderr);
  assert.match(child.stderr, /EVA_DATA_DIR.*expliciet/iu);
  assert.doesNotMatch(child.stdout, /DB_OPENED/u);
  assert.equal(fs.existsSync(path.join(isolatedRoot, 'data', 'eva.db')), false);
});

test('de immutable config kan cached DB-effecten niet naar een ander pad sturen', (t) => {
  const isolatedRoot = copiedProjectFixture(t, 'immutable-config');
  const dataDir = fs.mkdtempSync('/tmp/bottie-data-guard-immutable-data-');
  const outsideRoot = fs.mkdtempSync('/tmp/bottie-data-guard-immutable-outside-');
  t.after(() => {
    fs.rmSync(dataDir, { recursive: true, force: true });
    fs.rmSync(outsideRoot, { recursive: true, force: true });
  });
  const outsideData = path.join(outsideRoot, 'redirect-data');
  const outsideWorkspace = path.join(outsideRoot, 'redirect-workspace');
  const child = spawnSync(process.execPath, [
    '--input-type=module', '--eval', `
      const {config}=await import('./src/config.js');
      const changedData=Reflect.set(config,'dataDir',${JSON.stringify(outsideData)});
      const changedWorkspace=Reflect.set(config,'workspaceDir',${JSON.stringify(outsideWorkspace)});
      const {db}=await import('./src/db.js'); db.close();
      console.log(JSON.stringify({changedData,changedWorkspace}));
    `,
  ], {
    cwd: isolatedRoot,
    env: {
      ...cleanChildEnvironment(isolatedRoot), NODE_TEST_CONTEXT: 'child-v8',
      EVA_DATA_DIR: dataDir,
    },
    encoding: 'utf8',
    timeout: 30_000,
  });

  assert.equal(child.status, 0, child.stderr || child.stdout);
  assert.deepEqual(JSON.parse(child.stdout.trim()), {
    changedData: false, changedWorkspace: false,
  });
  assert.equal(fs.existsSync(outsideData), false);
  assert.equal(fs.existsSync(path.join(outsideWorkspace, 'package.json')), false);
  assert.equal(fs.existsSync(path.join(dataDir, 'eva.db')), true);
});

test('tracked productie-entrypoints geven data-authority expliciet en backups volgen de custom root', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(projectRoot, 'package.json'), 'utf8'));
  assert.equal(manifest.scripts.start,
    'EVA_DATA_DIR="${EVA_DATA_DIR:-./data}" node src/index.js');
  assert.equal(manifest.scripts.login,
    'EVA_DATA_DIR="${EVA_DATA_DIR:-./data}" node src/brain/oauth.js');

  const exampleEnvironment = fs.readFileSync(path.join(projectRoot, '.env.example'), 'utf8');
  assert.match(exampleEnvironment, /^EVA_DATA_DIR=\.\/data$/mu);

  const service = fs.readFileSync(path.join(projectRoot, 'deploy', 'eva.service'), 'utf8');
  assert.match(service, /^Environment=EVA_DATA_DIR=\/opt\/eva\/data$/mu);

  const indexSource = fs.readFileSync(path.join(projectRoot, 'src', 'index.js'), 'utf8');
  assert.match(indexSource,
    /const backupDir = path\.join\(config\.logicalDataDir, 'backups'\);/u);
  assert.match(indexSource,
    /env: \{ \.\.\.process\.env, EVA_DATA_DIR: config\.logicalDataDir \}/u);

  const backupSource = fs.readFileSync(path.join(projectRoot, 'scripts', 'eva-backup.mjs'), 'utf8');
  assert.match(backupSource,
    /backupData\(path\.join\(config\.logicalDataDir, 'backups'\)\)/u);
});

test('config pinning houdt DB en workspace op hetzelfde inode na een ancestorswap', (t) => {
  const isolatedRoot = copiedProjectFixture(t, 'ancestor-swap');
  const swapRoot = path.join(isolatedRoot, 'swap');
  const originalParent = path.join(swapRoot, 'selected-parent');
  const parkedParent = path.join(swapRoot, 'parked-parent');
  const outsideParent = path.join(swapRoot, 'outside-parent');
  const selectedData = path.join(originalParent, 'data');
  const outsideData = path.join(outsideParent, 'data');
  fs.mkdirSync(selectedData, { recursive: true, mode: 0o700 });
  fs.chmodSync(selectedData, 0o700);
  fs.mkdirSync(outsideData, { recursive: true, mode: 0o700 });
  fs.chmodSync(outsideData, 0o700);

  const child = spawnSync(process.execPath, [
    '--input-type=module', '--eval', `
      const fs=await import('node:fs');
      const {config}=await import('./src/config.js');
      fs.renameSync(${JSON.stringify(originalParent)}, ${JSON.stringify(parkedParent)});
      fs.symlinkSync(${JSON.stringify(outsideParent)}, ${JSON.stringify(originalParent)}, 'dir');
      const dataFd=Number(config.dataDir.split('/').at(-1));
      const workspaceFd=Number(config.workspaceDir.split('/').at(-1));
      const before={data:fs.fstatSync(dataFd).ino,workspace:fs.fstatSync(workspaceFd).ino};
      const {db}=await import('./src/db.js');
      db.prepare("INSERT INTO main.kv(key,value) VALUES('guard:ancestor','pinned')").run();
      const dbFd=Number(db.name.split('/').at(-1));
      const names=fs.readdirSync(config.dataDir).filter(name=>name.startsWith('eva.db')).sort();
      const details=Object.fromEntries(names.map(name=>[name,{
        real:fs.realpathSync(config.dataDir+'/'+name),
        ino:String(fs.statSync(config.dataDir+'/'+name).ino),
        dev:String(fs.statSync(config.dataDir+'/'+name).dev),
      }]));
      const sqliteDescriptors=Object.fromEntries(names.map(name=>[name,[]]));
      for(const entry of fs.readdirSync('/proc/self/fd')){
        if(!/^[0-9]+$/.test(entry)) continue;
        const descriptor=Number(entry);
        if(descriptor===dbFd) continue;
        try{
          const stat=fs.fstatSync(descriptor);
          const matched=names.find(name=>details[name].ino===String(stat.ino)
            && details[name].dev===String(stat.dev));
          if(!matched) continue;
          const flags=Number.parseInt(fs.readFileSync('/proc/self/fdinfo/'+entry,'utf8')
            .match(/^flags:\\s*([0-7]+)/m)?.[1]||'',8);
          sqliteDescriptors[matched].push({descriptor,flags});
        }catch{}
      }
      const after={data:fs.fstatSync(dataFd).ino,workspace:fs.fstatSync(workspaceFd).ino};
      const dbIno=String(fs.fstatSync(dbFd).ino);
      const journalMode=db.pragma('journal_mode',{simple:true});
      db.close();
      console.log(JSON.stringify({pid:process.pid,dataFd,workspaceFd,
        dataDir:config.dataDir,workspaceDir:config.workspaceDir,before,after,
        journalMode,names,details,dbIno,sqliteDescriptors}));
    `,
  ], {
    cwd: isolatedRoot,
    env: {
      ...cleanChildEnvironment(isolatedRoot),
      NODE_TEST_CONTEXT: 'child-v8',
      EVA_DATA_DIR: selectedData,
    },
    encoding: 'utf8',
    timeout: 30_000,
  });

  assert.equal(child.status, 0, child.stderr || child.stdout);
  assert.equal(fs.existsSync(path.join(parkedParent, 'data', 'eva.db')), true,
    'de vóór config-load gekozen directory blijft de autoriteit');
  assert.equal(fs.existsSync(path.join(outsideData, 'eva.db')), false,
    'de later ingeplugde ancestor ontvangt geen DB');
  assert.deepEqual(fs.readdirSync(outsideData).filter((name) => name.startsWith('eva.db')), [],
    'de later ingeplugde ancestor ontvangt ook geen WAL of SHM');
  assert.equal(fs.existsSync(path.join(outsideData, 'workspace', 'package.json')), false,
    'de later ingeplugde ancestor ontvangt geen workspacewrite');
  const reported = JSON.parse(child.stdout.trim());
  assert.match(reported.dataDir, /^\/proc\/self\/fd\/[1-9][0-9]*$/u);
  assert.match(reported.workspaceDir, /^\/proc\/self\/fd\/[1-9][0-9]*$/u);
  assert.notEqual(reported.workspaceDir, reported.dataDir);
  assert.deepEqual(reported.after, reported.before,
    'beide directoryhandles blijven tot na de DB-lifecycle op dezelfde inode open');
  assert.equal(reported.journalMode, 'wal');
  assert.deepEqual(reported.names, ['eva.db', 'eva.db-shm', 'eva.db-wal']);
  assert.equal(reported.dbIno, reported.details['eva.db'].ino,
    'de retained main-anchor blijft op dezelfde inode');
  for (const name of reported.names) {
    assert.equal(path.dirname(reported.details[name].real),
      fs.realpathSync(path.join(parkedParent, 'data')), `${name} blijft in de gepinde directory`);
    assert.ok(reported.sqliteDescriptors[name].length >= 1,
      `${name} heeft na de ancestorswap een echte SQLite-descriptor`);
    assert.equal(reported.sqliteDescriptors[name]
      .some(({ flags }) => (flags & 0o10000000) !== 0), false,
    `${name} SQLite-bewijs is niet de O_PATH-anchor`);
  }
  assert.equal(fs.existsSync(`/proc/${reported.pid}/fd/${reported.dataFd}`), false);
  assert.equal(fs.existsSync(`/proc/${reported.pid}/fd/${reported.workspaceFd}`), false,
    'de kernel ruimt retained handles op bij procesexit');
});

test('db-import faalt vóór filesystemeffect wanneer een retained handle is gesloten', (t) => {
  const isolatedRoot = copiedProjectFixture(t, 'closed-handle');
  const dataDir = fs.mkdtempSync('/tmp/bottie-data-guard-closed-data-');
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const child = spawnSync(process.execPath, [
    '--input-type=module', '--eval', `
      const fs=await import('node:fs');
      const {config}=await import('./src/config.js');
      fs.closeSync(Number(config.workspaceDir.split('/').at(-1)));
      const loaded=await import('./src/db.js'); loaded.db.close(); console.log('DB_OPENED');
    `,
  ], {
    cwd: isolatedRoot,
    env: {
      ...cleanChildEnvironment(isolatedRoot), NODE_TEST_CONTEXT: 'child-v8',
      EVA_DATA_DIR: dataDir,
    },
    encoding: 'utf8',
    timeout: 30_000,
  });

  assert.notEqual(child.status, 0, child.stdout || child.stderr);
  assert.match(child.stderr, /EVA_DATA_DIR.*verloor de gepinde workspace/iu);
  assert.doesNotMatch(child.stdout, /DB_OPENED/u);
  assert.equal(fs.existsSync(path.join(dataDir, 'eva.db')), false);
  assert.equal(fs.existsSync(path.join(dataDir, 'workspace', 'package.json')), false);
});

test('verwijderde testmarkering kan retained workspace-fd niet laten hergebruiken', (t) => {
  const isolatedRoot = copiedProjectFixture(t, 'removed-test-marker');
  const dataDir = fs.mkdtempSync('/tmp/bottie-data-guard-marker-data-');
  const redirectedWorkspace = fs.mkdtempSync('/tmp/bottie-data-guard-marker-redirect-');
  t.after(() => {
    fs.rmSync(dataDir, { recursive: true, force: true });
    fs.rmSync(redirectedWorkspace, { recursive: true, force: true });
  });
  const child = spawnSync(process.execPath, [
    '--input-type=module', '--eval', `
      const fs=await import('node:fs');
      const {config}=await import('./src/config.js');
      const workspaceFd=Number(config.workspaceDir.split('/').at(-1));
      fs.closeSync(workspaceFd);
      const replacement=fs.openSync(${JSON.stringify(redirectedWorkspace)},
        fs.constants.O_RDONLY|fs.constants.O_DIRECTORY|fs.constants.O_NOFOLLOW);
      if(replacement!==workspaceFd) throw new Error('fixture kreeg retained fd niet opnieuw');
      delete process.env.NODE_TEST_CONTEXT;
      const loaded=await import('./src/db.js'); loaded.db.close(); console.log('DB_OPENED');
    `,
  ], {
    cwd: isolatedRoot,
    env: {
      ...cleanChildEnvironment(isolatedRoot), NODE_TEST_CONTEXT: 'child-v8',
      EVA_DATA_DIR: dataDir,
    },
    encoding: 'utf8',
    timeout: 30_000,
  });

  assert.notEqual(child.status, 0, child.stdout || child.stderr);
  assert.match(child.stderr, /EVA_DATA_DIR.*gepinde workspace.*ongeldig/iu);
  assert.doesNotMatch(child.stdout, /DB_OPENED/u);
  assert.equal(fs.existsSync(path.join(dataDir, 'eva.db')), false);
  assert.equal(fs.existsSync(path.join(redirectedWorkspace, 'package.json')), false);
});

test('een eva.db-symlink binnen de gepinde testmap kan geen buitendatabase openen', (t) => {
  const isolatedRoot = copiedProjectFixture(t, 'database-link');
  const dataDir = fs.mkdtempSync('/tmp/bottie-data-guard-db-link-data-');
  const outsideDir = fs.mkdtempSync('/tmp/bottie-data-guard-db-link-outside-');
  t.after(() => {
    fs.rmSync(dataDir, { recursive: true, force: true });
    fs.rmSync(outsideDir, { recursive: true, force: true });
  });
  const outsideDatabase = path.join(outsideDir, 'eva.db');
  fs.symlinkSync(outsideDatabase, path.join(dataDir, 'eva.db'));
  const child = spawnSync(process.execPath, [
    '--input-type=module', '--eval', dbImportProgram(),
  ], {
    cwd: isolatedRoot,
    env: {
      ...cleanChildEnvironment(isolatedRoot), NODE_TEST_CONTEXT: 'child-v8',
      EVA_DATA_DIR: dataDir,
    },
    encoding: 'utf8',
    timeout: 30_000,
  });

  assert.notEqual(child.status, 0, child.stdout || child.stderr);
  assert.match(child.stderr, /EVA_DATA_DIR.*databasebestand.*(?:symlink|no-follow)/iu);
  assert.doesNotMatch(child.stdout, /DB_OPENED/u);
  assert.equal(fs.existsSync(outsideDatabase), false,
    'een onveilig kindpad krijgt buiten de testmap geen SQLite-bytes');
});

test('hardlink, FIFO en foreign-owner metadata worden vóór SQLite-open geweigerd', (t) => {
  assert.equal(typeof configModule.databaseFileMetadataProblem, 'function');
  assert.match(configModule.databaseFileMetadataProblem({
    regular: true, uid: process.getuid(), nlink: 1,
  }, process.getuid() + 1), /eigen/u, 'foreign-owner metadata faalt zuiver vóór een open');

  const foreignRoot = copiedProjectFixture(t, 'database-foreign-owner');
  const foreignDataDir = fs.mkdtempSync('/tmp/bottie-data-guard-db-foreign-data-');
  t.after(() => fs.rmSync(foreignDataDir, { recursive: true, force: true }));
  const foreignChild = spawnSync(process.execPath, [
    '--input-type=module', '--eval', `
      const fs=(await import('node:fs')).default;
      await import('./src/config.js');
      const original=fs.fstatSync;
      fs.fstatSync=function injectedForeignOwner(descriptor,...rest){
        const stat=original.call(fs,descriptor,...rest);
        if(!stat.isFile()) return stat;
        return new Proxy(stat,{get(target,property,receiver){
          if(property==='uid') return Number(target.uid)+1;
          const value=Reflect.get(target,property,receiver);
          return typeof value==='function'?value.bind(target):value;
        }});
      };
      const loaded=await import('./src/db.js'); loaded.db.close(); console.log('DB_OPENED');
    `,
  ], {
    cwd: foreignRoot,
    env: {
      ...cleanChildEnvironment(foreignRoot), NODE_TEST_CONTEXT: 'child-v8',
      EVA_DATA_DIR: foreignDataDir,
    },
    encoding: 'utf8',
    timeout: 30_000,
  });
  assert.notEqual(foreignChild.status, 0, foreignChild.stdout || foreignChild.stderr);
  assert.match(foreignChild.stderr, /databasebestand.*eigen regulier single-link/iu);
  assert.doesNotMatch(foreignChild.stdout, /DB_OPENED/u);
  assert.equal(fs.statSync(path.join(foreignDataDir, 'eva.db')).size, 0,
    'foreign-owner attestatie stopt vóór SQLite schema/WAL schrijft');

  for (const kind of ['hardlink', 'fifo']) {
    const isolatedRoot = copiedProjectFixture(t, `database-${kind}`);
    const dataDir = fs.mkdtempSync(`/tmp/bottie-data-guard-db-${kind}-data-`);
    const outsideDir = fs.mkdtempSync(`/tmp/bottie-data-guard-db-${kind}-outside-`);
    t.after(() => {
      fs.rmSync(dataDir, { recursive: true, force: true });
      fs.rmSync(outsideDir, { recursive: true, force: true });
    });
    const databasePath = path.join(dataDir, 'eva.db');
    const outsideFile = path.join(outsideDir, 'outside.db');
    if (kind === 'hardlink') {
      fs.writeFileSync(outsideFile, 'BUITEN-BLIJFT-BYTEGELIJK\n', { mode: 0o600 });
      fs.linkSync(outsideFile, databasePath);
    } else {
      const made = spawnSync('/usr/bin/mkfifo', ['-m', '600', databasePath], {
        encoding: 'utf8', timeout: 5_000,
      });
      assert.equal(made.status, 0, made.stderr);
    }
    const child = spawnSync(process.execPath, [
      '--input-type=module', '--eval', dbImportProgram(),
    ], {
      cwd: isolatedRoot,
      env: {
        ...cleanChildEnvironment(isolatedRoot), NODE_TEST_CONTEXT: 'child-v8',
        EVA_DATA_DIR: dataDir,
      },
      encoding: 'utf8',
      timeout: 5_000,
    });
    assert.notEqual(child.status, 0, `${kind}: ${child.stdout || child.stderr}`);
    assert.match(child.stderr, /databasebestand.*regulier single-link/iu, kind);
    assert.doesNotMatch(child.stdout, /DB_OPENED/u);
    if (kind === 'hardlink') {
      assert.equal(fs.readFileSync(outsideFile, 'utf8'), 'BUITEN-BLIJFT-BYTEGELIJK\n');
    }
  }
});

test('bestaande WAL/SHM symlink, hardlink, FIFO en foreign-owner falen vóór SQLite-effect', (t) => {
  for (const [leaf, kind] of [
    ['eva.db-wal', 'symlink'],
    ['eva.db-wal', 'hardlink'],
    ['eva.db-shm', 'fifo'],
  ]) {
    const isolatedRoot = copiedProjectFixture(t, `sidecar-${kind}`);
    const dataDir = fs.mkdtempSync(`/tmp/bottie-data-guard-sidecar-${kind}-data-`);
    const outsideDir = fs.mkdtempSync(`/tmp/bottie-data-guard-sidecar-${kind}-outside-`);
    t.after(() => {
      fs.rmSync(dataDir, { recursive: true, force: true });
      fs.rmSync(outsideDir, { recursive: true, force: true });
    });
    const target = path.join(dataDir, leaf);
    const outside = path.join(outsideDir, leaf);
    if (kind === 'symlink') {
      fs.symlinkSync(outside, target);
    } else if (kind === 'hardlink') {
      fs.writeFileSync(outside, 'BUITEN-BLIJFT-BYTEGELIJK\n', { mode: 0o600 });
      fs.linkSync(outside, target);
    } else {
      const made = spawnSync('/usr/bin/mkfifo', ['-m', '600', target], {
        encoding: 'utf8', timeout: 5_000,
      });
      assert.equal(made.status, 0, made.stderr);
    }
    const child = spawnSync(process.execPath, [
      '--input-type=module', '--eval', dbImportProgram(),
    ], {
      cwd: isolatedRoot,
      env: {
        ...cleanChildEnvironment(isolatedRoot), NODE_TEST_CONTEXT: 'child-v8',
        EVA_DATA_DIR: dataDir,
      },
      encoding: 'utf8',
      timeout: 5_000,
    });
    assert.notEqual(child.status, 0, `${leaf}/${kind}: ${child.stdout || child.stderr}`);
    assert.match(child.stderr, /databasebestand.*regulier single-link/iu, `${leaf}/${kind}`);
    assert.doesNotMatch(child.stdout, /DB_OPENED/u);
    assert.equal(fs.existsSync(path.join(dataDir, 'workspace', 'package.json')), true,
      'alleen de vóór de DB-leafguard verwachte pinned workspace-initialisatie mag bestaan');
    if (kind === 'symlink') assert.equal(fs.existsSync(outside), false);
    if (kind === 'hardlink') {
      assert.equal(fs.readFileSync(outside, 'utf8'), 'BUITEN-BLIJFT-BYTEGELIJK\n');
    }
  }

  const isolatedRoot = copiedProjectFixture(t, 'sidecar-foreign');
  const dataDir = fs.mkdtempSync('/tmp/bottie-data-guard-sidecar-foreign-data-');
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  fs.writeFileSync(path.join(dataDir, 'eva.db-wal'), '', { mode: 0o600 });
  const child = spawnSync(process.execPath, [
    '--input-type=module', '--eval', `
      const fs=(await import('node:fs')).default;
      await import('./src/config.js');
      const original=fs.fstatSync;
      fs.fstatSync=function injectedForeignSidecar(descriptor,...rest){
        const stat=original.call(fs,descriptor,...rest);
        const link=(()=>{try{return fs.readlinkSync('/proc/self/fd/'+descriptor)}catch{return ''}})();
        if(!link.endsWith('/eva.db-wal')) return stat;
        return new Proxy(stat,{get(target,property,receiver){
          if(property==='uid') return Number(target.uid)+1;
          const value=Reflect.get(target,property,receiver);
          return typeof value==='function'?value.bind(target):value;
        }});
      };
      const loaded=await import('./src/db.js'); loaded.db.close(); console.log('DB_OPENED');
    `,
  ], {
    cwd: isolatedRoot,
    env: {
      ...cleanChildEnvironment(isolatedRoot), NODE_TEST_CONTEXT: 'child-v8',
      EVA_DATA_DIR: dataDir,
    },
    encoding: 'utf8', timeout: 5_000,
  });
  assert.notEqual(child.status, 0, child.stdout || child.stderr);
  assert.match(child.stderr, /databasebestand.*regulier single-link/iu);
  assert.doesNotMatch(child.stdout, /DB_OPENED/u);
});

test('SQLite DB, WAL en SHM blijven aantoonbaar in dezelfde gepinde testdirectory', (t) => {
  const isolatedRoot = copiedProjectFixture(t, 'database-wal');
  const dataDir = fs.mkdtempSync('/tmp/bottie-data-guard-db-wal-data-');
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const child = spawnSync(process.execPath, [
    '--input-type=module', '--eval', `
      const fs=await import('node:fs');
      const {config}=await import('./src/config.js');
      const {db}=await import('./src/db.js');
      db.prepare("INSERT INTO main.kv(key,value) VALUES('guard:wal','pinned')").run();
      const dbFd=Number(db.name.split('/').at(-1));
      const dataFd=Number(config.dataDir.split('/').at(-1));
      const names=fs.readdirSync(config.dataDir).filter(name=>name.startsWith('eva.db')).sort();
      const details=Object.fromEntries(names.map(name=>[name,{
        real:fs.realpathSync(config.dataDir+'/'+name),
        ino:String(fs.statSync(config.dataDir+'/'+name).ino),
        dev:String(fs.statSync(config.dataDir+'/'+name).dev),
      }]));
      const sqliteDescriptors=Object.fromEntries(names.map(name=>[name,[]]));
      for(const entry of fs.readdirSync('/proc/self/fd')){
        if(!/^[0-9]+$/.test(entry)) continue;
        const descriptor=Number(entry);
        if(descriptor===dbFd) continue;
        try{
          const stat=fs.fstatSync(descriptor);
          const matched=names.find(name=>details[name].ino===String(stat.ino)
            && details[name].dev===String(stat.dev));
          if(!matched) continue;
          const flags=Number.parseInt(fs.readFileSync('/proc/self/fdinfo/'+entry,'utf8')
            .match(/^flags:\\s*([0-7]+)/m)?.[1]||'',8);
          sqliteDescriptors[matched].push({descriptor,flags});
        }catch{}
      }
      const mainFile=db.prepare('PRAGMA main.database_list').all()
        .find(row=>String(row.name)==='main').file;
      console.log(JSON.stringify({
        journalMode:db.pragma('journal_mode',{simple:true}),names,details,
        dbIno:String(fs.fstatSync(dbFd).ino),dataIno:String(fs.fstatSync(dataFd).ino),
        logicalDataIno:String(fs.statSync(config.logicalDataDir).ino),mainFile,sqliteDescriptors,
      }));
      db.close();
    `,
  ], {
    cwd: isolatedRoot,
    env: {
      ...cleanChildEnvironment(isolatedRoot), NODE_TEST_CONTEXT: 'child-v8',
      EVA_DATA_DIR: dataDir,
    },
    encoding: 'utf8',
    timeout: 30_000,
  });

  assert.equal(child.status, 0, child.stderr || child.stdout);
  const result = JSON.parse(child.stdout.trim());
  assert.equal(result.journalMode, 'wal');
  assert.deepEqual(result.names, ['eva.db', 'eva.db-shm', 'eva.db-wal']);
  assert.equal(result.dbIno, result.details['eva.db'].ino);
  assert.equal(fs.realpathSync(result.mainFile), result.details['eva.db'].real);
  assert.equal(result.dataIno, result.logicalDataIno);
  for (const name of result.names) {
    assert.equal(path.dirname(result.details[name].real), fs.realpathSync(dataDir), name);
    assert.ok(result.sqliteDescriptors[name].length >= 1,
      `${name} heeft naast de guard-anchor een echte SQLite-descriptor`);
    assert.equal(result.sqliteDescriptors[name].some(({ flags }) => (flags & 0o10000000) !== 0),
      false, `${name} bewijs gebruikt geen O_PATH-anchor als SQLite-fd`);
  }
});

test('db-import zonder EVA_DATA_DIR faalt vóór openen of aanmaken van het defaultpad', (t) => {
  const isolatedRoot = copiedProjectFixture(t, 'missing-explicit');

  const childEnvironment = {
    ...cleanChildEnvironment(isolatedRoot),
    NODE_TEST_CONTEXT: 'child-v8',
  };
  const child = spawnSync(process.execPath, [
    '--input-type=module',
    '--eval',
    "const loaded=await import('./src/db.js'); loaded.db.close(); console.log('DB_OPENED');",
  ], {
    cwd: isolatedRoot,
    env: childEnvironment,
    encoding: 'utf8',
    timeout: 30_000,
  });

  assert.notEqual(child.status, 0, child.stdout || child.stderr);
  assert.match(child.stderr, /EVA_DATA_DIR.*absolute.*\/tmp/iu);
  assert.doesNotMatch(child.stdout, /DB_OPENED/u);
  assert.equal(fs.existsSync(path.join(isolatedRoot, 'data', 'eva.db')), false);
  assert.equal(fs.existsSync(path.join(isolatedRoot, 'data', 'workspace', 'package.json')), false);
});
