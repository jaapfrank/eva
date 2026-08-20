import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import test, { after, before } from 'node:test';

const projectRoot = path.resolve(import.meta.dirname, '..');
const source = path.join(projectRoot, 'native', 'workspace_writer_broker.c');
const compiler = '/usr/bin/x86_64-linux-gnu-gcc-13';
const requestMagic = Buffer.from('EVWWREQ1', 'ascii');
const receiptMagic = Buffer.from('EVWWRCP1', 'ascii');
const journalMagic = Buffer.from('EVWWJRN1', 'ascii');
const requestHeaderBytes = 90;
const receiptPrefixBytes = 131;
const journalPrefixBytes = 17;
const journalChecksumBytes = 32;
const preparePayloadPrefixBytes = 154;
const effects = Object.freeze({ 1: 'created', 3: 'noop' });
const suiteRoot = fs.mkdtempSync('/tmp/eva-workspace-writer-native-');
const binary = path.join(suiteRoot, 'workspace-writer-broker');
const binaryB = path.join(suiteRoot, 'workspace-writer-broker-b');
const phaseBarrierBinary = path.join(suiteRoot, 'workspace-writer-broker-phase-barriers');
const sanitizerBinary = path.join(suiteRoot, 'workspace-writer-broker-sanitized');

fs.chmodSync(suiteRoot, 0o700);

function sha256(value) {
  return createHash('sha256').update(value).digest();
}

function operationId(label) {
  return sha256(Buffer.from(`operation:${label}`, 'utf8'));
}

function request({ id, generation, target, content }) {
  const targetBytes = Buffer.from(target, 'ascii');
  const body = Buffer.isBuffer(content) ? content : Buffer.from(content, 'utf8');
  const header = Buffer.alloc(requestHeaderBytes);
  requestMagic.copy(header, 0);
  operationId(id).copy(header, 8);
  header.writeBigUInt64BE(BigInt(generation), 40);
  header.writeUInt16BE(targetBytes.length, 48);
  header.writeBigUInt64BE(BigInt(body.length), 50);
  sha256(body).copy(header, 58);
  return Buffer.concat([header, targetBytes, body]);
}

function parseReceipts(output) {
  const receipts = [];
  let offset = 0;
  while (offset < output.length) {
    assert.ok(output.subarray(offset, offset + 8).equals(receiptMagic),
      `ongeldige receiptmagic op byte ${offset}`);
    assert.ok(output.length - offset >= receiptPrefixBytes + 32, 'afgekapt receiptprefix');
    const effect = effects[output[offset + 8]];
    assert.ok(effect, `onbekend receipteffect ${output[offset + 8]}`);
    const generationBefore = Number(output.readBigUInt64BE(offset + 9));
    const generationAfter = Number(output.readBigUInt64BE(offset + 17));
    const operation = output.subarray(offset + 25, offset + 57).toString('hex');
    const requestSha256 = output.subarray(offset + 57, offset + 89).toString('hex');
    const contentSha256 = output.subarray(offset + 89, offset + 121).toString('hex');
    const targetLength = output.readUInt16BE(offset + 121);
    const contentLength = Number(output.readBigUInt64BE(offset + 123));
    const bodyEnd = offset + receiptPrefixBytes + targetLength;
    const end = bodyEnd + 32;
    assert.ok(end <= output.length, 'afgekapt receipt');
    const target = output.subarray(offset + receiptPrefixBytes, bodyEnd).toString('ascii');
    const checksum = output.subarray(bodyEnd, end);
    assert.ok(checksum.equals(sha256(output.subarray(offset, bodyEnd))),
      'receiptchecksum bindt niet exact aan de canonieke bytes');
    receipts.push({
      bytes: Buffer.from(output.subarray(offset, end)), effect,
      generationBefore, generationAfter, operation,
      requestSha256, contentSha256, contentLength, target,
    });
    offset = end;
  }
  return receipts;
}

function privateFixture(label) {
  const base = fs.mkdtempSync(path.join(suiteRoot, `${label}-`));
  const root = path.join(base, 'workspace');
  const journal = path.join(base, 'journal');
  fs.mkdirSync(root, { mode: 0o700 });
  fs.mkdirSync(journal, { mode: 0o700 });
  return { base, root, journal };
}

function brokerEnvironment(overrides = {}) {
  return Object.freeze({
    LANG: 'C', LC_ALL: 'C', PATH: '/usr/bin:/bin', TMPDIR: suiteRoot, TZ: 'UTC', ...overrides,
  });
}

function runBrokerDirectories(root, journal, input, options = {}) {
  const rootDescriptor = fs.openSync(root, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY);
  const journalDescriptor = fs.openSync(
    journal, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY,
  );
  try {
    return spawnSync(options.executable ?? binary, [], {
      cwd: suiteRoot, env: brokerEnvironment(options.env), input,
      stdio: ['pipe', 'pipe', 'pipe', rootDescriptor, journalDescriptor],
      encoding: null, timeout: options.timeout ?? 10_000,
      maxBuffer: 4 * 1024 * 1024,
    });
  } finally {
    fs.closeSync(journalDescriptor);
    fs.closeSync(rootDescriptor);
  }
}

function runBroker(fixture, input, options = {}) {
  return runBrokerDirectories(fixture.root, fixture.journal, input, options);
}

function startBroker(fixture, input, options = {}) {
  const rootDescriptor = fs.openSync(fixture.root, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY);
  const journalDescriptor = fs.openSync(
    fixture.journal, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY,
  );
  const child = spawn(options.executable ?? binary, [], {
    cwd: suiteRoot, env: brokerEnvironment(options.env),
    stdio: ['pipe', 'pipe', 'pipe', rootDescriptor, journalDescriptor],
  });
  fs.closeSync(journalDescriptor);
  fs.closeSync(rootDescriptor);
  const stdout = [];
  const stderr = [];
  child.stdout.on('data', (chunk) => stdout.push(chunk));
  child.stderr.on('data', (chunk) => stderr.push(chunk));
  const closed = new Promise((resolve) => child.once('close', (status, signal) => resolve({
    status, signal, stdout: Buffer.concat(stdout), stderr: Buffer.concat(stderr),
  })));
  child.stdin.end(input);
  return { child, closed };
}

