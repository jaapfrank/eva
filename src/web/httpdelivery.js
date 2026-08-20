import { createHash, randomBytes } from 'node:crypto';

import { db } from '../db.js';
import { canonicalJson } from '../util.js';
import {
  canonicalHttpWireSha256,
  normalizeHttpClientAckPublicKey,
  verifyHttpClientAck,
  verifyLegacyHttpClientAck,
} from './httpack.js';
import {
  consumeHttpResponseAuthority,
  httpResponseAuthorityStoreStatus,
} from './httpresponseauthority.js';
import { exactSqliteIndexSignatures } from './sqliteindexattestation.js';

export { canonicalHttpClientAckPreimage, verifyHttpClientAck } from './httpack.js';

const deliveryDb = /** @type {any} */ (db);
const ITEM_TABLE = 'eva_http_delivery_items_v3';
const ATTEMPT_TABLE = 'eva_http_delivery_attempt_events_v3';
const ACK_TABLE = 'eva_http_delivery_ack_receipts_v3';
const TRANSMISSION_TABLE = 'eva_http_delivery_transmission_events_v3';
const LEGACY_TABLES = Object.freeze([ITEM_TABLE, ATTEMPT_TABLE, ACK_TABLE]);
const TABLES = Object.freeze([...LEGACY_TABLES, TRANSMISSION_TABLE]);
const INDEXES = Object.freeze([
  'idx_http_delivery_v3_request',
  'idx_http_delivery_v3_attempt_history',
  'idx_http_delivery_v3_ack_item',
  'idx_http_delivery_v3_transmission_history',
]);
const TRIGGERS = Object.freeze([
  'eva_http_delivery_v3_items_no_update',
  'eva_http_delivery_v3_items_no_delete',
  'eva_http_delivery_v3_items_no_replace',
  'eva_http_delivery_v3_items_valid_authority',
  'eva_http_delivery_v3_attempts_no_update',
  'eva_http_delivery_v3_attempts_no_delete',
  'eva_http_delivery_v3_attempts_no_replace',
  'eva_http_delivery_v3_attempts_valid_transition',
  'eva_http_delivery_v3_acks_no_update',
  'eva_http_delivery_v3_acks_no_delete',
  'eva_http_delivery_v3_acks_no_replace',
  'eva_http_delivery_v3_acks_valid_reference',
  'eva_http_delivery_v3_transmissions_no_update',
  'eva_http_delivery_v3_transmissions_no_delete',
  'eva_http_delivery_v3_transmissions_no_replace',
  'eva_http_delivery_v3_transmissions_valid_transition',
]);
const PROTECTED = Object.freeze([
  ...TABLES, ...INDEXES, ...TRIGGERS, 'eva_http_response_authorities_v3',
]);
const SHA256 = /^[a-f0-9]{64}$/u;
const REQUEST_REF = /^http-ingress:[a-f0-9]{64}$/u;
const RESPONSE_REF = /^http-response:[a-f0-9]{64}$/u;
const AUTHORITY_REF = /^http-response-authority-v3:[a-f0-9]{64}$/u;
const DELIVERY_REF = /^http-delivery-v3:[a-f0-9]{64}$/u;
const ATTEMPT_REF = /^http-write-attempt-v3:[a-f0-9]{64}$/u;
const TRANSMISSION_REF = /^http-transmission-event-v3:[a-f0-9]{64}$/u;
const ACK_REF = /^http-delivery-ack-v3:[a-f0-9]{64}$/u;
const CHALLENGE = SHA256;
const KEY_FINGERPRINT = /^p256-spki-sha256:[a-f0-9]{64}$/u;
const CONTENT_TYPE = 'application/json; charset=utf-8';
const MAX_GENERATIONS = 64;
const MAX_TRANSMISSIONS = Number.MAX_SAFE_INTEGER;
const ATTEMPT_ACTIONS = new Set(['issued', 'write_finished', 'abandoned']);
const TRANSMISSION_ACTIONS = new Set(['issued', 'write_finished', 'abandoned']);
const TRANSMISSION_MODES = new Set(['pre_ack_retrieval', 'acknowledged_retrieval']);
const LEASE_MS = 30_000;
const CLAIMS = new WeakMap();
const TRANSMISSION_CLAIMS = new WeakMap();
let initializationFailure = '';

const hash = (value) => createHash('sha256').update(value).digest('hex');
const nowIso = () => new Date(Date.now()).toISOString();
const mainTable = (name) => `main.${name}`;

const CURRENT_ACK_PREIMAGE_RULE = `SELECT CASE WHEN
    (SELECT count(*) FROM json_each(NEW.ack_preimage_json))<>9
    OR json_extract(NEW.ack_preimage_json,'$.contract') IS NOT 'HttpClientAck/v3'
    OR json_extract(NEW.ack_preimage_json,'$.requestRef') IS NOT NEW.request_ref
    OR json_extract(NEW.ack_preimage_json,'$.responseRef') IS NOT NEW.response_ref
    OR json_extract(NEW.ack_preimage_json,'$.responseSha256') IS NOT NEW.response_sha256
    OR json_extract(NEW.ack_preimage_json,'$.deliveryRef') IS NOT NEW.delivery_ref
    OR json_extract(NEW.ack_preimage_json,'$.challenge') IS NOT NEW.ack_challenge
    OR json_extract(NEW.ack_preimage_json,'$.wireSha256') IS NOT NEW.wire_sha256
    OR json_type(NEW.ack_preimage_json,'$.writeGeneration') IS NOT 'integer'
    OR NOT EXISTS (
      SELECT 1 FROM eva_http_delivery_attempt_events_v3 issued
      JOIN eva_http_delivery_attempt_events_v3 finished
        ON finished.delivery_row_id=issued.delivery_row_id
        AND finished.generation=issued.generation AND finished.sequence=1
        AND finished.action='write_finished'
      WHERE issued.delivery_row_id=NEW.delivery_row_id
        AND issued.generation=json_extract(NEW.ack_preimage_json,'$.writeGeneration')
        AND issued.sequence=0 AND issued.action='issued'
        AND issued.attempt_event_ref=json_extract(
          NEW.ack_preimage_json,'$.writeAttemptRef')
        AND NOT EXISTS (
          SELECT 1 FROM eva_http_delivery_attempt_events_v3 later
          WHERE later.delivery_row_id=issued.delivery_row_id
            AND (later.generation>finished.generation
              OR (later.generation=finished.generation AND later.sequence>finished.sequence))
        )
    )
  THEN RAISE(ABORT,'HTTP delivery v3 ACK preimage conflicts with finished write') END;`;
const LEGACY_ACK_PREIMAGE_RULE = `SELECT CASE WHEN
    (SELECT count(*) FROM json_each(NEW.ack_preimage_json))<>7
    OR json_extract(NEW.ack_preimage_json,'$.contract') IS NOT 'HttpClientAck/v3'
    OR json_extract(NEW.ack_preimage_json,'$.requestRef') IS NOT NEW.request_ref
    OR json_extract(NEW.ack_preimage_json,'$.responseRef') IS NOT NEW.response_ref
    OR json_extract(NEW.ack_preimage_json,'$.responseSha256') IS NOT NEW.response_sha256
    OR json_extract(NEW.ack_preimage_json,'$.deliveryRef') IS NOT NEW.delivery_ref
    OR json_extract(NEW.ack_preimage_json,'$.challenge') IS NOT NEW.ack_challenge
    OR json_extract(NEW.ack_preimage_json,'$.wireSha256') IS NOT NEW.wire_sha256
  THEN RAISE(ABORT,'HTTP delivery v3 ACK preimage conflicts with item') END;`;

