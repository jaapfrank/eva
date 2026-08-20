import { createHash } from 'node:crypto';

import {
  EXECUTION_RECORD_CONTRACT_V2,
  EXECUTION_VERIFICATION_VERIFIED,
  executionLedgerStatus,
} from './executionledger.js';
import { canonicalJson } from './util.js';
import { normalizeHttpClientAckPublicKey } from './web/httpack.js';

export const TURN_UNDERSTANDING_AUTHORITY_CONTRACT =
  'TurnUnderstandingExecutionAuthority/v1';
export const TURN_UNDERSTANDING_AUTHORITY_CONTRACT_V2 =
  'TurnUnderstandingExecutionAuthority/v2';
export const TURN_UNDERSTANDING_AUTHORITY_TABLE =
  'eva_turn_understanding_authorities_v1';

const TABLE = TURN_UNDERSTANDING_AUTHORITY_TABLE;
const SHA256 = /^[a-f0-9]{64}$/u;
const HTTP_CLAIM_EVENT_REF = /^http-claim-event:[a-f0-9]{64}$/u;
const SOURCE_BODY_MAX_BYTES = 64 * 1024;
const HTTP_INTENT_MAX_BYTES = 64 * 1024;
const HTTP_CLAIM_HISTORY_MAX = 128;
const CHANNELS = new Set(['telegram', 'voice', 'web']);
const SOURCE_KINDS_BY_CHANNEL = Object.freeze({
  telegram: Object.freeze(['telegram_ingress']),
  voice: Object.freeze(['voice_ingress', 'http_voice_ingress']),
  web: Object.freeze(['http_ingress']),
});
const ARG_FIELDS = Object.freeze([
  'channel', 'conversationId', 'messageId', 'originalText', 'requester',
  'sessionRef', 'supersessionScope', 'supersessionToken', 'syntheticTest',
]);
const HTTP_APPEND_ONLY_TRIGGER_SQL = Object.freeze({
  eva_http_ingress_requests_no_update:
    `CREATE TRIGGER eva_http_ingress_requests_no_update
    BEFORE UPDATE ON eva_http_ingress_requests
    BEGIN SELECT RAISE(ABORT,'HTTP ingress requests are append-only'); END`,
  eva_http_ingress_requests_no_delete:
    `CREATE TRIGGER eva_http_ingress_requests_no_delete
    BEFORE DELETE ON eva_http_ingress_requests
    BEGIN SELECT RAISE(ABORT,'HTTP ingress requests are append-only'); END`,
  eva_http_client_ack_bindings_v3_no_update:
    `CREATE TRIGGER eva_http_client_ack_bindings_v3_no_update
    BEFORE UPDATE ON eva_http_client_ack_bindings_v3
    BEGIN SELECT RAISE(ABORT,'HTTP client ACK bindings are append-only'); END`,
  eva_http_client_ack_bindings_v3_no_delete:
    `CREATE TRIGGER eva_http_client_ack_bindings_v3_no_delete
    BEFORE DELETE ON eva_http_client_ack_bindings_v3
    BEGIN SELECT RAISE(ABORT,'HTTP client ACK bindings are append-only'); END`,
  eva_http_ingress_claim_events_no_update:
    `CREATE TRIGGER eva_http_ingress_claim_events_no_update
    BEFORE UPDATE ON eva_http_ingress_claim_events
    BEGIN SELECT RAISE(ABORT,'HTTP ingress claim events are append-only'); END`,
  eva_http_ingress_claim_events_no_delete:
    `CREATE TRIGGER eva_http_ingress_claim_events_no_delete
    BEFORE DELETE ON eva_http_ingress_claim_events
    BEGIN SELECT RAISE(ABORT,'HTTP ingress claim events are append-only'); END`,
});

export const TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME =
  'eva_turn_understanding_authorities_v1_http_live_claim';
export const TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_SQL =
  `CREATE TRIGGER ${TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME}
  BEFORE INSERT ON ${TABLE}
  WHEN NEW.source_kind IN ('http_ingress','http_voice_ingress')
  BEGIN
    SELECT CASE WHEN NOT EXISTS (
      SELECT 1
      FROM main.eva_http_ingress_requests request
      JOIN main.eva_http_ingress_claim_events claim
        ON claim.request_row_id=request.id
        AND claim.request_ref=request.request_ref
      WHERE NEW.contract='TurnUnderstandingExecutionAuthority/v2'
        AND request.request_ref=NEW.source_ref
        AND request.channel=NEW.channel
        AND request.conversation_id=NEW.conversation_id
        AND request.request_id=NEW.message_id
        AND request.intent_sha256=NEW.source_sha256
        AND claim.phase='execution'
        AND claim.generation=NEW.source_generation
        AND claim.claim_event_ref=NEW.source_claim_event_ref
        AND claim.action IN ('acquired','renewed')
        AND claim.id=(
          SELECT latest.id
          FROM main.eva_http_ingress_claim_events latest
          WHERE latest.request_row_id=request.id
            AND latest.request_ref=request.request_ref
            AND latest.phase='execution'
          ORDER BY latest.generation DESC,latest.sequence DESC
          LIMIT 1
        )
        AND length(NEW.issued_ts)=24
        AND strftime('%Y-%m-%dT%H:%M:%fZ',NEW.issued_ts) IS NEW.issued_ts
        AND claim.created_ts=NEW.issued_ts
        AND NEW.issued_ts<claim.lease_until_ts
        AND strftime('%Y-%m-%dT%H:%M:%fZ','now')<claim.lease_until_ts
    ) THEN RAISE(ABORT,'HTTP turn authority requires a live exact execution claim') END;
  END`;
