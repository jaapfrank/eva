import { createHash } from 'node:crypto';

import {
  EXECUTION_RECORD_CONTRACT_V2,
  EXECUTION_VERIFICATION_VERIFIED,
  executionLedgerStatus,
} from './executionledger.js';
import { canonicalJson } from './util.js';

export const TURN_UNDERSTANDING_AUTHORITY_CONTRACT =
  'TurnUnderstandingExecutionAuthority/v1';
export const TURN_UNDERSTANDING_AUTHORITY_TABLE =
  'eva_turn_understanding_authorities_v1';

const TABLE = TURN_UNDERSTANDING_AUTHORITY_TABLE;
const SHA256 = /^[a-f0-9]{64}$/u;
const SOURCE_BODY_MAX_BYTES = 64 * 1024;
const CHANNELS = new Set(['telegram', 'voice', 'web']);
const SOURCE_KIND_BY_CHANNEL = Object.freeze({
  telegram: 'telegram_ingress',
  voice: 'voice_ingress',
  web: 'http_ingress',
});
const ARG_FIELDS = Object.freeze([
  'channel', 'conversationId', 'messageId', 'originalText', 'requester',
  'sessionRef', 'supersessionScope', 'supersessionToken', 'syntheticTest',
]);

export const TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL = `CREATE TABLE ${TABLE} (
  contract TEXT NOT NULL,
  execution_key TEXT NOT NULL,
  execution_seq INTEGER NOT NULL,
  intent_record_sha256 TEXT NOT NULL,
  channel TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  source_body_json TEXT,
  source_generation INTEGER NOT NULL,
  authority_sha256 TEXT NOT NULL,
  issued_ts TEXT NOT NULL,
  PRIMARY KEY(execution_key,source_generation),
  UNIQUE(execution_seq,source_generation),
  UNIQUE(intent_record_sha256,source_generation),
  UNIQUE(source_ref,source_generation),
  UNIQUE(authority_sha256),
  UNIQUE(channel,conversation_id,message_id,source_generation),
  CHECK(contract='TurnUnderstandingExecutionAuthority/v1'),
  CHECK(channel IN ('telegram','voice','web')),
  CHECK((channel='web' AND source_kind='http_ingress')
    OR (channel='telegram' AND source_kind='telegram_ingress')
    OR (channel='voice' AND source_kind='voice_ingress')),
  CHECK(typeof(execution_seq)='integer' AND execution_seq>=1),
  CHECK(typeof(source_generation)='integer'
    AND source_generation>=1 AND source_generation<=64),
  CHECK(length(intent_record_sha256)=64 AND lower(intent_record_sha256)=intent_record_sha256
    AND intent_record_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(source_sha256)=64 AND lower(source_sha256)=source_sha256
    AND source_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK((channel='web' AND source_body_json IS NULL)
    OR (channel IN ('telegram','voice') AND typeof(source_body_json)='text'
      AND length(CAST(source_body_json AS BLOB))>=2
      AND length(CAST(source_body_json AS BLOB))<=65536
      AND json_valid(source_body_json) AND json_type(source_body_json)='object')),
  CHECK(length(authority_sha256)=64 AND lower(authority_sha256)=authority_sha256
    AND authority_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(execution_key)=83),
  CHECK(length(conversation_id)>=1 AND length(conversation_id)<=240),
  CHECK(length(message_id)>=1 AND length(message_id)<=160),
  CHECK(length(source_ref)>=1 AND length(source_ref)<=320),
  CHECK(length(issued_ts)>=20 AND length(issued_ts)<=40)
)`;