const SCHEMA = `
CREATE TABLE IF NOT EXISTS eva_http_delivery_items_v3 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  delivery_ref TEXT NOT NULL UNIQUE,
  delivery_sha256 TEXT NOT NULL UNIQUE,
  authority_ref TEXT NOT NULL UNIQUE,
  authority_sha256 TEXT NOT NULL UNIQUE,
  request_ref TEXT NOT NULL UNIQUE,
  response_ref TEXT NOT NULL UNIQUE,
  response_sha256 TEXT NOT NULL UNIQUE,
  status INTEGER NOT NULL,
  content_type TEXT NOT NULL,
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
  item_json TEXT NOT NULL,
  prepared_ts TEXT NOT NULL,
  CHECK(contract='PreparedHttpDelivery/v3'),
  CHECK(delivery_ref GLOB 'http-delivery-v3:*' AND length(delivery_ref)=81),
  CHECK(length(delivery_sha256)=64 AND lower(delivery_sha256)=delivery_sha256
    AND delivery_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(authority_ref GLOB 'http-response-authority-v3:*' AND length(authority_ref)=91),
  CHECK(length(authority_sha256)=64 AND lower(authority_sha256)=authority_sha256
    AND authority_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(request_ref GLOB 'http-ingress:*' AND length(request_ref)=77),
  CHECK(response_ref GLOB 'http-response:*' AND length(response_ref)=78),
  CHECK(length(response_sha256)=64 AND lower(response_sha256)=response_sha256
    AND response_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(status)='integer' AND status>=200 AND status<=599),
  CHECK(content_type='${CONTENT_TYPE}'),
  CHECK(typeof(wire_bytes)='blob'),
  CHECK(length(wire_sha256)=64 AND lower(wire_sha256)=wire_sha256
    AND wire_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(wire_length)='integer' AND wire_length>=2 AND wire_length<=24576),
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
  CHECK(turn_id IS NULL OR (length(turn_id)>=1 AND length(turn_id)<=180)),
  CHECK(provenance_kind IN ('turn_result','durable_handoff',
    'request_supersession','ingress_rejection')),
  CHECK(length(provenance_ref)>=8 AND length(provenance_ref)<=240
    AND provenance_ref NOT GLOB '*[^A-Za-z0-9._:-]*'),
  CHECK(length(provenance_sha256)=64 AND lower(provenance_sha256)=provenance_sha256
    AND provenance_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(item_json) AND json_type(item_json,'$')='object'),
  CHECK(length(prepared_ts)=24 AND substr(prepared_ts,24,1)='Z')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_http_delivery_v3_request
  ON eva_http_delivery_items_v3(request_ref,response_sha256);
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_items_no_update
BEFORE UPDATE ON eva_http_delivery_items_v3
BEGIN SELECT RAISE(ABORT,'HTTP delivery v3 items are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_items_no_delete
BEFORE DELETE ON eva_http_delivery_items_v3
BEGIN SELECT RAISE(ABORT,'HTTP delivery v3 items are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_items_no_replace
BEFORE INSERT ON eva_http_delivery_items_v3
WHEN EXISTS (
  SELECT 1 FROM eva_http_delivery_items_v3
  WHERE id=NEW.id OR delivery_ref=NEW.delivery_ref
    OR delivery_sha256=NEW.delivery_sha256 OR authority_ref=NEW.authority_ref
    OR authority_sha256=NEW.authority_sha256 OR request_ref=NEW.request_ref
    OR response_ref=NEW.response_ref OR response_sha256=NEW.response_sha256
    OR wire_sha256=NEW.wire_sha256 OR ack_challenge=NEW.ack_challenge
    OR ack_challenge_sha256=NEW.ack_challenge_sha256
)
BEGIN SELECT RAISE(ABORT,'HTTP delivery v3 item identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_items_valid_authority
BEFORE INSERT ON eva_http_delivery_items_v3
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_http_response_authorities_v3 authority
    WHERE authority.authority_ref=NEW.authority_ref
      AND authority.authority_sha256=NEW.authority_sha256
      AND authority.delivery_ref=NEW.delivery_ref
      AND authority.request_ref=NEW.request_ref
      AND authority.response_ref=NEW.response_ref
      AND authority.response_sha256=NEW.response_sha256
      AND authority.status=NEW.status AND authority.content_type=NEW.content_type
      AND authority.wire_bytes=NEW.wire_bytes
      AND authority.wire_sha256=NEW.wire_sha256
      AND authority.wire_length=NEW.wire_length
      AND authority.ack_challenge=NEW.ack_challenge
      AND authority.ack_challenge_sha256=NEW.ack_challenge_sha256
      AND authority.client_public_key_json=NEW.client_public_key_json
      AND authority.client_key_fingerprint=NEW.client_key_fingerprint
      AND authority.turn_id IS NEW.turn_id
      AND authority.provenance_kind=NEW.provenance_kind
      AND authority.provenance_ref=NEW.provenance_ref
      AND authority.provenance_sha256=NEW.provenance_sha256
  ) THEN RAISE(ABORT,'HTTP delivery v3 item requires exact response authority') END;
END;

CREATE TABLE IF NOT EXISTS eva_http_delivery_attempt_events_v3 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  attempt_event_ref TEXT NOT NULL UNIQUE,
  attempt_event_sha256 TEXT NOT NULL UNIQUE,
  delivery_row_id INTEGER NOT NULL,
  delivery_ref TEXT NOT NULL,
  generation INTEGER NOT NULL,
  sequence INTEGER NOT NULL,
  action TEXT NOT NULL,
  token_sha256 TEXT NOT NULL,
  ack_challenge_sha256 TEXT NOT NULL,
  wire_sha256 TEXT NOT NULL,
  wire_length INTEGER NOT NULL,
  lease_until_ts TEXT NOT NULL,
  event_json TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  UNIQUE(delivery_row_id,generation,sequence),
  CHECK(contract='HttpDeliveryWriteAttemptEvent/v3'),
  CHECK(attempt_event_ref GLOB 'http-write-attempt-v3:*'
    AND length(attempt_event_ref)=86),
  CHECK(length(attempt_event_sha256)=64 AND lower(attempt_event_sha256)=attempt_event_sha256
    AND attempt_event_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(delivery_row_id)='integer' AND delivery_row_id>=1),
  CHECK(delivery_ref GLOB 'http-delivery-v3:*' AND length(delivery_ref)=81),
  CHECK(typeof(generation)='integer' AND generation>=1 AND generation<=${MAX_GENERATIONS}),
  CHECK(typeof(sequence)='integer' AND sequence IN (0,1)),
  CHECK(action IN ('issued','write_finished','abandoned')),
  CHECK(length(token_sha256)=64 AND lower(token_sha256)=token_sha256
    AND token_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(ack_challenge_sha256)=64 AND lower(ack_challenge_sha256)=ack_challenge_sha256
    AND ack_challenge_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(wire_sha256)=64 AND lower(wire_sha256)=wire_sha256
    AND wire_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(wire_length)='integer' AND wire_length>=2 AND wire_length<=24576),
  CHECK(length(lease_until_ts)=24 AND substr(lease_until_ts,24,1)='Z'),
  CHECK(json_valid(event_json) AND json_type(event_json,'$')='object'),
  CHECK(length(created_ts)=24 AND substr(created_ts,24,1)='Z')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_http_delivery_v3_attempt_history
  ON eva_http_delivery_attempt_events_v3(delivery_row_id,generation,sequence);
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_attempts_no_update
BEFORE UPDATE ON eva_http_delivery_attempt_events_v3
BEGIN SELECT RAISE(ABORT,'HTTP delivery v3 attempts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_attempts_no_delete
BEFORE DELETE ON eva_http_delivery_attempt_events_v3
BEGIN SELECT RAISE(ABORT,'HTTP delivery v3 attempts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_attempts_no_replace
BEFORE INSERT ON eva_http_delivery_attempt_events_v3
WHEN EXISTS (
  SELECT 1 FROM eva_http_delivery_attempt_events_v3
  WHERE id=NEW.id OR attempt_event_ref=NEW.attempt_event_ref
    OR attempt_event_sha256=NEW.attempt_event_sha256
    OR (delivery_row_id=NEW.delivery_row_id AND generation=NEW.generation
      AND sequence=NEW.sequence)
)
BEGIN SELECT RAISE(ABORT,'HTTP delivery v3 attempt identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_attempts_valid_transition
BEFORE INSERT ON eva_http_delivery_attempt_events_v3
BEGIN
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM eva_http_delivery_ack_receipts_v3 ack
    WHERE ack.delivery_row_id=NEW.delivery_row_id
  ) THEN RAISE(ABORT,'acknowledged HTTP delivery cannot start or finish attempts') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_http_delivery_items_v3 item
    WHERE item.id=NEW.delivery_row_id AND item.delivery_ref=NEW.delivery_ref
      AND item.ack_challenge_sha256=NEW.ack_challenge_sha256
      AND item.wire_sha256=NEW.wire_sha256 AND item.wire_length=NEW.wire_length
  ) THEN RAISE(ABORT,'HTTP delivery v3 attempt has invalid exact item reference') END;
  SELECT CASE WHEN NEW.action='issued' AND (
    NEW.sequence<>0
    OR NEW.generation<>COALESCE((
      SELECT max(generation)+1 FROM eva_http_delivery_attempt_events_v3
      WHERE delivery_row_id=NEW.delivery_row_id
    ),1)
    OR NEW.lease_until_ts<=NEW.created_ts
    OR EXISTS (
      SELECT 1 FROM eva_http_delivery_attempt_events_v3 current
      WHERE current.id=(
        SELECT id FROM eva_http_delivery_attempt_events_v3
        WHERE delivery_row_id=NEW.delivery_row_id
        ORDER BY generation DESC,sequence DESC LIMIT 1
      ) AND (current.action='write_finished'
        OR (current.action='issued' AND current.lease_until_ts>NEW.created_ts))
    )
  ) THEN RAISE(ABORT,'HTTP delivery v3 attempt acquisition is invalid') END;
  SELECT CASE WHEN NEW.action<>'issued' AND (
    NEW.sequence<>1 OR NEW.generation<>(
      SELECT max(generation) FROM eva_http_delivery_attempt_events_v3
      WHERE delivery_row_id=NEW.delivery_row_id
    )
    OR NOT EXISTS (
      SELECT 1 FROM eva_http_delivery_attempt_events_v3 issued
      WHERE issued.delivery_row_id=NEW.delivery_row_id
        AND issued.generation=NEW.generation AND issued.sequence=0
        AND issued.action='issued' AND issued.token_sha256=NEW.token_sha256
        AND issued.ack_challenge_sha256=NEW.ack_challenge_sha256
        AND issued.wire_sha256=NEW.wire_sha256
        AND issued.wire_length=NEW.wire_length
        AND ((NEW.action='write_finished' AND issued.lease_until_ts>NEW.created_ts)
          OR NEW.action='abandoned')
    )
    OR EXISTS (
      SELECT 1 FROM eva_http_delivery_attempt_events_v3 final
      WHERE final.delivery_row_id=NEW.delivery_row_id
        AND final.generation=NEW.generation AND final.sequence=1
    )
  ) THEN RAISE(ABORT,'HTTP delivery v3 attempt completion is invalid') END;
END;

CREATE TABLE IF NOT EXISTS eva_http_delivery_ack_receipts_v3 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  ack_receipt_ref TEXT NOT NULL UNIQUE,
  ack_receipt_sha256 TEXT NOT NULL UNIQUE,
  delivery_row_id INTEGER NOT NULL UNIQUE,
  delivery_ref TEXT NOT NULL UNIQUE,
  request_ref TEXT NOT NULL UNIQUE,
  response_ref TEXT NOT NULL UNIQUE,
  response_sha256 TEXT NOT NULL UNIQUE,
  ack_challenge TEXT NOT NULL UNIQUE,
  ack_challenge_sha256 TEXT NOT NULL UNIQUE,
  wire_sha256 TEXT NOT NULL,
  wire_length INTEGER NOT NULL,
  client_key_fingerprint TEXT NOT NULL,
  ack_preimage_json TEXT NOT NULL,
  ack_preimage_sha256 TEXT NOT NULL,
  signature TEXT NOT NULL,
  signature_sha256 TEXT NOT NULL,
  turn_id TEXT,
  provenance_kind TEXT NOT NULL,
  provenance_ref TEXT NOT NULL,
  provenance_sha256 TEXT NOT NULL,
  receipt_json TEXT NOT NULL,
  acknowledged_ts TEXT NOT NULL,
  CHECK(contract='HttpClientAckReceipt/v3'),
  CHECK(ack_receipt_ref GLOB 'http-delivery-ack-v3:*' AND length(ack_receipt_ref)=85),
  CHECK(length(ack_receipt_sha256)=64 AND lower(ack_receipt_sha256)=ack_receipt_sha256
    AND ack_receipt_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(delivery_row_id)='integer' AND delivery_row_id>=1),
  CHECK(delivery_ref GLOB 'http-delivery-v3:*' AND length(delivery_ref)=81),
  CHECK(request_ref GLOB 'http-ingress:*' AND length(request_ref)=77),
  CHECK(response_ref GLOB 'http-response:*' AND length(response_ref)=78),
  CHECK(length(response_sha256)=64 AND lower(response_sha256)=response_sha256
    AND response_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(ack_challenge)=64 AND lower(ack_challenge)=ack_challenge
    AND ack_challenge NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(ack_challenge_sha256)=64
    AND lower(ack_challenge_sha256)=ack_challenge_sha256
    AND ack_challenge_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(wire_sha256)=64 AND lower(wire_sha256)=wire_sha256
    AND wire_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(wire_length)='integer' AND wire_length>=2 AND wire_length<=24576),
  CHECK(client_key_fingerprint GLOB 'p256-spki-sha256:*'
    AND length(client_key_fingerprint)=81),
  CHECK(json_valid(ack_preimage_json) AND json_type(ack_preimage_json,'$')='object'),
  CHECK(length(ack_preimage_sha256)=64 AND lower(ack_preimage_sha256)=ack_preimage_sha256
    AND ack_preimage_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(signature)>=86 AND length(signature)<=88
    AND signature NOT GLOB '*[^A-Za-z0-9_-]*'),
  CHECK(length(signature_sha256)=64 AND lower(signature_sha256)=signature_sha256
    AND signature_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(turn_id IS NULL OR (length(turn_id)>=1 AND length(turn_id)<=180)),
  CHECK(provenance_kind IN ('turn_result','durable_handoff',
    'request_supersession','ingress_rejection')),
  CHECK(length(provenance_ref)>=8 AND length(provenance_ref)<=240
    AND provenance_ref NOT GLOB '*[^A-Za-z0-9._:-]*'),
  CHECK(length(provenance_sha256)=64 AND lower(provenance_sha256)=provenance_sha256
    AND provenance_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(receipt_json) AND json_type(receipt_json,'$')='object'),
  CHECK(length(acknowledged_ts)=24 AND substr(acknowledged_ts,24,1)='Z')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_http_delivery_v3_ack_item
  ON eva_http_delivery_ack_receipts_v3(delivery_row_id,ack_preimage_sha256);
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_acks_no_update
BEFORE UPDATE ON eva_http_delivery_ack_receipts_v3
BEGIN SELECT RAISE(ABORT,'HTTP delivery v3 ACK receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_acks_no_delete
BEFORE DELETE ON eva_http_delivery_ack_receipts_v3
BEGIN SELECT RAISE(ABORT,'HTTP delivery v3 ACK receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_acks_no_replace
BEFORE INSERT ON eva_http_delivery_ack_receipts_v3
WHEN EXISTS (
  SELECT 1 FROM eva_http_delivery_ack_receipts_v3
  WHERE id=NEW.id OR ack_receipt_ref=NEW.ack_receipt_ref
    OR ack_receipt_sha256=NEW.ack_receipt_sha256
    OR delivery_row_id=NEW.delivery_row_id OR delivery_ref=NEW.delivery_ref
    OR request_ref=NEW.request_ref OR response_ref=NEW.response_ref
    OR response_sha256=NEW.response_sha256 OR ack_challenge=NEW.ack_challenge
    OR ack_challenge_sha256=NEW.ack_challenge_sha256
)
BEGIN SELECT RAISE(ABORT,'HTTP delivery v3 ACK identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_acks_valid_reference
BEFORE INSERT ON eva_http_delivery_ack_receipts_v3
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_http_delivery_items_v3 item
    WHERE item.id=NEW.delivery_row_id AND item.delivery_ref=NEW.delivery_ref
      AND item.request_ref=NEW.request_ref AND item.response_ref=NEW.response_ref
      AND item.response_sha256=NEW.response_sha256
      AND item.ack_challenge=NEW.ack_challenge
      AND item.ack_challenge_sha256=NEW.ack_challenge_sha256
      AND item.wire_sha256=NEW.wire_sha256 AND item.wire_length=NEW.wire_length
      AND item.client_key_fingerprint=NEW.client_key_fingerprint
      AND item.turn_id IS NEW.turn_id AND item.provenance_kind=NEW.provenance_kind
      AND item.provenance_ref=NEW.provenance_ref
      AND item.provenance_sha256=NEW.provenance_sha256
  ) THEN RAISE(ABORT,'HTTP delivery v3 ACK has invalid exact item reference') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_http_delivery_attempt_events_v3 attempt
    WHERE attempt.delivery_row_id=NEW.delivery_row_id
      AND attempt.action='write_finished'
      AND attempt.ack_challenge_sha256=NEW.ack_challenge_sha256
      AND attempt.wire_sha256=NEW.wire_sha256
      AND attempt.wire_length=NEW.wire_length
  ) THEN RAISE(ABORT,'HTTP delivery v3 ACK has no finished exact write') END;
  ${CURRENT_ACK_PREIMAGE_RULE}
END;

CREATE TABLE IF NOT EXISTS eva_http_delivery_transmission_events_v3 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  transmission_event_ref TEXT NOT NULL UNIQUE,
  transmission_event_sha256 TEXT NOT NULL UNIQUE,
  delivery_row_id INTEGER NOT NULL,
  delivery_ref TEXT NOT NULL,
  generation INTEGER NOT NULL,
  sequence INTEGER NOT NULL,
  action TEXT NOT NULL,
  mode TEXT NOT NULL,
  token_sha256 TEXT NOT NULL,
  primary_generation INTEGER NOT NULL,
  primary_attempt_ref TEXT NOT NULL,
  ack_challenge_sha256 TEXT NOT NULL,
  wire_sha256 TEXT NOT NULL,
  wire_length INTEGER NOT NULL,
  lease_until_ts TEXT NOT NULL,
  event_json TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  UNIQUE(delivery_row_id,generation,sequence),
  CHECK(contract='HttpDeliveryTransmissionEvent/v3'),
  CHECK(transmission_event_ref GLOB 'http-transmission-event-v3:*'
    AND length(transmission_event_ref)=91),
  CHECK(length(transmission_event_sha256)=64
    AND lower(transmission_event_sha256)=transmission_event_sha256
    AND transmission_event_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(delivery_row_id)='integer' AND delivery_row_id>=1),
  CHECK(delivery_ref GLOB 'http-delivery-v3:*' AND length(delivery_ref)=81),
  CHECK(typeof(generation)='integer'
    AND generation>=1 AND generation<=${MAX_TRANSMISSIONS}),
  CHECK(typeof(sequence)='integer' AND sequence IN (0,1)),
  CHECK(action IN ('issued','write_finished','abandoned')),
  CHECK(mode IN ('pre_ack_retrieval','acknowledged_retrieval')),
  CHECK(length(token_sha256)=64 AND lower(token_sha256)=token_sha256
    AND token_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(primary_generation)='integer'
    AND primary_generation>=1 AND primary_generation<=64),
  CHECK(primary_attempt_ref GLOB 'http-write-attempt-v3:*'
    AND length(primary_attempt_ref)=86),
  CHECK(length(ack_challenge_sha256)=64
    AND lower(ack_challenge_sha256)=ack_challenge_sha256
    AND ack_challenge_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(wire_sha256)=64 AND lower(wire_sha256)=wire_sha256
    AND wire_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(wire_length)='integer' AND wire_length>=2 AND wire_length<=24576),
  CHECK(length(lease_until_ts)=24 AND substr(lease_until_ts,24,1)='Z'),
  CHECK(json_valid(event_json) AND json_type(event_json,'$')='object'),
  CHECK(length(created_ts)=24 AND substr(created_ts,24,1)='Z')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_http_delivery_v3_transmission_history
  ON eva_http_delivery_transmission_events_v3(delivery_row_id,generation,sequence);
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_transmissions_no_update
BEFORE UPDATE ON eva_http_delivery_transmission_events_v3
BEGIN SELECT RAISE(ABORT,'HTTP delivery v3 transmissions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_transmissions_no_delete
BEFORE DELETE ON eva_http_delivery_transmission_events_v3
BEGIN SELECT RAISE(ABORT,'HTTP delivery v3 transmissions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_transmissions_no_replace
BEFORE INSERT ON eva_http_delivery_transmission_events_v3
WHEN EXISTS (
  SELECT 1 FROM eva_http_delivery_transmission_events_v3
  WHERE id=NEW.id OR transmission_event_ref=NEW.transmission_event_ref
    OR transmission_event_sha256=NEW.transmission_event_sha256
    OR (delivery_row_id=NEW.delivery_row_id AND generation=NEW.generation
      AND sequence=NEW.sequence)
)
BEGIN SELECT RAISE(ABORT,'HTTP delivery v3 transmission identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_http_delivery_v3_transmissions_valid_transition
BEFORE INSERT ON eva_http_delivery_transmission_events_v3
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_http_delivery_items_v3 item
    JOIN eva_http_delivery_attempt_events_v3 issued
      ON issued.delivery_row_id=item.id AND issued.generation=NEW.primary_generation
      AND issued.sequence=0 AND issued.action='issued'
      AND issued.attempt_event_ref=NEW.primary_attempt_ref
    JOIN eva_http_delivery_attempt_events_v3 finished
      ON finished.delivery_row_id=issued.delivery_row_id
      AND finished.generation=issued.generation AND finished.sequence=1
      AND finished.action='write_finished'
    WHERE item.id=NEW.delivery_row_id AND item.delivery_ref=NEW.delivery_ref
      AND item.ack_challenge_sha256=NEW.ack_challenge_sha256
      AND item.wire_sha256=NEW.wire_sha256 AND item.wire_length=NEW.wire_length
  ) THEN RAISE(ABORT,'HTTP delivery v3 transmission requires exact finished primary write') END;
  SELECT CASE WHEN NEW.action='issued' AND NEW.mode='pre_ack_retrieval' AND EXISTS (
    SELECT 1 FROM eva_http_delivery_ack_receipts_v3 ack
    WHERE ack.delivery_row_id=NEW.delivery_row_id
  ) THEN RAISE(ABORT,'pre-ACK transmission cannot start after ACK') END;
  SELECT CASE WHEN NEW.action='issued' AND NEW.mode='acknowledged_retrieval' AND NOT EXISTS (
    SELECT 1 FROM eva_http_delivery_ack_receipts_v3 ack
    WHERE ack.delivery_row_id=NEW.delivery_row_id
  ) THEN RAISE(ABORT,'acknowledged transmission requires ACK') END;
  SELECT CASE WHEN NEW.action='issued' AND (
    NEW.sequence<>0
    OR NEW.generation<>COALESCE((
      SELECT max(generation)+1 FROM eva_http_delivery_transmission_events_v3
      WHERE delivery_row_id=NEW.delivery_row_id
    ),1)
    OR NEW.lease_until_ts<=NEW.created_ts
    OR EXISTS (
      SELECT 1 FROM eva_http_delivery_transmission_events_v3 current
      WHERE current.id=(
        SELECT id FROM eva_http_delivery_transmission_events_v3
        WHERE delivery_row_id=NEW.delivery_row_id
        ORDER BY generation DESC,sequence DESC LIMIT 1
      ) AND current.action='issued' AND current.lease_until_ts>NEW.created_ts
    )
  ) THEN RAISE(ABORT,'HTTP delivery v3 transmission acquisition is invalid') END;
  SELECT CASE WHEN NEW.action<>'issued' AND (
    NEW.sequence<>1 OR NEW.generation<>(
      SELECT max(generation) FROM eva_http_delivery_transmission_events_v3
      WHERE delivery_row_id=NEW.delivery_row_id
    )
    OR NOT EXISTS (
      SELECT 1 FROM eva_http_delivery_transmission_events_v3 issued
      WHERE issued.delivery_row_id=NEW.delivery_row_id
        AND issued.generation=NEW.generation AND issued.sequence=0
        AND issued.action='issued' AND issued.token_sha256=NEW.token_sha256
        AND issued.mode=NEW.mode
        AND issued.primary_generation=NEW.primary_generation
        AND issued.primary_attempt_ref=NEW.primary_attempt_ref
        AND issued.ack_challenge_sha256=NEW.ack_challenge_sha256
        AND issued.wire_sha256=NEW.wire_sha256
        AND issued.wire_length=NEW.wire_length
        AND ((NEW.action='write_finished' AND issued.lease_until_ts>NEW.created_ts)
          OR NEW.action='abandoned')
    )
    OR EXISTS (
      SELECT 1 FROM eva_http_delivery_transmission_events_v3 final
      WHERE final.delivery_row_id=NEW.delivery_row_id
        AND final.generation=NEW.generation AND final.sequence=1
    )
  ) THEN RAISE(ABORT,'HTTP delivery v3 transmission completion is invalid') END;
END;
`;

