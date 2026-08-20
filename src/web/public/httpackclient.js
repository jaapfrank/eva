const DATABASE_NAME = 'eva-http-client-ack-v3';
const DATABASE_VERSION = 2;
const KEY_STORE_NAME = 'client-keys';
const PENDING_STORE_NAME = 'pending-deliveries';
const STORE_NAMES = Object.freeze([KEY_STORE_NAME, PENDING_STORE_NAME]);
const PRIMARY_KEY = 'primary';
const PENDING_KEY = 'chat-primary';
const CONTENT_TYPE = 'application/json; charset=utf-8';
const SHA256 = /^[a-f0-9]{64}$/u;
const REQUEST_REF = /^http-ingress:[a-f0-9]{64}$/u;
const RESPONSE_REF = /^http-response:[a-f0-9]{64}$/u;
const DELIVERY_REF = /^http-delivery-v3:[a-f0-9]{64}$/u;
const WRITE_ATTEMPT_REF = /^http-write-attempt-v3:[a-f0-9]{64}$/u;
const ACK_RECEIPT_REF = /^http-delivery-ack-v3:[a-f0-9]{64}$/u;
const KEY_FINGERPRINT = /^p256-spki-sha256:[a-f0-9]{64}$/u;
const BASE64URL_32 = /^[A-Za-z0-9_-]{43}$/u;
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
let identityPromise = null;
let httpChatSubmitInFlight = false;

export async function runHttpChatSubmitSingleFlight(operation) {
  if (typeof operation !== 'function') {
    throw new TypeError('De HTTP-chat-submitoperatie ontbreekt.');
  }
  if (httpChatSubmitInFlight) return false;
  httpChatSubmitInFlight = true;
  try {
    await operation();
    return true;
  } finally {
    httpChatSubmitInFlight = false;
  }
}

function plainObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}

function exactKeys(value, keys) {
  return plainObject(value)
    && Object.keys(value).sort().join('\0') === [...keys].sort().join('\0');
}

function exactObjectStoreSchema(store) {
  if (!store || store.keyPath !== null || store.autoIncrement !== false) return false;
  const names = store.indexNames;
  if (!names || names.length !== 0) return false;
  return true;
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

function bytesToBase64Url(bytes) {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/u, '');
}

function base64UrlToBytes(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]+$/u.test(value)) return null;
  try {
    const standard = value.replaceAll('-', '+').replaceAll('_', '/');
    const padding = '='.repeat((4 - (standard.length % 4)) % 4);
    const decoded = Uint8Array.from(atob(`${standard}${padding}`), (character) => (
      character.charCodeAt(0)
    ));
    return bytesToBase64Url(decoded) === value ? decoded : null;
  } catch { return null; }
}

function canonicalCoordinate(value) {
  if (typeof value !== 'string' || !BASE64URL_32.test(value)) return false;
  try {
    const standard = value.replaceAll('-', '+').replaceAll('_', '/');
    const decoded = Uint8Array.from(atob(`${standard}=`), (character) => (
      character.charCodeAt(0)
    ));
    return decoded.length === 32 && bytesToBase64Url(decoded) === value;
  } catch { return false; }
}

function canonicalPublicKey(jwk) {
  const value = {
    contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256',
    kty: jwk?.kty, crv: jwk?.crv, x: jwk?.x, y: jwk?.y,
  };
  if (!exactKeys(value, ['contract', 'algorithm', 'kty', 'crv', 'x', 'y'])
    || value.kty !== 'EC' || value.crv !== 'P-256'
    || !canonicalCoordinate(value.x) || !canonicalCoordinate(value.y)) {
    throw new Error('De browser leverde geen canonieke P-256 publieke sleutel.');
  }
  return Object.freeze(value);
}

function exactClientAckPublicKey(value) {
  return exactKeys(value, ['contract', 'algorithm', 'kty', 'crv', 'x', 'y'])
    && value.contract === 'HttpClientAckPublicKey/v3' && value.algorithm === 'ES256'
    && value.kty === 'EC' && value.crv === 'P-256'
    && canonicalCoordinate(value.x) && canonicalCoordinate(value.y);
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    if (!globalThis.indexedDB) {
      reject(new Error('IndexedDB is niet beschikbaar voor de duurzame ACK-sleutel.'));
      return;
    }
    const request = globalThis.indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = (event) => {
      const names = [...request.result.objectStoreNames].sort();
      const oldVersion = Number(event.oldVersion);
      const exactEmpty = oldVersion === 0 && names.length === 0;
      let exactV1 = false;
      try {
        exactV1 = oldVersion === 1 && names.join('\0') === KEY_STORE_NAME
          && exactObjectStoreSchema(request.transaction.objectStore(KEY_STORE_NAME));
      } catch { exactV1 = false; }
      if (!exactEmpty && !exactV1) {
        request.transaction.abort();
        return;
      }
      try {
        for (const name of STORE_NAMES) {
          if (!request.result.objectStoreNames.contains(name)) {
            request.result.createObjectStore(name);
          }
        }
        if (!STORE_NAMES.every((name) => exactObjectStoreSchema(
          request.transaction.objectStore(name),
        ))) request.transaction.abort();
      } catch {
        request.transaction.abort();
      }
    };
    request.onsuccess = () => {
      const database = request.result;
      const names = [...database.objectStoreNames].sort();
      if (names.join('\0') !== [...STORE_NAMES].sort().join('\0')) {
        database.close();
        reject(new Error('De browser-ACK-database heeft een onverwachte namespace.'));
        return;
      }
      try {
        if (!STORE_NAMES.every((name) => exactObjectStoreSchema(
          database.transaction(name, 'readonly').objectStore(name),
        ))) {
          database.close();
          reject(new Error('De browser-ACK-database heeft een onverwacht storeschema.'));
          return;
        }
      } catch {
        database.close();
        reject(new Error('De browser-ACK-database kon niet exact worden geattesteerd.'));
        return;
      }
      resolve(database);
    };
    request.onerror = () => reject(request.error || new Error('ACK-sleuteldatabase openen mislukte.'));
    request.onblocked = () => reject(new Error('ACK-sleuteldatabase is geblokkeerd.'));
  });
}