function temporaryName(id) {
  return `.eva-writer-${operationId(id).toString('hex')}`;
}

function processIsStopped(processId) {
  try {
    return /^State:\s+T/mu.test(fs.readFileSync(`/proc/${processId}/status`, 'utf8'));
  } catch {
    return false;
  }
}

async function startStoppedAtPhase(fixture, input, phase) {
  const running = startBroker(fixture, input, {
    executable: phaseBarrierBinary,
    env: { EVWW_TEST_STOP_PHASE: phase },
  });
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline
      && running.child.exitCode == null && running.child.signalCode == null
      && !processIsStopped(running.child.pid)) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  if (!processIsStopped(running.child.pid)) {
    if (running.child.exitCode == null && running.child.signalCode == null) {
      process.kill(running.child.pid, 'SIGKILL');
    }
    const result = await running.closed;
    assert.fail(`fasebarrière ${phase} niet bereikt: ${result.stderr.toString('utf8')}`);
  }
  return running;
}

function assertCleanFailure(result, pattern) {
  assert.notEqual(result.status, 0, result.stderr?.toString('utf8'));
  assert.equal(result.signal, null);
  assert.match(result.stderr.toString('utf8'), pattern);
  assert.equal(result.stdout.length, 0);
}

function journalRecord(bytes, offset = 0) {
  assert.ok(bytes.subarray(offset, offset + 8).equals(journalMagic));
  assert.ok(bytes.length - offset >= journalPrefixBytes + journalChecksumBytes,
    'afgekapt journalrecord');
  const type = String.fromCharCode(bytes[offset + 8]);
  const payloadLength = Number(bytes.readBigUInt64BE(offset + 9));
  const payloadStart = offset + journalPrefixBytes;
  const payloadEnd = payloadStart + payloadLength;
  const end = payloadEnd + journalChecksumBytes;
  assert.ok(end <= bytes.length, 'afgekapt journalpayload');
  assert.ok(bytes.subarray(payloadEnd, end).equals(sha256(bytes.subarray(offset, payloadEnd))),
    'journalchecksum bindt niet exact aan type, lengte en payload');
  return { type, payload: bytes.subarray(payloadStart, payloadEnd), end };
}

function journalRecordEnd(bytes, offset = 0) {
  return journalRecord(bytes, offset).end;
}

function completeJournalRecords(file) {
  if (!fs.existsSync(file)) return [];
  const bytes = fs.readFileSync(file);
  const records = [];
  let offset = 0;
  while (bytes.length - offset >= journalPrefixBytes + journalChecksumBytes) {
    const payloadLength = bytes.readBigUInt64BE(offset + 9);
    if (payloadLength > BigInt(Number.MAX_SAFE_INTEGER)) break;
    const end = offset + journalPrefixBytes + Number(payloadLength) + journalChecksumBytes;
    if (end > bytes.length) break;
    try {
      const record = journalRecord(bytes, offset);
      records.push(record);
      offset = record.end;
    } catch { break; }
  }
  return records;
}

function assertStaticAmd64Elf(file) {
  const bytes = fs.readFileSync(file);
  assert.ok(bytes.subarray(0, 4).equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46])));
  assert.equal(bytes[4], 2, 'broker-ELF is niet 64-bit');
  assert.equal(bytes[5], 1, 'broker-ELF is niet little-endian');
  assert.equal(bytes.readUInt16LE(16), 2, 'broker-ELF is niet ET_EXEC');
  assert.equal(bytes.readUInt16LE(18), 62, 'broker-ELF is niet x86-64');
  const programOffset = Number(bytes.readBigUInt64LE(32));
  const programEntryBytes = bytes.readUInt16LE(54);
  const programCount = bytes.readUInt16LE(56);
  const types = [];
  for (let index = 0; index < programCount; index += 1) {
    types.push(bytes.readUInt32LE(programOffset + (index * programEntryBytes)));
  }
  assert.equal(types.includes(2), false, 'statische broker heeft onverwacht PT_DYNAMIC');
  assert.equal(types.includes(3), false, 'statische broker heeft onverwacht PT_INTERP');
}