const REQUIRED_COLUMNS = Object.freeze({
  [ITEM_TABLE]: [
    'id', 'contract', 'delivery_ref', 'delivery_sha256', 'authority_ref',
    'authority_sha256', 'request_ref', 'response_ref', 'response_sha256',
    'status', 'content_type', 'wire_bytes', 'wire_sha256', 'wire_length',
    'ack_challenge', 'ack_challenge_sha256', 'client_public_key_json',
    'client_key_fingerprint', 'turn_id', 'provenance_kind', 'provenance_ref',
    'provenance_sha256', 'item_json', 'prepared_ts',
  ],
  [ATTEMPT_TABLE]: [
    'id', 'contract', 'attempt_event_ref', 'attempt_event_sha256',
    'delivery_row_id', 'delivery_ref', 'generation', 'sequence', 'action',
    'token_sha256', 'ack_challenge_sha256', 'wire_sha256', 'wire_length',
    'lease_until_ts', 'event_json', 'created_ts',
  ],
  [ACK_TABLE]: [
    'id', 'contract', 'ack_receipt_ref', 'ack_receipt_sha256',
    'delivery_row_id', 'delivery_ref', 'request_ref', 'response_ref',
    'response_sha256', 'ack_challenge', 'ack_challenge_sha256', 'wire_sha256',
    'wire_length', 'client_key_fingerprint', 'ack_preimage_json',
    'ack_preimage_sha256', 'signature', 'signature_sha256', 'turn_id',
    'provenance_kind', 'provenance_ref', 'provenance_sha256', 'receipt_json',
    'acknowledged_ts',
  ],
  [TRANSMISSION_TABLE]: [
    'id', 'contract', 'transmission_event_ref', 'transmission_event_sha256',
    'delivery_row_id', 'delivery_ref', 'generation', 'sequence', 'action', 'mode',
    'token_sha256', 'primary_generation', 'primary_attempt_ref',
    'ack_challenge_sha256', 'wire_sha256', 'wire_length', 'lease_until_ts',
    'event_json', 'created_ts',
  ],
});
const INDEX_SIGNATURES = Object.freeze({
  [ITEM_TABLE]: [
    { name: INDEXES[0], origin: 'c', columns: ['request_ref', 'response_sha256'] },
    ...[
      'delivery_ref', 'delivery_sha256', 'authority_ref', 'authority_sha256',
      'request_ref', 'response_ref', 'response_sha256', 'wire_sha256',
      'ack_challenge', 'ack_challenge_sha256',
    ].map((column) => ({ origin: 'u', columns: [column] })),
  ],
  [ATTEMPT_TABLE]: [
    { name: INDEXES[1], origin: 'c', columns: ['delivery_row_id', 'generation', 'sequence'] },
    { origin: 'u', columns: ['attempt_event_ref'] },
    { origin: 'u', columns: ['attempt_event_sha256'] },
    { origin: 'u', columns: ['delivery_row_id', 'generation', 'sequence'] },
  ],
  [ACK_TABLE]: [
    { name: INDEXES[2], origin: 'c', columns: ['delivery_row_id', 'ack_preimage_sha256'] },
    ...[
      'ack_receipt_ref', 'ack_receipt_sha256', 'delivery_row_id', 'delivery_ref',
      'request_ref', 'response_ref', 'response_sha256', 'ack_challenge',
      'ack_challenge_sha256',
    ].map((column) => ({ origin: 'u', columns: [column] })),
  ],
  [TRANSMISSION_TABLE]: [
    { name: INDEXES[3], origin: 'c',
      columns: ['delivery_row_id', 'generation', 'sequence'] },
    { origin: 'u', columns: ['transmission_event_ref'] },
    { origin: 'u', columns: ['transmission_event_sha256'] },
    { origin: 'u', columns: ['delivery_row_id', 'generation', 'sequence'] },
  ],
});

