import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const scriptsDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptsDirectory, '..');
const sourceRunner = path.join(scriptsDirectory, 'run-isolated-tests.mjs');
const sourceBootstrap = path.join(scriptsDirectory, 'run-isolated-tests-bootstrap');
const sourceBootstrapC = path.join(scriptsDirectory, 'run-isolated-tests-bootstrap.c');
const releaseManifest = path.join(scriptsDirectory, 'run-isolated-tests-release.json');
const alreadyIsolated = process.env.EVA_ISOLATED_TEST_RUNNER === '1';
const compiler = '/usr/bin/x86_64-linux-gnu-gcc-13';

function write(filename, contents, mode = 0o600) {
  fs.writeFileSync(filename, contents, { encoding: 'utf8', mode, flag: 'wx' });
}

function compileX32Probe(repository) {
  const source = path.join(repository, 'test', 'x32-probe.c');
  const executable = path.join(repository, 'test', 'x32-probe');
  write(source, `
    #include <errno.h>
    #include <stdio.h>
    #include <sys/syscall.h>
    #include <unistd.h>
    #ifndef __X32_SYSCALL_BIT
    #define __X32_SYSCALL_BIT 0x40000000
    #endif
    int main(void) {
      errno = 0;
      long result = syscall(__X32_SYSCALL_BIT | __NR_getpid);
      printf("%ld:%d\\n", result, errno);
      return 0;
    }
  `);
  const compiled = spawnSync('/usr/bin/cc', [
    '-std=c11', '-D_GNU_SOURCE', '-O2', '-Wall', '-Wextra', '-Werror',
    '-fPIE', '-pie', '-o', executable, source,
  ], { cwd: repository, encoding: 'utf8' });
  assert.equal(compiled.status, 0, compiled.stderr || compiled.stdout);
  fs.chmodSync(executable, 0o700);
  return executable;
}

function installFakeCoverageReporter(repository) {
  const c8Directory = path.join(repository, 'node_modules', 'c8', 'bin');
  fs.mkdirSync(c8Directory, { recursive: true, mode: 0o700 });
  write(path.join(c8Directory, 'c8.js'), `
    import fs from 'node:fs';
    import path from 'node:path';
    const index = process.argv.indexOf('--temp-directory');
    if (process.argv[2] !== 'report' || !process.argv.includes('--clean=false') || index < 0) {
      process.stderr.write('onverwachte c8 report-argumenten\\n');
      process.exit(2);
    }
    const temporary = process.argv[index + 1];
    if (!/^\\/proc\\/self\\/fd\\/\\d+$/u.test(temporary)) {
      process.stderr.write('coverage-input is geen gepinde descriptor\\n');
      process.exit(3);
    }
    const profiles = fs.readdirSync(temporary).filter((name) => name.endsWith('.json'));
    const documents = profiles.map((name) => JSON.parse(
      fs.readFileSync(path.join(temporary, name), 'utf8'),
    ));
    if (profiles.length === 0 || !documents.every((document) =>
      Number.isFinite(document.timestamp) && Array.isArray(document.result))) {
      process.stderr.write('geen gevalideerde V8-profielen ontvangen\\n');
      process.exit(4);
    }
    fs.writeFileSync('coverage/coverage-report-proof.json', JSON.stringify({
      profileCount: profiles.length,
      urls: documents.flatMap(({ result }) => result.map(({ url }) => url)),
    }), { mode: 0o600, flag: 'wx' });
  `);
  write(path.join(repository, 'scripts', 'quality-gate.mjs'), `
    import fs from 'node:fs';
    if (process.argv[2] !== 'maintainer-coverage') process.exit(2);
    const proof = JSON.parse(fs.readFileSync('coverage/coverage-report-proof.json', 'utf8'));
    if (!(proof.profileCount > 0)) process.exit(3);
  `);
}

function createCopiedRepository(t, { symlinkData = false } = {}) {
  const fixtureRoot = fs.mkdtempSync('/tmp/bottie-isolated-runner-fixture-');
  fs.chmodSync(fixtureRoot, 0o700);
  const repository = path.join(fixtureRoot, 'copied-repo');
  fs.mkdirSync(repository, { mode: 0o700 });
  fs.mkdirSync(path.join(repository, 'scripts'), { mode: 0o700 });
  fs.mkdirSync(path.join(repository, 'test'), { mode: 0o700 });
  if (symlinkData) {
    const realData = path.join(fixtureRoot, 'private-real-data');
    fs.mkdirSync(realData, { mode: 0o700 });
    fs.symlinkSync(realData, path.join(repository, 'data'));
  } else {
    fs.mkdirSync(path.join(repository, 'data'), { mode: 0o700 });
  }
  write(path.join(repository, 'package.json'), '{"type":"module"}\n');
  const sentinel = path.join(symlinkData
    ? path.join(fixtureRoot, 'private-real-data')
    : path.join(repository, 'data'), 'private-production-sentinel');
  write(sentinel, 'copied-production-secret\n');
  fs.copyFileSync(sourceRunner, path.join(repository, 'scripts', 'run-isolated-tests.mjs'),
    fs.constants.COPYFILE_EXCL);
  fs.copyFileSync(sourceBootstrap,
    path.join(repository, 'scripts', 'run-isolated-tests-bootstrap'),
    fs.constants.COPYFILE_EXCL);
  fs.chmodSync(path.join(repository, 'scripts', 'run-isolated-tests-bootstrap'), 0o755);
  t.after(() => fs.rmSync(fixtureRoot, { recursive: true, force: true }));
  return { fixtureRoot, repository, sentinel };
}