before(() => {
  assert.match(suiteRoot, /^\/tmp\/eva-workspace-writer-native-[A-Za-z0-9]+$/u);
  assert.equal(fs.statSync(suiteRoot).mode & 0o777, 0o700,
    'de native suite moet werkelijk onder een private /tmp-wortel draaien');
  assert.equal(fs.existsSync(source), true, 'de native brokerbron ontbreekt');
  const releaseFlags = [
    '-std=c17', '-O2', '-pipe', '-Wall', '-Wextra', '-Werror', '-Wconversion',
    '-Wshadow', '-Wformat=2', '-Wstrict-prototypes', '-Wmissing-prototypes',
    '-fno-common', '-fstack-protector-strong', '-D_FORTIFY_SOURCE=3',
    '-static', '-no-pie', '-Wl,-z,relro,-z,now,-z,noexecstack',
  ];
  for (const output of [binary, binaryB]) {
    const compiled = spawnSync(compiler, [...releaseFlags, '-o', output, source], {
      cwd: suiteRoot, env: brokerEnvironment(), encoding: 'utf8', timeout: 30_000,
    });
    assert.equal(compiled.status, 0, compiled.stderr || compiled.stdout);
    fs.chmodSync(output, 0o500);
    assertStaticAmd64Elf(output);
  }
  assert.ok(fs.readFileSync(binary).equals(fs.readFileSync(binaryB)),
    'twee schone releasebuilds zijn niet bytegelijk');
  fs.chmodSync(binary, 0o500);
  const raceCompiled = spawnSync(compiler, [
    '-std=c17', '-O2', '-pipe', '-Wall', '-Wextra', '-Werror', '-Wconversion',
    '-Wshadow', '-Wformat=2', '-Wstrict-prototypes', '-Wmissing-prototypes',
    '-fno-common', '-fstack-protector-strong', '-D_FORTIFY_SOURCE=3',
    '-DEVWW_TEST_PHASE_BARRIERS',
    '-static', '-no-pie', '-Wl,-z,relro,-z,now,-z,noexecstack',
    '-o', phaseBarrierBinary, source,
  ], {
    cwd: suiteRoot, env: brokerEnvironment(), encoding: 'utf8', timeout: 30_000,
  });
  assert.equal(raceCompiled.status, 0, raceCompiled.stderr || raceCompiled.stdout);
  fs.chmodSync(phaseBarrierBinary, 0o500);
  assertStaticAmd64Elf(phaseBarrierBinary);

  const analyzed = spawnSync(compiler, [
    '-std=c17', '-O0', '-Wall', '-Wextra', '-Werror', '-Wconversion', '-Wshadow',
    '-Wformat=2', '-Wstrict-prototypes', '-Wmissing-prototypes', '-fanalyzer',
    '-fsyntax-only', source,
  ], { cwd: suiteRoot, env: brokerEnvironment(), encoding: 'utf8', timeout: 60_000 });
  assert.equal(analyzed.status, 0, analyzed.stderr || analyzed.stdout);

  const sanitized = spawnSync(compiler, [
    '-std=c17', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wconversion', '-Wshadow',
    '-Wformat=2', '-Wstrict-prototypes', '-Wmissing-prototypes',
    '-fno-omit-frame-pointer', '-fsanitize=address,undefined',
    '-o', sanitizerBinary, source,
  ], { cwd: suiteRoot, env: brokerEnvironment(), encoding: 'utf8', timeout: 30_000 });
  assert.equal(sanitized.status, 0, sanitized.stderr || sanitized.stdout);
  fs.chmodSync(sanitizerBinary, 0o500);
});

after(() => fs.rmSync(suiteRoot, { recursive: true, force: true }));

test('canonieke create en noop zijn monotoon; replace wordt vóór journal/effect geweigerd', () => {
  const fixture = privateFixture('state-machine');
  const first = request({ id: 'create', generation: 0, target: 'a/result.txt', content: 'een\n' });
  fs.mkdirSync(path.join(fixture.root, 'a'), { mode: 0o700 });
  const createdRun = runBroker(fixture, first);
  assert.equal(createdRun.status, 0, createdRun.stderr.toString('utf8'));
  const [created] = parseReceipts(createdRun.stdout);
  assert.deepEqual({ ...created, bytes: undefined }, {
    bytes: undefined, effect: 'created', generationBefore: 0, generationAfter: 1,
    operation: operationId('create').toString('hex'),
    requestSha256: sha256(first).toString('hex'),
    contentSha256: sha256(Buffer.from('een\n')).toString('hex'),
    contentLength: 4, target: 'a/result.txt',
  });
  assert.equal(fs.readFileSync(path.join(fixture.root, 'a/result.txt'), 'utf8'), 'een\n');
  assert.equal(fs.statSync(path.join(fixture.root, 'a/result.txt')).mode & 0o777, 0o600);

  const noopFrame = request({ id: 'noop', generation: 1, target: 'a/result.txt', content: 'een\n' });
  const noopRun = runBroker(fixture, noopFrame);
  assert.equal(noopRun.status, 0, noopRun.stderr.toString('utf8'));
  const [noop] = parseReceipts(noopRun.stdout);
  assert.equal(noop.effect, 'noop');
  assert.equal(noop.generationBefore, 1);
  assert.equal(noop.generationAfter, 2);

  const journalPath = path.join(fixture.journal, 'workspace-writer.journal');
  const beforeReplacement = fs.readFileSync(journalPath);
  const replacement = request({
    id: 'replace-refused', generation: 2, target: 'a/result.txt', content: 'twee\n',
  });
  assertCleanFailure(runBroker(fixture, replacement), /replace_unsupported/u);
  assert.equal(fs.readFileSync(path.join(fixture.root, 'a/result.txt'), 'utf8'), 'een\n');
  assert.ok(fs.readFileSync(journalPath).equals(beforeReplacement));
});

test('één proces ordent een canonieke requestbatch zonder generation-sprongen', () => {
  const fixture = privateFixture('single-process');
  const frames = [
    request({ id: 'batch-one', generation: 0, target: 'one.txt', content: 'een\n' }),
    request({ id: 'batch-two', generation: 1, target: 'two.txt', content: 'twee\n' }),
    request({ id: 'batch-three', generation: 2, target: 'one.txt', content: 'een\n' }),
  ];
  const run = runBroker(fixture, Buffer.concat(frames));
  assert.equal(run.status, 0, run.stderr.toString('utf8'));
  assert.deepEqual(parseReceipts(run.stdout).map((receipt) => ({
    effect: receipt.effect,
    before: receipt.generationBefore,
    after: receipt.generationAfter,
  })), [
    { effect: 'created', before: 0, after: 1 },
    { effect: 'created', before: 1, after: 2 },
    { effect: 'noop', before: 2, after: 3 },
  ]);
  assert.equal(fs.readFileSync(path.join(fixture.root, 'one.txt'), 'utf8'), 'een\n');
  assert.equal(fs.readFileSync(path.join(fixture.root, 'two.txt'), 'utf8'), 'twee\n');

  const skipped = runBroker(fixture, request({
    id: 'batch-skip', generation: 4, target: 'skip.txt', content: 'nee\n',
  }));
  assertCleanFailure(skipped, /generation_mismatch/u);
  assert.equal(fs.existsSync(path.join(fixture.root, 'skip.txt')), false);
});