function freeze(value) {
  if (!value || typeof value !== 'object' || Object.isFrozen(value)
    || ArrayBuffer.isView(value)) return value;
  Object.freeze(value);
  for (const child of Object.values(value)) freeze(child);
  return value;
}

function exactKeys(value, keys) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).sort().join('\0') === [...keys].sort().join('\0');
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
  return deliveryDb.prepare(`SELECT 1 FROM sqlite_temp_master
    WHERE name IN (${placeholders}) OR tbl_name IN (${placeholders})
      OR name GLOB 'eva_http_delivery*v3*'
      OR tbl_name GLOB 'eva_http_delivery*v3*'
      OR name GLOB 'idx_http_delivery_v3*'
      OR tbl_name GLOB 'idx_http_delivery_v3*'
    LIMIT 1`).get(...PROTECTED, ...PROTECTED);
}

const CURRENT_FINISHED_WRITE_GUARD = `) AND (current.action='write_finished'
        OR (current.action='issued' AND current.lease_until_ts>NEW.created_ts))`;
const LEGACY_FINISHED_WRITE_GUARD = `) AND current.action IN ('issued','write_finished')
        AND current.lease_until_ts>NEW.created_ts`;

function migrateExactFinishedWriteGuard() {
  if (tempShadow()) return false;
  const name = 'eva_http_delivery_v3_attempts_valid_transition';
  const stored = deliveryDb.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(name)?.sql;
  if (!stored) return false;
  const expected = expectedDdl('trigger', name);
  const legacy = expected.replace(CURRENT_FINISHED_WRITE_GUARD, LEGACY_FINISHED_WRITE_GUARD);
  if (legacy === expected || normalizedDdl(stored) !== normalizedDdl(legacy)) return false;
  deliveryDb.exec(`DROP TRIGGER main.${name};\n${expected}`);
  return true;
}

