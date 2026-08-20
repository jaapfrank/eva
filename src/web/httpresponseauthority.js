import { createHash, randomBytes } from 'node:crypto';

import { db } from '../db.js';
import { canonicalJson } from '../util.js';
import {
  canonicalHttpWireSha256,
  normalizeHttpClientAckPublicKey,
} from './httpack.js';
import {
  consumeDurableHttpIngressResponseAuthority,
  durableHttpIngressResponseBoundaryStatus,
} from './httpingress.js';
import { exactSqliteIndexSignatures } from './sqliteindexattestation.js';

const authorityDb = /** @type {any} */ (db);
const TABLE = 'eva_http_response_authorities_v3';
const INDEXES = Object.freeze(['idx_http_response_authority_request_v3']);
const TRIGGERS = Object.freeze([
  'eva_http_response_authorities_v3_no_update',
  'eva_http_response_authorities_v3_no_delete',
  'eva_http_response_authorities_v3_no_replace',
  'eva_http_response_authorities_v3_valid_ingress_response',
]);
const DEPENDENCIES = Object.freeze([
  'eva_http_ingress_requests', 'eva_http_prepared_responses',
  'eva_http_ingress_state_events', 'eva_http_client_ack_bindings_v3',
]);
const PROTECTED = Object.freeze([TABLE, ...INDEXES, ...TRIGGERS, ...DEPENDENCIES]);
const SHA256 = /^[a-f0-9]{64}$/u;
const REQUEST_REF = /^http-ingress:[a-f0-9]{64}$/u;
const RESPONSE_REF = /^http-response:[a-f0-9]{64}$/u;
const AUTHORITY_REF = /^http-response-authority-v3:[a-f0-9]{64}$/u;
const DELIVERY_REF = /^http-delivery-v3:[a-f0-9]{64}$/u;
const KEY_FINGERPRINT = /^p256-spki-sha256:[a-f0-9]{64}$/u;
const CONTENT_TYPE = 'application/json; charset=utf-8';
const MAX_BODY_BYTES = 24 * 1024;
const MAX_TURN_LENGTH = 180;
const ISSUED = new WeakMap();
let initializationFailure = '';

const hash = (value) => createHash('sha256').update(value).digest('hex');
const nowIso = () => new Date().toISOString();
const mainTable = (name) => `main.${name}`;