test('dezelfde operatie is byte-idempotent en een andere request met hetzelfde id faalt dicht', () => {
  const fixture = privateFixture('idempotency');
  const original = request({ id: 'stable', generation: 0, target: 'stable.txt', content: 'vast\n' });
  const first = runBroker(fixture, original);
  const replay = runBroker(fixture, original);
  assert.equal(first.status, 0, first.stderr.toString('utf8'));
  assert.equal(replay.status, 0, replay.stderr.toString('utf8'));
  assert.ok(replay.stdout.equals(first.stdout), 'replayreceipt is niet bytegelijk');

  const conflict = request({
    id: 'stable', generation: 0, target: 'stable.txt', content: 'ander\n',
  });
  assertCleanFailure(runBroker(fixture, conflict), /operation_conflict/u);
  assert.equal(fs.readFileSync(path.join(fixture.root, 'stable.txt'), 'utf8'), 'vast\n');
  assert.equal(parseReceipts(first.stdout)[0].generationAfter, 1);
});

test('ASan/UBSan-build doorloopt create, noop en replay zonder sanitizerdiagnose', () => {
  const fixture = privateFixture('sanitizers');
  const environment = {
    ASAN_OPTIONS: 'abort_on_error=1:detect_leaks=1:halt_on_error=1',
    UBSAN_OPTIONS: 'halt_on_error=1:print_stacktrace=1',
  };
  const createdFrame = request({
    id: 'sanitized-create', generation: 0, target: 'sanitized.txt', content: 'veilig\n',
  });
  const created = runBroker(fixture, createdFrame, {
    executable: sanitizerBinary, env: environment,
  });
  assert.equal(created.status, 0, created.stderr.toString('utf8'));
  assert.equal(created.stderr.length, 0);
  const noopFrame = request({
    id: 'sanitized-noop', generation: 1, target: 'sanitized.txt', content: 'veilig\n',
  });
  const noop = runBroker(fixture, noopFrame, {
    executable: sanitizerBinary, env: environment,
  });
  assert.equal(noop.status, 0, noop.stderr.toString('utf8'));
  assert.equal(noop.stderr.length, 0);
  const replay = runBroker(fixture, createdFrame, {
    executable: sanitizerBinary, env: environment,
  });
  assert.equal(replay.status, 0, replay.stderr.toString('utf8'));
  assert.ok(replay.stdout.equals(created.stdout));
});

test('noop vereist exact huidige euid, modus 0600, nlink 1 en content/stat', () => {
  const fixture = privateFixture('noop-metadata');
  const target = path.join(fixture.root, 'existing.txt');
  const sibling = path.join(fixture.root, 'sibling.txt');
  fs.writeFileSync(target, 'gelijk\n', { mode: 0o600 });
  fs.chmodSync(target, 0o644);
  const modeRefused = runBroker(fixture, request({
    id: 'noop-mode', generation: 0, target: 'existing.txt', content: 'gelijk\n',
  }));
  assertCleanFailure(modeRefused, /noop_metadata_refused/u);
  const journalPath = path.join(fixture.journal, 'workspace-writer.journal');
  assert.equal(fs.statSync(journalPath).size, 0);

  fs.chmodSync(target, 0o4600);
  const specialModeRefused = runBroker(fixture, request({
    id: 'noop-special-mode', generation: 0, target: 'existing.txt', content: 'gelijk\n',
  }));
  assertCleanFailure(specialModeRefused, /noop_metadata_refused/u);
  assert.equal(fs.statSync(journalPath).size, 0);

  fs.chmodSync(target, 0o600);
  fs.linkSync(target, sibling);
  const linkRefused = runBroker(fixture, request({
    id: 'noop-link', generation: 0, target: 'existing.txt', content: 'gelijk\n',
  }));
  assertCleanFailure(linkRefused, /noop_metadata_refused/u);
  assert.equal(fs.statSync(journalPath).size, 0);

  fs.unlinkSync(sibling);
  const accepted = runBroker(fixture, request({
    id: 'noop-safe', generation: 0, target: 'existing.txt', content: 'gelijk\n',
  }));
  assert.equal(accepted.status, 0, accepted.stderr.toString('utf8'));
  assert.equal(parseReceipts(accepted.stdout)[0].effect, 'noop');
});

test('de binaire parser weigert niet-canonieke frames en paden vóór journal of effect', () => {
  const cases = [
    ['nul-operation', (() => {
      const frame = request({ id: 'nul', generation: 0, target: 'ok.txt', content: 'x' });
      frame.fill(0, 8, 40);
      return frame;
    })(), /invalid_operation_id/u],
    ['dot-segment', request({ id: 'dot', generation: 0, target: 'a/../escape', content: 'x' }),
      /invalid_target/u],
    ['double-slash', request({ id: 'slash', generation: 0, target: 'a//b', content: 'x' }),
      /invalid_target/u],
    ['absolute', request({ id: 'absolute', generation: 0, target: '/outside', content: 'x' }),
      /invalid_target/u],
    ['bad-hash', (() => {
      const frame = request({ id: 'hash', generation: 0, target: 'hash.txt', content: 'x' });
      frame[58] ^= 0xff;
      return frame;
    })(), /content_hash_mismatch/u],
    ['trailing-byte', Buffer.concat([
      request({ id: 'tail', generation: 0, target: 'tail.txt', content: 'x' }), Buffer.from([0]),
    ]), /truncated_request/u],
  ];
  for (const [label, frame, error] of cases) {
    const fixture = privateFixture(`parser-${label}`);
    const result = runBroker(fixture, frame);
    assertCleanFailure(result, error);
    assert.deepEqual(fs.readdirSync(fixture.root), [], label);
    const journalPath = path.join(fixture.journal, 'workspace-writer.journal');
    if (fs.existsSync(journalPath)) assert.equal(fs.statSync(journalPath).size, 0, label);
  }
});

