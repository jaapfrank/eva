import assert from 'node:assert/strict';
import {
  createHash, createPublicKey, generateKeyPairSync, sign,
} from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-http-delivery-v3-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_HTTP_DELIVERY_LEASE_MS = '100';
process.env.EVA_HTTP_INGRESS_CLAIM_LEASE_MS = '100';

const { db } = await import('../src/db.js');
const { canonicalJson } = await import('../src/util.js');
const { seedAuthorizedTurnUnderstanding } = await import(
  './turn-understanding-authority-fixture.js'
);
const { exactSqliteIndexSignatures } = await import(
  '../src/web/sqliteindexattestation.js'
);
const ingress = await import('../src/web/httpingress.js');
const responseAuthority = await import('../src/web/httpresponseauthority.js');
const delivery = await import('../src/web/httpdelivery.js');
const {
  beginHttpIngress,
  issueVerifiedHttpResultAuthority,
  prepareHttpResponse,
  settleHttpIngress,
} = ingress;
const {
  httpResponseAuthorityCurrent,
  issueHttpResponseAuthority,
} = responseAuthority;
const {
  acknowledgeHttpDelivery,
  beginHttpDelivery,
  claimHttpDeliveryTransmission,
  canonicalHttpClientAckPreimage,
  claimHttpDeliveryWrite,
  finishHttpDeliveryWrite,
  finishHttpDeliveryTransmission,
  httpDeliveryStoreStatus,
  httpDeliveryWriteAuthorityCurrent,
  replayHttpDelivery,
} = delivery;

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const projectRoot = path.resolve(new URL('..', import.meta.url).pathname);
let sequence = 0;

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function keyPair() {
  const pair = generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
  const jwk = pair.publicKey.export({ format: 'jwk' });
  return {
    privateKey: pair.privateKey,
    publicKey: {
      contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256',
      kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y,
    },
  };
}

function response(label, overrides = {}) {
  sequence += 1;
  const keys = overrides.keys || keyPair();
  const turnId = overrides.turnId || `turn:http:v3:${label}:${sequence}`;
  const conversationId = `web:delivery:v3:${label}:${sequence}`;
  const requestId = `http-request:v3:${label}:${sequence}`;
  const intent = {
    contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Familie',
    rawText: `bericht ${label}`, conversationId, syntheticTest: false,
    clientAckPublicKey: keys.publicKey,
  };
  const begun = beginHttpIngress({ channel: 'web', conversationId, requestId, intent });
  assert.equal(begun.kind, 'started');
  const runtimeResult = {
    channel: 'web', conversationId, originMessageId: requestId, turnId,
    reply: overrides.body?.reply ?? `antwoord ${label}`, syntheticTest: false,
    ...(overrides.error ? { error: overrides.error } : {}),
    ...(overrides.superseded ? { superseded: true } : {}),
  };
  const metadata = overrides.superseded ? { superseded: true } : {};
  const preparedBody = {
    contract: 'PreparedUnderstandingTurn/v1', channel: 'web', conversationId,
    originMessageId: requestId, turnId, reply: runtimeResult.reply,
    replySha256: sha256(runtimeResult.reply), workItemId: null, goalEpisodeId: null,
    finalDeliveryBindingSha256: null, syntheticTest: false,
    error: runtimeResult.error || null, metadata,
  };
  const durableProof = {
    ...preparedBody, sha256: sha256(canonicalJson(preparedBody)),
  };
  seedAuthorizedTurnUnderstanding({
    database: db, channel: 'web', conversationId, messageId: requestId,
    requester: intent.sender, originalText: intent.rawText.trim(), output: durableProof,
  });
  const resultAuthority = issueVerifiedHttpResultAuthority(
    runtimeResult, begun.preparationAuthority,
  );
  const prepared = prepareHttpResponse(begun.preparationAuthority, resultAuthority);
  const settled = settleHttpIngress(prepared.settlementAuthority);
  assert.equal(settled.kind, 'settled');
  return { response: settled.response, responseAuthority: settled.responseAuthority, keys };
}

function prepare(label, overrides = {}) {
  const durable = response(label, overrides);
  const issued = issueHttpResponseAuthority(durable.responseAuthority);
  assert.equal(issued.kind, 'issued');
  assert.equal(httpResponseAuthorityCurrent(issued.authority), true);
  const begun = beginHttpDelivery(issued.authority);
  assert.equal(httpResponseAuthorityCurrent(issued.authority), false);
  assert.equal(begun.kind, 'prepared');
  return { ...durable, begun };
}

function ackBody(prepared, claim, overrides = {}) {
  const envelope = JSON.parse(claim.wire.bodyBytes.toString('utf8'))._httpDelivery;
  return {
    contract: 'HttpClientAck/v3', requestRef: prepared.begun.delivery.requestRef,
    responseRef: prepared.begun.delivery.responseRef,
    responseSha256: prepared.begun.delivery.responseSha256,
    deliveryRef: prepared.begun.delivery.deliveryRef,
    challenge: envelope.challenge,
    wireSha256: prepared.begun.delivery.wireSha256,
    writeAttemptRef: claim.writeAttemptRef || claim.attemptRef,
    writeGeneration: claim.writeGeneration || claim.generation,
    ...overrides,
  };
}