const TURN_UNDERSTANDING_HTTP_AUTHORITY_V1_TRIGGER_SQL =
  TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_SQL
    .replace(
      "      WHERE NEW.contract='TurnUnderstandingExecutionAuthority/v2'\n        AND request.request_ref=NEW.source_ref",
      '      WHERE request.request_ref=NEW.source_ref',
    )
    .replace('        AND claim.claim_event_ref=NEW.source_claim_event_ref\n', '')
    .replace('        AND claim.created_ts=NEW.issued_ts',
      '        AND claim.created_ts<=NEW.issued_ts');

export const TURN_UNDERSTANDING_AUTHORITY_HTTP_SOURCE_V1_TABLE_SQL = `CREATE TABLE ${TABLE} (
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
    OR (channel='voice' AND source_kind IN ('voice_ingress','http_voice_ingress'))),
  CHECK(typeof(execution_seq)='integer' AND execution_seq>=1),
  CHECK(typeof(source_generation)='integer'
    AND source_generation>=1 AND source_generation<=64),
  CHECK(length(intent_record_sha256)=64 AND lower(intent_record_sha256)=intent_record_sha256
    AND intent_record_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(source_sha256)=64 AND lower(source_sha256)=source_sha256
    AND source_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(((channel='web' OR (channel='voice' AND source_kind='http_voice_ingress'))
      AND source_body_json IS NULL)
    OR ((channel='telegram' OR (channel='voice' AND source_kind='voice_ingress'))
      AND typeof(source_body_json)='text'
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

export const TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL =
  TURN_UNDERSTANDING_AUTHORITY_HTTP_SOURCE_V1_TABLE_SQL
    .replace(
      "  source_generation INTEGER NOT NULL,\n  authority_sha256 TEXT NOT NULL,",
      `  source_generation INTEGER NOT NULL,
  source_claim_event_ref TEXT,
  authority_sha256 TEXT NOT NULL,`,
    )
    .replace(
      "  CHECK(contract='TurnUnderstandingExecutionAuthority/v1'),",
      `  CHECK(contract IN ('TurnUnderstandingExecutionAuthority/v1',
    'TurnUnderstandingExecutionAuthority/v2')),
  CHECK(contract='TurnUnderstandingExecutionAuthority/v1'
    OR source_kind IN ('http_ingress','http_voice_ingress')),`,
    )
    .replace(
      "  CHECK(length(authority_sha256)=64 AND lower(authority_sha256)=authority_sha256",
      `  CHECK(((source_kind IN ('http_ingress','http_voice_ingress'))
      AND source_claim_event_ref IS NOT NULL
      AND source_claim_event_ref GLOB 'http-claim-event:*'
      AND length(source_claim_event_ref)=81)
    OR (source_kind NOT IN ('http_ingress','http_voice_ingress')
      AND source_claim_event_ref IS NULL)),
  CHECK(length(authority_sha256)=64 AND lower(authority_sha256)=authority_sha256`,
    );

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
          OR (NEW.channel='voice'
            AND NEW.source_kind IN ('voice_ingress','http_voice_ingress'))))
    BEGIN SELECT RAISE(ABORT,'turn understanding authority requires exact started V2 execution'); END`,
});

// Exact het schema dat direct vóór de gediscrimineerde HTTP-voicebron actief was.
// Alleen deze bytevorm mag naar het huidige schema worden overgezet.
export const TURN_UNDERSTANDING_AUTHORITY_PRIOR_TABLE_SQL =
  TURN_UNDERSTANDING_AUTHORITY_HTTP_SOURCE_V1_TABLE_SQL
    .replace(
      "OR (channel='voice' AND source_kind IN ('voice_ingress','http_voice_ingress'))",
      "OR (channel='voice' AND source_kind='voice_ingress')",
    )
    .replace(`CHECK(((channel='web' OR (channel='voice' AND source_kind='http_voice_ingress'))
      AND source_body_json IS NULL)
    OR ((channel='telegram' OR (channel='voice' AND source_kind='voice_ingress'))
      AND typeof(source_body_json)='text'`, `CHECK((channel='web' AND source_body_json IS NULL)
    OR (channel IN ('telegram','voice') AND typeof(source_body_json)='text'`);
export const TURN_UNDERSTANDING_AUTHORITY_PRIOR_TRIGGER_SQL = Object.freeze({
  ...TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL,
  eva_turn_understanding_authorities_v1_valid_execution:
    TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL
      .eva_turn_understanding_authorities_v1_valid_execution
      .replace(`OR (NEW.channel='voice'
            AND NEW.source_kind IN ('voice_ingress','http_voice_ingress'))`,
      "OR (NEW.channel='voice' AND NEW.source_kind='voice_ingress')"),
});

// Exact het eenmalig intern bevroren predecessor-schema, vóór source_body_json.
// Dit is geen generieke schemaherkenning: iedere andere DDL/trigger/indexvorm
// blijft fail-closed. Native predecessor-rijen zijn niet migreerbaar omdat hun
// canonieke bronbytes destijds niet werden bewaard.
const PREDECESSOR_TABLE_SQL = TURN_UNDERSTANDING_AUTHORITY_PRIOR_TABLE_SQL
  .replace('  source_body_json TEXT,\n', '')
  .replace(`  CHECK((channel='web' AND source_body_json IS NULL)
    OR (channel IN ('telegram','voice') AND typeof(source_body_json)='text'
      AND length(CAST(source_body_json AS BLOB))>=2
      AND length(CAST(source_body_json AS BLOB))<=65536
      AND json_valid(source_body_json) AND json_type(source_body_json)='object')),\n`, '');
