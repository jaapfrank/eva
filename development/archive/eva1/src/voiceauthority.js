import { createHash } from 'node:crypto';

import { canonicalJson, isoNow } from './util.js';

export const VOICE_DELIVERY_AUTHORITY_CONTRACT = 'VoiceDeliveryAuthorityEvent/v1';
export const VOICE_DELIVERY_AUTHORITY_TABLE = 'eva_voice_delivery_authority_events_v1';

const TABLE = VOICE_DELIVERY_AUTHORITY_TABLE;
const SHA256 = /^[a-f0-9]{64}$/u;
const REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/u;
const SATELLITE = /^assist_satellite\.[a-z0-9_]+$/u;
const SOURCE_REF = /^home-assistant-websocket:[a-f0-9]{64}$/u;
const OFFER_REF = /^home-assistant-voice-reply:[a-f0-9]{64}$/u;
const MAX_JSON_BYTES = 64 * 1024;
const MAX_EVENTS_PER_REQUEST = 4;
const MAX_STORE_EVENTS = 200_000;
const AUTHENTICATED_VOICE_STORE_SOURCES = new WeakMap();
const COMMITTED_VOICE_STORE_SOURCES = new WeakMap();
const PENDING_ACKNOWLEDGED_VOICE_RECORDS = new WeakMap();
const VOICE_FINAL_DELIVERY_RECOVERY_PROOFS = new WeakMap();

export const VOICE_DELIVERY_AUTHORITY_TABLE_SQL = `CREATE TABLE ${TABLE} (
  contract TEXT NOT NULL,
  request_id TEXT NOT NULL,
  event_generation INTEGER NOT NULL,
  event_kind TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  requester TEXT NOT NULL,
  satellite TEXT NOT NULL,
  adapter_instance_sha256 TEXT NOT NULL,
  socket_generation INTEGER NOT NULL,
  query_source_ref TEXT NOT NULL,
  query_source_sha256 TEXT NOT NULL,
  query_source_body_json TEXT NOT NULL,
  turn_id TEXT,
  offer_operation_ref TEXT,
  offer_operation_sha256 TEXT,
  offer_payload_json TEXT,
  offer_payload_sha256 TEXT,
  offer_reply_sha256 TEXT,
  ack_source_ref TEXT,
  ack_source_sha256 TEXT,
  ack_source_body_json TEXT,
  acceptance_delivery_json TEXT,
  acceptance_delivery_sha256 TEXT,
  result_body_json TEXT,
  result_sha256 TEXT,
  predecessor_sha256 TEXT,
  event_sha256 TEXT NOT NULL,
  issued_ts TEXT NOT NULL,
  PRIMARY KEY(request_id,event_generation),
  UNIQUE(request_id,event_kind),
  UNIQUE(event_sha256),
  CHECK(contract='VoiceDeliveryAuthorityEvent/v1'),
  CHECK(event_kind IN ('query','offer','acknowledgement','delivered')),
  CHECK((event_generation=1 AND event_kind='query')
    OR (event_generation=2 AND event_kind='offer')
    OR (event_generation=3 AND event_kind='acknowledgement')
    OR (event_generation=4 AND event_kind='delivered')),
  CHECK(length(request_id)>=1 AND length(request_id)<=160),
  CHECK(length(conversation_id)>=1 AND length(conversation_id)<=240),
  CHECK(length(requester)>=1 AND length(requester)<=160),
  CHECK(length(satellite)>=18 AND length(satellite)<=160),
  CHECK(length(adapter_instance_sha256)=64 AND lower(adapter_instance_sha256)=adapter_instance_sha256
    AND adapter_instance_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(socket_generation)='integer' AND socket_generation>=1
    AND socket_generation<=2147483647),
  CHECK(length(query_source_ref)>=65 AND length(query_source_ref)<=320),
  CHECK(length(query_source_sha256)=64 AND lower(query_source_sha256)=query_source_sha256
    AND query_source_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(CAST(query_source_body_json AS BLOB))>=2
    AND length(CAST(query_source_body_json AS BLOB))<=65536
    AND json_valid(query_source_body_json) AND json_type(query_source_body_json)='object'),
  CHECK((event_generation=1 AND turn_id IS NULL AND offer_operation_ref IS NULL
      AND offer_operation_sha256 IS NULL AND offer_payload_json IS NULL
      AND offer_payload_sha256 IS NULL AND offer_reply_sha256 IS NULL
      AND ack_source_ref IS NULL AND ack_source_sha256 IS NULL
      AND ack_source_body_json IS NULL AND acceptance_delivery_json IS NULL
      AND acceptance_delivery_sha256 IS NULL AND result_body_json IS NULL
      AND result_sha256 IS NULL AND predecessor_sha256 IS NULL)
    OR (event_generation=2 AND turn_id IS NOT NULL AND offer_operation_ref IS NOT NULL
      AND offer_operation_sha256 IS NOT NULL AND offer_payload_json IS NOT NULL
      AND offer_payload_sha256 IS NOT NULL AND offer_reply_sha256 IS NOT NULL
      AND ack_source_ref IS NULL AND ack_source_sha256 IS NULL
      AND ack_source_body_json IS NULL AND acceptance_delivery_json IS NULL
      AND acceptance_delivery_sha256 IS NULL AND result_body_json IS NULL
      AND result_sha256 IS NULL AND predecessor_sha256 IS NOT NULL)
    OR (event_generation=3 AND turn_id IS NOT NULL AND offer_operation_ref IS NOT NULL
      AND offer_operation_sha256 IS NOT NULL AND offer_payload_json IS NOT NULL
      AND offer_payload_sha256 IS NOT NULL AND offer_reply_sha256 IS NOT NULL
      AND ack_source_ref IS NOT NULL AND ack_source_sha256 IS NOT NULL
      AND ack_source_body_json IS NOT NULL AND acceptance_delivery_json IS NOT NULL
      AND acceptance_delivery_sha256 IS NOT NULL AND result_body_json IS NULL
      AND result_sha256 IS NULL AND predecessor_sha256 IS NOT NULL)
    OR (event_generation=4 AND turn_id IS NOT NULL AND offer_operation_ref IS NOT NULL
      AND offer_operation_sha256 IS NOT NULL AND offer_payload_json IS NOT NULL
      AND offer_payload_sha256 IS NOT NULL AND offer_reply_sha256 IS NOT NULL
      AND ack_source_ref IS NOT NULL AND ack_source_sha256 IS NOT NULL
      AND ack_source_body_json IS NOT NULL AND acceptance_delivery_json IS NOT NULL
      AND acceptance_delivery_sha256 IS NOT NULL AND result_body_json IS NOT NULL
      AND result_sha256 IS NOT NULL AND predecessor_sha256 IS NOT NULL)),
  CHECK(turn_id IS NULL OR (length(turn_id)>=1 AND length(turn_id)<=320)),
  CHECK(offer_operation_ref IS NULL OR length(offer_operation_ref)=91),
  CHECK(offer_operation_sha256 IS NULL OR (length(offer_operation_sha256)=64
    AND lower(offer_operation_sha256)=offer_operation_sha256
    AND offer_operation_sha256 NOT GLOB '*[^a-f0-9]*')),
  CHECK(offer_payload_json IS NULL OR (length(CAST(offer_payload_json AS BLOB))>=2
    AND length(CAST(offer_payload_json AS BLOB))<=65536
    AND json_valid(offer_payload_json) AND json_type(offer_payload_json)='object')),
  CHECK(offer_payload_sha256 IS NULL OR (length(offer_payload_sha256)=64
    AND lower(offer_payload_sha256)=offer_payload_sha256
    AND offer_payload_sha256 NOT GLOB '*[^a-f0-9]*')),
  CHECK(offer_reply_sha256 IS NULL OR (length(offer_reply_sha256)=64
    AND lower(offer_reply_sha256)=offer_reply_sha256
    AND offer_reply_sha256 NOT GLOB '*[^a-f0-9]*')),
  CHECK(ack_source_ref IS NULL OR (length(ack_source_ref)>=65 AND length(ack_source_ref)<=320)),
  CHECK(ack_source_sha256 IS NULL OR (length(ack_source_sha256)=64
    AND lower(ack_source_sha256)=ack_source_sha256
    AND ack_source_sha256 NOT GLOB '*[^a-f0-9]*')),
  CHECK(ack_source_body_json IS NULL OR (length(CAST(ack_source_body_json AS BLOB))>=2
    AND length(CAST(ack_source_body_json AS BLOB))<=65536
    AND json_valid(ack_source_body_json) AND json_type(ack_source_body_json)='object')),
  CHECK(acceptance_delivery_json IS NULL OR (length(CAST(acceptance_delivery_json AS BLOB))>=2
    AND length(CAST(acceptance_delivery_json AS BLOB))<=65536
    AND json_valid(acceptance_delivery_json) AND json_type(acceptance_delivery_json)='object')),
  CHECK(acceptance_delivery_sha256 IS NULL OR (length(acceptance_delivery_sha256)=64
    AND lower(acceptance_delivery_sha256)=acceptance_delivery_sha256
    AND acceptance_delivery_sha256 NOT GLOB '*[^a-f0-9]*')),
  CHECK(result_body_json IS NULL OR (length(CAST(result_body_json AS BLOB))>=2
    AND length(CAST(result_body_json AS BLOB))<=65536
    AND json_valid(result_body_json) AND json_type(result_body_json)='object')),
  CHECK(result_sha256 IS NULL OR (length(result_sha256)=64 AND lower(result_sha256)=result_sha256
    AND result_sha256 NOT GLOB '*[^a-f0-9]*')),
  CHECK(predecessor_sha256 IS NULL OR (length(predecessor_sha256)=64
    AND lower(predecessor_sha256)=predecessor_sha256
    AND predecessor_sha256 NOT GLOB '*[^a-f0-9]*')),
  CHECK(length(event_sha256)=64 AND lower(event_sha256)=event_sha256
    AND event_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(issued_ts)>=20 AND length(issued_ts)<=40)
)`;