function selectDurableIdentity(database, candidate) {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(KEY_STORE_NAME, 'readwrite');
    const store = transaction.objectStore(KEY_STORE_NAME);
    let selected = null;
    let countResult;
    let storedResult;
    let reads = 0;
    let selectionError = null;
    const select = () => {
      reads += 1;
      if (reads !== 2) return;
      if (countResult === 0 && storedResult === undefined) {
        selected = candidate;
        store.put(candidate, PRIMARY_KEY);
        return;
      }
      if (countResult === 1 && storedResult !== undefined) {
        selected = storedResult;
        return;
      }
      selectionError = new Error('De browser-ACK-sleutelnamespace is bestaand maar ongeldig.');
      transaction.abort();
    };
    const count = store.count();
    count.onsuccess = () => { countResult = count.result; select(); };
    count.onerror = () => reject(count.error || new Error('ACK-sleutelnamespace tellen mislukte.'));
    const stored = store.get(PRIMARY_KEY);
    stored.onsuccess = () => { storedResult = stored.result; select(); };
    stored.onerror = () => reject(stored.error || new Error('ACK-sleutel lezen mislukte.'));
    transaction.oncomplete = () => resolve(selected);
    transaction.onerror = () => reject(transaction.error || new Error('ACK-sleutel bewaren mislukte.'));
    transaction.onabort = () => reject(selectionError || transaction.error
      || new Error('ACK-sleutel bewaren stopte.'));
  });
}