export const TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL = Object.freeze({
  eva_turn_understanding_authorities_v1_no_update:
    `CREATE TRIGGER eva_turn_understanding_authorities_v1_no_update
    BEFORE UPDATE ON ${TABLE}
    BEGIN SELECT RAISE(ABORT,'turn understanding authority is append-only'); END`,
  eva_turn_understanding_authorities_v1_no_delete:
    `CREATE TRIGGER eva_turn_understanding_authorities_v1_no_delete
    BEFORE DELETE ON ${TABLE}
    BEGIN SELECT RAISE(ABORT,'turn understanding authority is append-only'); END`,
  eva_turn_understanding_authorities_v1_no_replace:
    `CREATE TRIGGER eva_turn_understanding_authorities_v1_no_replace
    BEFORE INSERT ON ${TABLE}
    WHEN EXISTS (SELECT 1 FROM main.${TABLE} existing
      WHERE (existing.execution_key=NEW.execution_key
          AND existing.source_generation=NEW.source_generation)
        OR (existing.execution_seq=NEW.execution_seq
          AND existing.source_generation=NEW.source_generation)
        OR (existing.intent_record_sha256=NEW.intent_record_sha256
          AND existing.source_generation=NEW.source_generation)
        OR (existing.source_ref=NEW.source_ref
          AND existing.source_generation=NEW.source_generation)
        OR existing.authority_sha256=NEW.authority_sha256
        OR (existing.channel=NEW.channel
          AND existing.conversation_id=NEW.conversation_id
          AND existing.message_id=NEW.message_id
          AND existing.source_generation=NEW.source_generation))
    BEGIN SELECT RAISE(ABORT,'turn understanding authority replacement is forbidden'); END`,
  eva_turn_understanding_authorities_v1_valid_successor:
    `CREATE TRIGGER eva_turn_understanding_authorities_v1_valid_successor
    BEFORE INSERT ON ${TABLE}
    WHEN EXISTS (SELECT 1 FROM main.${TABLE} existing
      WHERE existing.execution_key=NEW.execution_key)
    BEGIN
      SELECT CASE WHEN NEW.source_generation<=(SELECT max(existing.source_generation)
        FROM main.${TABLE} existing WHERE existing.execution_key=NEW.execution_key)
      THEN RAISE(ABORT,'turn understanding authority generation is not newer') END;
      SELECT CASE WHEN EXISTS (SELECT 1 FROM main.${TABLE} existing
        WHERE existing.execution_key=NEW.execution_key
          AND (existing.execution_seq<>NEW.execution_seq
            OR existing.intent_record_sha256<>NEW.intent_record_sha256
            OR existing.channel<>NEW.channel
            OR existing.conversation_id<>NEW.conversation_id
            OR existing.message_id<>NEW.message_id
            OR existing.source_kind<>NEW.source_kind
            OR existing.source_ref<>NEW.source_ref
            OR existing.source_sha256<>NEW.source_sha256
            OR existing.source_body_json IS NOT NEW.source_body_json))
      THEN RAISE(ABORT,'turn understanding authority successor changes its source') END;
    END`,
  eva_turn_understanding_authorities_v1_valid_execution:
    `CREATE TRIGGER eva_turn_understanding_authorities_v1_valid_execution
    BEFORE INSERT ON ${TABLE}
    WHEN NOT EXISTS (SELECT 1 FROM main.turn_tool_executions execution
      WHERE execution.execution_key=NEW.execution_key
        AND execution.execution_seq=NEW.execution_seq
        AND execution.intent_record_sha256=NEW.intent_record_sha256
        AND execution.execution_contract='ExecutionRecord/v2'
        AND execution.execution_verification='verified'
        AND execution.tool='@turn/understanding'
        AND execution.status='started'
        AND execution.finished_ts IS NULL
        AND execution.output IS NULL
        AND execution.output_sha256 IS NULL
        AND execution.material_sha256 IS NULL
        AND execution.completion_record_sha256 IS NULL
        AND json_valid(execution.args)
        AND json_extract(execution.args,'$.channel')=NEW.channel
        AND CAST(json_extract(execution.args,'$.conversationId') AS TEXT)=NEW.conversation_id
        AND CAST(json_extract(execution.args,'$.messageId') AS TEXT)=NEW.message_id
        AND ((NEW.channel='web' AND NEW.source_kind='http_ingress')
          OR (NEW.channel='telegram' AND NEW.source_kind='telegram_ingress')
          OR (NEW.channel='voice' AND NEW.source_kind='voice_ingress')))
    BEGIN SELECT RAISE(ABORT,'turn understanding authority requires exact started V2 execution'); END`,
});