const SCHEMA = `
CREATE TABLE IF NOT EXISTS eva_http_response_authorities_v3 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  authority_ref TEXT NOT NULL UNIQUE,
  authority_sha256 TEXT NOT NULL UNIQUE,
  request_ref TEXT NOT NULL UNIQUE,
  response_ref TEXT NOT NULL UNIQUE,
  response_sha256 TEXT NOT NULL UNIQUE,
  delivery_ref TEXT NOT NULL UNIQUE,
  status INTEGER NOT NULL,
  content_type TEXT NOT NULL,
  public_body_json TEXT NOT NULL,
  public_body_sha256 TEXT NOT NULL,
  wire_bytes BLOB NOT NULL,
  wire_sha256 TEXT NOT NULL UNIQUE,
  wire_length INTEGER NOT NULL,
  ack_challenge TEXT NOT NULL UNIQUE,
  ack_challenge_sha256 TEXT NOT NULL UNIQUE,
  client_public_key_json TEXT NOT NULL,
  client_key_fingerprint TEXT NOT NULL,
  turn_id TEXT,
  provenance_kind TEXT NOT NULL,
  provenance_ref TEXT NOT NULL,
  provenance_sha256 TEXT NOT NULL,
  response_json TEXT NOT NULL,
  issued_ts TEXT NOT NULL,
  CHECK(contract='HttpIngressResponseAuthority/v3'),
  CHECK(authority_ref GLOB 'http-response-authority-v3:*' AND length(authority_ref)=91),
  CHECK(length(authority_sha256)=64 AND lower(authority_sha256)=authority_sha256
    AND authority_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(request_ref GLOB 'http-ingress:*' AND length(request_ref)=77),
  CHECK(response_ref GLOB 'http-response:*' AND length(response_ref)=78),
  CHECK(length(response_sha256)=64 AND lower(response_sha256)=response_sha256
    AND response_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(delivery_ref GLOB 'http-delivery-v3:*' AND length(delivery_ref)=81),
  CHECK(typeof(status)='integer' AND status>=200 AND status<=599),
  CHECK(content_type='${CONTENT_TYPE}'),
  CHECK(json_valid(public_body_json) AND json_type(public_body_json,'$')='object'
    AND length(CAST(public_body_json AS BLOB))<=${MAX_BODY_BYTES}),
  CHECK(length(public_body_sha256)=64 AND lower(public_body_sha256)=public_body_sha256
    AND public_body_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(wire_bytes)='blob'),
  CHECK(length(wire_sha256)=64 AND lower(wire_sha256)=wire_sha256
    AND wire_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(wire_length)='integer' AND wire_length>=2 AND wire_length<=${MAX_BODY_BYTES}),
  CHECK(length(ack_challenge)=64 AND lower(ack_challenge)=ack_challenge
    AND ack_challenge NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(ack_challenge_sha256)=64
    AND lower(ack_challenge_sha256)=ack_challenge_sha256
    AND ack_challenge_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(client_public_key_json)
    AND json_type(client_public_key_json,'$')='object'
    AND length(CAST(client_public_key_json AS BLOB))<=512),
  CHECK(client_key_fingerprint GLOB 'p256-spki-sha256:*'
    AND length(client_key_fingerprint)=81),
  CHECK(turn_id IS NULL OR (length(turn_id)>=1 AND length(turn_id)<=${MAX_TURN_LENGTH})),
  CHECK(provenance_kind IN ('turn_result','durable_handoff',
    'request_supersession','ingress_rejection')),
  CHECK(length(provenance_ref)>=8 AND length(provenance_ref)<=240
    AND provenance_ref NOT GLOB '*[^A-Za-z0-9._:-]*'),
  CHECK(length(provenance_sha256)=64 AND lower(provenance_sha256)=provenance_sha256
    AND provenance_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(response_json) AND json_type(response_json,'$')='object'),
  CHECK(length(issued_ts)=24 AND substr(issued_ts,24,1)='Z')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_http_response_authority_request_v3
  ON eva_http_response_authorities_v3(request_ref,response_sha256);
CREATE TRIGGER IF NOT EXISTS eva_http_response_authorities_v3_no_update
BEFORE UPDATE ON eva_http_response_authorities_v3
BEGIN SELECT RAISE(ABORT,'HTTP response authorities v3 are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_response_authorities_v3_no_delete
BEFORE DELETE ON eva_http_response_authorities_v3
BEGIN SELECT RAISE(ABORT,'HTTP response authorities v3 are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_response_authorities_v3_no_replace
BEFORE INSERT ON eva_http_response_authorities_v3
WHEN EXISTS (
  SELECT 1 FROM eva_http_response_authorities_v3
  WHERE id=NEW.id OR authority_ref=NEW.authority_ref
    OR authority_sha256=NEW.authority_sha256 OR request_ref=NEW.request_ref
    OR response_ref=NEW.response_ref OR response_sha256=NEW.response_sha256
    OR delivery_ref=NEW.delivery_ref OR wire_sha256=NEW.wire_sha256
    OR ack_challenge=NEW.ack_challenge
    OR ack_challenge_sha256=NEW.ack_challenge_sha256
)
BEGIN SELECT RAISE(ABORT,'HTTP response authority v3 identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_response_authorities_v3_valid_ingress_response
BEFORE INSERT ON eva_http_response_authorities_v3
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_http_prepared_responses response
    JOIN eva_http_client_ack_bindings_v3 binding
      ON binding.request_row_id=response.request_row_id
      AND binding.request_ref=response.request_ref
    WHERE response.response_ref=NEW.response_ref
      AND response.response_sha256=NEW.response_sha256
      AND response.request_ref=NEW.request_ref
      AND response.status=NEW.status AND response.turn_id IS NEW.turn_id
      AND response.public_body_json=NEW.public_body_json
      AND response.public_body_sha256=NEW.public_body_sha256
      AND response.provenance_kind=NEW.provenance_kind
      AND response.provenance_ref=NEW.provenance_ref
      AND response.provenance_sha256=NEW.provenance_sha256
      AND binding.ack_challenge=NEW.ack_challenge
      AND binding.ack_challenge_sha256=NEW.ack_challenge_sha256
      AND binding.public_key_json=NEW.client_public_key_json
      AND binding.key_fingerprint=NEW.client_key_fingerprint
      AND EXISTS (
        SELECT 1 FROM eva_http_ingress_state_events event
        WHERE event.request_row_id=response.request_row_id
          AND event.request_ref=response.request_ref AND event.ordinal=1
          AND event.state=response.target_state
          AND event.response_ref=response.response_ref
      )
  ) THEN RAISE(ABORT,'HTTP response authority v3 requires exact durable ingress response') END;
  SELECT CASE WHEN typeof(NEW.wire_bytes)<>'blob'
    OR CAST(NEW.wire_bytes AS TEXT) IS NULL
    OR json_valid(CAST(NEW.wire_bytes AS TEXT))<>1
    OR json_type(CAST(NEW.wire_bytes AS TEXT),'$')<>'object'
    OR json_remove(CAST(NEW.wire_bytes AS TEXT),'$._httpDelivery')
      IS NOT json(NEW.public_body_json)
    OR json_extract(CAST(NEW.wire_bytes AS TEXT),'$._httpDelivery.contract')
      IS NOT 'HttpClientAckChallenge/v3'
    OR json_extract(CAST(NEW.wire_bytes AS TEXT),'$._httpDelivery.requestRef')
      IS NOT NEW.request_ref
    OR json_extract(CAST(NEW.wire_bytes AS TEXT),'$._httpDelivery.responseRef')
      IS NOT NEW.response_ref
    OR json_extract(CAST(NEW.wire_bytes AS TEXT),'$._httpDelivery.responseSha256')
      IS NOT NEW.response_sha256
    OR json_extract(CAST(NEW.wire_bytes AS TEXT),'$._httpDelivery.deliveryRef')
      IS NOT NEW.delivery_ref
    OR json_extract(CAST(NEW.wire_bytes AS TEXT),'$._httpDelivery.challenge')
      IS NOT NEW.ack_challenge
    OR json_extract(CAST(NEW.wire_bytes AS TEXT),'$._httpDelivery.keyFingerprint')
      IS NOT NEW.client_key_fingerprint
    OR length(NEW.wire_bytes)<>NEW.wire_length
  THEN RAISE(ABORT,'HTTP response authority v3 wire conflicts with ingress response') END;
END;
`;