async function generatedIdentityRecord() {
  const pair = await crypto.subtle.generateKey(
    { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify'],
  );
  const publicJwk = canonicalPublicKey(await crypto.subtle.exportKey('jwk', pair.publicKey));
  return {
    contract: 'HttpClientAckIdentity/v3', privateKey: pair.privateKey,
    publicKey: pair.publicKey, publicJwk,
  };
}

function exactKeyUsage(value, expected) {
  return Array.isArray(value?.usages)
    && [...value.usages].sort().join('\0') === [...expected].sort().join('\0');
}

async function validatedIdentity(record) {
  if (!exactKeys(record, ['contract', 'privateKey', 'publicKey', 'publicJwk'])
    || record.contract !== 'HttpClientAckIdentity/v3'
    || record.privateKey?.type !== 'private' || record.privateKey.extractable !== false
    || record.privateKey.algorithm?.name !== 'ECDSA'
    || record.privateKey.algorithm?.namedCurve !== 'P-256'
    || !exactKeyUsage(record.privateKey, ['sign'])
    || record.publicKey?.type !== 'public' || record.publicKey.extractable !== true
    || record.publicKey.algorithm?.name !== 'ECDSA'
    || record.publicKey.algorithm?.namedCurve !== 'P-256'
    || !exactKeyUsage(record.publicKey, ['verify'])) {
    throw new Error('De duurzame browser-ACK-sleutel is ongeldig; sleutelrotatie is geweigerd.');
  }
  const publicJwk = canonicalPublicKey(await crypto.subtle.exportKey('jwk', record.publicKey));
  if (canonicalJson(publicJwk) !== canonicalJson(record.publicJwk)) {
    throw new Error('De duurzame browser-ACK-sleutel wijkt af van haar publieke identiteit.');
  }
  const probe = encoder.encode('HttpClientAckIdentityProbe/v3');
  const signature = new Uint8Array(await crypto.subtle.sign(
    { name: 'ECDSA', hash: 'SHA-256' }, record.privateKey, probe,
  ));
  if (signature.length !== 64 || !await crypto.subtle.verify(
    { name: 'ECDSA', hash: 'SHA-256' }, record.publicKey, signature, probe,
  )) throw new Error('De duurzame browser-ACK-sleutelpaarbinding is ongeldig.');
  const spki = new Uint8Array(await crypto.subtle.exportKey('spki', record.publicKey));
  const fingerprint = `p256-spki-sha256:${await sha256Hex(spki)}`;
  return Object.freeze({
    privateKey: record.privateKey, publicKey: record.publicKey, publicJwk, fingerprint,
  });
}

async function loadIdentity() {
  const database = await openDatabase();
  try {
    const candidate = await generatedIdentityRecord();
    return await validatedIdentity(await selectDurableIdentity(database, candidate));
  } finally {
    database.close();
  }
}

function identity() {
  if (!identityPromise) {
    identityPromise = loadIdentity().catch((error) => {
      identityPromise = null;
      throw error;
    });
  }
  return identityPromise;
}

async function sha256Hex(bytes) {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

function expectedStatus(body) {
  if (body?.reason === 'request_superseded') return 409;
  if (body?.execution_pending === true && body?.durable_handoff === true) return 202;
  if (Object.hasOwn(body || {}, 'error')) return 500;
  return 200;
}

function exactDeliveryEnvelope(value) {
  return exactKeys(value, [
    'contract', 'requestRef', 'responseRef', 'responseSha256',
    'deliveryRef', 'challenge', 'keyFingerprint',
  ]) && value.contract === 'HttpClientAckChallenge/v3'
    && REQUEST_REF.test(value.requestRef) && RESPONSE_REF.test(value.responseRef)
    && SHA256.test(value.responseSha256)
    && value.responseRef === `http-response:${value.responseSha256}`
    && DELIVERY_REF.test(value.deliveryRef) && SHA256.test(value.challenge)
    && KEY_FINGERPRINT.test(value.keyFingerprint);
}

function exactAck(value) {
  return exactKeys(value, [
    'contract', 'requestRef', 'responseRef', 'responseSha256',
    'deliveryRef', 'challenge', 'wireSha256', 'writeAttemptRef',
    'writeGeneration', 'signature',
  ]) && value.contract === 'HttpClientAck/v3'
    && REQUEST_REF.test(value.requestRef) && RESPONSE_REF.test(value.responseRef)
    && SHA256.test(value.responseSha256)
    && value.responseRef === `http-response:${value.responseSha256}`
    && DELIVERY_REF.test(value.deliveryRef) && SHA256.test(value.challenge)
    && SHA256.test(value.wireSha256)
    && WRITE_ATTEMPT_REF.test(value.writeAttemptRef)
    && Number.isSafeInteger(value.writeGeneration)
    && value.writeGeneration >= 1 && value.writeGeneration <= 64
    && /^[A-Za-z0-9_-]{86}$/u.test(value.signature);
}

function exactPendingRequestShape(value) {
  return exactKeys(value, [
    'contract', 'requestId', 'sender', 'text', 'clientAckPublicKey',
  ]) && value.contract === 'PendingPublicHttpRequest/v3'
    && /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/u.test(value.requestId)
    && typeof value.sender === 'string' && value.sender.trim() === value.sender
    && value.sender.length >= 1 && value.sender.length <= 160
    && typeof value.text === 'string' && value.text.trim()
    && value.text.length <= 64 * 1024
    && exactClientAckPublicKey(value.clientAckPublicKey);
}

function exactPendingRequest(value, publicJwk) {
  return exactPendingRequestShape(value)
    && canonicalJson(value.clientAckPublicKey) === canonicalJson(publicJwk);
}

async function verifiedStoredAck(value, currentIdentity) {
  if (!exactAck(value)) return false;
  const signature = base64UrlToBytes(value.signature);
  if (!signature || signature.length !== 64) return false;
  const preimage = { ...value };
  delete preimage.signature;
  return crypto.subtle.verify(
    { name: 'ECDSA', hash: 'SHA-256' }, currentIdentity.publicKey,
    signature, encoder.encode(canonicalJson(preimage)),
  );
}

function pendingStoreTransaction(database, mode, operation) {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(PENDING_STORE_NAME, mode);
    const store = transaction.objectStore(PENDING_STORE_NAME);
    let result;
    let operationError = null;
    try {
      operation({
        store,
        resolveOnComplete: (value) => { result = value; },
        fail: (error) => {
          operationError = error;
          transaction.abort();
        },
      });
    } catch (error) {
      operationError = error;
      transaction.abort();
    }
    transaction.oncomplete = () => resolve(result);
    transaction.onerror = () => reject(transaction.error
      || operationError || new Error('Pending HTTP-deliverytransactie mislukte.'));
    transaction.onabort = () => reject(operationError || transaction.error
      || new Error('Pending HTTP-deliverytransactie stopte.'));
  });
}

function exactPendingRecordShape(value) {
  if (!exactKeys(value, [
    'contract', 'state', 'request', 'status', 'contentType', 'bodyBytes', 'ack',
  ]) || value.contract !== 'PendingHttpDelivery/v3'
    || !['request_pending', 'ack_pending'].includes(value.state)
    || !exactPendingRequestShape(value.request)) return false;
  if (value.state === 'request_pending') {
    return value.status === null && value.contentType === null
      && value.bodyBytes === null && value.ack === null;
  }
  return Number.isSafeInteger(value.status) && value.status >= 200 && value.status <= 599
    && value.contentType === CONTENT_TYPE && value.bodyBytes instanceof Uint8Array
    && value.bodyBytes.length >= 2 && value.bodyBytes.length <= 24 * 1024
    && exactAck(value.ack);
}