function exactStoredDdl(type, name, expected = expectedDdl(type, name)) {
  const stored = deliveryDb.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type=? AND name=?`).get(type, name)?.sql;
  return Boolean(stored && expected && normalizedDdl(stored) === normalizedDdl(expected));
}

function exactReservedNamespace(expectedNames) {
  const names = deliveryDb.prepare(`SELECT name FROM main.sqlite_master
    WHERE name GLOB 'eva_http_delivery*v3*'
      OR name GLOB 'idx_http_delivery_v3*'
    ORDER BY name`).all().map(({ name }) => name);
  return names.join('\0') === [...expectedNames].sort().join('\0');
}

function exactSchemaObjects(tables, indexes, triggers, triggerDdl = new Map()) {
  if (tempShadow()) return false;
  for (const table of tables) {
    const expected = REQUIRED_COLUMNS[table];
    const actual = deliveryDb.prepare(`PRAGMA main.table_info('${table}')`).all()
      .map((column) => column.name);
    if (actual.join('\0') !== expected.join('\0')
      || !exactStoredDdl('table', table)
      || !exactSqliteIndexSignatures(deliveryDb, table, expected, INDEX_SIGNATURES[table])) {
      return false;
    }
  }
  if (!indexes.every((name) => exactStoredDdl('index', name))) return false;
  if (!triggers.every((name) => exactStoredDdl(
    'trigger', name, triggerDdl.get(name) || expectedDdl('trigger', name),
  ))) return false;
  const placeholders = tables.map(() => '?').join(',');
  const objects = deliveryDb.prepare(`SELECT type,name FROM main.sqlite_master
    WHERE tbl_name IN (${placeholders}) AND type IN ('index','trigger')`).all(...tables);
  const actualIndexes = objects.filter(({ type, name }) => type === 'index'
    && !name.startsWith('sqlite_autoindex_')).map(({ name }) => name).sort();
  const actualTriggers = objects.filter(({ type }) => type === 'trigger')
    .map(({ name }) => name).sort();
  return actualIndexes.join('\0') === [...indexes].sort().join('\0')
    && actualTriggers.join('\0') === [...triggers].sort().join('\0')
    && exactReservedNamespace(new Set([...tables, ...indexes, ...triggers]));
}

function exactLegacySchema() {
  const indexes = INDEXES.slice(0, 3);
  const triggers = TRIGGERS.slice(0, 12);
  const attemptName = 'eva_http_delivery_v3_attempts_valid_transition';
  const ackName = 'eva_http_delivery_v3_acks_valid_reference';
  const currentAttempt = expectedDdl('trigger', attemptName);
  const oldAttempt = currentAttempt.replace(
    CURRENT_FINISHED_WRITE_GUARD, LEGACY_FINISHED_WRITE_GUARD,
  );
  const oldAck = expectedDdl('trigger', ackName).replace(
    CURRENT_ACK_PREIMAGE_RULE, LEGACY_ACK_PREIMAGE_RULE,
  );
  for (const attemptDdl of [currentAttempt, oldAttempt]) {
    const overrides = new Map([[attemptName, attemptDdl], [ackName, oldAck]]);
    if (exactSchemaObjects(LEGACY_TABLES, indexes, triggers, overrides)) {
      return { attemptDdl, currentAttempt, attemptName, ackName };
    }
  }
  return null;
}

function transmissionSchemaSql() {
  return [
    expectedDdl('table', TRANSMISSION_TABLE),
    expectedDdl('index', INDEXES[3]),
    ...TRIGGERS.slice(12).map((name) => expectedDdl('trigger', name)),
  ].join('\n');
}

function migrateExactLegacySchema() {
  const legacy = exactLegacySchema();
  if (!legacy) return false;
  if (normalizedDdl(legacy.attemptDdl) !== normalizedDdl(legacy.currentAttempt)) {
    deliveryDb.exec(`DROP TRIGGER main.${legacy.attemptName};\n${legacy.currentAttempt}`);
  }
  deliveryDb.exec(`DROP TRIGGER main.${legacy.ackName};\n${
    expectedDdl('trigger', legacy.ackName)}\n${transmissionSchemaSql()}`);
  return true;
}

function exactCurrentSchemaWithLegacyAttempt() {
  const name = 'eva_http_delivery_v3_attempts_valid_transition';
  const current = expectedDdl('trigger', name);
  const legacy = current.replace(CURRENT_FINISHED_WRITE_GUARD, LEGACY_FINISHED_WRITE_GUARD);
  return legacy !== current && exactSchemaObjects(
    TABLES, INDEXES, TRIGGERS, new Map([[name, legacy]]),
  );
}

function schemaFailure() {
  if (initializationFailure) return initializationFailure;
  let dependency;
  try { dependency = httpResponseAuthorityStoreStatus(); } catch (error) {
    return `http_delivery_v3_response_authority_status_failed:${String(error?.message || error)}`;
  }
  if (!dependency?.available) {
    return `http_delivery_v3_response_authority_unavailable:${dependency?.reason || 'unknown'}`;
  }
  if (tempShadow()) return 'http_delivery_v3_schema_temp_shadow';
  for (const [table, expected] of Object.entries(REQUIRED_COLUMNS)) {
    const actual = deliveryDb.prepare(`PRAGMA main.table_info('${table}')`).all()
      .map((column) => column.name);
    if (actual.join('\0') !== expected.join('\0')) return 'http_delivery_v3_schema_columns_invalid';
    if (!exactSqliteIndexSignatures(
      deliveryDb, table, expected, INDEX_SIGNATURES[table],
    )) return `http_delivery_v3_schema_index_signatures_invalid:${table}`;
  }
  for (const [type, names] of [
    ['table', TABLES], ['index', INDEXES], ['trigger', TRIGGERS],
  ]) {
    for (const name of names) {
      const object = deliveryDb.prepare(`SELECT sql FROM main.sqlite_master
        WHERE type=? AND name=?`).get(type, name);
      if (!object || normalizedDdl(object.sql) !== normalizedDdl(expectedDdl(type, name))) {
        return `http_delivery_v3_schema_ddl_invalid:${type}:${name}`;
      }
    }
  }
  const placeholders = TABLES.map(() => '?').join(',');
  const objects = deliveryDb.prepare(`SELECT type,name FROM main.sqlite_master
    WHERE tbl_name IN (${placeholders}) AND (type='index' OR type='trigger')`)
    .all(...TABLES);
  const indexes = objects.filter((item) => item.type === 'index'
    && !item.name.startsWith('sqlite_autoindex_')).map((item) => item.name).sort();
  const triggers = objects.filter((item) => item.type === 'trigger')
    .map((item) => item.name).sort();
  if (indexes.join('\0') !== [...INDEXES].sort().join('\0')
    || triggers.join('\0') !== [...TRIGGERS].sort().join('\0')) {
    return 'http_delivery_v3_schema_objects_invalid';
  }
  const expectedNames = new Set([...TABLES, ...INDEXES, ...TRIGGERS]);
  const reserved = deliveryDb.prepare(`SELECT name FROM main.sqlite_master
    WHERE name GLOB 'eva_http_delivery*v3*'
      OR name GLOB 'idx_http_delivery_v3*'`).all();
  if (reserved.some(({ name }) => !expectedNames.has(name))) {
    return 'http_delivery_v3_schema_namespace_invalid';
  }
  return '';
}

function requireStore() {
  const reason = schemaFailure();
  if (reason) throw new Error(`HTTP delivery v3 store unavailable: ${reason}`);
}

function itemBody(consumed) {
  const response = consumed?.response;
  const wire = consumed?.wire;
  const key = normalizeHttpClientAckPublicKey(consumed?.clientPublicKey);
  if (consumed?.contract !== 'ConsumedHttpResponseAuthority/v3'
    || response?.contract !== 'HttpIngressResponseReceipt/v3'
    || wire?.contract !== 'CanonicalHttpWire/v3'
    || !AUTHORITY_REF.test(response.authorityRef)
    || !SHA256.test(response.authoritySha256) || !REQUEST_REF.test(response.requestRef)
    || !RESPONSE_REF.test(response.responseRef) || !SHA256.test(response.responseSha256)
    || !DELIVERY_REF.test(response.deliveryRef)
    || !SHA256.test(wire.wireSha256) || wire.contentType !== CONTENT_TYPE
    || !Buffer.isBuffer(wire.bodyBytes)
    || canonicalHttpWireSha256({
      status: wire.status, contentType: wire.contentType, bodyBytes: wire.bodyBytes,
    }) !== wire.wireSha256
    || wire.bodyBytes.length !== wire.wireLength
    || response.wireSha256 !== wire.wireSha256
    || response.wireLength !== wire.wireLength
    || key.keyFingerprint !== response.clientKeyFingerprint) {
    throw new Error('invalid consumed HTTP ingress response authority v3');
  }
  let envelope;
  try { envelope = JSON.parse(wire.bodyBytes.toString('utf8'))._httpDelivery; } catch { envelope = null; }
  if (!exactKeys(envelope, [
    'contract', 'requestRef', 'responseRef', 'responseSha256',
    'deliveryRef', 'challenge', 'keyFingerprint',
  ]) || envelope.contract !== 'HttpClientAckChallenge/v3'
    || envelope.requestRef !== response.requestRef
    || envelope.responseRef !== response.responseRef
    || envelope.responseSha256 !== response.responseSha256
    || envelope.deliveryRef !== response.deliveryRef
    || !CHALLENGE.test(envelope.challenge)
    || hash(envelope.challenge) !== response.ackChallengeSha256
    || envelope.keyFingerprint !== response.clientKeyFingerprint) {
    throw new Error('invalid exact HTTP client ACK challenge envelope');
  }
  const body = {
    contract: 'PreparedHttpDelivery/v3', authorityRef: response.authorityRef,
    authoritySha256: response.authoritySha256, requestRef: response.requestRef,
    responseRef: response.responseRef, responseSha256: response.responseSha256,
    deliveryRef: response.deliveryRef, status: wire.status,
    contentType: wire.contentType, wireSha256: wire.wireSha256,
    wireLength: wire.wireLength, ackChallengeSha256: response.ackChallengeSha256,
    clientKeyFingerprint: response.clientKeyFingerprint, turnId: response.turnId,
    provenance: response.provenance,
  };
  const itemJson = canonicalJson(body);
  return freeze({
    body, itemJson, deliverySha256: hash(itemJson),
    wireBytes: Buffer.from(wire.bodyBytes), ackChallenge: envelope.challenge,
    clientPublicKeyJson: key.publicKeyJson,
  });
}

function validateItem(row) {
  if (!row || row.contract !== 'PreparedHttpDelivery/v3'
    || !Number.isSafeInteger(Number(row.id)) || Number(row.id) < 1
    || !DELIVERY_REF.test(row.delivery_ref) || !KEY_FINGERPRINT.test(row.client_key_fingerprint)) {
    throw new Error('invalid HTTP delivery v3 item');
  }
  let body;
  let wireBody;
  let key;
  try {
    body = JSON.parse(row.item_json);
    wireBody = JSON.parse(Buffer.from(row.wire_bytes).toString('utf8'));
    key = normalizeHttpClientAckPublicKey(JSON.parse(row.client_public_key_json));
  } catch { throw new Error('invalid HTTP delivery v3 item'); }
  const envelope = wireBody._httpDelivery;
  const wire = Buffer.from(row.wire_bytes);
  if (canonicalJson(body) !== row.item_json || hash(row.item_json) !== row.delivery_sha256
    || body.contract !== row.contract || body.deliveryRef !== row.delivery_ref
    || body.authorityRef !== row.authority_ref || body.authoritySha256 !== row.authority_sha256
    || body.requestRef !== row.request_ref || body.responseRef !== row.response_ref
    || body.responseSha256 !== row.response_sha256 || body.status !== Number(row.status)
    || body.contentType !== row.content_type || body.wireSha256 !== row.wire_sha256
    || body.wireLength !== Number(row.wire_length)
    || body.ackChallengeSha256 !== row.ack_challenge_sha256
    || body.clientKeyFingerprint !== row.client_key_fingerprint
    || body.turnId !== row.turn_id || body.provenance?.kind !== row.provenance_kind
    || body.provenance?.ref !== row.provenance_ref
    || body.provenance?.sha256 !== row.provenance_sha256
    || canonicalJson(wireBody) !== wire.toString('utf8')
    || canonicalHttpWireSha256({
      status: Number(row.status), contentType: row.content_type, bodyBytes: wire,
    }) !== row.wire_sha256
    || wire.length !== Number(row.wire_length)
    || key.publicKeyJson !== row.client_public_key_json
    || key.keyFingerprint !== row.client_key_fingerprint
    || envelope?.contract !== 'HttpClientAckChallenge/v3'
    || envelope.requestRef !== row.request_ref || envelope.responseRef !== row.response_ref
    || envelope.responseSha256 !== row.response_sha256
    || envelope.deliveryRef !== row.delivery_ref
    || envelope.challenge !== row.ack_challenge
    || hash(row.ack_challenge) !== row.ack_challenge_sha256
    || envelope.keyFingerprint !== row.client_key_fingerprint) {
    throw new Error('invalid canonical HTTP delivery v3 item');
  }
  return freeze({ body, key: key.publicKey, wire });
}

function itemReceipt(row) {
  const normalized = validateItem(row);
  return freeze({
    contract: 'HttpDeliveryReceipt/v3', deliveryRef: row.delivery_ref,
    sha256: row.delivery_sha256, authorityRef: row.authority_ref,
    authoritySha256: row.authority_sha256, requestRef: row.request_ref,
    responseRef: row.response_ref, responseSha256: row.response_sha256,
    status: Number(row.status), contentType: row.content_type,
    wireSha256: row.wire_sha256, wireLength: Number(row.wire_length),
    ackChallengeSha256: row.ack_challenge_sha256,
    clientKeyFingerprint: row.client_key_fingerprint, turnId: row.turn_id,
    provenance: freeze({ ...normalized.body.provenance }), preparedAt: row.prepared_ts,
  });
}

const RECEIPT_KEYS = Object.freeze([
  'contract', 'deliveryRef', 'sha256', 'authorityRef', 'authoritySha256',
  'requestRef', 'responseRef', 'responseSha256', 'status', 'contentType',
  'wireSha256', 'wireLength', 'ackChallengeSha256', 'clientKeyFingerprint',
  'turnId', 'provenance', 'preparedAt',
]);

function itemForReceipt(receipt) {
  if (!exactKeys(receipt, RECEIPT_KEYS) || receipt.contract !== 'HttpDeliveryReceipt/v3'
    || !DELIVERY_REF.test(receipt.deliveryRef) || !SHA256.test(receipt.sha256)) {
    throw new TypeError('invalid exact HTTP delivery v3 receipt');
  }
  const row = deliveryDb.prepare(`SELECT * FROM ${mainTable(ITEM_TABLE)}
    WHERE delivery_ref=? AND delivery_sha256=?`).get(receipt.deliveryRef, receipt.sha256);
  if (!row) throw new Error('HTTP delivery v3 receipt has no durable item');
  const canonical = itemReceipt(row);
  if (canonicalJson(receipt) !== canonicalJson(canonical)) {
    throw new Error('HTTP delivery v3 receipt conflicts with durable item');
  }
  return row;
}

function attemptBody({ row, generation, sequence, action, tokenSha256, leaseUntil }) {
  return {
    contract: 'HttpDeliveryWriteAttemptEvent/v3', deliveryRef: row.delivery_ref,
    generation, sequence, action, tokenSha256,
    ackChallengeSha256: row.ack_challenge_sha256,
    wireSha256: row.wire_sha256, wireLength: Number(row.wire_length), leaseUntil,
  };
}

function insertAttempt(input, createdAt) {
  const body = attemptBody(input);
  const eventJson = canonicalJson(body);
  const eventSha256 = hash(eventJson);
  const attemptEventRef = `http-write-attempt-v3:${eventSha256}`;
  deliveryDb.prepare(`INSERT INTO ${mainTable(ATTEMPT_TABLE)} (
    contract,attempt_event_ref,attempt_event_sha256,delivery_row_id,delivery_ref,
    generation,sequence,action,token_sha256,ack_challenge_sha256,wire_sha256,
    wire_length,lease_until_ts,event_json,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    body.contract, attemptEventRef, eventSha256, Number(input.row.id),
    input.row.delivery_ref, input.generation, input.sequence, input.action,
    input.tokenSha256, input.row.ack_challenge_sha256, input.row.wire_sha256,
    Number(input.row.wire_length), input.leaseUntil, eventJson, createdAt,
  );
  return deliveryDb.prepare(`SELECT * FROM ${mainTable(ATTEMPT_TABLE)}
    WHERE attempt_event_ref=?`).get(attemptEventRef);
}

function latestAttempt(row) {
  return deliveryDb.prepare(`SELECT * FROM ${mainTable(ATTEMPT_TABLE)}
    WHERE delivery_row_id=? ORDER BY generation DESC,sequence DESC LIMIT 1`).get(row.id);
}

function finishedPrimaryWrite(row) {
  const finished = latestAttempt(row);
  if (!finished) return null;
  validateAttempt(finished, row);
  if (finished.action !== 'write_finished' || Number(finished.sequence) !== 1) return null;
  const issued = deliveryDb.prepare(`SELECT * FROM ${mainTable(ATTEMPT_TABLE)}
    WHERE delivery_row_id=? AND generation=? AND sequence=0`).get(
    row.id, finished.generation,
  );
  validateAttempt(issued, row);
  if (issued.action !== 'issued' || issued.token_sha256 !== finished.token_sha256) {
    throw new Error('invalid finished HTTP delivery v3 primary write');
  }
  return { issued, finished, generation: Number(issued.generation) };
}

function transmissionBody({ row, generation, sequence, action, mode, tokenSha256,
  primaryGeneration, primaryAttemptRef, leaseUntil }) {
  return {
    contract: 'HttpDeliveryTransmissionEvent/v3', deliveryRef: row.delivery_ref,
    generation, sequence, action, mode, tokenSha256,
    primaryGeneration, primaryAttemptRef,
    ackChallengeSha256: row.ack_challenge_sha256,
    wireSha256: row.wire_sha256, wireLength: Number(row.wire_length), leaseUntil,
  };
}