// Exact het eenmalig intern bevroren predecessor-schema, vóór source_body_json.
// Dit is geen generieke schemaherkenning: iedere andere DDL/trigger/indexvorm
// blijft fail-closed. Native predecessor-rijen zijn niet migreerbaar omdat hun
// canonieke bronbytes destijds niet werden bewaard.
const PREDECESSOR_TABLE_SQL = TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL
  .replace('  source_body_json TEXT,\n', '')
  .replace(`  CHECK((channel='web' AND source_body_json IS NULL)
    OR (channel IN ('telegram','voice') AND typeof(source_body_json)='text'
      AND length(CAST(source_body_json AS BLOB))>=2
      AND length(CAST(source_body_json AS BLOB))<=65536
      AND json_valid(source_body_json) AND json_type(source_body_json)='object')),\n`, '');
const PREDECESSOR_TRIGGER_SQL = Object.freeze({
  ...TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL,
  eva_turn_understanding_authorities_v1_valid_successor:
    TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL
      .eva_turn_understanding_authorities_v1_valid_successor
      .replace(`
            OR existing.source_sha256<>NEW.source_sha256
            OR existing.source_body_json IS NOT NEW.source_body_json))`, `
            OR existing.source_sha256<>NEW.source_sha256))`),
});

const EXPECTED_COLUMNS = Object.freeze([
  ['contract', 'TEXT', 1, 0], ['execution_key', 'TEXT', 1, 1],
  ['execution_seq', 'INTEGER', 1, 0], ['intent_record_sha256', 'TEXT', 1, 0],
  ['channel', 'TEXT', 1, 0], ['conversation_id', 'TEXT', 1, 0],
  ['message_id', 'TEXT', 1, 0], ['source_kind', 'TEXT', 1, 0],
  ['source_ref', 'TEXT', 1, 0], ['source_sha256', 'TEXT', 1, 0],
  ['source_body_json', 'TEXT', 0, 0],
  ['source_generation', 'INTEGER', 1, 2], ['authority_sha256', 'TEXT', 1, 0],
  ['issued_ts', 'TEXT', 1, 0],
]);
const PREDECESSOR_COLUMNS = Object.freeze(EXPECTED_COLUMNS
  .filter(([name]) => name !== 'source_body_json')
  .map(([name, type, notnull, pk]) => [
    name, type, notnull,
    name === 'source_generation' ? 2 : pk,
  ]));