test('openat2 weigert symlinktraversal en een gepind root-fd blijft bij een pathname-rebind', async () => {
  const escape = privateFixture('symlink');
  const outside = path.join(escape.base, 'outside');
  fs.mkdirSync(outside, { mode: 0o700 });
  fs.symlinkSync(outside, path.join(escape.root, 'link'));
  const escaped = runBroker(escape, request({
    id: 'escape', generation: 0, target: 'link/pwned.txt', content: 'nee\n',
  }));
  assertCleanFailure(escaped, /path_resolution_refused/u);
  assert.equal(fs.existsSync(path.join(outside, 'pwned.txt')), false);

  const rebound = privateFixture('rebind');
  const moved = path.join(rebound.base, 'moved-workspace');
  const replacement = path.join(rebound.base, 'replacement');
  fs.mkdirSync(replacement, { mode: 0o700 });
  const rootDescriptor = fs.openSync(rebound.root, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY);
  const journalDescriptor = fs.openSync(
    rebound.journal, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY,
  );
  const child = spawn(binary, [], {
    cwd: suiteRoot, env: brokerEnvironment(),
    stdio: ['pipe', 'pipe', 'pipe', rootDescriptor, journalDescriptor],
  });
  fs.closeSync(journalDescriptor);
  fs.closeSync(rootDescriptor);
  fs.renameSync(rebound.root, moved);
  fs.symlinkSync(replacement, rebound.root);
  const chunks = [];
  const errors = [];
  child.stdout.on('data', (chunk) => chunks.push(chunk));
  child.stderr.on('data', (chunk) => errors.push(chunk));
  child.stdin.end(request({ id: 'rebind', generation: 0, target: 'bound.txt', content: 'hier\n' }));
  const exit = await new Promise((resolve) => child.once('close', (status, signal) => resolve({ status, signal })));
  assert.deepEqual(exit, { status: 0, signal: null }, Buffer.concat(errors).toString('utf8'));
  assert.equal(parseReceipts(Buffer.concat(chunks))[0].effect, 'created');
  assert.equal(fs.readFileSync(path.join(moved, 'bound.txt'), 'utf8'), 'hier\n');
  assert.equal(fs.existsSync(path.join(replacement, 'bound.txt')), false);
});

test('journal- en workspace-fd mogen niet dezelfde of geneste directoryautoriteit dragen', () => {
  const fixture = privateFixture('authority-overlap');
  const nestedJournal = path.join(fixture.root, 'journal');
  fs.mkdirSync(nestedJournal, { mode: 0o700 });
  const frame = request({ id: 'overlap', generation: 0, target: 'result.txt', content: 'nee\n' });
  const nested = runBrokerDirectories(fixture.root, nestedJournal, frame);
  assertCleanFailure(nested, /directory_authority_overlap/u);
  assert.equal(fs.existsSync(path.join(nestedJournal, 'workspace-writer.journal')), false);
  assert.equal(fs.existsSync(path.join(fixture.root, 'result.txt')), false);

  const identical = runBrokerDirectories(fixture.root, fixture.root, frame);
  assertCleanFailure(identical, /directory_authority_overlap/u);
  assert.equal(fs.existsSync(path.join(fixture.root, 'workspace-writer.journal')), false);
  assert.equal(fs.existsSync(path.join(fixture.root, 'result.txt')), false);
});

test('een parent-rebind na durable prepare bereikt noch de oude parent noch een symlinkdoel', async () => {
  const fixture = privateFixture('parent-rebind');
  const parent = path.join(fixture.root, 'parent');
  const moved = path.join(fixture.base, 'moved-parent');
  const outside = path.join(fixture.base, 'outside');
  fs.mkdirSync(parent, { mode: 0o700 });
  fs.mkdirSync(outside, { mode: 0o700 });
  const content = Buffer.alloc(16 * 1024 * 1024, 0x41);
  const frame = request({
    id: 'parent-rebind-after-prepare', generation: 0,
    target: 'parent/result.bin', content,
  });
  const rootDescriptor = fs.openSync(fixture.root, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY);
  const journalDescriptor = fs.openSync(
    fixture.journal, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY,
  );
  const child = spawn(binary, [], {
    cwd: suiteRoot, env: brokerEnvironment(),
    stdio: ['pipe', 'pipe', 'pipe', rootDescriptor, journalDescriptor],
  });
  fs.closeSync(journalDescriptor);
  fs.closeSync(rootDescriptor);
  const stderr = [];
  child.stderr.on('data', (chunk) => stderr.push(chunk));
  const closed = new Promise((resolve) => child.once('close', (status, signal) => {
    resolve({ status, signal });
  }));
  child.stdin.end(frame);
  const journalPath = path.join(fixture.journal, 'workspace-writer.journal');
  const durablePrepareBytes = journalPrefixBytes + preparePayloadPrefixBytes
    + frame.length + journalChecksumBytes;
  const deadline = Date.now() + 10_000;
  let rebound = false;
  while (Date.now() < deadline && child.exitCode == null && child.signalCode == null) {
    if (!fs.existsSync(journalPath)
        || fs.statSync(journalPath).size < durablePrepareBytes) {
      await new Promise((resolve) => setImmediate(resolve));
      continue;
    }
    process.kill(child.pid, 'SIGSTOP');
    await new Promise((resolve) => setTimeout(resolve, 5));
    fs.renameSync(parent, moved);
    fs.symlinkSync(outside, parent);
    rebound = true;
    process.kill(child.pid, 'SIGCONT');
    break;
  }
  if (!rebound && child.exitCode == null && child.signalCode == null) {
    process.kill(child.pid, 'SIGKILL');
  }
  const exit = await closed;
  assert.equal(rebound, true, 'durable prepare werd niet extern waargenomen');
  assert.equal(exit.status, 65, Buffer.concat(stderr).toString('utf8'));
  assert.match(Buffer.concat(stderr).toString('utf8'), /parent_binding_changed/u);
  assert.equal(fs.existsSync(path.join(moved, 'result.bin')), false);
  assert.equal(fs.existsSync(path.join(outside, 'result.bin')), false);
  assert.deepEqual(fs.readdirSync(moved), []);
});