function probeSource(repository, label) {
  return `
    import assert from 'node:assert/strict';
    import { spawnSync } from 'node:child_process';
    import fs from 'node:fs';
    import path from 'node:path';
    import test from 'node:test';

    const repository = ${JSON.stringify(repository)};
    const dataMount = path.join(repository, 'data');
    const sentinel = path.join(dataMount, 'private-production-sentinel');

    test('isolated probe ${label}', () => {
      assert.equal(process.env.EVA_ISOLATED_TEST_RUNNER, '1');
      assert.match(process.env.EVA_DATA_DIR,
        /^\\/tmp\\/bottie-test-bootstrap-[A-Za-z0-9]{6}\\/test-[^/]+\\/data$/u);
      const dataStat = fs.lstatSync(process.env.EVA_DATA_DIR);
      assert.equal(dataStat.isDirectory(), true);
      assert.equal(dataStat.isSymbolicLink(), false);
      assert.equal(dataStat.mode & 0o777, 0o700);
      assert.equal(dataStat.uid, process.getuid());

      assert.throws(() => fs.readFileSync(sentinel, 'utf8'), { code: 'ENOENT' });
      assert.deepEqual(fs.readdirSync(dataMount), []);
      assert.throws(
        () => fs.writeFileSync(path.join(dataMount, 'must-not-exist'), 'blocked'),
        (error) => error?.code === 'EROFS' || error?.code === 'EACCES',
      );

      const unmount = spawnSync('/usr/bin/umount', [dataMount], { encoding: 'utf8' });
      assert.notEqual(unmount.status, 0, 'testcode kon de read-only shadow unmounten');
      const remount = spawnSync('/usr/bin/mount', ['-o', 'remount,rw', dataMount], {
        encoding: 'utf8',
      });
      assert.notEqual(remount.status, 0, 'testcode kon de shadow beschrijfbaar remounten');
      const nestedUnmount = spawnSync('/usr/bin/unshare', [
        '--user', '--map-current-user', '--keep-caps', '--mount', '--fork',
        '/usr/bin/umount', dataMount,
      ], { encoding: 'utf8' });
      assert.notEqual(nestedUnmount.status, 0,
        'testcode kon via een geneste user namespace de shadow unmounten');
      assert.equal(nestedUnmount.error, undefined, nestedUnmount.error?.message);
      assert.match(nestedUnmount.stderr, /Operation not permitted/iu);
      assert.throws(() => fs.readFileSync('/proc/1/root' + sentinel, 'utf8'), { code: 'ENOENT' });
      assert.throws(() => fs.readFileSync('/proc/self/root' + sentinel, 'utf8'), { code: 'ENOENT' });

      const jailRoot = fs.statSync('/');
      const dotdotRoot = fs.statSync('/../../../../../../');
      assert.equal(String(dotdotRoot.dev) + ':' + String(dotdotRoot.ino),
        String(jailRoot.dev) + ':' + String(jailRoot.ino));

      const status = fs.readFileSync('/proc/self/status', 'utf8');
      assert.match(status, /^NoNewPrivs:\\s+1$/mu);
      for (const capability of ['CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb']) {
        assert.match(status, new RegExp('^' + capability + ':\\\\s+0+$', 'mu'));
      }
      assert.match(status, /^Seccomp:\\s+2$/mu);

      const database = path.join(process.env.EVA_DATA_DIR, 'eva.db');
      fs.writeFileSync(database, 'fixture-only');
      assert.equal(fs.readFileSync(database, 'utf8'), 'fixture-only');
      process.stdout.write('ISOLATION_RECORD ' + JSON.stringify({
        label: ${JSON.stringify(label)}, dataDir: process.env.EVA_DATA_DIR,
      }) + '\\n');
    });
  `;
}

function runRunner(repository, files, extra = {}) {
  return spawnSync(path.join(repository, 'scripts', 'run-isolated-tests-bootstrap'), files, {
    cwd: repository,
    env: { ...process.env, EVA_DATA_DIR: path.join(repository, 'data'), ...extra },
    encoding: 'utf8',
    maxBuffer: 8 * 1024 * 1024,
  });
}

function runExistingRunner(files) {
  return spawnSync(sourceBootstrap, files, {
    cwd: projectRoot,
    env: { ...process.env, EVA_DATA_DIR: path.join(projectRoot, 'data') },
    encoding: 'utf8',
    maxBuffer: 8 * 1024 * 1024,
  });
}

async function startHostLoopbackSentinel(t) {
  const server = spawn(process.execPath, ['--input-type=module', '-e', `
    import net from 'node:net';
    const server = net.createServer((socket) => socket.end('HOST_LOOPBACK_SENTINEL'));
    server.listen(0, '127.0.0.1', () => process.stdout.write(String(server.address().port) + '\\n'));
    process.on('SIGTERM', () => server.close(() => process.exit(0)));
  `], { stdio: ['ignore', 'pipe', 'pipe'] });
  let stderr = '';
  server.stderr.setEncoding('utf8');
  server.stderr.on('data', (chunk) => { stderr += chunk; });
  const port = await new Promise((resolve, reject) => {
    let stdout = '';
    const timeout = setTimeout(() => reject(new Error(`loopback sentinel timeout: ${stderr}`)), 5000);
    server.stdout.setEncoding('utf8');
    server.stdout.on('data', (chunk) => {
      stdout += chunk;
      const match = /^(\d+)\n/u.exec(stdout);
      if (match) {
        clearTimeout(timeout);
        resolve(Number.parseInt(match[1], 10));
      }
    });
    server.once('error', (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    server.once('exit', (status) => {
      clearTimeout(timeout);
      reject(new Error(`loopback sentinel stopte (${status}): ${stderr}`));
    });
  });
  t.after(() => {
    if (server.exitCode === null && server.signalCode === null) server.kill('SIGTERM');
  });
  return port;
}

function sha256(filename) {
  return createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
}

function compileBootstrap(output, extra = []) {
  return spawnSync(compiler, [
    ...extra, '-static', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-fno-ident',
    '-fno-record-gcc-switches', '-Wl,--build-id=none', '-s', '-o', output,
    sourceBootstrapC,
  ], { cwd: projectRoot, encoding: 'utf8' });
}

function waitForChild(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve({ status: child.exitCode, signal: child.signalCode });
  }
  return new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (status, signal) => resolve({ status, signal }));
  });
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function activeResourceUnits(pattern = 'bottie-isolated-tests-*') {
  const result = spawnSync('/usr/bin/systemctl', [
    '--user', '--no-legend', '--plain', 'list-units', pattern,
    '--state=active,activating,deactivating',
  ], {
    encoding: 'utf8',
    env: {
      PATH: '/usr/bin:/bin',
      LANG: 'C.UTF-8',
      LC_ALL: 'C.UTF-8',
      XDG_RUNTIME_DIR: `/run/user/${process.getuid()}`,
    },
  });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}

test('statische bootstrap is exact reproduceerbaar en heeft geen dynamische loader', {
  skip: alreadyIsolated && 'de compiler hoort niet in de testjail',
}, (t) => {
  const fixture = fs.mkdtempSync('/tmp/bottie-bootstrap-repro-');
  fs.chmodSync(fixture, 0o700);
  t.after(() => fs.rmSync(fixture, { recursive: true, force: true }));
  const first = path.join(fixture, 'bootstrap-first');
  const second = path.join(fixture, 'bootstrap-second');
  for (const output of [first, second]) {
    const result = compileBootstrap(output);
    assert.equal(result.status, 0, result.stderr || result.stdout);
  }
  assert.equal(sha256(first), sha256(second));
  assert.equal(sha256(first), sha256(sourceBootstrap));
  const bytes = fs.readFileSync(sourceBootstrap);
  assert.equal(bytes.subarray(0, 4).toString('hex'), '7f454c46');
  assert.equal(bytes.readUInt16LE(16), 2, 'bootstrap is geen ET_EXEC');
  assert.equal(bytes.readUInt16LE(18), 62, 'bootstrap is geen x86_64 ELF');
  const tableOffset = Number(bytes.readBigUInt64LE(32));
  const entrySize = bytes.readUInt16LE(54);
  const entryCount = bytes.readUInt16LE(56);
  const types = Array.from({ length: entryCount }, (_, index) =>
    bytes.readUInt32LE(tableOffset + index * entrySize));
  assert.equal(types.includes(2), false, 'bootstrap bevat PT_DYNAMIC');
  assert.equal(types.includes(3), false, 'bootstrap bevat PT_INTERP');
  const status = fs.lstatSync(sourceBootstrap);
  assert.equal(status.isFile(), true);
  assert.equal(status.isSymbolicLink(), false);
  assert.equal(status.nlink, 1);
  assert.equal(status.mode & 0o022, 0);
});

