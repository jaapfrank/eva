import { createHash, randomBytes } from 'node:crypto';

import { db, validateExecutionRecord } from '../db.js';
import { executionLedgerStatus } from '../executionledger.js';
import { runtimeVersion } from '../runtimeversion.js';
import { turnUnderstandingAuthorityStoreStatus } from '../turnauthority.js';
import {
  retainedTurnResultMetadata,
  verifyPreparedUnderstandingTurn,
} from '../mind/understanding.js';
import { canonicalJson, normalizeUserFacingText } from '../util.js';
import { normalizeHttpClientAckPublicKey } from './httpack.js';
import { PUBLIC_REQUEST_REPLAY_SCOPE } from './requestidentity.js';
import { exactSqliteIndexSignatures } from './sqliteindexattestation.js';

const ingressDb = /** @type {any} */ (db);
const SHA256 = /^[a-f0-9]{64}$/u;
const REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/u;
const REASON = /^[a-z][a-z0-9_:-]{0,119}$/u;
const MAX_INTENT_BYTES = 64 * 1024;
const MAX_PUBLIC_BODY_BYTES = 24 * 1024;
const MAX_REPLY_LENGTH = 16_000;
const MAX_ERROR_LENGTH = 2_000;
const REQUEST_TABLE = 'eva_http_ingress_requests';
const RESPONSE_TABLE = 'eva_http_prepared_responses';
const EVENT_TABLE = 'eva_http_ingress_state_events';
const CLAIM_EVENT_TABLE = 'eva_http_ingress_claim_events';
const ACK_BINDING_TABLE = 'eva_http_client_ack_bindings_v3';
const OWNED_TABLES = Object.freeze([
  REQUEST_TABLE, RESPONSE_TABLE, EVENT_TABLE, CLAIM_EVENT_TABLE, ACK_BINDING_TABLE,
]);
const mainTable = (table) => `main.${table}`;
const TERMINAL_STATES = new Set(['prepared', 'handoff', 'superseded']);
const CLAIM_PHASES = new Set(['execution', 'settlement']);
const CLAIM_ACTIONS = new Set(['acquired', 'renewed', 'released']);
const MAX_CLAIM_GENERATIONS = 64;
const MAX_CLAIM_SEQUENCE = 64;
const MAX_CLAIM_EVENTS_PER_PHASE = 128;
const CLAIM_LEASE_MS = 30_000;
const CURRENT_PUBLIC_BODY_KEYS = new Set([
  'reply', 'error', 'reason', 'emotion', 'ok',
  'conversation_id', 'turn_id', 'request_id', 'request_replay_scope',
  'runtime_code_sha256', 'conversation_active', 'continue_listening',
  'listen_timeout_ms', 'execution_pending', 'durable_handoff',
]);
const LEGACY_PUBLIC_BODY_KEYS = new Set([
  'reply', 'error', 'reason', 'emotion', 'ok', 'conversationActive',
  'continueListening', 'listenTimeoutMs', 'executionPending', 'durableHandoff',
  'runtimeCodeSha256',
]);
const REQUIRED_COLUMNS = Object.freeze({
  [REQUEST_TABLE]: [
    'id', 'contract', 'request_ref', 'channel', 'conversation_id', 'request_id',
    'intent_sha256', 'intent_bytes', 'started_ts',
  ],
  [RESPONSE_TABLE]: [
    'id', 'contract', 'response_ref', 'response_sha256', 'request_row_id',
    'request_ref', 'target_state', 'status', 'turn_id', 'public_body_json',
    'public_body_sha256', 'provenance_kind', 'provenance_ref',
    'provenance_sha256', 'response_json', 'prepared_ts',
  ],
  [EVENT_TABLE]: [
    'id', 'contract', 'event_ref', 'event_sha256', 'request_row_id',
    'request_ref', 'ordinal', 'state', 'response_ref', 'event_json', 'created_ts',
  ],
  [CLAIM_EVENT_TABLE]: [
    'id', 'contract', 'claim_event_ref', 'claim_event_sha256', 'request_row_id',
    'request_ref', 'phase', 'generation', 'sequence', 'action', 'token_sha256',
    'lease_until_ts', 'event_json', 'created_ts',
  ],
  [ACK_BINDING_TABLE]: [
    'id', 'contract', 'request_row_id', 'request_ref', 'intent_sha256',
    'intent_json', 'public_key_json', 'key_fingerprint', 'ack_challenge',
    'ack_challenge_sha256', 'binding_json', 'created_ts',
  ],
});
const REQUIRED_INDEXES = Object.freeze([
  'idx_http_ingress_request_identity',
  'idx_http_ingress_response_request',
  'idx_http_ingress_event_request',
  'idx_http_ingress_claim_history',
  'idx_http_client_ack_binding_request_v3',
]);
const REQUIRED_TRIGGERS = Object.freeze([
  'eva_http_ingress_requests_no_update',
  'eva_http_ingress_requests_no_delete',
  'eva_http_ingress_requests_no_collision',
  'eva_http_prepared_responses_no_update',
  'eva_http_prepared_responses_no_delete',
  'eva_http_prepared_responses_no_collision',
  'eva_http_prepared_responses_valid_reference',
  'eva_http_ingress_state_events_no_update',
  'eva_http_ingress_state_events_no_delete',
  'eva_http_ingress_state_events_no_collision',
  'eva_http_ingress_state_events_valid_transition',
  'eva_http_ingress_claim_events_no_update',
  'eva_http_ingress_claim_events_no_delete',
  'eva_http_ingress_claim_events_no_collision',
  'eva_http_ingress_claim_events_valid_transition',
  'eva_http_client_ack_bindings_v3_no_update',
  'eva_http_client_ack_bindings_v3_no_delete',
  'eva_http_client_ack_bindings_v3_no_replace',
  'eva_http_client_ack_bindings_v3_valid_request',
]);
const REQUIRED_INDEX_SIGNATURES = Object.freeze({
  [REQUEST_TABLE]: [
    { name: 'idx_http_ingress_request_identity', origin: 'c',
      columns: ['channel', 'conversation_id', 'request_id'] },
    { origin: 'u', columns: ['request_ref'] },
    { origin: 'u', columns: ['channel', 'conversation_id', 'request_id'] },
  ],
  [RESPONSE_TABLE]: [
    { name: 'idx_http_ingress_response_request', origin: 'c',
      columns: ['request_row_id'] },
    ...['response_ref', 'response_sha256', 'request_row_id', 'request_ref']
      .map((column) => ({ origin: 'u', columns: [column] })),
  ],
  [EVENT_TABLE]: [
    { name: 'idx_http_ingress_event_request', origin: 'c',
      columns: ['request_row_id', 'ordinal'] },
    { origin: 'u', columns: ['event_ref'] },
    { origin: 'u', columns: ['event_sha256'] },
    { origin: 'u', columns: ['request_row_id', 'ordinal'] },
  ],
  [CLAIM_EVENT_TABLE]: [
    { name: 'idx_http_ingress_claim_history', origin: 'c', unique: false,
      columns: ['request_row_id', 'phase', 'generation', 'sequence'] },
    { origin: 'u', columns: ['claim_event_ref'] },
    { origin: 'u', columns: ['claim_event_sha256'] },
    { origin: 'u', columns: ['request_row_id', 'phase', 'generation', 'sequence'] },
  ],
  [ACK_BINDING_TABLE]: [
    { name: 'idx_http_client_ack_binding_request_v3', origin: 'c',
      columns: ['request_row_id', 'request_ref'] },
    ...[
      'request_row_id', 'request_ref', 'ack_challenge', 'ack_challenge_sha256',
    ].map((column) => ({ origin: 'u', columns: [column] })),
  ],
});
const CURRENT_PUBLIC_BODY_TRIGGER_RULE = `SELECT CASE WHEN
    (SELECT count(*) FROM json_each(NEW.public_body_json))<6
    OR EXISTS (
      SELECT 1 FROM json_each(NEW.public_body_json)
      WHERE key NOT IN ('reply','error','reason','emotion','ok','conversation_id',
        'turn_id','request_id','request_replay_scope','runtime_code_sha256',
        'conversation_active','continue_listening','listen_timeout_ms',
        'execution_pending','durable_handoff')
    )
    OR (json_type(NEW.public_body_json,'$.reply') IS NULL
      AND json_type(NEW.public_body_json,'$.error') IS NULL)
    OR (json_type(NEW.public_body_json,'$.reply') IS NOT NULL
      AND json_type(NEW.public_body_json,'$.error') IS NOT NULL)
    OR (json_type(NEW.public_body_json,'$.reply') IS NOT NULL
      AND json_type(NEW.public_body_json,'$.reply') IS NOT 'text')
    OR (json_type(NEW.public_body_json,'$.error') IS NOT NULL
      AND json_type(NEW.public_body_json,'$.error') IS NOT 'text')
    OR json_type(NEW.public_body_json,'$.ok') IS NULL
    OR json_type(NEW.public_body_json,'$.ok') NOT IN ('true','false')
    OR json_type(NEW.public_body_json,'$.conversation_id') IS NOT 'text'
    OR json_extract(NEW.public_body_json,'$.conversation_id') IS NOT (
      SELECT conversation_id FROM eva_http_ingress_requests WHERE id=NEW.request_row_id
    )
    OR json_type(NEW.public_body_json,'$.turn_id') IS NULL
    OR json_type(NEW.public_body_json,'$.turn_id') NOT IN ('text','null')
    OR json_extract(NEW.public_body_json,'$.turn_id') IS NOT NEW.turn_id
    OR json_type(NEW.public_body_json,'$.request_id') IS NOT 'text'
    OR json_extract(NEW.public_body_json,'$.request_id') IS NOT (
      SELECT request_id FROM eva_http_ingress_requests WHERE id=NEW.request_row_id
    )
    OR json_extract(NEW.public_body_json,'$.request_replay_scope')
      IS NOT '${PUBLIC_REQUEST_REPLAY_SCOPE}'
    OR json_type(NEW.public_body_json,'$.runtime_code_sha256') IS NOT 'text'
    OR length(json_extract(NEW.public_body_json,'$.runtime_code_sha256'))<>64
    OR lower(json_extract(NEW.public_body_json,'$.runtime_code_sha256'))
      <>json_extract(NEW.public_body_json,'$.runtime_code_sha256')
    OR json_extract(NEW.public_body_json,'$.runtime_code_sha256') GLOB '*[^a-f0-9]*'
    OR (json_type(NEW.public_body_json,'$.reason') IS NOT NULL
      AND json_type(NEW.public_body_json,'$.reason')<>'text')
    OR (json_type(NEW.public_body_json,'$.emotion') IS NOT NULL
      AND json_type(NEW.public_body_json,'$.emotion')<>'text')
    OR (json_type(NEW.public_body_json,'$.execution_pending') IS NOT NULL
      AND json_type(NEW.public_body_json,'$.execution_pending') NOT IN ('true','false'))
    OR (json_type(NEW.public_body_json,'$.durable_handoff') IS NOT NULL
      AND json_type(NEW.public_body_json,'$.durable_handoff') NOT IN ('true','false'))
    OR ((SELECT channel FROM eva_http_ingress_requests WHERE id=NEW.request_row_id)='voice'
      AND (json_type(NEW.public_body_json,'$.conversation_active') IS NULL
        OR json_type(NEW.public_body_json,'$.conversation_active') NOT IN ('true','false')
        OR json_type(NEW.public_body_json,'$.continue_listening') IS NULL
        OR json_type(NEW.public_body_json,'$.continue_listening') NOT IN ('true','false')
        OR json_type(NEW.public_body_json,'$.listen_timeout_ms') IS NOT 'integer'
        OR json_extract(NEW.public_body_json,'$.listen_timeout_ms')<0
        OR json_extract(NEW.public_body_json,'$.listen_timeout_ms')>120000))
    OR ((SELECT channel FROM eva_http_ingress_requests WHERE id=NEW.request_row_id)='web'
      AND (json_type(NEW.public_body_json,'$.conversation_active') IS NOT NULL
        OR json_type(NEW.public_body_json,'$.continue_listening') IS NOT NULL
        OR json_type(NEW.public_body_json,'$.listen_timeout_ms') IS NOT NULL))
  THEN RAISE(ABORT,'HTTP prepared response has invalid current public body') END;`;
const PREVIOUS_PUBLIC_BODY_TRIGGER_RULE = `SELECT CASE WHEN
    (SELECT count(*) FROM json_each(NEW.public_body_json))<1
    OR EXISTS (
      SELECT 1 FROM json_each(NEW.public_body_json)
      WHERE key NOT IN ('reply','error','reason','emotion','ok','conversationActive',
        'continueListening','listenTimeoutMs','executionPending','durableHandoff',
        'runtimeCodeSha256','conversation_id','turn_id','request_id',
        'request_replay_scope','runtime_code_sha256','conversation_active',
        'continue_listening','listen_timeout_ms','execution_pending','durable_handoff')
    )
    OR (json_type(NEW.public_body_json,'$.reply') IS NULL
      AND json_type(NEW.public_body_json,'$.error') IS NULL)
    OR (json_type(NEW.public_body_json,'$.reply') IS NOT NULL
      AND json_type(NEW.public_body_json,'$.error') IS NOT NULL)
  THEN RAISE(ABORT,'HTTP prepared response has invalid public body') END;`;
const HTTP_INGRESS_SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS eva_http_ingress_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  request_ref TEXT NOT NULL UNIQUE,
  channel TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  intent_sha256 TEXT NOT NULL,
  intent_bytes INTEGER NOT NULL,
  started_ts TEXT NOT NULL,
  UNIQUE(channel,conversation_id,request_id),
  CHECK(contract='HttpIngressRequest/v1'),
  CHECK(request_ref GLOB 'http-ingress:*' AND length(request_ref)=77),
  CHECK(channel IN ('web','voice')),
  CHECK(length(conversation_id)>=1 AND length(conversation_id)<=180),
  CHECK(length(request_id)>=8 AND length(request_id)<=160
    AND request_id NOT GLOB '*[^A-Za-z0-9._:-]*'),
  CHECK(length(intent_sha256)=64 AND lower(intent_sha256)=intent_sha256
    AND intent_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(intent_bytes)='integer'
    AND intent_bytes>=2 AND intent_bytes<=${MAX_INTENT_BYTES})
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_http_ingress_request_identity
  ON eva_http_ingress_requests(channel,conversation_id,request_id);
