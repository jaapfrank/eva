import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const testPath = fileURLToPath(import.meta.url);
const scenario = String(process.argv[2] || '');

function runFixtureWorker() {
  process.on('message', (message = {}) => {
    const target = message.payload?.target;
    if (target === 'pending') return;
    if (target === 'error') {
      process.send?.({ id: message.id, ok: false, error: 'gerichte workerfout' });
      return;
    }
    process.send?.({
      id: message.id,
      ok: true,
      result: { target, childPid: process.pid },
    });
  });
  process.on('disconnect', () => process.exit(0));
}

async function runLifecycleScenario(kind) {
  const {
    __localInferenceTest,
    shutdownLocalInference,
    warmupOwnModel,
  } = await import('../src/mind/localinference.js');
  __localInferenceTest.setWorkerPath(testPath);

  if (kind === 'success') {
    const result = await warmupOwnModel('success');
    assert.equal(result.target, 'success');
    assert.ok(result.childPid > 0);
  } else if (kind === 'error') {
    try {
      await assert.rejects(
        warmupOwnModel('error'),
        /gerichte workerfout/,
      );
    } finally {
      await shutdownLocalInference();
    }
    assert.deepEqual(__localInferenceTest.status(), {
      stopped: true,
      workers: 0,
      pending: 0,
      activeRequests: 0,
      requestKeys: 0,
    });
    return;
  } else if (kind === 'pending') {
    const request = warmupOwnModel('pending');
    const rejection = request.then(
      () => null,
      (error) => error,
    );
    assert.deepEqual(__localInferenceTest.status(), {
      stopped: false,
      workers: 1,
      pending: 1,
      activeRequests: 1,
      requestKeys: 1,
    });

    const firstShutdown = shutdownLocalInference();
    const secondShutdown = shutdownLocalInference();
    assert.strictEqual(secondShutdown, firstShutdown);
    await firstShutdown;

    const error = await rejection;
    assert.equal(error?.code, 'EVA_LOCAL_INFERENCE_SHUTDOWN');
    assert.match(error?.message || '', /afgesloten/);
    assert.deepEqual(__localInferenceTest.status(), {
      stopped: true,
      workers: 0,
      pending: 0,
      activeRequests: 0,
      requestKeys: 0,
    });
    return;
  } else {
    throw new Error(`onbekend lifecycle-scenario: ${kind}`);
  }

  const firstShutdown = shutdownLocalInference();
  const secondShutdown = shutdownLocalInference();
  assert.strictEqual(secondShutdown, firstShutdown);
  await firstShutdown;
  await assert.rejects(
    warmupOwnModel('na-shutdown'),
    (error) => error?.code === 'EVA_LOCAL_INFERENCE_SHUTDOWN',
  );
  assert.deepEqual(__localInferenceTest.status(), {
    stopped: true,
    workers: 0,
    pending: 0,
    activeRequests: 0,
    requestKeys: 0,
  });
}

function runInSubprocess(kind) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [testPath, kind], {
      cwd: process.cwd(),
      env: process.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try { child.kill('SIGKILL'); } catch { /* testproces kan net afgesloten zijn */ }
      reject(new Error(`lifecycle-scenario ${kind} sloot niet af; stdout=${stdout} stderr=${stderr}`));
    // De volledige suite start meerdere Node/TensorFlow-processen parallel. Geef
    // lifecycle-afsluiting genoeg marge voor CPU-belasting zonder een echte hang
    // onbeperkt te laten wachten.
    }, 15_000);

    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.once('error', (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    child.once('close', (code, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new Error(`lifecycle-scenario ${kind} stopte met code ${code}/${signal}; stdout=${stdout} stderr=${stderr}`));
    });
  });
}

if (typeof process.send === 'function') {
  runFixtureWorker();
} else if (scenario) {
  await runLifecycleScenario(scenario);
} else {
  const cases = [
    ['beantwoorde aanvraag sluit child en IPC af', 'success'],
    ['workerfout sluit via het fout-finally-pad af', 'error'],
    ['shutdown wijst pending werk af en ruimt alle lifecycle-state op', 'pending'],
  ];
  for (const [name, kind] of cases) {
    await runInSubprocess(kind);
    console.log(`ok - ${name}`);
  }
}
