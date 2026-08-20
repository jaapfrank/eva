import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import test, { after } from 'node:test';

const originalIndexedDb = globalThis.indexedDB;

function memoryIndexedDb() {
  const stores = new Map();
  const storeSchemas = new Map();
  let writes = 0;
  let deletes = 0;
  let countReads = 0;
  let databaseVersion = 0;
  let failCreateStore = null;
  let beforeNextWrite = null;
  const writeQueue = [];
  let activeWrite = null;

  function requestOperation(transaction, operation) {
    const request = {};
    transaction.pending += 1;
    transaction.operations.push({ request, operation });
    transaction.runNext();
    return request;
  }

  function runOperation(transaction, { request, operation }) {
    queueMicrotask(() => {
      try {
        request.result = operation();
        request.onsuccess?.({ target: request });
      } catch (error) {
        request.error = error;
        transaction.error = error;
        request.onerror?.({ target: request });
        transaction.onerror?.({ target: transaction });
      } finally {
        transaction.pending -= 1;
        transaction.executing = false;
        transaction.runNext();
        transaction.scheduleCompletion();
      }
    });
  }

  function activateNextWrite() {
    if (activeWrite || !writeQueue.length) return;
    activeWrite = writeQueue.shift();
    activeWrite.active = true;
    activeWrite.runNext();
  }

  const database = {
    objectStoreNames: {
      contains: (name) => stores.has(name),
      get length() { return stores.size; },
      item: (index) => [...stores.keys()][index] || null,
      [Symbol.iterator]: () => stores.keys(),
    },
    createObjectStore(name) {
      if (name === failCreateStore) throw new Error('forced create failure');
      assert.ok(['client-keys', 'pending-deliveries'].includes(name));
      assert.equal(stores.has(name), false);
      stores.set(name, new Map());
      storeSchemas.set(name, { keyPath: null, autoIncrement: false, indexNames: [] });
      return {
        keyPath: null, autoIncrement: false,
        indexNames: { length: 0, item: () => null, [Symbol.iterator]: () => [][Symbol.iterator]() },
      };
    },
    transaction(name, mode) {
      assert.ok(['client-keys', 'pending-deliveries'].includes(name));
      assert.equal(stores.has(name), true);
      assert.ok(['readonly', 'readwrite'].includes(mode));
      const records = stores.get(name);
      if (mode === 'readwrite' && beforeNextWrite) {
        const callback = beforeNextWrite;
        beforeNextWrite = null;
        callback({ name, records });
      }
      const transaction = {
        error: null,
        pending: 0,
        completed: false,
        active: mode === 'readonly',
        executing: false,
        operations: [],
        runNext() {
          if (!transaction.active || transaction.executing || !transaction.operations.length) return;
          transaction.executing = true;
          runOperation(transaction, transaction.operations.shift());
        },
        scheduleCompletion() {
          setTimeout(() => {
            if (transaction.completed || !transaction.active || transaction.pending !== 0
              || transaction.operations.length || transaction.executing) return;
            transaction.completed = true;
            transaction.oncomplete?.({ target: transaction });
            if (activeWrite === transaction) {
              activeWrite = null;
              activateNextWrite();
            }
          }, 0);
        },
        objectStore(storeName) {
          assert.equal(storeName, name);
          const schema = storeSchemas.get(storeName);
          return {
            keyPath: schema.keyPath,
            autoIncrement: schema.autoIncrement,
            indexNames: {
              get length() { return schema.indexNames.length; },
              item: (index) => schema.indexNames[index] || null,
              [Symbol.iterator]: () => schema.indexNames[Symbol.iterator](),
            },
            count() {
              countReads += 1;
              return requestOperation(transaction, () => records.size);
            },
            get(key) {
              return requestOperation(transaction, () => {
                if (!records.has(key)) return undefined;
                return structuredClone(records.get(key));
              });
            },
            put(value, key) {
              assert.equal(mode, 'readwrite');
              return requestOperation(transaction, () => {
                records.set(key, structuredClone(value));
                writes += 1;
                return key;
              });
            },
            delete(key) {
              assert.equal(mode, 'readwrite');
              return requestOperation(transaction, () => {
                deletes += 1;
                return records.delete(key);
              });
            },
          };
        },
        abort() {
          transaction.completed = true;
          transaction.onabort?.({ target: transaction });
          if (activeWrite === transaction) {
            activeWrite = null;
            activateNextWrite();
          }
        },
      };
      if (mode === 'readwrite') {
        transaction.active = false;
        writeQueue.push(transaction);
        activateNextWrite();
      }
      return transaction;
    },
    close() {},
  };

  return {
    seedSchema(version, names, overrides = {}) {
      databaseVersion = version;
      stores.clear();
      storeSchemas.clear();
      for (const name of names) {
        stores.set(name, new Map());
        storeSchemas.set(name, {
          keyPath: null, autoIncrement: false, indexNames: [],
          ...(overrides[name] || {}),
        });
      }
    },
    failCreate(name) { failCreateStore = name; },
    onNextWrite(callback) { beforeNextWrite = callback; },
    get version() { return databaseVersion; },
    get storeNames() { return [...stores.keys()].sort(); },
    get records() { return stores.get('client-keys'); },
    get pendingRecords() { return stores.get('pending-deliveries'); },
    get writes() { return writes; },
    get deletes() { return deletes; },
    get countReads() { return countReads; },
    open(name, version) {
      assert.equal(name, 'eva-http-client-ack-v3');
      assert.equal(version, 2);
      const request = { result: database };
      queueMicrotask(() => {
        if (databaseVersion < version) {
          const snapshot = new Map([...stores].map(([storeName, records]) => (
            [storeName, new Map([...records].map(([key, value]) => [key, structuredClone(value)]))]
          )));
          const schemaSnapshot = structuredClone(storeSchemas);
          let aborted = false;
          request.transaction = {
            abort() {
              aborted = true;
              stores.clear();
              for (const [storeName, records] of snapshot) stores.set(storeName, records);
              storeSchemas.clear();
              for (const [storeName, schema] of schemaSnapshot) {
                storeSchemas.set(storeName, schema);
              }
            },
            objectStore(storeName) {
              const schema = storeSchemas.get(storeName);
              if (!schema) throw new Error('missing upgrade store');
              return {
                keyPath: schema.keyPath, autoIncrement: schema.autoIncrement,
                indexNames: {
                  get length() { return schema.indexNames.length; },
                  item: (index) => schema.indexNames[index] || null,
                  [Symbol.iterator]: () => schema.indexNames[Symbol.iterator](),
                },
              };
            },
          };
          request.onupgradeneeded?.({ target: request, oldVersion: databaseVersion,
            newVersion: version });
          if (aborted) {
            request.error = new Error('upgrade aborted');
            request.onerror?.({ target: request });
            return;
          }
          databaseVersion = version;
        }
        request.onsuccess?.({ target: request });
      });
      return request;
    },
  };
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`
    )).join(',')}}`;
  }
  return JSON.stringify(value);
}