CREATE TRIGGER IF NOT EXISTS eva_http_ingress_requests_no_update
BEFORE UPDATE ON eva_http_ingress_requests
BEGIN SELECT RAISE(ABORT,'HTTP ingress requests are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_ingress_requests_no_delete
BEFORE DELETE ON eva_http_ingress_requests
BEGIN SELECT RAISE(ABORT,'HTTP ingress requests are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_ingress_requests_no_collision
BEFORE INSERT ON eva_http_ingress_requests
WHEN EXISTS (
  SELECT 1 FROM eva_http_ingress_requests
  WHERE id=NEW.id OR request_ref=NEW.request_ref
    OR (channel=NEW.channel AND conversation_id=NEW.conversation_id
      AND request_id=NEW.request_id)
)
BEGIN SELECT RAISE(ABORT,'HTTP ingress request identity collision'); END;

CREATE TABLE IF NOT EXISTS eva_http_prepared_responses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  response_ref TEXT NOT NULL UNIQUE,
  response_sha256 TEXT NOT NULL UNIQUE,
  request_row_id INTEGER NOT NULL UNIQUE,
  request_ref TEXT NOT NULL UNIQUE,
  target_state TEXT NOT NULL,
  status INTEGER NOT NULL,
  turn_id TEXT,
  public_body_json TEXT NOT NULL,
  public_body_sha256 TEXT NOT NULL,
  provenance_kind TEXT NOT NULL,
  provenance_ref TEXT NOT NULL,
  provenance_sha256 TEXT NOT NULL,
  response_json TEXT NOT NULL,
  prepared_ts TEXT NOT NULL,
  CHECK(contract='PreparedHttpResponse/v1'),
  CHECK(response_ref GLOB 'http-response:*' AND length(response_ref)=78),
  CHECK(length(response_sha256)=64 AND lower(response_sha256)=response_sha256
    AND response_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(request_row_id)='integer' AND request_row_id>=1),
  CHECK(request_ref GLOB 'http-ingress:*' AND length(request_ref)=77),
  CHECK(target_state IN ('prepared','handoff','superseded')),
  CHECK(typeof(status)='integer' AND status>=200 AND status<=599),
  CHECK(turn_id IS NULL OR (length(turn_id)>=1 AND length(turn_id)<=180)),
  CHECK(json_valid(public_body_json)
    AND json_type(public_body_json,'$')='object'
    AND length(public_body_json)<=${MAX_PUBLIC_BODY_BYTES}),
  CHECK(length(public_body_sha256)=64 AND lower(public_body_sha256)=public_body_sha256
    AND public_body_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(provenance_kind IN ('turn_result','durable_handoff',
    'request_supersession','ingress_rejection')),
  CHECK(length(provenance_ref)>=8 AND length(provenance_ref)<=240
    AND provenance_ref NOT GLOB '*[^A-Za-z0-9._:-]*'),
  CHECK(length(provenance_sha256)=64 AND lower(provenance_sha256)=provenance_sha256
    AND provenance_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(response_json)
    AND json_type(response_json,'$')='object'
    AND length(response_json)<=${MAX_PUBLIC_BODY_BYTES + 2_048})
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_http_ingress_response_request
  ON eva_http_prepared_responses(request_row_id);
CREATE TRIGGER IF NOT EXISTS eva_http_prepared_responses_no_update
BEFORE UPDATE ON eva_http_prepared_responses
BEGIN SELECT RAISE(ABORT,'HTTP prepared responses are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_prepared_responses_no_delete
BEFORE DELETE ON eva_http_prepared_responses
BEGIN SELECT RAISE(ABORT,'HTTP prepared responses are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_prepared_responses_no_collision
BEFORE INSERT ON eva_http_prepared_responses
WHEN EXISTS (
  SELECT 1 FROM eva_http_prepared_responses
  WHERE id=NEW.id OR response_ref=NEW.response_ref
    OR response_sha256=NEW.response_sha256 OR request_row_id=NEW.request_row_id
    OR request_ref=NEW.request_ref
)
BEGIN SELECT RAISE(ABORT,'HTTP prepared response identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_prepared_responses_valid_reference
BEFORE INSERT ON eva_http_prepared_responses
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_http_ingress_requests
    WHERE id=NEW.request_row_id AND request_ref=NEW.request_ref
  ) THEN RAISE(ABORT,'HTTP prepared response has invalid request reference') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_http_ingress_state_events
    WHERE request_row_id=NEW.request_row_id AND request_ref=NEW.request_ref
      AND ordinal=0 AND state='started' AND response_ref IS NULL
  ) OR EXISTS (
    SELECT 1 FROM eva_http_ingress_state_events
    WHERE request_row_id=NEW.request_row_id AND ordinal=1
  ) THEN RAISE(ABORT,'HTTP prepared response has invalid request state') END;
  SELECT CASE WHEN
    (SELECT count(*) FROM json_each(NEW.response_json))<>10
    OR EXISTS (
      SELECT 1 FROM json_each(NEW.response_json)
      WHERE key NOT IN ('contract','channel','conversationId','intentSha256',
        'provenance','publicBody','requestId','state','status','turnId')
    )
    OR json_extract(NEW.response_json,'$.contract') IS NOT NEW.contract
    OR json_extract(NEW.response_json,'$.state') IS NOT NEW.target_state
    OR json_extract(NEW.response_json,'$.status') IS NOT NEW.status
    OR json_extract(NEW.response_json,'$.turnId') IS NOT NEW.turn_id
    OR json_extract(NEW.response_json,'$.publicBody') IS NOT json(NEW.public_body_json)
    OR json_extract(NEW.response_json,'$.provenance.contract')
      IS NOT 'HttpResultProvenance/v1'
    OR json_extract(NEW.response_json,'$.provenance.kind') IS NOT NEW.provenance_kind
    OR json_extract(NEW.response_json,'$.provenance.ref') IS NOT NEW.provenance_ref
    OR json_extract(NEW.response_json,'$.provenance.sha256') IS NOT NEW.provenance_sha256
    OR json_extract(NEW.response_json,'$.channel') IS NOT (
      SELECT channel FROM eva_http_ingress_requests WHERE id=NEW.request_row_id
    )
    OR json_extract(NEW.response_json,'$.conversationId') IS NOT (
      SELECT conversation_id FROM eva_http_ingress_requests WHERE id=NEW.request_row_id
    )
    OR json_extract(NEW.response_json,'$.requestId') IS NOT (
      SELECT request_id FROM eva_http_ingress_requests WHERE id=NEW.request_row_id
    )
    OR json_extract(NEW.response_json,'$.intentSha256') IS NOT (
      SELECT intent_sha256 FROM eva_http_ingress_requests WHERE id=NEW.request_row_id
    )
  THEN RAISE(ABORT,'HTTP prepared response body conflicts with columns') END;
  ${CURRENT_PUBLIC_BODY_TRIGGER_RULE}
END;

CREATE TABLE IF NOT EXISTS eva_http_ingress_state_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  event_ref TEXT NOT NULL UNIQUE,
  event_sha256 TEXT NOT NULL UNIQUE,
  request_row_id INTEGER NOT NULL,
  request_ref TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  state TEXT NOT NULL,
  response_ref TEXT,
  event_json TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  UNIQUE(request_row_id,ordinal),
  CHECK(contract='HttpIngressStateEvent/v1'),
  CHECK(event_ref GLOB 'http-ingress-event:*' AND length(event_ref)=83),
  CHECK(length(event_sha256)=64 AND lower(event_sha256)=event_sha256
    AND event_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(request_row_id)='integer' AND request_row_id>=1),
  CHECK(request_ref GLOB 'http-ingress:*' AND length(request_ref)=77),
  CHECK(ordinal IN (0,1)),
  CHECK(state IN ('started','prepared','handoff','superseded')),
  CHECK(response_ref IS NULL
    OR (response_ref GLOB 'http-response:*' AND length(response_ref)=78)),
  CHECK(json_valid(event_json) AND json_type(event_json,'$')='object')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_http_ingress_event_request
  ON eva_http_ingress_state_events(request_row_id,ordinal);
CREATE TRIGGER IF NOT EXISTS eva_http_ingress_state_events_no_update
BEFORE UPDATE ON eva_http_ingress_state_events
BEGIN SELECT RAISE(ABORT,'HTTP ingress state events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_ingress_state_events_no_delete
BEFORE DELETE ON eva_http_ingress_state_events
BEGIN SELECT RAISE(ABORT,'HTTP ingress state events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_ingress_state_events_no_collision
BEFORE INSERT ON eva_http_ingress_state_events
WHEN EXISTS (
  SELECT 1 FROM eva_http_ingress_state_events
  WHERE id=NEW.id OR event_ref=NEW.event_ref OR event_sha256=NEW.event_sha256
    OR (request_row_id=NEW.request_row_id AND ordinal=NEW.ordinal)
)
BEGIN SELECT RAISE(ABORT,'HTTP ingress state event identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_ingress_state_events_valid_transition
BEFORE INSERT ON eva_http_ingress_state_events
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_http_ingress_requests
    WHERE id=NEW.request_row_id AND request_ref=NEW.request_ref
  ) THEN RAISE(ABORT,'HTTP ingress state event has invalid request reference') END;
  SELECT CASE WHEN
    (SELECT count(*) FROM json_each(NEW.event_json))<>5
    OR EXISTS (
      SELECT 1 FROM json_each(NEW.event_json)
      WHERE key NOT IN ('contract','ordinal','requestRef','responseRef','state')
    )
    OR json_extract(NEW.event_json,'$.contract') IS NOT NEW.contract
    OR json_extract(NEW.event_json,'$.ordinal') IS NOT NEW.ordinal
    OR json_extract(NEW.event_json,'$.requestRef') IS NOT NEW.request_ref
    OR json_extract(NEW.event_json,'$.responseRef') IS NOT NEW.response_ref
    OR json_extract(NEW.event_json,'$.state') IS NOT NEW.state
  THEN RAISE(ABORT,'HTTP ingress state event body conflicts with columns') END;
  SELECT CASE WHEN NEW.state='started' AND (
    NEW.ordinal<>0 OR NEW.response_ref IS NOT NULL
    OR EXISTS (
      SELECT 1 FROM eva_http_ingress_state_events
      WHERE request_row_id=NEW.request_row_id
    )
  ) THEN RAISE(ABORT,'HTTP ingress started transition is invalid') END;
  SELECT CASE WHEN NEW.state<>'started' AND (
    NEW.ordinal<>1 OR NEW.response_ref IS NULL
    OR NOT EXISTS (
      SELECT 1 FROM eva_http_ingress_state_events
      WHERE request_row_id=NEW.request_row_id AND ordinal=0 AND state='started'
    )
    OR NOT EXISTS (
      SELECT 1 FROM eva_http_prepared_responses
      WHERE request_row_id=NEW.request_row_id AND request_ref=NEW.request_ref
        AND response_ref=NEW.response_ref AND target_state=NEW.state
    )
  ) THEN RAISE(ABORT,'HTTP ingress terminal transition is invalid') END;
END;

CREATE TABLE IF NOT EXISTS eva_http_ingress_claim_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  claim_event_ref TEXT NOT NULL UNIQUE,
  claim_event_sha256 TEXT NOT NULL UNIQUE,
  request_row_id INTEGER NOT NULL,
  request_ref TEXT NOT NULL,
  phase TEXT NOT NULL,
  generation INTEGER NOT NULL,
  sequence INTEGER NOT NULL,
  action TEXT NOT NULL,
  token_sha256 TEXT NOT NULL,
  lease_until_ts TEXT NOT NULL,
  event_json TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  UNIQUE(request_row_id,phase,generation,sequence),
  CHECK(contract='HttpIngressClaimEvent/v1'),
  CHECK(claim_event_ref GLOB 'http-claim-event:*' AND length(claim_event_ref)=81),
  CHECK(length(claim_event_sha256)=64 AND lower(claim_event_sha256)=claim_event_sha256
    AND claim_event_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(request_row_id)='integer' AND request_row_id>=1),
  CHECK(request_ref GLOB 'http-ingress:*' AND length(request_ref)=77),
  CHECK(phase IN ('execution','settlement')),
  CHECK(typeof(generation)='integer'
    AND generation>=1 AND generation<=${MAX_CLAIM_GENERATIONS}),
  CHECK(typeof(sequence)='integer'
    AND sequence>=0 AND sequence<=${MAX_CLAIM_SEQUENCE}),
  CHECK(action IN ('acquired','renewed','released')),
  CHECK(length(token_sha256)=64 AND lower(token_sha256)=token_sha256
    AND token_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(lease_until_ts)=24 AND substr(lease_until_ts,24,1)='Z'),
  CHECK(json_valid(event_json) AND json_type(event_json,'$')='object'),
  CHECK(length(created_ts)=24 AND substr(created_ts,24,1)='Z')
);
CREATE INDEX IF NOT EXISTS idx_http_ingress_claim_history
  ON eva_http_ingress_claim_events(request_row_id,phase,generation,sequence);
CREATE TRIGGER IF NOT EXISTS eva_http_ingress_claim_events_no_update
BEFORE UPDATE ON eva_http_ingress_claim_events
BEGIN SELECT RAISE(ABORT,'HTTP ingress claim events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_ingress_claim_events_no_delete
BEFORE DELETE ON eva_http_ingress_claim_events
BEGIN SELECT RAISE(ABORT,'HTTP ingress claim events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_ingress_claim_events_no_collision
BEFORE INSERT ON eva_http_ingress_claim_events
WHEN EXISTS (
  SELECT 1 FROM eva_http_ingress_claim_events
  WHERE id=NEW.id OR claim_event_ref=NEW.claim_event_ref
    OR claim_event_sha256=NEW.claim_event_sha256
    OR (request_row_id=NEW.request_row_id AND phase=NEW.phase
      AND generation=NEW.generation AND sequence=NEW.sequence)
)
BEGIN SELECT RAISE(ABORT,'HTTP ingress claim event identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_ingress_claim_events_valid_transition
BEFORE INSERT ON eva_http_ingress_claim_events
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_http_ingress_requests
    WHERE id=NEW.request_row_id AND request_ref=NEW.request_ref
  ) THEN RAISE(ABORT,'HTTP ingress claim event has invalid request reference') END;
  SELECT CASE WHEN
    (SELECT count(*) FROM json_each(NEW.event_json))<>8
    OR EXISTS (
      SELECT 1 FROM json_each(NEW.event_json)
      WHERE key NOT IN ('action','contract','generation','leaseUntil','phase',
        'requestRef','sequence','tokenSha256')
    )
    OR json_extract(NEW.event_json,'$.contract') IS NOT NEW.contract
    OR json_extract(NEW.event_json,'$.action') IS NOT NEW.action
    OR json_extract(NEW.event_json,'$.generation') IS NOT NEW.generation
    OR json_extract(NEW.event_json,'$.leaseUntil') IS NOT NEW.lease_until_ts
    OR json_extract(NEW.event_json,'$.phase') IS NOT NEW.phase
    OR json_extract(NEW.event_json,'$.requestRef') IS NOT NEW.request_ref
    OR json_extract(NEW.event_json,'$.sequence') IS NOT NEW.sequence
    OR json_extract(NEW.event_json,'$.tokenSha256') IS NOT NEW.token_sha256
  THEN RAISE(ABORT,'HTTP ingress claim event body conflicts with columns') END;
  SELECT CASE WHEN NEW.action='acquired' AND (
    NEW.sequence<>0
    OR NEW.generation<>COALESCE((
      SELECT max(generation)+1 FROM eva_http_ingress_claim_events
      WHERE request_row_id=NEW.request_row_id AND phase=NEW.phase
    ),1)
    OR NEW.lease_until_ts<=NEW.created_ts
    OR EXISTS (
      SELECT 1 FROM eva_http_ingress_claim_events current
      WHERE current.id=(
        SELECT id FROM eva_http_ingress_claim_events
        WHERE request_row_id=NEW.request_row_id AND phase=NEW.phase
        ORDER BY generation DESC,sequence DESC LIMIT 1
      ) AND current.action<>'released' AND current.lease_until_ts>NEW.created_ts
    )
  ) THEN RAISE(ABORT,'HTTP ingress claim acquisition is invalid') END;
  SELECT CASE WHEN NEW.action<>'acquired' AND (
    NEW.generation<>(
      SELECT max(generation) FROM eva_http_ingress_claim_events
      WHERE request_row_id=NEW.request_row_id AND phase=NEW.phase
    )
    OR NEW.sequence<>COALESCE((
      SELECT max(sequence)+1 FROM eva_http_ingress_claim_events
      WHERE request_row_id=NEW.request_row_id AND phase=NEW.phase
        AND generation=NEW.generation
    ),0)
    OR NOT EXISTS (
      SELECT 1 FROM eva_http_ingress_claim_events current
      WHERE current.id=(
        SELECT id FROM eva_http_ingress_claim_events
        WHERE request_row_id=NEW.request_row_id AND phase=NEW.phase
        ORDER BY generation DESC,sequence DESC LIMIT 1
      ) AND current.action<>'released' AND current.token_sha256=NEW.token_sha256
        AND ((NEW.action='renewed' AND current.lease_until_ts>NEW.created_ts
          AND NEW.lease_until_ts>current.lease_until_ts)
          OR (NEW.action='released' AND NEW.lease_until_ts=current.lease_until_ts))
    )
  ) THEN RAISE(ABORT,'HTTP ingress claim continuation is invalid') END;
END;

CREATE TABLE IF NOT EXISTS eva_http_client_ack_bindings_v3 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  request_row_id INTEGER NOT NULL UNIQUE,
  request_ref TEXT NOT NULL UNIQUE,
  intent_sha256 TEXT NOT NULL,
  intent_json TEXT NOT NULL,
  public_key_json TEXT NOT NULL,
  key_fingerprint TEXT NOT NULL,
  ack_challenge TEXT NOT NULL UNIQUE,
  ack_challenge_sha256 TEXT NOT NULL UNIQUE,
  binding_json TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  CHECK(contract='HttpClientAckBinding/v3'),
  CHECK(typeof(request_row_id)='integer' AND request_row_id>=1),
  CHECK(request_ref GLOB 'http-ingress:*' AND length(request_ref)=77),
  CHECK(length(intent_sha256)=64 AND lower(intent_sha256)=intent_sha256
    AND intent_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(intent_json) AND json_type(intent_json,'$')='object'
    AND length(CAST(intent_json AS BLOB))<=${MAX_INTENT_BYTES}),
  CHECK(json_valid(public_key_json) AND json_type(public_key_json,'$')='object'
    AND length(CAST(public_key_json AS BLOB))<=512),
  CHECK(key_fingerprint GLOB 'p256-spki-sha256:*' AND length(key_fingerprint)=81),
  CHECK(length(ack_challenge)=64 AND lower(ack_challenge)=ack_challenge
    AND ack_challenge NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(ack_challenge_sha256)=64
    AND lower(ack_challenge_sha256)=ack_challenge_sha256
    AND ack_challenge_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(binding_json) AND json_type(binding_json,'$')='object'),
  CHECK(length(created_ts)=24 AND substr(created_ts,24,1)='Z')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_http_client_ack_binding_request_v3
  ON eva_http_client_ack_bindings_v3(request_row_id,request_ref);
CREATE TRIGGER IF NOT EXISTS eva_http_client_ack_bindings_v3_no_update
BEFORE UPDATE ON eva_http_client_ack_bindings_v3
BEGIN SELECT RAISE(ABORT,'HTTP client ACK bindings are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_client_ack_bindings_v3_no_delete
BEFORE DELETE ON eva_http_client_ack_bindings_v3
BEGIN SELECT RAISE(ABORT,'HTTP client ACK bindings are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_client_ack_bindings_v3_no_replace
BEFORE INSERT ON eva_http_client_ack_bindings_v3
WHEN EXISTS (
  SELECT 1 FROM eva_http_client_ack_bindings_v3
  WHERE id=NEW.id OR request_row_id=NEW.request_row_id OR request_ref=NEW.request_ref
    OR ack_challenge=NEW.ack_challenge
    OR ack_challenge_sha256=NEW.ack_challenge_sha256
)
BEGIN SELECT RAISE(ABORT,'HTTP client ACK binding identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_client_ack_bindings_v3_valid_request
BEFORE INSERT ON eva_http_client_ack_bindings_v3
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_http_ingress_requests request
    WHERE request.id=NEW.request_row_id AND request.request_ref=NEW.request_ref
      AND request.intent_sha256=NEW.intent_sha256
  ) THEN RAISE(ABORT,'HTTP client ACK binding has invalid request') END;
  SELECT CASE WHEN
    json_extract(NEW.intent_json,'$.clientAckPublicKey') IS NOT json(NEW.public_key_json)
    OR (SELECT count(*) FROM json_each(NEW.binding_json))<>8
    OR json_extract(NEW.binding_json,'$.contract') IS NOT NEW.contract
    OR json_extract(NEW.binding_json,'$.requestRef') IS NOT NEW.request_ref
    OR json_extract(NEW.binding_json,'$.intentSha256') IS NOT NEW.intent_sha256
    OR json_extract(NEW.binding_json,'$.publicKey') IS NOT json(NEW.public_key_json)
    OR json_extract(NEW.binding_json,'$.keyFingerprint') IS NOT NEW.key_fingerprint
    OR json_extract(NEW.binding_json,'$.challenge') IS NOT NEW.ack_challenge
    OR json_extract(NEW.binding_json,'$.challengeSha256') IS NOT NEW.ack_challenge_sha256
    OR json_extract(NEW.binding_json,'$.algorithm') IS NOT 'ES256'
  THEN RAISE(ABORT,'HTTP client ACK binding conflicts with canonical body') END;
END;
`;

const PREPARATION_AUTHORITIES = new WeakMap();
const SETTLEMENT_AUTHORITIES = new WeakMap();
const VERIFIED_RESULT_AUTHORITIES = new WeakMap();
const DURABLE_RESPONSE_AUTHORITIES = new WeakMap();
let initializationFailure = '';

const hashBytes = (value) => createHash('sha256').update(value).digest('hex');
const nowIso = () => new Date(Date.now()).toISOString();

function normalizedDdl(value) {
  const source = String(value || '');
  const tokens = [];
  for (let index = 0; index < source.length;) {
    const char = source[index];
    if (/\s/u.test(char)) { index += 1; continue; }
    if (["'", '"', '`'].includes(char)) {
      const quote = char;
      let token = char;
      index += 1;
      while (index < source.length) {
        token += source[index];
        if (source[index] === quote) {
          if (source[index + 1] === quote) {
            token += source[index + 1];
            index += 2;
            continue;
          }
          index += 1;
          break;
        }
        index += 1;
      }
      tokens.push(token);
      continue;
    }
    if (char === '[') {
      const end = source.indexOf(']', index + 1);
      if (end < 0) return '';
      tokens.push(source.slice(index, end + 1));
      index = end + 1;
      continue;
    }
    const word = source.slice(index).match(/^[A-Za-z_][A-Za-z0-9_]*/u)?.[0];
    if (word) {
      tokens.push(word.toLowerCase());
      index += word.length;
      continue;
    }
    const number = source.slice(index).match(/^\d+(?:_\d+)*/u)?.[0];
    if (number) {
      tokens.push(number.replace(/_/gu, ''));
      index += number.length;
      continue;
    }
    const pair = source.slice(index, index + 2);
    if (['<>', '<=', '>=', '!=', '||'].includes(pair)) {
      tokens.push(pair);
      index += 2;
      continue;
    }
    tokens.push(char);
    index += 1;
  }
  const filtered = [];
  for (let index = 0; index < tokens.length; index += 1) {
    if (tokens[index] === 'if' && tokens[index + 1] === 'not'
      && tokens[index + 2] === 'exists') {
      index += 2;
      continue;
    }
    filtered.push(tokens[index]);
  }
  while (filtered.at(-1) === ';') filtered.pop();
  return filtered.join(' ');
}

function expectedDdl(type, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
  const kind = type === 'index' ? '(?:UNIQUE\\s+)?INDEX' : type.toUpperCase();
  const position = HTTP_INGRESS_SCHEMA_SQL.search(new RegExp(
    `CREATE\\s+${kind}\\s+IF\\s+NOT\\s+EXISTS\\s+${escaped}`, 'iu',
  ));
  if (position < 0) return '';
  const tail = HTTP_INGRESS_SCHEMA_SQL.slice(position);
  if (type !== 'trigger') return tail.slice(0, tail.indexOf(';') + 1);
  const next = tail.search(
    /\nCREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX|TRIGGER)\s+IF\s+NOT\s+EXISTS\s+/iu,
  );
  return (next < 0 ? tail : tail.slice(0, next)).trim();
}

const PREVIOUS_PUBLIC_BODY_TRIGGER_SUFFIX = `'runtimeCodeSha256','conversation_id','turn_id','request_id',
        'request_replay_scope','runtime_code_sha256','conversation_active',
        'continue_listening','listen_timeout_ms','execution_pending','durable_handoff'`;
const LEGACY_PUBLIC_BODY_TRIGGER_SUFFIX = `'runtimeCodeSha256'`;

function migrateExactPublicBodyTrigger() {
  if (hasCriticalTemporarySchemaObject() || hasUnexpectedMainNamespaceObject()) return;
  const name = 'eva_http_prepared_responses_valid_reference';
  const stored = ingressDb.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(name)?.sql;
  if (!stored) return;
  const expected = expectedDdl('trigger', name);
  const previous = expected.replace(
    CURRENT_PUBLIC_BODY_TRIGGER_RULE, PREVIOUS_PUBLIC_BODY_TRIGGER_RULE,
  );
  const legacy = previous.replace(
    PREVIOUS_PUBLIC_BODY_TRIGGER_SUFFIX, LEGACY_PUBLIC_BODY_TRIGGER_SUFFIX,
  );
  const storedDdl = normalizedDdl(stored);
  if (previous === expected || ![previous, legacy].some(
    (candidate) => candidate !== expected && normalizedDdl(candidate) === storedDdl,
  )) return;
  if (!exactSchemaExceptPublicBodyTrigger(name)) return;
  ingressDb.exec(`DROP TRIGGER main.${name};\n${expected}`);
}

function exactSchemaObject(type, name) {
  try {
    const stored = ingressDb.prepare(
      'SELECT sql FROM main.sqlite_master WHERE type=? AND name=?',
    ).get(type, name)?.sql;
    const expected = expectedDdl(type, name);
    return Boolean(stored && expected && normalizedDdl(stored) === normalizedDdl(expected));
  } catch { return false; }
}

function exactOwnedObjectNames(type, expected) {
  try {
    const tables = Object.keys(REQUIRED_COLUMNS);
    const placeholders = tables.map(() => '?').join(',');
    const rows = ingressDb.prepare(`SELECT name FROM main.sqlite_master
      WHERE type=? AND tbl_name IN (${placeholders})
        ${type === 'index' ? 'AND sql IS NOT NULL' : ''}
      ORDER BY name`).all(type, ...tables).map((row) => row.name);
    return canonicalJson(rows) === canonicalJson([...expected].sort());
  } catch { return false; }
}

function exactSchemaExceptPublicBodyTrigger(targetName) {
  try {
    for (const [table, expected] of Object.entries(REQUIRED_COLUMNS)) {
      const actual = ingressDb.prepare(`PRAGMA main.table_info('${table}')`).all()
        .map((column) => column.name);
      if (canonicalJson(actual) !== canonicalJson(expected)
        || !exactSchemaObject('table', table)
        || !exactSqliteIndexSignatures(
          ingressDb, table, expected, REQUIRED_INDEX_SIGNATURES[table],
        )) return false;
    }
    return exactOwnedObjectNames('index', REQUIRED_INDEXES)
      && REQUIRED_INDEXES.every((name) => exactSchemaObject('index', name))
      && exactOwnedObjectNames('trigger', REQUIRED_TRIGGERS)
      && REQUIRED_TRIGGERS.filter((name) => name !== targetName)
        .every((name) => exactSchemaObject('trigger', name));
  } catch { return false; }
}

function hasUnexpectedMainNamespaceObject() {
  try {
    const expected = new Set([
      ...OWNED_TABLES, ...REQUIRED_INDEXES, ...REQUIRED_TRIGGERS,
    ]);
    return ingressDb.prepare(`SELECT name FROM main.sqlite_master
      WHERE name GLOB 'eva_http_ingress*'
        OR name GLOB 'eva_http_prepared_response*'
        OR name GLOB 'eva_http_client_ack_binding*'
        OR name GLOB 'idx_http_ingress*'
        OR name GLOB 'idx_http_client_ack_binding*'
      ORDER BY name`).all().some(({ name }) => !expected.has(name));
  } catch { return true; }
}

function dependencyFailure() {
  if (!ingressDb || typeof ingressDb.prepare !== 'function'
    || typeof ingressDb.transaction !== 'function') return 'database_api';
  try {
    if (Number(ingressDb.prepare("SELECT json_valid('{}') AS valid").get()?.valid) !== 1) {
      return 'sqlite_json';
    }
    const expected = new Map([
      ['id', ['INTEGER', 0, 1]],
      ['ts', ['TEXT', 1, 0]],
      ['channel', ['TEXT', 1, 0]],
      ['conversation_id', ['TEXT', 0, 0]],
      ['sender', ['TEXT', 1, 0]],
      ['role', ['TEXT', 1, 0]],
      ['content', ['TEXT', 1, 0]],
    ]);
    const columns = ingressDb.prepare("PRAGMA main.table_info('messages')").all();
    if (columns.length !== expected.size) return 'messages_schema';
    for (const column of columns) {
      const signature = expected.get(column.name);
      if (!signature || String(column.type).toUpperCase() !== signature[0]
        || Number(column.notnull) !== signature[1] || Number(column.pk) !== signature[2]) {
        return 'messages_schema';
      }
    }
    const indexSql = ingressDb.prepare(
      "SELECT sql FROM main.sqlite_master WHERE type='index' AND name='idx_messages_conversation'",
    ).get()?.sql;
    if (normalizedDdl(indexSql)
      !== normalizedDdl('CREATE INDEX idx_messages_conversation ON messages(conversation_id,id)')) {
      return 'messages_index';
    }
    const ledger = executionLedgerStatus(ingressDb);
    if (!ledger.available) {
      return `execution_ledger:${'reason' in ledger ? ledger.reason : 'unavailable'}`;
    }
    const turnAuthority = turnUnderstandingAuthorityStoreStatus(ingressDb);
    if (!turnAuthority.available) {
      return `turn_understanding_authority:${
        'reason' in turnAuthority ? turnAuthority.reason : 'unavailable'
      }`;
    }
  } catch { return 'database_query'; }
  return '';
}

function hasCriticalTemporarySchemaObject() {
  try {
    const criticalTables = new Set(
      [...OWNED_TABLES, 'messages', 'turn_tool_executions',
        'eva_turn_understanding_authorities_v1', 'timeline',
        'eva_work_items', 'eva_goal_episodes', 'eva_episode_events',
        'eva_episode_origins', 'eva_episode_delivery_receipts']
        .map((name) => name.toLowerCase()),
    );
    return ingressDb.prepare(`SELECT type,name,tbl_name FROM sqlite_temp_master
      ORDER BY type,name`).all().some((row) => (
      String(row.name || '').toLowerCase().startsWith('eva_http_')
        || criticalTables.has(String(row.name || '').toLowerCase())
        || criticalTables.has(String(row.tbl_name || '').toLowerCase())
    ));
  } catch { return true; }
}

function mainSchemaVersion() {
  const version = Number(ingressDb.prepare('PRAGMA main.schema_version').get()?.schema_version);
  if (!Number.isSafeInteger(version) || version < 0) {
    throw new Error('HTTP ingress main schema version is unavailable');
  }
  return version;
}

const initialDependencyFailure = dependencyFailure();
if (initialDependencyFailure) {
  initializationFailure = `http_ingress_dependency_invalid:${initialDependencyFailure}`;
} else {
  try {
    ingressDb.transaction(() => {
      const present = OWNED_TABLES.filter((name) => ingressDb.prepare(`SELECT 1
        FROM main.sqlite_master WHERE type='table' AND name=?`).get(name));
      if (hasCriticalTemporarySchemaObject() || hasUnexpectedMainNamespaceObject()) {
        initializationFailure = 'http_ingress_schema_namespace_or_temp_invalid';
      } else if (!present.length) {
        ingressDb.exec(HTTP_INGRESS_SCHEMA_SQL);
      } else if (present.length !== OWNED_TABLES.length) {
        initializationFailure = 'http_ingress_schema_incomplete';
      } else {
        migrateExactPublicBodyTrigger();
      }
    }).immediate();
  } catch (error) {
    initializationFailure = String(error?.message || error || 'schema initialization failed');
  }
}

function schemaFailure() {
  if (hasCriticalTemporarySchemaObject()) return 'http_ingress_schema_temp_shadow';
  if (hasUnexpectedMainNamespaceObject()) return 'http_ingress_schema_namespace_invalid';
  const dependency = dependencyFailure();
  if (dependency) return `http_ingress_dependency_invalid:${dependency}`;
  if (initializationFailure) return 'http_ingress_schema_initialization_failed';
  for (const [table, expected] of Object.entries(REQUIRED_COLUMNS)) {
    let actual;
    try {
      actual = ingressDb.prepare(`PRAGMA main.table_info('${table}')`).all()
        .map((column) => column.name);
    } catch { return `http_ingress_schema_invalid:${table}`; }
    if (canonicalJson(actual) !== canonicalJson(expected)
      || !exactSchemaObject('table', table)) return `http_ingress_schema_invalid:${table}`;
    if (!exactSqliteIndexSignatures(
      ingressDb, table, expected, REQUIRED_INDEX_SIGNATURES[table],
    )) return `http_ingress_schema_index_signatures_invalid:${table}`;
  }
  if (!exactOwnedObjectNames('index', REQUIRED_INDEXES)
    || REQUIRED_INDEXES.some((name) => !exactSchemaObject('index', name))) {
    return 'http_ingress_schema_indexes_invalid';
  }
  if (!exactOwnedObjectNames('trigger', REQUIRED_TRIGGERS)
    || REQUIRED_TRIGGERS.some((name) => !exactSchemaObject('trigger', name))) {
    return 'http_ingress_schema_triggers_invalid';
  }
  return '';
}

export function httpIngressStoreStatus() {
  const reason = schemaFailure();
  return Object.freeze({
    contract: 'HttpIngressStoreStatus/v1', available: !reason,
    ...(reason ? { reason } : {}),
  });
}

function requireStore() {
  const status = httpIngressStoreStatus();
  if (!status.available) throw new Error(`HTTP ingress store unavailable: ${status.reason}`);
}

function plainObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}

function finiteJson(value, depth = 0) {
  if (depth > 12 || value === undefined) return false;
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (Array.isArray(value)) {
    return value.length <= 128 && value.every((child) => finiteJson(child, depth + 1));
  }
  if (!plainObject(value)) return false;
  const keys = Object.keys(value);
  return keys.length <= 128 && keys.every((key) => (
    key.length >= 1 && key.length <= 160 && finiteJson(value[key], depth + 1)
  ));
}

function canonicalIntent(value) {
  const voice = value?.route === '/api/voice';
  const hasClientAckKey = plainObject(value)
    && Object.hasOwn(value, 'clientAckPublicKey');
  const baseFields = voice
    ? [
      'contract', 'route', 'sender', 'rawText', 'conversationId',
      'shouldSpeak', 'speaker', 'syntheticTest',
    ]
    : ['contract', 'route', 'sender', 'rawText', 'conversationId', 'syntheticTest'];
  const fields = hasClientAckKey ? [...baseFields, 'clientAckPublicKey'] : baseFields;
  if (!exactKeys(value, fields) || value.contract !== 'PublicHttpIntent/v1'
    || !['/api/chat', '/api/voice'].includes(value.route)
    || typeof value.sender !== 'string' || !value.sender
    || value.sender !== value.sender.trim() || value.sender.length > 160
    || hasControlCharacters(value.sender)
    || typeof value.rawText !== 'string'
    || typeof value.conversationId !== 'string' || !value.conversationId
    || value.conversationId !== value.conversationId.trim()
    || value.conversationId.length > 180 || hasControlCharacters(value.conversationId)
    || typeof value.syntheticTest !== 'boolean'
    || (voice && (typeof value.shouldSpeak !== 'boolean'
      || !(value.speaker === null || (typeof value.speaker === 'string'
        && value.speaker.length <= 160 && !hasControlCharacters(value.speaker)))))
    || !finiteJson(value)) throw new TypeError('invalid canonical HTTP intent');
  let clientAck = null;
  if (hasClientAckKey) {
    clientAck = normalizeHttpClientAckPublicKey(value.clientAckPublicKey);
    if (canonicalJson(value.clientAckPublicKey) !== clientAck.publicKeyJson) {
      throw new TypeError('invalid canonical HTTP client ACK public key');
    }
  }
  const canonicalValue = hasClientAckKey
    ? { ...value, clientAckPublicKey: clientAck.publicKey } : value;
  const serialized = canonicalJson(canonicalValue);
  if (!serialized || Buffer.byteLength(serialized, 'utf8') > MAX_INTENT_BYTES) {
    throw new TypeError('invalid canonical HTTP intent');
  }
  return { serialized, sha256: hashBytes(serialized), clientAck };
}

export function canonicalHttpIntentSha256(intent) {
  return canonicalIntent(intent).sha256;
}

function exactKeys(value, fields) {
  return plainObject(value)
    && canonicalJson(Object.keys(value).sort()) === canonicalJson([...fields].sort());
}

function hasControlCharacters(value) {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code <= 31 || code === 127) return true;
  }
  return false;
}

function durableUnderstandingResult(requestRowValue) {
  const bindingRow = ingressDb.prepare(`SELECT * FROM ${mainTable(ACK_BINDING_TABLE)}
    WHERE request_row_id=? AND request_ref=?`).get(
    requestRowValue.id, requestRowValue.request_ref,
  );
  validateAckBinding(bindingRow, requestRowValue);
  let intent;
  try { intent = JSON.parse(bindingRow.intent_json); } catch { return null; }
  const identity = {
    channel: requestRowValue.channel,
    conversationId: requestRowValue.conversation_id,
    messageId: requestRowValue.request_id,
  };
  const executionKey = `turn-understanding:${hashBytes(canonicalJson(identity))}`;
  let execution;
  let args;
  let prepared;
  try {
    execution = validateExecutionRecord(ingressDb.prepare(`SELECT *
      FROM main.turn_tool_executions WHERE execution_key=? AND turn_id=?
        AND tool='@turn/understanding'`).get(
      executionKey,
      `ingress:${identity.channel}:${identity.conversationId}:${identity.messageId}`,
    ));
    args = JSON.parse(String(execution.args || 'null'));
    prepared = verifyPreparedUnderstandingTurn(
      JSON.parse(String(execution.output || 'null')), identity,
    );
  } catch { return null; }
  if (execution.status !== 'done' || !prepared
    || canonicalJson(prepared) !== execution.output
    || !plainObject(args)
    || args.channel !== identity.channel
    || args.conversationId !== identity.conversationId
    || String(args.messageId || '') !== identity.messageId
    || String(args.requester || '') !== intent.sender
    || String(args.originalText || '') !== String(intent.rawText || '').trim()
    || args.syntheticTest !== intent.syntheticTest
    || prepared.syntheticTest !== intent.syntheticTest) return null;
  return { prepared };
}

function runtimeResultMatchesPrepared(result, prepared) {
  if (!plainObject(result)) return false;
  const metadata = retainedTurnResultMetadata(result);
  return prepared.channel === String(result.channel || '')
    && prepared.conversationId === String(result.conversationId || '')
    && prepared.originMessageId === String(result.originMessageId || '')
    && prepared.turnId === String(result.turnId || '')
    && prepared.reply === String(result.reply || '')
    && Number(prepared.workItemId || 0) === Number(result.workItemId || 0)
    && Number(prepared.goalEpisodeId || 0) === Number(result.goalEpisodeId || 0)
    && String(prepared.finalDeliveryBindingSha256 || '')
      === String(result.finalDeliveryBindingSha256 || '')
    && String(prepared.error || '') === String(result.error || '')
    && prepared.syntheticTest === (result.syntheticTest === true)
    && canonicalJson(prepared.metadata) === canonicalJson(metadata);
}

function verifiedResultFromPrepared(requestRowValue, prepared) {
  const superseded = prepared.metadata.superseded === true;
  const pending = prepared.metadata.durableHandoff === true
    || prepared.metadata.executionPending === true;
  const failed = !pending && !superseded && Boolean(prepared.error);
  const publicError = normalizeUserFacingText(prepared.error).trim() || 'onbekende fout';
  const state = superseded ? 'superseded' : pending ? 'handoff' : 'prepared';
  const status = superseded ? 409 : pending ? 202 : failed ? 500 : 200;
  const turnId = superseded ? null : prepared.turnId;
  const apiIdentity = {
    conversation_id: requestRowValue.conversation_id,
    turn_id: turnId,
    request_id: requestRowValue.request_id,
    request_replay_scope: PUBLIC_REQUEST_REPLAY_SCOPE,
    runtime_code_sha256: runtimeVersion().loaded_code_sha256,
  };
  let publicBody;
  if (superseded) {
    publicBody = {
      error: 'Dit verzoek is door een nieuwer verzoek in hetzelfde gesprek vervangen.',
      ok: false, reason: 'request_superseded', ...apiIdentity,
    };
  } else if (pending) {
    publicBody = {
      reply: prepared.reply.trim()
        || 'De opdracht is veilig bewaard en wordt vervolgd.',
      ok: true, durable_handoff: true, execution_pending: true, ...apiIdentity,
    };
  } else if (failed) {
    publicBody = {
      error: publicError,
      ok: false,
      ...apiIdentity,
    };
  } else if (prepared.reply.trim()) {
    publicBody = { reply: prepared.reply.trim(), ok: true, ...apiIdentity };
  } else return null;
  if (requestRowValue.channel === 'voice') {
    const conversationActive = !superseded && !failed;
    Object.assign(publicBody, {
      conversation_active: conversationActive,
      continue_listening: conversationActive,
      listen_timeout_ms: conversationActive ? 30_000 : 0,
    });
  }
  const provenanceKind = superseded ? 'request_supersession'
    : pending ? 'durable_handoff' : 'turn_result';
  return verifiedResultAttestation({
    contract: 'VerifiedHttpResult/v1', channel: requestRowValue.channel,
    conversationId: requestRowValue.conversation_id,
    requestId: requestRowValue.request_id,
    intentSha256: requestRowValue.intent_sha256,
    state, status, turnId,
    publicBody,
    provenance: {
      contract: 'HttpResultProvenance/v1', kind: provenanceKind,
      ref: provenanceKind === 'turn_result' ? prepared.turnId
        : `${provenanceKind}:${prepared.sha256.slice(0, 40)}`,
      sha256: prepared.sha256,
    },
  });
}

export function issueVerifiedHttpResultAuthority(result, preparationCapability) {
  if (arguments.length !== 2) return null;
  requireStore();
  const schemaVersion = mainSchemaVersion();
  const preparation = preparationCapability && typeof preparationCapability === 'object'
    ? PREPARATION_AUTHORITIES.get(preparationCapability) : null;
  if (!preparation
    || preparationCapability.contract !== 'HttpIngressPreparationAuthority/v1'
    || preparation.phase !== 'execution') return null;
  let row;
  let claim;
  try {
    row = rowByAttestation(preparation);
    claim = currentClaimForAttestation(row, preparation);
  } catch { return null; }
  if (!claim || claim.claimEventRef !== preparation.claimEventRef) return null;
  const durable = durableUnderstandingResult(row);
  const verified = durable && runtimeResultMatchesPrepared(result, durable.prepared)
    ? verifiedResultFromPrepared(row, durable.prepared) : null;
  requireStore();
  if (mainSchemaVersion() !== schemaVersion) {
    throw new Error('HTTP ingress schema changed during result verification');
  }
  if (!verified) return null;
  const capability = Object.freeze({ contract: 'VerifiedHttpResultAuthority/v1' });
  VERIFIED_RESULT_AUTHORITIES.set(capability, deepFreeze({
    result: verified, requestRowId: Number(row.id), requestRef: row.request_ref,
    phase: claim.phase, generation: claim.generation,
    tokenSha256: claim.tokenSha256, claimEventRef: claim.claimEventRef,
  }));
  return capability;
}

function normalizedIdentity(value) {
  if (!exactKeys(value, ['channel', 'conversationId', 'requestId', 'intent'])) {
    throw new TypeError('invalid canonical HTTP ingress identity');
  }
  const channel = value.channel;
  const conversationId = typeof value.conversationId === 'string'
    ? value.conversationId : '';
  const requestId = typeof value.requestId === 'string' ? value.requestId : '';
  if (!['web', 'voice'].includes(channel)
    || !conversationId || conversationId !== conversationId.trim()
    || conversationId.length > 180 || hasControlCharacters(conversationId)
    || !REQUEST_ID.test(requestId)) {
    throw new TypeError('invalid canonical HTTP ingress identity');
  }
  const intent = canonicalIntent(value.intent);
  const expectedRoute = channel === 'voice' ? '/api/voice' : '/api/chat';
  if (value.intent.route !== expectedRoute
    || value.intent.conversationId !== conversationId) {
    throw new TypeError('invalid canonical HTTP ingress identity');
  }
  const identityBody = { channel, conversationId, requestId };
  const requestRef = `http-ingress:${hashBytes(canonicalJson(identityBody))}`;
  return Object.freeze({
    channel, conversationId, requestId, requestRef,
    intentSha256: intent.sha256,
    intentBytes: Buffer.byteLength(intent.serialized, 'utf8'),
    intentSerialized: intent.serialized, clientAck: intent.clientAck,
  });
}

function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value)) deepFreeze(child);
  }
  return value;
}