function exactPendingRecord(value, publicJwk) {
  return exactPendingRecordShape(value)
    && canonicalJson(value.request.clientAckPublicKey) === canonicalJson(publicJwk);
}

async function exactPendingAckBinding(record, ack) {
  let body;
  let rawBody;
  try {
    rawBody = decoder.decode(record.bodyBytes);
    body = JSON.parse(rawBody);
  } catch { return false; }
  const envelope = body?._httpDelivery;
  if (!plainObject(body) || canonicalJson(body) !== rawBody
    || record.status !== expectedStatus(body) || !exactDeliveryEnvelope(envelope)
    || body.request_id !== record.request.requestId) return false;
  const bodySha256 = await sha256Hex(record.bodyBytes);
  const binding = canonicalJson({
    contract: 'CanonicalHttpWireBinding/v3', status: record.status,
    contentType: record.contentType, bodySha256, bodyLength: record.bodyBytes.length,
  });
  const prefix = encoder.encode(`${binding}\n`);
  const wire = new Uint8Array(prefix.length + record.bodyBytes.length);
  wire.set(prefix, 0);
  wire.set(record.bodyBytes, prefix.length);
  return body.request_id === record.request.requestId
    && ack.requestRef === envelope.requestRef
    && ack.responseRef === envelope.responseRef
    && ack.responseSha256 === envelope.responseSha256
    && ack.deliveryRef === envelope.deliveryRef
    && ack.challenge === envelope.challenge
    && ack.wireSha256 === await sha256Hex(wire);
}

function sameBytes(left, right) {
  return left instanceof Uint8Array && right instanceof Uint8Array
    && left.length === right.length && left.every((byte, index) => byte === right[index]);
}

function samePendingRecord(left, right) {
  if (!left || !right || left.state !== 'ack_pending' || right.state !== 'ack_pending') {
    return false;
  }
  return canonicalJson(left.request) === canonicalJson(right.request)
    && left.status === right.status && left.contentType === right.contentType
    && sameBytes(left.bodyBytes, right.bodyBytes)
    && canonicalJson(left.ack) === canonicalJson(right.ack);
}

export async function clientAckPublicKey() {
  return (await identity()).publicJwk;
}

/**
 * @param {{
 *   requestId?: string,
 *   sender?: string,
 *   text?: string,
 *   clientAckPublicKey?: object
 * }} [input]
 */
export async function storePendingHttpRequest({ requestId, sender, text,
  clientAckPublicKey: publicKey } = {}) {
  const currentIdentity = await identity();
  const request = {
    contract: 'PendingPublicHttpRequest/v3', requestId, sender, text,
    clientAckPublicKey: publicKey,
  };
  if (!exactPendingRequest(request, currentIdentity.publicJwk)) {
    throw new TypeError('De pending HTTP-request is niet canoniek of gebruikt een andere clientkey.');
  }
  const candidate = {
    contract: 'PendingHttpDelivery/v3', state: 'request_pending', request,
    status: null, contentType: null, bodyBytes: null, ack: null,
  };
  const database = await openDatabase();
  try {
    return await pendingStoreTransaction(database, 'readwrite', ({ store,
      resolveOnComplete, fail }) => {
      let countResult;
      let storedResult;
      let reads = 0;
      const select = () => {
        reads += 1;
        if (reads !== 2) return;
        if (countResult === 0 && storedResult === undefined) {
          store.put(candidate, PENDING_KEY);
          resolveOnComplete(candidate);
        } else if (countResult === 1 && exactPendingRecord(storedResult, currentIdentity.publicJwk)
          && canonicalJson(storedResult.request) === canonicalJson(request)) {
          resolveOnComplete(storedResult);
        } else fail(new Error('Er bestaat al een andere of corrupte pending HTTP-request.'));
      };
      const count = store.count();
      count.onsuccess = () => { countResult = count.result; select(); };
      count.onerror = () => fail(count.error || new Error('Pending request tellen mislukte.'));
      const stored = store.get(PENDING_KEY);
      stored.onsuccess = () => { storedResult = stored.result; select(); };
      stored.onerror = () => fail(stored.error || new Error('Pending request lezen mislukte.'));
    });
  } finally { database.close(); }
}

/**
 * @param {{
 *   requestId?: string,
 *   sender?: string,
 *   text?: string,
 *   clientAckPublicKey?: object,
 *   status?: number,
 *   contentType?: string,
 *   bodyBytes?: Uint8Array,
 *   writeAttemptRef?: string,
 *   writeGeneration?: number
 * }} [input]
 */