const hex = (bytes) => Buffer.from(bytes).toString('hex');

after(() => {
  if (originalIndexedDb === undefined) delete globalThis.indexedDB;
  else globalThis.indexedDB = originalIndexedDb;
});

test('browser bewaart één niet-extraheerbare P-256 sleutel en tekent exact servercompatibel', async () => {
  const indexedDb = memoryIndexedDb();
  globalThis.indexedDB = indexedDb;
  const firstModule = await import('../src/web/public/httpackclient.js?browser-run=first');
  const firstPublicKey = await firstModule.clientAckPublicKey();
  assert.deepEqual(Object.keys(firstPublicKey).sort(), [
    'algorithm', 'contract', 'crv', 'kty', 'x', 'y',
  ]);
  assert.equal(firstPublicKey.contract, 'HttpClientAckPublicKey/v3');
  assert.equal(firstPublicKey.algorithm, 'ES256');
  assert.match(firstPublicKey.x, /^[A-Za-z0-9_-]{43}$/u);
  assert.match(firstPublicKey.y, /^[A-Za-z0-9_-]{43}$/u);

  const stored = indexedDb.records.get('primary');
  assert.equal(stored.privateKey.type, 'private');
  assert.equal(stored.privateKey.extractable, false);
  assert.deepEqual(stored.privateKey.usages, ['sign']);
  assert.equal(stored.publicKey.type, 'public');
  assert.equal(stored.publicKey.extractable, true);
  assert.equal(indexedDb.writes, 1);

  const restartedModule = await import('../src/web/public/httpackclient.js?browser-run=restarted');
  assert.deepEqual(await restartedModule.clientAckPublicKey(), firstPublicKey);
  assert.equal(indexedDb.writes, 1, 'een browserherstart roteert de durable ACK-sleutel niet');

  const spki = new Uint8Array(await crypto.subtle.exportKey('spki', stored.publicKey));
  const fingerprint = `p256-spki-sha256:${hex(await crypto.subtle.digest('SHA-256', spki))}`;
  const responseSha256 = createHash('sha256').update('response').digest('hex');
  const body = {
    conversation_id: 'web:browser-ack', ok: true, reply: 'Exact antwoord: één.',
    request_id: 'browser-ack:00000001', request_replay_scope: 'durable_http_ingress',
    runtime_code_sha256: createHash('sha256').update('runtime').digest('hex'),
    turn_id: 'turn:browser-ack:00000001',
    _httpDelivery: {
      contract: 'HttpClientAckChallenge/v3',
      requestRef: `http-ingress:${createHash('sha256').update('request').digest('hex')}`,
      responseRef: `http-response:${responseSha256}`, responseSha256,
      deliveryRef: `http-delivery-v3:${createHash('sha256').update('delivery').digest('hex')}`,
      challenge: createHash('sha256').update('challenge').digest('hex'),
      keyFingerprint: fingerprint,
    },
  };
  const bodyBytes = new TextEncoder().encode(canonicalJson(body));
  const writeAttemptRef = `http-write-attempt-v3:${createHash('sha256')
    .update('browser-write').digest('hex')}`;
  const ack = await restartedModule.signHttpResponseAck({
    status: 200, contentType: 'application/json; charset=utf-8', bodyBytes,
    expectedRequestId: body.request_id, writeAttemptRef, writeGeneration: 1,
  });
  assert.deepEqual(Object.keys(ack).sort(), [
    'challenge', 'contract', 'deliveryRef', 'requestRef', 'responseRef',
    'responseSha256', 'signature', 'wireSha256', 'writeAttemptRef', 'writeGeneration',
  ]);
  assert.match(ack.signature, /^[A-Za-z0-9_-]{86}$/u);

  const { verifyHttpClientAck } = await import('../src/web/httpack.js');
  assert.equal(verifyHttpClientAck(ack, firstPublicKey).ack.wireSha256, ack.wireSha256);
  await assert.rejects(() => restartedModule.signHttpResponseAck({
    status: 201, contentType: 'application/json; charset=utf-8', bodyBytes,
    expectedRequestId: body.request_id, writeAttemptRef, writeGeneration: 1,
  }), /status|wire|response/iu);
  const tampered = new Uint8Array(bodyBytes);
  tampered[tampered.length - 2] ^= 1;
  await assert.rejects(() => restartedModule.signHttpResponseAck({
    status: 200, contentType: 'application/json; charset=utf-8', bodyBytes: tampered,
    expectedRequestId: body.request_id, writeAttemptRef, writeGeneration: 1,
  }), /canonical|JSON|response/iu);
});

