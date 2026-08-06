// Kleine TensorFlow-modellen draaien buiten Eva's hoofdproces. Een worker-thread
// deelt nog native bibliotheekinitialisatie met Node; tfjs-node kon daardoor ook
// daar de eventloop vertragen. Een child-proces houdt chat, dashboard, Home
// Assistant en de hartslag werkelijk los van die zware initialisatie.
import { fork } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { config } from '../config.js';

const DEFAULT_WORKER_PATH = fileURLToPath(new URL('./localinferenceworker.js', import.meta.url));
const SHUTDOWN_ERROR_CODE = 'EVA_LOCAL_INFERENCE_SHUTDOWN';

let worker = null;
let workerPath = DEFAULT_WORKER_PATH;
let stopped = false;
let shutdownPromise = null;
let sequence = 0;
const workers = new Set();
const pending = new Map();
const activeRequests = new Map();
const requestKeys = new Map();

function shutdownError() {
  const error = new Error('lokale inferentie is afgesloten');
  error.code = SHUTDOWN_ERROR_CODE;
  return error;
}

function releaseRequest(id) {
  const key = requestKeys.get(id);
  requestKeys.delete(id);
  if (key && activeRequests.get(key) === id) activeRequests.delete(key);
}

function rejectPending(error, instance = null) {
  for (const [id, request] of pending.entries()) {
    if (instance && request.worker !== instance) continue;
    clearTimeout(request.timer);
    request.reject(error);
    pending.delete(id);
    releaseRequest(id);
  }
}

function closeWorker(instance) {
  return new Promise((resolve) => {
    let termTimer = null;
    let killTimer = null;
    let exited = instance.exitCode !== null || instance.signalCode !== null;
    let disconnected = !instance.connected;
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      clearTimeout(termTimer);
      clearTimeout(killTimer);
      instance.removeListener('exit', onExit);
      instance.removeListener('disconnect', onDisconnect);
      instance.removeListener('close', onClose);
      workers.delete(instance);
      if (worker === instance) worker = null;
      resolve();
    };
    const finishWhenClosed = () => {
      if (exited && disconnected) finish();
    };
    const onExit = () => {
      exited = true;
      finishWhenClosed();
    };
    const onDisconnect = () => {
      disconnected = true;
      finishWhenClosed();
    };
    const onClose = () => {
      exited = true;
      disconnected = true;
      finish();
    };

    instance.once('exit', onExit);
    instance.once('disconnect', onDisconnect);
    instance.once('close', onClose);
    try { instance.ref(); } catch { /* een reeds gesloten child heeft geen actieve handle meer */ }
    termTimer = setTimeout(() => {
      if (instance.exitCode === null && instance.signalCode === null) {
        try { instance.kill('SIGTERM'); } catch { /* proces kan intussen gestopt zijn */ }
      }
    }, 100);
    killTimer = setTimeout(() => {
      if (instance.exitCode === null && instance.signalCode === null) {
        try { instance.kill('SIGKILL'); } catch { /* proces kan intussen gestopt zijn */ }
      }
    }, 1_000);

    try {
      if (instance.connected) instance.disconnect();
    } catch { /* het IPC-kanaal kan al door de child gesloten zijn */ }
    disconnected = !instance.connected;
    finishWhenClosed();
  });
}

function ensureWorker() {
  if (worker) return worker;
  if (stopped) throw shutdownError();
  const instance = fork(workerPath, [], {
    cwd: config.root,
    env: {
      ...process.env,
      EVA_LOCAL_INFERENCE_ROOT: config.root,
      TF_NUM_INTRAOP_THREADS: process.env.TF_NUM_INTRAOP_THREADS || '1',
      TF_NUM_INTEROP_THREADS: process.env.TF_NUM_INTEROP_THREADS || '1',
      OMP_NUM_THREADS: process.env.OMP_NUM_THREADS || '1',
      OMP_WAIT_POLICY: process.env.OMP_WAIT_POLICY || 'PASSIVE',
    },
    stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
  });
  workers.add(instance);
  instance.unref();
  instance.on('message', (message = {}) => {
    const id = Number(message.id);
    const request = pending.get(id);
    if (!request || request.worker !== instance) return;
    pending.delete(id);
    clearTimeout(request.timer);
    releaseRequest(id);
    if (message.ok) request.resolve(message.result);
    else request.reject(new Error(message.error || 'lokale inferentie mislukt'));
  });
  instance.on('error', (error) => {
    if (worker === instance) worker = null;
    rejectPending(error, instance);
  });
  instance.on('exit', (code) => {
    if (worker === instance) worker = null;
    workers.delete(instance);
    rejectPending(new Error(`lokale inferentieworker stopte met code ${code}`), instance);
  });
  instance.on('close', () => {
    workers.delete(instance);
    if (worker === instance) worker = null;
  });
  worker = instance;
  return instance;
}