const REQUIRED_COLUMNS = Object.freeze([
  'id', 'contract', 'authority_ref', 'authority_sha256', 'request_ref',
  'response_ref', 'response_sha256', 'delivery_ref', 'status', 'content_type',
  'public_body_json', 'public_body_sha256', 'wire_bytes', 'wire_sha256',
  'wire_length', 'ack_challenge', 'ack_challenge_sha256',
  'client_public_key_json', 'client_key_fingerprint', 'turn_id',
  'provenance_kind', 'provenance_ref', 'provenance_sha256', 'response_json',
  'issued_ts',
]);
const INDEX_SIGNATURES = Object.freeze([
  { name: INDEXES[0], origin: 'c', columns: ['request_ref', 'response_sha256'] },
  ...[
    'authority_ref', 'authority_sha256', 'request_ref', 'response_ref',
    'response_sha256', 'delivery_ref', 'wire_sha256', 'ack_challenge',
    'ack_challenge_sha256',
  ].map((column) => ({ origin: 'u', columns: [column] })),
]);

function plainObject(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  return [Object.prototype, null].includes(Object.getPrototypeOf(value));
}

function exactKeys(value, keys) {
  return plainObject(value)
    && Object.keys(value).sort().join('\0') === [...keys].sort().join('\0');
}

function freeze(value) {
  if (!value || typeof value !== 'object' || Object.isFrozen(value)
    || ArrayBuffer.isView(value)) return value;
  Object.freeze(value);
  for (const child of Object.values(value)) freeze(child);
  return value;
}

function normalizedDdl(value) {
  const source = String(value || '').trim().replace(/;$/u, '');
  let normalized = '';
  for (let index = 0; index < source.length;) {
    const char = source[index];
    if (/\s/u.test(char)) { index += 1; continue; }
    normalized += char;
    if (!["'", '"', '`'].includes(char)) { index += 1; continue; }
    const quote = char;
    index += 1;
    while (index < source.length) {
      const quoted = source[index];
      normalized += quoted;
      index += 1;
      if (quoted !== quote) continue;
      if (source[index] === quote) {
        normalized += source[index];
        index += 1;
      } else break;
    }
  }
  return normalized.replace(/IFNOTEXISTS/gu, '');
}