function signedAck(prepared, claim, privateKey = prepared.keys.privateKey, overrides = {}) {
  const body = ackBody(prepared, claim, overrides);
  const preimage = canonicalHttpClientAckPreimage(body);
  const signature = sign('sha256', Buffer.from(preimage, 'utf8'), {
    key: privateKey, dsaEncoding: 'ieee-p1363',
  }).toString('base64url');
  return { ...body, signature };
}

function runIsolated(source, extraEnv = {}) {
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: projectRoot, encoding: 'utf8', env: {
      ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp', ...extraEnv,
    },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return JSON.parse(result.stdout.trim().split('\n').filter(Boolean).at(-1));
}

function runConcurrent(source) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['--input-type=module', '-e', source], {
      cwd: projectRoot, env: { ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8').on('data', (chunk) => { stdout += chunk; });
    child.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code !== 0) reject(new Error(stderr || stdout));
      else resolve(JSON.parse(stdout.trim().split('\n').filter(Boolean).at(-1)));
    });
  });
}

function insertOrReplaceRow(table, row) {
  const columns = Object.keys(row);
  return db.prepare(`INSERT OR REPLACE INTO main.${table} (
    ${columns.join(',')}
  ) VALUES (${columns.map((column) => `@${column}`).join(',')})`).run(row);
}

function exactRows(table) {
  return db.prepare(`SELECT * FROM main.${table} ORDER BY id`).all().map((row) => (
    Object.fromEntries(Object.entries(row).map(([key, value]) => [
      key, Buffer.isBuffer(value) ? { blobBase64: value.toString('base64') } : value,
    ]))
  ));
}

function deliverySchemaObjects() {
  return db.prepare(`SELECT type,name,tbl_name,sql FROM main.sqlite_master
    WHERE name GLOB 'eva_http_delivery*v3*' OR name GLOB 'idx_http_delivery_v3*'
    ORDER BY type,name`).all();
}

function withoutTrigger(name, callback) {
  db.exec('BEGIN IMMEDIATE');
  try {
    db.exec(`DROP TRIGGER main.${name}`);
    callback();
  } finally {
    db.exec('ROLLBACK');
  }
}

function withNowOffset(offsetMs, callback) {
  const actualNow = Date.now;
  Date.now = () => actualNow() + offsetMs;
  try { return callback(); } finally { Date.now = actualNow; }
}

test('v3 store is exact, append-only, TEMP-safe, bounded, and has no ACK UDF', () => {
  assert.deepEqual(httpDeliveryStoreStatus(), {
    contract: 'HttpDeliveryStoreStatus/v3', available: true,
  });
  assert.deepEqual(db.prepare(`SELECT name FROM pragma_function_list
    WHERE name GLOB 'eva_http_delivery*' OR name GLOB 'eva_http_ack*'`).all(), []);
  for (const table of [
    'eva_http_delivery_items_v3', 'eva_http_delivery_attempt_events_v3',
    'eva_http_delivery_ack_receipts_v3', 'eva_http_delivery_transmission_events_v3',
  ]) {
    const indexes = db.prepare(`PRAGMA main.index_list('${table}')`).all();
    assert.equal(indexes.some((index) => index.origin === 'u'), true);
  }
  const attemptSql = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='table' AND name='eva_http_delivery_attempt_events_v3'`).get().sql;
  assert.match(attemptSql, /generation<=64/u);
  const transmissionSql = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='table' AND name='eva_http_delivery_transmission_events_v3'`).get().sql;
  assert.match(transmissionSql, /generation<=9007199254740991/u);
  const attemptTrigger = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name='eva_http_delivery_v3_attempts_valid_transition'`)
    .get().sql;
  assert.match(attemptTrigger, /item\.ack_challenge_sha256=NEW\.ack_challenge_sha256/u);
  assert.match(attemptTrigger, /current\.action='write_finished'/u);
  assert.match(attemptTrigger,
    /current\.action='issued' AND current\.lease_until_ts>NEW\.created_ts/u);
  assert.doesNotMatch(attemptTrigger,
    /current\.action IN \('issued','write_finished'\)/u);
  const ackTrigger = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name='eva_http_delivery_v3_acks_valid_reference'`)
    .get().sql;
  assert.match(ackTrigger, /item\.ack_challenge=NEW\.ack_challenge/u);
  assert.match(ackTrigger, /item\.client_key_fingerprint=NEW\.client_key_fingerprint/u);

  db.exec('CREATE TEMP TABLE eva_http_delivery_items_v3(value TEXT)');
  assert.equal(httpDeliveryStoreStatus().available, false);
  db.exec('DROP TABLE temp.eva_http_delivery_items_v3');
  db.exec(`CREATE TEMP TRIGGER arbitrary_temp_delivery_blocker
    BEFORE INSERT ON main.eva_http_delivery_items_v3 BEGIN SELECT RAISE(IGNORE); END`);
  assert.equal(httpDeliveryStoreStatus().available, false);
  db.exec('DROP TRIGGER temp.arbitrary_temp_delivery_blocker');
  db.exec('CREATE INDEX unexpected_http_delivery_v3 ON eva_http_delivery_items_v3(status)');
  assert.equal(httpDeliveryStoreStatus().available, false);
  db.exec('DROP INDEX unexpected_http_delivery_v3');
  assert.equal(httpDeliveryStoreStatus().available, true);
});