function requestReceipt(row) {
  if (!row || row.contract !== 'HttpIngressRequest/v1'
    || !Number.isSafeInteger(Number(row.id)) || Number(row.id) < 1
    || !['web', 'voice'].includes(row.channel)
    || typeof row.conversation_id !== 'string' || !row.conversation_id
    || !REQUEST_ID.test(row.request_id) || !SHA256.test(row.intent_sha256)
    || !Number.isSafeInteger(Number(row.intent_bytes)) || Number(row.intent_bytes) < 2
    || Number(row.intent_bytes) > MAX_INTENT_BYTES) {
    throw new Error('invalid durable HTTP ingress request');
  }
  const expectedRef = `http-ingress:${hashBytes(canonicalJson({
    channel: row.channel, conversationId: row.conversation_id, requestId: row.request_id,
  }))}`;
  if (row.request_ref !== expectedRef || !Number.isFinite(Date.parse(row.started_ts))) {
    throw new Error('invalid durable HTTP ingress request');
  }
  return Object.freeze({
    contract: row.contract, requestRef: row.request_ref, channel: row.channel,
    conversationId: row.conversation_id, requestId: row.request_id,
    intentSha256: row.intent_sha256, intentBytes: Number(row.intent_bytes),
    startedAt: row.started_ts,
  });
}