export const VOICE_DELIVERY_AUTHORITY_INDEX_SQL = Object.freeze({
  eva_voice_delivery_authority_events_v1_query_source_once:
    `CREATE UNIQUE INDEX eva_voice_delivery_authority_events_v1_query_source_once
    ON ${TABLE}(query_source_ref) WHERE event_generation=1`,
  eva_voice_delivery_authority_events_v1_offer_operation_once:
    `CREATE UNIQUE INDEX eva_voice_delivery_authority_events_v1_offer_operation_once
    ON ${TABLE}(offer_operation_ref) WHERE event_generation=2`,
  eva_voice_delivery_authority_events_v1_ack_source_once:
    `CREATE UNIQUE INDEX eva_voice_delivery_authority_events_v1_ack_source_once
    ON ${TABLE}(ack_source_ref) WHERE event_generation=3`,
});

export const VOICE_DELIVERY_AUTHORITY_TRIGGER_SQL = Object.freeze({
  eva_voice_delivery_authority_events_v1_no_update:
    `CREATE TRIGGER eva_voice_delivery_authority_events_v1_no_update
    BEFORE UPDATE ON ${TABLE}
    BEGIN SELECT RAISE(ABORT,'voice delivery authority is append-only'); END`,
  eva_voice_delivery_authority_events_v1_no_delete:
    `CREATE TRIGGER eva_voice_delivery_authority_events_v1_no_delete
    BEFORE DELETE ON ${TABLE}
    BEGIN SELECT RAISE(ABORT,'voice delivery authority is append-only'); END`,
  eva_voice_delivery_authority_events_v1_no_replace:
    `CREATE TRIGGER eva_voice_delivery_authority_events_v1_no_replace
    BEFORE INSERT ON ${TABLE}
    WHEN EXISTS (SELECT 1 FROM main.${TABLE} existing
      WHERE (existing.request_id=NEW.request_id
          AND existing.event_generation=NEW.event_generation)
        OR (existing.request_id=NEW.request_id AND existing.event_kind=NEW.event_kind)
        OR existing.event_sha256=NEW.event_sha256)
    BEGIN SELECT RAISE(ABORT,'voice delivery authority replacement is forbidden'); END`,
  eva_voice_delivery_authority_events_v1_valid_successor:
    `CREATE TRIGGER eva_voice_delivery_authority_events_v1_valid_successor
    BEFORE INSERT ON ${TABLE}
    BEGIN
      SELECT CASE WHEN NEW.event_generation=1 AND EXISTS (
        SELECT 1 FROM main.${TABLE} prior WHERE prior.request_id=NEW.request_id)
      THEN RAISE(ABORT,'voice delivery query already exists') END;
      SELECT CASE WHEN (
        SELECT count(*) FROM main.${TABLE})>=200000
      THEN RAISE(ABORT,'voice delivery authority store row bound reached') END;
      SELECT CASE WHEN NEW.event_generation>1 AND NOT EXISTS (
        SELECT 1 FROM main.${TABLE} prior
        WHERE prior.request_id=NEW.request_id
          AND prior.event_generation=NEW.event_generation-1
          AND prior.event_sha256=NEW.predecessor_sha256)
      THEN RAISE(ABORT,'voice delivery predecessor is missing') END;
      SELECT CASE WHEN NEW.event_generation>1 AND EXISTS (
        SELECT 1 FROM main.${TABLE} prior
        WHERE prior.request_id=NEW.request_id
          AND prior.event_generation=NEW.event_generation-1
          AND (prior.conversation_id<>NEW.conversation_id
            OR prior.requester<>NEW.requester
            OR prior.satellite<>NEW.satellite
            OR prior.adapter_instance_sha256<>NEW.adapter_instance_sha256
            OR prior.socket_generation<>NEW.socket_generation
            OR prior.query_source_ref<>NEW.query_source_ref
            OR prior.query_source_sha256<>NEW.query_source_sha256
            OR prior.query_source_body_json<>NEW.query_source_body_json))
      THEN RAISE(ABORT,'voice delivery successor changes query authority') END;
      SELECT CASE WHEN NEW.event_generation>2 AND EXISTS (
        SELECT 1 FROM main.${TABLE} prior
        WHERE prior.request_id=NEW.request_id
          AND prior.event_generation=NEW.event_generation-1
          AND (prior.turn_id<>NEW.turn_id
            OR prior.offer_operation_ref<>NEW.offer_operation_ref
            OR prior.offer_operation_sha256<>NEW.offer_operation_sha256
            OR prior.offer_payload_json<>NEW.offer_payload_json
            OR prior.offer_payload_sha256<>NEW.offer_payload_sha256
            OR prior.offer_reply_sha256<>NEW.offer_reply_sha256))
      THEN RAISE(ABORT,'voice delivery successor changes offer intent') END;
      SELECT CASE WHEN NEW.event_generation>3 AND EXISTS (
        SELECT 1 FROM main.${TABLE} prior
        WHERE prior.request_id=NEW.request_id
          AND prior.event_generation=NEW.event_generation-1
          AND (prior.ack_source_ref<>NEW.ack_source_ref
            OR prior.ack_source_sha256<>NEW.ack_source_sha256
            OR prior.ack_source_body_json<>NEW.ack_source_body_json
            OR prior.acceptance_delivery_json<>NEW.acceptance_delivery_json
            OR prior.acceptance_delivery_sha256<>NEW.acceptance_delivery_sha256))
      THEN RAISE(ABORT,'voice delivery successor changes acknowledgement') END;
      SELECT CASE WHEN NEW.event_generation=4 AND NOT EXISTS (
        SELECT 1 FROM main.timeline delivered
        WHERE delivered.kind='chat_out'
          AND json_valid(delivered.meta)
          AND CAST(json_extract(delivered.meta,'$.turnId') AS TEXT)=NEW.turn_id
          AND CAST(json_extract(delivered.meta,'$.deliveredResultSha256') AS TEXT)=NEW.result_sha256
          AND CAST(json_extract(delivered.meta,'$.deliveredAuthorityDeliverySha256') AS TEXT)
            =NEW.acceptance_delivery_sha256)
      THEN RAISE(ABORT,'voice delivered projection requires exact atomic chat_out') END;
    END`,
});