function insertTransmission(input, createdAt) {
  const body = transmissionBody(input);
  const eventJson = canonicalJson(body);
  const eventSha256 = hash(eventJson);
  const eventRef = `http-transmission-event-v3:${eventSha256}`;
  deliveryDb.prepare(`INSERT INTO ${mainTable(TRANSMISSION_TABLE)} (
    contract,transmission_event_ref,transmission_event_sha256,delivery_row_id,
    delivery_ref,generation,sequence,action,mode,token_sha256,primary_generation,
    primary_attempt_ref,ack_challenge_sha256,wire_sha256,wire_length,lease_until_ts,
    event_json,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    body.contract, eventRef, eventSha256, Number(input.row.id), input.row.delivery_ref,
    input.generation, input.sequence, input.action, input.mode, input.tokenSha256,
    input.primaryGeneration, input.primaryAttemptRef, input.row.ack_challenge_sha256,
    input.row.wire_sha256, Number(input.row.wire_length), input.leaseUntil,
    eventJson, createdAt,
  );
  return deliveryDb.prepare(`SELECT * FROM ${mainTable(TRANSMISSION_TABLE)}
    WHERE transmission_event_ref=?`).get(eventRef);
}

function latestTransmission(row) {
  return deliveryDb.prepare(`SELECT * FROM ${mainTable(TRANSMISSION_TABLE)}
    WHERE delivery_row_id=? ORDER BY generation DESC,sequence DESC LIMIT 1`).get(row.id);
}

function validateTransmission(event, row) {
  let stored;
  try { stored = JSON.parse(event?.event_json); } catch { stored = null; }
  const expected = event ? transmissionBody({
    row, generation: Number(event.generation), sequence: Number(event.sequence),
    action: event.action, mode: event.mode, tokenSha256: event.token_sha256,
    primaryGeneration: Number(event.primary_generation),
    primaryAttemptRef: event.primary_attempt_ref, leaseUntil: event.lease_until_ts,
  }) : null;
  const primary = event && finishedPrimaryWrite(row);
  if (!event || event.contract !== 'HttpDeliveryTransmissionEvent/v3'
    || !TRANSMISSION_REF.test(event.transmission_event_ref)
    || !TRANSMISSION_ACTIONS.has(event.action) || !TRANSMISSION_MODES.has(event.mode)
    || event.delivery_ref !== row.delivery_ref
    || event.ack_challenge_sha256 !== row.ack_challenge_sha256
    || event.wire_sha256 !== row.wire_sha256
    || Number(event.wire_length) !== Number(row.wire_length)
    || event.transmission_event_ref
      !== `http-transmission-event-v3:${event.transmission_event_sha256}`
    || hash(event.event_json) !== event.transmission_event_sha256
    || !stored || canonicalJson(stored) !== event.event_json
    || canonicalJson(expected) !== event.event_json
    || !Number.isSafeInteger(Number(event.generation))
    || Number(event.generation) < 1 || Number(event.generation) > MAX_TRANSMISSIONS
    || !primary || Number(event.primary_generation) !== primary.generation
    || event.primary_attempt_ref !== primary.issued.attempt_event_ref) {
    throw new Error('invalid HTTP delivery v3 transmission');
  }
  return event;
}

function validateAttempt(event, row) {
  let stored;
  try { stored = JSON.parse(event?.event_json); } catch { stored = null; }
  const expected = event ? attemptBody({
    row, generation: Number(event.generation), sequence: Number(event.sequence),
    action: event.action, tokenSha256: event.token_sha256,
    leaseUntil: event.lease_until_ts,
  }) : null;
  if (!event || event.contract !== 'HttpDeliveryWriteAttemptEvent/v3'
    || !ATTEMPT_REF.test(event.attempt_event_ref) || !ATTEMPT_ACTIONS.has(event.action)
    || event.delivery_ref !== row.delivery_ref
    || event.ack_challenge_sha256 !== row.ack_challenge_sha256
    || event.wire_sha256 !== row.wire_sha256
    || Number(event.wire_length) !== Number(row.wire_length)
    || event.attempt_event_ref !== `http-write-attempt-v3:${event.attempt_event_sha256}`
    || hash(event.event_json) !== event.attempt_event_sha256
    || !stored || canonicalJson(stored) !== event.event_json
    || canonicalJson(expected) !== event.event_json
    || !Number.isSafeInteger(Number(event.generation))
    || Number(event.generation) < 1 || Number(event.generation) > MAX_GENERATIONS) {
    throw new Error('invalid HTTP delivery v3 attempt');
  }
  return event;
}

function ackRow(row) {
  return deliveryDb.prepare(`SELECT * FROM ${mainTable(ACK_TABLE)}
    WHERE delivery_row_id=?`).get(row.id);
}

/**
 * @typedef {{
 *   contract?: unknown,
 *   ack_receipt_ref?: unknown,
 *   ack_receipt_sha256?: unknown,
 *   delivery_ref?: unknown,
 *   request_ref?: unknown,
 *   response_ref?: unknown,
 *   response_sha256?: unknown,
 *   ack_challenge?: unknown,
 *   ack_challenge_sha256?: unknown,
 *   wire_sha256?: unknown,
 *   wire_length?: unknown,
 *   client_key_fingerprint?: unknown,
 *   turn_id?: unknown,
 *   provenance_kind?: unknown,
 *   provenance_ref?: unknown,
 *   provenance_sha256?: unknown,
 *   receipt_json?: unknown,
 *   ack_preimage_json?: unknown,
 *   ack_preimage_sha256?: unknown,
 *   signature_sha256?: unknown,
 *   signature?: unknown,
 * }} StoredAckShape
 *
 * @typedef {{
 *   delivery_ref?: unknown,
 *   request_ref?: unknown,
 *   response_ref?: unknown,
 *   response_sha256?: unknown,
 *   ack_challenge?: unknown,
 *   ack_challenge_sha256?: unknown,
 *   wire_sha256?: unknown,
 *   wire_length?: unknown,
 *   client_key_fingerprint?: unknown,
 *   turn_id?: unknown,
 *   provenance_kind?: unknown,
 *   provenance_ref?: unknown,
 *   provenance_sha256?: unknown,
 * }} AckDeliveryRowShape
 *
 * @typedef {{
 *   contract?: unknown,
 *   kind?: unknown,
 *   ref?: unknown,
 *   sha256?: unknown,
 * }} AckReceiptProvenanceShape
 *
 * @typedef {{
 *   contract?: unknown,
 *   deliveryRef?: unknown,
 *   requestRef?: unknown,
 *   responseRef?: unknown,
 *   responseSha256?: unknown,
 *   ackChallengeSha256?: unknown,
 *   wireSha256?: unknown,
 *   wireLength?: unknown,
 *   clientKeyFingerprint?: unknown,
 *   ackPreimageSha256?: unknown,
 *   signatureSha256?: unknown,
 *   turnId?: unknown,
 *   provenance?: unknown,
 *   writeAttemptRef?: unknown,
 *   writeGeneration?: unknown,
 * }} AckReceiptBodyShape
 *
 * @typedef {{
 *   preimage?: unknown,
 *   preimageSha256?: unknown,
 *   signatureSha256?: unknown,
 *   keyFingerprint?: unknown,
 *   ack?: unknown,
 * }} VerifiedAckShape
 *
 * @typedef {{
 *   attempt_event_ref?: unknown,
 * }} PrimaryIssuedWriteShape
 *
 * @typedef {{
 *   issued?: unknown,
 *   generation?: unknown,
 * }} PrimaryWriteShape
 *
 * @typedef {{
 *   writeAttemptRef?: unknown,
 *   writeGeneration?: unknown,
 * }} AckWriteCoordinatesShape
 *
 * @typedef {{
 *   body: AckReceiptBodyShape,
 *   verified: VerifiedAckShape,
 *   primary: PrimaryWriteShape,
 *   receiptHasWriteBinding: boolean,
 *   legacyReceipt: boolean,
 * }} AckWriteBindingInput
 */

/**
 * @param {StoredAckShape} ack
 * @param {AckDeliveryRowShape} row
 */
function invalidStoredAckIdentity(ack, row) {
  return ack.contract !== 'HttpClientAckReceipt/v3'
    || typeof ack.ack_receipt_ref !== 'string'
    || !ACK_REF.test(ack.ack_receipt_ref)
    || ack.delivery_ref !== row.delivery_ref || ack.request_ref !== row.request_ref
    || ack.response_ref !== row.response_ref || ack.response_sha256 !== row.response_sha256
    || ack.ack_challenge !== row.ack_challenge
    || ack.ack_challenge_sha256 !== row.ack_challenge_sha256
    || ack.wire_sha256 !== row.wire_sha256
    || Number(ack.wire_length) !== Number(row.wire_length)
    || ack.client_key_fingerprint !== row.client_key_fingerprint
    || ack.turn_id !== row.turn_id || ack.provenance_kind !== row.provenance_kind
    || ack.provenance_ref !== row.provenance_ref
    || ack.provenance_sha256 !== row.provenance_sha256
    || typeof ack.ack_receipt_sha256 !== 'string'
    || ack.ack_receipt_ref !== `http-delivery-ack-v3:${ack.ack_receipt_sha256}`
    || typeof ack.receipt_json !== 'string'
    || hash(ack.receipt_json) !== ack.ack_receipt_sha256;
}

/**
 * @param {StoredAckShape} ack
 * @param {VerifiedAckShape} verified
 */
function invalidVerifiedAck(ack, verified) {
  return verified.preimage !== ack.ack_preimage_json
    || verified.preimageSha256 !== ack.ack_preimage_sha256
    || verified.signatureSha256 !== ack.signature_sha256
    || verified.keyFingerprint !== ack.client_key_fingerprint;
}

/**
 * @param {AckReceiptBodyShape} body
 * @param {StoredAckShape} ack
 * @param {boolean} legacyReceipt
 */
function invalidAckReceiptBody(body, ack, legacyReceipt) {
  const provenance = body && typeof body === 'object' && !Array.isArray(body)
    && body.provenance && typeof body.provenance === 'object'
    && !Array.isArray(body.provenance)
    ? /** @type {AckReceiptProvenanceShape} */ (body.provenance) : null;
  return !body || typeof body !== 'object' || Array.isArray(body)
    || canonicalJson(body) !== ack.receipt_json
    || body.contract !== ack.contract || body.deliveryRef !== ack.delivery_ref
    || body.requestRef !== ack.request_ref || body.responseRef !== ack.response_ref
    || body.responseSha256 !== ack.response_sha256
    || body.ackChallengeSha256 !== ack.ack_challenge_sha256
    || body.wireSha256 !== ack.wire_sha256
    || body.wireLength !== Number(ack.wire_length)
    || body.clientKeyFingerprint !== ack.client_key_fingerprint
    || body.ackPreimageSha256 !== ack.ack_preimage_sha256
    || (legacyReceipt && body.signatureSha256 !== ack.signature_sha256)
    || body.turnId !== ack.turn_id || provenance?.kind !== ack.provenance_kind
    || provenance?.ref !== ack.provenance_ref
    || provenance?.sha256 !== ack.provenance_sha256
    || !exactKeys(provenance, ['contract', 'kind', 'ref', 'sha256'])
    || provenance.contract !== 'HttpResultProvenance/v1';
}

/** @param {AckWriteBindingInput} input */
function invalidAckWriteBinding({
  body, verified, primary, receiptHasWriteBinding, legacyReceipt,
}) {
  const verifiedAck = verified.ack && typeof verified.ack === 'object'
    && !Array.isArray(verified.ack)
    ? /** @type {AckWriteCoordinatesShape} */ (verified.ack) : null;
  const primaryIssued = primary.issued && typeof primary.issued === 'object'
    && !Array.isArray(primary.issued)
    ? /** @type {PrimaryIssuedWriteShape} */ (primary.issued) : null;
  return (receiptHasWriteBinding && (
    !verifiedAck || !primaryIssued
    || body.writeAttemptRef !== primaryIssued.attempt_event_ref
    || body.writeGeneration !== primary.generation
    || verifiedAck.writeAttemptRef !== body.writeAttemptRef
    || verifiedAck.writeGeneration !== body.writeGeneration
  )) || (!receiptHasWriteBinding && !legacyReceipt);
}

function validateAck(ack, row) {
  if (!ack) return null;
  let body;
  let preimage;
  let verified;
  try {
    body = JSON.parse(ack.receipt_json);
    preimage = JSON.parse(ack.ack_preimage_json);
    const hasWriteBinding = Object.hasOwn(preimage || {}, 'writeAttemptRef')
      || Object.hasOwn(preimage || {}, 'writeGeneration');
    verified = (hasWriteBinding ? verifyHttpClientAck : verifyLegacyHttpClientAck)(
      { ...preimage, signature: ack.signature }, JSON.parse(row.client_public_key_json),
    );
  } catch { throw new Error('invalid durable HTTP client ACK receipt v3'); }
  const primary = finishedPrimaryWrite(row);
  const newReceiptKeys = [
    'contract', 'deliveryRef', 'requestRef', 'responseRef', 'responseSha256',
    'ackChallengeSha256', 'wireSha256', 'wireLength', 'clientKeyFingerprint',
    'ackPreimageSha256', 'writeAttemptRef', 'writeGeneration',
    'turnId', 'provenance',
  ];
  const legacyReceiptKeys = [
    ...newReceiptKeys.filter((key) => !['writeAttemptRef', 'writeGeneration'].includes(key)),
    'signatureSha256',
  ];
  const receiptHasWriteBinding = exactKeys(body, newReceiptKeys);
  const legacyReceipt = exactKeys(body, legacyReceiptKeys);
  if (invalidStoredAckIdentity(ack, row)
    || invalidVerifiedAck(ack, verified)
    || invalidAckReceiptBody(body, ack, legacyReceipt)
    || !primary
    || invalidAckWriteBinding({
      body, verified, primary, receiptHasWriteBinding, legacyReceipt,
    })) {
    throw new Error('invalid durable HTTP client ACK receipt v3');
  }
  return freeze({
    ...body, ackReceiptRef: ack.ack_receipt_ref,
    sha256: ack.ack_receipt_sha256, acknowledgedAt: ack.acknowledged_ts,
    receipt: body,
  });
}

function wireFor(row) {
  const normalized = validateItem(row);
  const wire = {
    contract: 'CanonicalHttpWire/v3', status: Number(row.status),
    contentType: row.content_type, wireSha256: row.wire_sha256,
    wireLength: Number(row.wire_length),
  };
  Object.defineProperty(wire, 'bodyBytes', {
    enumerable: true, get: () => Buffer.from(normalized.wire),
  });
  return freeze(wire);
}

function claimAttestation(capability) {
  const attestation = CLAIMS.get(capability);
  if (!attestation) throw new Error('invalid opaque HTTP delivery v3 write authority');
  const row = deliveryDb.prepare(`SELECT * FROM ${mainTable(ITEM_TABLE)} WHERE id=?`)
    .get(attestation.deliveryRowId);
  validateItem(row);
  const ack = validateAck(ackRow(row), row);
  if (ack) return { attestation, row, ack };
  const latest = validateAttempt(latestAttempt(row), row);
  if (latest.action !== 'issued' || Number(latest.generation) !== attestation.generation
    || latest.token_sha256 !== attestation.tokenSha256
    || Date.parse(latest.lease_until_ts) <= Date.now()) {
    CLAIMS.delete(capability);
    throw new Error('stale HTTP delivery v3 write authority');
  }
  return { attestation, row, latest };
}

function transmissionClaimAttestation(capability) {
  const attestation = TRANSMISSION_CLAIMS.get(capability);
  if (!attestation) throw new Error('invalid opaque HTTP delivery v3 transmission authority');
  const row = deliveryDb.prepare(`SELECT * FROM ${mainTable(ITEM_TABLE)} WHERE id=?`)
    .get(attestation.deliveryRowId);
  validateItem(row);
  const latest = validateTransmission(latestTransmission(row), row);
  if (latest.action !== 'issued' || Number(latest.generation) !== attestation.generation
    || latest.token_sha256 !== attestation.tokenSha256
    || latest.primary_attempt_ref !== attestation.primaryAttemptRef
    || Number(latest.primary_generation) !== attestation.primaryGeneration
    || Date.parse(latest.lease_until_ts) <= Date.now()) {
    TRANSMISSION_CLAIMS.delete(capability);
    throw new Error('stale HTTP delivery v3 transmission authority');
  }
  return { attestation, row, latest };
}

try {
  deliveryDb.transaction(() => {
    const dependency = httpResponseAuthorityStoreStatus();
    if (!dependency?.available) {
      initializationFailure = `http_delivery_v3_response_authority_unavailable:${
        dependency?.reason || 'unknown'}`;
      return;
    }
    const present = TABLES.filter((name) => deliveryDb.prepare(`SELECT 1
      FROM main.sqlite_master WHERE type='table' AND name=?`).get(name));
    if (!present.length) {
      if (tempShadow() || !exactReservedNamespace(new Set())) {
        initializationFailure = 'http_delivery_v3_schema_namespace_or_temp_invalid';
        return;
      }
      deliveryDb.exec(SCHEMA);
      return;
    }
    if (present.length === LEGACY_TABLES.length && !present.includes(TRANSMISSION_TABLE)) {
      if (!migrateExactLegacySchema()) {
        initializationFailure = 'http_delivery_v3_legacy_schema_invalid';
      }
      return;
    }
    if (present.length !== TABLES.length) {
      initializationFailure = 'http_delivery_v3_schema_incomplete';
      return;
    }
    if (exactSchemaObjects(TABLES, INDEXES, TRIGGERS)) return;
    if (exactCurrentSchemaWithLegacyAttempt()) {
      migrateExactFinishedWriteGuard();
      return;
    }
    initializationFailure = 'http_delivery_v3_schema_invalid';
  }).immediate();
} catch (error) {
  initializationFailure = `http_delivery_v3_initialization_failed:${error.message}`;
}

export function httpDeliveryStoreStatus() {
  const reason = schemaFailure();
  return freeze({
    contract: 'HttpDeliveryStoreStatus/v3', available: !reason,
    ...(reason ? { reason } : {}),
  });
}

export function beginHttpDelivery(responseAuthority) {
  requireStore();
  const normalized = itemBody(consumeHttpResponseAuthority(responseAuthority));
  let row = deliveryDb.prepare(`SELECT * FROM ${mainTable(ITEM_TABLE)} WHERE request_ref=?`)
    .get(normalized.body.requestRef);
  if (row) {
    validateItem(row);
    if (row.delivery_sha256 !== normalized.deliverySha256) {
      return freeze({ contract: 'HttpDeliveryConflict/v3', kind: 'conflict' });
    }
  } else {
    deliveryDb.prepare(`INSERT INTO ${mainTable(ITEM_TABLE)} (
      contract,delivery_ref,delivery_sha256,authority_ref,authority_sha256,
      request_ref,response_ref,response_sha256,status,content_type,wire_bytes,
      wire_sha256,wire_length,ack_challenge,ack_challenge_sha256,
      client_public_key_json,client_key_fingerprint,turn_id,provenance_kind,
      provenance_ref,provenance_sha256,item_json,prepared_ts
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      normalized.body.contract, normalized.body.deliveryRef, normalized.deliverySha256,
      normalized.body.authorityRef, normalized.body.authoritySha256,
      normalized.body.requestRef, normalized.body.responseRef,
      normalized.body.responseSha256, normalized.body.status,
      normalized.body.contentType, normalized.wireBytes,
      normalized.body.wireSha256, normalized.body.wireLength,
      normalized.ackChallenge, normalized.body.ackChallengeSha256,
      normalized.clientPublicKeyJson, normalized.body.clientKeyFingerprint,
      normalized.body.turnId, normalized.body.provenance.kind,
      normalized.body.provenance.ref, normalized.body.provenance.sha256,
      normalized.itemJson, nowIso(),
    );
    row = deliveryDb.prepare(`SELECT * FROM ${mainTable(ITEM_TABLE)} WHERE request_ref=?`)
      .get(normalized.body.requestRef);
  }
  const ack = validateAck(ackRow(row), row);
  return freeze({
    contract: 'HttpDeliveryState/v3', kind: ack ? 'acknowledged' : 'prepared',
    state: ack ? 'acknowledged' : 'prepared', delivered: Boolean(ack),
    delivery: itemReceipt(row), ...(ack ? { ack, wire: wireFor(row) } : {}),
  });
}