test('beschadigde durable browseridentiteit faalt gesloten zonder sleutelrotatie', async () => {
  const indexedDb = memoryIndexedDb();
  globalThis.indexedDB = indexedDb;
  const firstModule = await import('../src/web/public/httpackclient.js?tamper-run=seed');
  await firstModule.clientAckPublicKey();
  const stored = indexedDb.records.get('primary');
  indexedDb.records.set('primary', {
    ...stored, publicJwk: { ...stored.publicJwk, x: 'A'.repeat(43) },
  });
  const writesBefore = indexedDb.writes;
  const restartedModule = await import('../src/web/public/httpackclient.js?tamper-run=restart');
  await assert.rejects(
    () => restartedModule.clientAckPublicKey(),
    /wijkt af|ongeldig|sleutel/iu,
  );
  assert.equal(indexedDb.writes, writesBefore,
    'corruptie mag niet stil een nieuwe clientidentiteit uitgeven');
});

test('browserkey-keuze telt en leest atomair en overschrijft geen bestaande corrupte namespace', async () => {
  const indexedDb = memoryIndexedDb();
  globalThis.indexedDB = indexedDb;
  const seededModule = await import('../src/web/public/httpackclient.js?atomic-run=seed');
  await seededModule.clientAckPublicKey();
  indexedDb.records.set('unexpected-existing-record', { corrupt: true });
  const writesBefore = indexedDb.writes;
  const restartedModule = await import('../src/web/public/httpackclient.js?atomic-run=restart');
  await assert.rejects(
    () => restartedModule.clientAckPublicKey(),
    /namespace|database|identity|sleutel/iu,
  );
  assert.ok(indexedDb.countReads >= 1, 'dezelfde readwrite-transactie moet eerst exact tellen');
  assert.equal(indexedDb.writes, writesBefore, 'een bestaande namespace wordt nooit overschreven');
});