function expectedDdl(type, name) {
  const marker = type === 'table' ? `TABLE IF NOT EXISTS ${name}`
    : type === 'index' ? `INDEX IF NOT EXISTS ${name}`
      : `TRIGGER IF NOT EXISTS ${name}`;
  const markerAt = SCHEMA.indexOf(marker);
  if (markerAt < 0) return '';
  const previous = SCHEMA.lastIndexOf('\nCREATE ', markerAt);
  const start = previous < 0 ? SCHEMA.indexOf('CREATE ', 0) : previous + 1;
  const next = SCHEMA.indexOf('\nCREATE ', markerAt + marker.length);
  return SCHEMA.slice(start, next < 0 ? SCHEMA.length : next).trim();
}

function tempShadow() {
  const placeholders = PROTECTED.map(() => '?').join(',');
  return authorityDb.prepare(`SELECT 1 FROM sqlite_temp_master
    WHERE name IN (${placeholders}) OR tbl_name IN (${placeholders})
      OR name GLOB 'eva_http_response_authorit*v3*'
      OR tbl_name GLOB 'eva_http_response_authorit*v3*'
      OR name GLOB 'idx_http_response_authority*v3*'
      OR tbl_name GLOB 'idx_http_response_authority*v3*'
    LIMIT 1`).get(...PROTECTED, ...PROTECTED);
}

function schemaFailure(includeDependency = true) {
  if (initializationFailure) return initializationFailure;
  if (tempShadow()) return 'http_response_authority_v3_schema_temp_shadow';
  const columns = authorityDb.prepare(`PRAGMA main.table_info('${TABLE}')`).all()
    .map((column) => column.name);
  if (columns.join('\0') !== REQUIRED_COLUMNS.join('\0')) {
    return 'http_response_authority_v3_schema_columns_invalid';
  }
  if (!exactSqliteIndexSignatures(
    authorityDb, TABLE, REQUIRED_COLUMNS, INDEX_SIGNATURES,
  )) return 'http_response_authority_v3_schema_index_signatures_invalid';
  for (const [type, names] of [
    ['table', [TABLE]], ['index', INDEXES], ['trigger', TRIGGERS],
  ]) {
    for (const name of names) {
      const object = authorityDb.prepare(`SELECT sql FROM main.sqlite_master
        WHERE type=? AND name=?`).get(type, name);
      if (!object || normalizedDdl(object.sql) !== normalizedDdl(expectedDdl(type, name))) {
        return `http_response_authority_v3_schema_ddl_invalid:${type}:${name}`;
      }
    }
  }
  const objects = authorityDb.prepare(`SELECT type,name FROM main.sqlite_master
    WHERE (type='index' OR type='trigger') AND tbl_name=?`).all(TABLE);
  const indexes = objects.filter((item) => item.type === 'index'
    && !item.name.startsWith('sqlite_autoindex_')).map((item) => item.name).sort();
  const triggers = objects.filter((item) => item.type === 'trigger')
    .map((item) => item.name).sort();
  if (indexes.join('\0') !== [...INDEXES].sort().join('\0')
    || triggers.join('\0') !== [...TRIGGERS].sort().join('\0')) {
    return 'http_response_authority_v3_schema_objects_invalid';
  }
  const expectedNames = new Set([TABLE, ...INDEXES, ...TRIGGERS]);
  const reserved = authorityDb.prepare(`SELECT name FROM main.sqlite_master
    WHERE name GLOB 'eva_http_response_authorit*v3*'
      OR name GLOB 'idx_http_response_authority*v3*'`).all();
  if (reserved.some(({ name }) => !expectedNames.has(name))) {
    return 'http_response_authority_v3_schema_namespace_invalid';
  }
  if (includeDependency) {
    const dependency = durableHttpIngressResponseBoundaryStatus();
    if (!dependency.initialized) return 'http_response_authority_v3_ingress_uninitialized';
    if (!dependency.available) {
      return `http_response_authority_v3_ingress_unavailable:${dependency.reason || 'unknown'}`;
    }
  }
  return '';
}