export function replayHttpDelivery(delivery) {
  requireStore();
  const row = itemForReceipt(delivery);
  const ack = validateAck(ackRow(row), row);
  return freeze({
    contract: 'HttpDeliveryState/v3', kind: ack ? 'acknowledged' : 'prepared',
    state: ack ? 'acknowledged' : 'prepared', delivered: Boolean(ack),
    delivery: itemReceipt(row), ...(ack ? { ack, wire: wireFor(row) } : {}),
  });
}

export function claimHttpDeliveryWrite(delivery) {
  requireStore();
  const row = itemForReceipt(delivery);
  if (validateAck(ackRow(row), row)) return replayHttpDelivery(delivery);
  let result;
  try {
    result = deliveryDb.transaction(() => {
      const current = latestAttempt(row);
      if (current) validateAttempt(current, row);
      const now = Date.now();
      if (current?.action === 'write_finished') {
        return { kind: 'awaiting_client_ack', current };
      }
      if (current?.action === 'issued' && Date.parse(current.lease_until_ts) > now) {
        return { kind: 'in_progress', current };
      }
      const generation = current ? Number(current.generation) + 1 : 1;
      if (generation > MAX_GENERATIONS) return { kind: 'exhausted', current };
      const createdAt = new Date(now).toISOString();
      if (current?.action === 'issued') insertAttempt({
        row, generation: Number(current.generation), sequence: 1, action: 'abandoned',
        tokenSha256: current.token_sha256, leaseUntil: current.lease_until_ts,
      }, createdAt);
      const tokenSha256 = hash(randomBytes(32));
      const leaseUntil = new Date(now + LEASE_MS).toISOString();
      const event = insertAttempt({
        row, generation, sequence: 0, action: 'issued', tokenSha256, leaseUntil,
      }, createdAt);
      return { kind: 'write', generation, tokenSha256, leaseUntil, event };
    }).immediate();
  } catch (error) {
    if (!/identity collision|acquisition is invalid|UNIQUE constraint|acknowledged/u
      .test(String(error?.message || error))) throw error;
    if (validateAck(ackRow(row), row)) return replayHttpDelivery(delivery);
    const current = validateAttempt(latestAttempt(row), row);
    return freeze({
      contract: 'HttpDeliveryWriteState/v3',
      kind: current.action === 'issued' ? 'in_progress' : 'awaiting_client_ack',
      state: current.action, delivered: false, delivery: itemReceipt(row),
      leaseUntil: current.lease_until_ts,
    });
  }
  if (result.kind !== 'write') {
    return freeze({
      contract: 'HttpDeliveryWriteState/v3', kind: result.kind,
      state: result.current?.action || result.kind, delivered: false,
      delivery: itemReceipt(row),
      ...(result.current ? { leaseUntil: result.current.lease_until_ts } : {}),
    });
  }
  const authority = freeze({ nonce: randomBytes(32).toString('hex') });
  CLAIMS.set(authority, freeze({
    deliveryRowId: Number(row.id), generation: result.generation,
    tokenSha256: result.tokenSha256,
  }));
  return freeze({
    contract: 'HttpDeliveryWriteClaim/v3', kind: 'write', state: 'issued',
    delivered: false, delivery: itemReceipt(row), generation: result.generation,
    attemptRef: result.event.attempt_event_ref,
    writeAttemptRef: result.event.attempt_event_ref, writeGeneration: result.generation,
    leaseUntil: result.leaseUntil,
    wire: wireFor(row), writeAuthority: authority,
  });
}