// Exact predecessor vóór de atomair herbruikbare acceptance-deliverybytes.
export const VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TABLE_SQL =
  VOICE_DELIVERY_AUTHORITY_TABLE_SQL
    .replace('  acceptance_delivery_json TEXT,\n  acceptance_delivery_sha256 TEXT,\n', '')
    .replaceAll(' AND acceptance_delivery_json IS NULL\n      AND acceptance_delivery_sha256 IS NULL', '')
    .replaceAll(' AND acceptance_delivery_json IS NOT NULL\n      AND acceptance_delivery_sha256 IS NOT NULL', '')
    .replace(`  CHECK(acceptance_delivery_json IS NULL OR (length(CAST(acceptance_delivery_json AS BLOB))>=2
    AND length(CAST(acceptance_delivery_json AS BLOB))<=65536
    AND json_valid(acceptance_delivery_json) AND json_type(acceptance_delivery_json)='object')),
  CHECK(acceptance_delivery_sha256 IS NULL OR (length(acceptance_delivery_sha256)=64
    AND lower(acceptance_delivery_sha256)=acceptance_delivery_sha256
    AND acceptance_delivery_sha256 NOT GLOB '*[^a-f0-9]*')),
`, '');

export const VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TRIGGER_SQL = Object.freeze({
  ...VOICE_DELIVERY_AUTHORITY_TRIGGER_SQL,
  eva_voice_delivery_authority_events_v1_valid_successor:
    VOICE_DELIVERY_AUTHORITY_TRIGGER_SQL
      .eva_voice_delivery_authority_events_v1_valid_successor
      .replace(`
            OR prior.ack_source_body_json<>NEW.ack_source_body_json
            OR prior.acceptance_delivery_json<>NEW.acceptance_delivery_json
            OR prior.acceptance_delivery_sha256<>NEW.acceptance_delivery_sha256))`, `
            OR prior.ack_source_body_json<>NEW.ack_source_body_json))`)
      .replace(`
      SELECT CASE WHEN NEW.event_generation=4 AND NOT EXISTS (
        SELECT 1 FROM main.timeline delivered
        WHERE delivered.kind='chat_out'
          AND json_valid(delivered.meta)
          AND CAST(json_extract(delivered.meta,'$.turnId') AS TEXT)=NEW.turn_id
          AND CAST(json_extract(delivered.meta,'$.deliveredResultSha256') AS TEXT)=NEW.result_sha256
          AND CAST(json_extract(delivered.meta,'$.deliveredAuthorityDeliverySha256') AS TEXT)
            =NEW.acceptance_delivery_sha256)
      THEN RAISE(ABORT,'voice delivered projection requires exact atomic chat_out') END;`, ''),
});
export const VOICE_DELIVERY_AUTHORITY_PREDECESSOR_INDEX_SQL =
  VOICE_DELIVERY_AUTHORITY_INDEX_SQL;

const EXPECTED_COLUMNS = Object.freeze([
  ['contract', 'TEXT', 1, 0], ['request_id', 'TEXT', 1, 1],
  ['event_generation', 'INTEGER', 1, 2], ['event_kind', 'TEXT', 1, 0],
  ['conversation_id', 'TEXT', 1, 0], ['requester', 'TEXT', 1, 0],
  ['satellite', 'TEXT', 1, 0], ['adapter_instance_sha256', 'TEXT', 1, 0],
  ['socket_generation', 'INTEGER', 1, 0], ['query_source_ref', 'TEXT', 1, 0],
  ['query_source_sha256', 'TEXT', 1, 0], ['query_source_body_json', 'TEXT', 1, 0],
  ['turn_id', 'TEXT', 0, 0], ['offer_operation_ref', 'TEXT', 0, 0],
  ['offer_operation_sha256', 'TEXT', 0, 0], ['offer_payload_json', 'TEXT', 0, 0],
  ['offer_payload_sha256', 'TEXT', 0, 0], ['offer_reply_sha256', 'TEXT', 0, 0],
  ['ack_source_ref', 'TEXT', 0, 0], ['ack_source_sha256', 'TEXT', 0, 0],
  ['ack_source_body_json', 'TEXT', 0, 0], ['acceptance_delivery_json', 'TEXT', 0, 0],
  ['acceptance_delivery_sha256', 'TEXT', 0, 0], ['result_body_json', 'TEXT', 0, 0],
  ['result_sha256', 'TEXT', 0, 0], ['predecessor_sha256', 'TEXT', 0, 0],
  ['event_sha256', 'TEXT', 1, 0], ['issued_ts', 'TEXT', 1, 0],
]);
const PREDECESSOR_COLUMNS = Object.freeze(EXPECTED_COLUMNS.filter(
  ([name]) => !['acceptance_delivery_json', 'acceptance_delivery_sha256'].includes(String(name)),
));

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function normalizeSql(value) {
  const source = String(value || '');
  const literals = [];
  let syntax = '';
  for (let index = 0; index < source.length;) {
    if (source[index] !== "'") { syntax += source[index]; index += 1; continue; }
    const start = index;
    index += 1;
    while (index < source.length) {
      if (source[index] !== "'") { index += 1; continue; }
      if (source[index + 1] === "'") { index += 2; continue; }
      index += 1;
      break;
    }
    const literal = literals.push(source.slice(start, index)) - 1;
    syntax += `\u{e000}${literal}\u{e001}`;
  }
  const normalized = syntax.replace(/\bmain\./gu, '').replace(/;\s*$/u, '')
    .replace(/\s+/gu, ' ').replace(/\s*([(),=])\s*/gu, '$1').trim().toLowerCase();
  return normalized.replace(/\u{e000}(\d+)\u{e001}/gu, (
    _match, literal,
  ) => literals[Number(literal)]);
}

function exactJson(value, label) {
  if (typeof value !== 'string' || Buffer.byteLength(value, 'utf8') < 2
    || Buffer.byteLength(value, 'utf8') > MAX_JSON_BYTES) {
    throw new Error(`${label} overschrijdt de toegestane bytes of ontbreekt`);
  }
  let parsed;
  try { parsed = JSON.parse(value); } catch { throw new Error(`${label} is geen JSON`); }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)
    || canonicalJson(parsed) !== value) throw new Error(`${label} is niet canoniek`);
  return parsed;
}