function requireStore() {
  const reason = schemaFailure();
  if (reason) throw new Error(`HTTP response authority v3 store unavailable: ${reason}`);
}

function normalizeProvenance(value, turnId) {
  if (!exactKeys(value, ['contract', 'kind', 'ref', 'sha256'])
    || value.contract !== 'HttpResultProvenance/v1'
    || !['turn_result', 'durable_handoff', 'request_supersession', 'ingress_rejection']
      .includes(value.kind)
    || typeof value.ref !== 'string'
    || !/^[A-Za-z0-9][A-Za-z0-9._:-]{7,239}$/u.test(value.ref)
    || !SHA256.test(value.sha256)
    || (value.kind === 'turn_result' && (turnId === null || value.ref !== turnId))
    || (turnId === null && value.kind === 'turn_result')) {
    throw new TypeError('invalid HTTP response provenance');
  }
  return freeze({ ...value });
}

function durableIngressResponse(capability) {
  const proof = consumeDurableHttpIngressResponseAuthority(capability);
  const value = proof.response;
  if (!exactKeys(value, [
    'contract', 'channel', 'conversationId', 'intentSha256', 'provenance',
    'publicBody', 'requestId', 'state', 'status', 'turnId', 'responseRef',
    'sha256', 'preparedAt',
  ]) || value.contract !== 'PreparedHttpResponse/v1'
    || !RESPONSE_REF.test(value.responseRef) || !SHA256.test(value.sha256)) {
    throw new TypeError('invalid durable HTTP ingress response receipt');
  }
  const row = authorityDb.prepare(`SELECT response.*,request.channel,request.conversation_id,
      request.request_id,request.intent_sha256,request.request_ref,request.id AS ingress_row_id,
      binding.public_key_json,binding.key_fingerprint,binding.ack_challenge,
      binding.ack_challenge_sha256
    FROM main.eva_http_prepared_responses response
    JOIN main.eva_http_ingress_requests request ON request.id=response.request_row_id
    JOIN main.eva_http_client_ack_bindings_v3 binding
      ON binding.request_row_id=request.id AND binding.request_ref=request.request_ref
    WHERE response.response_ref=? AND response.response_sha256=?`).get(
    value.responseRef, value.sha256,
  );
  if (!row) throw new TypeError('HTTP response authority requires durable client-bound response');
  let publicBody;
  let storedResponse;
  let key;
  try {
    publicBody = JSON.parse(row.public_body_json);
    storedResponse = JSON.parse(row.response_json);
    key = normalizeHttpClientAckPublicKey(JSON.parse(row.public_key_json));
  } catch { throw new Error('invalid durable client-bound HTTP ingress response'); }
  const expectedReceipt = {
    ...storedResponse, responseRef: row.response_ref, sha256: row.response_sha256,
    preparedAt: row.prepared_ts,
  };
  const terminal = authorityDb.prepare(`SELECT 1 FROM main.eva_http_ingress_state_events
    WHERE request_row_id=? AND request_ref=? AND ordinal=1 AND state=? AND response_ref=?`)
    .get(row.request_row_id, row.request_ref, row.target_state, row.response_ref);
  if (!terminal || canonicalJson(value) !== canonicalJson(expectedReceipt)
    || canonicalJson(storedResponse) !== row.response_json
    || hash(row.response_json) !== row.response_sha256
    || row.response_ref !== `http-response:${row.response_sha256}`
    || canonicalJson(publicBody) !== row.public_body_json
    || hash(row.public_body_json) !== row.public_body_sha256
    || value.channel !== row.channel || value.conversationId !== row.conversation_id
    || value.requestId !== row.request_id || value.intentSha256 !== row.intent_sha256
    || proof.request.requestRef !== row.request_ref
    || proof.request.intentSha256 !== row.intent_sha256
    || proof.clientAckBinding.requestRef !== row.request_ref
    || proof.clientAckBinding.intentSha256 !== row.intent_sha256
    || proof.clientAckBinding.challenge !== row.ack_challenge
    || proof.clientAckBinding.challengeSha256 !== row.ack_challenge_sha256
    || proof.clientAckBinding.keyFingerprint !== row.key_fingerprint
    || key.publicKeyJson !== row.public_key_json
    || key.keyFingerprint !== row.key_fingerprint
    || hash(row.ack_challenge) !== row.ack_challenge_sha256) {
    throw new Error('invalid canonical durable client-bound HTTP ingress response');
  }
  const provenance = normalizeProvenance({
    contract: 'HttpResultProvenance/v1', kind: row.provenance_kind,
    ref: row.provenance_ref, sha256: row.provenance_sha256,
  }, row.turn_id);
  const authoritySeed = hash(canonicalJson({
    contract: 'HttpIngressResponseAuthoritySeed/v3', requestRef: row.request_ref,
    responseRef: row.response_ref, responseSha256: row.response_sha256,
    challenge: row.ack_challenge, keyFingerprint: row.key_fingerprint,
  }));
  const deliveryRef = `http-delivery-v3:${authoritySeed}`;
  const wireBody = {
    ...publicBody,
    _httpDelivery: {
      contract: 'HttpClientAckChallenge/v3', requestRef: row.request_ref,
      responseRef: row.response_ref, responseSha256: row.response_sha256,
      deliveryRef, challenge: row.ack_challenge,
      keyFingerprint: row.key_fingerprint,
    },
  };
  const wireBytes = Buffer.from(canonicalJson(wireBody), 'utf8');
  if (wireBytes.length > MAX_BODY_BYTES) throw new Error('HTTP response wire exceeds bound');
  const wireSha256 = canonicalHttpWireSha256({
    status: Number(row.status), contentType: CONTENT_TYPE, bodyBytes: wireBytes,
  });
  const response = freeze({
    contract: 'HttpIngressResponseWire/v3', requestRef: row.request_ref,
    responseRef: row.response_ref, responseSha256: row.response_sha256,
    deliveryRef, status: Number(row.status), contentType: CONTENT_TYPE,
    publicBodySha256: row.public_body_sha256, wireSha256,
    wireLength: wireBytes.length, ackChallengeSha256: row.ack_challenge_sha256,
    clientKeyFingerprint: row.key_fingerprint, turnId: row.turn_id, provenance,
  });
  const responseJson = canonicalJson(response);
  const authoritySha256 = hash(responseJson);
  return freeze({
    response, responseJson, authoritySha256,
    authorityRef: `http-response-authority-v3:${authoritySha256}`,
    publicBodyJson: row.public_body_json,
    publicBodySha256: row.public_body_sha256,
    wireBytes, wireSha256, wireLength: wireBytes.length,
    ackChallenge: row.ack_challenge,
    ackChallengeSha256: row.ack_challenge_sha256,
    clientPublicKeyJson: row.public_key_json,
    clientKeyFingerprint: row.key_fingerprint, deliveryRef,
  });
}