/**
 * @typedef {{
 *   reply?: unknown,
 *   error?: unknown,
 *   reason?: unknown,
 *   emotion?: unknown,
 *   ok?: unknown,
 *   conversationActive?: unknown,
 *   continueListening?: unknown,
 *   listenTimeoutMs?: unknown,
 *   executionPending?: unknown,
 *   durableHandoff?: unknown,
 *   runtimeCodeSha256?: unknown,
 *   conversation_active?: unknown,
 *   continue_listening?: unknown,
 *   listen_timeout_ms?: unknown,
 *   execution_pending?: unknown,
 *   durable_handoff?: unknown,
 *   runtime_code_sha256?: unknown,
 *   conversation_id?: unknown,
 *   request_id?: unknown,
 *   request_replay_scope?: unknown,
 *   turn_id?: unknown,
 * }} PublicBodyShape
 *
 * @typedef {'ok' | 'conversationActive' | 'continueListening' | 'executionPending' | 'durableHandoff' | 'conversation_active' | 'continue_listening' | 'execution_pending' | 'durable_handoff'} PublicBooleanMetadataKey
 *
 * @typedef {'listenTimeoutMs' | 'listen_timeout_ms'} PublicTimeoutMetadataKey
 *
 * @typedef {'runtimeCodeSha256' | 'runtime_code_sha256'} PublicRuntimeHashKey
 */