export async function storePendingHttpDelivery({ requestId, sender, text,
  clientAckPublicKey: publicKey, status, contentType, bodyBytes,
  writeAttemptRef, writeGeneration } = {}) {
  const currentIdentity = await identity();
  const request = {
    contract: 'PendingPublicHttpRequest/v3', requestId, sender, text,
    clientAckPublicKey: publicKey,
  };
  if (!exactPendingRequest(request, currentIdentity.publicJwk)) {
    throw new TypeError('De pending HTTP-request is niet canoniek of gebruikt een andere clientkey.');
  }
  const bytes = bodyBytes instanceof Uint8Array ? new Uint8Array(bodyBytes) : bodyBytes;
  const ack = await signHttpResponseAck({
    status, contentType, bodyBytes: bytes, expectedRequestId: requestId,
    writeAttemptRef, writeGeneration,
  });
  const candidate = {
    contract: 'PendingHttpDelivery/v3', state: 'ack_pending', request,
    status, contentType, bodyBytes: bytes, ack,
  };
  if (!exactPendingRecord(candidate, currentIdentity.publicJwk)
    || !await exactPendingAckBinding(candidate, ack)) {
    throw new Error('De pending HTTP-response is niet exact aan request en ACK gebonden.');
  }
  const database = await openDatabase();
  try {
    return await pendingStoreTransaction(database, 'readwrite', ({ store,
      resolveOnComplete, fail }) => {
      const count = store.count();
      const stored = store.get(PENDING_KEY);
      let countResult;
      let storedResult;
      let reads = 0;
      const replace = () => {
        reads += 1;
        if (reads !== 2) return;
        if (countResult === 1 && exactPendingRecord(storedResult, currentIdentity.publicJwk)
          && storedResult.state === 'request_pending'
          && canonicalJson(storedResult.request) === canonicalJson(request)) {
          store.put(candidate, PENDING_KEY);
          resolveOnComplete(candidate);
        } else fail(new Error('De pending HTTP-response mist haar exacte duurzame request.'));
      };
      count.onsuccess = () => { countResult = count.result; replace(); };
      count.onerror = () => fail(count.error || new Error('Pending response tellen mislukte.'));
      stored.onsuccess = () => { storedResult = stored.result; replace(); };
      stored.onerror = () => fail(stored.error || new Error('Pending response lezen mislukte.'));
    });
  } finally { database.close(); }
}

export async function loadPendingHttpDelivery() {
  const database = await openDatabase();
  try {
    const record = await pendingStoreTransaction(database, 'readonly', ({ store,
      resolveOnComplete, fail }) => {
      const count = store.count();
      const stored = store.get(PENDING_KEY);
      let countResult;
      let storedResult;
      let reads = 0;
      const finish = () => {
        reads += 1;
        if (reads !== 2) return;
        if (countResult === 0 && storedResult === undefined) resolveOnComplete(null);
        else if (countResult === 1 && exactPendingRecordShape(storedResult)) {
          resolveOnComplete(storedResult);
        }
        else fail(new Error('De duurzame pending HTTP-deliverynamespace is ongeldig.'));
      };
      count.onsuccess = () => { countResult = count.result; finish(); };
      count.onerror = () => fail(count.error || new Error('Pending delivery tellen mislukte.'));
      stored.onsuccess = () => { storedResult = stored.result; finish(); };
      stored.onerror = () => fail(stored.error || new Error('Pending delivery lezen mislukte.'));
    });
    if (record?.state === 'ack_pending'
      && !await exactPendingAckBinding(record, record.ack)) {
      throw new Error('De duurzame pending HTTP-wire is ongeldig.');
    }
    if (record?.state === 'ack_pending') {
      await pendingStoreTransaction(database, 'readwrite', ({ store,
        resolveOnComplete, fail }) => {
        const count = store.count();
        const stored = store.get(PENDING_KEY);
        let countResult;
        let storedResult;
        let reads = 0;
        const attest = () => {
          reads += 1;
          if (reads !== 2) return;
          if (countResult !== 1 || !exactPendingRecordShape(storedResult)
            || !samePendingRecord(storedResult, record)) {
            fail(new Error('De pending HTTP-deliverynamespace veranderde vóór hervatten.'));
            return;
          }
          resolveOnComplete(true);
        };
        count.onsuccess = () => { countResult = count.result; attest(); };
        count.onerror = () => fail(count.error
          || new Error('Pending delivery tellen mislukte.'));
        stored.onsuccess = () => { storedResult = stored.result; attest(); };
        stored.onerror = () => fail(stored.error
          || new Error('Pending delivery herlezen mislukte.'));
      });
    }
    const currentIdentity = await identity();
    if (!record) return null;
    if (!exactPendingRecord(record, currentIdentity.publicJwk)) {
      throw new Error('De duurzame pending HTTP-delivery hoort niet bij deze clientkey.');
    }
    if (record.state === 'request_pending') return record;
    if (!await verifiedStoredAck(record.ack, currentIdentity)) {
      throw new Error('De duurzame pending HTTP-ACK is ongeldig.');
    }
    const ack = await signHttpResponseAck({
      status: record.status, contentType: record.contentType,
      bodyBytes: record.bodyBytes, expectedRequestId: record.request.requestId,
      writeAttemptRef: record.ack.writeAttemptRef,
      writeGeneration: record.ack.writeGeneration,
    });
    if (!await exactPendingAckBinding(record, ack)) {
      throw new Error('De duurzame pending HTTP-wire kon niet exact opnieuw worden getekend.');
    }
    const resumed = { ...record, bodyBytes: new Uint8Array(record.bodyBytes), ack };
    await pendingStoreTransaction(database, 'readwrite', ({ store,
      resolveOnComplete, fail }) => {
      const count = store.count();
      const stored = store.get(PENDING_KEY);
      let countResult;
      let storedResult;
      let reads = 0;
      const resume = () => {
        reads += 1;
        if (reads !== 2) return;
        if (countResult !== 1 || !exactPendingRecord(storedResult, currentIdentity.publicJwk)
          || !samePendingRecord(storedResult, record)) {
          fail(new Error('De pending HTTP-delivery veranderde tijdens hervatten.'));
          return;
        }
        store.put(resumed, PENDING_KEY);
        resolveOnComplete(resumed);
      };
      count.onsuccess = () => { countResult = count.result; resume(); };
      count.onerror = () => fail(count.error || new Error('Pending delivery tellen mislukte.'));
      stored.onsuccess = () => { storedResult = stored.result; resume(); };
      stored.onerror = () => fail(stored.error
        || new Error('Pending delivery herlezen mislukte.'));
    });
    return resumed;
  } finally { database.close(); }
}