test('browser-ACK API bevat durable raw-wire opslag voor crashherstel', async () => {
  const indexedDb = memoryIndexedDb();
  globalThis.indexedDB = indexedDb;
  const client = await import('../src/web/public/httpackclient.js?pending-api=red');
  assert.equal(typeof client.storePendingHttpDelivery, 'function');
  assert.equal(typeof client.loadPendingHttpDelivery, 'function');
  assert.equal(typeof client.clearPendingHttpDelivery, 'function');
  assert.equal(typeof client.clearPendingHttpDeliveryOrReload, 'function');
  assert.equal(typeof client.runHttpChatSubmitSingleFlight, 'function');
});

test('chat-submit claimt synchroon singleflight vóór netwerk en omvat clear en display', async () => {
  const client = await import('../src/web/public/httpackclient.js?submit-singleflight=red');
  let releaseNetwork;
  const networkPaused = new Promise((resolve) => { releaseNetwork = resolve; });
  const calls = { network: 0, clear: 0, display: 0, duplicate: 0 };
  const first = client.runHttpChatSubmitSingleFlight(async () => {
    calls.network += 1;
    await networkPaused;
    calls.clear += 1;
    calls.display += 1;
  });
  const second = client.runHttpChatSubmitSingleFlight(async () => {
    calls.duplicate += 1;
  });
  assert.deepEqual(calls, { network: 1, clear: 0, display: 0, duplicate: 0 },
    'de claim staat vóór de eerste await en laat geen tweede operatie starten');
  releaseNetwork();
  assert.deepEqual(await Promise.all([first, second]), [true, false]);
  assert.deepEqual(calls, { network: 1, clear: 1, display: 1, duplicate: 0 });

  const appSource = readFileSync(new URL('../src/web/public/app.js', import.meta.url), 'utf8');
  const submitStart = appSource.indexOf('async function submitChat()');
  const handlerStart = appSource.indexOf("$('#chat-form').addEventListener('submit'");
  const handlerEnd = appSource.indexOf("pendingChatRequest().then", handlerStart);
  const submit = appSource.slice(submitStart, handlerStart);
  const handler = appSource.slice(handlerStart, handlerEnd);
  assert.ok(submitStart >= 0 && handlerStart > submitStart && handlerEnd > handlerStart);
  assert.equal(handler.indexOf('await '), handler.indexOf('await runHttpChatSubmitSingleFlight('),
    'de app claimt de module-singleflight als eerste async stap');
  assert.ok(submit.includes('clearPendingHttpDeliveryOrReload({'),
    'ACK-clear gebruikt de duurzame CAS-loss-herlaadroute');
  assert.equal(submit.includes('await clearPendingHttpDelivery({'), false,
    'de app omzeilt de CAS-loss-herlaadroute niet');
});