test('indexattestatie detecteert collation/DESC-tamper en extra indexes semantisch', () => {
  db.exec(`CREATE TABLE main.http_index_attestation_probe (
    id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE
  );
  CREATE UNIQUE INDEX idx_http_index_attestation_probe
    ON http_index_attestation_probe(token)`);
  const expected = [
    { name: 'idx_http_index_attestation_probe', origin: 'c', columns: ['token'] },
    { origin: 'u', columns: ['token'] },
  ];
  assert.equal(exactSqliteIndexSignatures(
    db, 'http_index_attestation_probe', ['id', 'token'], expected,
  ), true);
  db.exec(`DROP INDEX idx_http_index_attestation_probe;
  CREATE UNIQUE INDEX idx_http_index_attestation_probe
    ON http_index_attestation_probe(token COLLATE NOCASE DESC)`);
  assert.equal(exactSqliteIndexSignatures(
    db, 'http_index_attestation_probe', ['id', 'token'], expected,
  ), false);
  db.exec(`DROP INDEX idx_http_index_attestation_probe;
  CREATE UNIQUE INDEX idx_http_index_attestation_probe
    ON http_index_attestation_probe(token);
  CREATE INDEX extra_http_index_attestation_probe
    ON http_index_attestation_probe(id)`);
  assert.equal(exactSqliteIndexSignatures(
    db, 'http_index_attestation_probe', ['id', 'token'], expected,
  ), false);
  db.exec('DROP TABLE main.http_index_attestation_probe');
});

test('alle writer-zichtbare waarden zonder privésleutel kunnen geen terminale ACK maken', () => {
  const prepared = prepare('writer-cannot-mint');
  const claim = claimHttpDeliveryWrite(prepared.begun.delivery);
  const body = JSON.parse(claim.wire.bodyBytes.toString('utf8'));
  assert.equal(body._httpDelivery.contract, 'HttpClientAckChallenge/v3');
  assert.equal(Object.hasOwn(claim, 'ackAuthority'), false);
  assert.equal(Object.hasOwn(claim, 'privateKey'), false);
  assert.equal(JSON.stringify(claim).includes('"d"'), false);
  const publicKeyObject = createPublicKey({
    key: {
      kty: 'EC', crv: 'P-256', x: prepared.keys.publicKey.x,
      y: prepared.keys.publicKey.y,
    },
    format: 'jwk',
  });
  assert.throws(() => sign('sha256', Buffer.from('server-visible'), publicKeyObject));
  const finished = finishHttpDeliveryWrite(claim.writeAuthority);
  assert.equal(finished.delivered, false);
  const forged = {
    ...ackBody(prepared, claim), signature: Buffer.alloc(64).toString('base64url'),
  };
  assert.throws(() => acknowledgeHttpDelivery(forged), /signature/u);
  assert.equal(replayHttpDelivery(prepared.begun.delivery).delivered, false);
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_http_delivery_ack_receipts_v3')
    .get().count, 0);

  const valid = acknowledgeHttpDelivery(signedAck(prepared, claim));
  assert.equal(valid.delivered, true);
  assert.equal(valid.ack.contract, 'HttpClientAckReceipt/v3');
});

test('verkeerde sleutel, handtekening en cross-request/ref-swaps slaan geen receipt', () => {
  const left = prepare('swap-left');
  const right = prepare('swap-right');
  const leftClaim = claimHttpDeliveryWrite(left.begun.delivery);
  const rightClaim = claimHttpDeliveryWrite(right.begun.delivery);
  finishHttpDeliveryWrite(leftClaim.writeAuthority);
  finishHttpDeliveryWrite(rightClaim.writeAuthority);
  const before = db.prepare('SELECT count(*) AS count FROM eva_http_delivery_ack_receipts_v3')
    .get().count;

  assert.throws(() => acknowledgeHttpDelivery(signedAck(
    left, leftClaim, right.keys.privateKey,
  )), /signature/u);
  for (const changed of [
    { requestRef: right.begun.delivery.requestRef },
    { responseRef: right.begun.delivery.responseRef,
      responseSha256: right.begun.delivery.responseSha256 },
    { deliveryRef: right.begun.delivery.deliveryRef },
    { challenge: JSON.parse(rightClaim.wire.bodyBytes.toString('utf8'))._httpDelivery.challenge },
    { wireSha256: right.begun.delivery.wireSha256 },
  ]) {
    const mixed = signedAck(left, leftClaim, left.keys.privateKey, changed);
    assert.throws(() => acknowledgeHttpDelivery(mixed), /signature|match|durable/u);
  }
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_http_delivery_ack_receipts_v3')
    .get().count, before);
  assert.equal(acknowledgeHttpDelivery(signedAck(left, leftClaim)).delivered, true);
  assert.equal(acknowledgeHttpDelivery(signedAck(right, rightClaim)).delivered, true);
});

test('exacte receiptvelden zijn allemaal gebonden; caller-mutaties falen gesloten', () => {
  const prepared = prepare('receipt-exact');
  for (const mutation of [
    { status: 201 }, { wireLength: prepared.begun.delivery.wireLength + 1 },
    { turnId: 'turn:other' }, { clientKeyFingerprint: `p256-spki-sha256:${'0'.repeat(64)}` },
    { provenance: { ...prepared.begun.delivery.provenance, ref: 'turn:other' } },
    { extra: true },
  ]) assert.throws(() => claimHttpDeliveryWrite({
    ...prepared.begun.delivery, ...mutation,
  }), /exact|conflicts|invalid/u);
  assert.equal(claimHttpDeliveryWrite(prepared.begun.delivery).kind, 'write');
});