async function exactAckReceipt(record, ackReceiptRef, receipt) {
  const keys = [
    'contract', 'deliveryRef', 'requestRef', 'responseRef', 'responseSha256',
    'ackChallengeSha256', 'wireSha256', 'wireLength', 'clientKeyFingerprint',
    'ackPreimageSha256', 'writeAttemptRef', 'writeGeneration', 'turnId', 'provenance',
  ];
  let body;
  let stableReceipt;
  let receiptJson;
  try {
    if (!plainObject(receipt)) return false;
    receiptJson = canonicalJson(receipt);
    stableReceipt = JSON.parse(receiptJson);
    body = JSON.parse(decoder.decode(record.bodyBytes));
  } catch { return false; }
  if (!exactKeys(stableReceipt, keys)
    || stableReceipt.contract !== 'HttpClientAckReceipt/v3'
    || !exactKeys(stableReceipt.provenance, ['contract', 'kind', 'ref', 'sha256'])
    || stableReceipt.provenance.contract !== 'HttpResultProvenance/v1'
    || !['turn_result', 'durable_handoff', 'request_supersession', 'ingress_rejection']
      .includes(stableReceipt.provenance.kind)
    || typeof stableReceipt.provenance.ref !== 'string'
    || !/^[A-Za-z0-9][A-Za-z0-9._:-]{7,239}$/u.test(stableReceipt.provenance.ref)
    || !SHA256.test(stableReceipt.provenance.sha256)
    || (stableReceipt.provenance.kind === 'turn_result'
      && (typeof stableReceipt.turnId !== 'string'
        || stableReceipt.provenance.ref !== stableReceipt.turnId))
    || stableReceipt.deliveryRef !== record.ack.deliveryRef
    || stableReceipt.requestRef !== record.ack.requestRef
    || stableReceipt.responseRef !== record.ack.responseRef
    || stableReceipt.responseSha256 !== record.ack.responseSha256
    || stableReceipt.wireSha256 !== record.ack.wireSha256
    || stableReceipt.wireLength !== record.bodyBytes.length
    || stableReceipt.clientKeyFingerprint !== body?._httpDelivery?.keyFingerprint
    || stableReceipt.writeAttemptRef !== record.ack.writeAttemptRef
    || stableReceipt.writeGeneration !== record.ack.writeGeneration
    || stableReceipt.turnId !== body?.turn_id) return false;
  const preimage = { ...record.ack };
  delete preimage.signature;
  return stableReceipt.ackChallengeSha256
      === await sha256Hex(encoder.encode(record.ack.challenge))
    && stableReceipt.ackPreimageSha256
      === await sha256Hex(encoder.encode(canonicalJson(preimage)))
    && ackReceiptRef === `http-delivery-ack-v3:${
      await sha256Hex(encoder.encode(receiptJson))}`;
}

/**
 * @param {{
 *   requestId?: string,
 *   deliveryRef?: string,
 *   ackReceiptRef?: string,
 *   ackReceipt?: object
 * }} [input]
 */