test('IndexedDB-upgrade accepteert alleen exact v0 of de bekende v1-namespace atomair', async () => {
  const exactV0 = memoryIndexedDb();
  globalThis.indexedDB = exactV0;
  const initialized = await import('../src/web/public/httpackclient.js?idb-upgrade=exact-v0');
  await initialized.clientAckPublicKey();
  assert.equal(exactV0.version, 2);
  assert.deepEqual(exactV0.storeNames, ['client-keys', 'pending-deliveries']);

  const exactV1 = memoryIndexedDb();
  exactV1.seedSchema(1, ['client-keys']);
  globalThis.indexedDB = exactV1;
  const migrated = await import('../src/web/public/httpackclient.js?idb-upgrade=exact-v1');
  await migrated.clientAckPublicKey();
  assert.equal(exactV1.version, 2);
  assert.deepEqual(exactV1.storeNames, ['client-keys', 'pending-deliveries']);

  for (const [label, names] of [
    ['corrupt-v0', ['unexpected-store']],
    ['extra', ['client-keys', 'unexpected-store']],
    ['missing', ['pending-deliveries']],
  ]) {
    const corrupt = memoryIndexedDb();
    corrupt.seedSchema(label === 'corrupt-v0' ? 0 : 1, names);
    globalThis.indexedDB = corrupt;
    const client = await import(`../src/web/public/httpackclient.js?idb-upgrade=${label}`);
    await assert.rejects(() => client.clientAckPublicKey(), /database|upgrade|geblokkeerd/iu);
    assert.equal(corrupt.version, label === 'corrupt-v0' ? 0 : 1);
    assert.deepEqual(corrupt.storeNames, [...names].sort());
  }

  for (const [label, schema] of [
    ['key-path', { keyPath: 'id' }],
    ['auto-increment', { autoIncrement: true }],
    ['index', { indexNames: ['unexpected-index'] }],
  ]) {
    const corrupt = memoryIndexedDb();
    corrupt.seedSchema(1, ['client-keys'], { 'client-keys': schema });
    corrupt.records.set('legacy-row', { remains: label });
    globalThis.indexedDB = corrupt;
    const client = await import(`../src/web/public/httpackclient.js?idb-schema=${label}`);
    await assert.rejects(() => client.clientAckPublicKey(), /database|upgrade|schema/iu);
    assert.equal(corrupt.version, 1);
    assert.deepEqual(corrupt.storeNames, ['client-keys']);
    assert.deepEqual(corrupt.records.get('legacy-row'), { remains: label });
  }

  const corruptV2 = memoryIndexedDb();
  corruptV2.seedSchema(2, ['client-keys', 'pending-deliveries'], {
    'pending-deliveries': { keyPath: 'unexpected' },
  });
  globalThis.indexedDB = corruptV2;
  const currentCorrupt = await import('../src/web/public/httpackclient.js?idb-schema=current');
  await assert.rejects(() => currentCorrupt.clientAckPublicKey(), /database|schema|geattesteerd/iu);
  assert.equal(corruptV2.version, 2);
  assert.deepEqual(corruptV2.storeNames, ['client-keys', 'pending-deliveries']);

  const forcedV0 = memoryIndexedDb();
  forcedV0.failCreate('pending-deliveries');
  globalThis.indexedDB = forcedV0;
  const failedV0 = await import('../src/web/public/httpackclient.js?idb-upgrade=forced-v0');
  await assert.rejects(() => failedV0.clientAckPublicKey(), /database|upgrade/iu);
  assert.equal(forcedV0.version, 0);
  assert.deepEqual(forcedV0.storeNames, [],
    'de eerder gemaakte client-key store wordt door upgrade-abort teruggedraaid');

  const forced = memoryIndexedDb();
  forced.seedSchema(1, ['client-keys']);
  forced.records.set('legacy-row', { exact: 'blijft bytegelijk' });
  const legacyBefore = structuredClone(forced.records.get('legacy-row'));
  forced.failCreate('pending-deliveries');
  globalThis.indexedDB = forced;
  const failed = await import('../src/web/public/httpackclient.js?idb-upgrade=forced-create');
  await assert.rejects(() => failed.clientAckPublicKey(), /database|upgrade/iu);
  assert.equal(forced.version, 1);
  assert.deepEqual(forced.storeNames, ['client-keys']);
  assert.deepEqual(forced.records.get('legacy-row'), legacyBefore);
});

test('twee browsertabs kiezen atomair exact dezelfde eerste clientkey', async () => {
  const indexedDb = memoryIndexedDb();
  globalThis.indexedDB = indexedDb;
  const [firstModule, secondModule] = await Promise.all([
    import('../src/web/public/httpackclient.js?two-tab=first'),
    import('../src/web/public/httpackclient.js?two-tab=second'),
  ]);
  const [firstKey, secondKey] = await Promise.all([
    firstModule.clientAckPublicKey(), secondModule.clientAckPublicKey(),
  ]);
  assert.deepEqual(secondKey, firstKey);
  assert.equal(indexedDb.records.size, 1);
  assert.equal(indexedDb.writes, 1);
});