function temporaryShadow(database) {
  return database.prepare(`SELECT type,name,tbl_name FROM sqlite_temp_master
    WHERE lower(name) GLOB 'eva_voice_delivery_authorit*'
      OR lower(tbl_name) GLOB 'eva_voice_delivery_authorit*'
    ORDER BY type,name LIMIT 1`).get() || null;
}

function exactColumns(database, expected = EXPECTED_COLUMNS) {
  const columns = database.prepare(`PRAGMA main.table_info('${TABLE}')`).all().map((column) => [
    String(column.name), String(column.type).toUpperCase(),
    Number(column.notnull), Number(column.pk),
  ]);
  return canonicalJson(columns) === canonicalJson(expected);
}

/** @param {any} database @param {any} [options] */
function exactObjects(database, {
  tableSql = VOICE_DELIVERY_AUTHORITY_TABLE_SQL,
  triggerSql = VOICE_DELIVERY_AUTHORITY_TRIGGER_SQL,
  columns = EXPECTED_COLUMNS,
} = {}) {
  if (temporaryShadow(database)) return false;
  const table = database.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='table' AND name=?`).get(TABLE);
  if (normalizeSql(table?.sql) !== normalizeSql(tableSql) || !exactColumns(database, columns)) {
    return false;
  }
  const triggers = database.prepare(`SELECT name,sql FROM main.sqlite_master
    WHERE type='trigger' AND tbl_name=? ORDER BY name`).all(TABLE);
  const triggerNames = Object.keys(triggerSql).sort();
  if (canonicalJson(triggers.map(({ name }) => String(name))) !== canonicalJson(triggerNames)
    || triggers.some(({ name, sql }) => normalizeSql(sql) !== normalizeSql(triggerSql[name]))) {
    return false;
  }
  const indexes = database.prepare(`SELECT name,sql FROM main.sqlite_master
    WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name`).all(TABLE);
  const indexNames = Object.keys(VOICE_DELIVERY_AUTHORITY_INDEX_SQL).sort();
  if (canonicalJson(indexes.map(({ name }) => String(name))) !== canonicalJson(indexNames)
    || indexes.some(({ name, sql }) => (
      normalizeSql(sql) !== normalizeSql(VOICE_DELIVERY_AUTHORITY_INDEX_SQL[name])
    ))) return false;
  const expectedNames = new Map([[TABLE, ['table', TABLE]]]);
  for (let ordinal = 1; ordinal <= 3; ordinal += 1) {
    expectedNames.set(`sqlite_autoindex_${TABLE}_${ordinal}`, ['index', TABLE]);
  }
  for (const name of indexNames) expectedNames.set(name, ['index', TABLE]);
  for (const name of triggerNames) expectedNames.set(name, ['trigger', TABLE]);
  const namespace = database.prepare(`SELECT type,name,tbl_name FROM main.sqlite_master
    WHERE lower(name) GLOB 'eva_voice_delivery_authorit*'
      OR lower(tbl_name) GLOB 'eva_voice_delivery_authorit*'
    ORDER BY type,name`).all();
  return namespace.length === expectedNames.size && namespace.every((row) => {
    const expected = expectedNames.get(String(row.name));
    return expected && row.type === expected[0] && row.tbl_name === expected[1];
  });
}

function exactPredecessor(database) {
  try {
    return exactObjects(database, {
      tableSql: VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TABLE_SQL,
      triggerSql: VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TRIGGER_SQL,
      columns: PREDECESSOR_COLUMNS,
    });
  } catch { return false; }
}

export function voiceDeliveryAuthorityStoreStatus(database) {
  try {
    if (!exactObjects(database)) throw new Error('schema/index/trigger/namespace is inexact');
    return Object.freeze({ contract: 'VoiceDeliveryAuthorityStoreStatus/v1', available: true });
  } catch (error) {
    return Object.freeze({
      contract: 'VoiceDeliveryAuthorityStoreStatus/v1', available: false,
      reason: String(error?.message || error || 'unknown'),
    });
  }
}

function requireStore(database) {
  const status = voiceDeliveryAuthorityStoreStatus(database);
  if (!status.available) {
    throw new Error(`voice delivery authority store unavailable: ${
      'reason' in status ? status.reason : 'unavailable'
    }`);
  }
  return status;
}

function createExactStore(database) {
  database.exec(VOICE_DELIVERY_AUTHORITY_TABLE_SQL);
  for (const sql of Object.values(VOICE_DELIVERY_AUTHORITY_INDEX_SQL)) database.exec(sql);
  for (const sql of Object.values(VOICE_DELIVERY_AUTHORITY_TRIGGER_SQL)) database.exec(sql);
}

function migrateExactEmptyPredecessor(database) {
  if (!exactPredecessor(database)) return false;
  const count = Number(database.prepare(`SELECT count(*) AS value FROM main.${TABLE}`).get().value);
  if (count !== 0) throw new Error('exact predecessor bevat niet-migreerbare authorityrijen');
  database.transaction(() => {
    for (const name of Object.keys(VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TRIGGER_SQL)) {
      database.exec(`DROP TRIGGER main.${name}`);
    }
    for (const name of Object.keys(VOICE_DELIVERY_AUTHORITY_INDEX_SQL)) {
      database.exec(`DROP INDEX main.${name}`);
    }
    database.exec(`DROP TABLE main.${TABLE}`);
    createExactStore(database);
    requireStore(database);
  }).immediate();
  return true;
}

export function initializeVoiceDeliveryAuthorityStore(database) {
  const shadow = temporaryShadow(database);
  if (shadow) throw new Error(`voice delivery authority TEMP shadow: ${shadow.type}:${shadow.name}`);
  const existing = database.prepare(`SELECT type FROM main.sqlite_master
    WHERE lower(name)=lower(?)`).get(TABLE);
  if (!existing) {
    database.transaction(() => {
      createExactStore(database);
      requireStore(database);
    }).immediate();
  } else if (!voiceDeliveryAuthorityStoreStatus(database).available) {
    migrateExactEmptyPredecessor(database);
  }
  return requireStore(database);
}

function eventBody(row) {
  return {
    contract: row.contract,
    requestId: row.request_id,
    eventGeneration: Number(row.event_generation),
    eventKind: row.event_kind,
    conversationId: row.conversation_id,
    requester: row.requester,
    satellite: row.satellite,
    adapterInstanceSha256: row.adapter_instance_sha256,
    socketGeneration: Number(row.socket_generation),
    querySourceRef: row.query_source_ref,
    querySourceSha256: row.query_source_sha256,
    querySourceBodyJson: row.query_source_body_json,
    turnId: row.turn_id,
    offerOperationRef: row.offer_operation_ref,
    offerOperationSha256: row.offer_operation_sha256,
    offerPayloadJson: row.offer_payload_json,
    offerPayloadSha256: row.offer_payload_sha256,
    offerReplySha256: row.offer_reply_sha256,
    ackSourceRef: row.ack_source_ref,
    ackSourceSha256: row.ack_source_sha256,
    ackSourceBodyJson: row.ack_source_body_json,
    acceptanceDeliveryJson: row.acceptance_delivery_json,
    acceptanceDeliverySha256: row.acceptance_delivery_sha256,
    resultBodyJson: row.result_body_json,
    resultSha256: row.result_sha256,
    predecessorSha256: row.predecessor_sha256,
  };
}

function eventHash(row) {
  return sha256(canonicalJson(eventBody(row)));
}

function rawEvidence({ sourceRef, sourceSha256, sourceBodyJson }, eventType) {
  if (!SOURCE_REF.test(String(sourceRef || '')) || !SHA256.test(String(sourceSha256 || ''))
    || sha256(String(sourceBodyJson || '')) !== sourceSha256) {
    throw new Error(`${eventType} source hash/ref is ongeldig`);
  }
  const body = exactJson(sourceBodyJson, `${eventType} source body`);
  if (body.contract !== 'HomeAssistantVoiceEventEvidence/v1'
    || body.eventType !== eventType
    || !SHA256.test(String(body.adapterInstanceSha256 || ''))
    || !Number.isSafeInteger(Number(body.socketGeneration))
    || Number(body.socketGeneration) < 1
    || !SHA256.test(String(body.frameSha256 || ''))
    || typeof body.frameBase64 !== 'string') throw new Error(`${eventType} source evidence is ongeldig`);
  let frame;
  let event;
  try {
    frame = Buffer.from(body.frameBase64, 'base64');
    event = JSON.parse(frame.toString('utf8'));
  } catch { throw new Error(`${eventType} raw frame is ongeldig`); }
  if (frame.toString('base64') !== body.frameBase64
    || frame.length !== Number(body.frameByteLength)
    || sha256(frame) !== body.frameSha256
    || canonicalJson(event) !== canonicalJson(body.event)
    || event?.type !== 'event' || event?.event?.event_type !== eventType) {
    throw new Error(`${eventType} raw frame evidence is ongeldig`);
  }
  return Object.freeze({ body, event, data: event.event.data || {} });
}

function markAuthenticatedVoiceStoreSource(source, eventType) {
  if (!source || typeof source !== 'object' || source.eventType !== eventType
    || typeof source.isCurrent !== 'function' || source.isCurrent() !== true) return null;
  const evidence = rawEvidence(source, eventType);
  if (source.adapterInstanceSha256 !== evidence.body.adapterInstanceSha256
    || Number(source.socketGeneration) !== Number(evidence.body.socketGeneration)
    || canonicalJson(source.data || {}) !== canonicalJson(evidence.data)) return null;
  AUTHENTICATED_VOICE_STORE_SOURCES.set(source, Object.freeze({
    eventType,
    sourceRef: source.sourceRef,
    sourceSha256: source.sourceSha256,
    sourceBodyJson: source.sourceBodyJson,
  }));
  return source;
}

export async function verifiedVoiceStoreEventSource(capability, eventType) {
  if (!['eva_voice_query', 'eva_voice_reply_ack'].includes(eventType)) return null;
  const { verifiedAuthenticatedHaVoiceEvent } = await import(
    './integrations/homeassistant.js'
  );
  const source = verifiedAuthenticatedHaVoiceEvent(capability, eventType);
  return markAuthenticatedVoiceStoreSource(source, eventType);
}

export async function consumeVoiceStoreEventSource(capability, eventType, source) {
  const committed = source && typeof source === 'object'
    ? COMMITTED_VOICE_STORE_SOURCES.get(source) : null;
  if (!committed || committed.eventType !== eventType) return false;
  const { consumeAuthenticatedHaVoiceEvent } = await import(
    './integrations/homeassistant.js'
  );
  if (!consumeAuthenticatedHaVoiceEvent(capability, eventType, source)) return false;
  COMMITTED_VOICE_STORE_SOURCES.delete(source);
  return true;
}

function exactAuthenticatedStoreSource(input, eventType) {
  const source = input?.authenticatedSource;
  const issued = source && typeof source === 'object'
    ? AUTHENTICATED_VOICE_STORE_SOURCES.get(source) : null;
  if (!issued || issued.eventType !== eventType
    || source.eventType !== eventType
    || source.sourceRef !== issued.sourceRef
    || source.sourceSha256 !== issued.sourceSha256
    || source.sourceBodyJson !== issued.sourceBodyJson
    || typeof source.isCurrent !== 'function' || source.isCurrent() !== true) {
    throw new Error(`voice ${eventType} mist private authenticated source-authority`);
  }
  return source;
}

function queryFields(input) {
  const source = exactAuthenticatedStoreSource(input, 'eva_voice_query');
  const requestId = String(input?.requestId || '');
  const conversationId = String(input?.conversationId || '');
  const requester = String(input?.requester || '');
  const satellite = String(input?.satellite || '').toLowerCase();
  const evidence = rawEvidence(source, 'eva_voice_query');
  if (!REQUEST_ID.test(requestId) || !conversationId || conversationId.length > 240
    || !requester || requester.length > 160 || !SATELLITE.test(satellite)
    || evidence.data.id !== requestId || String(evidence.data.satellite || '').toLowerCase() !== satellite
    || source.adapterInstanceSha256 !== evidence.body.adapterInstanceSha256
    || Number(source.socketGeneration) !== Number(evidence.body.socketGeneration)) {
    throw new Error('voice query authority past niet exact bij de raw bron');
  }
  return {
    conversation_id: conversationId, requester, satellite,
    adapter_instance_sha256: evidence.body.adapterInstanceSha256,
    socket_generation: Number(evidence.body.socketGeneration),
    query_source_ref: source.sourceRef,
    query_source_sha256: source.sourceSha256,
    query_source_body_json: source.sourceBodyJson,
  };
}

function offerFields(input, prior) {
  const turnId = String(input?.turnId || '');
  const operationRef = String(input?.operationRef || '');
  const operationSha256 = String(input?.operationSha256 || '');
  const payloadSha256 = String(input?.payloadSha256 || '');
  const replySha256 = String(input?.replySha256 || '');
  const payload = exactJson(input?.payloadJson, 'voice offer payload');
  const expectedOperationSha256 = sha256(canonicalJson({
    contract: 'HomeAssistantVoiceReplyOperation/v1',
    requestId: prior.request_id,
    satellite: prior.satellite,
    adapterInstanceSha256: prior.adapter_instance_sha256,
    socketGeneration: Number(prior.socket_generation),
    querySourceRef: prior.query_source_ref,
    querySourceSha256: prior.query_source_sha256,
    payloadSha256,
    replySha256,
  }));
  if (!turnId || turnId.length > 320 || !OFFER_REF.test(operationRef)
    || !SHA256.test(operationSha256) || operationSha256 !== expectedOperationSha256
    || operationRef !== `home-assistant-voice-reply:${operationSha256}`
    || !SHA256.test(payloadSha256) || sha256(input.payloadJson) !== payloadSha256
    || !SHA256.test(replySha256) || sha256(String(payload.reply || '')) !== replySha256
    || String(payload.id || '') !== prior.request_id
    || String(payload.satellite || '').toLowerCase() !== prior.satellite
    || String(payload.conversation_id || '') !== prior.conversation_id) {
    throw new Error('voice offer intent is niet exact gebonden');
  }
  return {
    turn_id: turnId,
    offer_operation_ref: operationRef,
    offer_operation_sha256: operationSha256,
    offer_payload_json: input.payloadJson,
    offer_payload_sha256: payloadSha256,
    offer_reply_sha256: replySha256,
  };
}

function exactAcceptanceAuthority(authority, prior, source) {
  return Boolean(authority
    && authority.contract === 'HomeAssistantVoiceDeliveryAuthorityEvidence/v2'
    && authority.requestId === prior.request_id
    && authority.satellite === prior.satellite
    && authority.query?.sourceRef === prior.query_source_ref
    && authority.query?.sourceSha256 === prior.query_source_sha256
    && authority.query?.sourceBodyJson === prior.query_source_body_json
    && authority.offer?.operationRef === prior.offer_operation_ref
    && authority.offer?.operationSha256 === prior.offer_operation_sha256
    && authority.offer?.payloadJson === prior.offer_payload_json
    && authority.offer?.payloadSha256 === prior.offer_payload_sha256
    && authority.offer?.replySha256 === prior.offer_reply_sha256
    && authority.acknowledgement?.sourceRef === source.sourceRef
    && authority.acknowledgement?.sourceSha256 === source.sourceSha256
    && authority.acknowledgement?.sourceBodyJson === source.sourceBodyJson);
}

function acknowledgementFields(input, prior) {
  const source = exactAuthenticatedStoreSource(input, 'eva_voice_reply_ack');
  const evidence = rawEvidence(source, 'eva_voice_reply_ack');
  const data = evidence.data;
  if (evidence.body.adapterInstanceSha256 !== prior.adapter_instance_sha256
    || Number(evidence.body.socketGeneration) !== Number(prior.socket_generation)
    || data.id !== prior.request_id || ![true, 'true', 1, '1'].includes(data.accepted)
    || String(data.satellite || '').toLowerCase() !== prior.satellite
    || data.delivery_operation_ref !== prior.offer_operation_ref
    || data.payload_sha256 !== prior.offer_payload_sha256
    || data.reply_sha256 !== prior.offer_reply_sha256) {
    throw new Error('voice acknowledgement past niet exact bij query en offer');
  }
  const payload = exactJson(prior.offer_payload_json, 'stored voice offer payload');
  if (String(data.reply ?? '') !== String(payload.reply ?? '')
    || String(data.conversation_id || '') !== prior.conversation_id) {
    throw new Error('voice acknowledgement echo is gewijzigd');
  }
  const delivery = exactJson(input?.deliveryJson, 'voice acceptance delivery');
  if (!SHA256.test(String(input?.deliverySha256 || ''))
    || sha256(input.deliveryJson) !== input.deliverySha256
    || delivery.accepted !== true
    || delivery.transport !== 'home-assistant-conversation-response'
    || delivery.acknowledgement !== 'eva_voice_reply_ack'
    || String(delivery.satellite || '').toLowerCase() !== prior.satellite
    || Number(delivery.socketGeneration) !== Number(prior.socket_generation)) {
    throw new Error('voice acceptance delivery receipt is ongeldig');
  }
  const authority = delivery.authorityEvidence;
  if (!exactAcceptanceAuthority(authority, prior, source)) {
    throw new Error('voice acceptance authority evidence is niet exact');
  }
  return {
    ack_source_ref: source.sourceRef,
    ack_source_sha256: source.sourceSha256,
    ack_source_body_json: source.sourceBodyJson,
    acceptance_delivery_json: input.deliveryJson,
    acceptance_delivery_sha256: input.deliverySha256,
  };
}

function resultFields(input, prior) {
  const body = exactJson(input?.resultBodyJson, 'voice delivered result');
  if (!SHA256.test(String(input?.resultSha256 || ''))
    || sha256(input.resultBodyJson) !== input.resultSha256
    || body.contract !== 'DeliverableTurnResult/v1'
    || body.originChannel !== 'voice'
    || body.originConversationId !== prior.conversation_id
    || String(body.originMessageId || '') !== prior.request_id
    || String(body.turnId || '') !== prior.turn_id
    || String(body.sender || '') !== prior.requester
    || sha256(String(body.reply || '')) !== prior.offer_reply_sha256
    || body.replySha256 !== prior.offer_reply_sha256
    || input.deliveryJson !== prior.acceptance_delivery_json
    || input.deliverySha256 !== prior.acceptance_delivery_sha256) {
    throw new Error('voice delivered projection is niet exact gebonden');
  }
  return { result_body_json: input.resultBodyJson, result_sha256: input.resultSha256 };
}

function baseSuccessor(prior, generation, kind) {
  return {
    contract: VOICE_DELIVERY_AUTHORITY_CONTRACT,
    request_id: prior.request_id,
    event_generation: generation,
    event_kind: kind,
    conversation_id: prior.conversation_id,
    requester: prior.requester,
    satellite: prior.satellite,
    adapter_instance_sha256: prior.adapter_instance_sha256,
    socket_generation: Number(prior.socket_generation),
    query_source_ref: prior.query_source_ref,
    query_source_sha256: prior.query_source_sha256,
    query_source_body_json: prior.query_source_body_json,
    turn_id: prior.turn_id,
    offer_operation_ref: prior.offer_operation_ref,
    offer_operation_sha256: prior.offer_operation_sha256,
    offer_payload_json: prior.offer_payload_json,
    offer_payload_sha256: prior.offer_payload_sha256,
    offer_reply_sha256: prior.offer_reply_sha256,
    ack_source_ref: prior.ack_source_ref,
    ack_source_sha256: prior.ack_source_sha256,
    ack_source_body_json: prior.ack_source_body_json,
    acceptance_delivery_json: prior.acceptance_delivery_json,
    acceptance_delivery_sha256: prior.acceptance_delivery_sha256,
    result_body_json: null,
    result_sha256: null,
    predecessor_sha256: prior.event_sha256,
  };
}

const INSERT_FIELDS = Object.freeze(EXPECTED_COLUMNS.map(([name]) => name));

function insertEvent(database, row) {
  const complete = {
    ...row,
    event_sha256: eventHash(row),
    issued_ts: isoNow(),
  };
  database.prepare(`INSERT INTO main.${TABLE} (${INSERT_FIELDS.join(',')})
    VALUES (${INSERT_FIELDS.map(() => '?').join(',')})`).run(
    ...INSERT_FIELDS.map((field) => complete[field]),
  );
  return complete;
}

function eventAt(database, requestId, generation) {
  return database.prepare(`SELECT * FROM main.${TABLE}
    WHERE request_id=? AND event_generation=?`).get(requestId, generation) || null;
}

function appendExact(database, requestId, generation, build) {
  requireStore(database);
  const existing = eventAt(database, requestId, generation);
  const prior = generation === 1 ? null : eventAt(database, requestId, generation - 1);
  if (generation > 1 && !prior) throw new Error('voice delivery predecessor ontbreekt');
  const row = build(prior);
  const expectedHash = eventHash(row);
  if (existing) {
    if (existing.event_sha256 !== expectedHash || eventHash(existing) !== existing.event_sha256) {
      throw new Error('voice delivery generation is al anders gebonden');
    }
    return voiceDeliveryAuthorityRecord(database, requestId);
  }
  if (Number(database.prepare(`SELECT count(*) AS value FROM main.${TABLE}
    WHERE request_id=?`).get(requestId).value) >= MAX_EVENTS_PER_REQUEST) {
    throw new Error('voice delivery authority heeft te veel generaties');
  }
  if (Number(database.prepare(`SELECT count(*) AS value
    FROM main.${TABLE}`).get().value) >= MAX_STORE_EVENTS) {
    throw new Error('voice delivery authority store heeft haar rijgrens bereikt');
  }
  insertEvent(database, row);
  return voiceDeliveryAuthorityRecord(database, requestId);
}

export function recordVoiceQueryAuthority(database, input) {
  const requestId = String(input?.requestId || '');
  const recorded = appendExact(database, requestId, 1, () => ({
    contract: VOICE_DELIVERY_AUTHORITY_CONTRACT,
    request_id: requestId,
    event_generation: 1,
    event_kind: 'query',
    ...queryFields(input),
    turn_id: null,
    offer_operation_ref: null,
    offer_operation_sha256: null,
    offer_payload_json: null,
    offer_payload_sha256: null,
    offer_reply_sha256: null,
    ack_source_ref: null,
    ack_source_sha256: null,
    ack_source_body_json: null,
    acceptance_delivery_json: null,
    acceptance_delivery_sha256: null,
    result_body_json: null,
    result_sha256: null,
    predecessor_sha256: null,
  }));
  AUTHENTICATED_VOICE_STORE_SOURCES.delete(input?.authenticatedSource);
  COMMITTED_VOICE_STORE_SOURCES.set(input.authenticatedSource, Object.freeze({
    eventType: 'eva_voice_query', requestId,
  }));
  return recorded;
}

export function recordVoiceOfferIntent(database, input) {
  const requestId = String(input?.requestId || '');
  return appendExact(database, requestId, 2, (prior) => ({
    ...baseSuccessor(prior, 2, 'offer'),
    ...offerFields(input, prior),
  }));
}

export function recordVoiceAckReceipt(database, input) {
  const requestId = String(input?.requestId || '');
  const recorded = appendExact(database, requestId, 3, (prior) => ({
    ...baseSuccessor(prior, 3, 'acknowledgement'),
    ...acknowledgementFields(input, prior),
  }));
  AUTHENTICATED_VOICE_STORE_SOURCES.delete(input?.authenticatedSource);
  COMMITTED_VOICE_STORE_SOURCES.set(input.authenticatedSource, Object.freeze({
    eventType: 'eva_voice_reply_ack', requestId,
  }));
  return recorded;
}

export function recordVoiceDeliveredProjection(database, input) {
  const requestId = String(input?.requestId || '');
  return appendExact(database, requestId, 4, (prior) => ({
    ...baseSuccessor(prior, 4, 'delivered'),
    ...resultFields(input, prior),
  }));
}

export function voiceDeliveryAuthorityRecord(database, requestId) {
  try {
    requireStore(database);
    const id = String(requestId || '');
    if (!REQUEST_ID.test(id)) return null;
    const rows = database.prepare(`SELECT * FROM main.${TABLE}
      WHERE request_id=? ORDER BY event_generation`).all(id);
    if (!rows.length || rows.length > MAX_EVENTS_PER_REQUEST) return null;
    let predecessor = null;
    for (const [index, row] of rows.entries()) {
      if (Number(row.event_generation) !== index + 1
        || row.event_sha256 !== eventHash(row)
        || row.predecessor_sha256 !== predecessor) return null;
      predecessor = row.event_sha256;
    }
    const query = rows[0];
    const querySource = Object.freeze({
      eventType: 'eva_voice_query',
      data: Object.freeze({ ...(JSON.parse(query.query_source_body_json)?.event?.event?.data || {}) }),
      sourceRef: query.query_source_ref,
      sourceSha256: query.query_source_sha256,
      sourceBodyJson: query.query_source_body_json,
      adapterInstanceSha256: query.adapter_instance_sha256,
      socketGeneration: Number(query.socket_generation),
      isCurrent: () => true,
    });
    markAuthenticatedVoiceStoreSource(querySource, 'eva_voice_query');
    queryFields({
      requestId: query.request_id,
      conversationId: query.conversation_id,
      requester: query.requester,
      satellite: query.satellite,
      authenticatedSource: querySource,
    });
    AUTHENTICATED_VOICE_STORE_SOURCES.delete(querySource);
    if (rows[1]) offerFields({
      turnId: rows[1].turn_id,
      operationRef: rows[1].offer_operation_ref,
      operationSha256: rows[1].offer_operation_sha256,
      payloadJson: rows[1].offer_payload_json,
      payloadSha256: rows[1].offer_payload_sha256,
      replySha256: rows[1].offer_reply_sha256,
    }, rows[0]);
    if (rows[2]) {
      const ackBody = JSON.parse(rows[2].ack_source_body_json);
      const ackSource = Object.freeze({
        eventType: 'eva_voice_reply_ack',
        data: Object.freeze({ ...(ackBody?.event?.event?.data || {}) }),
        sourceRef: rows[2].ack_source_ref,
        sourceSha256: rows[2].ack_source_sha256,
        sourceBodyJson: rows[2].ack_source_body_json,
        adapterInstanceSha256: query.adapter_instance_sha256,
        socketGeneration: Number(query.socket_generation),
        isCurrent: () => true,
      });
      markAuthenticatedVoiceStoreSource(ackSource, 'eva_voice_reply_ack');
      acknowledgementFields({
        authenticatedSource: ackSource,
        deliveryJson: rows[2].acceptance_delivery_json,
        deliverySha256: rows[2].acceptance_delivery_sha256,
      }, rows[1]);
      AUTHENTICATED_VOICE_STORE_SOURCES.delete(ackSource);
    }
    if (rows[3]) resultFields({
      resultBodyJson: rows[3].result_body_json,
      resultSha256: rows[3].result_sha256,
      deliveryJson: rows[3].acceptance_delivery_json,
      deliverySha256: rows[3].acceptance_delivery_sha256,
    }, rows[2]);
    const latest = rows.at(-1);
    const stages = ['query', 'offered', 'acknowledged', 'delivered'];
    return Object.freeze({
      contract: 'VoiceDeliveryAuthorityRecord/v1',
      requestId: id,
      stage: stages[rows.length - 1],
      conversationId: query.conversation_id,
      requester: query.requester,
      satellite: query.satellite,
      adapterInstanceSha256: query.adapter_instance_sha256,
      socketGeneration: Number(query.socket_generation),
      query: Object.freeze({
        sourceRef: query.query_source_ref,
        sourceSha256: query.query_source_sha256,
        sourceBodyJson: query.query_source_body_json,
      }),
      offer: rows[1] ? Object.freeze({
        turnId: rows[1].turn_id,
        operationRef: rows[1].offer_operation_ref,
        operationSha256: rows[1].offer_operation_sha256,
        payloadJson: rows[1].offer_payload_json,
        payloadSha256: rows[1].offer_payload_sha256,
        replySha256: rows[1].offer_reply_sha256,
      }) : null,
      acknowledgement: rows[2] ? Object.freeze({
        sourceRef: rows[2].ack_source_ref,
        sourceSha256: rows[2].ack_source_sha256,
        sourceBodyJson: rows[2].ack_source_body_json,
        deliveryJson: rows[2].acceptance_delivery_json,
        deliverySha256: rows[2].acceptance_delivery_sha256,
      }) : null,
      result: rows[3] ? Object.freeze({
        bodyJson: rows[3].result_body_json,
        sha256: rows[3].result_sha256,
      }) : null,
      eventSha256: latest.event_sha256,
      events: Object.freeze(rows.map((row) => Object.freeze({ ...row }))),
    });
  } catch { return null; }
}

function acknowledgedVoiceDeliveryPageCursor(value) {
  const issuedTs = String(value?.issuedTs || '');
  const requestId = String(value?.requestId || '');
  return issuedTs && issuedTs.length <= 40 && REQUEST_ID.test(requestId)
    ? Object.freeze({ issuedTs, requestId }) : null;
}

export function pendingAcknowledgedVoiceDeliveryPage(database, {
  limit = 20, after = null,
} = {}) {
  requireStore(database);
  const bounded = Math.max(1, Math.min(100, Number(limit) || 20));
  const cursor = after == null ? null : acknowledgedVoiceDeliveryPageCursor(after);
  if (after != null && !cursor) throw new TypeError('voice draincursor is ongeldig');
  const rows = database.prepare(`SELECT request_id,max(issued_ts) AS issued_ts
    FROM main.${TABLE}
    GROUP BY request_id HAVING max(event_generation)=3
      AND (? IS NULL OR max(issued_ts)>?
        OR (max(issued_ts)=? AND request_id>?))
    ORDER BY max(issued_ts),request_id LIMIT ?`).all(
    cursor?.issuedTs ?? null,
    cursor?.issuedTs ?? null,
    cursor?.issuedTs ?? null,
    cursor?.requestId ?? '',
    bounded + 1,
  );
  const selected = rows.slice(0, bounded);
  const records = selected
    .map(({ request_id: requestId }) => voiceDeliveryAuthorityRecord(database, requestId))
    .filter((record) => record?.stage === 'acknowledged');
  for (const record of records) {
    const row = selected.find(({ request_id: requestId }) => requestId === record.requestId);
    PENDING_ACKNOWLEDGED_VOICE_RECORDS.set(record, Object.freeze({
      database, requestId: record.requestId, eventSha256: record.eventSha256,
      issuedTs: String(row?.issued_ts || ''),
    }));
  }
  const last = selected.at(-1);
  return Object.freeze({
    records: Object.freeze(records),
    nextCursor: last ? Object.freeze({
      issuedTs: String(last.issued_ts), requestId: String(last.request_id),
    }) : cursor,
    hasMore: rows.length > bounded,
  });
}

export function pendingAcknowledgedVoiceDeliveries(database, limit = 20) {
  return pendingAcknowledgedVoiceDeliveryPage(database, { limit }).records;
}

function exactVoiceRecoveryResult(input, prior) {
  const fields = resultFields(input, prior);
  const body = exactJson(fields.result_body_json, 'voice recovery result');
  if (!Number.isSafeInteger(Number(body.goalEpisodeId)) || Number(body.goalEpisodeId) < 1
    || !Number.isSafeInteger(Number(body.workItemId)) || Number(body.workItemId) < 1
    || !SHA256.test(String(body.finalDeliveryBindingSha256 || ''))
    || body.syntheticTest === true || body.replayed !== true) {
    throw new Error('voice recovery result mist duurzame finale binding');
  }
  return Object.freeze({
    requestId: prior.request_id,
    conversationId: prior.conversation_id,
    requester: prior.requester,
    turnId: prior.turn_id,
    replySha256: prior.offer_reply_sha256,
    episodeId: Number(body.goalEpisodeId),
    workItemId: Number(body.workItemId),
    finalDeliveryBindingSha256: body.finalDeliveryBindingSha256,
    resultBodyJson: fields.result_body_json,
    resultSha256: fields.result_sha256,
    deliveryJson: input.deliveryJson,
    deliverySha256: input.deliverySha256,
  });
}

/**
 * Mint uitsluitend vanuit de object-identiteit die de begrensde pending-reader
 * zojuist voor een actuele, volledig geattesteerde gen3-keten teruggaf.
 * De terugwaarde zelf bevat geen serialiseerbare bevoegdheid.
 */
export async function verifiedAcknowledgedVoiceFinalDeliveryRecovery(
  database, authority, privateRequest,
) {
  const issued = authority && typeof authority === 'object'
    ? PENDING_ACKNOWLEDGED_VOICE_RECORDS.get(authority) : null;
  if (!issued || issued.database !== database
    || issued.requestId !== authority.requestId
    || issued.eventSha256 !== authority.eventSha256
    || authority.stage !== 'acknowledged') return null;
  const current = voiceDeliveryAuthorityRecord(database, authority.requestId);
  if (!current || current.stage !== 'acknowledged'
    || current.eventSha256 !== issued.eventSha256
    || current.query.sourceRef !== authority.query.sourceRef
    || current.query.sourceSha256 !== authority.query.sourceSha256
    || current.query.sourceBodyJson !== authority.query.sourceBodyJson
    || current.offer.operationRef !== authority.offer.operationRef
    || current.offer.operationSha256 !== authority.offer.operationSha256
    || current.offer.payloadJson !== authority.offer.payloadJson
    || current.offer.payloadSha256 !== authority.offer.payloadSha256
    || current.offer.replySha256 !== authority.offer.replySha256
    || current.acknowledgement.sourceRef !== authority.acknowledgement.sourceRef
    || current.acknowledgement.sourceSha256 !== authority.acknowledgement.sourceSha256
    || current.acknowledgement.sourceBodyJson !== authority.acknowledgement.sourceBodyJson
    || current.acknowledgement.deliveryJson !== authority.acknowledgement.deliveryJson
    || current.acknowledgement.deliverySha256
      !== authority.acknowledgement.deliverySha256) return null;
  const { verifiedVoiceFinalDeliveryRecoveryRequest } = await import('./mind/agent.js');
  const input = verifiedVoiceFinalDeliveryRecoveryRequest(privateRequest);
  let exact;
  try { exact = exactVoiceRecoveryResult(input, current.events[2]); } catch { return null; }
  const proof = Object.freeze({});
  VOICE_FINAL_DELIVERY_RECOVERY_PROOFS.set(proof, Object.freeze({
    database, eventSha256: current.eventSha256, ...exact,
  }));
  PENDING_ACKNOWLEDGED_VOICE_RECORDS.delete(authority);
  return proof;
}

export function verifiedVoiceFinalDeliveryRecoveryProof(database, proof, expected = {}) {
  const issued = proof && typeof proof === 'object'
    ? VOICE_FINAL_DELIVERY_RECOVERY_PROOFS.get(proof) : null;
  if (!issued || issued.database !== database) return false;
  const current = voiceDeliveryAuthorityRecord(database, issued.requestId);
  if (!current || current.stage !== 'acknowledged'
    || current.eventSha256 !== issued.eventSha256) return false;
  const expectedDeliveryJson = canonicalJson(expected.delivery || null);
  return [
    String(expected.requestId || '') === issued.requestId,
    String(expected.conversationId || '') === issued.conversationId,
    String(expected.requester || '') === issued.requester,
    String(expected.turnId || '') === issued.turnId,
    String(expected.replySha256 || '') === issued.replySha256,
    Number(expected.episodeId) === issued.episodeId,
    Number(expected.workItemId) === issued.workItemId,
    String(expected.finalDeliveryBindingSha256 || '')
      === issued.finalDeliveryBindingSha256,
    String(expected.resultSha256 || '') === issued.resultSha256,
    String(expected.deliverySha256 || '') === issued.deliverySha256,
    expectedDeliveryJson === issued.deliveryJson,
    sha256(expectedDeliveryJson) === issued.deliverySha256,
  ].every(Boolean);
}

export function consumeVoiceFinalDeliveryRecoveryProof(database, proof) {
  const issued = proof && typeof proof === 'object'
    ? VOICE_FINAL_DELIVERY_RECOVERY_PROOFS.get(proof) : null;
  if (!issued || issued.database !== database) return false;
  VOICE_FINAL_DELIVERY_RECOVERY_PROOFS.delete(proof);
  return true;
}