/**
 * @param {PublicBodyShape} value
 * @param {boolean} hasReply
 * @param {boolean} hasError
 */
function invalidPublicBodyContent(value, hasReply, hasError) {
  return hasReply === hasError
    || (hasReply && (typeof value.reply !== 'string' || !value.reply
      || value.reply.length > MAX_REPLY_LENGTH))
    || (hasError && (typeof value.error !== 'string' || !value.error
      || value.error.length > MAX_ERROR_LENGTH))
    || (Object.hasOwn(value, 'reason')
      && (typeof value.reason !== 'string' || !REASON.test(value.reason)))
    || (Object.hasOwn(value, 'emotion')
      && (typeof value.emotion !== 'string' || !value.emotion || value.emotion.length > 80));
}

/** @param {PublicBodyShape} value */
function invalidPublicBodyScalarMetadata(value) {
  /** @type {readonly PublicBooleanMetadataKey[]} */
  const booleanKeys = [
    'ok', 'conversationActive', 'continueListening', 'executionPending',
    'durableHandoff', 'conversation_active', 'continue_listening',
    'execution_pending', 'durable_handoff',
  ];
  /** @type {readonly PublicTimeoutMetadataKey[]} */
  const timeoutKeys = ['listenTimeoutMs', 'listen_timeout_ms'];
  /** @type {readonly PublicRuntimeHashKey[]} */
  const runtimeHashKeys = ['runtimeCodeSha256', 'runtime_code_sha256'];
  return booleanKeys.some((key) => (
    Object.hasOwn(value, key) && typeof value[key] !== 'boolean'
  ))
    || timeoutKeys.some((key) => {
      const candidate = value[key];
      return Object.hasOwn(value, key) && (typeof candidate !== 'number'
        || !Number.isSafeInteger(candidate) || candidate < 0 || candidate > 120_000);
    })
    || runtimeHashKeys.some((key) => (
      Object.hasOwn(value, key) && !SHA256.test(String(value[key] || ''))
    ));
}

/** @param {PublicBodyShape} value */
function invalidPublicBodyIdentity(value) {
  return (Object.hasOwn(value, 'conversation_id')
    && (typeof value.conversation_id !== 'string' || !value.conversation_id
      || value.conversation_id !== value.conversation_id.trim()
      || value.conversation_id.length > 180
      || hasControlCharacters(value.conversation_id)))
    || (Object.hasOwn(value, 'request_id')
      && !REQUEST_ID.test(String(value.request_id)))
    || (Object.hasOwn(value, 'request_replay_scope')
      && value.request_replay_scope !== PUBLIC_REQUEST_REPLAY_SCOPE)
    || (Object.hasOwn(value, 'turn_id')
      && !(value.turn_id === null || (typeof value.turn_id === 'string'
        && value.turn_id && value.turn_id === value.turn_id.trim()
        && value.turn_id.length <= 180 && !hasControlCharacters(value.turn_id))));
}

function publicBody(value, { allowHistorical = false } = {}) {
  if (!plainObject(value)) throw new TypeError('invalid canonical public HTTP response');
  const keys = Object.keys(value);
  const currentIdentityKeys = [
    'conversation_id', 'turn_id', 'request_id', 'request_replay_scope',
    'runtime_code_sha256',
  ];
  const current = keys.length > 0
    && keys.every((key) => CURRENT_PUBLIC_BODY_KEYS.has(key))
    && currentIdentityKeys.every((key) => Object.hasOwn(value, key));
  const historical = allowHistorical && keys.length > 0
    && keys.every((key) => LEGACY_PUBLIC_BODY_KEYS.has(key));
  if (!current && !historical) {
    throw new TypeError('invalid canonical public HTTP response');
  }
  const hasReply = Object.hasOwn(value, 'reply');
  const hasError = Object.hasOwn(value, 'error');
  if (invalidPublicBodyContent(value, hasReply, hasError)
    || invalidPublicBodyScalarMetadata(value)
    || invalidPublicBodyIdentity(value)) {
    throw new TypeError('invalid canonical public HTTP response');
  }
  const serialized = canonicalJson(value);
  if (Buffer.byteLength(serialized, 'utf8') > MAX_PUBLIC_BODY_BYTES) {
    throw new TypeError('invalid canonical public HTTP response');
  }
  return {
    kind: current ? 'current_public_api' : 'historical_public_body',
    value: deepFreeze(JSON.parse(serialized)), serialized, sha256: hashBytes(serialized),
  };
}

function exactPublicApiIdentity(body, requestRow, turnId) {
  const keys = [
    'conversation_id', 'turn_id', 'request_id', 'request_replay_scope',
    'runtime_code_sha256',
  ];
  const identity = keys.every((key) => Object.hasOwn(body.value, key))
    && body.value.conversation_id === requestRow.conversation_id
    && body.value.turn_id === turnId
    && body.value.request_id === requestRow.request_id
    && body.value.request_replay_scope === PUBLIC_REQUEST_REPLAY_SCOPE
    && SHA256.test(body.value.runtime_code_sha256);
  const voiceKeys = ['conversation_active', 'continue_listening', 'listen_timeout_ms'];
  if (!identity) return false;
  if (requestRow.channel === 'web') {
    return voiceKeys.every((key) => !Object.hasOwn(body.value, key));
  }
  return typeof body.value.conversation_active === 'boolean'
    && typeof body.value.continue_listening === 'boolean'
    && Number.isSafeInteger(body.value.listen_timeout_ms)
    && body.value.listen_timeout_ms >= 0 && body.value.listen_timeout_ms <= 120_000;
}

function normalizedProvenance(value) {
  if (!exactKeys(value, ['contract', 'kind', 'ref', 'sha256'])
    || value.contract !== 'HttpResultProvenance/v1'
    || !['turn_result', 'durable_handoff', 'request_supersession', 'ingress_rejection']
      .includes(value.kind)
    || typeof value.ref !== 'string'
    || !/^[A-Za-z0-9][A-Za-z0-9._:-]{7,239}$/u.test(value.ref)
    || !SHA256.test(value.sha256)) return null;
  return Object.freeze({
    contract: value.contract, kind: value.kind, ref: value.ref, sha256: value.sha256,
  });
}

function invalidPreparedResponseHeader(state, status, turnId) {
  return !TERMINAL_STATES.has(state)
    || typeof status !== 'number' || !Number.isSafeInteger(status)
    || status < 200 || status > 599
    || !(turnId === null || (typeof turnId === 'string' && turnId
      && turnId === turnId.trim() && turnId.length <= 180
      && !hasControlCharacters(turnId)));
}

function invalidPreparedOutcome({
  state, status, turnId, body, provenance, reply, error,
  successfulPrepared, failedPrepared, preparedProvenance,
}) {
  return (state === 'prepared' && !successfulPrepared && !failedPrepared)
    || (state === 'prepared' && [204, 205].includes(status))
    || (successfulPrepared && (status === 202 || !reply || error
      || body.value.ok !== true || !turnId || provenance.kind !== 'turn_result'
      || provenance.ref !== turnId))
    || (failedPrepared && (!error || reply || body.value.ok !== false
      || !preparedProvenance));
}

function invalidHandoffOutcome({ state, status, turnId, body, provenance, reply, error }) {
  const durableHandoff = body.value.durable_handoff === true
    || body.value.durableHandoff === true;
  const executionPending = body.value.execution_pending === true
    || body.value.executionPending === true;
  return (state === 'handoff' && (status !== 202 || !reply || error
    || body.value.ok !== true || !turnId
    || !durableHandoff || !executionPending
    || provenance.kind !== 'durable_handoff'))
    || (state !== 'handoff' && status === 202)
    || (state !== 'handoff' && (durableHandoff || executionPending));
}

function invalidSupersededOutcome({ state, status, turnId, body, provenance, reply, error }) {
  return (state === 'superseded' && (status !== 409 || !error || reply
    || body.value.ok !== false || turnId !== null
    || body.value.reason !== 'request_superseded'
    || provenance.kind !== 'request_supersession'))
    || (state !== 'superseded' && body.value.reason === 'request_superseded');
}