function values(normalized, issuedAt) {
  const response = normalized.response;
  return {
    contract: 'HttpIngressResponseAuthority/v3',
    authority_ref: normalized.authorityRef,
    authority_sha256: normalized.authoritySha256,
    request_ref: response.requestRef, response_ref: response.responseRef,
    response_sha256: response.responseSha256, delivery_ref: response.deliveryRef,
    status: response.status, content_type: response.contentType,
    public_body_json: normalized.publicBodyJson,
    public_body_sha256: normalized.publicBodySha256,
    wire_bytes: normalized.wireBytes, wire_sha256: normalized.wireSha256,
    wire_length: normalized.wireLength, ack_challenge: normalized.ackChallenge,
    ack_challenge_sha256: normalized.ackChallengeSha256,
    client_public_key_json: normalized.clientPublicKeyJson,
    client_key_fingerprint: normalized.clientKeyFingerprint,
    turn_id: response.turnId, provenance_kind: response.provenance.kind,
    provenance_ref: response.provenance.ref,
    provenance_sha256: response.provenance.sha256,
    response_json: normalized.responseJson, issued_ts: issuedAt,
  };
}

function exactStoredDeliveryEnvelope(envelope, row) {
  return exactKeys(envelope, [
    'contract', 'requestRef', 'responseRef', 'responseSha256',
    'deliveryRef', 'challenge', 'keyFingerprint',
  ]) && envelope.contract === 'HttpClientAckChallenge/v3'
    && envelope.requestRef === row.request_ref
    && envelope.responseRef === row.response_ref
    && envelope.responseSha256 === row.response_sha256
    && envelope.deliveryRef === row.delivery_ref
    && envelope.challenge === row.ack_challenge
    && envelope.keyFingerprint === row.client_key_fingerprint;
}