test('een bestaande undefined keywaarde wordt niet als afwezige key overschreven', async () => {
  const indexedDb = memoryIndexedDb();
  globalThis.indexedDB = indexedDb;
  const seeded = await import('../src/web/public/httpackclient.js?undefined-key=seed');
  await seeded.clientAckPublicKey();
  indexedDb.records.set('primary', undefined);
  const writesBefore = indexedDb.writes;
  const restarted = await import('../src/web/public/httpackclient.js?undefined-key=restart');
  await assert.rejects(() => restarted.clientAckPublicKey(), /namespace|ongeldig|sleutel/iu);
  assert.equal(indexedDb.writes, writesBefore);
  assert.equal(indexedDb.records.get('primary'), undefined);
});

test('pending request bewaart raw wire en ACK duurzaam en hervat via exacte herondertekening', async () => {
  const indexedDb = memoryIndexedDb();
  globalThis.indexedDB = indexedDb;
  const client = await import('../src/web/public/httpackclient.js?pending=seed');
  const publicKey = await client.clientAckPublicKey();
  const request = {
    requestId: 'browser-pending:00000001', sender: 'Jaap', text: 'hervat exact',
    clientAckPublicKey: publicKey,
  };
  const pendingRequest = await client.storePendingHttpRequest(request);
  assert.equal(pendingRequest.state, 'request_pending');
  const storedKey = indexedDb.records.get('primary');
  const spki = new Uint8Array(await crypto.subtle.exportKey('spki', storedKey.publicKey));
  const fingerprint = `p256-spki-sha256:${hex(await crypto.subtle.digest('SHA-256', spki))}`;
  const responseSha256 = createHash('sha256').update('pending-response').digest('hex');
  const body = {
    conversation_id: 'web:browser-pending', ok: true, reply: 'duurzaam antwoord',
    request_id: request.requestId, request_replay_scope: 'durable_http_ingress',
    runtime_code_sha256: createHash('sha256').update('pending-runtime').digest('hex'),
    turn_id: 'turn:browser-pending:00000001',
    _httpDelivery: {
      contract: 'HttpClientAckChallenge/v3',
      requestRef: `http-ingress:${createHash('sha256').update('pending-request').digest('hex')}`,
      responseRef: `http-response:${responseSha256}`, responseSha256,
      deliveryRef: `http-delivery-v3:${createHash('sha256').update('pending-delivery').digest('hex')}`,
      challenge: createHash('sha256').update('pending-challenge').digest('hex'),
      keyFingerprint: fingerprint,
    },
  };
  const bodyBytes = new TextEncoder().encode(canonicalJson(body));
  const stored = await client.storePendingHttpDelivery({
    ...request, status: 200, contentType: 'application/json; charset=utf-8', bodyBytes,
    writeAttemptRef: `http-write-attempt-v3:${createHash('sha256')
      .update('pending-write').digest('hex')}`,
    writeGeneration: 1,
  });
  assert.equal(stored.state, 'ack_pending');
  assert.deepEqual(stored.bodyBytes, bodyBytes);
  assert.equal(stored.ack.deliveryRef, body._httpDelivery.deliveryRef);

  const exactStoredRecord = structuredClone(indexedDb.pendingRecords.get('chat-primary'));
  const corruptBody = { ...body, reply: 'stil gewijzigd antwoord' };
  const corruptRecord = {
    ...exactStoredRecord,
    bodyBytes: new TextEncoder().encode(canonicalJson(corruptBody)),
  };
  indexedDb.pendingRecords.set('chat-primary', corruptRecord);
  const writesBeforeCorruptLoad = indexedDb.writes;
  const deletesBeforeCorruptLoad = indexedDb.deletes;
  const corruptRestart = await import('../src/web/public/httpackclient.js?pending=corrupt-wire');
  const originalSign = crypto.subtle.sign;
  let signCalls = 0;
  crypto.subtle.sign = async function countedSign(...args) {
    signCalls += 1;
    return originalSign.apply(this, args);
  };
  try {
    await assert.rejects(() => corruptRestart.loadPendingHttpDelivery(), /wire|ACK|ongeldig/iu);
    await assert.rejects(() => corruptRestart.retryPendingHttpDelivery({
      requestId: request.requestId, deliveryRef: exactStoredRecord.ack.deliveryRef,
      writeAttemptRef: exactStoredRecord.ack.writeAttemptRef,
      writeGeneration: exactStoredRecord.ack.writeGeneration,
    }), /exact|past niet|retry/iu);
  } finally {
    crypto.subtle.sign = originalSign;
  }
  assert.equal(signCalls, 0, 'corrupte wire wordt nooit opnieuw ondertekend');
  assert.equal(indexedDb.writes, writesBeforeCorruptLoad,
    'corrupte raw wire mag niet opnieuw worden getekend of opgeslagen');
  assert.equal(indexedDb.deletes, deletesBeforeCorruptLoad,
    'corrupte raw wire mag niet worden gewist');
  assert.deepEqual(indexedDb.pendingRecords.get('chat-primary'), corruptRecord,
    'corrupte record blijft fail-closed bytegelijk staan');
  indexedDb.pendingRecords.set('chat-primary', exactStoredRecord);

  const namespaceRace = await import('../src/web/public/httpackclient.js?pending=namespace-race');
  const writesBeforeNamespaceRace = indexedDb.writes;
  const extraRecord = { corrupt: 'door concurrerende browsertab toegevoegd' };
  indexedDb.onNextWrite(({ name, records }) => {
    if (name === 'pending-deliveries') records.set('unexpected-pending', extraRecord);
  });
  const signBeforeNamespaceRace = crypto.subtle.sign;
  let namespaceRaceSigns = 0;
  crypto.subtle.sign = async function countedNamespaceRaceSign(...args) {
    namespaceRaceSigns += 1;
    return signBeforeNamespaceRace.apply(this, args);
  };
  try {
    await assert.rejects(
      () => namespaceRace.loadPendingHttpDelivery(),
      /namespace|veranderde/iu,
    );
    assert.equal(namespaceRaceSigns, 0,
      'namespace-CAS faalt vóór de duurzame ACK opnieuw wordt ondertekend');
    assert.equal(indexedDb.writes, writesBeforeNamespaceRace,
      'namespace-CAS-verlies schrijft geen hervatte ACK');
    assert.equal(indexedDb.pendingRecords.size, 2);
    assert.deepEqual(indexedDb.pendingRecords.get('chat-primary'), exactStoredRecord);
    assert.deepEqual(indexedDb.pendingRecords.get('unexpected-pending'), extraRecord);
  } finally {
    crypto.subtle.sign = signBeforeNamespaceRace;
    indexedDb.pendingRecords.delete('unexpected-pending');
    indexedDb.pendingRecords.set('chat-primary', exactStoredRecord);
  }

  const restarted = await import('../src/web/public/httpackclient.js?pending=restart');
  const resumed = await restarted.loadPendingHttpDelivery();
  assert.equal(resumed.status, 200);
  assert.equal(resumed.contentType, 'application/json; charset=utf-8');
  assert.deepEqual(resumed.bodyBytes, bodyBytes);
  assert.deepEqual(resumed.request, pendingRequest.request);
  const { verifyHttpClientAck } = await import('../src/web/httpack.js');
  assert.equal(verifyHttpClientAck(resumed.ack, publicKey).ack.deliveryRef,
    body._httpDelivery.deliveryRef);
  const ackPreimage = { ...resumed.ack };
  delete ackPreimage.signature;
  const ackReceipt = {
    contract: 'HttpClientAckReceipt/v3', deliveryRef: resumed.ack.deliveryRef,
    requestRef: resumed.ack.requestRef, responseRef: resumed.ack.responseRef,
    responseSha256: resumed.ack.responseSha256,
    ackChallengeSha256: createHash('sha256').update(resumed.ack.challenge).digest('hex'),
    wireSha256: resumed.ack.wireSha256, wireLength: bodyBytes.length,
    clientKeyFingerprint: fingerprint,
    ackPreimageSha256: createHash('sha256').update(canonicalJson(ackPreimage)).digest('hex'),
    writeAttemptRef: resumed.ack.writeAttemptRef,
    writeGeneration: resumed.ack.writeGeneration,
    turnId: body.turn_id,
    provenance: {
      contract: 'HttpResultProvenance/v1', kind: 'turn_result', ref: body.turn_id,
      sha256: createHash('sha256').update('pending-provenance').digest('hex'),
    },
  };
  const ackReceiptRef = `http-delivery-ack-v3:${createHash('sha256')
    .update(canonicalJson(ackReceipt)).digest('hex')}`;
  await assert.rejects(() => restarted.clearPendingHttpDelivery({
    requestId: request.requestId,
    deliveryRef: `http-delivery-v3:${'0'.repeat(64)}`,
    ackReceiptRef, ackReceipt,
  }), /past niet|identiteit/iu);
  await assert.rejects(() => restarted.clearPendingHttpDelivery({
    requestId: request.requestId, deliveryRef: resumed.ack.deliveryRef,
    ackReceiptRef: `http-delivery-ack-v3:${'0'.repeat(64)}`, ackReceipt,
  }), /receipt|past niet|identiteit/iu);
  for (const changedReceipt of [
    { ...ackReceipt, writeGeneration: ackReceipt.writeGeneration + 1 },
    { ...ackReceipt, turnId: 'turn:browser-pending:changed' },
    { ...ackReceipt, clientKeyFingerprint: `p256-spki-sha256:${'0'.repeat(64)}` },
    { ...ackReceipt, extra: true },
    { ...ackReceipt, provenance: { ...ackReceipt.provenance, ref: 'turn:other' } },
  ]) {
    const changedRef = `http-delivery-ack-v3:${createHash('sha256')
      .update(canonicalJson(changedReceipt)).digest('hex')}`;
    await assert.rejects(() => restarted.clearPendingHttpDelivery({
      requestId: request.requestId, deliveryRef: resumed.ack.deliveryRef,
      ackReceiptRef: changedRef, ackReceipt: changedReceipt,
    }), /receipt|past niet/iu);
    assert.equal(indexedDb.pendingRecords.has('chat-primary'), true);
  }

  const beforeRace = structuredClone(indexedDb.pendingRecords.get('chat-primary'));
  const writesBeforeRace = indexedDb.writes;
  const deletesBeforeRace = indexedDb.deletes;
  indexedDb.onNextWrite(({ name, records }) => {
    if (name !== 'pending-deliveries') return;
    const raced = records.get('chat-primary');
    records.set('chat-primary', {
      ...raced, request: { ...raced.request, text: 'concurrent gewijzigd' },
    });
  });
  await assert.rejects(() => restarted.clearPendingHttpDelivery({
    requestId: request.requestId, deliveryRef: resumed.ack.deliveryRef,
    ackReceiptRef, ackReceipt,
  }), /veranderde|past niet/iu);
  assert.equal(indexedDb.writes, writesBeforeRace);
  assert.equal(indexedDb.deletes, deletesBeforeRace);
  indexedDb.pendingRecords.set('chat-primary', beforeRace);

  const replacement = {
    contract: 'PendingHttpDelivery/v3', state: 'request_pending',
    request: beforeRace.request, status: null, contentType: null,
    bodyBytes: null, ack: null,
  };
  indexedDb.onNextWrite(({ name, records }) => {
    if (name === 'pending-deliveries') records.set('chat-primary', replacement);
  });
  const reconciled = await restarted.clearPendingHttpDeliveryOrReload({
    requestId: request.requestId, deliveryRef: resumed.ack.deliveryRef,
    ackReceiptRef, ackReceipt,
  });
  assert.deepEqual(Object.keys(reconciled).sort(), ['outcome', 'pending']);
  assert.equal(reconciled.outcome, 'pending_reloaded');
  assert.deepEqual(reconciled.pending, replacement,
    'CAS-verlies herlaadt uitsluitend de actuele durable state');
  assert.deepEqual(indexedDb.pendingRecords.get('chat-primary'), replacement,
    'de stale ACK-state wordt niet vanuit volatile geheugen teruggezet');
  assert.equal(indexedDb.writes, writesBeforeRace);
  assert.equal(indexedDb.deletes, deletesBeforeRace);
  indexedDb.pendingRecords.set('chat-primary', beforeRace);

  assert.equal(await restarted.clearPendingHttpDelivery({
    requestId: request.requestId, deliveryRef: resumed.ack.deliveryRef,
    ackReceiptRef, ackReceipt,
  }), true);
  assert.equal(indexedDb.deletes, deletesBeforeRace + 1);
  assert.equal(await restarted.loadPendingHttpDelivery(), null);
});