test('crash vóór finish is bij verschillende proces-env exact duurzaam gefenced', () => {
  const live = prepare('crash-before-finish-live');
  const liveClaim = claimHttpDeliveryWrite(live.begun.delivery);
  assert.ok(Date.parse(liveClaim.leaseUntil) - Date.now() >= 29_000);
  const blocked = runIsolated(`
    const m=await import('./src/web/httpdelivery.js');
    console.log(JSON.stringify(m.claimHttpDeliveryWrite(${JSON.stringify(
      live.begun.delivery,
    )})));
  `, { EVA_HTTP_DELIVERY_LEASE_MS: '120000' });
  assert.equal(blocked.kind, 'in_progress');

  const prepared = prepare('crash-before-finish-recovery');
  const first = withNowOffset(-31_000,
    () => claimHttpDeliveryWrite(prepared.begun.delivery));
  const recovered = runIsolated(`
    const m=await import('./src/web/httpdelivery.js');
    const claim=m.claimHttpDeliveryWrite(${JSON.stringify(prepared.begun.delivery)});
    const done=m.finishHttpDeliveryWrite(claim.writeAuthority);
    console.log(JSON.stringify({kind:claim.kind,generation:claim.generation,
      wireSha256:claim.wire.wireSha256,wire:claim.wire.bodyBytes,done:done.kind,
      writeAttemptRef:claim.writeAttemptRef,writeGeneration:claim.writeGeneration,
      leaseMs:Date.parse(claim.leaseUntil)-Date.now()}));
  `, { EVA_HTTP_DELIVERY_LEASE_MS: '120000' });
  assert.equal(recovered.kind, 'write');
  assert.equal(recovered.generation, first.generation + 1);
  assert.equal(recovered.wireSha256, first.wire.wireSha256);
  assert.deepEqual(Buffer.from(recovered.wire.data), first.wire.bodyBytes);
  assert.equal(recovered.done, 'write_finished');
  assert.ok(recovered.leaseMs >= 29_000 && recovered.leaseMs <= 30_000);
  assert.throws(() => finishHttpDeliveryWrite(first.writeAuthority), /stale/u);
  assert.equal(httpDeliveryWriteAuthorityCurrent(first.writeAuthority), false);
  assert.throws(() => acknowledgeHttpDelivery(signedAck(prepared, first)), /current|finished/u);
  assert.equal(acknowledgeHttpDelivery(signedAck(prepared, {
    ...first, writeAttemptRef: recovered.writeAttemptRef,
    writeGeneration: recovered.writeGeneration,
  })).delivered, true);
});