export async function clearPendingHttpDelivery({ requestId, deliveryRef,
  ackReceiptRef, ackReceipt } = {}) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/u.test(requestId)
    || !DELIVERY_REF.test(deliveryRef) || !ACK_RECEIPT_REF.test(ackReceiptRef)) {
    throw new TypeError('De bevestigde pending HTTP-identiteit is ongeldig.');
  }
  const database = await openDatabase();
  try {
    const original = await pendingStoreTransaction(database, 'readonly', ({ store,
      resolveOnComplete, fail }) => {
      const count = store.count();
      const stored = store.get(PENDING_KEY);
      let countResult;
      let storedResult;
      let reads = 0;
      const read = () => {
        reads += 1;
        if (reads !== 2) return;
        if (countResult === 1 && exactPendingRecordShape(storedResult)
          && storedResult.state === 'ack_pending') resolveOnComplete(storedResult);
        else fail(new Error('De ACK-bevestiging mist haar exacte pending HTTP-delivery.'));
      };
      count.onsuccess = () => { countResult = count.result; read(); };
      count.onerror = () => fail(count.error || new Error('Pending delivery tellen mislukte.'));
      stored.onsuccess = () => { storedResult = stored.result; read(); };
      stored.onerror = () => fail(stored.error || new Error('Pending delivery lezen mislukte.'));
    });
    if (!await exactPendingAckBinding(original, original.ack)) {
      throw new Error('De pending HTTP-wire is corrupt; wissen is geweigerd.');
    }
    const currentIdentity = await identity();
    if (!exactPendingRecord(original, currentIdentity.publicJwk)
      || original.request.requestId !== requestId || original.ack.deliveryRef !== deliveryRef
      || !await verifiedStoredAck(original.ack, currentIdentity)
      || !await exactAckReceipt(original, ackReceiptRef, ackReceipt)) {
      throw new Error('De ACK-receipt past niet exact op de pending HTTP-delivery.');
    }
    return await pendingStoreTransaction(database, 'readwrite', ({ store,
      resolveOnComplete, fail }) => {
      const count = store.count();
      const stored = store.get(PENDING_KEY);
      let countResult;
      let storedResult;
      let reads = 0;
      const clear = () => {
        reads += 1;
        if (reads !== 2) return;
        if (countResult !== 1 || !exactPendingRecord(storedResult, currentIdentity.publicJwk)
          || storedResult.state !== 'ack_pending'
          || storedResult.request.requestId !== requestId
          || storedResult.ack.deliveryRef !== deliveryRef
          || !samePendingRecord(storedResult, original)) {
          fail(new Error('De ACK-bevestiging past niet op de pending HTTP-delivery.'));
          return;
        }
        store.delete(PENDING_KEY);
        resolveOnComplete(true);
      };
      count.onsuccess = () => { countResult = count.result; clear(); };
      count.onerror = () => fail(count.error || new Error('Pending delivery tellen mislukte.'));
      stored.onsuccess = () => { storedResult = stored.result; clear(); };
      stored.onerror = () => fail(stored.error || new Error('Pending delivery lezen mislukte.'));
    });
  } finally { database.close(); }
}

export async function clearPendingHttpDeliveryOrReload(confirmation) {
  try {
    await clearPendingHttpDelivery(confirmation);
    return Object.freeze({ outcome: 'cleared', pending: null });
  } catch (clearError) {
    try {
      const pending = await loadPendingHttpDelivery();
      return Object.freeze({
        outcome: pending ? 'pending_reloaded' : 'already_cleared', pending,
      });
    } catch (reloadError) {
      throw new AggregateError(
        [clearError, reloadError],
        'De ACK-clear verloor haar CAS en de duurzame pending state kon niet worden herladen.',
      );
    }
  }
}

/**
 * @param {{
 *   requestId?: string,
 *   deliveryRef?: string,
 *   writeAttemptRef?: string,
 *   writeGeneration?: number
 * }} [input]
 */
export async function retryPendingHttpDelivery({ requestId, deliveryRef,
  writeAttemptRef, writeGeneration } = {}) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/u.test(requestId)
    || !DELIVERY_REF.test(deliveryRef) || !WRITE_ATTEMPT_REF.test(writeAttemptRef)
    || !Number.isSafeInteger(writeGeneration)
    || writeGeneration < 1 || writeGeneration > 64) {
    throw new TypeError('De retrybare pending HTTP-write-identiteit is ongeldig.');
  }
  const database = await openDatabase();
  try {
    const original = await pendingStoreTransaction(database, 'readonly', ({ store,
      resolveOnComplete, fail }) => {
      const count = store.count();
      const stored = store.get(PENDING_KEY);
      let countResult;
      let storedResult;
      let reads = 0;
      const read = () => {
        reads += 1;
        if (reads !== 2) return;
        if (countResult === 1 && exactPendingRecordShape(storedResult)
          && storedResult.state === 'ack_pending') {
          resolveOnComplete(storedResult);
        } else fail(new Error('De retry-instructie past niet op de pending HTTP-write.'));
      };
      count.onsuccess = () => { countResult = count.result; read(); };
      count.onerror = () => fail(count.error || new Error('Pending retry tellen mislukte.'));
      stored.onsuccess = () => { storedResult = stored.result; read(); };
      stored.onerror = () => fail(stored.error || new Error('Pending retry lezen mislukte.'));
    });
    if (!await exactPendingAckBinding(original, original.ack)) {
      throw new Error('De pending HTTP-wire is corrupt; retry is geweigerd.');
    }
    const currentIdentity = await identity();
    if (!exactPendingRecord(original, currentIdentity.publicJwk)
      || original.request.requestId !== requestId
      || original.ack.deliveryRef !== deliveryRef
      || original.ack.writeAttemptRef !== writeAttemptRef
      || original.ack.writeGeneration !== writeGeneration
      || !await verifiedStoredAck(original.ack, currentIdentity)) {
      throw new Error('De retry-instructie past niet op de exacte pending HTTP-write.');
    }
    return await pendingStoreTransaction(database, 'readwrite', ({ store,
      resolveOnComplete, fail }) => {
      const count = store.count();
      const stored = store.get(PENDING_KEY);
      let countResult;
      let storedResult;
      let reads = 0;
      const retry = () => {
        reads += 1;
        if (reads !== 2) return;
        if (countResult !== 1 || !exactPendingRecord(storedResult, currentIdentity.publicJwk)
          || !samePendingRecord(storedResult, original)) {
          fail(new Error('De pending HTTP-write veranderde tijdens retry.'));
          return;
        }
        const candidate = {
          contract: 'PendingHttpDelivery/v3', state: 'request_pending',
          request: storedResult.request, status: null, contentType: null,
          bodyBytes: null, ack: null,
        };
        store.put(candidate, PENDING_KEY);
        resolveOnComplete(candidate);
      };
      count.onsuccess = () => { countResult = count.result; retry(); };
      count.onerror = () => fail(count.error || new Error('Pending retry tellen mislukte.'));
      stored.onsuccess = () => { storedResult = stored.result; retry(); };
      stored.onerror = () => fail(stored.error || new Error('Pending retry lezen mislukte.'));
    });
  } finally { database.close(); }
}