function exactStoredWireResponse(response, row) {
  return response.contract === 'HttpIngressResponseWire/v3'
    && response.requestRef === row.request_ref
    && response.responseRef === row.response_ref
    && response.responseSha256 === row.response_sha256
    && response.deliveryRef === row.delivery_ref
    && response.status === Number(row.status)
    && response.contentType === row.content_type
    && response.publicBodySha256 === row.public_body_sha256
    && response.wireSha256 === row.wire_sha256
    && response.wireLength === Number(row.wire_length)
    && response.ackChallengeSha256 === row.ack_challenge_sha256
    && response.clientKeyFingerprint === row.client_key_fingerprint
    && response.turnId === row.turn_id
    && response.provenance?.kind === row.provenance_kind
    && response.provenance?.ref === row.provenance_ref
    && response.provenance?.sha256 === row.provenance_sha256;
}

function normalizeStored(row) {
  if (!row || row.contract !== 'HttpIngressResponseAuthority/v3'
    || !AUTHORITY_REF.test(row.authority_ref) || !DELIVERY_REF.test(row.delivery_ref)
    || !REQUEST_REF.test(row.request_ref) || !RESPONSE_REF.test(row.response_ref)
    || !KEY_FINGERPRINT.test(row.client_key_fingerprint)) {
    throw new Error('invalid durable HTTP response authority v3');
  }
  let response;
  let publicBody;
  let wireBody;
  let key;
  try {
    response = JSON.parse(row.response_json);
    publicBody = JSON.parse(row.public_body_json);
    wireBody = JSON.parse(Buffer.from(row.wire_bytes).toString('utf8'));
    key = normalizeHttpClientAckPublicKey(JSON.parse(row.client_public_key_json));
  } catch { throw new Error('invalid durable HTTP response authority v3'); }
  const wireBytes = Buffer.from(row.wire_bytes);
  const envelope = wireBody._httpDelivery;
  const stripped = { ...wireBody };
  delete stripped._httpDelivery;
  if (canonicalJson(response) !== row.response_json
    || hash(row.response_json) !== row.authority_sha256
    || row.authority_ref !== `http-response-authority-v3:${row.authority_sha256}`
    || canonicalJson(publicBody) !== row.public_body_json
    || hash(row.public_body_json) !== row.public_body_sha256
    || canonicalJson(stripped) !== row.public_body_json
    || canonicalJson(wireBody) !== wireBytes.toString('utf8')
    || canonicalHttpWireSha256({
      status: Number(row.status), contentType: row.content_type, bodyBytes: wireBytes,
    }) !== row.wire_sha256
    || wireBytes.length !== Number(row.wire_length)
    || hash(row.ack_challenge) !== row.ack_challenge_sha256
    || key.publicKeyJson !== row.client_public_key_json
    || key.keyFingerprint !== row.client_key_fingerprint
    || !exactStoredDeliveryEnvelope(envelope, row)
    || !exactStoredWireResponse(response, row)) {
    throw new Error('invalid canonical durable HTTP response authority v3');
  }
  return freeze({ response, key: key.publicKey, wireBytes });
}

function receipt(row) {
  const normalized = normalizeStored(row);
  return freeze({
    contract: 'HttpIngressResponseReceipt/v3', authorityRef: row.authority_ref,
    authoritySha256: row.authority_sha256, requestRef: row.request_ref,
    responseRef: row.response_ref, responseSha256: row.response_sha256,
    deliveryRef: row.delivery_ref, status: Number(row.status),
    contentType: row.content_type, publicBodySha256: row.public_body_sha256,
    wireSha256: row.wire_sha256, wireLength: Number(row.wire_length),
    ackChallengeSha256: row.ack_challenge_sha256,
    clientKeyFingerprint: row.client_key_fingerprint, turnId: row.turn_id,
    provenance: normalized.response.provenance, issuedAt: row.issued_ts,
  });
}

function canonicalWire(row) {
  const normalized = normalizeStored(row);
  const wire = {
    contract: 'CanonicalHttpWire/v3', status: Number(row.status),
    contentType: row.content_type, wireSha256: row.wire_sha256,
    wireLength: Number(row.wire_length),
  };
  Object.defineProperty(wire, 'bodyBytes', {
    enumerable: true, get: () => Buffer.from(normalized.wireBytes),
  });
  return freeze(wire);
}