function normalizedPreparedResponse(value, requestRow, { allowHistorical = false } = {}) {
  if (!exactKeys(value, ['state', 'status', 'turnId', 'publicBody', 'provenance'])) {
    throw new TypeError('invalid canonical public HTTP response');
  }
  const state = value.state;
  const status = value.status;
  const turnId = value.turnId;
  if (invalidPreparedResponseHeader(state, status, turnId)) {
    throw new TypeError('invalid canonical public HTTP response');
  }
  const body = publicBody(value.publicBody, { allowHistorical });
  const provenance = normalizedProvenance(value.provenance);
  const reply = Object.hasOwn(body.value, 'reply');
  const error = Object.hasOwn(body.value, 'error');
  const successfulPrepared = state === 'prepared' && status >= 200 && status <= 299;
  const failedPrepared = state === 'prepared' && status >= 400 && status <= 599;
  const preparedProvenance = turnId === null
    ? provenance?.kind === 'ingress_rejection'
    : provenance?.kind === 'turn_result' && provenance.ref === turnId;
  const publicIdentityValid = body.kind === 'current_public_api'
    ? exactPublicApiIdentity(body, requestRow, turnId)
    : allowHistorical && body.kind === 'historical_public_body';
  if (!provenance || !publicIdentityValid
    || invalidPreparedOutcome({
      state, status, turnId, body, provenance, reply, error,
      successfulPrepared, failedPrepared, preparedProvenance,
    })
    || invalidHandoffOutcome({ state, status, turnId, body, provenance, reply, error })
    || invalidSupersededOutcome({ state, status, turnId, body, provenance, reply, error })) {
    throw new TypeError('invalid canonical public HTTP response');
  }
  const responseBody = {
    contract: 'PreparedHttpResponse/v1',
    channel: requestRow.channel,
    conversationId: requestRow.conversation_id,
    intentSha256: requestRow.intent_sha256,
    provenance,
    publicBody: body.value,
    requestId: requestRow.request_id,
    state,
    status,
    turnId,
  };
  const serialized = canonicalJson(responseBody);
  const responseSha256 = hashBytes(serialized);
  return {
    state, status, turnId, body, provenance, responseBody, serialized, responseSha256,
    responseRef: `http-response:${responseSha256}`,
  };
}

function verifiedResultAttestation(value) {
  if (!exactKeys(value, [
    'contract', 'channel', 'conversationId', 'requestId', 'intentSha256',
    'state', 'status', 'turnId', 'publicBody', 'provenance',
  ]) || value.contract !== 'VerifiedHttpResult/v1'
    || !['web', 'voice'].includes(value.channel)
    || typeof value.conversationId !== 'string' || !value.conversationId
    || value.conversationId !== value.conversationId.trim()
    || value.conversationId.length > 180 || hasControlCharacters(value.conversationId)
    || !REQUEST_ID.test(value.requestId) || !SHA256.test(value.intentSha256)) return null;
  const requestRow = {
    channel: value.channel, conversation_id: value.conversationId,
    request_id: value.requestId, intent_sha256: value.intentSha256,
  };
  try {
    const normalized = normalizedPreparedResponse({
      state: value.state, status: value.status, turnId: value.turnId,
      publicBody: value.publicBody, provenance: value.provenance,
    }, requestRow);
    return deepFreeze({
      contract: value.contract, channel: value.channel,
      conversationId: value.conversationId, requestId: value.requestId,
      intentSha256: value.intentSha256, state: normalized.state,
      status: normalized.status, turnId: normalized.turnId,
      publicBody: normalized.body.value, provenance: normalized.provenance,
    });
  } catch { return null; }
}

function responseReceipt(row, requestRow) {
  if (!row || row.contract !== 'PreparedHttpResponse/v1'
    || !SHA256.test(row.response_sha256) || !SHA256.test(row.public_body_sha256)
    || !SHA256.test(row.provenance_sha256)
    || row.response_ref !== `http-response:${row.response_sha256}`
    || Number(row.request_row_id) !== Number(requestRow.id)
    || row.request_ref !== requestRow.request_ref
    || !TERMINAL_STATES.has(row.target_state)
    || !Number.isSafeInteger(Number(row.status))
    || !Number.isFinite(Date.parse(row.prepared_ts))) {
    throw new Error('invalid durable prepared HTTP response');
  }
  let body;
  let storedResponse;
  try {
    body = publicBody(JSON.parse(row.public_body_json), { allowHistorical: true });
    storedResponse = JSON.parse(row.response_json);
  } catch { throw new Error('invalid durable prepared HTTP response'); }
  const normalized = normalizedPreparedResponse({
    state: row.target_state, status: Number(row.status), turnId: row.turn_id,
    publicBody: body.value,
    provenance: {
      contract: 'HttpResultProvenance/v1', kind: row.provenance_kind,
      ref: row.provenance_ref, sha256: row.provenance_sha256,
    },
  }, requestRow, { allowHistorical: true });
  if (canonicalJson(body.value) !== row.public_body_json
    || body.sha256 !== row.public_body_sha256
    || canonicalJson(storedResponse) !== row.response_json
    || normalized.serialized !== row.response_json
    || normalized.responseSha256 !== row.response_sha256
    || normalized.responseRef !== row.response_ref) {
    throw new Error('invalid durable prepared HTTP response');
  }
  return deepFreeze({
    ...normalized.responseBody, responseRef: row.response_ref,
    sha256: row.response_sha256, preparedAt: row.prepared_ts,
  });
}

function storedPublicBodyKind(row) {
  try {
    return publicBody(JSON.parse(row.public_body_json), { allowHistorical: true }).kind;
  } catch { throw new Error('invalid durable prepared HTTP response'); }
}

function eventBody({ requestRef, ordinal, state, responseRef }) {
  return {
    contract: 'HttpIngressStateEvent/v1', ordinal, requestRef,
    responseRef: responseRef || null, state,
  };
}

function eventValues(body) {
  const serialized = canonicalJson(body);
  const eventSha256 = hashBytes(serialized);
  return {
    serialized, eventSha256, eventRef: `http-ingress-event:${eventSha256}`,
  };
}

function validateEvent(row, requestRow) {
  if (!row || row.contract !== 'HttpIngressStateEvent/v1'
    || !SHA256.test(row.event_sha256)
    || row.event_ref !== `http-ingress-event:${row.event_sha256}`
    || Number(row.request_row_id) !== Number(requestRow.id)
    || row.request_ref !== requestRow.request_ref
    || !Number.isFinite(Date.parse(row.created_ts))) {
    throw new Error('invalid durable HTTP ingress state event');
  }
  const expected = eventBody({
    requestRef: requestRow.request_ref, ordinal: Number(row.ordinal), state: row.state,
    responseRef: row.response_ref,
  });
  const values = eventValues(expected);
  if (values.serialized !== row.event_json || values.eventSha256 !== row.event_sha256
    || values.eventRef !== row.event_ref
    || (row.state === 'started' && (Number(row.ordinal) !== 0 || row.response_ref !== null))
    || (row.state !== 'started'
      && (!TERMINAL_STATES.has(row.state) || Number(row.ordinal) !== 1
        || typeof row.response_ref !== 'string'))) {
    throw new Error('invalid durable HTTP ingress state event');
  }
  return row.state;
}

function requestRow(identity) {
  return ingressDb.prepare(`SELECT * FROM ${mainTable(REQUEST_TABLE)}
    WHERE channel=? AND conversation_id=? AND request_id=?`).get(
    identity.channel, identity.conversationId, identity.requestId,
  );
}

function ackBindingBody({ requestRef, intentSha256, publicKey, keyFingerprint,
  challenge, challengeSha256 }) {
  return {
    algorithm: 'ES256', challenge, challengeSha256,
    contract: 'HttpClientAckBinding/v3', intentSha256, keyFingerprint,
    publicKey, requestRef,
  };
}

function validateAckBinding(row, requestRowValue) {
  if (!row || row.contract !== 'HttpClientAckBinding/v3'
    || Number(row.request_row_id) !== Number(requestRowValue.id)
    || row.request_ref !== requestRowValue.request_ref
    || row.intent_sha256 !== requestRowValue.intent_sha256
    || !SHA256.test(row.ack_challenge)
    || hashBytes(row.ack_challenge) !== row.ack_challenge_sha256
    || !Number.isFinite(Date.parse(row.created_ts))) {
    throw new Error('invalid durable HTTP client ACK binding');
  }
  let intent;
  let publicKey;
  let stored;
  try {
    intent = JSON.parse(row.intent_json);
    publicKey = normalizeHttpClientAckPublicKey(JSON.parse(row.public_key_json));
    stored = JSON.parse(row.binding_json);
  } catch { throw new Error('invalid durable HTTP client ACK binding'); }
  const expected = ackBindingBody({
    requestRef: row.request_ref, intentSha256: row.intent_sha256,
    publicKey: publicKey.publicKey, keyFingerprint: publicKey.keyFingerprint,
    challenge: row.ack_challenge, challengeSha256: row.ack_challenge_sha256,
  });
  if (canonicalJson(intent) !== row.intent_json
    || hashBytes(row.intent_json) !== row.intent_sha256
    || canonicalJson(intent.clientAckPublicKey) !== row.public_key_json
    || publicKey.publicKeyJson !== row.public_key_json
    || publicKey.keyFingerprint !== row.key_fingerprint
    || canonicalJson(stored) !== row.binding_json
    || canonicalJson(expected) !== row.binding_json) {
    throw new Error('invalid canonical durable HTTP client ACK binding');
  }
  return deepFreeze({ ...expected, createdAt: row.created_ts });
}

function ackBindingRow(requestRowValue) {
  const row = ingressDb.prepare(`SELECT * FROM ${mainTable(ACK_BINDING_TABLE)}
    WHERE request_row_id=? AND request_ref=?`).get(
    requestRowValue.id, requestRowValue.request_ref,
  );
  return row ? validateAckBinding(row, requestRowValue) : null;
}