test('de anonieme bron-fd kan niet via een siblingnaam worden verwisseld', async () => {
  const fixture = privateFixture('source-swap');
  const id = 'source-path-swap';
  const reserved = path.join(fixture.root, temporaryName(id));
  const target = path.join(fixture.root, 'result.bin');
  const content = Buffer.alloc(64 * 1024 * 1024, 0x53);
  const frame = request({ id, generation: 0, target: 'result.bin', content });
  const running = await startStoppedAtPhase(fixture, frame, 'prelink');
  assert.equal(fs.existsSync(reserved), false,
    'de broker publiceerde vóór de eindlink toch een benoemde bron');
  fs.writeFileSync(reserved, content, { mode: 0o600 });
  fs.chmodSync(reserved, 0o600);
  const counterfeitInode = fs.statSync(reserved).ino;
  process.kill(running.child.pid, 'SIGCONT');
  const result = await running.closed;
  assert.equal(result.status, 0, result.stderr.toString('utf8'));
  assert.equal(result.signal, null);
  assert.equal(parseReceipts(result.stdout)[0].effect, 'created');
  assert.ok(fs.readFileSync(target).equals(content));
  assert.notEqual(fs.statSync(target).ino, counterfeitInode,
    'de target koos een siblingnaam in plaats van de gehouden anonieme fd');
  assert.equal(fs.statSync(reserved).ino, counterfeitInode);
});

test('na publicatie bestaat geen named-temp-cleanup die een sibling kan verwijderen', async () => {
  const fixture = privateFixture('cleanup-swap');
  const id = 'cleanup-last-check-swap';
  const reserved = path.join(fixture.root, temporaryName(id));
  const target = path.join(fixture.root, 'published.bin');
  const content = Buffer.alloc(64 * 1024 * 1024, 0x43);
  const running = await startStoppedAtPhase(fixture, request({
    id, generation: 0, target: 'published.bin', content,
  }), 'postlink-pre-L');
  assert.ok(fs.readFileSync(target).equals(content));
  assert.equal(fs.existsSync(reserved), false);
  fs.writeFileSync(reserved, 'onafhankelijke sibling\n', { mode: 0o600 });
  fs.chmodSync(reserved, 0o600);
  const siblingInode = fs.statSync(reserved).ino;
  process.kill(running.child.pid, 'SIGCONT');
  const result = await running.closed;
  assert.equal(result.status, 0, result.stderr.toString('utf8'));
  assert.equal(fs.statSync(reserved).ino, siblingInode,
    'de broker verwijderde een na publicatie gemaakte sibling');
  assert.equal(fs.readFileSync(reserved, 'utf8'), 'onafhankelijke sibling\n');
});

test('de statische publicatievolgorde synchroniseert eerst de gehouden inode en dan de parent vóór L', () => {
  const sourceText = fs.readFileSync(source, 'utf8');
  const syncStart = sourceText.indexOf('static int synchronize_published_material(');
  const syncEnd = sourceText.indexOf('\nstatic uint32_t rotate_right(', syncStart);
  assert.ok(syncStart >= 0 && syncEnd > syncStart,
    'synchronize_published_material ontbreekt of is niet statisch begrensd');
  assert.match(sourceText.slice(syncStart, syncEnd), /return fsync\(descriptor\);/u,
    'de materialhelper synchroniseert niet werkelijk de gehouden inode-fd');

  const materialStart = sourceText.indexOf('static int create_anonymous_material(');
  const materialEnd = sourceText.indexOf('\nstatic int append_identity_record(', materialStart);
  assert.ok(materialStart >= 0 && materialEnd > materialStart,
    'create_anonymous_material ontbreekt of is niet statisch begrensd');
  const materialBody = sourceText.slice(materialStart, materialEnd);
  const link = materialBody.indexOf('linkat(descriptor, ""');
  const inodeSync = materialBody.indexOf('synchronize_published_material(descriptor)');
  const parentSync = materialBody.indexOf('fsync(target->parent_descriptor)');
  assert.ok(link >= 0 && inodeSync > link && parentSync > inodeSync,
    'vereiste volgorde linkat -> fsync(gehouden inode) -> fsync(parent) ontbreekt');

  const publishStart = sourceText.indexOf('static int publish_operation(');
  const publishEnd = sourceText.indexOf('\nstatic int verify_operation_effect(', publishStart);
  assert.ok(publishStart >= 0 && publishEnd > publishStart,
    'publish_operation ontbreekt of is niet statisch begrensd');
  const publishBody = sourceText.slice(publishStart, publishEnd);
  const materialCall = publishBody.indexOf('create_anonymous_material(target, operation)');
  const lCall = publishBody.indexOf('record_published_target(state, target, operation)');
  assert.ok(materialCall >= 0 && lCall > materialCall,
    'L mag pas na de volledig gesynchroniseerde materialpublicatie worden geschreven');
});

test('een fout bij fsync van de gepubliceerde inode blijft P-only en receiptloos', () => {
  const fixture = privateFixture('published-inode-fsync-failure');
  const target = path.join(fixture.root, 'result.bin');
  const frame = request({
    id: 'published-inode-fsync-failure', generation: 0,
    target: 'result.bin', content: 'duurzaam\n',
  });
  const failed = runBroker(fixture, frame, {
    executable: phaseBarrierBinary,
    env: { EVWW_TEST_FAIL_PHASE: 'postlink-material-fsync' },
  });
  assertCleanFailure(failed, /publish_material_sync_failed/u);
  assert.equal(fs.readFileSync(target, 'utf8'), 'duurzaam\n');

  const journalPath = path.join(fixture.journal, 'workspace-writer.journal');
  const pOnly = fs.readFileSync(journalPath);
  assert.deepEqual(completeJournalRecords(journalPath).map((record) => record.type), ['P']);
  const restarted = runBroker(fixture, frame);
  assertCleanFailure(restarted, /pending_effect_uncertain/u);
  assert.ok(fs.readFileSync(journalPath).equals(pOnly));
});