test('finish-store failure laat geen ACK of wire toe en herneemt veilig als primary n+1', () => {
  const prepared = prepare('finish-store-failure');
  const primary = claimHttpDeliveryWrite(prepared.begun.delivery);
  const item = db.prepare(`SELECT * FROM main.eva_http_delivery_items_v3
    WHERE delivery_ref=?`).get(prepared.begun.delivery.deliveryRef);
  const issued = db.prepare(`SELECT * FROM main.eva_http_delivery_attempt_events_v3
    WHERE delivery_row_id=? AND generation=? AND sequence=0`).get(item.id, primary.generation);
  const finishBody = {
    contract: 'HttpDeliveryWriteAttemptEvent/v3', deliveryRef: item.delivery_ref,
    generation: primary.generation, sequence: 1, action: 'write_finished',
    tokenSha256: issued.token_sha256,
    ackChallengeSha256: item.ack_challenge_sha256,
    wireSha256: item.wire_sha256, wireLength: Number(item.wire_length),
    leaseUntil: issued.lease_until_ts,
  };
  const blockedFinishSha256 = sha256(canonicalJson(finishBody));
  const blocker = prepare('finish-store-failure-blocker');
  const blockerItem = db.prepare(`SELECT * FROM main.eva_http_delivery_items_v3
    WHERE delivery_ref=?`).get(blocker.begun.delivery.deliveryRef);
  const transitionName = 'eva_http_delivery_v3_attempts_valid_transition';
  const transitionSql = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(transitionName).sql;
  db.transaction(() => {
    db.exec(`DROP TRIGGER main.${transitionName}`);
    const blockerRefHash = sha256(`finish-store-blocker:${blockedFinishSha256}`);
    db.prepare(`INSERT INTO main.eva_http_delivery_attempt_events_v3 (
      contract,attempt_event_ref,attempt_event_sha256,delivery_row_id,delivery_ref,
      generation,sequence,action,token_sha256,ack_challenge_sha256,wire_sha256,
      wire_length,lease_until_ts,event_json,created_ts
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      'HttpDeliveryWriteAttemptEvent/v3', `http-write-attempt-v3:${blockerRefHash}`,
      blockedFinishSha256, blockerItem.id, blockerItem.delivery_ref, 1, 0, 'issued',
      sha256('finish-store-blocker-token'), blockerItem.ack_challenge_sha256,
      blockerItem.wire_sha256, blockerItem.wire_length,
      new Date(Date.now() + 30_000).toISOString(), '{}', new Date().toISOString(),
    );
    db.exec(transitionSql);
  }).immediate();

  assert.throws(() => finishHttpDeliveryWrite(primary.writeAuthority),
    /identity collision|UNIQUE constraint/iu);
  assert.equal(httpDeliveryWriteAuthorityCurrent(primary.writeAuthority), false,
    'een mislukte finish geeft de opaque capability niet opnieuw uit');
  assert.equal(db.prepare(`SELECT action FROM main.eva_http_delivery_attempt_events_v3
    WHERE delivery_row_id=? ORDER BY generation DESC,sequence DESC LIMIT 1`).get(item.id).action,
  'issued');
  assert.throws(() => acknowledgeHttpDelivery(signedAck(prepared, primary)),
    /current|finished/u);

  const noDeleteName = 'eva_http_delivery_v3_attempts_no_delete';
  const noDeleteSql = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(noDeleteName).sql;
  db.transaction(() => {
    db.exec(`DROP TRIGGER main.${noDeleteName}`);
    db.prepare(`DELETE FROM main.eva_http_delivery_attempt_events_v3
      WHERE delivery_row_id=? AND attempt_event_sha256=?`).run(
      blockerItem.id, blockedFinishSha256,
    );
    db.exec(noDeleteSql);
  }).immediate();

  let recovered;
  withNowOffset(31_000, () => {
    recovered = claimHttpDeliveryWrite(prepared.begun.delivery);
    assert.equal(recovered.kind, 'write');
    assert.equal(recovered.generation, primary.generation + 1);
    assert.deepEqual(recovered.wire.bodyBytes, primary.wire.bodyBytes);
    finishHttpDeliveryWrite(recovered.writeAuthority);
  });
  assert.throws(() => acknowledgeHttpDelivery(signedAck(prepared, primary)),
    /current|finished/u);
  assert.equal(acknowledgeHttpDelivery(signedAck(prepared, recovered)).delivered, true);
});

test('write_finished blijft ook na lease-expiry permanent op exact dezelfde client-ACK wachten', () => {
  const live = prepare('crash-after-finish-live');
  const liveClaim = claimHttpDeliveryWrite(live.begun.delivery);
  finishHttpDeliveryWrite(liveClaim.writeAuthority);
  const waiting = runIsolated(`
    const m=await import('./src/web/httpdelivery.js');
    console.log(JSON.stringify(m.claimHttpDeliveryWrite(${JSON.stringify(
      live.begun.delivery,
    )})));
  `);
  assert.equal(waiting.kind, 'awaiting_client_ack');
  assert.equal(Object.hasOwn(waiting, 'wire'), false);

  const prepared = prepare('crash-after-finish-recovery');
  let first;
  withNowOffset(-31_000, () => {
    first = claimHttpDeliveryWrite(prepared.begun.delivery);
    finishHttpDeliveryWrite(first.writeAuthority);
  });
  const expired = runIsolated(`
    const m=await import('./src/web/httpdelivery.js');
    console.log(JSON.stringify(m.claimHttpDeliveryWrite(${JSON.stringify(
    prepared.begun.delivery,
  )})));
  `);
  assert.equal(expired.kind, 'awaiting_client_ack');
  assert.equal(expired.state, 'write_finished');
  assert.equal(Object.hasOwn(expired, 'wire'), false);
  assert.equal(acknowledgeHttpDelivery(signedAck(prepared, first)).delivered, true);
});

test('iedere heruitgifte traceert apart en bindt ACK aan de permanente finished primary write', () => {
  const prepared = prepare('traced-retrieval');
  const primary = claimHttpDeliveryWrite(prepared.begun.delivery);
  finishHttpDeliveryWrite(primary.writeAuthority);
  const stillFenced = claimHttpDeliveryWrite(prepared.begun.delivery);
  assert.equal(stillFenced.kind, 'awaiting_client_ack');
  assert.equal(Object.hasOwn(stillFenced, 'wire'), false);

  const retrieval = claimHttpDeliveryTransmission(prepared.begun.delivery);
  assert.equal(retrieval.kind, 'write');
  assert.equal(retrieval.mode, 'pre_ack_retrieval');
  assert.equal(retrieval.writeAttemptRef, primary.writeAttemptRef);
  assert.equal(retrieval.writeGeneration, primary.writeGeneration);
  assert.deepEqual(retrieval.wire.bodyBytes, primary.wire.bodyBytes);
  const overlap = claimHttpDeliveryTransmission(prepared.begun.delivery);
  assert.equal(overlap.kind, 'in_progress');
  assert.equal(Object.hasOwn(overlap, 'wire'), false);
  finishHttpDeliveryTransmission(retrieval.transmissionAuthority);

  const acknowledged = acknowledgeHttpDelivery(signedAck(prepared, primary));
  assert.equal(acknowledged.delivered, true);
  assert.equal(acknowledged.ack.writeAttemptRef, primary.writeAttemptRef);
  assert.equal(acknowledged.ack.writeGeneration, primary.writeGeneration);
  const acknowledgedRetrieval = claimHttpDeliveryTransmission(prepared.begun.delivery);
  assert.equal(acknowledgedRetrieval.kind, 'write');
  assert.equal(acknowledgedRetrieval.mode, 'acknowledged_retrieval');
  assert.equal(acknowledgedRetrieval.writeAttemptRef, primary.writeAttemptRef);
  finishHttpDeliveryTransmission(acknowledgedRetrieval.transmissionAuthority);
});

test('twee multiprocess geldige ACKs zijn idempotent en maken exact één receipt', async () => {
  const prepared = prepare('concurrent-ack');
  const claim = claimHttpDeliveryWrite(prepared.begun.delivery);
  finishHttpDeliveryWrite(claim.writeAuthority);
  const firstAck = signedAck(prepared, claim);
  const secondAck = signedAck(prepared, claim);
  assert.notEqual(firstAck.signature, secondAck.signature);
  const source = (ack) => `
    const m=await import('./src/web/httpdelivery.js');
    const out=m.acknowledgeHttpDelivery(${JSON.stringify(ack)});
    console.log(JSON.stringify({delivered:out.delivered,ref:out.ack.ackReceiptRef}));
  `;
  const [first, second] = await Promise.all([
    runConcurrent(source(firstAck)), runConcurrent(source(secondAck)),
  ]);
  assert.equal(first.delivered, true);
  assert.equal(second.delivered, true);
  assert.equal(first.ref, second.ref);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM eva_http_delivery_ack_receipts_v3
    WHERE delivery_ref=?`).get(prepared.begun.delivery.deliveryRef).count, 1);
});

test('acknowledged restart-replay geeft exact de oorspronkelijke bytes, ook bij supersession', () => {
  const prepared = prepare('null-turn', { superseded: true });
  assert.equal(prepared.begun.delivery.turnId, null);
  assert.equal(prepared.begun.delivery.provenance.kind, 'request_supersession');
  const claim = claimHttpDeliveryWrite(prepared.begun.delivery);
  finishHttpDeliveryWrite(claim.writeAuthority);
  const ack = signedAck(prepared, claim);
  const client = runIsolated(`
    const m=await import('./src/web/httpdelivery.js');
    const out=m.acknowledgeHttpDelivery(${JSON.stringify(ack)});
    console.log(JSON.stringify({delivered:out.delivered,ref:out.ack.ackReceiptRef}));
  `);
  assert.equal(client.delivered, true);
  const replay = runIsolated(`
    const m=await import('./src/web/httpdelivery.js');
    const out=m.replayHttpDelivery(${JSON.stringify(prepared.begun.delivery)});
    console.log(JSON.stringify({delivered:out.delivered,turnId:out.delivery.turnId,
      wireSha256:out.wire.wireSha256,wire:out.wire.bodyBytes,ref:out.ack.ackReceiptRef}));
  `);
  assert.equal(replay.delivered, true);
  assert.equal(replay.turnId, null);
  assert.equal(replay.wireSha256, claim.wire.wireSha256);
  assert.deepEqual(Buffer.from(replay.wire.data), claim.wire.bodyBytes);
  assert.equal(replay.ref, client.ref);
});

test('items, attempts en ACKs zijn OR REPLACE-safe en delivery sluit geen episode', () => {
  const prepared = prepare('append-only');
  const claim = claimHttpDeliveryWrite(prepared.begun.delivery);
  finishHttpDeliveryWrite(claim.writeAuthority);
  acknowledgeHttpDelivery(signedAck(prepared, claim));
  const transmission = claimHttpDeliveryTransmission(prepared.begun.delivery);
  finishHttpDeliveryTransmission(transmission.transmissionAuthority);
  for (const table of [
    'eva_http_delivery_items_v3', 'eva_http_delivery_attempt_events_v3',
    'eva_http_delivery_ack_receipts_v3', 'eva_http_delivery_transmission_events_v3',
  ]) {
    const row = db.prepare(`SELECT * FROM main.${table} ORDER BY id DESC LIMIT 1`).get();
    assert.throws(() => db.prepare(`INSERT OR REPLACE INTO main.${table}
      SELECT * FROM main.${table} WHERE id=?`).run(row.id),
    /collision|append-only|acknowledged|completion is invalid/u);
    assert.throws(() => db.prepare(`UPDATE main.${table} SET id=id WHERE id=?`).run(row.id),
      /append-only/u);
    assert.throws(() => db.prepare(`DELETE FROM main.${table} WHERE id=?`).run(row.id),
      /append-only/u);
  }
  const owned = db.prepare(`SELECT name FROM main.sqlite_master
    WHERE name GLOB 'eva_http_delivery*v3*'`).all().map((row) => row.name);
  assert.equal(owned.some((name) => /episode|work_completion/u.test(name)), false);
});

test('challenge-UNIQUE OR REPLACE bewaart delivery-item en ACK byte-exact', () => {
  const prepared = prepare('challenge-unique-guards');
  const claim = claimHttpDeliveryWrite(prepared.begun.delivery);
  finishHttpDeliveryWrite(claim.writeAuthority);
  acknowledgeHttpDelivery(signedAck(prepared, claim));
  const itemTable = 'eva_http_delivery_items_v3';
  const ackTable = 'eva_http_delivery_ack_receipts_v3';
  const item = db.prepare(`SELECT * FROM main.${itemTable} WHERE delivery_ref=?`)
    .get(prepared.begun.delivery.deliveryRef);
  const ack = db.prepare(`SELECT * FROM main.${ackTable} WHERE delivery_row_id=?`)
    .get(item.id);
  for (const [trigger, fields] of [
    ['eva_http_delivery_v3_items_no_replace', ['ack_challenge', 'ack_challenge_sha256']],
    ['eva_http_delivery_v3_acks_no_replace', ['ack_challenge', 'ack_challenge_sha256']],
  ]) {
    const sql = db.prepare(`SELECT sql FROM main.sqlite_master
      WHERE type='trigger' AND name=?`).get(trigger).sql;
    for (const field of fields) assert.match(sql, new RegExp(`${field}=NEW\\.${field}`, 'u'));
  }

  const changed = sha256('changed-delivery-unique-identities');
  const itemBase = {
    ...item, id: Number(item.id) + 1_000_000,
    delivery_ref: `http-delivery-v3:${changed}`, delivery_sha256: changed,
    authority_ref: `http-response-authority-v3:${changed}`,
    authority_sha256: changed, request_ref: `http-ingress:${changed}`,
    response_ref: `http-response:${changed}`, response_sha256: changed,
    wire_sha256: changed, item_json: '{"changed":true}',
  };
  withoutTrigger('eva_http_delivery_v3_items_valid_authority', () => {
    for (const candidate of [
      { ...itemBase, ack_challenge: item.ack_challenge,
        ack_challenge_sha256: changed },
      { ...itemBase, ack_challenge: changed,
        ack_challenge_sha256: item.ack_challenge_sha256 },
    ]) {
      assert.throws(() => insertOrReplaceRow(itemTable, candidate),
        /HTTP delivery v3 item identity collision/u);
      assert.deepEqual(db.prepare(`SELECT * FROM main.${itemTable} WHERE id=?`)
        .get(item.id), item);
    }
  });

  const ackBase = {
    ...ack, id: Number(ack.id) + 1_000_000,
    ack_receipt_ref: `http-delivery-ack-v3:${changed}`,
    ack_receipt_sha256: changed, delivery_row_id: Number(item.id) + 1_000_000,
    delivery_ref: `http-delivery-v3:${changed}`,
    request_ref: `http-ingress:${changed}`, response_ref: `http-response:${changed}`,
    response_sha256: changed, receipt_json: '{"changed":true}',
  };
  withoutTrigger('eva_http_delivery_v3_acks_valid_reference', () => {
    for (const candidate of [
      { ...ackBase, ack_challenge: ack.ack_challenge,
        ack_challenge_sha256: changed },
      { ...ackBase, ack_challenge: changed,
        ack_challenge_sha256: ack.ack_challenge_sha256 },
    ]) {
      assert.throws(() => insertOrReplaceRow(ackTable, candidate),
        /HTTP delivery v3 ACK identity collision/u);
      assert.deepEqual(db.prepare(`SELECT * FROM main.${ackTable} WHERE id=?`)
        .get(ack.id), ack);
    }
  });
  assert.deepEqual(db.prepare(`SELECT * FROM main.${itemTable} WHERE id=?`).get(item.id), item);
  assert.deepEqual(db.prepare(`SELECT * FROM main.${ackTable} WHERE id=?`).get(ack.id), ack);
});

test('legacy delivery-DDL migreert IMMEDIATE of rolt bij forced CREATE byte-exact terug', async () => {
  const legacyPrepared = prepare('legacy-migration-row');
  const legacyClaim = claimHttpDeliveryWrite(legacyPrepared.begun.delivery);
  finishHttpDeliveryWrite(legacyClaim.writeAuthority);
  acknowledgeHttpDelivery(signedAck(legacyPrepared, legacyClaim));
  const legacyItem = db.prepare(`SELECT * FROM main.eva_http_delivery_items_v3
    WHERE delivery_ref=?`).get(legacyPrepared.begun.delivery.deliveryRef);
  const legacyAck = db.prepare(`SELECT * FROM main.eva_http_delivery_ack_receipts_v3
    WHERE delivery_row_id=?`).get(legacyItem.id);
  const legacyPreimageBody = ackBody(legacyPrepared, legacyClaim);
  delete legacyPreimageBody.writeAttemptRef;
  delete legacyPreimageBody.writeGeneration;
  const legacyPreimageJson = canonicalJson(legacyPreimageBody);
  const legacySignature = sign('sha256', Buffer.from(legacyPreimageJson), {
    key: legacyPrepared.keys.privateKey, dsaEncoding: 'ieee-p1363',
  }).toString('base64url');
  const legacySignatureSha256 = sha256(Buffer.from(legacySignature, 'base64url'));
  const legacyReceiptBody = {
    contract: 'HttpClientAckReceipt/v3', deliveryRef: legacyAck.delivery_ref,
    requestRef: legacyAck.request_ref, responseRef: legacyAck.response_ref,
    responseSha256: legacyAck.response_sha256,
    ackChallengeSha256: legacyAck.ack_challenge_sha256,
    wireSha256: legacyAck.wire_sha256, wireLength: Number(legacyAck.wire_length),
    clientKeyFingerprint: legacyAck.client_key_fingerprint,
    ackPreimageSha256: sha256(legacyPreimageJson),
    signatureSha256: legacySignatureSha256, turnId: legacyAck.turn_id,
    provenance: { ...legacyPrepared.begun.delivery.provenance },
  };
  const legacyReceiptJson = canonicalJson(legacyReceiptBody);
  const legacyReceiptSha256 = sha256(legacyReceiptJson);
  const ackNoUpdateName = 'eva_http_delivery_v3_acks_no_update';
  const ackNoUpdateSql = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(ackNoUpdateName).sql;
  db.transaction(() => {
    db.exec(`DROP TRIGGER main.${ackNoUpdateName}`);
    db.prepare(`UPDATE main.eva_http_delivery_ack_receipts_v3 SET
      ack_receipt_ref=?,ack_receipt_sha256=?,ack_preimage_json=?,
      ack_preimage_sha256=?,signature=?,signature_sha256=?,receipt_json=?
      WHERE id=?`).run(
      `http-delivery-ack-v3:${legacyReceiptSha256}`, legacyReceiptSha256,
      legacyPreimageJson, sha256(legacyPreimageJson), legacySignature,
      legacySignatureSha256, legacyReceiptJson, legacyAck.id,
    );
    db.exec(ackNoUpdateSql);
  }).immediate();
  assert.equal(replayHttpDelivery(legacyPrepared.begun.delivery).ack.ackReceiptRef,
    `http-delivery-ack-v3:${legacyReceiptSha256}`);

  const attemptName = 'eva_http_delivery_v3_attempts_valid_transition';
  const ackName = 'eva_http_delivery_v3_acks_valid_reference';
  const currentAttemptDdl = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(attemptName).sql;
  const currentAckDdl = db.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(ackName).sql;
  const source = fs.readFileSync(new URL('../src/web/httpdelivery.js', import.meta.url), 'utf8');
  const currentGuard = source.match(
    /const CURRENT_FINISHED_WRITE_GUARD = `([\s\S]*?)`;/u,
  )?.[1];
  const legacyGuard = source.match(
    /const LEGACY_FINISHED_WRITE_GUARD = `([\s\S]*?)`;/u,
  )?.[1];
  const currentAckRule = source.match(
    /const CURRENT_ACK_PREIMAGE_RULE = `([\s\S]*?)`;/u,
  )?.[1];
  const legacyAckRule = source.match(
    /const LEGACY_ACK_PREIMAGE_RULE = `([\s\S]*?)`;/u,
  )?.[1];
  assert.ok(currentGuard && legacyGuard && currentAckRule && legacyAckRule);
  const previousAttemptDdl = currentAttemptDdl.replace(currentGuard, legacyGuard);
  const previousAckDdl = currentAckDdl.replace(currentAckRule, legacyAckRule);
  assert.notEqual(previousAttemptDdl, currentAttemptDdl);
  assert.notEqual(previousAckDdl, currentAckDdl);
  const currentSchema = deliverySchemaObjects();
  const transmissionTriggers = db.prepare(`SELECT name FROM main.sqlite_master
    WHERE type='trigger' AND tbl_name='eva_http_delivery_transmission_events_v3'
    ORDER BY name`).all().map(({ name }) => name);
  assert.equal(transmissionTriggers.length, 4);
  db.transaction(() => {
    for (const name of transmissionTriggers) db.exec(`DROP TRIGGER main.${name}`);
    db.exec(`DROP INDEX main.idx_http_delivery_v3_transmission_history;
      DROP TABLE main.eva_http_delivery_transmission_events_v3;
      DROP TRIGGER main.${attemptName};
      ${previousAttemptDdl};
      DROP TRIGGER main.${ackName};
      ${previousAckDdl};`);
  }).immediate();

  const legacyTables = [
    'eva_http_delivery_items_v3', 'eva_http_delivery_attempt_events_v3',
    'eva_http_delivery_ack_receipts_v3',
  ];
  const rowsBefore = Object.fromEntries(legacyTables.map((table) => [table, exactRows(table)]));
  db.exec('VACUUM');
  const legacySchema = deliverySchemaObjects();
  const originalMaxPages = db.pragma('max_page_count', { simple: true });
  const pageCount = db.pragma('page_count', { simple: true });
  let failed;
  try {
    assert.equal(db.pragma(`max_page_count = ${pageCount}`, { simple: true }), pageCount);
    const failedModule = await import('../src/web/httpdelivery.js?migration=forced-create');
    failed = failedModule.httpDeliveryStoreStatus();
  } finally {
    db.pragma(`max_page_count = ${originalMaxPages}`);
  }
  assert.equal(failed.available, false, JSON.stringify(failed));
  assert.match(failed.reason, /initialization_failed:database or disk is full/u);
  assert.deepEqual(deliverySchemaObjects(), legacySchema,
    'DROP/CREATE-migratie rolt haar volledige schemawijziging terug');
  for (const table of legacyTables) {
    assert.deepEqual(exactRows(table), rowsBefore[table], `${table} blijft byte-exact`);
  }

  const restarted = await import('../src/web/httpdelivery.js?migration=successful-restart');
  assert.equal(restarted.httpDeliveryStoreStatus().available, true);
  assert.equal(restarted.replayHttpDelivery(legacyPrepared.begun.delivery).ack.ackReceiptRef,
    `http-delivery-ack-v3:${legacyReceiptSha256}`);
  assert.deepEqual(deliverySchemaObjects(), currentSchema);
  for (const table of legacyTables) {
    assert.deepEqual(exactRows(table), rowsBefore[table], `${table} migreert byte-exact`);
  }
  assert.equal(db.prepare(`SELECT count(*) AS count
    FROM main.eva_http_delivery_transmission_events_v3`).get().count, 0);
});