test('iedere Node-grens draagt exact de fetch-veilige vlag binnen ongewijzigde limieten', () => {
  const bootstrapSource = fs.readFileSync(sourceBootstrapC, 'utf8');
  const runnerSource = fs.readFileSync(sourceRunner, 'utf8');
  assert.match(bootstrapSource,
    /#define NODE_WASM_FLAG "--disable-wasm-trap-handler"/u);
  assert.match(bootstrapSource,
    /set_limit\(RLIMIT_AS, 6ULL \* 1024ULL \* 1024ULL \* 1024ULL,\s*6ULL \* 1024ULL \* 1024ULL \* 1024ULL\)/u);
  assert.match(bootstrapSource, /#define MEMORY_MAX_BYTES 4294967296ULL/u);
  assert.match(bootstrapSource, /"MemoryMax=4294967296"/u);
  assert.match(bootstrapSource,
    /runner_arguments\[offset\+\+\] = \(char \*\)NODE_BINARY;\s*runner_arguments\[offset\+\+\] = \(char \*\)NODE_WASM_FLAG;/u);
  assert.match(bootstrapSource,
    /\(char \*\)NODE_BINARY, \(char \*\)NODE_WASM_FLAG, c8, "report"/u);
  assert.match(bootstrapSource,
    /\(char \*\)NODE_BINARY, \(char \*\)NODE_WASM_FLAG, quality, "maintainer-coverage"/u);

  assert.match(runnerSource,
    /const NODE_WASM_FLAG = '--disable-wasm-trap-handler';/u);
  assert.match(runnerSource,
    /process\.execPath, '\/usr\/bin\/getent', '\/usr\/bin\/git'/u);
  assert.match(runnerSource, /executableStat\.nlink !== 1/u);
  assert.match(runnerSource,
    /remountJailDirectoryReadOnly\(payload\.jailRoot, '\/usr'\)/u);
  assert.match(runnerSource,
    /remountJailDirectoryReadOnly\(payload\.jailRoot, '\/etc', \{ noexec: true \}\)/u);
  assert.equal((runnerSource.match(/process\.execPath,\s*NODE_WASM_FLAG,/gu) || []).length, 2,
    'namespace- en verify-boundary missen hun exacte Node-vlag');
  assert.match(runnerSource,
    /spawnSync\(process\.execPath, \[NODE_WASM_FLAG, '--test', payload\.testFile\]/u);
  assert.match(runnerSource,
    /process\.execArgv\.filter\(\(argument\) => argument === NODE_WASM_FLAG\)/u);
  assert.match(runnerSource,
    /NODE_OPTIONS:\s*NODE_WASM_FLAG/u,
  'alleen de runner-beheerde testomgeving geeft normale nested Node-processen de vlag');
  assert.match(runnerSource,
    /spawnSync\(process\.execPath,\s*\['--input-type=module', '--eval', NESTED_NODE_SELF_GATE_SOURCE\]/u,
  'de verplichte self-gate start een normale nested Node zonder expliciete argv-vlag');
  assert.match(runnerSource, /process\.env\.NODE_OPTIONS, NODE_WASM_FLAG/u);
  assert.match(runnerSource, /NESTED_NODE_FETCH_BODY/u);
  assert.match(runnerSource, /await globalThis\.fetch\(/u);
  assert.match(runnerSource, /await response\.text\(\)/u);
  assert.match(runnerSource, /assert\.equal\(response\.bodyUsed, true\)/u);
  assert.equal(runnerSource.includes(
    'Max address space\\\\s+6442450944\\\\s+6442450944\\\\s+bytes'), true);
});

test('statische bootstrap handhaaft externe rlimits en een harde wall-clock-timeout', {
  skip: alreadyIsolated && 'de compiler/outer watchdog hoort niet in de testjail',
}, (t) => {
  const { repository } = createCopiedRepository(t);
  const boundedBootstrap = path.join(repository, 'scripts', 'bounded-bootstrap');
  const compiled = compileBootstrap(boundedBootstrap, ['-DWALL_TIMEOUT_SECONDS=2']);
  assert.equal(compiled.status, 0, compiled.stderr || compiled.stdout);
  fs.chmodSync(boundedBootstrap, 0o755);
  const probe = path.join(repository, 'test', 'limit-and-timeout.test.js');
  write(probe, `
    import assert from 'node:assert/strict';
    import { spawnSync } from 'node:child_process';
    import fs from 'node:fs';
    import test from 'node:test';
    test('external limits are visible before untrusted test execution', () => {
      const limits = fs.readFileSync('/proc/self/limits', 'utf8');
      assert.match(limits, /^Max core file size\\s+0\\s+0\\s+bytes\\s*$/mu);
      assert.match(limits, /^Max open files\\s+512\\s+512\\s+files\\s*$/mu);
      assert.match(limits, /^Max processes\\s+256\\s+256\\s+processes\\s*$/mu);
      assert.match(limits, /^Max file size\\s+134217728\\s+134217728\\s+bytes\\s*$/mu);
      assert.match(limits, /^Max address space\\s+6442450944\\s+6442450944\\s+bytes\\s*$/mu);
      assert.match(limits, /^Max cpu time\\s+900\\s+900\\s+seconds\\s*$/mu);
      const child = spawnSync(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {
        stdio: 'ignore', timeout: 30_000,
      });
      assert.fail('nested process returned unexpectedly: ' + JSON.stringify(child));
    });
  `);
  const started = Date.now();
  const child = spawnSync(boundedBootstrap, ['test/limit-and-timeout.test.js'], {
    cwd: repository, env: { ...process.env }, encoding: 'utf8',
    timeout: 15_000, maxBuffer: 8 * 1024 * 1024,
  });
  const elapsed = Date.now() - started;
  assert.equal(child.status, 124, `${child.stderr}\n${child.stdout}`);
  assert.match(child.stderr, /harde timeout na 2 seconden/iu);
  assert.equal(elapsed >= 1_500 && elapsed < 8_000, true, `elapsed=${elapsed}`);
  assert.equal(child.signal, null);
});

test('aggregate cgroupgrenzen en private tmpfs staan buiten bereik van testcode', {
  skip: alreadyIsolated && 'de outer resourcegate is vóór de huidige jail geattesteerd',
}, (t) => {
  const { repository } = createCopiedRepository(t);
  const probe = path.join(repository, 'test', 'aggregate-resource-gate.test.js');
  write(probe, `
    import assert from 'node:assert/strict';
    import fs from 'node:fs';
    import test from 'node:test';
    test('aggregate resource authority', () => {
      assert.equal(process.env.EVA_ISOLATED_RESOURCE_GATE, '1');
      assert.match(fs.readFileSync('/proc/self/cgroup', 'utf8'),
        /^0::\\/.*bottie-isolated-tests-.*\\.service$/mu);
      const temporary = fs.statfsSync('/tmp');
      assert.equal(temporary.type, 0x01021994, '/tmp is geen private tmpfs');
      assert.equal(temporary.blocks * temporary.bsize <= 3 * 1024 ** 3, true);
      assert.throws(() => fs.statSync('/sys/fs/cgroup'), { code: 'ENOENT' });
      const limits = fs.readFileSync('/proc/self/limits', 'utf8');
      assert.match(limits, /^Max processes\\s+256\\s+256\\s+processes\\s*$/mu);
      assert.match(limits, /^Max file size\\s+134217728\\s+134217728\\s+bytes\\s*$/mu);
    });
  `);
  const child = runRunner(repository, ['test/aggregate-resource-gate.test.js']);
  assert.equal(child.status, 0, `${child.stderr}\n${child.stdout}`);
});

test('tree-budgetfout breekt de unit af en ruimt de externe private tmpfs op', {
  skip: alreadyIsolated && 'de treebudget-watchdog draait buiten de huidige jail',
}, (t) => {
  const { repository } = createCopiedRepository(t);
  const boundedBootstrap = path.join(repository, 'scripts', 'tree-budget-bootstrap');
  const compiled = compileBootstrap(boundedBootstrap, ['-DMAX_TREE_ENTRIES=10ULL']);
  assert.equal(compiled.status, 0, compiled.stderr || compiled.stdout);
  fs.chmodSync(boundedBootstrap, 0o755);
  const probe = path.join(repository, 'test', 'tree-budget.test.js');
  write(probe, `
    import test from 'node:test';
    test('must be stopped by outer tree budget', async () => {
      setInterval(() => {}, 1000);
      await new Promise(() => {});
    });
  `);
  const before = new Set(fs.readdirSync('/tmp').filter((name) =>
    name.startsWith('bottie-test-bootstrap-')));
  const child = spawnSync(boundedBootstrap, ['test/tree-budget.test.js'], {
    cwd: repository,
    env: { ...process.env },
    encoding: 'utf8',
    timeout: 15_000,
    maxBuffer: 8 * 1024 * 1024,
  });
  assert.equal(child.status, 125, `${child.stderr}\n${child.stdout}`);
  assert.match(child.stderr, /authorityroot bevat te veel entries/iu);
  const prefix = `bottie-isolated-tests-${process.getuid()}-${child.pid}-*`;
  assert.equal(activeResourceUnits(prefix), '');
  const leaked = fs.readdirSync('/tmp').filter((name) =>
    !before.has(name) && name.startsWith('bottie-test-bootstrap-'));
  assert.deepEqual(leaked, []);
});

test('SIGTERM doodt de childgroep en laat geen private authorityroot achter', {
  skip: alreadyIsolated && 'signaalafhandeling moet de outer bootstrap raken',
  timeout: 20_000,
}, async (t) => {
  const { repository } = createCopiedRepository(t);
  const probe = path.join(repository, 'test', 'signal-cleanup.test.js');
  write(probe, `
    import test from 'node:test';
    test('hang until outer termination', async () => {
      process.stdout.write('SIGNAL_PROBE_READY\\n');
      setInterval(() => {}, 1000);
      await new Promise(() => {});
    });
  `);
  const before = new Set(fs.readdirSync('/tmp').filter((name) =>
    name.startsWith('bottie-test-bootstrap-')));
  const child = spawn(path.join(repository, 'scripts', 'run-isolated-tests-bootstrap'), [
    'test/signal-cleanup.test.js',
  ], {
    cwd: repository,
    env: { ...process.env },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  child.stdout.on('data', (chunk) => { output += chunk; });
  child.stderr.on('data', (chunk) => { output += chunk; });
  t.after(() => {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    for (const name of fs.readdirSync('/tmp')) {
      if (!before.has(name) && name.startsWith('bottie-test-bootstrap-')) {
        fs.rmSync(path.join('/tmp', name), { recursive: true, force: true });
      }
    }
  });
  const readyDeadline = Date.now() + 10_000;
  while (!output.includes('SIGNAL_PROBE_READY') && Date.now() < readyDeadline) await delay(20);
  assert.match(output, /SIGNAL_PROBE_READY/u);
  assert.equal(child.kill('SIGTERM'), true);
  const result = await waitForChild(child);
  assert.equal(result.status === 143 || result.signal === 'SIGTERM', true,
    JSON.stringify({ result, output }));
  await delay(300);
  const leaked = fs.readdirSync('/tmp').filter((name) =>
    !before.has(name) && name.startsWith('bottie-test-bootstrap-'));
  assert.deepEqual(leaked, [], `authorityroot lekte na SIGTERM: ${leaked.join(', ')}`);
  const prefix = `bottie-isolated-tests-${process.getuid()}-${child.pid}-*`;
  assert.equal(activeResourceUnits(prefix), '');
});

test('onverwachte outer SIGKILL sluit de parent-death-pipe en beëindigt de unit', {
  skip: alreadyIsolated && 'de onverwachte outer stop moet buiten de jail worden veroorzaakt',
  timeout: 20_000,
}, async (t) => {
  const { repository } = createCopiedRepository(t);
  const probe = path.join(repository, 'test', 'parent-death-cleanup.test.js');
  write(probe, `
    import test from 'node:test';
    test('hang until outer parent death', async () => {
      process.stdout.write('PDEATH_PROBE_READY\\n');
      setInterval(() => {}, 1000);
      await new Promise(() => {});
    });
  `);
  const child = spawn(path.join(repository, 'scripts', 'run-isolated-tests-bootstrap'), [
    'test/parent-death-cleanup.test.js',
  ], {
    cwd: repository,
    env: { ...process.env },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  child.stdout.on('data', (chunk) => { output += chunk; });
  child.stderr.on('data', (chunk) => { output += chunk; });
  t.after(() => {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
  });
  const readyDeadline = Date.now() + 10_000;
  while (!output.includes('PDEATH_PROBE_READY') && Date.now() < readyDeadline) await delay(20);
  assert.match(output, /PDEATH_PROBE_READY/u);
  const prefix = `bottie-isolated-tests-${process.getuid()}-${child.pid}-*`;
  assert.notEqual(activeResourceUnits(prefix), '', 'transient unit was niet aantoonbaar actief');
  assert.equal(child.kill('SIGKILL'), true);
  const result = await waitForChild(child);
  assert.deepEqual(result, { status: null, signal: 'SIGKILL' });
  const deadline = Date.now() + 5_000;
  while (activeResourceUnits(prefix) !== '' && Date.now() < deadline) await delay(50);
  assert.equal(activeResourceUnits(prefix), '', `unit lekte na outer SIGKILL: ${output}`);
});

test('same-uid padvervanging vindt geen runnerpathname en kan de gepinde bytes niet wijzigen', {
  skip: alreadyIsolated && 'de pathname-race hoort buiten de jail',
  timeout: 20_000,
}, async (t) => {
  const { repository } = createCopiedRepository(t);
  const delayedBootstrap = path.join(repository, 'scripts', 'delayed-bootstrap');
  const compiled = compileBootstrap(delayedBootstrap,
    ['-DBOOTSTRAP_TEST_PRE_NODE_DELAY_MS=1200']);
  assert.equal(compiled.status, 0, compiled.stderr || compiled.stdout);
  fs.chmodSync(delayedBootstrap, 0o755);
  const probe = path.join(repository, 'test', 'same-uid-runner-fd.test.js');
  write(probe, `
    import assert from 'node:assert/strict';
    import fs from 'node:fs';
    import test from 'node:test';
    test('runner fd is gone before testcode', () => {
      assert.equal(fs.statSync('/proc/self/fd/0').isFile(), false);
      assert.doesNotMatch(fs.readlinkSync('/proc/self/fd/0'), /bottie-trusted-runner|memfd/iu);
      assert.equal(fs.existsSync(${JSON.stringify(path.join(repository, 'same-uid-pwned'))}), false);
    });
  `);
  const before = new Set(fs.readdirSync('/tmp').filter((name) =>
    name.startsWith('bottie-test-bootstrap-')));
  const child = spawn(delayedBootstrap, ['test/same-uid-runner-fd.test.js'], {
    cwd: repository, env: { ...process.env }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  child.stdout.on('data', (chunk) => { output += chunk; });
  child.stderr.on('data', (chunk) => { output += chunk; });
  let pathnameObserved = false;
  const observedPathnames = new Set();
  let sealedDescriptorObserved = false;
  let sealedMutationBlocked = false;
  const unitNeedle = `/bottie-isolated-tests-${process.getuid()}-${child.pid}-`;
  const started = Date.now();
  while (child.exitCode === null && child.signalCode === null && Date.now() - started < 10_000) {
    for (const name of fs.readdirSync('/tmp')) {
      if (before.has(name) || !name.startsWith('bottie-test-bootstrap-')) continue;
      const candidate = path.join('/tmp', name, 'trusted-runner.mjs');
      if (!fs.existsSync(candidate)) continue;
      pathnameObserved = true;
      observedPathnames.add(candidate);
      const size = fs.statSync(candidate).size;
      const attack = Buffer.from(`import fs from 'node:fs';\nfs.writeFileSync(${JSON.stringify(
        path.join(repository, 'same-uid-pwned'),
      )}, 'pwned');\nprocess.exit(0);\n`);
      assert.equal(attack.length <= size, true);
      const payload = Buffer.alloc(size, 0x20);
      attack.copy(payload);
      fs.chmodSync(candidate, 0o600);
      const descriptor = fs.openSync(candidate, 'r+');
      fs.writeSync(descriptor, payload, 0, payload.length, 0);
      fs.fsyncSync(descriptor);
      fs.closeSync(descriptor);
    }
    const processIds = fs.readdirSync('/proc').filter((name) => /^\d+$/u.test(name)).slice(0, 32_768);
    for (const processId of processIds) {
      let cgroup;
      try {
        cgroup = fs.readFileSync(path.join('/proc', processId, 'cgroup'), 'utf8');
      } catch {
        continue;
      }
      if (!cgroup.includes(unitNeedle)) continue;
      const processTmp = path.join('/proc', processId, 'root', 'tmp');
      try {
        for (const name of fs.readdirSync(processTmp).slice(0, 4096)) {
          if (before.has(name) || !name.startsWith('bottie-test-bootstrap-')) continue;
          const candidate = path.join(processTmp, name, 'trusted-runner.mjs');
          if (fs.existsSync(candidate)) {
            pathnameObserved = true;
            observedPathnames.add(candidate);
          }
        }
      } catch {
        // Een proces kan tussen de begrensde /proc-scan en inspectie verdwijnen.
      }
      let descriptors;
      try {
        descriptors = fs.readdirSync(path.join('/proc', processId, 'fd')).slice(0, 512);
      } catch {
        continue;
      }
      for (const descriptorName of descriptors) {
        const descriptorPath = path.join('/proc', processId, 'fd', descriptorName);
        let target;
        try {
          target = fs.readlinkSync(descriptorPath);
        } catch {
          continue;
        }
        if (!target.includes('memfd:bottie-trusted-runner.mjs')) continue;
        sealedDescriptorObserved = true;
        try {
          const attackDescriptor = fs.openSync(descriptorPath, 'r+');
          try {
            assert.throws(() => fs.writeSync(attackDescriptor, Buffer.from('pwn'), 0, 3, 0),
              (error) => error?.code === 'EPERM' || error?.code === 'EACCES');
            sealedMutationBlocked = true;
          } finally {
            fs.closeSync(attackDescriptor);
          }
        } catch (error) {
          if (error?.code === 'EPERM' || error?.code === 'EACCES') sealedMutationBlocked = true;
          else throw error;
        }
      }
    }
    await delay(2);
  }
  const result = await waitForChild(child);
  assert.equal(result.status, 0, `${output}\n${JSON.stringify(result)}`);
  assert.equal(pathnameObserved, false,
    `same uid vond runnerpathname(s): ${[...observedPathnames].join(', ')}`);
  assert.equal(sealedDescriptorObserved, true, 'same uid zag de gepinde memfd niet');
  assert.equal(sealedMutationBlocked, true, 'same uid kon de sealed runnerbytes muteren');
  assert.equal(fs.existsSync(path.join(repository, 'same-uid-pwned')), false);
});

test('releasecontract bindt exact artefact, bron en runner aan een unprivileged direct pad', () => {
  const contract = JSON.parse(fs.readFileSync(releaseManifest, 'utf8'));
  assert.equal(contract.schema, 1);
  assert.equal(contract.installPath,
    '/usr/local/libexec/eva/run-isolated-tests-bootstrap');
  assert.equal(contract.execution.runAsRoot, false);
  assert.equal(contract.execution.dedicatedUnprivilegedIdentity, true);
  assert.equal(contract.execution.invokeDirectlyOutsideNodeAndNpm, true);
  assert.equal(contract.integration.npmPackageScripts,
    'developer-alias-only-not-a-pre-node-trust-boundary');
  assert.equal(contract.integration.rootGate, 'separate-required');
  assert.equal(contract.claims.sameUidCoveragePostprocessing, false);
  assert.equal(contract.claims.normalNestedNodeAvailability, true);
  assert.equal(contract.claims.hostileTestEnvironmentWipeAvailability, false);
  assert.equal(contract.sha256.bootstrap, sha256(sourceBootstrap));
  assert.equal(contract.sha256.bootstrapSource, sha256(sourceBootstrapC));
  assert.equal(contract.sha256.runner, sha256(sourceRunner));
});

test('hostile loader- en Node-omgeving raakt raw Node maar niet de statische bootstrap', {
  skip: alreadyIsolated && 'de hostile pre-bootstrap RED hoort buiten de testjail',
}, (t) => {
  const { repository } = createCopiedRepository(t);
  const librarySource = path.join(repository, 'test', 'preload-attack.c');
  const library = path.join(repository, 'test', 'preload-attack.so');
  write(librarySource, `
    #include <stdio.h>
    #include <stdlib.h>
    __attribute__((constructor)) static void attack(void) {
      const char *filename = getenv("BOTTIE_ATTACK_SENTINEL");
      if (filename == NULL) return;
      FILE *stream = fopen(filename, "a");
      if (stream != NULL) { fputs("LD_PRELOAD\\n", stream); fclose(stream); }
    }
  `);
  const compiled = spawnSync(compiler, [
    '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-Werror', '-o', library, librarySource,
  ], { cwd: repository, encoding: 'utf8' });
  assert.equal(compiled.status, 0, compiled.stderr || compiled.stdout);
  const nodeAttack = path.join(repository, 'test', 'node-options-attack.cjs');
  write(nodeAttack, `
    require('node:fs').appendFileSync(process.env.BOTTIE_ATTACK_SENTINEL, 'NODE_OPTIONS\\n');
  `);
  const probe = path.join(repository, 'test', 'bootstrap-environment.test.js');
  write(probe, `
    import assert from 'node:assert/strict';
    import test from 'node:test';
    test('hostile bootstrap environment is absent', () => {
      for (const name of [
        'LD_PRELOAD', 'LD_LIBRARY_PATH', 'NODE_PATH', 'NODE_V8_COVERAGE',
        'GCC_EXEC_PREFIX', 'COMPILER_PATH', 'BOTTIE_ATTACK_SENTINEL',
      ]) assert.equal(process.env[name], undefined, name);
      assert.equal(process.env.NODE_OPTIONS, '--disable-wasm-trap-handler');
    });
  `);
  const redSentinel = path.join(repository, 'raw-node-attack-proof');
  const redCoverage = path.join(repository, 'raw-node-v8-coverage');
  fs.mkdirSync(redCoverage, { mode: 0o700 });
  const hostile = {
    ...process.env,
    BOTTIE_ATTACK_SENTINEL: redSentinel,
    LD_PRELOAD: library,
    LD_LIBRARY_PATH: path.join(repository, 'attacker-libraries'),
    NODE_OPTIONS: `--require=${nodeAttack}`,
    NODE_PATH: path.join(repository, 'attacker-node-path'),
    NODE_V8_COVERAGE: redCoverage,
    GCC_EXEC_PREFIX: path.join(repository, 'attacker-gcc'),
    COMPILER_PATH: path.join(repository, 'attacker-compiler'),
    PATH: path.join(repository, 'attacker-path'),
  };
  const raw = spawnSync(process.execPath, [sourceRunner, 'test/bootstrap-environment.test.js'], {
    cwd: repository, env: hostile, encoding: 'utf8', maxBuffer: 1024 * 1024,
  });
  assert.notEqual(raw.status, 0);
  assert.match(fs.readFileSync(redSentinel, 'utf8'), /LD_PRELOAD[\s\S]*NODE_OPTIONS/u);
  assert.equal(fs.readdirSync(redCoverage).some((name) => name.endsWith('.json')), true);

  const greenSentinel = path.join(repository, 'static-bootstrap-must-not-touch');
  const greenCoverage = path.join(repository, 'static-bootstrap-must-not-cover-here');
  fs.mkdirSync(greenCoverage, { mode: 0o700 });
  const secured = runRunner(repository, ['test/bootstrap-environment.test.js'], {
    ...hostile,
    BOTTIE_ATTACK_SENTINEL: greenSentinel,
    NODE_V8_COVERAGE: greenCoverage,
  });
  assert.equal(secured.status, 0, `${secured.stderr}\n${secured.stdout}`);
  assert.equal(fs.existsSync(greenSentinel), false);
  assert.deepEqual(fs.readdirSync(greenCoverage), []);
});

test('runner geeft ieder testbestand een verse /tmp-root en verbergt copied-repo data', {
  skip: alreadyIsolated && 'de canonical runner heeft deze buitenste fixture al geleverd',
}, (t) => {
  const { repository, sentinel } = createCopiedRepository(t);
  const first = path.join(repository, 'test', 'first.test.js');
  const second = path.join(repository, 'test', 'second.test.js');
  write(first, probeSource(repository, 'first'));
  write(second, probeSource(repository, 'second'));

  const child = runRunner(repository, ['test/first.test.js', 'test/second.test.js']);
  assert.equal(child.status, 0, child.stderr || child.stdout);
  const records = [...child.stdout.matchAll(/ISOLATION_RECORD (\{[^\n]+\})/gu)]
    .map((match) => JSON.parse(match[1]));
  if (records.length !== 2) {
    throw new Error(`runneroutput zonder twee isolation records: ${JSON.stringify(child)}`);
  }
  assert.deepEqual(records.map(({ label }) => label).sort(), ['first', 'second'], child.stdout);
  assert.equal(new Set(records.map(({ dataDir }) => dataDir)).size, 2);
  for (const { dataDir } of records) assert.equal(fs.existsSync(dataDir), false);
  assert.equal(fs.readFileSync(sentinel, 'utf8'), 'copied-production-secret\n');
  assert.deepEqual(fs.readdirSync(path.dirname(sentinel)), ['private-production-sentinel']);
});

test('interne fasen zijn niet rechtstreeks bruikbaar en raw node blijft buiten scope', {
  skip: alreadyIsolated && 'geneste runnerstart is door de actieve seccompgrens verboden',
}, (t) => {
  const { repository, sentinel } = createCopiedRepository(t);
  const runner = path.join(repository, 'scripts', 'run-isolated-tests.mjs');
  const internal = spawnSync(process.execPath, [
    '--disable-wasm-trap-handler', runner, '--internal-verify',
  ], {
    cwd: repository, encoding: 'utf8',
  });
  assert.notEqual(internal.status, 0);
  assert.match(internal.stderr, /interne runnerfase geweigerd/iu);
  const directRunner = spawnSync(process.execPath, [
    '--disable-wasm-trap-handler', runner, 'test/no-test.test.js',
  ], {
    cwd: repository, encoding: 'utf8',
  });
  assert.notEqual(directRunner.status, 0);
  assert.match(directRunner.stderr, /statische bootstrap ontbreekt/iu);

  const raw = spawnSync(process.execPath, ['--input-type=module', '-e', `
    import fs from 'node:fs';
    process.stdout.write(fs.readFileSync(${JSON.stringify(sentinel)}, 'utf8'));
  `], { cwd: repository, encoding: 'utf8' });
  assert.equal(raw.status, 0, raw.stderr);
  assert.equal(raw.stdout, 'copied-production-secret\n');
});

test('runner weigert een symlink als repository-data mounttarget', {
  skip: alreadyIsolated && 'mounttargetselectie gebeurde al vóór de huidige jail',
}, (t) => {
  const { repository, sentinel } = createCopiedRepository(t, { symlinkData: true });
  const probe = path.join(repository, 'test', 'probe.test.js');
  write(probe, "import test from 'node:test'; test('mag niet starten',()=>{});\n");
  const child = runRunner(repository, ['test/probe.test.js']);
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /data.*(?:symbolic link|symlink|mount target)/iu);
  assert.equal(fs.readFileSync(sentinel, 'utf8'), 'copied-production-secret\n');
});

test('statische bootstrap weigert een runner die van zijn ingebedde SHA-256 afwijkt', {
  skip: alreadyIsolated && 'de canonical bootstrap heeft zijn runnerhash al geattesteerd',
}, (t) => {
  const { repository, sentinel } = createCopiedRepository(t);
  const probe = path.join(repository, 'test', 'never-start.test.js');
  write(probe, "import test from 'node:test'; test('mag niet starten',()=>{});\n");
  fs.appendFileSync(path.join(repository, 'scripts', 'run-isolated-tests.mjs'), '\n');
  const child = runRunner(repository, ['test/never-start.test.js']);
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /gebonden SHA-256/iu);
  assert.equal(fs.readFileSync(sentinel, 'utf8'), 'copied-production-secret\n');
});

test('runner weigert FIFO, socket en hardlink vóór ze een bindbron kunnen worden', {
  skip: alreadyIsolated && 'de canonical snapshot is al recursief geattesteerd',
}, async (t) => {
  const executeUnsafeFixture = (repository) => {
    const probe = path.join(repository, 'test', 'never-start.test.js');
    write(probe, "import test from 'node:test'; test('mag niet starten',()=>{});\n");
    return runRunner(repository, ['test/never-start.test.js']);
  };

  {
    const { repository, sentinel } = createCopiedRepository(t);
    const fifo = path.join(repository, 'test', 'unsafe-fifo');
    const created = spawnSync('/usr/bin/mkfifo', [fifo], { encoding: 'utf8' });
    assert.equal(created.status, 0, created.stderr);
    const child = executeUnsafeFixture(repository);
    assert.notEqual(child.status, 0);
    assert.match(child.stderr, /snapshotbron bevat speciaal bestand/iu);
    assert.equal(fs.readFileSync(sentinel, 'utf8'), 'copied-production-secret\n');
  }

  {
    const { repository, sentinel } = createCopiedRepository(t);
    const first = path.join(repository, 'test', 'unsafe-hardlink-a');
    const second = path.join(repository, 'test', 'unsafe-hardlink-b');
    write(first, 'hardlinked');
    fs.linkSync(first, second);
    const child = executeUnsafeFixture(repository);
    assert.notEqual(child.status, 0);
    assert.match(child.stderr, /snapshotbestand is geen begrensd enkelvoudig bestand/iu);
    assert.equal(fs.readFileSync(sentinel, 'utf8'), 'copied-production-secret\n');
  }

  {
    const { repository, sentinel } = createCopiedRepository(t);
    const socketPath = path.join(repository, 'test', 'unsafe.sock');
    const net = await import('node:net');
    const server = net.createServer();
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(socketPath, resolve);
    });
    try {
      const child = executeUnsafeFixture(repository);
      assert.notEqual(child.status, 0);
      assert.match(child.stderr, /snapshotbron bevat speciaal bestand/iu);
      assert.equal(fs.readFileSync(sentinel, 'utf8'), 'copied-production-secret\n');
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  }
});

test('package developer-alias gebruikt de runner en claimt geen pre-Node authority', () => {
  const packageJson = JSON.parse(fs.readFileSync(path.join(projectRoot, 'package.json'), 'utf8'));
  assert.equal(packageJson.scripts['test:all'],
    './scripts/run-isolated-tests-bootstrap test/*.test.js test/*.test.mjs scripts/*.test.mjs');
  assert.equal(packageJson.engines.node, '>=22.22.0');
  assert.equal(packageJson.scripts.coverage,
    './scripts/run-isolated-tests-bootstrap --coverage test/*.test.js test/*.test.mjs scripts/*.test.mjs');
  assert.equal(packageJson.scripts['architecture:check'],
    'node scripts/generate-runtime-contract-map.mjs --check && node scripts/generate-repository-inventory.mjs --check');
  assert.equal(packageJson.scripts.architecture,
    'node scripts/generate-runtime-contract-map.mjs && node scripts/generate-repository-inventory.mjs');
  const packageLock = JSON.parse(fs.readFileSync(path.join(projectRoot, 'package-lock.json'), 'utf8'));
  assert.equal(packageLock.packages[''].engines.node, '>=22.22.0');
});

test('runner maakt benchmark- en native-inputs werkelijk zichtbaar in iedere jail', {
  skip: alreadyIsolated && 'de buitenste runner heeft de repositorysnapshot al vastgelegd',
}, (t) => {
  const { repository } = createCopiedRepository(t);
  for (const [directory, filename, contents] of [
    ['benchmark', 'catalog.json', '{"contract":"benchmark-visible"}\n'],
    ['native', 'broker.c', '/* native-visible */\n'],
  ]) {
    fs.mkdirSync(path.join(repository, directory), { mode: 0o700 });
    write(path.join(repository, directory, filename), contents);
  }
  const probe = path.join(repository, 'test', 'repository-inputs.test.js');
  write(probe, `
    import assert from 'node:assert/strict';
    import fs from 'node:fs';
    import test from 'node:test';
    test('repository inputs', () => {
      assert.equal(JSON.parse(fs.readFileSync('benchmark/catalog.json', 'utf8')).contract,
        'benchmark-visible');
      assert.equal(fs.readFileSync('native/broker.c', 'utf8'), '/* native-visible */\\n');
    });
  `);
  const child = runRunner(repository, ['test/repository-inputs.test.js']);
  assert.equal(child.status, 0, child.stderr || child.stdout);
});

test('runner scheidt hostnetwerk, behoudt child-loopback en wist secrets', {
  skip: alreadyIsolated && 'de buitenste runner heeft de netwerknamespace al geleverd',
}, async (t) => {
  const hostPort = await startHostLoopbackSentinel(t);
  const { repository } = createCopiedRepository(t);
  const probe = path.join(repository, 'test', 'network-boundary.test.js');
  write(probe, `
    import assert from 'node:assert/strict';
    import fs from 'node:fs';
    import net from 'node:net';
    import test from 'node:test';

    const connect = (host, port, timeout = 500) => new Promise((resolve) => {
      const socket = net.createConnection({ host, port });
      let data = '';
      const done = (connected, error = null) => {
        socket.destroy();
        resolve({ connected, data, error: error?.code || null });
      };
      socket.setEncoding('utf8');
      socket.on('data', (chunk) => { data += chunk; });
      socket.once('end', () => done(true));
      socket.once('error', (error) => done(false, error));
      socket.setTimeout(timeout, () => done(false, { code: 'TIMEOUT' }));
    });

    test('network boundary', async () => {
      const hostSentinel = await connect('127.0.0.1', ${hostPort});
      assert.equal(hostSentinel.connected, false,
        'testcode bereikte een uitsluitend op host-loopback luisterende service');
      assert.doesNotMatch(hostSentinel.data, /HOST_LOOPBACK_SENTINEL/u);

      assert.equal(process.env.BOTTIE_RUNNER_FAKE_SECRET, undefined);
      assert.equal(process.env.OPENAI_API_KEY, undefined);
      assert.equal(process.env.HA_URL, undefined);
      assert.equal(process.env.TELEGRAM_BOT_TOKEN, undefined);

      const interfaces = fs.readFileSync('/proc/net/dev', 'utf8').split('\\n')
        .slice(2).map((line) => line.split(':')[0].trim()).filter(Boolean);
      assert.deepEqual(interfaces, ['lo']);
      const routes = fs.readFileSync('/proc/net/route', 'utf8').trim().split('\\n').slice(1)
        .filter((line) => line.trim());
      assert.deepEqual(routes, []);

      const external = await connect('1.1.1.1', 443);
      assert.equal(external.connected, false, 'testcode bereikte een extern netwerkadres');

      const localServer = net.createServer((socket) => socket.end('CHILD_LOCAL_OK'));
      await new Promise((resolve, reject) => {
        localServer.once('error', reject);
        localServer.listen(0, '127.0.0.1', resolve);
      });
      const local = await connect('127.0.0.1', localServer.address().port);
      await new Promise((resolve) => localServer.close(resolve));
      assert.equal(local.connected, true);
      assert.equal(local.data, 'CHILD_LOCAL_OK');
    });
  `);
  const child = runRunner(repository, ['test/network-boundary.test.js'], {
    BOTTIE_RUNNER_FAKE_SECRET: 'must-never-reach-test',
    OPENAI_API_KEY: 'fake-openai-secret',
    HA_URL: 'http://127.0.0.1:8123',
    TELEGRAM_BOT_TOKEN: 'fake-telegram-token',
  });
  assert.equal(child.status, 0, `${child.stderr}\n${child.stdout}`);
});

test('seccomp blokkeert ook iedere x32-gecodeerde syscall vóór de kernel', {
  skip: alreadyIsolated && 'de actieve seccompfilter blokkeert compilatie/geneste runnerstart',
}, (t) => {
  const { repository } = createCopiedRepository(t);
  const executable = compileX32Probe(repository);
  const raw = spawnSync(executable, [], { cwd: repository, encoding: 'utf8' });
  assert.equal(raw.status, 0, raw.stderr);
  assert.notEqual(raw.stdout.trim(), '-1:1',
    'raw hostpad leverde onverwacht al de runner-specifieke EPERM-garantie');
  const probe = path.join(repository, 'test', 'x32-boundary.test.js');
  write(probe, `
    import assert from 'node:assert/strict';
    import { spawnSync } from 'node:child_process';
    import test from 'node:test';
    test('x32 syscall bit is denied by seccomp', () => {
      const child = spawnSync(${JSON.stringify(executable)}, [], { encoding: 'utf8' });
      assert.equal(child.status, 0, child.stderr);
      assert.equal(child.stdout.trim(), '-1:1');
    });
  `);
  const child = runRunner(repository, ['test/x32-boundary.test.js']);
  assert.equal(child.status, 0, `${child.stderr}\n${child.stdout}`);
});

test('expliciete coverage mode draagt alleen gevalideerde child-profielen over', {
  skip: alreadyIsolated && 'coverage-uitvoer wordt door de buitenste runner beheerd',
}, (t) => {
  const { repository } = createCopiedRepository(t);
  installFakeCoverageReporter(repository);
  const probe = path.join(repository, 'test', 'coverage-child.test.js');
  write(probe, `
    import assert from 'node:assert/strict';
    import fs from 'node:fs';
    import test from 'node:test';
    test('coverage child', () => {
      assert.equal(process.env.BOTTIE_COVERAGE_SECRET, undefined);
      assert.match(process.env.NODE_V8_COVERAGE,
        /^\\/tmp\\/bottie-test-bootstrap-[A-Za-z0-9]{6}\\/test-[^/]+\\/v8-coverage$/u);
      assert.throws(() => fs.statSync(${JSON.stringify(path.join(repository, 'coverage'))}),
        { code: 'ENOENT' });
      assert.equal(21 * 2, 42);
    });
  `);
  const hostileCoverage = path.join(repository, 'attacker-chosen-v8-output');
  fs.mkdirSync(hostileCoverage, { mode: 0o700 });
  const child = runRunner(repository, ['--coverage', 'test/coverage-child.test.js'], {
    BOTTIE_COVERAGE_SECRET: 'must-not-pass',
    NODE_V8_COVERAGE: hostileCoverage,
  });
  assert.equal(child.status, 0, `${child.stderr}\n${child.stdout}`);
  assert.deepEqual(fs.readdirSync(hostileCoverage), []);
  const proof = JSON.parse(fs.readFileSync(
    path.join(repository, 'coverage', 'coverage-report-proof.json'), 'utf8',
  ));
  assert.equal(proof.profileCount > 0, true);
  assert.equal(proof.urls.some((url) => url.endsWith('/test/coverage-child.test.js')), true);
  assert.equal(fs.existsSync(path.join(repository, 'coverage', 'tmp')), false);
});

test('huidige echte eva.db blijft vanuit een runner-test volledig onzichtbaar', {
  skip: alreadyIsolated && 'de huidige test draait zelf al achter de bewezen shadow',
}, (t) => {
  const realData = path.join(projectRoot, 'data');
  const realDataStat = fs.lstatSync(realData);
  assert.equal(realDataStat.isDirectory(), true);
  assert.equal(realDataStat.isSymbolicLink(), false);
  const sentinel = path.join(realData, 'eva.db');
  const sentinelStat = fs.lstatSync(sentinel);
  assert.equal(sentinelStat.isFile(), true);
  const sentinelIdentity = `${sentinelStat.dev}:${sentinelStat.ino}`;
  const probe = path.join(projectRoot, 'test', `runner-host-probe-${process.pid}.test.js`);
  write(probe, `
    import assert from 'node:assert/strict';
    import fs from 'node:fs';
    import path from 'node:path';
    import test from 'node:test';
    const sentinel = path.join(${JSON.stringify(realData)}, 'eva.db');
    test('host data is shadowed', () => {
      assert.throws(() => fs.readFileSync(sentinel, 'utf8'), { code: 'ENOENT' });
      assert.throws(() => fs.readFileSync('/proc/1/root' + sentinel, 'utf8'), { code: 'ENOENT' });
      assert.deepEqual(fs.readdirSync(${JSON.stringify(realData)}), []);
    });
  `);
  t.after(() => {
    fs.rmSync(probe, { force: true });
  });
  const child = runExistingRunner([path.relative(projectRoot, probe)]);
  assert.equal(child.status, 0, child.stderr || child.stdout);
  const after = fs.lstatSync(sentinel);
  assert.equal(`${after.dev}:${after.ino}`, sentinelIdentity);
});

test('canonical uitvoering bewijst de actieve jail zonder geneste namespace', {
  skip: !alreadyIsolated && 'deze asserties horen binnen de canonical runner',
}, () => {
  const dataMount = path.join(projectRoot, 'data');
  const sentinel = path.join(dataMount, 'eva.db');
  assert.throws(() => fs.readFileSync(sentinel, 'utf8'), { code: 'ENOENT' });
  assert.throws(() => fs.readFileSync('/proc/1/root' + sentinel, 'utf8'), { code: 'ENOENT' });
  assert.deepEqual(fs.readdirSync(dataMount), []);
  assert.match(fs.readFileSync('/proc/self/status', 'utf8'), /^NoNewPrivs:\s+1$/mu);
  const interfaces = fs.readFileSync('/proc/net/dev', 'utf8').split('\n').slice(2)
    .map((line) => line.split(':')[0].trim()).filter(Boolean);
  assert.deepEqual(interfaces, ['lo']);
  assert.deepEqual(fs.readFileSync('/proc/net/route', 'utf8').trim().split('\n').slice(1)
    .filter((line) => line.trim()), []);
  assert.equal(process.env.OPENAI_API_KEY, undefined);
  assert.equal(process.env.HA_TOKEN, undefined);
  assert.equal(process.env.TELEGRAM_BOT_TOKEN, undefined);
});