test('een exact-content destination-race blijft ook na herstart P-only en zonder receipt', async () => {
  const fixture = privateFixture('destination-race');
  const target = path.join(fixture.root, 'winner.txt');
  const content = Buffer.alloc(64 * 1024 * 1024, 0x57);
  const frame = request({
    id: 'destination-create-race', generation: 0, target: 'winner.txt',
    content,
  });
  const running = await startStoppedAtPhase(fixture, frame, 'prelink');
  fs.writeFileSync(target, content, { flag: 'wx', mode: 0o600 });
  fs.chmodSync(target, 0o600);
  const winnerInode = fs.statSync(target).ino;
  process.kill(running.child.pid, 'SIGCONT');
  const result = await running.closed;
  assert.equal(result.status, 65, result.stderr.toString('utf8'));
  assert.match(result.stderr.toString('utf8'), /destination_exists/u);
  assert.equal(result.stdout.length, 0);
  assert.ok(fs.readFileSync(target).equals(content));
  assert.equal(fs.statSync(target).ino, winnerInode);

  const journalPath = path.join(fixture.journal, 'workspace-writer.journal');
  const pOnly = fs.readFileSync(journalPath);
  assert.deepEqual(completeJournalRecords(journalPath).map((record) => record.type), ['P']);
  const restarted = runBroker(fixture, frame, { timeout: 30_000 });
  assertCleanFailure(restarted, /pending_effect_uncertain/u);
  assert.ok(fs.readFileSync(journalPath).equals(pOnly));
  assert.ok(fs.readFileSync(target).equals(content));
  assert.equal(fs.statSync(target).ino, winnerInode);
});

test('een exact-content destination-swap na link krijgt nooit een L of receipt', async () => {
  const fixture = privateFixture('postlink-destination-swap');
  const target = path.join(fixture.root, 'result.bin');
  const displaced = path.join(fixture.root, 'broker-inode.bin');
  const content = Buffer.alloc(16 * 1024 * 1024, 0x44);
  const frame = request({
    id: 'postlink-destination-swap', generation: 0, target: 'result.bin', content,
  });
  const running = await startStoppedAtPhase(fixture, frame, 'postlink-pre-L');
  const brokerInode = fs.statSync(target).ino;
  fs.renameSync(target, displaced);
  fs.writeFileSync(target, content, { mode: 0o600 });
  fs.chmodSync(target, 0o600);
  assert.notEqual(fs.statSync(target).ino, brokerInode);
  process.kill(running.child.pid, 'SIGCONT');
  const result = await running.closed;
  assertCleanFailure(result, /published_target_mismatch/u);
  assert.ok(fs.readFileSync(displaced).equals(content));
  assert.ok(fs.readFileSync(target).equals(content));
  const winnerInode = fs.statSync(target).ino;
  const displacedInode = fs.statSync(displaced).ino;
  const journalPath = path.join(fixture.journal, 'workspace-writer.journal');
  const pOnly = fs.readFileSync(journalPath);
  assert.deepEqual(completeJournalRecords(journalPath).map((record) => record.type), ['P']);

  const restarted = runBroker(fixture, frame, { timeout: 30_000 });
  assertCleanFailure(restarted, /pending_effect_uncertain/u);
  assert.ok(fs.readFileSync(journalPath).equals(pOnly));
  assert.ok(fs.readFileSync(target).equals(content));
  assert.ok(fs.readFileSync(displaced).equals(content));
  assert.equal(fs.statSync(target).ino, winnerInode);
  assert.equal(fs.statSync(displaced).ino, displacedInode);
});

test('een noop bindt de geobserveerde inode en weigert een exact-content sibling-swap', async () => {
  const fixture = privateFixture('noop-inode-swap');
  const target = path.join(fixture.root, 'existing.txt');
  const displaced = path.join(fixture.root, 'observed.txt');
  fs.writeFileSync(target, 'gelijk\n', { mode: 0o600 });
  fs.chmodSync(target, 0o600);
  const frame = request({
    id: 'noop-inode-swap', generation: 0, target: 'existing.txt', content: 'gelijk\n',
  });
  const running = await startStoppedAtPhase(fixture, frame, 'post-P');
  fs.renameSync(target, displaced);
  fs.writeFileSync(target, 'gelijk\n', { mode: 0o600 });
  fs.chmodSync(target, 0o600);
  process.kill(running.child.pid, 'SIGCONT');
  const result = await running.closed;
  assertCleanFailure(result, /target_changed_before_publish/u);
  assert.notEqual(fs.statSync(target).ino, fs.statSync(displaced).ino);
  assert.deepEqual(completeJournalRecords(
    path.join(fixture.journal, 'workspace-writer.journal'),
  ).map((record) => record.type), ['P']);
});

test('journalcorruptie en ieder onvolledig record worden zonder reparatie in quarantaine gezet', () => {
  const fixture = privateFixture('journal');
  const firstFrame = request({ id: 'journal-one', generation: 0, target: 'one.txt', content: 'een\n' });
  const first = runBroker(fixture, firstFrame);
  assert.equal(first.status, 0, first.stderr.toString('utf8'));
  const journalPath = path.join(fixture.journal, 'workspace-writer.journal');
  const durable = fs.readFileSync(journalPath);
  const prepare = journalRecord(durable);
  assert.equal(prepare.type, 'P');
  assert.ok(prepare.payload.subarray(preparePayloadPrefixBytes).equals(firstFrame),
    'prepare journaliseert niet de exacte request en materialbytes vóór effect');
  const prepareEnd = prepare.end;
  const published = journalRecord(durable, prepareEnd);
  assert.equal(published.type, 'L');
  const commit = journalRecord(durable, published.end);
  assert.equal(commit.type, 'C');
  const commitEnd = commit.end;
  assert.equal(commitEnd, durable.length, 'journal bevat geen exact prepare+commit-paar');

  fs.appendFileSync(journalPath, Buffer.from('EVWW', 'ascii'));
  const incomplete = fs.readFileSync(journalPath);
  const secondFrame = request({ id: 'journal-two', generation: 1, target: 'two.txt', content: 'twee\n' });
  const recoveredTail = runBroker(fixture, secondFrame);
  assertCleanFailure(recoveredTail, /journal_incomplete_record/u);
  assert.ok(fs.readFileSync(journalPath).equals(incomplete));
  assert.equal(fs.existsSync(path.join(fixture.root, 'two.txt')), false);

  fs.writeFileSync(journalPath, durable, { mode: 0o600 });
  const lengthChanged = Buffer.from(durable);
  const originalCommitLength = lengthChanged.readBigUInt64BE(published.end + 9);
  lengthChanged.writeBigUInt64BE(originalCommitLength + 1n, published.end + 9);
  fs.writeFileSync(journalPath, lengthChanged, { mode: 0o600 });
  const lengthRefused = runBroker(fixture, secondFrame);
  assertCleanFailure(lengthRefused, /journal_incomplete_record/u);
  assert.ok(fs.readFileSync(journalPath).equals(lengthChanged),
    'een corrupte final-recordlengte werd stil gerepareerd');
  assert.equal(fs.existsSync(path.join(fixture.root, 'two.txt')), false);

  const changed = Buffer.from(durable);
  changed[prepareEnd - 1] ^= 0xff;
  fs.writeFileSync(journalPath, changed, { mode: 0o600 });
  const refused = runBroker(fixture, request({
    id: 'journal-three', generation: 2, target: 'three.txt', content: 'drie\n',
  }));
  assertCleanFailure(refused, /journal_checksum_mismatch/u);
  assert.equal(fs.existsSync(path.join(fixture.root, 'three.txt')), false);
});