function normalizeSql(value) {
  const source = String(value || '');
  const literals = [];
  let syntax = '';
  for (let index = 0; index < source.length;) {
    if (source[index] !== "'") {
      syntax += source[index];
      index += 1;
      continue;
    }
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
  const normalized = syntax.replace(/\bmain\./gu, '')
    .replace(/;\s*$/u, '').replace(/\s+/gu, ' ')
    .replace(/\s*([(),=])\s*/gu, '$1').trim().toLowerCase();
  return normalized.replace(/\u{e000}(\d+)\u{e001}/gu, (
    _match, literal,
  ) => literals[Number(literal)]);
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function temporaryShadow(database) {
  return database.prepare(`SELECT type,name,tbl_name FROM sqlite_temp_master
    WHERE lower(name) GLOB 'eva_turn_understanding_authorit*'
      OR lower(tbl_name) GLOB 'eva_turn_understanding_authorit*'
    ORDER BY type,name LIMIT 1`).get() || null;
}

function exactColumns(database, expected = EXPECTED_COLUMNS) {
  const columns = database.prepare(`PRAGMA main.table_info('${TABLE}')`).all()
    .map((column) => [
      String(column.name), String(column.type).toUpperCase(),
      Number(column.notnull), Number(column.pk),
    ]);
  return canonicalJson(columns) === canonicalJson(expected);
}

function exactIndexes(database, generationCid = 11, authorityCid = 12) {
  const indexes = database.prepare(`PRAGMA main.index_list('${TABLE}')`).all();
  const prefix = `sqlite_autoindex_${TABLE}_`;
  const expected = new Map([
    [`${prefix}1`, [5, 1, 'pk', [
      [0, 1, 'execution_key', 0, 'BINARY', 1],
      [1, generationCid, 'source_generation', 0, 'BINARY', 1],
      [2, -1, null, 0, 'BINARY', 0],
    ]]],
    [`${prefix}2`, [4, 1, 'u', [
      [0, 2, 'execution_seq', 0, 'BINARY', 1],
      [1, generationCid, 'source_generation', 0, 'BINARY', 1],
      [2, -1, null, 0, 'BINARY', 0],
    ]]],
    [`${prefix}3`, [3, 1, 'u', [
      [0, 3, 'intent_record_sha256', 0, 'BINARY', 1],
      [1, generationCid, 'source_generation', 0, 'BINARY', 1],
      [2, -1, null, 0, 'BINARY', 0],
    ]]],
    [`${prefix}4`, [2, 1, 'u', [
      [0, 8, 'source_ref', 0, 'BINARY', 1],
      [1, generationCid, 'source_generation', 0, 'BINARY', 1],
      [2, -1, null, 0, 'BINARY', 0],
    ]]],
    [`${prefix}5`, [1, 1, 'u', [
      [0, authorityCid, 'authority_sha256', 0, 'BINARY', 1],
      [1, -1, null, 0, 'BINARY', 0],
    ]]],
    [`${prefix}6`, [0, 1, 'u', [
      [0, 4, 'channel', 0, 'BINARY', 1],
      [1, 5, 'conversation_id', 0, 'BINARY', 1],
      [2, 6, 'message_id', 0, 'BINARY', 1],
      [3, generationCid, 'source_generation', 0, 'BINARY', 1],
      [4, -1, null, 0, 'BINARY', 0],
    ]]],
  ]);
  if (indexes.length !== expected.size) return false;
  return indexes.every((index) => {
    const signature = expected.get(String(index.name));
    if (!signature || Number(index.seq) !== signature[0]
      || Number(index.unique) !== signature[1]
      || String(index.origin) !== signature[2]
      || Number(index.partial) !== 0) return false;
    const xinfo = database.prepare(
      `PRAGMA main.index_xinfo('${String(index.name).replaceAll("'", "''")}')`,
    ).all().map((entry) => [
      Number(entry.seqno), Number(entry.cid),
      entry.name == null ? null : String(entry.name),
      Number(entry.desc), String(entry.coll || ''), Number(entry.key),
    ]);
    return canonicalJson(xinfo) === canonicalJson(signature[3]);
  });
}

function exactTriggers(database, expectedSql = TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL) {
  const rows = database.prepare(`SELECT name,sql FROM main.sqlite_master
    WHERE type='trigger' AND tbl_name=? ORDER BY name`).all(TABLE);
  const expected = Object.keys(expectedSql).sort();
  return canonicalJson(rows.map((row) => String(row.name))) === canonicalJson(expected)
    && rows.every((row) => normalizeSql(row.sql) === normalizeSql(
      expectedSql[row.name],
    ));
}

function exactPredecessor(database) {
  try {
    const shadow = temporaryShadow(database);
    if (shadow) return false;
    const sql = database.prepare(`SELECT sql FROM main.sqlite_master
      WHERE type='table' AND name=?`).get(TABLE)?.sql;
    return normalizeSql(sql) === normalizeSql(PREDECESSOR_TABLE_SQL)
      && exactColumns(database, PREDECESSOR_COLUMNS)
      && exactIndexes(database, 10, 11)
      && exactTriggers(database, PREDECESSOR_TRIGGER_SQL)
      && exactNamespace(database);
  } catch { return false; }
}

function migrateExactPredecessor(database) {
  if (!exactPredecessor(database)) return false;
  const nativeRows = Number(database.prepare(`SELECT count(*) AS value
    FROM main.${TABLE} WHERE channel IN ('telegram','voice')`).get().value);
  if (nativeRows !== 0) {
    throw new Error('exact predecessor bevat native authority zonder reconstructeerbare bronbytes');
  }
  const predecessor = `${TABLE}_exact_predecessor`;
  database.transaction(() => {
    for (const name of Object.keys(PREDECESSOR_TRIGGER_SQL)) {
      database.exec(`DROP TRIGGER main.${name}`);
    }
    database.exec(`ALTER TABLE main.${TABLE} RENAME TO ${predecessor}`);
    database.exec(TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL);
    database.exec(`INSERT INTO main.${TABLE} (
      contract,execution_key,execution_seq,intent_record_sha256,channel,
      conversation_id,message_id,source_kind,source_ref,source_sha256,
      source_body_json,source_generation,authority_sha256,issued_ts
    ) SELECT contract,execution_key,execution_seq,intent_record_sha256,channel,
      conversation_id,message_id,source_kind,source_ref,source_sha256,
      NULL,source_generation,authority_sha256,issued_ts
      FROM main.${predecessor}`);
    database.exec(`DROP TABLE main.${predecessor}`);
    for (const sql of Object.values(TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL)) {
      database.exec(sql);
    }
    const migrated = database.prepare(`SELECT DISTINCT execution_key
      FROM main.${TABLE}`).all();
    for (const { execution_key: executionKey } of migrated) {
      const execution = database.prepare(`SELECT * FROM main.turn_tool_executions
        WHERE execution_key=?`).get(executionKey);
      if (!turnUnderstandingAuthorityRecord(database, execution)) {
        throw new Error('exact predecessor bevat ongeldig web-authoritybewijs');
      }
    }
    requireTurnUnderstandingAuthorityStore(database);
  }).immediate();
  return true;
}

function exactNamespace(database) {
  const expected = new Map([[TABLE, ['table', TABLE]]]);
  for (let ordinal = 1; ordinal <= 6; ordinal += 1) {
    expected.set(`sqlite_autoindex_${TABLE}_${ordinal}`, ['index', TABLE]);
  }
  for (const name of Object.keys(TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL)) {
    expected.set(name, ['trigger', TABLE]);
  }
  const rows = database.prepare(`SELECT type,name,tbl_name FROM main.sqlite_master
    WHERE lower(name) GLOB 'eva_turn_understanding_authorit*'
      OR lower(tbl_name) GLOB 'eva_turn_understanding_authorit*'
    ORDER BY type,name`).all();
  if (rows.length !== expected.size) return false;
  return rows.every((row) => {
    const signature = expected.get(String(row.name));
    return signature && String(row.type) === signature[0]
      && String(row.tbl_name) === signature[1];
  });
}

export function turnUnderstandingAuthorityStoreStatus(database) {
  try {
    const ledger = executionLedgerStatus(database);
    if (!ledger.available) return Object.freeze({
      contract: 'TurnUnderstandingAuthorityStoreStatus/v1', available: false,
      reason: `execution_ledger_unavailable:${ledger.reason}`,
    });
    const shadow = temporaryShadow(database);
    if (shadow) throw new Error(`TEMP shadow: ${shadow.type}:${shadow.name}`);
    const sql = database.prepare(`SELECT sql FROM main.sqlite_master
      WHERE type='table' AND name=?`).get(TABLE)?.sql;
    if (normalizeSql(sql) !== normalizeSql(TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL)) {
      throw new Error('table DDL is inexact');
    }
    if (!exactColumns(database)) throw new Error('columns are inexact');
    if (!exactIndexes(database)) throw new Error('indexes are inexact');
    if (!exactTriggers(database)) throw new Error('triggers are inexact');
    if (!exactNamespace(database)) throw new Error('namespace is inexact');
    return Object.freeze({
      contract: 'TurnUnderstandingAuthorityStoreStatus/v1', available: true,
    });
  } catch (error) {
    return Object.freeze({
      contract: 'TurnUnderstandingAuthorityStoreStatus/v1', available: false,
      reason: String(error?.message || error || 'unknown'),
    });
  }
}

export function requireTurnUnderstandingAuthorityStore(database) {
  const status = turnUnderstandingAuthorityStoreStatus(database);
  if (!status.available) {
    throw new Error(`turn understanding authority store unavailable: ${
      'reason' in status ? status.reason : 'unavailable'
    }`);
  }
  return status;
}

export function initializeTurnUnderstandingAuthorityStore(database) {
  const existing = database.prepare(`SELECT type FROM main.sqlite_master
    WHERE name=?`).get(TABLE);
  if (!existing) {
    database.transaction(() => {
      database.exec(TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL);
      for (const sql of Object.values(TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL)) {
        database.exec(sql);
      }
      requireTurnUnderstandingAuthorityStore(database);
    }).immediate();
  } else if (!turnUnderstandingAuthorityStoreStatus(database).available) {
    migrateExactPredecessor(database);
  }
  return requireTurnUnderstandingAuthorityStore(database);
}

function parsedUnderstandingArgs(row) {
  let args;
  try { args = JSON.parse(String(row?.args || '')); } catch { return null; }
  if (!args || typeof args !== 'object' || Array.isArray(args)
    || canonicalJson(Object.keys(args).sort()) !== canonicalJson(ARG_FIELDS)
    || !CHANNELS.has(args.channel)
    || typeof args.conversationId !== 'string' || !args.conversationId
    || typeof args.messageId !== 'string' && typeof args.messageId !== 'number'
    || !String(args.messageId)
    || typeof args.requester !== 'string' || typeof args.originalText !== 'string'
    || typeof args.sessionRef !== 'string'
    || typeof args.syntheticTest !== 'boolean'
    || args.supersessionScope !== null && typeof args.supersessionScope !== 'string'
    || args.supersessionToken !== null && typeof args.supersessionToken !== 'string') return null;
  return args;
}

function exactTelegramSourceBody(body, authority) {
  return body.contract === 'TelegramArchivedUserEnvelope/v1'
    && String(body.chatId || '') === String(authority.conversation_id).replace(/^telegram:/u, '')
    && String(body.messageId || '') === String(authority.message_id)
    && body.role === 'user' && body.deleted === false
    && body.meta?.inbound === true
    && /^[1-9][0-9]*$/u.test(String(body.meta?.fromTelegramId || ''));
}

function exactVoiceSourceBody(body, authority) {
  if (body.contract !== 'HomeAssistantVoiceEventEvidence/v1'
    || body.eventType !== 'eva_voice_query'
    || !SHA256.test(String(body.adapterInstanceSha256 || ''))
    || !Number.isSafeInteger(Number(body.socketGeneration))
    || Number(body.socketGeneration) < 1
    || !SHA256.test(String(body.frameSha256 || ''))
    || typeof body.frameBase64 !== 'string') return false;
  let frame;
  let event;
  try {
    frame = Buffer.from(body.frameBase64, 'base64');
    if (frame.toString('base64') !== body.frameBase64
      || frame.length !== Number(body.frameByteLength)
      || sha256(frame) !== body.frameSha256) return false;
    event = JSON.parse(frame.toString('utf8'));
  } catch { return false; }
  return canonicalJson(event) === canonicalJson(body.event)
    && event?.type === 'event'
    && event?.event?.event_type === 'eva_voice_query'
    && String(event?.event?.data?.id || '') === String(authority.message_id);
}

function exactSourceBody(authority) {
  const channel = String(authority?.channel || '');
  const bodyJson = authority?.source_body_json;
  if (channel === 'web') return bodyJson === null;
  if (!['telegram', 'voice'].includes(channel) || typeof bodyJson !== 'string'
    || Buffer.byteLength(bodyJson, 'utf8') < 2
    || Buffer.byteLength(bodyJson, 'utf8') > SOURCE_BODY_MAX_BYTES
    || sha256(bodyJson) !== authority.source_sha256) return false;
  let body;
  try { body = JSON.parse(bodyJson); } catch { return false; }
  if (!body || typeof body !== 'object' || Array.isArray(body)
    || canonicalJson(body) !== bodyJson) return false;
  return channel === 'telegram'
    ? exactTelegramSourceBody(body, authority) : exactVoiceSourceBody(body, authority);
}

export function turnUnderstandingAuthorityBody(authority, execution) {
  return {
    contract: authority.contract,
    executionKey: authority.execution_key,
    executionSeq: Number(authority.execution_seq),
    intentRecordSha256: authority.intent_record_sha256,
    channel: authority.channel,
    conversationId: authority.conversation_id,
    messageId: authority.message_id,
    sourceKind: authority.source_kind,
    sourceRef: authority.source_ref,
    sourceSha256: authority.source_sha256,
    ...(authority.source_body_json == null ? {} : {
      sourceBodyJson: authority.source_body_json,
    }),
    sourceGeneration: Number(authority.source_generation),
    issuedTs: authority.issued_ts,
    turnId: execution.turn_id,
    tool: execution.tool,
    args: execution.args,
  };
}

/**
 * Leesvalidator; mint of muteert nooit authority. `null` betekent dat de rij
 * geen actuele, store-owned @turn-authority heeft.
 */
export function turnUnderstandingAuthorityRecord(database, execution) {
  try {
    requireTurnUnderstandingAuthorityStore(database);
    const args = parsedUnderstandingArgs(execution);
    if (!args || execution?.tool !== '@turn/understanding'
      || execution.execution_contract !== EXECUTION_RECORD_CONTRACT_V2
      || execution.execution_verification !== EXECUTION_VERIFICATION_VERIFIED
      || !Number.isSafeInteger(Number(execution.execution_seq))
      || Number(execution.execution_seq) < 1
      || !SHA256.test(String(execution.intent_record_sha256 || ''))) return null;
    const messageId = String(args.messageId);
    const expectedKey = `turn-understanding:${sha256(canonicalJson({
      channel: args.channel, conversationId: args.conversationId, messageId,
    }))}`;
    if (execution.execution_key !== expectedKey
      || execution.turn_id !== `ingress:${args.channel}:${args.conversationId}:${messageId}`) {
      return null;
    }
    const authorities = database.prepare(`SELECT * FROM main.${TABLE}
      WHERE execution_key=? ORDER BY source_generation`).all(execution.execution_key);
    if (!authorities.length || authorities.length > 64) return null;
    let priorGeneration = 0;
    let sourceRef = null;
    let sourceSha256 = null;
    let sourceBodyJson;
    for (const authority of authorities) {
      const generation = Number(authority.source_generation);
      if (authority.contract !== TURN_UNDERSTANDING_AUTHORITY_CONTRACT
        || authority.execution_key !== execution.execution_key
        || Number(authority.execution_seq) !== Number(execution.execution_seq)
        || authority.intent_record_sha256 !== execution.intent_record_sha256
        || authority.channel !== args.channel
        || authority.conversation_id !== args.conversationId
        || authority.message_id !== messageId
        || authority.source_kind !== SOURCE_KIND_BY_CHANNEL[args.channel]
        || typeof authority.source_ref !== 'string' || !authority.source_ref
        || !SHA256.test(String(authority.source_sha256 || ''))
        || !exactSourceBody(authority)
        || !Number.isSafeInteger(generation) || generation <= priorGeneration
        || generation > 64
        || (sourceRef != null && authority.source_ref !== sourceRef)
        || (sourceSha256 != null && authority.source_sha256 !== sourceSha256)
        || (sourceBodyJson !== undefined
          && authority.source_body_json !== sourceBodyJson)
        || !Number.isFinite(Date.parse(String(authority.issued_ts || '')))) return null;
      const expectedAuthoritySha256 = sha256(canonicalJson(
        turnUnderstandingAuthorityBody(authority, execution),
      ));
      if (authority.authority_sha256 !== expectedAuthoritySha256) return null;
      priorGeneration = generation;
      sourceRef = authority.source_ref;
      sourceSha256 = authority.source_sha256;
      sourceBodyJson = authority.source_body_json;
    }
    const authority = authorities.at(-1);
    return Object.freeze({
      ...turnUnderstandingAuthorityBody(authority, execution),
      authoritySha256: authority.authority_sha256,
    });
  } catch { return null; }
}