function request(type, payload = {}, { timeoutMs = 30_000 } = {}) {
  // De child verwerkt bewust serieel. Zonder deze poort bleven korte runtime-
  // time-outs nieuwe opdrachten opstapelen terwijl een model nog minutenlang
  // laadde. Eén lopende inferentie is genoeg; alle andere routes vallen tijdelijk
  // terug op hun goedkope geheugen- of beleidsvariant.
  const requestKey = 'local-inference-worker';
  if (stopped) return Promise.reject(shutdownError());
  if (activeRequests.has(requestKey)) {
    return Promise.reject(new Error(`lokale inferentie bezig (${type})`));
  }
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const instance = ensureWorker();
    const timer = setTimeout(() => {
      pending.delete(id);
      releaseRequest(id);
      reject(new Error(`lokale inferentie timeout (${type})`));
      // Een verlopen native modelcall kan niet via IPC worden geannuleerd. Laat
      // hem dan ook niet op de achtergrond CPU/geheugen vasthouden of een latere
      // opdracht blokkeren: deze worker bevat hooguit deze ene actieve call.
      if (worker === instance) worker = null;
      try { instance.kill('SIGKILL'); } catch { /* proces kan al gestopt zijn */ }
    }, Math.max(1_000, Number(timeoutMs) || 30_000));
    pending.set(id, { resolve, reject, timer, worker: instance });
    activeRequests.set(requestKey, id);
    requestKeys.set(id, requestKey);
    const failSend = (error) => {
      if (!error || !pending.has(id)) return;
      pending.delete(id);
      clearTimeout(timer);
      releaseRequest(id);
      reject(error);
    };
    try {
      instance.send({ id, type, payload }, failSend);
    } catch (error) {
      failSend(error);
    }
  });
}

export function shutdownLocalInference() {
  if (shutdownPromise) return shutdownPromise;
  stopped = true;
  worker = null;
  rejectPending(shutdownError());
  activeRequests.clear();
  requestKeys.clear();

  const closingWorkers = Array.from(workers, closeWorker);
  shutdownPromise = Promise.all(closingWorkers).then(() => undefined);
  return shutdownPromise;
}

export function predictWithOwnModel(input, modelSha) {
  return request('predictor', { input: Array.from(input || []), modelSha });
}

export function predictPolicyWithOwnModel(input, sourceFeatures, status) {
  return request('policy', {
    input: Array.from(input || []),
    sourceFeatures: Array.from(sourceFeatures || []),
    modelSha: status?.sha256 || null,
    encoder: status?.encoder || null,
  });
}

export function embedWithLocalModel(text) {
  return request('embedding', { text: String(text || '').slice(0, 1000) }, { timeoutMs: 30_000 });
}

export function warmupOwnModel(target, payload = {}) {
  return request('warmup', { target, ...payload }, { timeoutMs: 3 * 60_000 });
}

export const __localInferenceTest = {
  setWorkerPath(path) {
    if (worker || workers.size || pending.size || stopped) {
      throw new Error('localinference-testworker kan alleen voor de eerste aanvraag worden ingesteld');
    }
    workerPath = String(path);
  },
  status() {
    return {
      stopped,
      workers: workers.size,
      pending: pending.size,
      activeRequests: activeRequests.size,
      requestKeys: requestKeys.size,
    };
  },
};