test('een durable P zonder zichtbaar effect wordt exact uit de gebonden request rematerialiseerd', () => {
  const fixture = privateFixture('prepare-rematerialize');
  const frame = request({
    id: 'prepare-rematerialize', generation: 0, target: 'prepared.bin', content: 'vastgelegd\n',
  });
  const created = runBroker(fixture, frame);
  assert.equal(created.status, 0, created.stderr.toString('utf8'));
  const journalPath = path.join(fixture.journal, 'workspace-writer.journal');
  const records = completeJournalRecords(journalPath);
  assert.deepEqual(records.map((record) => record.type), ['P', 'L', 'C']);
  fs.truncateSync(journalPath, records[0].end);
  fs.unlinkSync(path.join(fixture.root, 'prepared.bin'));

  const recovered = runBroker(fixture, frame);
  assert.equal(recovered.status, 0, recovered.stderr.toString('utf8'));
  assert.equal(fs.readFileSync(path.join(fixture.root, 'prepared.bin'), 'utf8'), 'vastgelegd\n');
  assert.deepEqual(completeJournalRecords(journalPath).map((record) => record.type),
    ['P', 'L', 'C']);
});

for (const phase of [
  { barrier: 'post-P', records: ['P'], targetVisible: false, recoverable: true },
  { barrier: 'prelink', records: ['P'], targetVisible: false, recoverable: true },
  { barrier: 'postlink-pre-L', records: ['P'], targetVisible: true, recoverable: false },
  { barrier: 'post-L', records: ['P', 'L'], targetVisible: true, recoverable: true },
  { barrier: 'post-C', records: ['P', 'L', 'C'], targetVisible: true, recoverable: true },
]) {
  const recoveryExpectation = phase.recoverable
    ? 'herstelt naar één P/L/C en één receipt'
    : 'blijft onclassificeerbaar P-only en fail-closed';
  test(`externe SIGKILL op ${phase.barrier} ${recoveryExpectation}`, async () => {
    const fixture = privateFixture(`crash-${phase.barrier}`);
    const id = `crash-${phase.barrier}`;
    const target = path.join(fixture.root, 'crash.bin');
    const content = Buffer.alloc(16 * 1024 * 1024, phase.barrier.charCodeAt(0));
    const frame = request({ id, generation: 0, target: 'crash.bin', content });
    const running = await startStoppedAtPhase(fixture, frame, phase.barrier);
    const journalPath = path.join(fixture.journal, 'workspace-writer.journal');
    assert.deepEqual(completeJournalRecords(journalPath).map((record) => record.type),
      phase.records);
    assert.equal(fs.existsSync(target), phase.targetVisible);
    const competing = runBroker(fixture, frame, { timeout: 20_000 });
    assertCleanFailure(competing, /broker_already_active/u);
    process.kill(running.child.pid, 'SIGKILL');
    const killed = await running.closed;
    assert.equal(killed.signal, 'SIGKILL');
    assert.equal(killed.stdout.length, 0, `${phase.barrier}-crash lekte al een receipt`);

    if (!phase.recoverable) {
      const pOnly = fs.readFileSync(journalPath);
      const inode = fs.statSync(target).ino;
      const recovered = runBroker(fixture, frame, { timeout: 30_000 });
      assertCleanFailure(recovered, /pending_effect_uncertain/u);
      assert.ok(fs.readFileSync(journalPath).equals(pOnly));
      assert.equal(fs.statSync(target).ino, inode);
      assert.ok(fs.readFileSync(target).equals(content));
      assert.deepEqual(completeJournalRecords(journalPath).map((record) => record.type), ['P']);
      const replay = runBroker(fixture, frame, { timeout: 30_000 });
      assertCleanFailure(replay, /pending_effect_uncertain/u);
      assert.ok(fs.readFileSync(journalPath).equals(pOnly));
      assert.equal(fs.statSync(target).ino, inode);
      return;
    }

    const recovered = runBroker(fixture, frame, { timeout: 30_000 });
    assert.equal(recovered.status, 0, recovered.stderr.toString('utf8'));
    const [receipt] = parseReceipts(recovered.stdout);
    assert.equal(receipt.effect, 'created');
    assert.equal(receipt.generationBefore, 0);
    assert.equal(receipt.generationAfter, 1);
    assert.equal(receipt.requestSha256, sha256(frame).toString('hex'));
    assert.ok(fs.readFileSync(target).equals(content));
    assert.deepEqual(fs.readdirSync(fixture.root).sort(), ['crash.bin']);
    assert.deepEqual(completeJournalRecords(journalPath).map((record) => record.type),
      ['P', 'L', 'C']);

    const replay = runBroker(fixture, frame, { timeout: 30_000 });
    assert.equal(replay.status, 0, replay.stderr.toString('utf8'));
    assert.ok(replay.stdout.equals(recovered.stdout),
      `${phase.barrier}-crashreplayreceipt is niet bytegelijk`);
  });
}