const PREDECESSOR_TRIGGER_SQL = Object.freeze({
  ...TURN_UNDERSTANDING_AUTHORITY_PRIOR_TRIGGER_SQL,
  eva_turn_understanding_authorities_v1_valid_successor:
    TURN_UNDERSTANDING_AUTHORITY_PRIOR_TRIGGER_SQL
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
  ['source_generation', 'INTEGER', 1, 2], ['source_claim_event_ref', 'TEXT', 0, 0],
  ['authority_sha256', 'TEXT', 1, 0],
  ['issued_ts', 'TEXT', 1, 0],
]);
const HTTP_SOURCE_V1_COLUMNS = Object.freeze(EXPECTED_COLUMNS
  .filter(([name]) => name !== 'source_claim_event_ref')
  .map(([name, type, notnull, pk]) => [name, type, notnull, pk]));
const PREDECESSOR_COLUMNS = Object.freeze(EXPECTED_COLUMNS
  .filter(([name]) => !['source_body_json', 'source_claim_event_ref'].includes(String(name)))
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

function exactIndexes(database, generationCid = 11, authorityCid = 13) {
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

/**
 * @param {any} database
 * @param {Readonly<Record<string, string>>} [expectedSql]
 */
function exactTriggers(database, expectedSql = TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL,
  httpBoundarySql = TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_SQL) {
  const rows = database.prepare(`SELECT name,sql FROM main.sqlite_master
    WHERE type='trigger' AND tbl_name=? ORDER BY name`).all(TABLE);
  const httpBoundary = rows.find((row) => (
    row.name === TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME
  ));
  if (httpBoundary && normalizeSql(httpBoundary.sql)
    !== normalizeSql(httpBoundarySql)) return false;
  const exactExpected = httpBoundary ? {
    ...expectedSql,
    [TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME]:
      httpBoundarySql,
  } : expectedSql;
  const expected = Object.keys(exactExpected).sort();
  return canonicalJson(rows.map((row) => String(row.name))) === canonicalJson(expected)
    && rows.every((row) => normalizeSql(row.sql) === normalizeSql(
      exactExpected[row.name],
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
      && exactTriggers(database, PREDECESSOR_TRIGGER_SQL,
        TURN_UNDERSTANDING_HTTP_AUTHORITY_V1_TRIGGER_SQL)
      && exactNamespace(database, TURN_UNDERSTANDING_HTTP_AUTHORITY_V1_TRIGGER_SQL);
  } catch { return false; }
}

function exactHttpSourceV1Schema(database) {
  try {
    const shadow = temporaryShadow(database);
    if (shadow) return false;
    const sql = database.prepare(`SELECT sql FROM main.sqlite_master
      WHERE type='table' AND name=?`).get(TABLE)?.sql;
    return normalizeSql(sql) === normalizeSql(
      TURN_UNDERSTANDING_AUTHORITY_HTTP_SOURCE_V1_TABLE_SQL,
    )
      && exactColumns(database, HTTP_SOURCE_V1_COLUMNS)
      && exactIndexes(database, 11, 12)
      && exactTriggers(
        database, TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL,
        TURN_UNDERSTANDING_HTTP_AUTHORITY_V1_TRIGGER_SQL,
      )
      && exactNamespace(database, TURN_UNDERSTANDING_HTTP_AUTHORITY_V1_TRIGGER_SQL);
  } catch { return false; }
}

function exactPriorSchema(database) {
  try {
    const shadow = temporaryShadow(database);
    if (shadow) return false;
    const sql = database.prepare(`SELECT sql FROM main.sqlite_master
      WHERE type='table' AND name=?`).get(TABLE)?.sql;
    return normalizeSql(sql) === normalizeSql(
      TURN_UNDERSTANDING_AUTHORITY_PRIOR_TABLE_SQL,
    )
      && exactColumns(database, HTTP_SOURCE_V1_COLUMNS)
      && exactIndexes(database, 11, 12)
      && exactTriggers(database, TURN_UNDERSTANDING_AUTHORITY_PRIOR_TRIGGER_SQL,
        TURN_UNDERSTANDING_HTTP_AUTHORITY_V1_TRIGGER_SQL)
      && exactNamespace(database, TURN_UNDERSTANDING_HTTP_AUTHORITY_V1_TRIGGER_SQL);
  } catch { return false; }
}

function installHttpAuthorityBoundaryForExistingSourceTables(database) {
  const names = [
    'eva_http_ingress_requests', 'eva_http_client_ack_bindings_v3',
    'eva_http_ingress_claim_events',
  ];
  const present = names.filter((name) => database.prepare(`SELECT 1
    FROM main.sqlite_master WHERE type='table' AND name=?`).get(name));
  if (!present.length) return;
  if (present.length !== names.length) {
    throw new Error('turn authority migration ziet een incompleet HTTP-bronschema');
  }
  const boundary = database.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(
    TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME,
  );
  if (!boundary) database.exec(TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_SQL);
  else if (normalizeSql(boundary.sql)
    !== normalizeSql(TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_SQL)) {
    throw new Error('turn authority migration ziet een gewijzigde HTTP-ingressgrens');
  }
}

const HTTP_SOURCE_V1_FIELDS = `contract,execution_key,execution_seq,
  intent_record_sha256,channel,conversation_id,message_id,source_kind,source_ref,
  source_sha256,source_body_json,source_generation,authority_sha256,issued_ts`;

function migrateExactHttpSourceV1Schema(database) {
  if (!exactHttpSourceV1Schema(database)) return false;
  database.transaction(() => {
    const before = database.prepare(`SELECT ${HTTP_SOURCE_V1_FIELDS}
      FROM main.${TABLE} ORDER BY execution_key,source_generation`).all();
    const migrated = before.map((authority) => {
      const http = ['http_ingress', 'http_voice_ingress'].includes(authority.source_kind);
      const sourceClaimEventRef = http
        ? reconstructedLegacyHttpClaimEventRefForAuthority(database, authority) : null;
      if (http && !sourceClaimEventRef) {
        throw new Error('exact HTTP-v1 authority heeft geen unieke claimeventbinding');
      }
      return { ...authority, source_claim_event_ref: sourceClaimEventRef };
    });
    const predecessor = `${TABLE}_http_source_v1`;
    const triggerNames = database.prepare(`SELECT name FROM main.sqlite_master
      WHERE type='trigger' AND tbl_name=? ORDER BY name`).all(TABLE);
    for (const { name } of triggerNames) database.exec(`DROP TRIGGER main.${name}`);
    database.exec(`ALTER TABLE main.${TABLE} RENAME TO ${predecessor}`);
    database.exec(TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL);
    const insert = database.prepare(`INSERT INTO main.${TABLE} (
      contract,execution_key,execution_seq,intent_record_sha256,channel,
      conversation_id,message_id,source_kind,source_ref,source_sha256,source_body_json,
      source_generation,source_claim_event_ref,authority_sha256,issued_ts
    ) VALUES (@contract,@execution_key,@execution_seq,@intent_record_sha256,@channel,
      @conversation_id,@message_id,@source_kind,@source_ref,@source_sha256,
      @source_body_json,@source_generation,@source_claim_event_ref,
      @authority_sha256,@issued_ts)`);
    for (const authority of migrated) insert.run(authority);
    database.exec(`DROP TABLE main.${predecessor}`);
    for (const sql of Object.values(TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL)) {
      database.exec(sql);
    }
    installHttpAuthorityBoundaryForExistingSourceTables(database);
    requireTurnUnderstandingAuthorityStore(database);
    const after = database.prepare(`SELECT ${HTTP_SOURCE_V1_FIELDS}
      FROM main.${TABLE} ORDER BY execution_key,source_generation`).all();
    if (canonicalJson(after) !== canonicalJson(before)) {
      throw new Error('HTTP-v1 migration veranderde duurzaam gepinde authoritybytes');
    }
    if (!existingHttpTurnAuthorityBindingsExact(database)) {
      throw new Error('HTTP-v1 migration leverde geen exacte claimeventbinding');
    }
  }).immediate();
  return true;
}

function migrateExactPriorSchema(database) {
  if (!exactPriorSchema(database)) return false;
  const fields = `contract,execution_key,execution_seq,intent_record_sha256,channel,
    conversation_id,message_id,source_kind,source_ref,source_sha256,source_body_json,
    source_generation,authority_sha256,issued_ts`;
  const predecessor = `${TABLE}_exact_prior_schema`;
  database.transaction(() => {
    const before = database.prepare(`SELECT ${fields} FROM main.${TABLE}
      ORDER BY execution_key,source_generation`).all();
    const bound = before.map((authority) => {
      const http = authority.source_kind === 'http_ingress';
      const sourceClaimEventRef = http
        ? reconstructedLegacyHttpClaimEventRefForAuthority(database, authority) : null;
      if (http && !sourceClaimEventRef) {
        throw new Error('exact prior-schema bevat ongeldig authoritybewijs zonder unieke claim');
      }
      return { ...authority, source_claim_event_ref: sourceClaimEventRef };
    });
    database.exec(`DROP TRIGGER IF EXISTS main.${
      TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME}`);
    for (const name of Object.keys(TURN_UNDERSTANDING_AUTHORITY_PRIOR_TRIGGER_SQL)) {
      database.exec(`DROP TRIGGER main.${name}`);
    }
    database.exec(`ALTER TABLE main.${TABLE} RENAME TO ${predecessor}`);
    database.exec(TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL);
    const insert = database.prepare(`INSERT INTO main.${TABLE} (
      contract,execution_key,execution_seq,intent_record_sha256,channel,
      conversation_id,message_id,source_kind,source_ref,source_sha256,source_body_json,
      source_generation,source_claim_event_ref,authority_sha256,issued_ts
    ) VALUES (@contract,@execution_key,@execution_seq,@intent_record_sha256,@channel,
      @conversation_id,@message_id,@source_kind,@source_ref,@source_sha256,
      @source_body_json,@source_generation,@source_claim_event_ref,
      @authority_sha256,@issued_ts)`);
    for (const authority of bound) insert.run(authority);
    database.exec(`DROP TABLE main.${predecessor}`);
    for (const sql of Object.values(TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL)) {
      database.exec(sql);
    }
    installHttpAuthorityBoundaryForExistingSourceTables(database);
    requireTurnUnderstandingAuthorityStore(database);
    const after = database.prepare(`SELECT ${fields} FROM main.${TABLE}
      ORDER BY execution_key,source_generation`).all();
    if (canonicalJson(after) !== canonicalJson(before)) {
      throw new Error('exact prior-schema migration veranderde authoritybytes');
    }
    if (!existingHttpTurnAuthorityBindingsExact(database)) {
      throw new Error('exact prior-schema bevat ongeldig authoritybewijs');
    }
    const migrated = database.prepare(`SELECT DISTINCT execution_key
      FROM main.${TABLE}`).all();
    for (const { execution_key: executionKey } of migrated) {
      const execution = database.prepare(`SELECT * FROM main.turn_tool_executions
        WHERE execution_key=?`).get(executionKey);
      if (!turnUnderstandingAuthorityRecord(database, execution)) {
        throw new Error('exact prior-schema bevat ongeldig authoritybewijs');
      }
    }
  }).immediate();
  return true;
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
    const fields = `contract,execution_key,execution_seq,intent_record_sha256,channel,
      conversation_id,message_id,source_kind,source_ref,source_sha256,
      source_generation,authority_sha256,issued_ts`;
    const before = database.prepare(`SELECT ${fields} FROM main.${TABLE}
      ORDER BY execution_key,source_generation`).all();
    const bound = before.map((row) => {
      const authority = { ...row, source_body_json: null };
      const sourceClaimEventRef = reconstructedLegacyHttpClaimEventRefForAuthority(
        database, authority,
      );
      if (!sourceClaimEventRef) {
        throw new Error('exact predecessor bevat ongeldig web-authoritybewijs');
      }
      return { ...authority, source_claim_event_ref: sourceClaimEventRef };
    });
    database.exec(`DROP TRIGGER IF EXISTS main.${
      TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME}`);
    for (const name of Object.keys(PREDECESSOR_TRIGGER_SQL)) {
      database.exec(`DROP TRIGGER main.${name}`);
    }
    database.exec(`ALTER TABLE main.${TABLE} RENAME TO ${predecessor}`);
    database.exec(TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL);
    const insert = database.prepare(`INSERT INTO main.${TABLE} (
      contract,execution_key,execution_seq,intent_record_sha256,channel,
      conversation_id,message_id,source_kind,source_ref,source_sha256,
      source_body_json,source_generation,source_claim_event_ref,authority_sha256,issued_ts
    ) VALUES (@contract,@execution_key,@execution_seq,@intent_record_sha256,@channel,
      @conversation_id,@message_id,@source_kind,@source_ref,@source_sha256,
      @source_body_json,@source_generation,@source_claim_event_ref,
      @authority_sha256,@issued_ts)`);
    for (const authority of bound) insert.run(authority);
    database.exec(`DROP TABLE main.${predecessor}`);
    for (const sql of Object.values(TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL)) {
      database.exec(sql);
    }
    installHttpAuthorityBoundaryForExistingSourceTables(database);
    const after = database.prepare(`SELECT ${fields} FROM main.${TABLE}
      ORDER BY execution_key,source_generation`).all();
    if (canonicalJson(after) !== canonicalJson(before)) {
      throw new Error('exact predecessor migration veranderde authoritybytes');
    }
    if (!existingHttpTurnAuthorityBindingsExact(database)) {
      throw new Error('exact predecessor bevat ongeldig web-authoritybewijs');
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

function exactNamespace(database,
  httpBoundarySql = TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_SQL) {
  const expected = new Map([[TABLE, ['table', TABLE]]]);
  for (let ordinal = 1; ordinal <= 6; ordinal += 1) {
    expected.set(`sqlite_autoindex_${TABLE}_${ordinal}`, ['index', TABLE]);
  }
  for (const name of Object.keys(TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL)) {
    expected.set(name, ['trigger', TABLE]);
  }
  const httpBoundary = database.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=? AND tbl_name=?`).get(
    TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME, TABLE,
  );
  if (httpBoundary) {
    if (normalizeSql(httpBoundary.sql)
      !== normalizeSql(httpBoundarySql)) return false;
    expected.set(TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME, ['trigger', TABLE]);
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
    if (!migrateExactHttpSourceV1Schema(database)
      && !migrateExactPriorSchema(database)) migrateExactPredecessor(database);
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

function plainObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}

function finiteHttpJson(value, depth = 0) {
  if (depth > 12 || value === undefined) return false;
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (Array.isArray(value)) {
    return value.length <= 128 && value.every((child) => finiteHttpJson(child, depth + 1));
  }
  if (!plainObject(value)) return false;
  const keys = Object.keys(value);
  return keys.length <= 128 && keys.every((key) => (
    key.length >= 1 && key.length <= 160 && finiteHttpJson(value[key], depth + 1)
  ));
}

function hasControlCharacters(value) {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code <= 31 || code === 127) return true;
  }
  return false;
}

function canonicalIso(value) {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString() === value;
}

function exactHttpSourceAppendOnlyBoundary(database) {
  return Object.entries(HTTP_APPEND_ONLY_TRIGGER_SQL).every(([name, sql]) => {
    const row = database.prepare(`SELECT tbl_name,sql FROM main.sqlite_master
      WHERE type='trigger' AND name=?`).get(name);
    const table = name.startsWith('eva_http_ingress_requests_')
      ? 'eva_http_ingress_requests'
      : name.startsWith('eva_http_client_ack_bindings_v3_')
        ? 'eva_http_client_ack_bindings_v3' : 'eva_http_ingress_claim_events';
    return row?.tbl_name === table && normalizeSql(row.sql) === normalizeSql(sql);
  });
}

function exactHttpAppendOnlyBoundary(database) {
  if (!exactHttpSourceAppendOnlyBoundary(database)) return false;
  const row = database.prepare(`SELECT tbl_name,sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(
    TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME,
  );
  return row?.tbl_name === TABLE && normalizeSql(row.sql)
    === normalizeSql(TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_SQL);
}

function exactHttpIntent(intent, authority, args) {
  const voice = authority.source_kind === 'http_voice_ingress';
  const fields = voice
    ? [
      'contract', 'route', 'sender', 'rawText', 'conversationId',
      'shouldSpeak', 'speaker', 'syntheticTest', 'clientAckPublicKey',
    ]
    : [
      'contract', 'route', 'sender', 'rawText', 'conversationId',
      'syntheticTest', 'clientAckPublicKey',
    ];
  if (!plainObject(intent)
    || canonicalJson(Object.keys(intent).sort()) !== canonicalJson([...fields].sort())
    || intent.contract !== 'PublicHttpIntent/v1'
    || intent.route !== (voice ? '/api/voice' : '/api/chat')
    || typeof intent.sender !== 'string' || !intent.sender
    || intent.sender !== intent.sender.trim() || intent.sender.length > 160
    || hasControlCharacters(intent.sender) || typeof intent.rawText !== 'string'
    || intent.conversationId !== authority.conversation_id
    || typeof intent.conversationId !== 'string' || !intent.conversationId
    || intent.conversationId !== intent.conversationId.trim()
    || intent.conversationId.length > 180 || hasControlCharacters(intent.conversationId)
    || typeof intent.syntheticTest !== 'boolean'
    || (voice && (typeof intent.shouldSpeak !== 'boolean'
      || !(intent.speaker === null || (typeof intent.speaker === 'string'
        && intent.speaker.length <= 160 && !hasControlCharacters(intent.speaker)))))
    || args.requester !== intent.sender || args.originalText !== intent.rawText.trim()
    || args.syntheticTest !== intent.syntheticTest || !finiteHttpJson(intent)) return null;
  try {
    const key = normalizeHttpClientAckPublicKey(intent.clientAckPublicKey);
    if (canonicalJson(intent.clientAckPublicKey) !== key.publicKeyJson) return null;
    const serialized = canonicalJson({ ...intent, clientAckPublicKey: key.publicKey });
    if (Buffer.byteLength(serialized, 'utf8') > HTTP_INTENT_MAX_BYTES) return null;
    return { intent, key, serialized, sha256: sha256(serialized) };
  } catch { return null; }
}

function exactHttpRequest(database, authority, args) {
  const rows = database.prepare(`SELECT * FROM main.eva_http_ingress_requests
    WHERE channel=? AND conversation_id=? AND request_id=? LIMIT 2`).all(
    authority.channel, authority.conversation_id, authority.message_id,
  );
  if (rows.length !== 1) return null;
  const row = rows[0];
  const requestRef = `http-ingress:${sha256(canonicalJson({
    channel: authority.channel, conversationId: authority.conversation_id,
    requestId: authority.message_id,
  }))}`;
  if (row.contract !== 'HttpIngressRequest/v1'
    || !Number.isSafeInteger(Number(row.id)) || Number(row.id) < 1
    || row.request_ref !== requestRef || authority.source_ref !== requestRef
    || row.channel !== authority.channel
    || row.conversation_id !== authority.conversation_id
    || row.request_id !== authority.message_id
    || row.intent_sha256 !== authority.source_sha256
    || !SHA256.test(String(row.intent_sha256 || ''))
    || !Number.isSafeInteger(Number(row.intent_bytes))
    || Number(row.intent_bytes) < 2 || Number(row.intent_bytes) > HTTP_INTENT_MAX_BYTES
    || !canonicalIso(row.started_ts)) return null;
  const binding = database.prepare(`SELECT * FROM main.eva_http_client_ack_bindings_v3
    WHERE request_row_id=? AND request_ref=?`).get(row.id, row.request_ref);
  if (!binding || binding.contract !== 'HttpClientAckBinding/v3'
    || Number(binding.request_row_id) !== Number(row.id)
    || binding.request_ref !== row.request_ref
    || binding.intent_sha256 !== row.intent_sha256
    || !SHA256.test(String(binding.ack_challenge || ''))
    || sha256(binding.ack_challenge) !== binding.ack_challenge_sha256
    || !canonicalIso(binding.created_ts)) return null;
  let intent;
  let storedBinding;
  let storedKey;
  try {
    intent = JSON.parse(binding.intent_json);
    storedBinding = JSON.parse(binding.binding_json);
    storedKey = JSON.parse(binding.public_key_json);
  } catch { return null; }
  const canonical = exactHttpIntent(intent, authority, args);
  if (!canonical || canonical.serialized !== binding.intent_json
    || canonical.sha256 !== binding.intent_sha256
    || Buffer.byteLength(binding.intent_json, 'utf8') !== Number(row.intent_bytes)
    || canonicalJson(storedKey) !== binding.public_key_json
    || canonical.key.publicKeyJson !== binding.public_key_json
    || canonical.key.keyFingerprint !== binding.key_fingerprint
    || canonicalJson(storedBinding) !== binding.binding_json) return null;
  const expectedBinding = {
    algorithm: 'ES256', challenge: binding.ack_challenge,
    challengeSha256: binding.ack_challenge_sha256,
    contract: 'HttpClientAckBinding/v3', intentSha256: binding.intent_sha256,
    keyFingerprint: binding.key_fingerprint, publicKey: canonical.key.publicKey,
    requestRef: binding.request_ref,
  };
  return canonicalJson(expectedBinding) === binding.binding_json ? row : null;
}

function exactHttpExecutionClaimHistory(database, request) {
  const rows = database.prepare(`SELECT * FROM main.eva_http_ingress_claim_events
    WHERE request_row_id=? AND phase='execution' ORDER BY generation,sequence
    LIMIT ${HTTP_CLAIM_HISTORY_MAX + 1}`).all(request.id);
  if (!rows.length || rows.length > HTTP_CLAIM_HISTORY_MAX) return null;
  /** @type {{action:string,generation:number,sequence:number,tokenSha256:string,
   * leaseUntil:string,createdAt:string}|null} */
  let current = null;
  const generations = new Set();
  const claimEvents = new Map();
  for (const row of rows) {
    const generation = Number(row.generation);
    const sequence = Number(row.sequence);
    if (row.contract !== 'HttpIngressClaimEvent/v1'
      || Number(row.request_row_id) !== Number(request.id)
      || row.request_ref !== request.request_ref || row.phase !== 'execution'
      || !['acquired', 'renewed', 'released'].includes(row.action)
      || !Number.isSafeInteger(generation) || generation < 1 || generation > 64
      || !Number.isSafeInteger(sequence) || sequence < 0 || sequence > 64
      || !SHA256.test(String(row.token_sha256 || ''))
      || !canonicalIso(row.lease_until_ts) || !canonicalIso(row.created_ts)) return null;
    const body = {
      action: row.action, contract: 'HttpIngressClaimEvent/v1', generation,
      leaseUntil: row.lease_until_ts, phase: 'execution',
      requestRef: row.request_ref, sequence, tokenSha256: row.token_sha256,
    };
    const eventJson = canonicalJson(body);
    const eventSha256 = sha256(eventJson);
    if (row.event_json !== eventJson || row.claim_event_sha256 !== eventSha256
      || row.claim_event_ref !== `http-claim-event:${eventSha256}`) return null;
    if (row.action === 'acquired') {
      const expectedGeneration = current === null ? 1 : current.generation + 1;
      if (sequence !== 0 || generation !== expectedGeneration
        || Date.parse(row.lease_until_ts) <= Date.parse(row.created_ts)
        || (current && Date.parse(row.created_ts) < Date.parse(current.createdAt))
        || (current && current.action !== 'released'
          && Date.parse(current.leaseUntil) > Date.parse(row.created_ts))) return null;
      generations.add(generation);
    } else if (!current || current.action === 'released'
      || generation !== current.generation || sequence !== current.sequence + 1
      || row.token_sha256 !== current.tokenSha256
      || Date.parse(row.created_ts) < Date.parse(current.createdAt)
      || (row.action === 'renewed' && (Date.parse(current.leaseUntil)
        <= Date.parse(row.created_ts)
        || Date.parse(row.lease_until_ts) <= Date.parse(current.leaseUntil)))
      || (row.action === 'released' && row.lease_until_ts !== current.leaseUntil)) return null;
    claimEvents.set(row.claim_event_ref, Object.freeze({
      action: row.action, generation, sequence,
      createdAt: row.created_ts, leaseUntil: row.lease_until_ts,
    }));
    current = {
      action: row.action, generation, sequence, tokenSha256: row.token_sha256,
      leaseUntil: row.lease_until_ts, createdAt: row.created_ts,
    };
  }
  if (current === null) return null;
  return { currentGeneration: current.generation, generations, claimEvents };
}

function exactHttpAuthorityIssuance(httpProof, authority) {
  if (!HTTP_CLAIM_EVENT_REF.test(String(authority.source_claim_event_ref || ''))) return false;
  const event = httpProof.claimEvents.get(authority.source_claim_event_ref);
  if (!event || !['acquired', 'renewed'].includes(event.action)
    || event.generation !== Number(authority.source_generation)) return false;
  return authority.contract !== TURN_UNDERSTANDING_AUTHORITY_CONTRACT_V2
    || authority.issued_ts === event.createdAt;
}

function reconstructedLegacyHttpClaimEventRef(httpProof, generation, issuedTs) {
  if (!canonicalIso(issuedTs)) return null;
  const issuedMs = Date.parse(issuedTs);
  const events = [...httpProof.claimEvents.entries()]
    .filter(([, event]) => event.generation === generation
      && Date.parse(event.createdAt) <= issuedMs);
  if (!events.length) return null;
  const latestCreatedMs = Math.max(...events.map(([, event]) => (
    Date.parse(event.createdAt)
  )));
  const latest = events.filter(([, event]) => (
    Date.parse(event.createdAt) === latestCreatedMs
  ));
  if (latest.length !== 1) return null;
  const [claimEventRef, event] = latest[0];
  return ['acquired', 'renewed'].includes(event.action)
    && issuedMs < Date.parse(event.leaseUntil) ? claimEventRef : null;
}

function reconstructedLegacyHttpClaimEventRefForAuthority(database, authority) {
  if (authority.contract !== TURN_UNDERSTANDING_AUTHORITY_CONTRACT
    || !['http_ingress', 'http_voice_ingress'].includes(authority.source_kind)
    || !exactHttpSourceAppendOnlyBoundary(database)) return null;
  const execution = database.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(authority.execution_key);
  const args = parsedUnderstandingArgs(execution);
  const request = args ? exactHttpRequest(database, authority, args) : null;
  const httpProof = request ? exactHttpExecutionClaimHistory(database, request) : null;
  if (!execution || !httpProof || !exactSourceBody(authority)
    || authority.authority_sha256 !== sha256(canonicalJson(
      turnUnderstandingAuthorityBody(authority, execution),
    ))) return null;
  return reconstructedLegacyHttpClaimEventRef(
    httpProof, Number(authority.source_generation), authority.issued_ts,
  );
}

function exactDurableHttpSource(database, authority, args) {
  if (!['http_ingress', 'http_voice_ingress'].includes(authority.source_kind)
    || !exactHttpAppendOnlyBoundary(database)) return null;
  const request = exactHttpRequest(database, authority, args);
  return request ? exactHttpExecutionClaimHistory(database, request) : null;
}

function exactSourceBody(authority) {
  const channel = String(authority?.channel || '');
  const sourceKind = String(authority?.source_kind || '');
  const bodyJson = authority?.source_body_json;
  if (channel === 'web' || (channel === 'voice' && sourceKind === 'http_voice_ingress')) {
    return bodyJson === null;
  }
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
  const body = {
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
    ...(authority.contract === TURN_UNDERSTANDING_AUTHORITY_CONTRACT_V2 ? {
      sourceClaimEventRef: authority.source_claim_event_ref,
    } : {}),
    issuedTs: authority.issued_ts,
    turnId: execution.turn_id,
    tool: execution.tool,
    args: execution.args,
  };
  return body;
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
    const httpSource = ['http_ingress', 'http_voice_ingress'].includes(
      authorities.at(-1)?.source_kind,
    );
    const httpProof = httpSource
      ? exactDurableHttpSource(database, authorities.at(-1), args) : null;
    if (httpSource && !httpProof) return null;
    let priorGeneration = 0;
    let sourceRef = null;
    let sourceSha256 = null;
    let sourceBodyJson;
    for (const authority of authorities) {
      const generation = Number(authority.source_generation);
      if (![TURN_UNDERSTANDING_AUTHORITY_CONTRACT,
        TURN_UNDERSTANDING_AUTHORITY_CONTRACT_V2].includes(authority.contract)
        || (authority.contract === TURN_UNDERSTANDING_AUTHORITY_CONTRACT_V2
          && !['http_ingress', 'http_voice_ingress'].includes(authority.source_kind))
        || authority.execution_key !== execution.execution_key
        || Number(authority.execution_seq) !== Number(execution.execution_seq)
        || authority.intent_record_sha256 !== execution.intent_record_sha256
        || authority.channel !== args.channel
        || authority.conversation_id !== args.conversationId
        || authority.message_id !== messageId
        || !SOURCE_KINDS_BY_CHANNEL[args.channel]?.includes(authority.source_kind)
        || typeof authority.source_ref !== 'string' || !authority.source_ref
        || !SHA256.test(String(authority.source_sha256 || ''))
        || !exactSourceBody(authority)
        || !Number.isSafeInteger(generation) || generation <= priorGeneration
        || generation > 64
        || (httpProof && (!httpProof.generations.has(generation)
          || !exactHttpAuthorityIssuance(httpProof, authority)))
        || (sourceRef != null && authority.source_ref !== sourceRef)
        || (sourceSha256 != null && authority.source_sha256 !== sourceSha256)
        || (sourceBodyJson !== undefined
          && authority.source_body_json !== sourceBodyJson)
        || (httpProof ? !canonicalIso(authority.issued_ts)
          : !Number.isFinite(Date.parse(String(authority.issued_ts || ''))))) return null;
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
      ...(httpProof ? { sourceClaimEventRef: authority.source_claim_event_ref } : {}),
      authoritySha256: authority.authority_sha256,
    });
  } catch { return null; }
}

export function existingHttpTurnAuthorityBindingsExact(database, {
  reconstructLegacy = false,
} = {}) {
  try {
    requireTurnUnderstandingAuthorityStore(database);
    const authorities = database.prepare(`SELECT * FROM main.${TABLE}
      WHERE source_kind IN ('http_ingress','http_voice_ingress')
      ORDER BY execution_key,source_generation`).all();
    const executions = new Map();
    for (const authority of authorities) {
      let execution = executions.get(authority.execution_key);
      if (!execution) {
        execution = database.prepare(`SELECT * FROM main.turn_tool_executions
          WHERE execution_key=?`).get(authority.execution_key);
        if (!execution) return false;
        executions.set(authority.execution_key, execution);
      }
      const args = parsedUnderstandingArgs(execution);
      const httpProof = args ? exactDurableHttpSource(database, authority, args) : null;
      if (!httpProof || !exactHttpAuthorityIssuance(httpProof, authority)
        || (reconstructLegacy
          && authority.contract === TURN_UNDERSTANDING_AUTHORITY_CONTRACT
          && reconstructedLegacyHttpClaimEventRef(
            httpProof, Number(authority.source_generation), authority.issued_ts,
          ) !== authority.source_claim_event_ref)) return false;
    }
    return [...executions.values()].every((execution) => (
      Boolean(turnUnderstandingAuthorityRecord(database, execution))
    ));
  } catch { return false; }
}