function insertAckBinding(requestRowValue, identity) {
  if (!identity.clientAck) return null;
  const challenge = randomBytes(32).toString('hex');
  const challengeSha256 = hashBytes(challenge);
  const body = ackBindingBody({
    requestRef: requestRowValue.request_ref, intentSha256: identity.intentSha256,
    publicKey: identity.clientAck.publicKey,
    keyFingerprint: identity.clientAck.keyFingerprint,
    challenge, challengeSha256,
  });
  const createdAt = nowIso();
  ingressDb.prepare(`INSERT INTO ${mainTable(ACK_BINDING_TABLE)} (
    contract,request_row_id,request_ref,intent_sha256,intent_json,public_key_json,
    key_fingerprint,ack_challenge,ack_challenge_sha256,binding_json,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
    body.contract, requestRowValue.id, requestRowValue.request_ref,
    identity.intentSha256, identity.intentSerialized, identity.clientAck.publicKeyJson,
    identity.clientAck.keyFingerprint, challenge, challengeSha256,
    canonicalJson(body), createdAt,
  );
  return ackBindingRow(requestRowValue);
}

function responseRow(requestRowValue) {
  return ingressDb.prepare(`SELECT * FROM ${mainTable(RESPONSE_TABLE)} WHERE request_row_id=?`)
    .get(requestRowValue.id);
}

function requestLifecycle(requestRowValue) {
  const events = ingressDb.prepare(`SELECT * FROM ${mainTable(EVENT_TABLE)}
    WHERE request_row_id=? ORDER BY ordinal`).all(requestRowValue.id);
  if (!events.length || events.length > 2
    || validateEvent(events[0], requestRowValue) !== 'started') {
    throw new Error('invalid durable HTTP ingress lifecycle');
  }
  const response = responseRow(requestRowValue);
  const terminal = events[1] || null;
  if (terminal) {
    const state = validateEvent(terminal, requestRowValue);
    if (!response || terminal.response_ref !== response.response_ref
      || response.target_state !== state) {
      throw new Error('invalid durable HTTP ingress lifecycle');
    }
    return { state, response };
  }
  return { state: 'started', response };
}

function earlierOpenLaneRequest(requestRowValue) {
  const row = ingressDb.prepare(`SELECT prior.* FROM ${mainTable(REQUEST_TABLE)} prior
    WHERE prior.channel=? AND prior.conversation_id=? AND prior.id<?
      AND EXISTS (
        SELECT 1 FROM ${mainTable(ACK_BINDING_TABLE)} binding
        WHERE binding.request_row_id=prior.id AND binding.request_ref=prior.request_ref
      )
      AND NOT EXISTS (
        SELECT 1 FROM ${mainTable(EVENT_TABLE)} terminal
        WHERE terminal.request_row_id=prior.id AND terminal.ordinal=1
      )
    ORDER BY prior.id LIMIT 1`).get(
    requestRowValue.channel, requestRowValue.conversation_id, requestRowValue.id,
  );
  if (!row) return null;
  requestReceipt(row);
  const lifecycle = requestLifecycle(row);
  return lifecycle.state === 'started' ? row : null;
}

function insertEvent(requestRowValue, state, responseRef = null, ordinal = 0) {
  const body = eventBody({
    requestRef: requestRowValue.request_ref, ordinal, state, responseRef,
  });
  const values = eventValues(body);
  const result = ingressDb.prepare(`INSERT INTO ${mainTable(EVENT_TABLE)} (
    contract,event_ref,event_sha256,request_row_id,request_ref,ordinal,state,
    response_ref,event_json,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
    body.contract, values.eventRef, values.eventSha256, requestRowValue.id,
    requestRowValue.request_ref, ordinal, state, responseRef,
    values.serialized, nowIso(),
  );
  if (Number(result.changes) !== 1) {
    throw new Error('HTTP ingress state event insert did not change exactly one row');
  }
  const stored = ingressDb.prepare(`SELECT * FROM ${mainTable(EVENT_TABLE)} WHERE event_ref=?`)
    .get(values.eventRef);
  validateEvent(stored, requestRowValue);
  return stored;
}

function claimEventBody({
  requestRef, phase, generation, sequence, action, tokenSha256, leaseUntil,
}) {
  return {
    action, contract: 'HttpIngressClaimEvent/v1', generation, leaseUntil,
    phase, requestRef, sequence, tokenSha256,
  };
}

function claimEventValues(body) {
  const serialized = canonicalJson(body);
  const claimEventSha256 = hashBytes(serialized);
  return {
    serialized, claimEventSha256,
    claimEventRef: `http-claim-event:${claimEventSha256}`,
  };
}

function canonicalIso(value) {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString() === value;
}

function validateClaimEvent(row, requestRowValue) {
  if (!row || row.contract !== 'HttpIngressClaimEvent/v1'
    || !SHA256.test(row.claim_event_sha256) || !SHA256.test(row.token_sha256)
    || row.claim_event_ref !== `http-claim-event:${row.claim_event_sha256}`
    || Number(row.request_row_id) !== Number(requestRowValue.id)
    || row.request_ref !== requestRowValue.request_ref
    || !CLAIM_PHASES.has(row.phase) || !CLAIM_ACTIONS.has(row.action)
    || !Number.isSafeInteger(Number(row.generation)) || Number(row.generation) < 1
    || !Number.isSafeInteger(Number(row.sequence)) || Number(row.sequence) < 0
    || !canonicalIso(row.lease_until_ts) || !canonicalIso(row.created_ts)) {
    throw new Error('invalid durable HTTP ingress claim event');
  }
  const body = claimEventBody({
    requestRef: row.request_ref, phase: row.phase,
    generation: Number(row.generation), sequence: Number(row.sequence),
    action: row.action, tokenSha256: row.token_sha256,
    leaseUntil: row.lease_until_ts,
  });
  const values = claimEventValues(body);
  if (values.serialized !== row.event_json
    || values.claimEventSha256 !== row.claim_event_sha256
    || values.claimEventRef !== row.claim_event_ref) {
    throw new Error('invalid durable HTTP ingress claim event');
  }
  return Object.freeze({
    id: Number(row.id), requestRowId: Number(row.request_row_id),
    requestRef: row.request_ref, phase: row.phase,
    generation: Number(row.generation), sequence: Number(row.sequence),
    action: row.action, tokenSha256: row.token_sha256,
    leaseUntil: row.lease_until_ts, createdAt: row.created_ts,
    claimEventRef: row.claim_event_ref,
  });
}

function claimHistory(requestRowValue, phase) {
  if (!CLAIM_PHASES.has(phase)) throw new Error('invalid HTTP ingress claim phase');
  const rows = ingressDb.prepare(`SELECT * FROM ${mainTable(CLAIM_EVENT_TABLE)}
    WHERE request_row_id=? AND phase=? ORDER BY generation,sequence
    LIMIT ${MAX_CLAIM_EVENTS_PER_PHASE + 1}`)
    .all(requestRowValue.id, phase);
  if (rows.length > MAX_CLAIM_EVENTS_PER_PHASE) {
    throw new Error('durable HTTP ingress claim history bound exceeded');
  }
  let current = null;
  for (const row of rows) {
    const event = validateClaimEvent(row, requestRowValue);
    if (event.action === 'acquired') {
      if (event.sequence !== 0 || event.generation !== (current?.generation || 0) + 1
        || Date.parse(event.leaseUntil) <= Date.parse(event.createdAt)
        || (current && current.action !== 'released'
          && Date.parse(current.leaseUntil) > Date.parse(event.createdAt))) {
        throw new Error('invalid durable HTTP ingress claim history');
      }
    } else if (!current || current.action === 'released'
      || event.generation !== current.generation
      || event.sequence !== current.sequence + 1
      || event.tokenSha256 !== current.tokenSha256
      || (event.action === 'renewed' && (Date.parse(current.leaseUntil) <= Date.parse(event.createdAt)
        || Date.parse(event.leaseUntil) <= Date.parse(current.leaseUntil)))
      || (event.action === 'released' && event.leaseUntil !== current.leaseUntil)) {
      throw new Error('invalid durable HTTP ingress claim history');
    }
    current = event;
  }
  return current;
}

function claimReceipt(claim) {
  if (!claim) return null;
  return Object.freeze({
    contract: 'HttpIngressClaimReceipt/v1', phase: claim.phase,
    generation: claim.generation, sequence: claim.sequence,
    leaseUntil: claim.leaseUntil, claimEventRef: claim.claimEventRef,
  });
}

function insertClaimEvent(requestRowValue, input, createdAt = nowIso()) {
  if (!Number.isSafeInteger(input.generation) || input.generation < 1
    || input.generation > MAX_CLAIM_GENERATIONS
    || !Number.isSafeInteger(input.sequence) || input.sequence < 0
    || input.sequence > MAX_CLAIM_SEQUENCE) {
    throw new Error('HTTP ingress claim history bound exceeded');
  }
  const count = Number(ingressDb.prepare(`SELECT count(*) AS count
    FROM ${mainTable(CLAIM_EVENT_TABLE)} WHERE request_row_id=? AND phase=?`)
    .get(requestRowValue.id, input.phase)?.count || 0);
  if (count >= MAX_CLAIM_EVENTS_PER_PHASE) {
    throw new Error('HTTP ingress claim history bound exceeded');
  }
  const body = claimEventBody({ requestRef: requestRowValue.request_ref, ...input });
  const values = claimEventValues(body);
  const result = ingressDb.prepare(`INSERT INTO ${mainTable(CLAIM_EVENT_TABLE)} (
    contract,claim_event_ref,claim_event_sha256,request_row_id,request_ref,phase,
    generation,sequence,action,token_sha256,lease_until_ts,event_json,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    body.contract, values.claimEventRef, values.claimEventSha256,
    requestRowValue.id, requestRowValue.request_ref, body.phase, body.generation,
    body.sequence, body.action, body.tokenSha256, body.leaseUntil,
    values.serialized, createdAt,
  );
  if (Number(result.changes) !== 1) {
    throw new Error('HTTP ingress claim event insert did not change exactly one row');
  }
  const stored = ingressDb.prepare(`SELECT * FROM ${mainTable(CLAIM_EVENT_TABLE)}
    WHERE claim_event_ref=?`).get(values.claimEventRef);
  const receipt = validateClaimEvent(stored, requestRowValue);
  const reread = claimHistory(requestRowValue, body.phase);
  if (!reread || reread.claimEventRef !== receipt.claimEventRef) {
    throw new Error('HTTP ingress claim event reread mismatch');
  }
  return reread;
}

function activeClaim(current, atMs = Date.now()) {
  return Boolean(current && current.action !== 'released'
    && Date.parse(current.leaseUntil) > atMs);
}

function acquireClaim(requestRowValue, phase, atMs = Date.now()) {
  const current = claimHistory(requestRowValue, phase);
  if (activeClaim(current, atMs)) return { acquired: false, claim: current };
  const tokenSha256 = hashBytes(randomBytes(32));
  const createdAt = new Date(atMs).toISOString();
  const leaseUntil = new Date(atMs + CLAIM_LEASE_MS).toISOString();
  const claim = insertClaimEvent(requestRowValue, {
    phase, generation: (current?.generation || 0) + 1, sequence: 0,
    action: 'acquired', tokenSha256, leaseUntil,
  }, createdAt);
  return { acquired: true, claim };
}

function appendClaimContinuation(requestRowValue, claim, action, atMs = Date.now()) {
  const current = claimHistory(requestRowValue, claim.phase);
  if (!current || current.generation !== claim.generation
    || current.tokenSha256 !== claim.tokenSha256 || current.action === 'released') return null;
  const createdAt = new Date(atMs).toISOString();
  if (action === 'renewed' && !activeClaim(current, atMs)) return null;
  const leaseUntil = action === 'renewed'
    ? new Date(Math.max(Date.parse(current.leaseUntil) + 1, atMs + CLAIM_LEASE_MS)).toISOString()
    : current.leaseUntil;
  return insertClaimEvent(requestRowValue, {
    phase: claim.phase, generation: claim.generation, sequence: current.sequence + 1,
    action, tokenSha256: claim.tokenSha256, leaseUntil,
  }, createdAt);
}

function issuePreparation(requestRowValue, claim) {
  const capability = Object.freeze({ contract: 'HttpIngressPreparationAuthority/v1' });
  PREPARATION_AUTHORITIES.set(capability, Object.freeze({
    requestRowId: Number(requestRowValue.id), requestRef: requestRowValue.request_ref,
    phase: 'execution', generation: claim.generation,
    tokenSha256: claim.tokenSha256, leaseUntil: claim.leaseUntil,
    claimEventRef: claim.claimEventRef,
  }));
  return capability;
}

function issueSettlement(requestRowValue, responseRowValue, claim) {
  const capability = Object.freeze({ contract: 'HttpIngressSettlementAuthority/v1' });
  SETTLEMENT_AUTHORITIES.set(capability, Object.freeze({
    requestRowId: Number(requestRowValue.id), requestRef: requestRowValue.request_ref,
    responseRef: responseRowValue.response_ref, phase: 'settlement',
    generation: claim.generation, tokenSha256: claim.tokenSha256,
    leaseUntil: claim.leaseUntil, claimEventRef: claim.claimEventRef,
  }));
  return capability;
}

function issueDurableResponse(requestRowValue, responseRowValue) {
  responseReceipt(responseRowValue, requestRowValue);
  if (storedPublicBodyKind(responseRowValue) !== 'current_public_api') return null;
  const capability = Object.freeze({
    contract: 'DurableHttpIngressResponseAuthority/v3',
  });
  DURABLE_RESPONSE_AUTHORITIES.set(capability, Object.freeze({
    requestRowId: Number(requestRowValue.id),
    requestRef: requestRowValue.request_ref,
    responseRowId: Number(responseRowValue.id),
    responseRef: responseRowValue.response_ref,
    responseSha256: responseRowValue.response_sha256,
  }));
  return capability;
}

function verifyDurableResponseAttestation(attestation) {
  requireStore();
  const requestRowValue = ingressDb.prepare(`SELECT * FROM ${mainTable(REQUEST_TABLE)}
    WHERE id=? AND request_ref=?`).get(
    attestation.requestRowId, attestation.requestRef,
  );
  if (!requestRowValue) {
    throw new Error('durable HTTP ingress response authority lost its request');
  }
  requestReceipt(requestRowValue);
  const lifecycle = requestLifecycle(requestRowValue);
  if (lifecycle.state === 'started' || !lifecycle.response
    || Number(lifecycle.response.id) !== attestation.responseRowId
    || lifecycle.response.response_ref !== attestation.responseRef
    || lifecycle.response.response_sha256 !== attestation.responseSha256) {
    throw new Error('durable HTTP ingress response authority lost its terminal response');
  }
  const response = responseReceipt(lifecycle.response, requestRowValue);
  if (storedPublicBodyKind(lifecycle.response) !== 'current_public_api') {
    throw new Error('historical HTTP public body cannot consume response authority');
  }
  const binding = ackBindingRow(requestRowValue);
  if (!binding) {
    throw new Error('durable HTTP ingress response authority has no client ACK binding');
  }
  return deepFreeze({
    contract: 'ConsumedDurableHttpIngressResponseAuthority/v3',
    request: requestReceipt(requestRowValue), response,
    clientAckBinding: binding,
  });
}

export function durableHttpIngressResponseBoundaryStatus() {
  const status = httpIngressStoreStatus();
  return Object.freeze({
    initialized: true, available: status.available,
    ...(status.reason ? { reason: status.reason } : {}),
  });
}

export function consumeDurableHttpIngressResponseAuthority(capability) {
  const attestation = capability && typeof capability === 'object'
    ? DURABLE_RESPONSE_AUTHORITIES.get(capability) : null;
  if (!attestation
    || capability.contract !== 'DurableHttpIngressResponseAuthority/v3') {
    throw new Error('invalid opaque one-shot durable HTTP ingress response authority');
  }
  DURABLE_RESPONSE_AUTHORITIES.delete(capability);
  return verifyDurableResponseAttestation(attestation);
}

function authorityAttestation(registry, capability, contract, label) {
  const attestation = capability && typeof capability === 'object'
    ? registry.get(capability) : null;
  if (!attestation || capability.contract !== contract) {
    throw new Error(`invalid opaque one-shot ${label} authority`);
  }
  return attestation;
}

function verifiedResultAuthority(capability) {
  const attestation = capability && typeof capability === 'object'
    ? VERIFIED_RESULT_AUTHORITIES.get(capability) : null;
  if (!attestation || capability.contract !== 'VerifiedHttpResultAuthority/v1') {
    throw new Error('invalid opaque one-shot verified HTTP result authority');
  }
  return attestation;
}

function deleteAuthority(registry, capability) {
  registry.delete(capability);
}

function currentClaimForAttestation(row, attestation, { requireLive = true } = {}) {
  const current = claimHistory(row, attestation.phase);
  if (!current || current.generation !== attestation.generation
    || current.tokenSha256 !== attestation.tokenSha256 || current.action === 'released'
    || (requireLive && !activeClaim(current))) return null;
  return current;
}

function rowByAttestation(attestation) {
  const row = ingressDb.prepare(`SELECT * FROM ${mainTable(REQUEST_TABLE)}
    WHERE id=? AND request_ref=?`).get(attestation.requestRowId, attestation.requestRef);
  if (!row) throw new Error('HTTP ingress authority lost its durable request');
  requestReceipt(row);
  return row;
}

function locatedClaimAuthority(capability) {
  if (capability && typeof capability === 'object'
    && PREPARATION_AUTHORITIES.has(capability)) {
    return {
      registry: PREPARATION_AUTHORITIES, label: 'preparation',
      contract: 'HttpIngressPreparationAuthority/v1',
      attestation: PREPARATION_AUTHORITIES.get(capability),
    };
  }
  if (capability && typeof capability === 'object'
    && SETTLEMENT_AUTHORITIES.has(capability)) {
    return {
      registry: SETTLEMENT_AUTHORITIES, label: 'settlement',
      contract: 'HttpIngressSettlementAuthority/v1',
      attestation: SETTLEMENT_AUTHORITIES.get(capability),
    };
  }
  return null;
}

export function httpIngressAuthorityCurrent(capability) {
  const located = locatedClaimAuthority(capability);
  if (!located || capability.contract !== located.contract) return false;
  try {
    requireStore();
    const row = rowByAttestation(located.attestation);
    return Boolean(currentClaimForAttestation(row, located.attestation));
  } catch { return false; }
}

export function verifiedHttpIngressExecutionAuthority(capability, expected = {}) {
  const attestation = capability && typeof capability === 'object'
    ? PREPARATION_AUTHORITIES.get(capability) : null;
  if (!attestation || capability.contract !== 'HttpIngressPreparationAuthority/v1') return null;
  try {
    requireStore();
    const identity = normalizedIdentity(expected);
    const row = rowByAttestation(attestation);
    const current = currentClaimForAttestation(row, attestation);
    if (!current || attestation.phase !== 'execution'
      || row.request_ref !== identity.requestRef
      || row.intent_sha256 !== identity.intentSha256
      || Number(row.intent_bytes) !== identity.intentBytes) return null;
    return Object.freeze({
      contract: 'VerifiedHttpIngressExecutionAuthority/v1',
      requestRef: row.request_ref, channel: row.channel,
      conversationId: row.conversation_id, requestId: row.request_id,
      intentSha256: row.intent_sha256, generation: current.generation,
      claimEventRef: current.claimEventRef,
    });
  } catch { return null; }
}

export function renewHttpIngressAuthority(capability) {
  requireStore();
  const located = locatedClaimAuthority(capability);
  if (!located || capability.contract !== located.contract) {
    throw new Error('invalid opaque HTTP ingress claim authority');
  }
  const row = rowByAttestation(located.attestation);
  const renewed = ingressDb.transaction(() => {
    const current = currentClaimForAttestation(row, located.attestation);
    return current
      ? appendClaimContinuation(row, current, 'renewed') : null;
  }).immediate();
  if (!renewed) return null;
  const updated = Object.freeze({
    ...located.attestation, leaseUntil: renewed.leaseUntil,
    claimEventRef: renewed.claimEventRef,
  });
  located.registry.set(capability, updated);
  return claimReceipt(renewed);
}

export function releaseHttpIngressAuthority(capability) {
  requireStore();
  const located = locatedClaimAuthority(capability);
  if (!located || capability.contract !== located.contract) {
    throw new Error('invalid opaque HTTP ingress claim authority');
  }
  const row = rowByAttestation(located.attestation);
  const released = ingressDb.transaction(() => {
    const current = currentClaimForAttestation(
      row, located.attestation, { requireLive: false },
    );
    return current ? appendClaimContinuation(row, current, 'released') : null;
  }).immediate();
  located.registry.delete(capability);
  return Object.freeze({
    contract: 'HttpIngressClaimRelease/v1', released: Boolean(released),
    phase: located.attestation.phase, generation: located.attestation.generation,
  });
}

export function beginHttpIngress(input) {
  requireStore();
  const identity = normalizedIdentity(input);
  const lookup = ingressDb.transaction(() => {
    let row = requestRow(identity);
    let created = false;
    if (!row) {
      if (!identity.clientAck) {
        return {
          kind: 'client_ack_key_required', request: null,
          lifecycle: { state: 'rejected' },
        };
      }
      const startedAt = nowIso();
      const inserted = ingressDb.prepare(`INSERT INTO ${mainTable(REQUEST_TABLE)} (
        contract,request_ref,channel,conversation_id,request_id,intent_sha256,
        intent_bytes,started_ts
      ) VALUES (?,?,?,?,?,?,?,?)`).run(
        'HttpIngressRequest/v1', identity.requestRef, identity.channel,
        identity.conversationId, identity.requestId, identity.intentSha256,
        identity.intentBytes, startedAt,
      );
      if (Number(inserted.changes) !== 1) {
        throw new Error('HTTP ingress request insert did not change exactly one row');
      }
      row = ingressDb.prepare(`SELECT * FROM ${mainTable(REQUEST_TABLE)} WHERE id=?`)
        .get(Number(inserted.lastInsertRowid));
      requestReceipt(row);
      insertEvent(row, 'started');
      insertAckBinding(row, identity);
      created = true;
    }
    const request = requestReceipt(row);
    const durableAckBinding = ackBindingRow(row);
    const lifecycle = requestLifecycle(row);
    if (row.intent_sha256 !== identity.intentSha256
      || Number(row.intent_bytes) !== identity.intentBytes) {
      return { kind: 'conflict', request, lifecycle };
    }
    if (!durableAckBinding) {
      return { kind: 'legacy_unacknowledged', request, row, lifecycle };
    }
    if (!identity.clientAck
      || identity.clientAck.keyFingerprint !== durableAckBinding.keyFingerprint) {
      return { kind: 'conflict', request, lifecycle };
    }
    if (lifecycle.state !== 'started') {
      return { kind: 'terminal', request, row, lifecycle };
    }
    const laneBlocker = earlierOpenLaneRequest(row);
    if (laneBlocker) {
      return {
        kind: 'lane_blocked', request, row, lifecycle,
        blockedByRequestRef: laneBlocker.request_ref,
      };
    }
    const phase = lifecycle.response ? 'settlement' : 'execution';
    const claim = acquireClaim(row, phase);
    return {
      kind: created ? 'started' : 'existing', request, row, lifecycle, phase,
      claim,
    };
  }).immediate();

  if (lookup.kind === 'conflict') {
    return Object.freeze({
      kind: 'conflict', reason: 'request_intent_conflict', state: lookup.lifecycle.state,
      request: lookup.request, existingIntentSha256: lookup.request.intentSha256,
    });
  }
  if (lookup.kind === 'client_ack_key_required') {
    return Object.freeze({
      kind: 'rejected', state: 'rejected', reason: 'client_ack_key_required',
    });
  }
  if (lookup.kind === 'legacy_unacknowledged') {
    return deepFreeze({
      kind: 'legacy_unacknowledged', state: lookup.lifecycle.state,
      request: lookup.request, delivered: false,
      reason: 'legacy_missing_client_ack_key',
      ...(lookup.lifecycle.response
        ? { response: responseReceipt(lookup.lifecycle.response, lookup.row) } : {}),
    });
  }
  if (lookup.kind === 'lane_blocked') {
    return Object.freeze({
      kind: 'lane_in_progress', state: 'started', request: lookup.request,
      blockedByRequestRef: lookup.blockedByRequestRef,
    });
  }
  const { row, request, lifecycle } = lookup;
  if (lifecycle.state !== 'started') {
    const legacy = !ackBindingRow(row);
    const response = responseReceipt(lifecycle.response, row);
    const responseAuthority = legacy ? null : issueDurableResponse(row, lifecycle.response);
    const historicalPublicBody = !legacy && !responseAuthority;
    return deepFreeze({
      kind: legacy ? 'legacy_unacknowledged'
        : historicalPublicBody ? 'legacy_unattested_public_body' : 'replay',
      state: lifecycle.state, request,
      response,
      ...(legacy ? { delivered: false, reason: 'legacy_missing_client_ack_key' }
        : historicalPublicBody
          ? { delivered: false, reason: 'legacy_unattested_public_body' }
          : { responseAuthority }),
    });
  }
  const claim = claimReceipt(lookup.claim.claim);
  if (lifecycle.response) {
    const response = responseReceipt(lifecycle.response, row);
    if (!lookup.claim.acquired) {
      return deepFreeze({
        kind: 'settlement_in_progress', state: 'started', request, response, claim,
      });
    }
    return deepFreeze({
      kind: 'settlement_required', state: 'started', request, response, claim,
      settlementAuthority: issueSettlement(row, lifecycle.response, lookup.claim.claim),
    });
  }
  if (!lookup.claim.acquired) {
    return deepFreeze({ kind: 'in_progress', state: 'started', request, claim });
  }
  return deepFreeze({
    kind: lookup.kind === 'started' ? 'started' : 'resume', state: 'started', request,
    claim, preparationAuthority: issuePreparation(row, lookup.claim.claim),
  });
}

export function prepareHttpResponse(capability, resultCapability) {
  requireStore();
  const attestation = authorityAttestation(
    PREPARATION_AUTHORITIES, capability,
    'HttpIngressPreparationAuthority/v1', 'preparation',
  );
  const verifiedBinding = verifiedResultAuthority(resultCapability);
  const row = rowByAttestation(attestation);
  if (verifiedBinding.requestRowId !== Number(row.id)
    || verifiedBinding.requestRef !== row.request_ref
    || verifiedBinding.phase !== attestation.phase
    || verifiedBinding.generation !== attestation.generation
    || verifiedBinding.tokenSha256 !== attestation.tokenSha256
    || verifiedBinding.claimEventRef !== attestation.claimEventRef) {
    deleteAuthority(VERIFIED_RESULT_AUTHORITIES, resultCapability);
    return deepFreeze({
      kind: 'conflict', reason: 'result_claim_mismatch',
      state: requestLifecycle(row).state, request: requestReceipt(row),
    });
  }
  const verifiedResult = verifiedBinding.result;
  const normalized = normalizedPreparedResponse({
    state: verifiedResult.state, status: verifiedResult.status,
    turnId: verifiedResult.turnId, publicBody: verifiedResult.publicBody,
    provenance: verifiedResult.provenance,
  }, row);
  const outcome = ingressDb.transaction(() => {
    const lifecycle = requestLifecycle(row);
    const claim = currentClaimForAttestation(row, attestation);
    if (!claim) return { kind: 'fenced', lifecycle };
    if (claim.claimEventRef !== verifiedBinding.claimEventRef) {
      return { kind: 'result_mismatch', lifecycle };
    }
    if (lifecycle.response) {
      const response = responseReceipt(lifecycle.response, row);
      if (response.sha256 !== normalized.responseSha256) {
        return { kind: 'conflict', response, lifecycle };
      }
      if (lifecycle.state !== 'started') {
        return { kind: 'replay', response, lifecycle, responseRow: lifecycle.response };
      }
      const released = appendClaimContinuation(row, claim, 'released');
      if (!released) throw new Error('HTTP execution claim release failed');
      const settlement = acquireClaim(row, 'settlement');
      if (!settlement.acquired) throw new Error('HTTP settlement claim acquisition failed');
      return {
        kind: 'replay', response, lifecycle, responseRow: lifecycle.response,
        settlementClaim: settlement.claim,
      };
    }
    if (lifecycle.state !== 'started') {
      throw new Error('HTTP ingress request is already terminal without a response');
    }
    const preparedAt = nowIso();
    const inserted = ingressDb.prepare(`INSERT INTO ${mainTable(RESPONSE_TABLE)} (
      contract,response_ref,response_sha256,request_row_id,request_ref,target_state,
      status,turn_id,public_body_json,public_body_sha256,provenance_kind,
      provenance_ref,provenance_sha256,response_json,prepared_ts
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      normalized.responseBody.contract, normalized.responseRef,
      normalized.responseSha256, row.id, row.request_ref, normalized.state,
      normalized.status, normalized.turnId, normalized.body.serialized,
      normalized.body.sha256, normalized.provenance.kind, normalized.provenance.ref,
      normalized.provenance.sha256, normalized.serialized, preparedAt,
    );
    if (Number(inserted.changes) !== 1) {
      throw new Error('HTTP prepared response insert did not change exactly one row');
    }
    const stored = responseRow(row);
    const response = responseReceipt(stored, row);
    const released = appendClaimContinuation(row, claim, 'released');
    if (!released) throw new Error('HTTP execution claim release failed');
    const settlement = acquireClaim(row, 'settlement');
    if (!settlement.acquired) throw new Error('HTTP settlement claim acquisition failed');
    const reread = requestLifecycle(row);
    if (reread.state !== 'started' || reread.response?.response_ref !== stored.response_ref) {
      throw new Error('HTTP prepared response reread mismatch');
    }
    return {
      kind: 'created', response, lifecycle: reread, responseRow: stored,
      settlementClaim: settlement.claim,
    };
  }).immediate();
  deleteAuthority(PREPARATION_AUTHORITIES, capability);
  deleteAuthority(VERIFIED_RESULT_AUTHORITIES, resultCapability);

  if (outcome.kind === 'fenced') {
    return deepFreeze({
      kind: 'conflict', reason: 'claim_fenced', state: outcome.lifecycle.state,
      request: requestReceipt(row),
    });
  }
  if (outcome.kind === 'result_mismatch') {
    return deepFreeze({
      kind: 'conflict', reason: 'result_claim_mismatch',
      state: outcome.lifecycle.state, request: requestReceipt(row),
    });
  }

  if (outcome.kind === 'conflict') {
    return deepFreeze({
      kind: 'conflict', reason: 'prepared_response_conflict',
      state: outcome.lifecycle.state, request: requestReceipt(row),
      response: outcome.response,
    });
  }
  if (outcome.lifecycle.state !== 'started') {
    const responseAuthority = issueDurableResponse(row, outcome.responseRow);
    return deepFreeze({
      kind: responseAuthority ? 'replay' : 'legacy_unattested_public_body',
      state: outcome.lifecycle.state, request: requestReceipt(row),
      response: outcome.response,
      ...(responseAuthority ? { responseAuthority }
        : { delivered: false, reason: 'legacy_unattested_public_body' }),
    });
  }
  return deepFreeze({
    kind: outcome.kind === 'created' ? 'response_prepared' : 'response_replay',
    state: 'started', targetState: outcome.response.state,
    request: requestReceipt(row), response: outcome.response,
    claim: claimReceipt(outcome.settlementClaim),
    settlementAuthority: issueSettlement(
      row, outcome.responseRow, outcome.settlementClaim,
    ),
  });
}