try {
  const existing = authorityDb.prepare(`SELECT name FROM main.sqlite_master
    WHERE type='table' AND name=?`).get(TABLE);
  if (!existing) authorityDb.exec(SCHEMA);
  else if (schemaFailure(false)) initializationFailure = schemaFailure(false);
} catch (error) {
  initializationFailure = `http_response_authority_v3_initialization_failed:${error.message}`;
}

export function httpResponseAuthorityStoreStatus() {
  const reason = schemaFailure();
  return freeze({
    contract: 'HttpResponseAuthorityStoreStatus/v3', available: !reason,
    ...(reason ? { reason } : {}),
  });
}

export function issueHttpResponseAuthority(input) {
  requireStore();
  const normalized = durableIngressResponse(input);
  let row = authorityDb.prepare(`SELECT * FROM ${mainTable(TABLE)} WHERE request_ref=?`)
    .get(normalized.response.requestRef);
  if (row) {
    normalizeStored(row);
    if (row.response_ref !== normalized.response.responseRef
      || row.response_sha256 !== normalized.response.responseSha256
      || row.authority_sha256 !== normalized.authoritySha256) {
      return freeze({ contract: 'HttpResponseAuthorityConflict/v3', kind: 'conflict' });
    }
  } else {
    authorityDb.prepare(`INSERT INTO ${mainTable(TABLE)} (
      contract,authority_ref,authority_sha256,request_ref,response_ref,response_sha256,
      delivery_ref,status,content_type,public_body_json,public_body_sha256,wire_bytes,
      wire_sha256,wire_length,ack_challenge,ack_challenge_sha256,
      client_public_key_json,client_key_fingerprint,turn_id,provenance_kind,
      provenance_ref,provenance_sha256,response_json,issued_ts
    ) VALUES (
      @contract,@authority_ref,@authority_sha256,@request_ref,@response_ref,@response_sha256,
      @delivery_ref,@status,@content_type,@public_body_json,@public_body_sha256,@wire_bytes,
      @wire_sha256,@wire_length,@ack_challenge,@ack_challenge_sha256,
      @client_public_key_json,@client_key_fingerprint,@turn_id,@provenance_kind,
      @provenance_ref,@provenance_sha256,@response_json,@issued_ts
    )`).run(values(normalized, nowIso()));
    row = authorityDb.prepare(`SELECT * FROM ${mainTable(TABLE)} WHERE request_ref=?`)
      .get(normalized.response.requestRef);
  }
  normalizeStored(row);
  const capability = freeze({ nonce: randomBytes(32).toString('hex') });
  ISSUED.set(capability, freeze({
    rowId: Number(row.id), authorityRef: row.authority_ref,
    authoritySha256: row.authority_sha256,
  }));
  return freeze({
    contract: 'IssuedHttpResponseAuthority/v3', kind: 'issued',
    response: receipt(row), authority: capability,
  });
}

export function consumeHttpResponseAuthority(capability) {
  requireStore();
  const attestation = ISSUED.get(capability);
  if (!attestation) throw new Error('invalid opaque one-shot HTTP response authority v3');
  ISSUED.delete(capability);
  const row = authorityDb.prepare(`SELECT * FROM ${mainTable(TABLE)}
    WHERE id=? AND authority_ref=? AND authority_sha256=?`).get(
    attestation.rowId, attestation.authorityRef, attestation.authoritySha256,
  );
  if (!row) throw new Error('HTTP response authority v3 lost its durable response');
  const normalized = normalizeStored(row);
  return freeze({
    contract: 'ConsumedHttpResponseAuthority/v3', response: receipt(row),
    wire: canonicalWire(row), clientPublicKey: normalized.key,
  });
}

export function httpResponseAuthorityCurrent(capability) {
  const attestation = ISSUED.get(capability);
  if (!attestation || schemaFailure()) return false;
  try {
    const row = authorityDb.prepare(`SELECT * FROM ${mainTable(TABLE)} WHERE id=?`)
      .get(attestation.rowId);
    normalizeStored(row);
    return row.authority_ref === attestation.authorityRef
      && row.authority_sha256 === attestation.authoritySha256;
  } catch { return false; }
}