export function claimHttpDeliveryTransmission(delivery) {
  requireStore();
  const row = itemForReceipt(delivery);
  const primary = finishedPrimaryWrite(row);
  if (!primary) {
    return freeze({
      contract: 'HttpDeliveryTransmissionState/v3', kind: 'primary_not_finished',
      state: latestAttempt(row)?.action || 'prepared', delivered: false,
      delivery: itemReceipt(row),
    });
  }
  const acknowledged = Boolean(validateAck(ackRow(row), row));
  const mode = acknowledged ? 'acknowledged_retrieval' : 'pre_ack_retrieval';
  let result;
  try {
    result = deliveryDb.transaction(() => {
      const current = latestTransmission(row);
      if (current) validateTransmission(current, row);
      const now = Date.now();
      if (current?.action === 'issued' && Date.parse(current.lease_until_ts) > now) {
        return { kind: 'in_progress', current };
      }
      const generation = current ? Number(current.generation) + 1 : 1;
      if (!Number.isSafeInteger(generation) || generation > MAX_TRANSMISSIONS) {
        return { kind: 'exhausted', current };
      }
      const createdAt = new Date(now).toISOString();
      if (current?.action === 'issued') insertTransmission({
        row, generation: Number(current.generation), sequence: 1, action: 'abandoned',
        mode: current.mode, tokenSha256: current.token_sha256,
        primaryGeneration: Number(current.primary_generation),
        primaryAttemptRef: current.primary_attempt_ref,
        leaseUntil: current.lease_until_ts,
      }, createdAt);
      const tokenSha256 = hash(randomBytes(32));
      const leaseUntil = new Date(now + LEASE_MS).toISOString();
      const event = insertTransmission({
        row, generation, sequence: 0, action: 'issued', mode, tokenSha256,
        primaryGeneration: primary.generation,
        primaryAttemptRef: primary.issued.attempt_event_ref, leaseUntil,
      }, createdAt);
      return { kind: 'write', generation, tokenSha256, leaseUntil, event };
    }).immediate();
  } catch (error) {
    if (!/identity collision|acquisition is invalid|UNIQUE constraint/u
      .test(String(error?.message || error))) throw error;
    const current = validateTransmission(latestTransmission(row), row);
    return freeze({
      contract: 'HttpDeliveryTransmissionState/v3', kind: 'in_progress',
      state: current.action, delivered: acknowledged, delivery: itemReceipt(row),
      leaseUntil: current.lease_until_ts,
    });
  }
  if (result.kind !== 'write') {
    return freeze({
      contract: 'HttpDeliveryTransmissionState/v3', kind: result.kind,
      state: result.current?.action || result.kind, delivered: acknowledged,
      delivery: itemReceipt(row),
      ...(result.current ? { leaseUntil: result.current.lease_until_ts } : {}),
    });
  }
  const authority = freeze({ nonce: randomBytes(32).toString('hex') });
  TRANSMISSION_CLAIMS.set(authority, freeze({
    deliveryRowId: Number(row.id), generation: result.generation,
    tokenSha256: result.tokenSha256, primaryGeneration: primary.generation,
    primaryAttemptRef: primary.issued.attempt_event_ref,
  }));
  return freeze({
    contract: 'HttpDeliveryTransmissionClaim/v3', kind: 'write', state: 'issued',
    delivered: acknowledged, mode, delivery: itemReceipt(row),
    generation: result.generation,
    transmissionRef: result.event.transmission_event_ref,
    leaseUntil: result.leaseUntil, wire: wireFor(row), transmissionAuthority: authority,
    writeGeneration: primary.generation,
    writeAttemptRef: primary.issued.attempt_event_ref,
  });
}

export function finishHttpDeliveryWrite(writeAuthority) {
  requireStore();
  const { attestation, row, latest, ack } = claimAttestation(writeAuthority);
  CLAIMS.delete(writeAuthority);
  if (ack) return replayHttpDelivery(itemReceipt(row));
  const event = insertAttempt({
    row, generation: attestation.generation, sequence: 1, action: 'write_finished',
    tokenSha256: attestation.tokenSha256, leaseUntil: latest.lease_until_ts,
  }, nowIso());
  return freeze({
    contract: 'HttpDeliveryWriteFinished/v3', kind: 'write_finished',
    state: 'write_finished', delivered: false, delivery: itemReceipt(row),
    generation: attestation.generation, writeAttemptRef: latest.attempt_event_ref,
    writeFinishedEventRef: event.attempt_event_ref,
  });
}

export function finishHttpDeliveryTransmission(transmissionAuthority) {
  requireStore();
  const { attestation, row, latest } = transmissionClaimAttestation(transmissionAuthority);
  TRANSMISSION_CLAIMS.delete(transmissionAuthority);
  const event = insertTransmission({
    row, generation: attestation.generation, sequence: 1, action: 'write_finished',
    mode: latest.mode, tokenSha256: attestation.tokenSha256,
    primaryGeneration: attestation.primaryGeneration,
    primaryAttemptRef: attestation.primaryAttemptRef, leaseUntil: latest.lease_until_ts,
  }, nowIso());
  return freeze({
    contract: 'HttpDeliveryTransmissionFinished/v3', kind: 'write_finished',
    state: 'write_finished', delivery: itemReceipt(row),
    generation: attestation.generation,
    transmissionFinishedRef: event.transmission_event_ref,
    writeGeneration: attestation.primaryGeneration,
    writeAttemptRef: attestation.primaryAttemptRef,
  });
}

export function abandonHttpDeliveryWrite(writeAuthority) {
  requireStore();
  const { attestation, row, latest, ack } = claimAttestation(writeAuthority);
  CLAIMS.delete(writeAuthority);
  if (ack) return replayHttpDelivery(itemReceipt(row));
  insertAttempt({
    row, generation: attestation.generation, sequence: 1, action: 'abandoned',
    tokenSha256: attestation.tokenSha256, leaseUntil: latest.lease_until_ts,
  }, nowIso());
  return freeze({
    contract: 'HttpDeliveryWriteAbandoned/v3', kind: 'abandoned',
    state: 'abandoned', delivered: false, delivery: itemReceipt(row),
    generation: attestation.generation,
  });
}

export function abandonHttpDeliveryTransmission(transmissionAuthority) {
  requireStore();
  const { attestation, row, latest } = transmissionClaimAttestation(transmissionAuthority);
  TRANSMISSION_CLAIMS.delete(transmissionAuthority);
  insertTransmission({
    row, generation: attestation.generation, sequence: 1, action: 'abandoned',
    mode: latest.mode, tokenSha256: attestation.tokenSha256,
    primaryGeneration: attestation.primaryGeneration,
    primaryAttemptRef: attestation.primaryAttemptRef, leaseUntil: latest.lease_until_ts,
  }, nowIso());
  return freeze({
    contract: 'HttpDeliveryTransmissionAbandoned/v3', kind: 'abandoned',
    state: 'abandoned', delivery: itemReceipt(row), generation: attestation.generation,
  });
}

export function httpDeliveryWriteAuthorityCurrent(writeAuthority) {
  try {
    if (schemaFailure()) return false;
    return !claimAttestation(writeAuthority).ack;
  } catch { return false; }
}

export function httpDeliveryTransmissionAuthorityCurrent(transmissionAuthority) {
  try {
    if (schemaFailure()) return false;
    return Boolean(transmissionClaimAttestation(transmissionAuthority));
  } catch { return false; }
}

function acknowledgedState(row) {
  const ack = validateAck(ackRow(row), row);
  if (!ack) return null;
  return freeze({
    contract: 'HttpDeliveryState/v3', kind: 'acknowledged', state: 'acknowledged',
    delivered: true, delivery: itemReceipt(row), ack, wire: wireFor(row),
  });
}

export function acknowledgeHttpDelivery(input) {
  requireStore();
  if (!input || typeof input !== 'object' || !DELIVERY_REF.test(input.deliveryRef)) {
    throw new TypeError('invalid exact HTTP client ACK v3');
  }
  const row = deliveryDb.prepare(`SELECT * FROM ${mainTable(ITEM_TABLE)}
    WHERE delivery_ref=?`).get(input.deliveryRef);
  const normalized = validateItem(row);
  const verified = verifyHttpClientAck(input, normalized.key);
  if (verified.ack.requestRef !== row.request_ref
    || verified.ack.responseRef !== row.response_ref
    || verified.ack.responseSha256 !== row.response_sha256
    || verified.ack.deliveryRef !== row.delivery_ref
    || verified.ack.challenge !== row.ack_challenge
    || verified.ack.wireSha256 !== row.wire_sha256
    || verified.keyFingerprint !== row.client_key_fingerprint) {
    throw new Error('HTTP client ACK v3 does not match durable write');
  }
  const primary = finishedPrimaryWrite(row);
  if (!primary || verified.ack.writeGeneration !== primary.generation
    || verified.ack.writeAttemptRef !== primary.issued.attempt_event_ref) {
    throw Object.assign(
      new Error('HTTP client ACK v3 has no exact current durable finished write'),
      {
        code: 'EVA_HTTP_DELIVERY_WRITE_NOT_FINISHED',
        requestRef: row.request_ref,
        deliveryRef: row.delivery_ref,
        writeGeneration: verified.ack.writeGeneration,
        writeAttemptRef: verified.ack.writeAttemptRef,
      },
    );
  }
  let outcome;
  try {
    outcome = deliveryDb.transaction(() => {
      const existing = acknowledgedState(row);
      if (existing) return existing;
      const receiptBody = {
        contract: 'HttpClientAckReceipt/v3', deliveryRef: row.delivery_ref,
        requestRef: row.request_ref, responseRef: row.response_ref,
        responseSha256: row.response_sha256,
        ackChallengeSha256: row.ack_challenge_sha256,
        wireSha256: row.wire_sha256, wireLength: Number(row.wire_length),
        clientKeyFingerprint: row.client_key_fingerprint,
        ackPreimageSha256: verified.preimageSha256,
        writeAttemptRef: primary.issued.attempt_event_ref,
        writeGeneration: primary.generation, turnId: row.turn_id,
        provenance: {
          contract: 'HttpResultProvenance/v1', kind: row.provenance_kind,
          ref: row.provenance_ref, sha256: row.provenance_sha256,
        },
      };
      const receiptJson = canonicalJson(receiptBody);
      const ackReceiptSha256 = hash(receiptJson);
      deliveryDb.prepare(`INSERT INTO ${mainTable(ACK_TABLE)} (
        contract,ack_receipt_ref,ack_receipt_sha256,delivery_row_id,delivery_ref,
        request_ref,response_ref,response_sha256,ack_challenge,ack_challenge_sha256,
        wire_sha256,wire_length,client_key_fingerprint,ack_preimage_json,
        ack_preimage_sha256,signature,signature_sha256,turn_id,provenance_kind,
        provenance_ref,provenance_sha256,receipt_json,acknowledged_ts
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
        receiptBody.contract, `http-delivery-ack-v3:${ackReceiptSha256}`,
        ackReceiptSha256, Number(row.id), row.delivery_ref, row.request_ref,
        row.response_ref, row.response_sha256, row.ack_challenge,
        row.ack_challenge_sha256, row.wire_sha256, Number(row.wire_length),
        row.client_key_fingerprint, verified.preimage, verified.preimageSha256,
        verified.signature, verified.signatureSha256, row.turn_id,
        row.provenance_kind, row.provenance_ref, row.provenance_sha256,
        receiptJson, nowIso(),
      );
      return acknowledgedState(row);
    }).immediate();
  } catch (error) {
    if (!/identity collision|UNIQUE constraint|database is locked/u
      .test(String(error?.message || error))) throw error;
    outcome = acknowledgedState(row);
    if (!outcome) throw error;
  }
  return outcome;
}