export function settleHttpIngress(capability) {
  requireStore();
  const attestation = authorityAttestation(
    SETTLEMENT_AUTHORITIES, capability,
    'HttpIngressSettlementAuthority/v1', 'settlement',
  );
  const row = rowByAttestation(attestation);
  const outcome = ingressDb.transaction(() => {
    const lifecycle = requestLifecycle(row);
    if (!lifecycle.response || lifecycle.response.response_ref !== attestation.responseRef) {
      throw new Error('HTTP ingress settlement lost its prepared response');
    }
    const response = responseReceipt(lifecycle.response, row);
    if (lifecycle.state !== 'started') {
      if (lifecycle.state !== response.state) {
        throw new Error('HTTP ingress settlement conflicts with terminal state');
      }
      return { kind: 'replay', state: lifecycle.state, response };
    }
    const claim = currentClaimForAttestation(row, attestation);
    if (!claim) return { kind: 'fenced', state: 'started', response };
    insertEvent(row, response.state, response.responseRef, 1);
    const released = appendClaimContinuation(row, claim, 'released');
    if (!released) throw new Error('HTTP settlement claim release failed');
    const reread = requestLifecycle(row);
    if (reread.state !== response.state
      || reread.response?.response_ref !== response.responseRef) {
      throw new Error('HTTP ingress terminal reread mismatch');
    }
    return { kind: 'settled', state: response.state, response };
  }).immediate();
  deleteAuthority(SETTLEMENT_AUTHORITIES, capability);
  if (outcome.kind === 'fenced') {
    return deepFreeze({
      kind: 'conflict', reason: 'claim_fenced', state: outcome.state,
      request: requestReceipt(row), response: outcome.response,
    });
  }
  const responseAuthority = issueDurableResponse(row, responseRow(row));
  return deepFreeze({
    ...outcome, request: requestReceipt(row),
    ...(responseAuthority
      ? { responseAuthority }
      : { kind: 'legacy_unattested_public_body', delivered: false,
        reason: 'legacy_unattested_public_body' }),
  });
}