/** @param {{ requestId?: string }} [input] */
export async function discardPendingHttpRequest({ requestId } = {}) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/u.test(requestId)) {
    throw new TypeError('De pending HTTP-requestidentiteit is ongeldig.');
  }
  const currentIdentity = await identity();
  const database = await openDatabase();
  try {
    return await pendingStoreTransaction(database, 'readwrite', ({ store,
      resolveOnComplete, fail }) => {
      const count = store.count();
      const stored = store.get(PENDING_KEY);
      let countResult;
      let storedResult;
      let reads = 0;
      const clear = () => {
        reads += 1;
        if (reads !== 2) return;
        if (countResult !== 1 || !exactPendingRecord(storedResult, currentIdentity.publicJwk)
          || storedResult.state !== 'request_pending'
          || storedResult.request.requestId !== requestId) {
          fail(new Error('Alleen exact deze nog onbeantwoorde HTTP-request kan worden verwijderd.'));
          return;
        }
        store.delete(PENDING_KEY);
        resolveOnComplete(true);
      };
      count.onsuccess = () => { countResult = count.result; clear(); };
      count.onerror = () => fail(count.error || new Error('Pending request tellen mislukte.'));
      stored.onsuccess = () => { storedResult = stored.result; clear(); };
      stored.onerror = () => fail(stored.error || new Error('Pending request lezen mislukte.'));
    });
  } finally { database.close(); }
}

/**
 * @param {{
 *   status?: number,
 *   contentType?: string,
 *   bodyBytes?: Uint8Array,
 *   expectedRequestId?: string,
 *   writeAttemptRef?: string,
 *   writeGeneration?: number
 * }} [input]
 */
export async function signHttpResponseAck({
  status, contentType, bodyBytes, expectedRequestId, writeAttemptRef, writeGeneration,
} = {}) {
  if (!Number.isSafeInteger(status) || status < 200 || status > 599
    || contentType !== CONTENT_TYPE || !(bodyBytes instanceof Uint8Array)
    || bodyBytes.length < 2 || bodyBytes.length > 24 * 1024
    || !WRITE_ATTEMPT_REF.test(writeAttemptRef)
    || !Number.isSafeInteger(writeGeneration)
    || writeGeneration < 1 || writeGeneration > 64) {
    throw new TypeError('De HTTP-responsewire is niet canoniek.');
  }
  const bytes = new Uint8Array(bodyBytes);
  let rawBody;
  let body;
  try {
    rawBody = decoder.decode(bytes);
    body = JSON.parse(rawBody);
  } catch { throw new TypeError('De HTTP-response bevat geen exacte UTF-8 JSON-wire.'); }
  if (!plainObject(body) || canonicalJson(body) !== rawBody
    || body.request_id !== expectedRequestId || status !== expectedStatus(body)
    || !exactDeliveryEnvelope(body._httpDelivery)) {
    throw new Error('De HTTP-response wijkt af van haar canonieke request of status.');
  }
  const currentIdentity = await identity();
  const envelope = body._httpDelivery;
  if (envelope.keyFingerprint !== currentIdentity.fingerprint) {
    throw new Error('De HTTP-response is niet aan deze duurzame browserkey gebonden.');
  }
  const bodySha256 = await sha256Hex(bytes);
  const binding = canonicalJson({
    contract: 'CanonicalHttpWireBinding/v3', status, contentType,
    bodySha256, bodyLength: bytes.length,
  });
  const prefix = encoder.encode(`${binding}\n`);
  const wire = new Uint8Array(prefix.length + bytes.length);
  wire.set(prefix, 0);
  wire.set(bytes, prefix.length);
  const ack = {
    contract: 'HttpClientAck/v3', requestRef: envelope.requestRef,
    responseRef: envelope.responseRef, responseSha256: envelope.responseSha256,
    deliveryRef: envelope.deliveryRef, challenge: envelope.challenge,
    wireSha256: await sha256Hex(wire), writeAttemptRef, writeGeneration,
  };
  const signature = new Uint8Array(await crypto.subtle.sign(
    { name: 'ECDSA', hash: 'SHA-256' }, currentIdentity.privateKey,
    encoder.encode(canonicalJson(ack)),
  ));
  if (signature.length !== 64) throw new Error('De browser gaf geen canonieke ES256-handtekening.');
  return Object.freeze({ ...ack, signature: bytesToBase64Url(signature) });
}
