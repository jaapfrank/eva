import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import { config, requireConfiguredDataRoot } from '../config.js';
import {
  claimTurnToolExecution, db, validateExecutionRecord,
} from '../db.js';
import { executionLedgerStatus } from '../executionledger.js';
import {
  turnUnderstandingAuthorityRecord,
  turnUnderstandingAuthorityStoreStatus,
} from '../turnauthority.js';
import { canonicalJson } from '../util.js';
import {
  artifactVersion, artifactVersionReceipt, artifactVersionStoreStatus,
} from './artifacts.js';
import { createWorkCompletionPlanSeal } from './workcompletion.js';

const lifecycleDb = /** @type {any} */ (db);
const SHA256 = /^[a-f0-9]{64}$/u;
const HANDOFF_KEY = SHA256;
const REQUIREMENT_ID = /^r(?:[1-9]|1[0-2])$/u;
const OPERATION_KEY = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,239}$/u;
const PROJECTION_RULE = /^[A-Za-z0-9][A-Za-z0-9./:_-]{0,79}$/u;
const MAX_OBLIGATION_JSON_BYTES = 64 * 1024;
const MAX_MATERIAL_BYTES = 2 * 1024 * 1024;
const WORK_STATE_JSON_LIMIT = 12_000;
const INTERPRETATION_RETRY_TTL_MS = 6 * 60 * 60_000;
const INTERPRETATION_RETRY_MAX_ATTEMPTS = 4;
const RETRY_SOURCE_PROJECTION_RULE = 'trimmed-redacted-user-text/v1';

const CONTRACT_TABLE = 'eva_material_execution_contracts';
const INTENT_TABLE = 'eva_material_effect_intents';
const RECONCILIATION_TABLE = 'eva_material_effect_reconciliations';
const OBSERVATION_TABLE = 'eva_material_execution_observations';
const CLAIM_TABLE = 'eva_material_effect_claims';
const HANDOFF_TABLE = 'eva_interpretation_retry_handoffs';
const OWNED_PREFIX = 'eva_material_';
const HANDOFF_ORIGIN_INDEX = 'idx_eva_interpretation_retry_origin';
const HANDOFF_TABLE_SQL = `CREATE TABLE eva_interpretation_retry_handoffs (
  handoff_key TEXT NOT NULL PRIMARY KEY,
  contract TEXT NOT NULL,
  origin_channel TEXT NOT NULL,
  origin_conversation_id TEXT NOT NULL,
  origin_message_id TEXT NOT NULL,
  raw_original_text_sha256 TEXT NOT NULL,
  original_text_sha256 TEXT NOT NULL,
  requester_sha256 TEXT NOT NULL,
  supersession_scope_sha256 TEXT NOT NULL,
  supersession_token_sha256 TEXT NOT NULL,
  work_item_id INTEGER NOT NULL UNIQUE,
  episode_id INTEGER NOT NULL UNIQUE,
  binding_sha256 TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  UNIQUE(contract, origin_channel, origin_conversation_id, origin_message_id)
)`;
const HANDOFF_ORIGIN_INDEX_SQL = `CREATE INDEX
  idx_eva_interpretation_retry_origin ON eva_interpretation_retry_handoffs(
    origin_channel, origin_conversation_id, origin_message_id
  )`;
const HANDOFF_TRIGGER_SQL = Object.freeze(new Map([
  ['trg_interpretation_retry_handoff_insert_guard', `CREATE TRIGGER
trg_interpretation_retry_handoff_insert_guard
BEFORE INSERT ON eva_interpretation_retry_handoffs
BEGIN
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM eva_interpretation_retry_handoffs AS prior
    WHERE prior.handoff_key = NEW.handoff_key
      OR prior.work_item_id = NEW.work_item_id
      OR prior.episode_id = NEW.episode_id
      OR (prior.contract = NEW.contract
        AND prior.origin_channel = NEW.origin_channel
        AND prior.origin_conversation_id = NEW.origin_conversation_id
        AND prior.origin_message_id = NEW.origin_message_id)
  ) THEN RAISE(ABORT, 'ChatInterpretationRetry/v1-registryidentiteit is onveranderlijk') END;
  SELECT CASE WHEN NEW.contract IS NULL
    OR NEW.contract <> 'ChatInterpretationRetry/v1'
    OR NEW.handoff_key IS NULL
    OR length(NEW.handoff_key) <> 64
    OR NEW.handoff_key GLOB '*[^0-9a-f]*'
    OR NEW.origin_channel IS NULL
    OR NEW.origin_channel NOT IN ('telegram', 'voice', 'web')
    OR NEW.origin_conversation_id IS NULL
    OR length(NEW.origin_conversation_id) < 1
    OR length(NEW.origin_conversation_id) > 240
    OR NEW.origin_message_id IS NULL
    OR length(NEW.origin_message_id) < 1
    OR length(NEW.origin_message_id) > 160
    OR NEW.raw_original_text_sha256 IS NULL
    OR length(NEW.raw_original_text_sha256) <> 64
    OR NEW.raw_original_text_sha256 GLOB '*[^0-9a-f]*'
    OR NEW.original_text_sha256 IS NULL
    OR length(NEW.original_text_sha256) <> 64
    OR NEW.original_text_sha256 GLOB '*[^0-9a-f]*'
    OR NEW.requester_sha256 IS NULL
    OR length(NEW.requester_sha256) <> 64
    OR NEW.requester_sha256 GLOB '*[^0-9a-f]*'
    OR NEW.supersession_scope_sha256 IS NULL
    OR length(NEW.supersession_scope_sha256) <> 64
    OR NEW.supersession_scope_sha256 GLOB '*[^0-9a-f]*'
    OR NEW.supersession_token_sha256 IS NULL
    OR length(NEW.supersession_token_sha256) <> 64
    OR NEW.supersession_token_sha256 GLOB '*[^0-9a-f]*'
    OR typeof(NEW.work_item_id) <> 'integer'
    OR NEW.work_item_id < 1
    OR typeof(NEW.episode_id) <> 'integer'
    OR NEW.episode_id < 1
    OR NEW.binding_sha256 IS NULL
    OR length(NEW.binding_sha256) <> 64
    OR NEW.binding_sha256 GLOB '*[^0-9a-f]*'
    OR NEW.created_ts IS NULL
    OR length(NEW.created_ts) < 20
    OR length(NEW.created_ts) > 40
  THEN RAISE(ABORT, 'ongeldige ChatInterpretationRetry/v1-registryrij') END;
END`],
  ['trg_interpretation_retry_handoff_immutable_update', `CREATE TRIGGER
trg_interpretation_retry_handoff_immutable_update
BEFORE UPDATE ON eva_interpretation_retry_handoffs
BEGIN
  SELECT RAISE(ABORT, 'ChatInterpretationRetry/v1-registry is onveranderlijk');
END`],
  ['trg_interpretation_retry_handoff_immutable_delete', `CREATE TRIGGER
trg_interpretation_retry_handoff_immutable_delete
BEFORE DELETE ON eva_interpretation_retry_handoffs
BEGIN
  SELECT RAISE(ABORT, 'ChatInterpretationRetry/v1-registry is onveranderlijk');
END`],
]));
const HANDOFF_COLUMNS = Object.freeze([
  ['handoff_key', 'TEXT', 1, 1], ['contract', 'TEXT', 1, 0],
  ['origin_channel', 'TEXT', 1, 0], ['origin_conversation_id', 'TEXT', 1, 0],
  ['origin_message_id', 'TEXT', 1, 0], ['raw_original_text_sha256', 'TEXT', 1, 0],
  ['original_text_sha256', 'TEXT', 1, 0], ['requester_sha256', 'TEXT', 1, 0],
  ['supersession_scope_sha256', 'TEXT', 1, 0],
  ['supersession_token_sha256', 'TEXT', 1, 0],
  ['work_item_id', 'INTEGER', 1, 0], ['episode_id', 'INTEGER', 1, 0],
  ['binding_sha256', 'TEXT', 1, 0], ['created_ts', 'TEXT', 1, 0],
]);

const REFERENCE_COLUMNS = Object.freeze({
  timeline: [
    ['id', 'INTEGER', 0, 1], ['ts', 'TEXT', 1, 0], ['kind', 'TEXT', 1, 0],
    ['content', 'TEXT', 1, 0], ['meta', 'TEXT', 0, 0], ['outcome_key', 'TEXT', 0, 0],
  ],
  messages: [
    ['id', 'INTEGER', 0, 1], ['ts', 'TEXT', 1, 0], ['channel', 'TEXT', 1, 0],
    ['conversation_id', 'TEXT', 0, 0], ['sender', 'TEXT', 1, 0],
    ['role', 'TEXT', 1, 0], ['content', 'TEXT', 1, 0],
  ],
  telegram_messages: [
    ['id', 'INTEGER', 0, 1], ['chat_id', 'TEXT', 1, 0],
    ['message_id', 'TEXT', 1, 0], ['ts', 'TEXT', 1, 0],
    ['archived_ts', 'TEXT', 1, 0], ['sender', 'TEXT', 1, 0],
    ['role', 'TEXT', 1, 0], ['kind', 'TEXT', 1, 0], ['content', 'TEXT', 1, 0],
    ['reply_to_message_id', 'TEXT', 0, 0], ['media_ref', 'TEXT', 0, 0],
    ['file_id', 'TEXT', 0, 0], ['deleted', 'INTEGER', 1, 0],
    ['processing_status', 'TEXT', 1, 0], ['processing_attempts', 'INTEGER', 1, 0],
    ['processed_ts', 'TEXT', 0, 0], ['processing_error', 'TEXT', 0, 0],
    ['meta', 'TEXT', 0, 0],
  ],
  eva_work_items: [
    ['id', 'INTEGER', 0, 1], ['created_ts', 'TEXT', 1, 0],
    ['updated_ts', 'TEXT', 1, 0], ['source', 'TEXT', 1, 0],
    ['owner', 'TEXT', 0, 0], ['title', 'TEXT', 1, 0], ['goal', 'TEXT', 1, 0],
    ['next_step', 'TEXT', 0, 0], ['status', 'TEXT', 1, 0],
    ['evidence', 'TEXT', 0, 0], ['last_action', 'TEXT', 0, 0],
    ['result', 'TEXT', 0, 0], ['visible_to_family', 'INTEGER', 1, 0],
    ['definition_of_done', 'TEXT', 0, 0], ['current_step', 'TEXT', 0, 0],
    ['state_json', 'TEXT', 0, 0], ['wait_json', 'TEXT', 0, 0],
    ['priority', 'INTEGER', 1, 0], ['next_wake_ts', 'TEXT', 0, 0],
    ['revision', 'INTEGER', 1, 0], ['lease_owner', 'TEXT', 0, 0],
    ['lease_until', 'TEXT', 0, 0], ['attempt_count', 'INTEGER', 1, 0],
    ['last_error', 'TEXT', 0, 0], ['interrupted_ts', 'TEXT', 0, 0],
  ],
  eva_goal_episodes: [
    ['id', 'INTEGER', 0, 1], ['episode_key', 'TEXT', 1, 0],
    ['goal_id', 'INTEGER', 1, 0], ['project_id', 'INTEGER', 1, 0],
    ['work_item_id', 'INTEGER', 0, 0], ['owner_actor', 'TEXT', 1, 0],
    ['origin_kind', 'TEXT', 1, 0], ['origin_channel', 'TEXT', 0, 0],
    ['origin_conversation_id', 'TEXT', 0, 0], ['origin_message_id', 'TEXT', 0, 0],
    ['requester', 'TEXT', 0, 0], ['requested_outcome', 'TEXT', 1, 0],
    ['delivery_policy', 'TEXT', 1, 0], ['delivery_target', 'TEXT', 0, 0],
    ['state', 'TEXT', 1, 0], ['next_action', 'TEXT', 0, 0],
    ['checkpoint', 'TEXT', 0, 0], ['proof_summary', 'TEXT', 0, 0],
    ['wake_ts', 'TEXT', 0, 0], ['lease_owner', 'TEXT', 0, 0],
    ['lease_until', 'TEXT', 0, 0], ['created_ts', 'TEXT', 1, 0],
    ['updated_ts', 'TEXT', 1, 0], ['closed_ts', 'TEXT', 0, 0],
    ['revision', 'INTEGER', 1, 0],
  ],
  eva_episode_origins: [
    ['id', 'INTEGER', 0, 1], ['episode_id', 'INTEGER', 1, 0],
    ['origin_key', 'TEXT', 1, 0], ['origin_kind', 'TEXT', 1, 0],
    ['origin_channel', 'TEXT', 0, 0], ['origin_conversation_id', 'TEXT', 0, 0],
    ['origin_message_id', 'TEXT', 0, 0], ['requester', 'TEXT', 0, 0],
    ['attached_ts', 'TEXT', 1, 0],
  ],
  eva_artifact_versions: [
    ['id', 'INTEGER', 0, 1], ['version_ref', 'TEXT', 1, 0],
    ['seal_id', 'INTEGER', 1, 0], ['ordinal', 'INTEGER', 1, 0],
    ['role', 'TEXT', 1, 0], ['canonical_path', 'TEXT', 1, 0],
    ['artifact_sha256', 'TEXT', 1, 0], ['byte_length', 'INTEGER', 1, 0],
    ['understanding_sha256', 'TEXT', 1, 0], ['requirement_id', 'TEXT', 1, 0],
    ['producer_execution_key', 'TEXT', 1, 0],
    ['producer_completion_sha256', 'TEXT', 1, 0], ['proof_json', 'TEXT', 1, 0],
    ['proof_sha256', 'TEXT', 1, 0], ['previous_version_ref', 'TEXT', 0, 0],
    ['provenance_kind', 'TEXT', 1, 0], ['provenance_sha256', 'TEXT', 1, 0],
    ['created_ts', 'TEXT', 1, 0],
  ],
});

const REFERENCE_DEFAULTS = Object.freeze({
  telegram_messages: Object.freeze({
    sender: "''", kind: "'text'", content: "''", deleted: '0',
    processing_status: "'received'", processing_attempts: '0',
  }),
  eva_work_items: Object.freeze({
    status: "'open'", visible_to_family: '1', priority: '50', revision: '0',
    attempt_count: '0',
  }),
  eva_goal_episodes: Object.freeze({
    owner_actor: "'eva'", delivery_policy: "'required'",
    state: "'committed'", revision: '0',
  }),
});

// These hashes deliberately cover the complete SQLite DDL, including
// AUTOINCREMENT, UNIQUE and partial/expression-index predicates. A matching
// column list alone is not a dependency contract.
const REFERENCE_TABLE_SQL_SHA256 = Object.freeze({
  timeline: 'becb44c64df74ee83e0e2e7a32f8fd6c985acd6fccde9c073dc2e85a3967aec7',
  messages: '8f5f42a8120f09ed1165203311fc4a45d6755412b6f1f51c0f68a660320180e9',
  telegram_messages: '0b712141c5e991210ee6a22c32bbee388325a121de6e4c65fcd3ae86fef94ffa',
  eva_work_items: '3649ada4b26ee1ece7e495cd55684896b2f40f61937076ee719c3f21da633a59',
  eva_goal_episodes: 'bdfbd08f09a6948304c24be7a000936d84de90b2846f4c68b19ff21ebf5ec83a',
  eva_episode_origins: '32f7aad5ecc6c9b4151e7f81f4172b1251ae8b3eb2be41c9142818fd07f5b396',
});

const REFERENCE_INDEXES = Object.freeze({
  timeline: [
    ['idx_timeline_contribution_outcome_key', 1, 'c', 1, ['outcome_key'],
      '948045ce5fff9e45517b8fed70b1731c0d2c68fe065976b51c4f5dea659ed463'],
    ['idx_timeline_kind', 0, 'c', 0, ['kind'],
      'a2ee8d9f93cb50f7a0e3807d798807e00c84a4496430db04d667a06019e4adab'],
    ['idx_timeline_ts', 0, 'c', 0, ['ts'],
      '5c8788d7d4a0851839052c7a70455580eb9edd71de6c0d391c2b4aa418d50e6a'],
    ['idx_timeline_understanding_conversation', 0, 'c', 1, ['kind', null, 'id'],
      '46435fbc6a67ace50eae4fee1d1d4255d177a9b8408320781f50c701453c8296'],
  ],
  messages: [
    ['idx_messages_conversation', 0, 'c', 0, ['conversation_id', 'id'],
      '972ab3218184a3e8b00138a575875abf7ef17a25a896b2011a09b2dd21ddabdd'],
    ['idx_messages_ts', 0, 'c', 0, ['ts'],
      '3aacd58c65bb8c931b8867450118da33a6e3e66327be066e30c906187500a449'],
  ],
  telegram_messages: [
    ['idx_telegram_messages_chat_id', 0, 'c', 0, ['chat_id', 'id'],
      '1494191da703cf1dae08a5218d82088cc8c715d79c78876a6f522fc8b713a273'],
    ['idx_telegram_messages_media', 0, 'c', 0, ['chat_id', 'kind', 'id'],
      'd6e6fffff3bd3ef3a998091358875fff501589ec34d296b7f5772446a8324c08'],
    ['sqlite_autoindex_telegram_messages_1', 1, 'u', 0,
      ['chat_id', 'message_id'], null],
  ],
  eva_work_items: [
    ['idx_eva_work_items_status', 0, 'c', 0, ['status', 'updated_ts'],
      'e83e4e88a6d18325c8c6762d497df250a50c4afd68436c21378efce1921f6945'],
    ['idx_eva_work_wake', 0, 'c', 0, ['status', 'next_wake_ts', 'priority'],
      'e433bda7b675a7909539d5e376f8a8befd49defe8a601d19f6a146404dd87466'],
  ],
  eva_goal_episodes: [
    ['idx_eva_goal_episode_one_owner', 1, 'c', 1, ['work_item_id'],
      'a9b9c7e52d3a96443f272a81bbaa9f64bea9e2b8221a6906957de3588c4c5faf'],
    ['idx_eva_goal_episodes_delivery', 0, 'c', 0,
      ['delivery_policy', 'state', 'updated_ts'],
      'e70e40bea04756f7da2b000a716d827693023c9cdc566dad180691a3dd00dd2b'],
    ['idx_eva_goal_episodes_work', 0, 'c', 0, ['work_item_id', 'state'],
      'ad0012ffbcb8f752caa873510a215dffb2cb971c686bdf3ed4bd01b5bafb8809'],
    ['sqlite_autoindex_eva_goal_episodes_1', 1, 'u', 0, ['episode_key'], null],
  ],
  eva_episode_origins: [
    ['idx_eva_episode_origins_conversation', 0, 'c', 0,
      ['origin_conversation_id', 'episode_id'],
      '1507c7d7338b35e6b075b48c735974ee2991fc8d82a00c76794ae080a14f41c4'],
    ['idx_eva_episode_origins_episode', 0, 'c', 0,
      ['episode_id', 'attached_ts'],
      '7a5d1381fd3843df0f8151a0ab5fb5ebcf842fdde2dac9402e5c950433f4d96c'],
    ['sqlite_autoindex_eva_episode_origins_1', 1, 'u', 0, ['origin_key'], null],
  ],
});

const REFERENCE_TRIGGERS = Object.freeze({
});

const REQUIRED_COLUMNS = Object.freeze({
  [CONTRACT_TABLE]: [
    'id', 'contract', 'contract_ref', 'contract_sha256', 'handoff_key',
    'created_ts', 'raw_body_sha256', 'projection_rule', 'projection_sha256',
    'work_item_id', 'episode_id', 'source_turn_id', 'authority_timeline_id',
    'understanding_sha256', 'semantic_authority_sha256', 'plan_sha256',
    'plan_seal_json', 'excluded_actions_json', 'excluded_actions_sha256',
    'obligation_json', 'obligation_sha256', 'execution_turn_id', 'authority_sha256',
    'handoff_binding_sha256', 'handoff_registry_sha256', 'contract_json',
    'authority_core_json', 'authority_core_sha256',
  ],
  [INTENT_TABLE]: [
    'id', 'contract', 'intent_ref', 'intent_sha256', 'contract_id',
    'contract_ref', 'contract_sha256', 'requirement_id', 'canonical_path',
    'physical_target_id', 'target_authority_sha256',
    'preimage_kind', 'preimage_sha256', 'preimage_bytes',
    'intended_sha256', 'intended_bytes', 'pre_read_execution_key',
    'pre_read_completion_sha256', 'pre_read_sequence',
    'pre_read_path_write_head_sequence', 'artifact_version_head_id',
    'artifact_version_head_ref', 'artifact_version_head_provenance_sha256',
    'write_execution_key', 'intent_json', 'created_ts',
  ],
  [RECONCILIATION_TABLE]: [
    'id', 'contract', 'reconciliation_ref', 'reconciliation_sha256',
    'intent_id', 'intent_ref', 'intent_sha256', 'contract_ref',
    'claim_generation', 'claim_ref', 'claim_sha256', 'claim_fence_token',
    'canonical_path', 'outcome', 'reason', 'next_action',
    'recovery_generation', 'recovery_write_execution_key', 'current_kind',
    'current_sha256', 'current_bytes', 'recovery_read_execution_key',
    'recovery_read_completion_sha256', 'recovery_read_sequence',
    'started_write_sequence', 'started_write_intent_sha256',
    'material_intent_head_id', 'path_write_head_sequence',
    'artifact_version_head_id', 'cas_context_json', 'cas_context_sha256',
    'observation_json', 'observation_sha256', 'receipt_json', 'created_ts',
  ],
  [OBSERVATION_TABLE]: [
    'observation_sequence', 'execution_key', 'tool', 'physical_target_id',
    'canonical_path', 'intent_record_sha256', 'execution_snapshot_sha256',
    'observed_ts',
  ],
  [CLAIM_TABLE]: [
    'id', 'contract', 'claim_ref', 'claim_sha256', 'intent_id', 'intent_ref',
    'intent_sha256', 'generation', 'physical_target_id', 'target_authority_sha256',
    'execution_key', 'fence_token', 'observation_head_sequence',
    'artifact_version_head_ref', 'artifact_version_head_provenance_sha256',
    'predecessor_reconciliation_ref', 'claim_json', 'created_ts',
  ],
});

const REQUIRED_TRIGGERS = Object.freeze([
  'eva_material_execution_contracts_no_update',
  'eva_material_execution_contracts_no_delete',
  'eva_material_execution_contracts_no_collision',
  'eva_material_execution_contracts_valid_reference',
  'eva_material_effect_intents_no_update',
  'eva_material_effect_intents_no_delete',
  'eva_material_effect_intents_no_collision',
  'eva_material_effect_intents_valid_reference',
  'eva_material_effect_reconciliations_no_update',
  'eva_material_effect_reconciliations_no_delete',
  'eva_material_effect_reconciliations_no_collision',
  'eva_material_effect_reconciliations_valid_reference',
  'eva_material_execution_observations_no_update',
  'eva_material_execution_observations_no_delete',
  'eva_material_execution_observations_no_collision',
  'eva_material_effect_claims_no_update',
  'eva_material_effect_claims_no_delete',
  'eva_material_effect_claims_no_collision',
  'eva_material_effect_claims_valid_reference',
]);

const REQUIRED_INDEXES = Object.freeze([
  'idx_material_execution_contract_work_episode',
  'idx_material_effect_intents_contract',
  'idx_material_effect_intents_path',
  'idx_material_effect_reconciliations_outcome',
  'idx_material_execution_observations_target',
  'idx_material_effect_claims_target',
]);

const LIFECYCLE_TRIGGER_NAMES = Object.freeze({
  [CONTRACT_TABLE]: REQUIRED_TRIGGERS.slice(0, 4),
  [INTENT_TABLE]: REQUIRED_TRIGGERS.slice(4, 8),
  [RECONCILIATION_TABLE]: REQUIRED_TRIGGERS.slice(8, 12),
  [OBSERVATION_TABLE]: REQUIRED_TRIGGERS.slice(12, 15),
  [CLAIM_TABLE]: REQUIRED_TRIGGERS.slice(15, 19),
});

const LIFECYCLE_INDEX_NAMES = Object.freeze({
  [CONTRACT_TABLE]: [
    'idx_material_execution_contract_work_episode',
    ...Array.from({ length: 7 }, (_, index) => (
      `sqlite_autoindex_${CONTRACT_TABLE}_${index + 1}`
    )),
  ],
  [INTENT_TABLE]: [
    'idx_material_effect_intents_contract', 'idx_material_effect_intents_path',
    ...Array.from({ length: 6 }, (_, index) => (
      `sqlite_autoindex_${INTENT_TABLE}_${index + 1}`
    )),
  ],
  [RECONCILIATION_TABLE]: [
    'idx_material_effect_reconciliations_outcome',
    ...Array.from({ length: 10 }, (_, index) => (
      `sqlite_autoindex_${RECONCILIATION_TABLE}_${index + 1}`
    )),
  ],
  [OBSERVATION_TABLE]: [
    'idx_material_execution_observations_target',
    ...Array.from({ length: 2 }, (_, index) => (
      `sqlite_autoindex_${OBSERVATION_TABLE}_${index + 1}`
    )),
  ],
  [CLAIM_TABLE]: [
    'idx_material_effect_claims_target',
    ...Array.from({ length: 5 }, (_, index) => (
      `sqlite_autoindex_${CLAIM_TABLE}_${index + 1}`
    )),
  ],
});

const MATERIAL_LIFECYCLE_SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS eva_material_execution_contracts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  contract_ref TEXT NOT NULL UNIQUE,
  contract_sha256 TEXT NOT NULL UNIQUE,
  handoff_key TEXT NOT NULL UNIQUE,
  created_ts TEXT NOT NULL,
  raw_body_sha256 TEXT NOT NULL,
  projection_rule TEXT NOT NULL,
  projection_sha256 TEXT NOT NULL,
  work_item_id INTEGER NOT NULL,
  episode_id INTEGER NOT NULL,
  source_turn_id TEXT NOT NULL,
  authority_timeline_id INTEGER NOT NULL,
  understanding_sha256 TEXT NOT NULL,
  semantic_authority_sha256 TEXT NOT NULL,
  plan_sha256 TEXT NOT NULL,
  plan_seal_json TEXT NOT NULL,
  excluded_actions_json TEXT NOT NULL,
  excluded_actions_sha256 TEXT NOT NULL,
  obligation_json TEXT NOT NULL,
  obligation_sha256 TEXT NOT NULL,
  execution_turn_id TEXT NOT NULL UNIQUE,
  authority_sha256 TEXT NOT NULL UNIQUE,
  handoff_binding_sha256 TEXT NOT NULL,
  handoff_registry_sha256 TEXT NOT NULL UNIQUE,
  contract_json TEXT NOT NULL,
  authority_core_json TEXT NOT NULL,
  authority_core_sha256 TEXT NOT NULL UNIQUE,
  CHECK(contract='MaterialExecutionContract/v1'),
  CHECK(contract_ref='material-contract:' || contract_sha256),
  CHECK(length(contract_sha256)=64 AND lower(contract_sha256)=contract_sha256
    AND contract_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(handoff_key)=64 AND lower(handoff_key)=handoff_key
    AND handoff_key NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(raw_body_sha256)=64 AND lower(raw_body_sha256)=raw_body_sha256
    AND raw_body_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(projection_rule)>=1 AND length(projection_rule)<=80),
  CHECK(length(projection_sha256)=64 AND lower(projection_sha256)=projection_sha256
    AND projection_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(work_item_id)='integer' AND work_item_id>=1),
  CHECK(typeof(episode_id)='integer' AND episode_id>=1),
  CHECK(length(source_turn_id)>=1 AND length(source_turn_id)<=180),
  CHECK(typeof(authority_timeline_id)='integer' AND authority_timeline_id>=1),
  CHECK(length(understanding_sha256)=64
    AND understanding_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(semantic_authority_sha256)=64
    AND semantic_authority_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(plan_sha256)=64 AND plan_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(plan_seal_json)),
  CHECK(json_valid(excluded_actions_json)),
  CHECK(length(excluded_actions_sha256)=64
    AND excluded_actions_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(obligation_json) AND length(obligation_json)<=${MAX_OBLIGATION_JSON_BYTES}),
  CHECK(length(obligation_sha256)=64
    AND obligation_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(execution_turn_id GLOB 'material-execution:*'
    AND length(execution_turn_id)=83),
  CHECK(length(authority_sha256)=64 AND authority_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(handoff_binding_sha256)=64
    AND handoff_binding_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(handoff_registry_sha256)=64
    AND handoff_registry_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(contract_json)),
  CHECK(json_valid(authority_core_json)),
  CHECK(length(authority_core_sha256)=64
    AND authority_core_sha256 NOT GLOB '*[^a-f0-9]*')
);
CREATE INDEX IF NOT EXISTS idx_material_execution_contract_work_episode
  ON eva_material_execution_contracts(work_item_id,episode_id,id);
CREATE TRIGGER IF NOT EXISTS eva_material_execution_contracts_no_update
BEFORE UPDATE ON eva_material_execution_contracts
BEGIN SELECT RAISE(ABORT,'material execution contracts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_execution_contracts_no_delete
BEFORE DELETE ON eva_material_execution_contracts
BEGIN SELECT RAISE(ABORT,'material execution contracts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_execution_contracts_no_collision
BEFORE INSERT ON eva_material_execution_contracts
WHEN EXISTS (
  SELECT 1 FROM eva_material_execution_contracts
  WHERE id=NEW.id OR contract_ref=NEW.contract_ref
    OR contract_sha256=NEW.contract_sha256 OR handoff_key=NEW.handoff_key
    OR execution_turn_id=NEW.execution_turn_id
    OR authority_sha256=NEW.authority_sha256
    OR handoff_registry_sha256=NEW.handoff_registry_sha256
)
BEGIN SELECT RAISE(ABORT,'material execution contract identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_execution_contracts_valid_reference
BEFORE INSERT ON eva_material_execution_contracts
BEGIN
  SELECT CASE WHEN json_extract(NEW.contract_json,'$.contract') IS NOT NEW.contract
    OR json_extract(NEW.contract_json,'$.handoffKey') IS NOT NEW.handoff_key
    OR json_extract(NEW.contract_json,'$.rawBodySha256') IS NOT NEW.raw_body_sha256
    OR json_extract(NEW.contract_json,'$.projection.rule') IS NOT NEW.projection_rule
    OR json_extract(NEW.contract_json,'$.projection.sha256') IS NOT NEW.projection_sha256
    OR json_extract(NEW.contract_json,'$.workItemId') IS NOT NEW.work_item_id
    OR json_extract(NEW.contract_json,'$.episodeId') IS NOT NEW.episode_id
    OR json_extract(NEW.contract_json,'$.sourceTurnId') IS NOT NEW.source_turn_id
    OR json_extract(NEW.contract_json,'$.authorityTimelineId')
      IS NOT NEW.authority_timeline_id
    OR json_extract(NEW.contract_json,'$.understandingSha256')
      IS NOT NEW.understanding_sha256
    OR json_extract(NEW.contract_json,'$.semanticAuthoritySha256')
      IS NOT NEW.semantic_authority_sha256
    OR json_extract(NEW.contract_json,'$.planSha256') IS NOT NEW.plan_sha256
    OR json_extract(NEW.contract_json,'$.excludedActionsSha256')
      IS NOT NEW.excluded_actions_sha256
    OR json_extract(NEW.contract_json,'$.obligationSha256')
      IS NOT NEW.obligation_sha256
    OR json_extract(NEW.contract_json,'$.executionTurnId')
      IS NOT NEW.execution_turn_id
    OR json_extract(NEW.contract_json,'$.authoritySha256')
      IS NOT NEW.authority_sha256
    OR json_extract(NEW.contract_json,'$.handoffBindingSha256')
      IS NOT NEW.handoff_binding_sha256
    OR json_extract(NEW.contract_json,'$.handoffRegistrySha256')
      IS NOT NEW.handoff_registry_sha256
    OR json_extract(NEW.contract_json,'$.authorityCoreSha256')
      IS NOT NEW.authority_core_sha256
  THEN RAISE(ABORT,'material execution contract body conflicts with columns') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM timeline
    WHERE id=NEW.authority_timeline_id AND kind='turn_understanding'
      AND json_valid(meta)
      AND json_extract(meta,'$.turnId')=NEW.source_turn_id
      AND json_extract(meta,'$.interpretationSucceeded')=1
      AND json_extract(meta,'$.understanding.contract')='TurnUnderstandingRecord/v1'
      AND json_extract(meta,'$.understanding.sourceSha256')=NEW.understanding_sha256
  ) THEN RAISE(ABORT,'material execution contract has invalid turn authority') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_interpretation_retry_handoffs handoff
    JOIN eva_goal_episodes episode ON episode.id=handoff.episode_id
    JOIN eva_work_items work ON work.id=handoff.work_item_id
    WHERE handoff.handoff_key=NEW.handoff_key
      AND handoff.contract='ChatInterpretationRetry/v1'
      AND handoff.raw_original_text_sha256=NEW.raw_body_sha256
      AND handoff.work_item_id=NEW.work_item_id
      AND handoff.episode_id=NEW.episode_id
      AND handoff.binding_sha256=NEW.handoff_binding_sha256
      AND episode.work_item_id=work.id
  ) THEN RAISE(ABORT,'material execution contract has invalid handoff authority') END;
END;

CREATE TABLE IF NOT EXISTS eva_material_execution_observations (
  observation_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  execution_key TEXT NOT NULL UNIQUE,
  tool TEXT NOT NULL,
  physical_target_id TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  intent_record_sha256 TEXT NOT NULL,
  execution_snapshot_sha256 TEXT NOT NULL UNIQUE,
  observed_ts TEXT NOT NULL,
  CHECK(tool IN ('lees_bestand','schrijf_bestand','@host/workspace.lstat')),
  CHECK(length(physical_target_id)=64
    AND physical_target_id NOT GLOB '*[^a-f0-9]*'),
  CHECK(canonical_path GLOB 'data/workspace/?*'),
  CHECK(length(intent_record_sha256)=64
    AND intent_record_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(execution_snapshot_sha256)=64
    AND execution_snapshot_sha256 NOT GLOB '*[^a-f0-9]*')
);
CREATE INDEX IF NOT EXISTS idx_material_execution_observations_target
  ON eva_material_execution_observations(physical_target_id,observation_sequence);
CREATE TRIGGER IF NOT EXISTS eva_material_execution_observations_no_update
BEFORE UPDATE ON eva_material_execution_observations
BEGIN SELECT RAISE(ABORT,'material execution observations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_execution_observations_no_delete
BEFORE DELETE ON eva_material_execution_observations
BEGIN SELECT RAISE(ABORT,'material execution observations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_execution_observations_no_collision
BEFORE INSERT ON eva_material_execution_observations
WHEN EXISTS (
  SELECT 1 FROM eva_material_execution_observations
  WHERE observation_sequence=NEW.observation_sequence
    OR execution_key=NEW.execution_key
    OR execution_snapshot_sha256=NEW.execution_snapshot_sha256
)
BEGIN SELECT RAISE(ABORT,'material execution observation identity collision'); END;

CREATE TABLE IF NOT EXISTS eva_material_effect_intents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  intent_ref TEXT NOT NULL UNIQUE,
  intent_sha256 TEXT NOT NULL UNIQUE,
  contract_id INTEGER NOT NULL,
  contract_ref TEXT NOT NULL,
  contract_sha256 TEXT NOT NULL,
  requirement_id TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  physical_target_id TEXT NOT NULL,
  target_authority_sha256 TEXT NOT NULL,
  preimage_kind TEXT NOT NULL,
  preimage_sha256 TEXT,
  preimage_bytes INTEGER,
  intended_sha256 TEXT NOT NULL,
  intended_bytes INTEGER NOT NULL,
  pre_read_execution_key TEXT NOT NULL UNIQUE,
  pre_read_completion_sha256 TEXT NOT NULL UNIQUE,
  pre_read_sequence INTEGER NOT NULL,
  pre_read_path_write_head_sequence INTEGER NOT NULL,
  artifact_version_head_id INTEGER NOT NULL,
  artifact_version_head_ref TEXT,
  artifact_version_head_provenance_sha256 TEXT,
  write_execution_key TEXT NOT NULL UNIQUE,
  intent_json TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  UNIQUE(contract_id,requirement_id,canonical_path),
  CHECK(contract='MaterialEffectIntent/v1'),
  CHECK(intent_ref='material-effect-intent:' || intent_sha256),
  CHECK(length(intent_sha256)=64 AND intent_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(contract_ref)=82 AND contract_ref GLOB 'material-contract:*'),
  CHECK(length(contract_sha256)=64 AND contract_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(requirement_id GLOB 'r[1-9]' OR requirement_id GLOB 'r1[0-2]'),
  CHECK(canonical_path GLOB 'data/workspace/?*'
    AND canonical_path NOT GLOB '*\\*'
    AND canonical_path NOT LIKE '%/../%'),
  CHECK(length(physical_target_id)=64
    AND physical_target_id NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(target_authority_sha256)=64
    AND target_authority_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(preimage_kind IN ('absent','sha256')),
  CHECK((preimage_kind='absent' AND preimage_sha256 IS NULL AND preimage_bytes IS NULL)
    OR (preimage_kind='sha256' AND length(preimage_sha256)=64
      AND preimage_sha256 NOT GLOB '*[^a-f0-9]*'
      AND typeof(preimage_bytes)='integer'
      AND preimage_bytes>=0 AND preimage_bytes<=${MAX_MATERIAL_BYTES})),
  CHECK(length(intended_sha256)=64 AND intended_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(intended_bytes)='integer'
    AND intended_bytes>=0 AND intended_bytes<=${MAX_MATERIAL_BYTES}),
  CHECK(preimage_kind='absent'
    OR preimage_sha256<>intended_sha256 OR preimage_bytes<>intended_bytes),
  CHECK(length(pre_read_execution_key)>=1 AND length(pre_read_execution_key)<=240),
  CHECK(length(pre_read_completion_sha256)=64
    AND pre_read_completion_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(pre_read_sequence)='integer' AND pre_read_sequence>=1),
  CHECK(typeof(pre_read_path_write_head_sequence)='integer'
    AND pre_read_path_write_head_sequence>=0
    AND pre_read_path_write_head_sequence<pre_read_sequence),
  CHECK(typeof(artifact_version_head_id)='integer' AND artifact_version_head_id>=0),
  CHECK((artifact_version_head_id=0 AND artifact_version_head_ref IS NULL
      AND artifact_version_head_provenance_sha256 IS NULL)
    OR (artifact_version_head_id>=1
      AND artifact_version_head_ref GLOB 'artifact-version:*'
      AND length(artifact_version_head_provenance_sha256)=64
      AND artifact_version_head_provenance_sha256 NOT GLOB '*[^a-f0-9]*')),
  CHECK(write_execution_key GLOB 'material-write:*'
    AND length(write_execution_key)=79),
  CHECK(json_valid(intent_json))
);
CREATE INDEX IF NOT EXISTS idx_material_effect_intents_contract
  ON eva_material_effect_intents(contract_id,id);
CREATE INDEX IF NOT EXISTS idx_material_effect_intents_path
  ON eva_material_effect_intents(canonical_path,id);
CREATE TRIGGER IF NOT EXISTS eva_material_effect_intents_no_update
BEFORE UPDATE ON eva_material_effect_intents
BEGIN SELECT RAISE(ABORT,'material effect intents are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_effect_intents_no_delete
BEFORE DELETE ON eva_material_effect_intents
BEGIN SELECT RAISE(ABORT,'material effect intents are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_effect_intents_no_collision
BEFORE INSERT ON eva_material_effect_intents
WHEN EXISTS (
  SELECT 1 FROM eva_material_effect_intents
  WHERE id=NEW.id OR intent_ref=NEW.intent_ref OR intent_sha256=NEW.intent_sha256
    OR pre_read_execution_key=NEW.pre_read_execution_key
    OR pre_read_completion_sha256=NEW.pre_read_completion_sha256
    OR write_execution_key=NEW.write_execution_key
    OR (contract_id=NEW.contract_id AND requirement_id=NEW.requirement_id
      AND canonical_path=NEW.canonical_path)
)
BEGIN SELECT RAISE(ABORT,'material effect intent identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_effect_intents_valid_reference
BEFORE INSERT ON eva_material_effect_intents
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_material_execution_contracts
    WHERE id=NEW.contract_id AND contract_ref=NEW.contract_ref
      AND contract_sha256=NEW.contract_sha256
  ) THEN RAISE(ABORT,'material effect intent has invalid contract reference') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM main.turn_tool_executions execution
    JOIN eva_material_execution_observations observed
      ON observed.execution_key=execution.execution_key
    JOIN eva_material_execution_contracts material
      ON material.id=NEW.contract_id
    WHERE observed.observation_sequence=NEW.pre_read_sequence
      AND observed.physical_target_id=NEW.physical_target_id
      AND execution.execution_key=NEW.pre_read_execution_key
      AND execution.completion_record_sha256=NEW.pre_read_completion_sha256
      AND execution.status='done'
      AND execution.turn_id=material.execution_turn_id
      AND execution.tool IN ('lees_bestand','@host/workspace.lstat')
      AND json_valid(execution.args) AND json_valid(execution.semantic_binding)
      AND json_valid(execution.output)
      AND json_extract(execution.args,'$.pad')=NEW.canonical_path
      AND json_extract(execution.semantic_binding,'$.understandingSha256')
        =material.understanding_sha256
      AND json_extract(execution.semantic_binding,'$.requirementId')=NEW.requirement_id
      AND json_extract(execution.output,'$.ok')=1
      AND json_extract(execution.output,'$.verified')=1
      AND json_extract(execution.output,'$.partial')=0
      AND json_extract(execution.output,'$.needs_followup')=0
      AND ((execution.tool='lees_bestand'
        AND json_extract(execution.semantic_binding,'$.capabilityId')
          ='workspace.file.read'
        AND json_extract(execution.semantic_binding,'$.operationId')='read'
        AND json_extract(execution.output,'$.contract')='ActionResult/v1'
        AND json_extract(execution.output,'$.artifact_receipt.path')=NEW.canonical_path
        AND json_extract(execution.output,'$.artifact_receipt.complete_read')=1)
      OR (execution.tool='@host/workspace.lstat'
        AND json_extract(execution.args,'$.no_follow')=1
        AND json_extract(execution.semantic_binding,'$.capabilityId')
          ='workspace.file.stat'
        AND json_extract(execution.semantic_binding,'$.operationId')='lstat'
        AND json_extract(execution.output,'$.contract')
          ='WorkspacePathAbsenceReceipt/v1'
        AND json_extract(execution.output,'$.path')=NEW.canonical_path
        AND json_extract(execution.output,'$.absent')=1
        AND json_extract(execution.output,'$.noFollow')=1))
  ) THEN RAISE(ABORT,'material effect intent has invalid durable pre-read') END;
  SELECT CASE WHEN json_extract(NEW.intent_json,'$.contract') IS NOT NEW.contract
    OR json_extract(NEW.intent_json,'$.contractRef') IS NOT NEW.contract_ref
    OR json_extract(NEW.intent_json,'$.contractSha256') IS NOT NEW.contract_sha256
    OR json_extract(NEW.intent_json,'$.requirementId') IS NOT NEW.requirement_id
    OR json_extract(NEW.intent_json,'$.canonicalPath') IS NOT NEW.canonical_path
    OR json_extract(NEW.intent_json,'$.physicalTargetId') IS NOT NEW.physical_target_id
    OR json_extract(NEW.intent_json,'$.targetAuthoritySha256')
      IS NOT NEW.target_authority_sha256
    OR json_extract(NEW.intent_json,'$.preimage.kind') IS NOT NEW.preimage_kind
    OR json_extract(NEW.intent_json,'$.intendedPostimage.sha256')
      IS NOT NEW.intended_sha256
    OR json_extract(NEW.intent_json,'$.intendedPostimage.bytes')
      IS NOT NEW.intended_bytes
    OR json_extract(NEW.intent_json,'$.preReadExecutionKey')
      IS NOT NEW.pre_read_execution_key
    OR json_extract(NEW.intent_json,'$.preReadCompletionSha256')
      IS NOT NEW.pre_read_completion_sha256
    OR json_extract(NEW.intent_json,'$.preReadSequence') IS NOT NEW.pre_read_sequence
    OR json_extract(NEW.intent_json,'$.preReadPathWriteHeadSequence')
      IS NOT NEW.pre_read_path_write_head_sequence
    OR json_extract(NEW.intent_json,'$.artifactVersionHeadId')
      IS NOT NEW.artifact_version_head_id
    OR json_extract(NEW.intent_json,'$.artifactVersionHeadRef')
      IS NOT NEW.artifact_version_head_ref
    OR json_extract(NEW.intent_json,'$.artifactVersionHeadProvenanceSha256')
      IS NOT NEW.artifact_version_head_provenance_sha256
    OR json_extract(NEW.intent_json,'$.writeExecutionKey')
      IS NOT NEW.write_execution_key
  THEN RAISE(ABORT,'material effect intent body conflicts with columns') END;
  SELECT CASE WHEN NEW.pre_read_path_write_head_sequence IS NOT coalesce((
    SELECT max(observation_sequence) FROM eva_material_execution_observations
    WHERE tool='schrijf_bestand' AND physical_target_id=NEW.physical_target_id
  ),0) THEN RAISE(ABORT,'material effect intent has a stale path head') END;
END;

CREATE TABLE IF NOT EXISTS eva_material_effect_claims (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  claim_ref TEXT NOT NULL UNIQUE,
  claim_sha256 TEXT NOT NULL UNIQUE,
  intent_id INTEGER NOT NULL,
  intent_ref TEXT NOT NULL,
  intent_sha256 TEXT NOT NULL,
  generation INTEGER NOT NULL,
  physical_target_id TEXT NOT NULL,
  target_authority_sha256 TEXT NOT NULL,
  execution_key TEXT NOT NULL UNIQUE,
  fence_token TEXT NOT NULL UNIQUE,
  observation_head_sequence INTEGER NOT NULL,
  artifact_version_head_ref TEXT,
  artifact_version_head_provenance_sha256 TEXT,
  predecessor_reconciliation_ref TEXT,
  claim_json TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  UNIQUE(intent_id,generation),
  CHECK(contract='MaterialEffectWriteClaim/v1'),
  CHECK(claim_ref='material-effect-claim:' || claim_sha256),
  CHECK(length(claim_sha256)=64 AND claim_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(generation IN (0,1)),
  CHECK(length(physical_target_id)=64
    AND physical_target_id NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(target_authority_sha256)=64
    AND target_authority_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(fence_token)=64 AND fence_token NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(observation_head_sequence)='integer'
    AND observation_head_sequence>=0),
  CHECK((artifact_version_head_ref IS NULL
      AND artifact_version_head_provenance_sha256 IS NULL)
    OR (artifact_version_head_ref GLOB 'artifact-version:*'
      AND length(artifact_version_head_provenance_sha256)=64
      AND artifact_version_head_provenance_sha256 NOT GLOB '*[^a-f0-9]*')),
  CHECK((generation=0 AND predecessor_reconciliation_ref IS NULL)
    OR (generation=1
      AND predecessor_reconciliation_ref GLOB 'material-effect-reconciliation:*')),
  CHECK(json_valid(claim_json))
);
CREATE INDEX IF NOT EXISTS idx_material_effect_claims_target
  ON eva_material_effect_claims(physical_target_id,id);
CREATE TRIGGER IF NOT EXISTS eva_material_effect_claims_no_update
BEFORE UPDATE ON eva_material_effect_claims
BEGIN SELECT RAISE(ABORT,'material effect claims are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_effect_claims_no_delete
BEFORE DELETE ON eva_material_effect_claims
BEGIN SELECT RAISE(ABORT,'material effect claims are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_effect_claims_no_collision
BEFORE INSERT ON eva_material_effect_claims
WHEN EXISTS (
  SELECT 1 FROM eva_material_effect_claims
  WHERE id=NEW.id OR claim_ref=NEW.claim_ref OR claim_sha256=NEW.claim_sha256
    OR execution_key=NEW.execution_key OR fence_token=NEW.fence_token
    OR (intent_id=NEW.intent_id AND generation=NEW.generation)
)
BEGIN SELECT RAISE(ABORT,'material effect claim identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_effect_claims_valid_reference
BEFORE INSERT ON eva_material_effect_claims
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_material_effect_intents
    WHERE id=NEW.intent_id AND intent_ref=NEW.intent_ref
      AND intent_sha256=NEW.intent_sha256
      AND physical_target_id=NEW.physical_target_id
      AND target_authority_sha256=NEW.target_authority_sha256
  ) THEN RAISE(ABORT,'material effect claim has invalid intent reference') END;
  SELECT CASE WHEN NEW.generation=1 AND NOT EXISTS (
    SELECT 1 FROM eva_material_effect_reconciliations
    WHERE intent_id=NEW.intent_id AND claim_generation=0
      AND reconciliation_ref=NEW.predecessor_reconciliation_ref
      AND outcome='no_effect' AND next_action='retry_cas'
      AND recovery_generation=1 AND recovery_write_execution_key=NEW.execution_key
  ) THEN RAISE(ABORT,'material effect recovery claim lacks predecessor') END;
  SELECT CASE WHEN json_extract(NEW.claim_json,'$.contract') IS NOT NEW.contract
    OR json_extract(NEW.claim_json,'$.intentRef') IS NOT NEW.intent_ref
    OR json_extract(NEW.claim_json,'$.intentSha256') IS NOT NEW.intent_sha256
    OR json_extract(NEW.claim_json,'$.generation') IS NOT NEW.generation
    OR json_extract(NEW.claim_json,'$.physicalTargetId') IS NOT NEW.physical_target_id
    OR json_extract(NEW.claim_json,'$.targetAuthoritySha256')
      IS NOT NEW.target_authority_sha256
    OR json_extract(NEW.claim_json,'$.executionKey') IS NOT NEW.execution_key
    OR json_extract(NEW.claim_json,'$.fenceToken') IS NOT NEW.fence_token
    OR json_extract(NEW.claim_json,'$.observationHeadSequence')
      IS NOT NEW.observation_head_sequence
  THEN RAISE(ABORT,'material effect claim body conflicts with columns') END;
END;

CREATE TABLE IF NOT EXISTS eva_material_effect_reconciliations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract TEXT NOT NULL,
  reconciliation_ref TEXT NOT NULL UNIQUE,
  reconciliation_sha256 TEXT NOT NULL UNIQUE,
  intent_id INTEGER NOT NULL,
  intent_ref TEXT NOT NULL,
  intent_sha256 TEXT NOT NULL,
  contract_ref TEXT NOT NULL,
  claim_generation INTEGER NOT NULL,
  claim_ref TEXT NOT NULL UNIQUE,
  claim_sha256 TEXT NOT NULL,
  claim_fence_token TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  outcome TEXT NOT NULL,
  reason TEXT NOT NULL,
  next_action TEXT NOT NULL,
  recovery_generation INTEGER NOT NULL,
  recovery_write_execution_key TEXT UNIQUE,
  current_kind TEXT NOT NULL,
  current_sha256 TEXT,
  current_bytes INTEGER,
  recovery_read_execution_key TEXT NOT NULL UNIQUE,
  recovery_read_completion_sha256 TEXT NOT NULL UNIQUE,
  recovery_read_sequence INTEGER NOT NULL,
  started_write_sequence INTEGER NOT NULL,
  started_write_intent_sha256 TEXT NOT NULL,
  material_intent_head_id INTEGER NOT NULL,
  path_write_head_sequence INTEGER NOT NULL,
  artifact_version_head_id INTEGER NOT NULL,
  cas_context_json TEXT NOT NULL,
  cas_context_sha256 TEXT NOT NULL UNIQUE,
  observation_json TEXT NOT NULL,
  observation_sha256 TEXT NOT NULL UNIQUE,
  receipt_json TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  UNIQUE(intent_id,claim_generation),
  UNIQUE(intent_ref,claim_generation),
  CHECK(contract='MaterialEffectReconciliationReceipt/v1'),
  CHECK(reconciliation_ref='material-effect-reconciliation:' || reconciliation_sha256),
  CHECK(length(reconciliation_sha256)=64
    AND reconciliation_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(intent_ref)=87 AND intent_ref GLOB 'material-effect-intent:*'),
  CHECK(length(intent_sha256)=64 AND intent_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(contract_ref)=82 AND contract_ref GLOB 'material-contract:*'),
  CHECK(claim_generation IN (0,1)),
  CHECK(claim_ref GLOB 'material-effect-claim:*'),
  CHECK(length(claim_sha256)=64 AND claim_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(claim_fence_token)=64
    AND claim_fence_token NOT GLOB '*[^a-f0-9]*'),
  CHECK(canonical_path GLOB 'data/workspace/?*'),
  CHECK(outcome IN ('applied','no_effect','uncertain_manual')),
  CHECK(length(reason)>=1 AND length(reason)<=80),
  CHECK((outcome='applied' AND next_action='none'
      AND recovery_generation=claim_generation
      AND recovery_write_execution_key IS NULL)
    OR (outcome='no_effect' AND claim_generation=0
      AND next_action='retry_cas' AND recovery_generation=1
      AND recovery_write_execution_key GLOB 'material-write-recovery:*'
      AND length(recovery_write_execution_key)=88)
    OR (outcome='uncertain_manual' AND next_action='manual'
      AND recovery_generation=claim_generation
      AND recovery_write_execution_key IS NULL)),
  CHECK(current_kind IN ('absent','sha256')),
  CHECK((current_kind='absent' AND current_sha256 IS NULL AND current_bytes IS NULL)
    OR (current_kind='sha256' AND length(current_sha256)=64
      AND current_sha256 NOT GLOB '*[^a-f0-9]*'
      AND typeof(current_bytes)='integer'
      AND current_bytes>=0 AND current_bytes<=${MAX_MATERIAL_BYTES})),
  CHECK(length(recovery_read_execution_key)>=1
    AND length(recovery_read_execution_key)<=240),
  CHECK(length(recovery_read_completion_sha256)=64
    AND recovery_read_completion_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(recovery_read_sequence)='integer' AND recovery_read_sequence>=1),
  CHECK(typeof(started_write_sequence)='integer' AND started_write_sequence>=1),
  CHECK(length(started_write_intent_sha256)=64
    AND started_write_intent_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(typeof(material_intent_head_id)='integer' AND material_intent_head_id>=1),
  CHECK(typeof(path_write_head_sequence)='integer' AND path_write_head_sequence>=1),
  CHECK(typeof(artifact_version_head_id)='integer' AND artifact_version_head_id>=0),
  CHECK(json_valid(cas_context_json)),
  CHECK(length(cas_context_sha256)=64
    AND cas_context_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(observation_json)),
  CHECK(length(observation_sha256)=64
    AND observation_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(receipt_json))
);
CREATE INDEX IF NOT EXISTS idx_material_effect_reconciliations_outcome
  ON eva_material_effect_reconciliations(outcome,id);
CREATE TRIGGER IF NOT EXISTS eva_material_effect_reconciliations_no_update
BEFORE UPDATE ON eva_material_effect_reconciliations
BEGIN SELECT RAISE(ABORT,'material effect reconciliations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_effect_reconciliations_no_delete
BEFORE DELETE ON eva_material_effect_reconciliations
BEGIN SELECT RAISE(ABORT,'material effect reconciliations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_effect_reconciliations_no_collision
BEFORE INSERT ON eva_material_effect_reconciliations
WHEN EXISTS (
  SELECT 1 FROM eva_material_effect_reconciliations
  WHERE id=NEW.id OR reconciliation_ref=NEW.reconciliation_ref
    OR reconciliation_sha256=NEW.reconciliation_sha256
    OR claim_ref=NEW.claim_ref OR observation_sha256=NEW.observation_sha256
    OR recovery_read_execution_key=NEW.recovery_read_execution_key
    OR recovery_read_completion_sha256=NEW.recovery_read_completion_sha256
    OR cas_context_sha256=NEW.cas_context_sha256
    OR (intent_id=NEW.intent_id AND claim_generation=NEW.claim_generation)
    OR (intent_ref=NEW.intent_ref AND claim_generation=NEW.claim_generation)
    OR (NEW.recovery_write_execution_key IS NOT NULL
      AND recovery_write_execution_key=NEW.recovery_write_execution_key)
)
BEGIN SELECT RAISE(ABORT,'material effect reconciliation identity collision'); END;
CREATE TRIGGER IF NOT EXISTS eva_material_effect_reconciliations_valid_reference
BEFORE INSERT ON eva_material_effect_reconciliations
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_material_effect_intents
    WHERE id=NEW.intent_id AND intent_ref=NEW.intent_ref
      AND intent_sha256=NEW.intent_sha256 AND contract_ref=NEW.contract_ref
      AND canonical_path=NEW.canonical_path
  ) THEN RAISE(ABORT,'material effect reconciliation has invalid intent reference') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM eva_material_effect_claims
    WHERE intent_id=NEW.intent_id AND generation=NEW.claim_generation
      AND claim_ref=NEW.claim_ref AND claim_sha256=NEW.claim_sha256
      AND fence_token=NEW.claim_fence_token
  ) THEN RAISE(ABORT,'material effect reconciliation has invalid claim reference') END;
  SELECT CASE WHEN NEW.recovery_read_sequence<=NEW.started_write_sequence
    OR NEW.started_write_sequence<=(
      SELECT pre_read_sequence FROM eva_material_effect_intents WHERE id=NEW.intent_id
    )
  THEN RAISE(ABORT,'material effect reconciliation has invalid execution order') END;
  SELECT CASE WHEN NEW.material_intent_head_id IS NOT (
    SELECT max(id) FROM eva_material_effect_intents
    WHERE physical_target_id=(
      SELECT physical_target_id FROM eva_material_effect_intents WHERE id=NEW.intent_id
    )
  ) THEN RAISE(ABORT,'material effect reconciliation has a stale intent head') END;
  SELECT CASE WHEN NEW.path_write_head_sequence IS NOT (
    SELECT max(observation_sequence) FROM eva_material_execution_observations
    WHERE tool='schrijf_bestand' AND physical_target_id=(
      SELECT physical_target_id FROM eva_material_effect_intents WHERE id=NEW.intent_id
    )
  ) THEN RAISE(ABORT,'material effect reconciliation has a stale write head') END;
  SELECT CASE WHEN NOT json_valid(NEW.cas_context_json)
    OR json_extract(NEW.cas_context_json,'$.contract')
      IS NOT 'MaterialEffectCasContext/v1'
    OR json_extract(NEW.cas_context_json,'$.materialIntentHeadId')
      IS NOT NEW.material_intent_head_id
    OR json_extract(NEW.cas_context_json,'$.pathWriteHeadSequence')
      IS NOT NEW.path_write_head_sequence
    OR json_extract(NEW.cas_context_json,'$.artifactVersionHeadId')
      IS NOT NEW.artifact_version_head_id
    OR (NEW.outcome IN ('applied','no_effect')
      AND json_extract(NEW.cas_context_json,'$.conflictFree') IS NOT 1)
  THEN RAISE(ABORT,'material effect reconciliation has invalid CAS context') END;
  SELECT CASE WHEN NEW.recovery_read_execution_key IS NOT (
    SELECT execution.execution_key FROM main.turn_tool_executions execution
    JOIN eva_material_execution_observations observed
      ON observed.execution_key=execution.execution_key
    JOIN eva_material_effect_intents intent ON intent.id=NEW.intent_id
    JOIN eva_material_execution_contracts material
      ON material.contract_ref=intent.contract_ref
    WHERE observed.observation_sequence=NEW.recovery_read_sequence
      AND execution.tool IN ('lees_bestand','@host/workspace.lstat')
      AND execution.status='done'
      AND execution.turn_id=material.execution_turn_id
      AND execution.completion_record_sha256=NEW.recovery_read_completion_sha256
      AND json_valid(execution.args) AND json_valid(execution.semantic_binding)
      AND json_valid(execution.output)
      AND json_extract(execution.args,'$.pad')=NEW.canonical_path
      AND json_extract(execution.semantic_binding,'$.understandingSha256')
        =material.understanding_sha256
      AND json_extract(execution.semantic_binding,'$.requirementId')
        =intent.requirement_id
      AND json_extract(execution.output,'$.ok')=1
      AND json_extract(execution.output,'$.verified')=1
      AND json_extract(execution.output,'$.partial')=0
      AND json_extract(execution.output,'$.needs_followup')=0
  ) THEN RAISE(ABORT,'material effect reconciliation has invalid recovery read') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM main.turn_tool_executions execution
    JOIN eva_material_execution_observations observed
      ON observed.execution_key=execution.execution_key
    JOIN eva_material_effect_claims claim ON claim.claim_ref=NEW.claim_ref
    WHERE observed.observation_sequence=NEW.started_write_sequence
      AND execution.execution_key=claim.execution_key
      AND execution.tool='schrijf_bestand'
      AND execution.status IN ('started','done')
      AND execution.intent_record_sha256=NEW.started_write_intent_sha256
  ) THEN RAISE(ABORT,'material effect reconciliation has invalid started write') END;
  SELECT CASE WHEN NEW.outcome='applied' AND NOT EXISTS (
    SELECT 1 FROM eva_material_effect_intents
    WHERE id=NEW.intent_id AND preimage_kind IN ('absent','sha256')
      AND (preimage_kind='absent' OR preimage_sha256<>intended_sha256
        OR preimage_bytes<>intended_bytes)
      AND NEW.current_kind='sha256'
      AND NEW.current_sha256=intended_sha256 AND NEW.current_bytes=intended_bytes
  ) THEN RAISE(ABORT,'applied reconciliation does not prove intended postimage') END;
  SELECT CASE WHEN NEW.outcome='no_effect' AND NOT EXISTS (
    SELECT 1 FROM eva_material_effect_intents
    WHERE id=NEW.intent_id
      AND ((preimage_kind='absent' AND NEW.current_kind='absent')
        OR (preimage_kind='sha256' AND NEW.current_kind='sha256'
          AND NEW.current_sha256=preimage_sha256 AND NEW.current_bytes=preimage_bytes))
  ) THEN RAISE(ABORT,'no-effect reconciliation does not prove the preimage') END;
  SELECT CASE WHEN json_extract(NEW.receipt_json,'$.contract') IS NOT NEW.contract
    OR json_extract(NEW.receipt_json,'$.intentRef') IS NOT NEW.intent_ref
    OR json_extract(NEW.receipt_json,'$.intentSha256') IS NOT NEW.intent_sha256
    OR json_extract(NEW.receipt_json,'$.contractRef') IS NOT NEW.contract_ref
    OR json_extract(NEW.receipt_json,'$.claimGeneration') IS NOT NEW.claim_generation
    OR json_extract(NEW.receipt_json,'$.claimRef') IS NOT NEW.claim_ref
    OR json_extract(NEW.receipt_json,'$.canonicalPath') IS NOT NEW.canonical_path
    OR json_extract(NEW.receipt_json,'$.outcome') IS NOT NEW.outcome
    OR json_extract(NEW.receipt_json,'$.reason') IS NOT NEW.reason
    OR json_extract(NEW.receipt_json,'$.nextAction') IS NOT NEW.next_action
    OR json_extract(NEW.receipt_json,'$.recoveryGeneration')
      IS NOT NEW.recovery_generation
    OR json_extract(NEW.receipt_json,'$.observationSha256')
      IS NOT NEW.observation_sha256
    OR json_extract(NEW.receipt_json,'$.casContextSha256')
      IS NOT NEW.cas_context_sha256
  THEN RAISE(ABORT,'material effect reconciliation body conflicts with columns') END;
END;
`;

let initializationFailure = '';
try {
  lifecycleDb.exec(MATERIAL_LIFECYCLE_SCHEMA_SQL);
} catch (error) {
  initializationFailure = String(error?.message || error || 'schema initialization failed');
}

const AUTHORITY_VERIFIERS = [];
const RECOVERY_VERIFIERS = [];
const CONTRACT_CAPABILITIES = new WeakMap();
const INTENT_CAPABILITIES = new WeakMap();
const CLAIM_CAPABILITIES = new WeakMap();
const RECONCILIATION_CAPABILITIES = new WeakMap();

const hashBytes = (value) => createHash('sha256').update(value).digest('hex');
const hashJson = (value) => hashBytes(canonicalJson(value));
const nowIso = () => new Date().toISOString();

function exactKeys(value, fields) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && canonicalJson(Object.keys(value).sort()) === canonicalJson([...fields].sort());
}

function normalizeSqlOutsideLiterals(value, transform) {
  const source = String(value || '');
  const literals = [];
  let masked = '';
  for (let index = 0; index < source.length;) {
    if (source[index] !== "'") {
      masked += source[index];
      index += 1;
      continue;
    }
    const start = index;
    index += 1;
    while (index < source.length) {
      if (source[index] !== "'") {
        index += 1;
        continue;
      }
      if (source[index + 1] === "'") {
        index += 2;
        continue;
      }
      index += 1;
      break;
    }
    const literalIndex = literals.push(source.slice(start, index)) - 1;
    masked += `\u{e000}${literalIndex}\u{e001}`;
  }
  const normalized = transform(masked);
  return normalized.replace(/\u{e000}(\d+)\u{e001}/gu, (
    _match, literalIndex,
  ) => literals[Number(literalIndex)]);
}

function normalizedDdl(value) {
  return normalizeSqlOutsideLiterals(value, (syntax) => syntax
    .replaceAll('"', '').replaceAll('`', '').replaceAll('[', '').replaceAll(']', '')
    .replace(/\bIF\s+NOT\s+EXISTS\b/giu, '')
    .replace(/\s+/gu, ' ')
    .replace(/\s*([(),;=])\s*/gu, '$1')
    .replace(/;+$/u, '')
    .trim()
    .toLowerCase());
}

function expectedDdl(type, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
  const kind = type === 'index' ? '(?:UNIQUE\\s+)?INDEX' : type.toUpperCase();
  const position = MATERIAL_LIFECYCLE_SCHEMA_SQL.search(new RegExp(
    `CREATE\\s+${kind}\\s+IF\\s+NOT\\s+EXISTS\\s+${escaped}`, 'iu',
  ));
  if (position < 0) return '';
  const tail = MATERIAL_LIFECYCLE_SCHEMA_SQL.slice(position);
  if (type !== 'trigger') return tail.slice(0, tail.indexOf(';') + 1);
  const nextStatement = tail.search(
    /\nCREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX|TRIGGER)\s+IF\s+NOT\s+EXISTS\s+/iu,
  );
  return (nextStatement < 0 ? tail : tail.slice(0, nextStatement)).trim();
}

function exactSchemaObject(type, name) {
  try {
    const stored = lifecycleDb.prepare(
      'SELECT sql FROM main.sqlite_master WHERE type=? AND name=?',
    ).get(type, name)?.sql;
    const expected = expectedDdl(type, name);
    return Boolean(stored && expected && normalizedDdl(stored) === normalizedDdl(expected));
  } catch { return false; }
}

function exactLifecycleObjectNames(type, expectedByTable) {
  try {
    return Object.entries(expectedByTable).every(([table, expected]) => {
      const actual = lifecycleDb.prepare(`SELECT name FROM main.sqlite_master
        WHERE type=? AND tbl_name=? ORDER BY name`).all(type, table)
        .map((row) => row.name);
      return canonicalJson(actual) === canonicalJson([...expected].sort());
    });
  } catch { return false; }
}

function normalizedRegistrySql(value) {
  return normalizeSqlOutsideLiterals(value, (syntax) => syntax
    .replace(/;\s*$/u, '').replace(/\s+/gu, ' ').trim());
}

function registryIndexColumns(name) {
  const rows = lifecycleDb.prepare(`PRAGMA main.index_xinfo('${name}')`).all();
  const keyRows = rows.filter((row) => Number(row.key) === 1);
  if (!keyRows.length || keyRows.some((row) => Number(row.cid) < 0
    || Number(row.desc) !== 0 || row.coll !== 'BINARY')) return '';
  return keyRows.map((row) => row.name).join('\0');
}

function exactRegistryIndexes() {
  const expectedUnique = new Map([
    ['handoff_key', 'pk'], ['work_item_id', 'u'], ['episode_id', 'u'],
    ['contract\0origin_channel\0origin_conversation_id\0origin_message_id', 'u'],
  ]);
  const indexes = lifecycleDb.prepare(`PRAGMA main.index_list('${HANDOFF_TABLE}')`).all();
  let originSeen = false;
  for (const index of indexes) {
    if (Number(index.partial) !== 0) return false;
    const columns = registryIndexColumns(index.name);
    if (index.name === HANDOFF_ORIGIN_INDEX) {
      const stored = lifecycleDb.prepare(`SELECT sql FROM main.sqlite_master
        WHERE type='index' AND name=? AND tbl_name=?`).get(
        HANDOFF_ORIGIN_INDEX, HANDOFF_TABLE,
      );
      if (originSeen || Number(index.unique) !== 0 || index.origin !== 'c'
        || columns !== 'origin_channel\0origin_conversation_id\0origin_message_id'
        || normalizedRegistrySql(stored?.sql)
          !== normalizedRegistrySql(HANDOFF_ORIGIN_INDEX_SQL)) return false;
      originSeen = true;
      continue;
    }
    if (Number(index.unique) !== 1 || expectedUnique.get(columns) !== index.origin) {
      return false;
    }
    expectedUnique.delete(columns);
  }
  return originSeen && expectedUnique.size === 0
    && indexes.length === 5;
}

function interpretationRegistrySchemaFailure() {
  let table;
  try {
    table = lifecycleDb.prepare(`SELECT sql FROM main.sqlite_master
      WHERE type='table' AND name=?`).get(HANDOFF_TABLE);
  } catch { return 'material_lifecycle_interpretation_registry_unavailable'; }
  if (!table) return 'material_lifecycle_interpretation_registry_unavailable';
  try {
    const columns = lifecycleDb.prepare(`PRAGMA main.table_info('${HANDOFF_TABLE}')`).all();
    const flags = lifecycleDb.prepare(
      `PRAGMA main.table_list('${HANDOFF_TABLE}')`,
    ).get();
    const foreignKeys = lifecycleDb.prepare(`PRAGMA main.foreign_key_list('${HANDOFF_TABLE}')`).all();
    if (columns.length !== HANDOFF_COLUMNS.length
      || columns.some((row, index) => {
        const expected = HANDOFF_COLUMNS[index];
        return Number(row.cid) !== index || row.name !== expected[0]
          || String(row.type).toUpperCase() !== expected[1]
          || Number(row.notnull) !== expected[2] || Number(row.pk) !== expected[3]
          || row.dflt_value != null;
      })
      || Number(flags?.wr) !== 0 || Number(flags?.strict) !== 0
      || foreignKeys.length !== 0
      || normalizedRegistrySql(table.sql) !== normalizedRegistrySql(HANDOFF_TABLE_SQL)
      || !exactRegistryIndexes()) {
      return 'material_lifecycle_interpretation_registry_schema_invalid';
    }
    const triggers = lifecycleDb.prepare(`SELECT name,sql FROM main.sqlite_master
      WHERE type='trigger' AND tbl_name=? ORDER BY name`).all(HANDOFF_TABLE);
    if (triggers.length !== HANDOFF_TRIGGER_SQL.size || triggers.some((trigger) => (
      normalizedRegistrySql(trigger.sql)
        !== normalizedRegistrySql(HANDOFF_TRIGGER_SQL.get(trigger.name))
    ))) return 'material_lifecycle_interpretation_registry_schema_invalid';
  } catch { return 'material_lifecycle_interpretation_registry_schema_invalid'; }
  return '';
}

function hasCriticalTemporarySchemaObject() {
  try {
    const critical = new Set([
      ...Object.keys(REQUIRED_COLUMNS), ...Object.keys(REFERENCE_COLUMNS),
      'turn_tool_executions', HANDOFF_TABLE,
    ].map((name) => name.toLowerCase()));
    return lifecycleDb.prepare(`SELECT name,tbl_name FROM temp.sqlite_master
      ORDER BY name`).all().some((row) => (
      String(row.name || '').toLowerCase().startsWith(OWNED_PREFIX)
        || critical.has(String(row.name || '').toLowerCase())
        || critical.has(String(row.tbl_name || '').toLowerCase())
        || String(row.name || '').toLowerCase().startsWith('eva_artifact_')
    ));
  } catch { return true; }
}

function exactReferenceIndexes(table) {
  const expected = REFERENCE_INDEXES[table];
  if (!expected) return table === 'eva_artifact_versions';
  const actual = lifecycleDb.prepare(`PRAGMA main.index_list('${table}')`).all()
    .map((index) => {
      const keyRows = lifecycleDb.prepare(
        `PRAGMA main.index_xinfo('${String(index.name)}')`,
      ).all().filter((entry) => Number(entry.key) === 1);
      if (keyRows.some((entry) => Number(entry.desc) !== 0
        || entry.coll !== 'BINARY')) return null;
      const storedSql = lifecycleDb.prepare(`SELECT sql FROM main.sqlite_master
        WHERE type='index' AND name=? AND tbl_name=?`).get(index.name, table)?.sql;
      return [
        String(index.name), Number(index.unique), String(index.origin),
        Number(index.partial), keyRows.map((entry) => entry.name),
        storedSql == null ? null : hashBytes(String(storedSql)),
      ];
    });
  if (actual.some((index) => index == null)) return false;
  const sorted = (values) => values.sort((left, right) => (
    String(left[0]).localeCompare(String(right[0]), 'en')
  ));
  return canonicalJson(sorted(actual))
    === canonicalJson(sorted(expected.map((index) => [...index])));
}

function exactReferenceTriggers(table) {
  if (table === 'eva_artifact_versions') return true;
  const expected = REFERENCE_TRIGGERS[table] || [];
  const actual = lifecycleDb.prepare(`SELECT name,sql FROM main.sqlite_master
    WHERE type='trigger' AND tbl_name=? ORDER BY name`).all(table)
    .map(({ name, sql }) => [String(name), hashBytes(String(sql || ''))]);
  return canonicalJson(actual) === canonicalJson(expected);
}

function exactReferenceSchemas() {
  try {
    if (!executionLedgerStatus(lifecycleDb).available) return false;
    if (!turnUnderstandingAuthorityStoreStatus(lifecycleDb).available) return false;
    return Object.entries(REFERENCE_COLUMNS).every(([table, expected]) => {
      const master = lifecycleDb.prepare(
        "SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?",
      ).get(table);
      if (!master) return false;
      const sql = lifecycleDb.prepare(
        "SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?",
      ).get(table)?.sql;
      const expectedDefaults = REFERENCE_DEFAULTS[table] || {};
      const actual = lifecycleDb.prepare(`PRAGMA main.table_info('${table}')`).all()
        .map((column) => [
          String(column.name), String(column.type).toUpperCase(),
          Number(column.notnull), Number(column.pk), column.dflt_value,
        ]);
      const exactColumns = expected.map((column) => [
        ...column, expectedDefaults[column[0]] ?? null,
      ]);
      const flags = lifecycleDb.prepare(`PRAGMA main.table_list('${table}')`).get();
      const foreignKeys = lifecycleDb.prepare(
        `PRAGMA main.foreign_key_list('${table}')`,
      ).all();
      return canonicalJson(actual) === canonicalJson(exactColumns)
        && Number(flags?.wr) === 0 && Number(flags?.strict) === 0
        && foreignKeys.length === 0
        && exactReferenceTriggers(table)
        && (table === 'eva_artifact_versions'
          || hashBytes(String(sql || '')) === REFERENCE_TABLE_SQL_SHA256[table])
        && exactReferenceIndexes(table);
    });
  } catch { return false; }
}

function schemaFailure() {
  if (hasCriticalTemporarySchemaObject()) return 'material_lifecycle_schema_temp_shadow';
  if (initializationFailure) return 'material_lifecycle_schema_initialization_failed';
  let artifactStatus;
  try { artifactStatus = artifactVersionStoreStatus(); } catch {
    return 'material_lifecycle_artifact_store_unavailable:status_error';
  }
  if (!exactKeys(artifactStatus, ['contract', 'available'])
    || artifactStatus?.contract !== 'ArtifactVersionStoreStatus/v1'
    || artifactStatus.available !== true) {
    return `material_lifecycle_artifact_store_unavailable:${String(
      artifactStatus?.reason || 'invalid_status',
    )}`;
  }
  const registryFailure = interpretationRegistrySchemaFailure();
  if (registryFailure) return registryFailure;
  if (!exactReferenceSchemas()) return 'material_lifecycle_reference_schema_invalid';
  for (const [table, columns] of Object.entries(REQUIRED_COLUMNS)) {
    let actual;
    try {
      actual = lifecycleDb.prepare(`PRAGMA main.table_info('${table}')`).all()
        .map((column) => column.name);
    } catch { return `material_lifecycle_schema_invalid:${table}`; }
    if (canonicalJson(actual) !== canonicalJson(columns)
      || !exactSchemaObject('table', table)) {
      return `material_lifecycle_schema_invalid:${table}`;
    }
  }
  if (!exactLifecycleObjectNames('trigger', LIFECYCLE_TRIGGER_NAMES)
    || REQUIRED_TRIGGERS.some((name) => !exactSchemaObject('trigger', name))) {
    return 'material_lifecycle_schema_triggers_invalid';
  }
  if (!exactLifecycleObjectNames('index', LIFECYCLE_INDEX_NAMES)
    || REQUIRED_INDEXES.some((name) => !exactSchemaObject('index', name))) {
    return 'material_lifecycle_schema_indexes_invalid';
  }
  return '';
}

export function materialLifecycleStoreStatus() {
  const reason = schemaFailure();
  return Object.freeze({
    contract: 'MaterialLifecycleStoreStatus/v1', available: !reason,
    ...(reason ? { reason } : {}),
  });
}

function requireStore() {
  const status = materialLifecycleStoreStatus();
  if (!status.available) {
    throw new Error(`material lifecycle store unavailable: ${status.reason}`);
  }
}

function registerOnlyVerifier(registry, verifier, label) {
  if (typeof verifier !== 'function' || registry.length) {
    throw new Error(`${label} verifier can only be registered once`);
  }
  registry.push(verifier);
}

export function registerMaterialLifecycleAuthorityVerifier(verifier) {
  registerOnlyVerifier(AUTHORITY_VERIFIERS, verifier, 'material lifecycle authority');
}

export function registerMaterialEffectRecoveryVerifier(verifier) {
  registerOnlyVerifier(RECOVERY_VERIFIERS, verifier, 'material effect recovery');
}

function validSha(value) {
  return SHA256.test(String(value || '')) ? String(value) : '';
}

function positiveId(value) {
  const id = Number(value);
  return Number.isSafeInteger(id) && id >= 1 ? id : 0;
}

function positiveSequence(value) {
  const sequence = Number(value);
  return Number.isSafeInteger(sequence) && sequence >= 1 ? sequence : 0;
}

function exactText(value, pattern, max) {
  const text = typeof value === 'string' ? value : '';
  return text && text.length <= max && text === text.trim() && pattern.test(text) ? text : '';
}

function canonicalWorkspacePath(value) {
  try { return workspaceTargetAuthority(value).canonicalPath; } catch { return ''; }
}

function noFollowFileAuthority(file, expected) {
  if (expected.size > BigInt(MAX_MATERIAL_BYTES)) {
    throw new Error('workspace material exceeds the bounded authority size');
  }
  const noFollow = Number(fs.constants.O_NOFOLLOW || 0);
  const descriptor = fs.openSync(file, fs.constants.O_RDONLY | noFollow);
  try {
    const before = fs.fstatSync(descriptor, { bigint: true });
    if (!before.isFile() || before.dev !== expected.dev || before.ino !== expected.ino
      || before.size > BigInt(MAX_MATERIAL_BYTES)) {
      throw new Error('workspace target changed during no-follow authority resolution');
    }
    const bytes = fs.readFileSync(descriptor);
    const after = fs.fstatSync(descriptor, { bigint: true });
    if (after.dev !== before.dev || after.ino !== before.ino
      || after.size !== before.size || after.mtimeNs !== before.mtimeNs
      || after.ctimeNs !== before.ctimeNs
      || BigInt(bytes.length) !== after.size) {
      throw new Error('workspace target changed during no-follow snapshot');
    }
    return { bytes: bytes.length, sha256: hashBytes(bytes) };
  } finally { fs.closeSync(descriptor); }
}

function configuredWorkspaceRoot() {
  const configuredValue = String(config.workspaceDir || '');
  const configuredRoot = path.resolve(configuredValue);
  const pinned = configuredValue === configuredRoot
    ? /^\/proc\/self\/fd\/([1-9][0-9]*)$/u.exec(configuredRoot) : null;
  if (!pinned) {
    const rootStat = fs.lstatSync(configuredRoot, { bigint: true });
    if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) {
      throw new Error('workspace root lacks no-follow authority');
    }
    return Object.freeze({
      accessRoot: configuredRoot, realRoot: fs.realpathSync(configuredRoot), pinned: false,
    });
  }
  const descriptor = Number(pinned[1]);
  if (!Number.isSafeInteger(descriptor) || descriptor < 3 || descriptor > 1_048_575
    || String(descriptor) !== pinned[1]) {
    throw new Error('workspace pinned descriptor is not canonical');
  }
  requireConfiguredDataRoot();
  const linkStat = fs.lstatSync(configuredRoot, { bigint: true });
  const descriptorStat = fs.fstatSync(descriptor, { bigint: true });
  const pathStat = fs.statSync(configuredRoot, { bigint: true });
  if (!linkStat.isSymbolicLink() || !descriptorStat.isDirectory()
    || !pathStat.isDirectory() || descriptorStat.dev !== pathStat.dev
    || descriptorStat.ino !== pathStat.ino) {
    throw new Error('workspace pinned descriptor lost exact directory authority');
  }
  const root = fs.realpathSync(configuredRoot);
  const realStat = fs.statSync(root, { bigint: true });
  if (!realStat.isDirectory() || realStat.dev !== descriptorStat.dev
    || realStat.ino !== descriptorStat.ino) {
    throw new Error('workspace pinned descriptor realpath changed identity');
  }
  return Object.freeze({ accessRoot: configuredRoot, realRoot: root, pinned: true });
}

function workspaceTargetAuthority(value) {
  const supplied = typeof value === 'string' ? value.trim() : '';
  if (!supplied || supplied.length > 500 || supplied.includes('\0')
    || supplied.includes('\\')) throw new TypeError('invalid workspace target');
  const rootAuthority = configuredWorkspaceRoot();
  const root = rootAuthority.realRoot;
  const portable = supplied.replace(/^\.\//u, '');
  const relativeInput = path.isAbsolute(supplied)
    ? path.relative(
      rootAuthority.pinned ? rootAuthority.accessRoot : root,
      path.resolve(supplied),
    )
    : portable === 'data/workspace' ? ''
      : portable.startsWith('data/workspace/')
        ? portable.slice('data/workspace/'.length) : portable;
  const relative = path.normalize(relativeInput);
  const absolute = path.resolve(root, relative);
  if (!relative || relative === '.' || relative.startsWith(`..${path.sep}`)
    || path.isAbsolute(relative) || absolute === root
    || !absolute.startsWith(`${root}${path.sep}`)) {
    throw new TypeError('workspace target escapes the physical root');
  }
  const components = [];
  let current = root;
  const segments = relative.split(path.sep).filter(Boolean);
  for (let index = 0; index < segments.length; index += 1) {
    current = path.join(current, segments[index]);
    let stat;
    try { stat = fs.lstatSync(current, { bigint: true }); } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
      components.push({ name: segments[index], kind: 'absent' });
      for (const remainder of segments.slice(index + 1)) {
        components.push({ name: remainder, kind: 'absent' });
      }
      break;
    }
    if (stat.isSymbolicLink()) throw new Error('workspace target contains a symlink');
    const final = index === segments.length - 1;
    if ((!final && !stat.isDirectory())
      || (final && !stat.isDirectory() && !stat.isFile())) {
      throw new Error('workspace target has an unsupported physical component');
    }
    if (final && stat.isDirectory()) throw new Error('workspace write target is a directory');
    const component = {
      name: segments[index], kind: stat.isDirectory() ? 'directory' : 'file',
      device: String(stat.dev), inode: String(stat.ino),
    };
    components.push(final && stat.isFile()
      ? { ...component, ...noFollowFileAuthority(current, stat) } : component);
  }
  const canonicalPath = `data/workspace/${relative.replaceAll(path.sep, '/')}`;
  const identity = {
    contract: 'WorkspacePhysicalTargetIdentity/v1', root, relative,
  };
  const authority = {
    contract: 'WorkspaceNoFollowTargetAuthority/v1',
    physicalTargetId: hashJson(identity), canonicalPath,
    components,
  };
  return Object.freeze({
    ...authority, absolutePath: absolute,
    authoritySha256: hashJson(authority),
  });
}

function finiteJsonTree(value, depth = 0) {
  if (depth > 16) return false;
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (Array.isArray(value)) {
    return value.length <= 512 && value.every((item) => finiteJsonTree(item, depth + 1));
  }
  if (!value || typeof value !== 'object'
    || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) return false;
  const keys = Object.keys(value);
  return keys.length <= 512 && keys.every((key) => value[key] !== undefined
    && finiteJsonTree(value[key], depth + 1));
}

function canonicalJsonObject(value, maxBytes = MAX_OBLIGATION_JSON_BYTES) {
  if (!finiteJsonTree(value) || !value || Array.isArray(value)) return null;
  let serialized;
  try { serialized = canonicalJson(value); } catch { return null; }
  if (Buffer.byteLength(serialized, 'utf8') > maxBytes) return null;
  try { return JSON.parse(serialized); } catch { return null; }
}

function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value)) deepFreeze(child);
  }
  return value;
}

const OBLIGATION_FIELDS = Object.freeze([
  'contract', 'sha256', 'required', 'understandingSha256',
  'requestSourceTextSha256', 'selectedRequirementIds',
  'deferredRequirementIds', 'discoveryRequirementIds', 'selectedWrites',
  'selectedPlans', 'activeConditions', 'sentinelRequired',
  'sentinelTargetPaths', 'sentinelTargetSetComplete', 'sentinelTargetsMatch',
  'universalScope', 'universalScopeAuthority', 'scopeUnknown',
  'invalidTargetSet',
]);

function exactRequirementIds(value, { allowEmpty = true } = {}) {
  if (!Array.isArray(value) || value.length > 12 || (!allowEmpty && !value.length)) {
    return null;
  }
  const normalized = value.map((item) => String(item || ''));
  return normalized.every((item) => REQUIREMENT_ID.test(item))
    && new Set(normalized).size === normalized.length ? normalized : null;
}

function exactCondition(condition, index, selectedIds) {
  if (!exactKeys(condition, [
    'index', 'role', 'requirementIds', 'afterRequirementIds', 'statementSha256',
  ]) || Number(condition.index) !== index
    || !['action', 'completion'].includes(condition.role)
    || !validSha(condition.statementSha256)) return false;
  const requirementIds = exactRequirementIds(condition.requirementIds, { allowEmpty: false });
  const afterRequirementIds = exactRequirementIds(condition.afterRequirementIds);
  return Boolean(requirementIds && afterRequirementIds
    && requirementIds.every((id) => selectedIds.has(id))
    && afterRequirementIds.every((id) => selectedIds.has(id)));
}

function exactPlanText(value, max = 160) {
  return exactText(value, /^[^\s\0]+$/u, max);
}

function exactSelectedPlans(plans, selectedRequirementIds, writes, discoveryIds) {
  if (!Array.isArray(plans) || plans.length !== selectedRequirementIds.length) return false;
  const writeByRequirement = new Map(writes.map((write) => [write.requirementId, write]));
  const observedDiscoveryIds = [];
  return plans.every((plan, index) => {
    if (!exactKeys(plan, [
      'requirementId', 'capabilityId', 'operationId', 'effect', 'toolName', 'args',
    ]) || plan.requirementId !== selectedRequirementIds[index]
      || !exactPlanText(plan.capabilityId) || !exactPlanText(plan.operationId)
      || !exactPlanText(plan.effect, 80) || !exactPlanText(plan.toolName)
      || canonicalJsonObject(plan.args, MAX_MATERIAL_BYTES) == null) return false;
    const write = writeByRequirement.get(plan.requirementId);
    const claimsWrite = plan.capabilityId === 'workspace.file.write'
      && plan.operationId === 'write';
    if (write) {
      return claimsWrite && plan.effect === 'workspace'
        && plan.toolName === 'schrijf_bestand'
        && canonicalWorkspacePath(plan.args.pad) === write.canonicalPath
        && typeof plan.args.inhoud === 'string'
        && Buffer.byteLength(plan.args.inhoud, 'utf8') <= MAX_MATERIAL_BYTES;
    }
    if (claimsWrite) return false;
    if (plan.capabilityId === 'workspace.file.list' && plan.operationId === 'list') {
      observedDiscoveryIds.push(plan.requirementId);
    }
    return true;
  }) && canonicalJson(observedDiscoveryIds) === canonicalJson(discoveryIds);
}

function exactObligationSentinel(obligation, targetPaths) {
  const sentinelPaths = Array.isArray(obligation.sentinelTargetPaths)
    ? obligation.sentinelTargetPaths.map(canonicalWorkspacePath) : [];
  if (sentinelPaths.some((item) => !item)
    || new Set(sentinelPaths).size !== sentinelPaths.length) return false;
  if (obligation.sentinelRequired === false) {
    return obligation.sentinelTargetSetComplete === false
      && obligation.sentinelTargetsMatch === false
      && sentinelPaths.length === 0;
  }
  return obligation.sentinelRequired === true
    && obligation.sentinelTargetSetComplete === true
    && obligation.sentinelTargetsMatch === true
    && canonicalJson([...sentinelPaths].sort())
      === canonicalJson([...targetPaths].sort());
}

function exactObligationHash(obligation) {
  const authorityBody = structuredClone(obligation);
  delete authorityBody.sha256;
  delete authorityBody.understandingSha256;
  return hashJson(authorityBody) === obligation.sha256;
}

function normalizedObligation(value, understandingSha256) {
  const obligation = canonicalJsonObject(value);
  if (!obligation || !exactKeys(obligation, OBLIGATION_FIELDS)
    || obligation.contract !== 'MaterialCompletionObligation/v1'
    || obligation.required !== true || !validSha(obligation.sha256)
    || obligation.understandingSha256 !== understandingSha256
    || !['', understandingSha256].includes(obligation.requestSourceTextSha256)
      && !validSha(obligation.requestSourceTextSha256)
    || obligation.scopeUnknown !== false || obligation.invalidTargetSet !== false
    || obligation.universalScope !== false
    || obligation.universalScopeAuthority !== null
    || !Array.isArray(obligation.deferredRequirementIds)
    || obligation.deferredRequirementIds.length !== 0
    || !exactObligationHash(obligation)) return null;
  const selectedRequirementIds = exactRequirementIds(
    obligation.selectedRequirementIds, { allowEmpty: false },
  );
  const discoveryRequirementIds = exactRequirementIds(
    obligation.discoveryRequirementIds,
  );
  const selectedWrites = obligation.selectedWrites;
  if (!selectedRequirementIds || !discoveryRequirementIds
    || !Array.isArray(selectedWrites) || !selectedWrites.length
    || selectedWrites.length > selectedRequirementIds.length) return null;
  const selectedSet = new Set(selectedRequirementIds);
  const exactWrites = selectedWrites.every((write) => (
    exactKeys(write, ['requirementId', 'capabilityId', 'operationId', 'canonicalPath'])
      && selectedSet.has(write.requirementId)
      && write.capabilityId === 'workspace.file.write'
      && write.operationId === 'write'
      && canonicalWorkspacePath(write.canonicalPath) === write.canonicalPath
  ));
  const targetPaths = selectedWrites.map((write) => write.canonicalPath);
  const targetIds = selectedWrites.map((write) => write.requirementId);
  if (!exactWrites || new Set(targetIds).size !== targetIds.length
    || new Set(targetPaths).size !== targetPaths.length
    || canonicalJson(targetIds) !== canonicalJson(
      selectedRequirementIds.filter((id) => targetIds.includes(id)),
    )
    || !exactSelectedPlans(
      obligation.selectedPlans, selectedRequirementIds,
      selectedWrites, discoveryRequirementIds,
    )
    || !Array.isArray(obligation.activeConditions)
    || obligation.activeConditions.length > 12
    || !obligation.activeConditions.every((condition, index) => (
      exactCondition(condition, index, selectedSet)
    ))
    || !exactObligationSentinel(obligation, targetPaths)) return null;
  return deepFreeze(obligation);
}

function normalizedContractAttestation(value) {
  const fields = [
    'contract', 'handoffKey', 'rawBodySha256', 'projection', 'workItemId',
    'episodeId', 'sourceTurnId', 'authorityTimelineId', 'understandingSha256',
    'semanticAuthoritySha256', 'planSeal', 'obligation',
  ];
  if (!exactKeys(value, fields)
    || value.contract !== 'MaterialExecutionContractAuthority/v1'
    || !HANDOFF_KEY.test(String(value.handoffKey || ''))
    || !validSha(value.rawBodySha256)
    || !exactKeys(value.projection, ['rule', 'sha256'])
    || !PROJECTION_RULE.test(String(value.projection.rule || ''))
    || !validSha(value.projection.sha256)) return null;
  const workItemId = positiveId(value.workItemId);
  const episodeId = positiveId(value.episodeId);
  const authorityTimelineId = positiveId(value.authorityTimelineId);
  const sourceTurnId = exactText(value.sourceTurnId, /^[^\s\0]+$/u, 180);
  const understandingSha256 = validSha(value.understandingSha256);
  const semanticAuthoritySha256 = validSha(value.semanticAuthoritySha256);
  const planSeal = normalizedProductionPlanSeal(
    value.planSeal, understandingSha256,
  );
  const obligation = normalizedObligation(value.obligation, understandingSha256);
  if (!workItemId || !episodeId || !authorityTimelineId || !sourceTurnId
    || !understandingSha256 || !semanticAuthoritySha256 || !planSeal
    || !obligation || canonicalJson(planSeal.plans.map((plan) => ({
      requirementId: plan.requirementId,
      capabilityId: plan.capabilityId,
      operationId: plan.operationId,
      effect: plan.effect,
      toolName: plan.toolName,
      args: plan.args,
    }))) !== canonicalJson(obligation.selectedPlans)) return null;
  return deepFreeze({
    contract: value.contract, handoffKey: String(value.handoffKey),
    rawBodySha256: String(value.rawBodySha256),
    projection: {
      rule: String(value.projection.rule), sha256: String(value.projection.sha256),
    },
    workItemId, episodeId, sourceTurnId, authorityTimelineId,
    understandingSha256, semanticAuthoritySha256,
    planSeal, planSha256: planSeal.sha256, obligation,
  });
}

function normalizedProductionPlanSeal(value, understandingSha256) {
  const seal = canonicalJsonObject(value, MAX_OBLIGATION_JSON_BYTES);
  if (!seal || !exactKeys(seal, ['contract', 'decision', 'plans', 'sha256'])
    || seal.contract !== 'AgentInterpretationRetryPlanProjection/v1'
    || !exactKeys(seal.decision, ['kind', 'requirementIds', 'replyDraft'])
    || !['act', 'inspect'].includes(String(seal.decision.kind || ''))
    || seal.decision.replyDraft !== null
    || !validSha(seal.sha256) || !Array.isArray(seal.plans)
    || seal.plans.length < 1 || seal.plans.length > 12) return null;
  const valid = seal.plans.every((plan) => exactKeys(plan, [
    'requirementId', 'capabilityId', 'operationId', 'toolName', 'args', 'effect',
    'idempotency', 'proofFloor', 'supportsDeferred', 'execution',
    'proofOperations', 'recoveryOperations', 'semanticBinding',
  ]) && REQUIREMENT_ID.test(String(plan.requirementId || ''))
    && exactPlanText(plan.capabilityId) && exactPlanText(plan.operationId)
    && exactPlanText(plan.toolName) && exactPlanText(plan.effect, 80)
    && canonicalJsonObject(plan.args, MAX_MATERIAL_BYTES) != null
    && canonicalJsonObject(plan.execution, MAX_OBLIGATION_JSON_BYTES) != null
    && exactKeys(plan.semanticBinding, [
      'understandingSha256', 'requirementId', 'addressesRequirementIds',
      'basisReceiptIds', 'capabilityId', 'operationId', 'role', 'executionType',
    ])
    && plan.semanticBinding.understandingSha256 === understandingSha256
    && plan.semanticBinding.requirementId === plan.requirementId
    && canonicalJson(plan.semanticBinding.addressesRequirementIds)
      === canonicalJson([plan.requirementId])
    && plan.semanticBinding.capabilityId === plan.capabilityId
    && plan.semanticBinding.operationId === plan.operationId
    && exactPlanText(plan.semanticBinding.role, 80)
    && Array.isArray(plan.semanticBinding.basisReceiptIds)
    && plan.semanticBinding.executionType === null);
  if (!valid || new Set(seal.plans.map((plan) => plan.requirementId)).size
      !== seal.plans.length
    || canonicalJson(seal.decision.requirementIds)
      !== canonicalJson(seal.plans.map((plan) => plan.requirementId))) return null;
  try {
    const reissued = createWorkCompletionPlanSeal({
      decision: seal.decision, plans: seal.plans,
    });
    if (canonicalJson(reissued) !== canonicalJson(seal)) return null;
  } catch { return null; }
  return deepFreeze(seal);
}

function handoffRegistryProjection(row) {
  if (!row) return null;
  const fields = [
    'handoff_key', 'contract', 'origin_channel', 'origin_conversation_id',
    'origin_message_id', 'raw_original_text_sha256', 'original_text_sha256',
    'requester_sha256', 'supersession_scope_sha256',
    'supersession_token_sha256', 'work_item_id', 'episode_id',
    'binding_sha256', 'created_ts',
  ];
  if (fields.some((field) => !Object.hasOwn(row, field))) return null;
  return Object.fromEntries(fields.map((field) => [field, row[field]]));
}

function validInterpretationHandoff(attestation) {
  if (interpretationRegistrySchemaFailure()) return null;
  const row = lifecycleDb.prepare(`SELECT * FROM main.${HANDOFF_TABLE}
    WHERE handoff_key=?`).get(attestation.handoffKey);
  const work = row ? lifecycleDb.prepare('SELECT id FROM main.eva_work_items WHERE id=?')
    .get(row.work_item_id) : null;
  const episode = row ? lifecycleDb.prepare(`SELECT id,work_item_id,origin_kind,
    origin_channel,origin_conversation_id,origin_message_id
    FROM main.eva_goal_episodes WHERE id=?`).get(row.episode_id) : null;
  const expectedHandoffKey = row ? hashJson({
    contract: row.contract, channel: row.origin_channel,
    conversationId: row.origin_conversation_id, messageId: row.origin_message_id,
    rawOriginalTextSha256: row.raw_original_text_sha256,
    originalTextSha256: row.original_text_sha256,
  }) : '';
  const expectedBindingSha256 = row ? hashJson({
    handoffKey: row.handoff_key,
    rawOriginalTextSha256: row.raw_original_text_sha256,
    originalTextSha256: row.original_text_sha256,
    requesterSha256: row.requester_sha256,
    supersessionScopeSha256: row.supersession_scope_sha256,
    supersessionTokenSha256: row.supersession_token_sha256,
    workItemId: Number(row.work_item_id), episodeId: Number(row.episode_id),
  }) : '';
  if (!row || row.contract !== 'ChatInterpretationRetry/v1'
    || row.handoff_key !== expectedHandoffKey
    || row.binding_sha256 !== expectedBindingSha256
    || row.raw_original_text_sha256 !== attestation.rawBodySha256
    || attestation.projection?.rule !== RETRY_SOURCE_PROJECTION_RULE
    || attestation.obligation?.requestSourceTextSha256
      !== row.raw_original_text_sha256
    || Number(row.work_item_id) !== attestation.workItemId
    || Number(row.episode_id) !== attestation.episodeId
    || !work || !episode
    || Number(episode.work_item_id) !== Number(work.id)
    || episode.origin_kind !== 'user'
    || episode.origin_channel !== row.origin_channel
    || episode.origin_conversation_id !== row.origin_conversation_id
    || episode.origin_message_id !== row.origin_message_id) return null;
  const projection = handoffRegistryProjection(row);
  return projection ? Object.freeze({
    bindingSha256: row.binding_sha256,
    registrySha256: hashJson(projection),
    registry: deepFreeze(projection),
    rawOriginalTextSha256: row.raw_original_text_sha256,
    originalTextSha256: row.original_text_sha256,
  }) : null;
}

function exactIngressAuthority(row) {
  const channel = String(row.origin_channel || '');
  const conversationId = String(row.origin_conversation_id || '');
  const messageId = String(row.origin_message_id || '');
  if (!['telegram', 'voice', 'web'].includes(channel)
    || !conversationId || !messageId) return null;
  const executionKey = `turn-understanding:${hashJson({
    channel, conversationId, messageId,
  })}`;
  let execution;
  try {
    execution = validateExecutionRecord(lifecycleDb.prepare(
      'SELECT * FROM main.turn_tool_executions WHERE execution_key=?',
    ).get(executionKey));
  } catch { return null; }
  if (!turnUnderstandingAuthorityRecord(lifecycleDb, execution)) return null;
  let args;
  let output;
  try {
    args = parseCanonicalExecutionField(execution.args, 'ingress args');
    output = parseCanonicalExecutionField(execution.output, 'ingress output');
  } catch {
    return null;
  }
  const scope = String(args?.supersessionScope || '');
  const token = String(args?.supersessionToken || '');
  const expectedArgs = [
    'channel', 'conversationId', 'messageId', 'requester', 'originalText',
    'sessionRef', 'supersessionScope', 'supersessionToken', 'syntheticTest',
  ];
  if (execution.tool !== '@turn/understanding' || execution.status !== 'done'
    || execution.turn_id !== `ingress:${channel}:${conversationId}:${messageId}`
    || !exactKeys(args, expectedArgs)
    || args?.channel !== channel || args?.conversationId !== conversationId
    || String(args?.messageId || '') !== messageId
    || !exactText(args?.sessionRef, /^[^\s\0]+$/u, 240)
    || hashBytes(String(args?.requester || '')) !== row.requester_sha256
    || hashBytes(String(args?.originalText || ''))
      !== row.raw_original_text_sha256
    || hashBytes(scope) !== row.supersession_scope_sha256
    || hashBytes(token) !== row.supersession_token_sha256
    || args.syntheticTest !== false
    || (output?.durableHandoff !== true
      && output?.metadata?.durableHandoff !== true)
    || Number(output.workItemId) !== Number(row.work_item_id)
    || Number(output.goalEpisodeId) !== Number(row.episode_id)) return null;
  const projection = {
    contract: 'MaterialIngressSource/v1', kind: 'turn_tool_execution',
    executionKey, intentRecordSha256: execution.intent_record_sha256,
    completionRecordSha256: execution.completion_record_sha256,
    sessionRef: args.sessionRef,
    channel, conversationId, messageId, requesterSha256: row.requester_sha256,
    redactedSourceSha256: row.original_text_sha256,
    rawSourceSha256: row.raw_original_text_sha256,
  };
  return deepFreeze({ ...projection, sha256: hashJson(projection) });
}

function exactUnderstandingActionShape(action) {
  return exactKeys(action, [
    'requirementId', 'capabilityId', 'operationId', 'polarity', 'effect',
    'authorization', 'sourceSpans', 'boundaryReasons',
  ]) && REQUIREMENT_ID.test(String(action.requirementId || ''))
    && exactPlanText(action.capabilityId) && exactPlanText(action.operationId)
    && exactPlanText(action.effect, 80)
    && ['request', 'deny', 'mention'].includes(action.polarity)
    && ['allowed', 'denied'].includes(action.authorization)
    && Array.isArray(action.sourceSpans) && action.sourceSpans.length > 0
    && action.sourceSpans.every((span) => exactKeys(span, ['start', 'end'])
      && Number.isSafeInteger(span.start) && Number.isSafeInteger(span.end)
      && span.start >= 0 && span.end > span.start)
    && Array.isArray(action.boundaryReasons)
    && action.boundaryReasons.length <= 12
    && action.boundaryReasons.every((reason) => (
      typeof reason === 'string' && reason.length > 0 && reason.length <= 120
    ))
    && (action.boundaryReasons.length === 0)
      === (action.authorization === 'allowed');
}

function understandingActionSets(understanding, obligation, planSeal) {
  const selectedPlans = planSeal?.plans;
  const actions = understanding?.actions;
  if (!Array.isArray(selectedPlans) || !Array.isArray(actions)
    || actions.length < selectedPlans.length || actions.length > 12
    || actions.some((action) => !exactUnderstandingActionShape(action))
    || new Set(actions.map((action) => action.requirementId)).size !== actions.length) return null;
  const byRequirement = new Map(actions.map((action) => [action.requirementId, action]));
  const executableIds = actions.filter((action) => (
    action.polarity === 'request' && action.authorization === 'allowed'
  )).map((action) => action.requirementId);
  if (canonicalJson(executableIds) !== canonicalJson(
    selectedPlans.map((plan) => plan.requirementId),
  )) return null;
  const selected = selectedPlans.map((plan) => byRequirement.get(plan.requirementId));
  if (selected.some((action, index) => {
    const plan = selectedPlans[index];
    if (!action) return true;
    return action.capabilityId !== plan.capabilityId
      || action.operationId !== plan.operationId
      || action.effect !== plan.effect
      || action.polarity !== 'request'
      || action.authorization !== 'allowed'
      || action.boundaryReasons.length !== 0;
  })) return null;
  const selectedIds = new Set(selectedPlans.map((plan) => plan.requirementId));
  const excluded = actions.filter((action) => !selectedIds.has(action.requirementId));
  return Object.freeze({ selected, excluded: deepFreeze(structuredClone(excluded)) });
}

function exactProjectedDecision(decisionProjection, actions, selectedIds) {
  if (!exactKeys(decisionProjection, [
    'proposedKind', 'proposedRequirementIds', 'acceptedRequirementIds',
    'rejectedRequirementIds', 'unknownRequirementIds', 'hostAddedRequirementIds',
  ]) || ![
    'reply', 'act', 'inspect', 'schedule', 'clarify', 'cancel', 'silent',
    'fail_closed', 'unknown',
  ].includes(decisionProjection.proposedKind)) return false;
  const proposed = decisionProjection.proposedRequirementIds;
  if (!Array.isArray(proposed) || proposed.length > 12
    || proposed.some((id) => typeof id !== 'string' || !id || id.length > 120)
    || new Set(proposed).size !== proposed.length) return false;
  const actionIds = new Set(actions.map((action) => action.requirementId));
  const accepted = selectedIds.filter((id) => proposed.includes(id));
  const expected = {
    acceptedRequirementIds: accepted,
    rejectedRequirementIds: proposed.filter((id) => (
      actionIds.has(id) && !selectedIds.includes(id)
    )),
    unknownRequirementIds: proposed.filter((id) => !actionIds.has(id)),
    hostAddedRequirementIds: selectedIds.filter((id) => !accepted.includes(id)),
  };
  return Object.entries(expected).every(([field, ids]) => (
    canonicalJson(decisionProjection[field]) === canonicalJson(ids)
  ));
}

function projectedActiveConditions(understanding) {
  if (!Array.isArray(understanding?.conditions)) return null;
  if (understanding.conditions.some((condition) => !exactKeys(condition, [
    'statement', 'role', 'state', 'requirementIds', 'afterRequirementIds',
  ]))) return null;
  return understanding.conditions
    .filter((condition) => ['active', 'required', 'unmet'].includes(condition.state)
      && ['action', 'completion'].includes(condition.role))
    .map((condition, index) => ({
      index, role: condition.role,
      requirementIds: Array.isArray(condition.requirementIds)
        ? [...new Set(condition.requirementIds.map(String))] : null,
      afterRequirementIds: Array.isArray(condition.afterRequirementIds)
        ? [...new Set(condition.afterRequirementIds.map(String))] : null,
      statementSha256: hashBytes(String(condition.statement || '')),
    }));
}

function exactUnderstandingObligation(understanding, obligation, planSeal) {
  const selectedIds = obligation?.selectedRequirementIds;
  const decision = understanding?.firstDecision;
  const activeConditions = projectedActiveConditions(understanding);
  const actionSets = understandingActionSets(understanding, obligation, planSeal);
  const decisionProjection = understanding?.decisionProjection;
  return understanding?.sourceKind === 'model'
    && ['now', 'none'].includes(String(understanding?.temporal?.mode || ''))
    && exactKeys(decision, ['kind', 'requirementIds', 'replyDraftSha256'])
    && ['act', 'inspect'].includes(decision.kind)
    && decision.kind === planSeal?.decision?.kind
    && decision.replyDraftSha256 === null
    && planSeal?.decision?.replyDraft === null
    && canonicalJson(decision.requirementIds)
      === canonicalJson(planSeal?.decision?.requirementIds)
    && canonicalJson(decision.requirementIds) === canonicalJson(selectedIds)
    && canonicalJson(decision.requirementIds)
      === canonicalJson(planSeal?.plans.map((plan) => plan.requirementId))
    && actionSets != null
    && exactProjectedDecision(decisionProjection, understanding.actions, selectedIds)
    && activeConditions != null
    && !activeConditions.some((condition) => (
      condition.requirementIds == null || condition.afterRequirementIds == null
    ))
    && canonicalJson(activeConditions) === canonicalJson(obligation.activeConditions)
    ? actionSets : null;
}

function understandingAuthorityRecord(attestation) {
  let rows;
  try {
    rows = lifecycleDb.prepare(`SELECT id,meta FROM main.timeline
      WHERE kind='turn_understanding' AND json_valid(meta)
        AND json_extract(meta,'$.turnId')=? ORDER BY id LIMIT 2`)
      .all(attestation.sourceTurnId);
  } catch { return null; }
  if (rows.length !== 1 || Number(rows[0].id) !== attestation.authorityTimelineId) {
    return null;
  }
  let meta;
  try { meta = JSON.parse(rows[0].meta); } catch { return null; }
  const understanding = meta?.understanding;
  const registry = lifecycleDb.prepare(`SELECT origin_conversation_id,
    origin_message_id,requester_sha256,raw_original_text_sha256,
    original_text_sha256 FROM main.${HANDOFF_TABLE} WHERE handoff_key=?`)
    .get(attestation.handoffKey);
  const actionSets = exactUnderstandingObligation(
    understanding, attestation.obligation, attestation.planSeal,
  );
  if (meta?.turnId !== attestation.sourceTurnId
    || meta?.interpretationSucceeded !== true
    || meta?.conversationId !== registry?.origin_conversation_id
    || meta?.originMessageId !== registry?.origin_message_id
    || hashBytes(String(meta?.requester || '')) !== registry?.requester_sha256
    || meta?.semanticAuthoritySha256 !== attestation.semanticAuthoritySha256
    || understanding?.contract !== 'TurnUnderstandingRecord/v1'
    || understanding?.sourceSha256 !== attestation.understandingSha256
    || understanding?.sourceTextSha256 !== attestation.projection?.sha256
    || attestation.projection?.rule !== RETRY_SOURCE_PROJECTION_RULE
    || attestation.rawBodySha256 !== registry?.raw_original_text_sha256
    || attestation.obligation?.requestSourceTextSha256
      !== registry?.raw_original_text_sha256
    || !actionSets
    || !validSha(understanding?.sha256)) return null;
  const { sha256: recordSha256, ...body } = understanding;
  return hashJson(body) === recordSha256
    ? Object.freeze({
      recordSha256, understanding,
      excludedActions: actionSets.excluded,
      excludedActionsSha256: hashJson(actionSets.excluded),
    }) : null;
}

function validUnderstandingAuthority(attestation) {
  return understandingAuthorityRecord(attestation) != null;
}

function parsedWorkState(work) {
  if (typeof work?.state_json !== 'string'
    || work.state_json.length > WORK_STATE_JSON_LIMIT) return null;
  try {
    const state = JSON.parse(work.state_json);
    return state && typeof state === 'object' && !Array.isArray(state) ? state : null;
  } catch { return null; }
}

function exactActiveRetry(attestation, row, work, episode) {
  const state = parsedWorkState(work);
  const retry = state?.interpretation_retry;
  const understanding = understandingAuthorityRecord(attestation);
  if (!retry || typeof retry !== 'object' || Array.isArray(retry) || !understanding) {
    return false;
  }
  const createdMs = Date.parse(String(retry.createdAt || ''));
  const deadlineMs = Date.parse(String(retry.deadlineAt || ''));
  const nowMs = Date.now();
  const attempts = Number(retry.attempts);
  const scope = String(retry.supersessionScope || '');
  const token = String(retry.supersessionToken || '');
  const projectedSourceText = typeof retry.originalText === 'string'
    ? retry.originalText.trim() : '';
  const supersessionValid = scope || token
    ? Boolean(scope && token
      && hashBytes(`${scope}\n${token}`) === retry.supersessionSha256
      && hashBytes(scope) === row.supersession_scope_sha256
      && hashBytes(token) === row.supersession_token_sha256)
    : row.supersession_scope_sha256 === hashBytes('')
      && row.supersession_token_sha256 === hashBytes('');
  const originSha256 = hashBytes(JSON.stringify({
    channel: String(episode.origin_channel || ''),
    conversationId: String(episode.origin_conversation_id || ''),
    messageId: String(episode.origin_message_id || ''),
    requester: String(episode.requester || ''),
    deliveryPolicy: String(episode.delivery_policy || ''),
    deliveryTarget: String(episode.delivery_target || ''),
  }));
  return [
    work.status === 'open', episode.state === 'executing',
    work.wait_json == null, work.next_wake_ts == null,
    episode.wake_ts == null, episode.closed_ts == null,
    retry.contract === 'ChatInterpretationRetry/v1',
    retry.handoffKey === row.handoff_key,
    retry.rawOriginalTextSha256 === row.raw_original_text_sha256,
    typeof retry.originalText === 'string',
    hashBytes(retry.originalText) === row.original_text_sha256,
    retry.originalTextSha256 === row.original_text_sha256,
    projectedSourceText.length > 0,
    attestation.projection?.rule === RETRY_SOURCE_PROJECTION_RULE,
    hashBytes(projectedSourceText) === attestation.projection?.sha256,
    understanding.understanding.sourceTextSha256 === attestation.projection?.sha256,
    attestation.rawBodySha256 === row.raw_original_text_sha256,
    attestation.obligation?.requestSourceTextSha256 === row.raw_original_text_sha256,
    understanding.understanding.actions.every((action) => action.sourceSpans.every((span) => (
      span.end <= projectedSourceText.length
        && projectedSourceText.slice(span.start, span.end).trim().length > 0
    ))),
    retry.originSha256 === originSha256,
    Number.isFinite(createdMs), Number.isFinite(deadlineMs),
    deadlineMs === createdMs + INTERPRETATION_RETRY_TTL_MS,
    createdMs <= nowMs, nowMs < deadlineMs,
    Number.isSafeInteger(attempts), attempts >= 0,
    attempts < INTERPRETATION_RETRY_MAX_ATTEMPTS,
    retry.phase === 'understood', retry.planSha256 === attestation.planSeal.sha256,
    retry.understandingSha256 === attestation.understandingSha256,
    retry.materialAuthoritySha256 === attestation.obligation.sha256,
    retry.semanticAuthoritySha256 === attestation.semanticAuthoritySha256,
    supersessionValid,
  ].every(Boolean);
}

function newerTelegramOriginMessage(episode) {
  const match = String(episode.origin_conversation_id || '')
    .match(/^telegram:(.+)$/u);
  const messageId = String(episode.origin_message_id || '').trim();
  if (!match || !/^\d+$/u.test(messageId)) return false;
  const current = lifecycleDb.prepare(`SELECT meta FROM main.telegram_messages
    WHERE chat_id=? AND message_id=?`).get(match[1], messageId);
  let meta = {};
  try { meta = current?.meta ? JSON.parse(current.meta) : {}; } catch { return true; }
  const fromTelegramId = String(meta?.fromTelegramId || '');
  const threadKnown = current != null;
  const messageThreadId = String(meta?.messageThreadId || '');
  return Boolean(lifecycleDb.prepare(`SELECT 1 FROM main.telegram_messages
    WHERE chat_id=? AND role='user' AND deleted=0
      AND message_id GLOB '[0-9]*'
      AND CAST(message_id AS INTEGER)>CAST(? AS INTEGER)
      AND (?='' OR coalesce(json_extract(meta,'$.fromTelegramId'),'')=?)
      AND (?=0 OR coalesce(json_extract(meta,'$.messageThreadId'),'')=?)
    ORDER BY CAST(message_id AS INTEGER) LIMIT 1`).get(
    match[1], messageId, fromTelegramId, fromTelegramId,
    threadKnown ? 1 : 0, messageThreadId,
  ));
}

function latestAuthorizedIngressToken(scope, createdTs) {
  if (!turnUnderstandingAuthorityStoreStatus(lifecycleDb).available) return null;
  const rows = lifecycleDb.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE tool='@turn/understanding' AND json_valid(args)
      AND json_extract(args,'$.supersessionScope')=? AND started_ts>=?
    ORDER BY execution_seq DESC`).all(scope, createdTs);
  for (const row of rows) {
    try {
      if (row.status !== 'done') continue;
      const execution = validateExecutionRecord(row);
      if (!turnUnderstandingAuthorityRecord(lifecycleDb, execution)) continue;
      const args = JSON.parse(execution.args);
      if (args.supersessionScope === scope
        && typeof args.supersessionToken === 'string') return args.supersessionToken;
    } catch { /* Historische/ongeldige @turn-rij verleent geen actualiteitsauthority. */ }
  }
  return null;
}

function activeRetrySuperseded(retry, episode, registryRow) {
  const scope = String(retry.supersessionScope || '');
  const token = String(retry.supersessionToken || '');
  if (scope && token) {
    const authorityToken = latestAuthorizedIngressToken(
      scope, String(registryRow?.created_ts || ''),
    );
    const timelineToken = lifecycleDb.prepare(`SELECT
      json_extract(meta,'$.messageContext.ingressToken') AS token
      FROM main.timeline WHERE kind='chat_in'
        AND json_extract(meta,'$.messageContext.ingressScope')=?
      ORDER BY id DESC LIMIT 1`).get(scope)?.token;
    const latest = authorityToken || timelineToken || null;
    if (latest && latest !== token) return true;
  }
  if (episode.origin_channel === 'telegram') return newerTelegramOriginMessage(episode);
  const messageId = String(episode.origin_message_id || '');
  if (!/^\d+$/u.test(messageId) || !episode.origin_conversation_id) return false;
  return Boolean(lifecycleDb.prepare(`SELECT 1 FROM main.messages
    WHERE conversation_id=? AND role='user' AND id>? ORDER BY id LIMIT 1`)
  .get(episode.origin_conversation_id, Number(messageId)));
}

function activeInterpretationHandoff(attestation) {
  const historical = validInterpretationHandoff(attestation);
  if (!historical) return null;
  try {
    const row = lifecycleDb.prepare(`SELECT * FROM main.${HANDOFF_TABLE}
      WHERE handoff_key=?`).get(attestation.handoffKey);
    const work = lifecycleDb.prepare('SELECT * FROM main.eva_work_items WHERE id=?')
      .get(attestation.workItemId);
    const episode = lifecycleDb.prepare('SELECT * FROM main.eva_goal_episodes WHERE id=?')
      .get(attestation.episodeId);
    const origins = lifecycleDb.prepare(`SELECT * FROM main.eva_episode_origins
      WHERE episode_id=? AND origin_kind='user'
        AND coalesce(origin_channel,'')=?
        AND coalesce(origin_conversation_id,'')=?
        AND coalesce(origin_message_id,'')=? ORDER BY id`).all(
      attestation.episodeId, row.origin_channel,
      row.origin_conversation_id, row.origin_message_id,
    );
    const origin = origins.length === 1 ? origins[0] : null;
    const retry = parsedWorkState(work)?.interpretation_retry;
    const ingress = exactIngressAuthority(row);
    const understanding = understandingAuthorityRecord(attestation);
    const exactOrigin = [
      origin, work, episode, ingress, understanding,
      Number(episode?.work_item_id) === Number(work?.id),
      String(origin?.requester || '') === String(episode?.requester || ''),
      hashBytes(String(origin?.requester || '')) === row.requester_sha256,
      work?.source === row.origin_channel,
      hashBytes(String(work?.owner || '')) === row.requester_sha256,
    ].every(Boolean);
    if (!exactOrigin || !exactActiveRetry(attestation, row, work, episode)
      || activeRetrySuperseded(retry, episode, row)) return null;
    const core = deepFreeze({
      contract: 'MaterialExecutionAuthorityCore/v1',
      registrySha256: historical.registrySha256,
      handoffBindingSha256: historical.bindingSha256,
      work: {
        id: Number(work.id), revision: Number(work.revision), status: work.status,
        stateSha256: hashBytes(String(work.state_json || '')),
        waitSha256: hashBytes(String(work.wait_json || '')),
        source: work.source, ownerSha256: hashBytes(String(work.owner || '')),
      },
      episode: {
        id: Number(episode.id), workItemId: Number(episode.work_item_id),
        revision: Number(episode.revision), state: episode.state,
        originChannel: episode.origin_channel,
        originConversationId: episode.origin_conversation_id,
        originMessageId: episode.origin_message_id,
        requesterSha256: hashBytes(String(episode.requester || '')),
      },
      origin: {
        id: Number(origin.id), episodeId: Number(origin.episode_id),
        originKey: origin.origin_key, kind: origin.origin_kind,
        channel: origin.origin_channel, conversationId: origin.origin_conversation_id,
        messageId: origin.origin_message_id,
        requesterSha256: hashBytes(String(origin.requester || '')),
      },
      ingress,
      source: {
        timelineId: attestation.authorityTimelineId,
        turnId: attestation.sourceTurnId,
        conversationId: row.origin_conversation_id,
        messageId: row.origin_message_id,
        recordSha256: understanding.recordSha256,
        semanticAuthoritySha256: attestation.semanticAuthoritySha256,
      },
      retry: {
        planSha256: attestation.planSeal.sha256,
        obligationSha256: attestation.obligation.sha256,
        semanticAuthoritySha256: attestation.semanticAuthoritySha256,
        supersessionScopeSha256: row.supersession_scope_sha256,
        supersessionTokenSha256: row.supersession_token_sha256,
      },
    });
    return Object.freeze({
      ...historical, authorityCore: core, authorityCoreSha256: hashJson(core),
      excludedActions: understanding.excludedActions,
      excludedActionsSha256: understanding.excludedActionsSha256,
    });
  } catch { return null; }
}

function verifiedAuthorityCandidate(context, expectedContract) {
  let candidate;
  try { candidate = AUTHORITY_VERIFIERS[0]?.(context) || null; } catch { return null; }
  if (!candidate) return null;
  if (candidate.contract === 'MaterialExecutionContractAuthority/v1') {
    const normalized = normalizedContractAttestation(candidate);
    if (!normalized) throw new TypeError('invalid material execution contract host attestation');
    if (expectedContract !== candidate.contract) return null;
    return normalized;
  }
  if (candidate.contract === 'MaterialEffectIntentAuthority/v1') {
    if (!canonicalWorkspacePath(candidate.canonicalPath)) {
      throw new TypeError('material effect intent requires a canonical data/workspace path');
    }
    const normalized = normalizedIntentAttestation(candidate);
    if (!normalized) throw new TypeError('invalid material effect intent host attestation');
    if (expectedContract !== candidate.contract) return null;
    return normalized;
  }
  if (candidate.contract === 'MaterialEffectWriteClaimAuthority/v1') {
    if (!exactKeys(candidate, ['contract', 'intentRef', 'generation', 'physicalPath'])
      || !/^material-effect-intent:[a-f0-9]{64}$/u.test(
        String(candidate.intentRef || ''),
      ) || ![0, 1].includes(Number(candidate.generation))) {
      throw new TypeError('invalid material effect write claim host attestation');
    }
    const target = workspaceTargetAuthority(candidate.physicalPath);
    const normalized = deepFreeze({
      contract: candidate.contract, intentRef: String(candidate.intentRef),
      generation: Number(candidate.generation),
      physicalPath: String(candidate.physicalPath),
      physicalTargetId: target.physicalTargetId,
    });
    if (expectedContract !== candidate.contract) return null;
    return normalized;
  }
  throw new TypeError('invalid material lifecycle host attestation');
}

export function issueMaterialExecutionContractCapability(hostContext) {
  requireStore();
  const attestation = verifiedAuthorityCandidate(
    hostContext, 'MaterialExecutionContractAuthority/v1',
  );
  requireStore();
  const handoff = attestation && activeInterpretationHandoff(attestation);
  if (!attestation || !handoff || !validUnderstandingAuthority(attestation)) return null;
  const capability = Object.freeze({ contract: 'MaterialExecutionContractCapability/v1' });
  CONTRACT_CAPABILITIES.set(capability, deepFreeze({ ...attestation,
    handoffBindingSha256: handoff.bindingSha256,
    handoffRegistrySha256: handoff.registrySha256,
    authorityCore: handoff.authorityCore,
    authorityCoreSha256: handoff.authorityCoreSha256,
    excludedActions: handoff.excludedActions,
    excludedActionsSha256: handoff.excludedActionsSha256,
  }));
  return capability;
}

function consumeCapability(registry, capability, expectedContract) {
  const value = capability && typeof capability === 'object'
    ? registry.get(capability) : null;
  if (!value || capability.contract !== expectedContract) {
    throw new Error(`operation requires an exact one-shot capability (${expectedContract})`);
  }
  registry.delete(capability);
  return value;
}

function contractBody(attestation) {
  const authoritySha256 = hashJson(attestation);
  const obligationSha256 = hashJson(attestation.obligation);
  const executionTurnId = executionTurnIdentity(attestation, obligationSha256);
  return deepFreeze({
    contract: 'MaterialExecutionContract/v1',
    handoffKey: attestation.handoffKey,
    rawBodySha256: attestation.rawBodySha256,
    projection: attestation.projection,
    workItemId: attestation.workItemId,
    episodeId: attestation.episodeId,
    sourceTurnId: attestation.sourceTurnId,
    authorityTimelineId: attestation.authorityTimelineId,
    understandingSha256: attestation.understandingSha256,
    semanticAuthoritySha256: attestation.semanticAuthoritySha256,
    planSha256: attestation.planSha256,
    planSeal: attestation.planSeal,
    excludedActions: attestation.excludedActions,
    excludedActionsSha256: attestation.excludedActionsSha256,
    obligation: attestation.obligation,
    obligationSha256,
    executionTurnId,
    authoritySha256,
    handoffBindingSha256: attestation.handoffBindingSha256,
    handoffRegistrySha256: attestation.handoffRegistrySha256,
    authorityCore: attestation.authorityCore,
    authorityCoreSha256: attestation.authorityCoreSha256,
  });
}

function executionTurnIdentity(attestation, obligationSha256) {
  return `material-execution:${hashJson({
    contract: 'MaterialExecutionTurnIdentity/v1',
    handoffKey: attestation.handoffKey,
    rawBodySha256: attestation.rawBodySha256,
    projection: attestation.projection,
    workItemId: attestation.workItemId,
    episodeId: attestation.episodeId,
    sourceTurnId: attestation.sourceTurnId,
    authorityTimelineId: attestation.authorityTimelineId,
    understandingSha256: attestation.understandingSha256,
    semanticAuthoritySha256: attestation.semanticAuthoritySha256,
    planSha256: attestation.planSha256,
    planSealSha256: attestation.planSeal.sha256,
    excludedActionsSha256: attestation.excludedActionsSha256,
    obligationSha256,
    authorityCoreSha256: attestation.authorityCoreSha256,
  })}`;
}

function parseCanonical(value, label) {
  let parsed;
  try { parsed = JSON.parse(String(value)); } catch {
    throw new Error(`${label} failed integrity verification`);
  }
  if (canonicalJson(parsed) !== String(value)) {
    throw new Error(`${label} failed integrity verification`);
  }
  return parsed;
}

function contractReceipt(row) {
  if (!row) return null;
  const body = parseCanonical(row.contract_json, 'material execution contract');
  const obligation = parseCanonical(row.obligation_json, 'material execution obligation');
  const planSeal = parseCanonical(row.plan_seal_json, 'material production plan seal');
  const excludedActions = parseCanonical(
    row.excluded_actions_json, 'material excluded actions',
  );
  const authorityCore = parseCanonical(
    row.authority_core_json, 'material authority core',
  );
  const contractSha256 = hashJson(body);
  const expected = {
    contract: row.contract, handoffKey: row.handoff_key,
    rawBodySha256: row.raw_body_sha256,
    projection: { rule: row.projection_rule, sha256: row.projection_sha256 },
    workItemId: Number(row.work_item_id), episodeId: Number(row.episode_id),
    sourceTurnId: row.source_turn_id,
    authorityTimelineId: Number(row.authority_timeline_id),
    understandingSha256: row.understanding_sha256,
    semanticAuthoritySha256: row.semantic_authority_sha256,
    planSha256: row.plan_sha256, planSeal,
    excludedActions, excludedActionsSha256: row.excluded_actions_sha256,
    obligation, obligationSha256: row.obligation_sha256,
    executionTurnId: row.execution_turn_id, authoritySha256: row.authority_sha256,
    handoffBindingSha256: row.handoff_binding_sha256,
    handoffRegistrySha256: row.handoff_registry_sha256,
    authorityCore, authorityCoreSha256: row.authority_core_sha256,
  };
  const attestation = normalizedContractAttestation({
    contract: 'MaterialExecutionContractAuthority/v1',
    handoffKey: expected.handoffKey, rawBodySha256: expected.rawBodySha256,
    projection: expected.projection, workItemId: expected.workItemId,
    episodeId: expected.episodeId, sourceTurnId: expected.sourceTurnId,
    authorityTimelineId: expected.authorityTimelineId,
    understandingSha256: expected.understandingSha256,
    semanticAuthoritySha256: expected.semanticAuthoritySha256,
    planSeal: expected.planSeal, obligation: expected.obligation,
  });
  const handoff = attestation && validInterpretationHandoff(attestation);
  const boundAttestation = attestation ? {
    ...attestation,
    handoffBindingSha256: expected.handoffBindingSha256,
    handoffRegistrySha256: expected.handoffRegistrySha256,
    authorityCore, authorityCoreSha256: row.authority_core_sha256,
    excludedActions, excludedActionsSha256: row.excluded_actions_sha256,
  } : null;
  if (attestation && !handoff) {
    throw new Error('interpretation handoff failed integrity verification');
  }
  if (canonicalJson(body) !== canonicalJson(expected)
    || row.contract_sha256 !== contractSha256
    || row.contract_ref !== `material-contract:${contractSha256}`
    || row.obligation_sha256 !== hashJson(obligation)
    || row.plan_sha256 !== planSeal.sha256
    || canonicalJson(normalizedProductionPlanSeal(
      planSeal, row.understanding_sha256,
    )) !== canonicalJson(planSeal)
    || !Array.isArray(excludedActions)
    || row.excluded_actions_sha256 !== hashJson(excludedActions)
    || row.authority_core_sha256 !== hashJson(authorityCore)
    || !attestation || !handoff
    || row.handoff_binding_sha256 !== handoff.bindingSha256
    || row.handoff_registry_sha256 !== handoff.registrySha256
    || row.authority_sha256 !== hashJson(boundAttestation)
    || row.execution_turn_id
      !== executionTurnIdentity(boundAttestation, row.obligation_sha256)
    || !validUnderstandingAuthority(attestation)) {
    throw new Error('material execution contract failed integrity verification');
  }
  return deepFreeze({
    ...body, contractRef: row.contract_ref, sha256: row.contract_sha256,
    createdAt: row.created_ts,
  });
}

function contractRowByRef(contractRef) {
  return lifecycleDb.prepare(
    `SELECT * FROM main.${CONTRACT_TABLE} WHERE contract_ref=?`,
  ).get(String(contractRef || '')) || null;
}

export function materialExecutionContract(contractRef) {
  requireStore();
  return contractReceipt(contractRowByRef(contractRef));
}

export function materialExecutionContractByHandoff(handoffKey) {
  requireStore();
  if (!HANDOFF_KEY.test(String(handoffKey || ''))) return null;
  return contractReceipt(lifecycleDb.prepare(
    `SELECT * FROM main.${CONTRACT_TABLE} WHERE handoff_key=?`,
  ).get(String(handoffKey)) || null);
}

function insertContract(body, contractSha256, contractRef, createdAt) {
  const inserted = lifecycleDb.prepare(`INSERT INTO main.${CONTRACT_TABLE} (
    contract,contract_ref,contract_sha256,handoff_key,created_ts,
    raw_body_sha256,projection_rule,projection_sha256,work_item_id,episode_id,
    source_turn_id,authority_timeline_id,understanding_sha256,
    semantic_authority_sha256,plan_sha256,plan_seal_json,
    excluded_actions_json,excluded_actions_sha256,
    obligation_json,obligation_sha256,execution_turn_id,authority_sha256,
    handoff_binding_sha256,handoff_registry_sha256,contract_json,
    authority_core_json,authority_core_sha256
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    body.contract, contractRef, contractSha256, body.handoffKey, createdAt,
    body.rawBodySha256, body.projection.rule, body.projection.sha256,
    body.workItemId, body.episodeId, body.sourceTurnId, body.authorityTimelineId,
    body.understandingSha256, body.semanticAuthoritySha256, body.planSha256,
    canonicalJson(body.planSeal), canonicalJson(body.excludedActions),
    body.excludedActionsSha256, canonicalJson(body.obligation),
    body.obligationSha256, body.executionTurnId, body.authoritySha256,
    body.handoffBindingSha256, body.handoffRegistrySha256,
    canonicalJson(body), canonicalJson(body.authorityCore), body.authorityCoreSha256,
  );
  if (inserted.changes !== 1) {
    throw new Error('material execution contract insert was not durably applied');
  }
  const stored = contractReceipt(contractRowByRef(contractRef));
  if (!stored || stored.contractRef !== contractRef || stored.sha256 !== contractSha256) {
    throw new Error('material execution contract insert failed exact reread');
  }
  return stored;
}

function contractConflict(row, contractSha256) {
  const existing = contractReceipt(row);
  if (existing.sha256 === contractSha256) return { kind: 'replay', value: existing };
  return {
    kind: 'conflict', reason: 'handoff_key_bound_to_different_contract',
    existingContractRef: existing.contractRef,
  };
}

export function bindMaterialExecutionContract(capability) {
  requireStore();
  const attestation = consumeCapability(
    CONTRACT_CAPABILITIES, capability, 'MaterialExecutionContractCapability/v1',
  );
  const handoff = activeInterpretationHandoff(attestation);
  if (!validUnderstandingAuthority(attestation) || !handoff
    || handoff.bindingSha256 !== attestation.handoffBindingSha256
    || handoff.registrySha256 !== attestation.handoffRegistrySha256
    || handoff.authorityCoreSha256 !== attestation.authorityCoreSha256
    || handoff.excludedActionsSha256 !== attestation.excludedActionsSha256) {
    throw new Error('material execution contract lost its durable authority');
  }
  const body = contractBody(attestation);
  const contractSha256 = hashJson(body);
  const contractRef = `material-contract:${contractSha256}`;
  return lifecycleDb.transaction(() => {
    requireStore();
    const current = activeInterpretationHandoff(attestation);
    if (!current || current.authorityCoreSha256 !== attestation.authorityCoreSha256) {
      throw new Error('material execution contract lost its durable authority');
    }
    const existing = lifecycleDb.prepare(
      `SELECT * FROM main.${CONTRACT_TABLE} WHERE handoff_key=?`,
    ).get(body.handoffKey);
    if (existing) return contractConflict(existing, contractSha256);
    const collision = lifecycleDb.prepare(`SELECT contract_ref FROM main.${CONTRACT_TABLE}
      WHERE contract_ref=? OR contract_sha256=? OR execution_turn_id=?
        OR authority_sha256=?`).get(
      contractRef, contractSha256, body.executionTurnId, body.authoritySha256,
    );
    if (collision) return {
      kind: 'conflict', reason: 'contract_identity_collision',
      existingContractRef: collision.contract_ref,
    };
    const stored = insertContract(body, contractSha256, contractRef, nowIso());
    return { kind: 'created', value: stored };
  }).immediate();
}

function normalizedImage(value, { absent = false } = {}) {
  if (absent && exactKeys(value, ['kind']) && value.kind === 'absent') {
    return Object.freeze({ kind: 'absent' });
  }
  if (!exactKeys(value, ['kind', 'sha256', 'bytes']) || value.kind !== 'sha256') return null;
  const digest = validSha(value.sha256);
  const bytes = Number(value.bytes);
  if (!digest || !Number.isSafeInteger(bytes) || bytes < 0 || bytes > MAX_MATERIAL_BYTES) {
    return null;
  }
  return Object.freeze({ kind: 'sha256', sha256: digest, bytes });
}

function normalizedIntentAttestation(value) {
  const fields = [
    'contract', 'contractRef', 'requirementId', 'canonicalPath',
    'intendedPostimage', 'preReadExecutionKey', 'preReadCompletionSha256',
  ];
  if (!exactKeys(value, fields)
    || value.contract !== 'MaterialEffectIntentAuthority/v1') return null;
  const contractRef = exactText(
    value.contractRef, /^material-contract:[a-f0-9]{64}$/u, 82,
  );
  const requirementId = exactText(value.requirementId, REQUIREMENT_ID, 3);
  const canonicalPath = canonicalWorkspacePath(value.canonicalPath);
  const intendedPostimage = normalizedImage({
    kind: 'sha256', ...value.intendedPostimage,
  });
  const preReadExecutionKey = exactText(value.preReadExecutionKey, OPERATION_KEY, 240);
  const preReadCompletionSha256 = validSha(value.preReadCompletionSha256);
  if (!contractRef || !requirementId || !canonicalPath
    || intendedPostimage?.kind !== 'sha256' || !preReadExecutionKey
    || !preReadCompletionSha256) return null;
  return deepFreeze({
    contract: value.contract, contractRef, requirementId, canonicalPath,
    intendedPostimage: {
      sha256: intendedPostimage.sha256, bytes: intendedPostimage.bytes,
    },
    preReadExecutionKey, preReadCompletionSha256,
  });
}

function parseCanonicalExecutionField(value, label) {
  try {
    const parsed = JSON.parse(String(value));
    if (canonicalJson(parsed) !== String(value)) throw new Error('not canonical');
    return parsed;
  } catch { throw new Error(`durable ${label} failed integrity verification`); }
}

function observedExecutionIntent(row) {
  if (!row || row.execution_verification !== 'verified'
    || !validSha(row.intent_record_sha256)
    || !validSha(row.semantic_hash)) return null;
  let args;
  let binding;
  try {
    args = row.args == null ? null : parseCanonicalExecutionField(row.args, 'execution args');
    binding = row.semantic_binding == null
      ? null : parseCanonicalExecutionField(row.semantic_binding, 'execution binding');
  } catch { return null; }
  const intentBody = {
    turnId: row.turn_id, tool: row.tool, args, binding,
    semanticHash: row.semantic_hash,
  };
  const expected = row.execution_contract === 'ExecutionRecord/v1'
    ? hashJson(intentBody)
    : row.execution_contract === 'ExecutionRecord/v2'
      ? hashJson({
        executionContract: row.execution_contract,
        executionSeq: Number(row.execution_seq),
        ...intentBody,
      })
      : null;
  return expected === row.intent_record_sha256 ? { args, binding } : null;
}

function observeExecution(row) {
  if (!row) return null;
  const existing = lifecycleDb.prepare(`SELECT * FROM main.${OBSERVATION_TABLE}
    WHERE execution_key=?`).get(row.execution_key);
  const intent = observedExecutionIntent(row);
  if (!intent || !['lees_bestand', 'schrijf_bestand', '@host/workspace.lstat']
    .includes(row.tool)) return null;
  let target;
  try { target = workspaceTargetAuthority(intent.args?.pad); } catch (error) {
    if (row.tool === 'schrijf_bestand') throw error;
    return null;
  }
  const snapshot = {
    contract: 'MaterialExecutionObservation/v1',
    executionKey: row.execution_key, turnId: row.turn_id, tool: row.tool,
    physicalTargetId: target.physicalTargetId,
    canonicalPath: target.canonicalPath,
    intentRecordSha256: row.intent_record_sha256,
  };
  const snapshotSha256 = hashJson(snapshot);
  if (existing) {
    if (existing.tool !== row.tool
      || existing.physical_target_id !== target.physicalTargetId
      || existing.canonical_path !== target.canonicalPath
      || existing.intent_record_sha256 !== row.intent_record_sha256
      || existing.execution_snapshot_sha256 !== snapshotSha256) {
      throw new Error('material execution observation failed integrity verification');
    }
    return existing;
  }
  lifecycleDb.prepare(`INSERT INTO main.${OBSERVATION_TABLE} (
    execution_key,tool,physical_target_id,canonical_path,intent_record_sha256,
    execution_snapshot_sha256,observed_ts
  ) VALUES (?,?,?,?,?,?,?)`).run(
    row.execution_key, row.tool, target.physicalTargetId, target.canonicalPath,
    row.intent_record_sha256, snapshotSha256, nowIso(),
  );
  return lifecycleDb.prepare(`SELECT * FROM main.${OBSERVATION_TABLE}
    WHERE execution_key=?`).get(row.execution_key);
}

function syncExecutionObservations() {
  const rows = lifecycleDb.prepare(`SELECT execution.*
    FROM main.turn_tool_executions execution
    WHERE execution.tool IN ('lees_bestand','schrijf_bestand','@host/workspace.lstat')
      AND execution.intent_record_sha256 IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM main.${OBSERVATION_TABLE} observed
        WHERE observed.execution_key=execution.execution_key)
    ORDER BY execution.started_ts,execution.execution_key`).all();
  for (const row of rows) observeExecution(row);
}

function durableReadExecution(executionKey) {
  try {
    syncExecutionObservations();
    const row = validateExecutionRecord(lifecycleDb.prepare(`SELECT execution.*
      FROM main.turn_tool_executions execution WHERE execution.execution_key=?`)
      .get(executionKey));
    const observed = observeExecution(row);
    return observed ? { ...row, sequence: Number(observed.observation_sequence) } : null;
  } catch { throw new Error('durable read receipt failed integrity verification'); }
}

function readReceiptKind(row) {
  if (row.tool === 'lees_bestand') return 'file';
  if (row.tool === '@host/workspace.lstat') return 'absence';
  return null;
}

function readExecutionPayload(row) {
  try {
    return {
      args: parseCanonicalExecutionField(row.args, 'read args'),
      binding: parseCanonicalExecutionField(row.semantic_binding, 'read binding'),
      output: JSON.parse(String(row.output)),
    };
  } catch { return null; }
}

function exactReadExecutionIdentity(row, completionSha256, contractValue) {
  return [
    row.status === 'done',
    row.turn_id === contractValue.executionTurnId,
    row.completion_record_sha256 === completionSha256,
    positiveSequence(row.sequence),
  ].every(Boolean);
}

function exactReadBinding(payload, kind, contractValue, requirementId, target) {
  const absenceProbe = kind === 'absence';
  const expectedCapability = absenceProbe ? 'workspace.file.stat' : 'workspace.file.read';
  const expectedOperation = absenceProbe ? 'lstat' : 'read';
  let suppliedTarget;
  try { suppliedTarget = workspaceTargetAuthority(payload.args?.pad); } catch { return false; }
  return [
    suppliedTarget.physicalTargetId === target.physicalTargetId,
    absenceProbe ? payload.args?.no_follow === true
      && exactKeys(payload.args, ['pad', 'no_follow']) : exactKeys(payload.args, ['pad']),
    payload.binding?.understandingSha256 === contractValue.understandingSha256,
    payload.binding?.requirementId === requirementId,
    canonicalJson(payload.binding?.addressesRequirementIds)
      === canonicalJson([requirementId]),
    payload.binding?.capabilityId === expectedCapability,
    payload.binding?.operationId === expectedOperation,
    payload.output?.ok === true,
    payload.output?.verified === true,
    payload.output?.partial === false,
    payload.output?.needs_followup === false,
  ].every(Boolean);
}

function readReceiptProjection(row, preimage) {
  return deepFreeze({
    executionKey: row.execution_key,
    completionRecordSha256: row.completion_record_sha256,
    sequence: Number(row.sequence), preimage,
  });
}

function absenceReadReceipt(row, output, target) {
  let outputTarget;
  try { outputTarget = workspaceTargetAuthority(output.path); } catch { return null; }
  const valid = [
    output.contract === 'WorkspacePathAbsenceReceipt/v1',
    output.tool === '@host/workspace.lstat',
    outputTarget.physicalTargetId === target.physicalTargetId,
    output.absent === true,
    output.noFollow === true,
  ].every(Boolean);
  return valid ? readReceiptProjection(row, { kind: 'absent' }) : null;
}

function fileReadReceipt(row, output, target) {
  if (output.contract !== 'ActionResult/v1' || output.tool !== 'lees_bestand') return null;
  const receipt = output.artifact_receipt;
  const data = typeof output.data === 'string' ? output.data : null;
  if (data == null) return null;
  const bytes = Buffer.from(data, 'utf8');
  let receiptTarget;
  try { receiptTarget = workspaceTargetAuthority(receipt?.path); } catch { return null; }
  const valid = [
    receiptTarget.physicalTargetId === target.physicalTargetId,
    receipt?.complete_read === true,
    Number(receipt?.bytes) === bytes.length,
    Number(receipt?.source_bytes) === bytes.length,
    receipt?.sha256 === hashBytes(bytes),
  ].every(Boolean);
  if (!valid) return null;
  return readReceiptProjection(row, {
    kind: 'sha256', sha256: receipt.sha256, bytes: bytes.length,
  });
}

function exactReadReceipt({
  executionKey, completionSha256, contractValue, requirementId, canonicalPath,
}) {
  const target = workspaceTargetAuthority(canonicalPath);
  const row = durableReadExecution(executionKey);
  if (!row) return null;
  const kind = readReceiptKind(row);
  if (!kind || !exactReadExecutionIdentity(row, completionSha256, contractValue)) return null;
  const payload = readExecutionPayload(row);
  if (!payload
    || !exactReadBinding(payload, kind, contractValue, requirementId, target)) {
    return null;
  }
  return kind === 'absence'
    ? absenceReadReceipt(row, payload.output, target)
    : fileReadReceipt(row, payload.output, target);
}

function artifactVersionHead(canonicalPath) {
  try {
    const status = artifactVersionStoreStatus();
    if (!exactKeys(status, ['contract', 'available'])
      || status.contract !== 'ArtifactVersionStoreStatus/v1'
      || status.available !== true) return null;
    const target = workspaceTargetAuthority(canonicalPath);
    const rows = lifecycleDb.prepare(`SELECT id,version_ref,canonical_path,
      provenance_sha256 FROM main.eva_artifact_versions ORDER BY id DESC`).all();
    const row = rows.find((candidate) => {
      try {
        return workspaceTargetAuthority(candidate.canonical_path).physicalTargetId
          === target.physicalTargetId;
      } catch { return false; }
    });
    if (!row) return Object.freeze({ id: 0, versionRef: null, provenanceSha256: null });
    const version = artifactVersion(row.version_ref);
    const receipt = artifactVersionReceipt(version);
    if (Number(version?.id) !== Number(row.id)
      || version?.path !== row.canonical_path
      || version?.provenanceSha256 !== row.provenance_sha256
      || receipt?.versionRef !== row.version_ref
      || receipt?.provenanceSha256 !== row.provenance_sha256) return null;
    return Object.freeze({
      id: Number(row.id), versionRef: row.version_ref,
      provenanceSha256: row.provenance_sha256,
    });
  } catch { return null; }
}

function exactStoredArtifactHead(value) {
  if (Number(value.artifactVersionHeadId) === 0) {
    return value.artifactVersionHeadRef == null
      && value.artifactVersionHeadProvenanceSha256 == null;
  }
  if (!positiveId(value.artifactVersionHeadId)
    || !/^artifact-version:[a-f0-9]{64}$/u.test(
      String(value.artifactVersionHeadRef || ''),
    ) || !validSha(value.artifactVersionHeadProvenanceSha256)) return false;
  try {
    const version = artifactVersion(value.artifactVersionHeadRef);
    const receipt = artifactVersionReceipt(version);
    return Number(version?.id) === Number(value.artifactVersionHeadId)
      && version?.path === value.canonicalPath
      && version?.provenanceSha256 === value.artifactVersionHeadProvenanceSha256
      && receipt?.provenanceSha256 === value.artifactVersionHeadProvenanceSha256;
  } catch { return false; }
}

export function issueMaterialEffectIntentCapability(hostContext) {
  requireStore();
  const attestation = verifiedAuthorityCandidate(
    hostContext, 'MaterialEffectIntentAuthority/v1',
  );
  requireStore();
  if (!attestation) return null;
  const row = contractRowByRef(attestation.contractRef);
  if (!row) return null;
  const contractValue = contractReceipt(row);
  const active = activeInterpretationHandoff(contractValue);
  if (!active || active.authorityCoreSha256 !== contractValue.authorityCoreSha256
    || !obligationAuthorizesIntent(contractValue, attestation)) return null;
  const read = exactReadReceipt({
    executionKey: attestation.preReadExecutionKey,
    completionSha256: attestation.preReadCompletionSha256,
    contractValue, requirementId: attestation.requirementId,
    canonicalPath: attestation.canonicalPath,
  });
  if (!read) return null;
  if (read.preimage.kind === 'sha256'
    && read.preimage.sha256 === attestation.intendedPostimage.sha256
    && read.preimage.bytes === attestation.intendedPostimage.bytes) return null;
  const target = workspaceTargetAuthority(attestation.canonicalPath);
  const writeHead = pathWriteHead(target.physicalTargetId);
  const versionHead = artifactVersionHead(attestation.canonicalPath);
  if (versionHead == null) return null;
  const capability = Object.freeze({ contract: 'MaterialEffectIntentCapability/v1' });
  if (writeHead && writeHead.sequence >= read.sequence) return null;
  INTENT_CAPABILITIES.set(capability, deepFreeze({
    ...attestation, preimage: read.preimage, preReadSequence: read.sequence,
    physicalTargetId: target.physicalTargetId,
    targetAuthoritySha256: target.authoritySha256,
    preReadPathWriteHeadSequence: Number(writeHead?.sequence || 0),
    artifactVersionHeadId: versionHead.id,
    artifactVersionHeadRef: versionHead.versionRef,
    artifactVersionHeadProvenanceSha256: versionHead.provenanceSha256,
  }));
  return capability;
}

function obligationAuthorizesIntent(contractValue, attestation) {
  const writes = contractValue.obligation?.selectedWrites;
  const plans = contractValue.obligation?.selectedPlans;
  const write = Array.isArray(writes) ? writes.find((candidate) => (
    candidate?.requirementId === attestation.requirementId
      && candidate?.canonicalPath === attestation.canonicalPath
  )) : null;
  const plan = Array.isArray(plans) ? plans.find((candidate) => (
    candidate?.requirementId === attestation.requirementId
  )) : null;
  if (!write || !plan || plan.capabilityId !== 'workspace.file.write'
    || plan.operationId !== 'write' || plan.effect !== 'workspace'
    || plan.toolName !== 'schrijf_bestand'
    || canonicalWorkspacePath(plan.args?.pad) !== attestation.canonicalPath
    || typeof plan.args?.inhoud !== 'string') return false;
  const plannedBytes = Buffer.from(plan.args.inhoud, 'utf8');
  return attestation.intendedPostimage?.sha256 === hashBytes(plannedBytes)
    && attestation.intendedPostimage?.bytes === plannedBytes.length;
}

function intentBody(contractValue, attestation) {
  const writeExecutionKey = `material-write:${hashJson({
    contract: 'MaterialEffectWriteIdentity/v1',
    contractSha256: contractValue.sha256,
    requirementId: attestation.requirementId,
    canonicalPath: attestation.canonicalPath,
    physicalTargetId: attestation.physicalTargetId,
    targetAuthoritySha256: attestation.targetAuthoritySha256,
    preimage: attestation.preimage,
    intendedPostimage: attestation.intendedPostimage,
    preReadExecutionKey: attestation.preReadExecutionKey,
    preReadCompletionSha256: attestation.preReadCompletionSha256,
    preReadSequence: attestation.preReadSequence,
    preReadPathWriteHeadSequence: attestation.preReadPathWriteHeadSequence,
    artifactVersionHeadId: attestation.artifactVersionHeadId,
    artifactVersionHeadRef: attestation.artifactVersionHeadRef,
    artifactVersionHeadProvenanceSha256:
      attestation.artifactVersionHeadProvenanceSha256,
  })}`;
  return deepFreeze({
    contract: 'MaterialEffectIntent/v1', contractRef: contractValue.contractRef,
    contractSha256: contractValue.sha256, requirementId: attestation.requirementId,
    canonicalPath: attestation.canonicalPath,
    physicalTargetId: attestation.physicalTargetId,
    targetAuthoritySha256: attestation.targetAuthoritySha256,
    preimage: attestation.preimage,
    intendedPostimage: attestation.intendedPostimage,
    preReadExecutionKey: attestation.preReadExecutionKey,
    preReadCompletionSha256: attestation.preReadCompletionSha256,
    preReadSequence: attestation.preReadSequence,
    preReadPathWriteHeadSequence: attestation.preReadPathWriteHeadSequence,
    artifactVersionHeadId: attestation.artifactVersionHeadId,
    artifactVersionHeadRef: attestation.artifactVersionHeadRef,
    artifactVersionHeadProvenanceSha256:
      attestation.artifactVersionHeadProvenanceSha256,
    writeExecutionKey,
  });
}

function intentReceipt(row) {
  if (!row) return null;
  const body = parseCanonical(row.intent_json, 'material effect intent');
  const intentSha256 = hashJson(body);
  const preimage = row.preimage_kind === 'absent' ? { kind: 'absent' } : {
    kind: 'sha256', sha256: row.preimage_sha256, bytes: Number(row.preimage_bytes),
  };
  const expected = {
    contract: row.contract, contractRef: row.contract_ref,
    contractSha256: row.contract_sha256, requirementId: row.requirement_id,
    canonicalPath: row.canonical_path, physicalTargetId: row.physical_target_id,
    targetAuthoritySha256: row.target_authority_sha256, preimage,
    intendedPostimage: {
      sha256: row.intended_sha256, bytes: Number(row.intended_bytes),
    },
    preReadExecutionKey: row.pre_read_execution_key,
    preReadCompletionSha256: row.pre_read_completion_sha256,
    preReadSequence: Number(row.pre_read_sequence),
    preReadPathWriteHeadSequence: Number(row.pre_read_path_write_head_sequence),
    artifactVersionHeadId: Number(row.artifact_version_head_id),
    artifactVersionHeadRef: row.artifact_version_head_ref,
    artifactVersionHeadProvenanceSha256:
      row.artifact_version_head_provenance_sha256,
    writeExecutionKey: row.write_execution_key,
  };
  const contractRow = contractRowByRef(row.contract_ref);
  const contractValue = contractReceipt(contractRow);
  const authority = normalizedIntentAttestation({
    contract: 'MaterialEffectIntentAuthority/v1',
    contractRef: expected.contractRef, requirementId: expected.requirementId,
    canonicalPath: expected.canonicalPath,
    intendedPostimage: expected.intendedPostimage,
    preReadExecutionKey: expected.preReadExecutionKey,
    preReadCompletionSha256: expected.preReadCompletionSha256,
  });
  const read = authority && exactReadReceipt({
    executionKey: authority.preReadExecutionKey,
    completionSha256: authority.preReadCompletionSha256,
    contractValue, requirementId: authority.requirementId,
    canonicalPath: authority.canonicalPath,
  });
  const attestation = authority && read ? deepFreeze({
    ...authority, preimage: read.preimage, preReadSequence: read.sequence,
    physicalTargetId: row.physical_target_id,
    targetAuthoritySha256: row.target_authority_sha256,
    preReadPathWriteHeadSequence: Number(row.pre_read_path_write_head_sequence),
    artifactVersionHeadId: Number(row.artifact_version_head_id),
    artifactVersionHeadRef: row.artifact_version_head_ref,
    artifactVersionHeadProvenanceSha256:
      row.artifact_version_head_provenance_sha256,
  }) : null;
  if (authority && !read) {
    throw new Error('durable read receipt failed integrity verification');
  }
  if (!contractRow || Number(contractRow.id) !== Number(row.contract_id)
    || contractValue.sha256 !== row.contract_sha256
    || !attestation
    || canonicalJson(read.preimage) !== canonicalJson(expected.preimage)
    || workspaceTargetAuthority(expected.canonicalPath).physicalTargetId
      !== expected.physicalTargetId
    || !validSha(expected.targetAuthoritySha256)
    || !exactStoredArtifactHead(expected)
    || read.sequence !== expected.preReadSequence
    || expected.preReadPathWriteHeadSequence >= expected.preReadSequence
    || canonicalJson(intentBody(contractValue, attestation)) !== canonicalJson(body)
    || canonicalJson(body) !== canonicalJson(expected)
    || row.intent_sha256 !== intentSha256
    || row.intent_ref !== `material-effect-intent:${intentSha256}`
    || !obligationAuthorizesIntent(contractValue, expected)) {
    throw new Error('material effect intent failed integrity verification');
  }
  return deepFreeze({
    ...body, intentRef: row.intent_ref, sha256: row.intent_sha256,
    createdAt: row.created_ts,
  });
}

function intentRowByRef(intentRef) {
  return lifecycleDb.prepare(`SELECT * FROM main.${INTENT_TABLE} WHERE intent_ref=?`)
    .get(String(intentRef || '')) || null;
}

export function materialEffectIntent(intentRef) {
  requireStore();
  return intentReceipt(intentRowByRef(intentRef));
}

function insertIntent(contractRow, body, intentSha256, intentRef, createdAt) {
  const inserted = lifecycleDb.prepare(`INSERT INTO main.${INTENT_TABLE} (
    contract,intent_ref,intent_sha256,contract_id,contract_ref,contract_sha256,
    requirement_id,canonical_path,physical_target_id,target_authority_sha256,
    preimage_kind,preimage_sha256,preimage_bytes,
    intended_sha256,intended_bytes,pre_read_execution_key,
    pre_read_completion_sha256,pre_read_sequence,
    pre_read_path_write_head_sequence,artifact_version_head_id,
    artifact_version_head_ref,artifact_version_head_provenance_sha256,
    write_execution_key,intent_json,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    body.contract, intentRef, intentSha256, Number(contractRow.id), body.contractRef,
    body.contractSha256, body.requirementId, body.canonicalPath,
    body.physicalTargetId, body.targetAuthoritySha256, body.preimage.kind,
    body.preimage.kind === 'sha256' ? body.preimage.sha256 : null,
    body.preimage.kind === 'sha256' ? body.preimage.bytes : null,
    body.intendedPostimage.sha256, body.intendedPostimage.bytes,
    body.preReadExecutionKey, body.preReadCompletionSha256,
    body.preReadSequence, body.preReadPathWriteHeadSequence,
    body.artifactVersionHeadId, body.artifactVersionHeadRef,
    body.artifactVersionHeadProvenanceSha256, body.writeExecutionKey,
    canonicalJson(body), createdAt,
  );
  if (inserted.changes !== 1) {
    throw new Error('material effect intent insert was not durably applied');
  }
  const stored = intentReceipt(intentRowByRef(intentRef));
  if (!stored || stored.intentRef !== intentRef || stored.sha256 !== intentSha256) {
    throw new Error('material effect intent insert failed exact reread');
  }
  return stored;
}

function intentConflict(row, intentSha256) {
  const existing = intentReceipt(row);
  if (existing.sha256 === intentSha256) return { kind: 'replay', value: existing };
  return {
    kind: 'conflict', reason: 'effect_identity_bound_to_different_intent',
    existingIntentRef: existing.intentRef,
  };
}

export function appendMaterialEffectIntent(capability) {
  requireStore();
  const attestation = consumeCapability(
    INTENT_CAPABILITIES, capability, 'MaterialEffectIntentCapability/v1',
  );
  return lifecycleDb.transaction(() => {
    requireStore();
    const contractRow = contractRowByRef(attestation.contractRef);
    const contractValue = contractReceipt(contractRow);
    if (!contractRow || !obligationAuthorizesIntent(contractValue, attestation)) {
      throw new Error('material effect intent is outside the frozen obligation');
    }
    const active = activeInterpretationHandoff(contractValue);
    if (!active || active.authorityCoreSha256 !== contractValue.authorityCoreSha256) {
      throw new Error('material effect intent lost its active durable authority');
    }
    syncExecutionObservations();
    const target = workspaceTargetAuthority(attestation.canonicalPath);
    const writeHead = pathWriteHead(target.physicalTargetId);
    const versionHead = artifactVersionHead(attestation.canonicalPath);
    if (Number(writeHead?.sequence || 0) !== attestation.preReadPathWriteHeadSequence
      || (writeHead && writeHead.sequence >= attestation.preReadSequence)
      || target.physicalTargetId !== attestation.physicalTargetId
      || target.authoritySha256 !== attestation.targetAuthoritySha256
      || !versionHead
      || canonicalJson(versionHead) !== canonicalJson({
        id: attestation.artifactVersionHeadId,
        versionRef: attestation.artifactVersionHeadRef,
        provenanceSha256: attestation.artifactVersionHeadProvenanceSha256,
      })) {
      throw new Error('material effect intent lost its exact pre-read head');
    }
    const body = intentBody(contractValue, attestation);
    const intentSha256 = hashJson(body);
    const intentRef = `material-effect-intent:${intentSha256}`;
    const existing = lifecycleDb.prepare(`SELECT * FROM main.${INTENT_TABLE}
      WHERE contract_id=? AND requirement_id=? AND physical_target_id=?`).get(
      contractRow.id, body.requirementId, body.physicalTargetId,
    );
    if (existing) return intentConflict(existing, intentSha256);
    const collision = lifecycleDb.prepare(`SELECT intent_ref FROM main.${INTENT_TABLE}
      WHERE intent_ref=? OR intent_sha256=? OR pre_read_execution_key=?
        OR pre_read_completion_sha256=? OR write_execution_key=?`).get(
      intentRef, intentSha256, body.preReadExecutionKey,
      body.preReadCompletionSha256, body.writeExecutionKey,
    );
    if (collision) return {
      kind: 'conflict', reason: 'intent_identity_collision',
      existingIntentRef: collision.intent_ref,
    };
    const stored = insertIntent(
      contractRow, body, intentSha256, intentRef, nowIso(),
    );
    return { kind: 'created', value: stored };
  }).immediate();
}

function claimRow(intentId, generation) {
  return lifecycleDb.prepare(`SELECT * FROM main.${CLAIM_TABLE}
    WHERE intent_id=? AND generation=?`).get(intentId, generation) || null;
}

function claimBody(intentValue, generation, {
  executionKey, predecessorReconciliationRef, observationHeadSequence,
} = /** @type {any} */ ({})) {
  const fenceToken = hashJson({
    contract: 'MaterialEffectFenceToken/v1', intentSha256: intentValue.sha256,
    generation, physicalTargetId: intentValue.physicalTargetId,
    targetAuthoritySha256: intentValue.targetAuthoritySha256,
    observationHeadSequence,
    artifactVersionHeadRef: intentValue.artifactVersionHeadRef,
    artifactVersionHeadProvenanceSha256:
      intentValue.artifactVersionHeadProvenanceSha256,
    predecessorReconciliationRef,
  });
  return deepFreeze({
    contract: 'MaterialEffectWriteClaim/v1', intentRef: intentValue.intentRef,
    intentSha256: intentValue.sha256, generation,
    physicalTargetId: intentValue.physicalTargetId,
    targetAuthoritySha256: intentValue.targetAuthoritySha256,
    executionKey, fenceToken, observationHeadSequence,
    artifactVersionHeadRef: intentValue.artifactVersionHeadRef,
    artifactVersionHeadProvenanceSha256:
      intentValue.artifactVersionHeadProvenanceSha256,
    predecessorReconciliationRef,
  });
}

function claimReceipt(row) {
  if (!row) return null;
  const body = parseCanonical(row.claim_json, 'material effect write claim');
  const expected = {
    contract: row.contract, intentRef: row.intent_ref,
    intentSha256: row.intent_sha256, generation: Number(row.generation),
    physicalTargetId: row.physical_target_id,
    targetAuthoritySha256: row.target_authority_sha256,
    executionKey: row.execution_key, fenceToken: row.fence_token,
    observationHeadSequence: Number(row.observation_head_sequence),
    artifactVersionHeadRef: row.artifact_version_head_ref,
    artifactVersionHeadProvenanceSha256:
      row.artifact_version_head_provenance_sha256,
    predecessorReconciliationRef: row.predecessor_reconciliation_ref,
  };
  const claimSha256 = hashJson(body);
  const intentRow = intentRowByRef(row.intent_ref);
  const intentValue = intentReceipt(intentRow);
  const recomputed = intentValue && claimBody(intentValue, Number(row.generation), {
    executionKey: row.execution_key,
    predecessorReconciliationRef: row.predecessor_reconciliation_ref,
    observationHeadSequence: Number(row.observation_head_sequence),
  });
  const execution = lifecycleDb.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(row.execution_key);
  const observed = execution && observeExecution(execution);
  if (!intentRow || Number(intentRow.id) !== Number(row.intent_id)
    || canonicalJson(body) !== canonicalJson(expected)
    || canonicalJson(recomputed) !== canonicalJson(body)
    || row.claim_sha256 !== claimSha256
    || row.claim_ref !== `material-effect-claim:${claimSha256}`
    || !execution || !observed || execution.tool !== 'schrijf_bestand'
    || execution.turn_id !== contractReceipt(
      contractRowByRef(intentValue.contractRef),
    ).executionTurnId
    || execution.intent_record_sha256 !== observed.intent_record_sha256
    || observed.physical_target_id !== intentValue.physicalTargetId) {
    throw new Error('material effect write claim failed integrity verification');
  }
  return deepFreeze({
    ...body, claimRef: row.claim_ref, sha256: row.claim_sha256,
    writeObservationSequence: Number(observed.observation_sequence),
    createdAt: row.created_ts,
  });
}

export function materialEffectWriteClaim(intentRef, generation = 0) {
  requireStore();
  const row = intentRowByRef(intentRef);
  if (!row || ![0, 1].includes(Number(generation))) return null;
  return claimReceipt(claimRow(Number(row.id), Number(generation)));
}

export function issueMaterialEffectWriteClaimCapability(hostContext) {
  requireStore();
  const authority = verifiedAuthorityCandidate(
    hostContext, 'MaterialEffectWriteClaimAuthority/v1',
  );
  requireStore();
  if (!authority) return null;
  const intentRow = intentRowByRef(authority.intentRef);
  const intentValue = intentReceipt(intentRow);
  if (!intentValue || authority.physicalTargetId !== intentValue.physicalTargetId) return null;
  const capability = Object.freeze({ contract: 'MaterialEffectWriteClaimCapability/v1' });
  CLAIM_CAPABILITIES.set(capability, authority);
  return capability;
}

function generationClaimAuthority(intentRow, intentValue, generation) {
  if (generation === 0) return {
    executionKey: intentValue.writeExecutionKey,
    predecessorReconciliationRef: null,
    expectedWriteHead: intentValue.preReadPathWriteHeadSequence,
  };
  const predecessor = lifecycleDb.prepare(`SELECT * FROM main.${RECONCILIATION_TABLE}
    WHERE intent_id=? AND claim_generation=0`).get(intentRow.id);
  const receipt = predecessor && reconciliationReceipt(predecessor);
  if (!receipt || receipt.outcome !== 'no_effect' || receipt.nextAction !== 'retry_cas'
    || receipt.recoveryGeneration !== 1) return null;
  return {
    executionKey: receipt.recoveryWriteExecutionKey,
    predecessorReconciliationRef: receipt.reconciliationRef,
    expectedWriteHead: receipt.startedWrite.sequence,
  };
}

function claimPlan(contractValue, intentValue) {
  return contractValue.planSeal.plans.find((plan) => (
    plan.requirementId === intentValue.requirementId
      && plan.capabilityId === 'workspace.file.write'
      && plan.operationId === 'write' && plan.toolName === 'schrijf_bestand'
      && canonicalWorkspacePath(plan.args?.pad) === intentValue.canonicalPath
  )) || null;
}

export function claimMaterialEffectWrite(capability) {
  requireStore();
  const authority = consumeCapability(
    CLAIM_CAPABILITIES, capability, 'MaterialEffectWriteClaimCapability/v1',
  );
  return lifecycleDb.transaction(() => {
    requireStore();
    syncExecutionObservations();
    const intentRow = intentRowByRef(authority.intentRef);
    const intentValue = intentReceipt(intentRow);
    const contractValue = intentValue
      ? contractReceipt(contractRowByRef(intentValue.contractRef)) : null;
    const active = contractValue && activeInterpretationHandoff(contractValue);
    const generation = Number(authority.generation);
    const generationAuthority = intentValue
      ? generationClaimAuthority(intentRow, intentValue, generation) : null;
    const target = intentValue && workspaceTargetAuthority(authority.physicalPath);
    const plan = contractValue && intentValue ? claimPlan(contractValue, intentValue) : null;
    const intentHead = target ? Number(lifecycleDb.prepare(`SELECT coalesce(max(id),0) id
      FROM main.${INTENT_TABLE} WHERE physical_target_id=?`)
      .get(target.physicalTargetId).id) : 0;
    const writeHead = target ? pathWriteHead(target.physicalTargetId) : null;
    const versionHead = intentValue ? artifactVersionHead(intentValue.canonicalPath) : null;
    if (!intentValue || !contractValue || !active
      || active.authorityCoreSha256 !== contractValue.authorityCoreSha256
      || !generationAuthority || !target || !plan
      || target.physicalTargetId !== intentValue.physicalTargetId
      || target.authoritySha256 !== intentValue.targetAuthoritySha256
      || intentHead !== Number(intentRow.id)
      || !versionHead || canonicalJson(versionHead) !== canonicalJson({
        id: intentValue.artifactVersionHeadId,
        versionRef: intentValue.artifactVersionHeadRef,
        provenanceSha256: intentValue.artifactVersionHeadProvenanceSha256,
      })) throw new Error('material effect write claim lost its CAS authority');
    const body = claimBody(intentValue, generation, {
      executionKey: generationAuthority.executionKey,
      predecessorReconciliationRef: generationAuthority.predecessorReconciliationRef,
      observationHeadSequence: generationAuthority.expectedWriteHead,
    });
    const claimSha256 = hashJson(body);
    const existing = claimRow(Number(intentRow.id), generation);
    if (existing) {
      const value = claimReceipt(existing);
      if (value.sha256 !== claimSha256) {
        throw new Error('material effect claim generation is already bound');
      }
      return { kind: 'replay', value };
    }
    if (Number(writeHead?.sequence || 0) !== generationAuthority.expectedWriteHead) {
      throw new Error('material effect write claim lost its CAS authority');
    }
    const claimRef = `material-effect-claim:${claimSha256}`;
    lifecycleDb.prepare(`INSERT INTO main.${CLAIM_TABLE} (
      contract,claim_ref,claim_sha256,intent_id,intent_ref,intent_sha256,
      generation,physical_target_id,target_authority_sha256,execution_key,
      fence_token,observation_head_sequence,artifact_version_head_ref,
      artifact_version_head_provenance_sha256,predecessor_reconciliation_ref,
      claim_json,created_ts
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      body.contract, claimRef, claimSha256, intentRow.id, intentValue.intentRef,
      intentValue.sha256, generation, body.physicalTargetId,
      body.targetAuthoritySha256, body.executionKey, body.fenceToken,
      body.observationHeadSequence, body.artifactVersionHeadRef,
      body.artifactVersionHeadProvenanceSha256,
      body.predecessorReconciliationRef, canonicalJson(body), nowIso(),
    );
    const binding = plan.semanticBinding;
    const claimed = claimTurnToolExecution({
      executionKey: body.executionKey, turnId: contractValue.executionTurnId,
      tool: 'schrijf_bestand', args: plan.args, semanticBinding: binding,
    });
    if (!claimed.claimed) throw new Error('material effect execution key was already claimed');
    observeExecution(claimed.row);
    return { kind: 'created', value: claimReceipt(claimRow(intentRow.id, generation)) };
  }).immediate();
}

export function materialEffectWriteFenceCurrent(claimRef, fenceToken) {
  requireStore();
  syncExecutionObservations();
  const row = lifecycleDb.prepare(`SELECT * FROM main.${CLAIM_TABLE}
    WHERE claim_ref=?`).get(String(claimRef || ''));
  const claim = row && claimReceipt(row);
  if (!claim || claim.fenceToken !== String(fenceToken || '')) return false;
  const intentRow = intentRowByRef(claim.intentRef);
  const intentValue = intentReceipt(intentRow);
  const execution = lifecycleDb.prepare(`SELECT status FROM main.turn_tool_executions
    WHERE execution_key=?`).get(claim.executionKey);
  const target = workspaceTargetAuthority(intentValue.canonicalPath);
  const writeHead = pathWriteHead(intentValue.physicalTargetId);
  const intentHead = Number(lifecycleDb.prepare(`SELECT coalesce(max(id),0) id
    FROM main.${INTENT_TABLE} WHERE physical_target_id=?`)
    .get(intentValue.physicalTargetId).id);
  const versionHead = artifactVersionHead(intentValue.canonicalPath);
  return execution?.status === 'started'
    && target.authoritySha256 === claim.targetAuthoritySha256
    && Number(writeHead?.sequence || 0) === claim.writeObservationSequence
    && intentHead === Number(intentRow.id)
    && canonicalJson(versionHead) === canonicalJson({
      id: intentValue.artifactVersionHeadId,
      versionRef: claim.artifactVersionHeadRef,
      provenanceSha256: claim.artifactVersionHeadProvenanceSha256,
    });
}

function normalizedRecoveryObservation(value) {
  const fields = [
    'contract', 'intentRef', 'claimGeneration', 'recoveryReadExecutionKey',
    'recoveryReadCompletionSha256',
  ];
  if (!exactKeys(value, fields)
    || value.contract !== 'MaterialEffectRecoveryObservation/v1') return null;
  const intentRef = exactText(
    value.intentRef, /^material-effect-intent:[a-f0-9]{64}$/u, 87,
  );
  const recoveryReadExecutionKey = exactText(
    value.recoveryReadExecutionKey, OPERATION_KEY, 240,
  );
  const recoveryReadCompletionSha256 = validSha(value.recoveryReadCompletionSha256);
  const claimGeneration = Number(value.claimGeneration);
  if (!intentRef || ![0, 1].includes(claimGeneration)
    || !recoveryReadExecutionKey || !recoveryReadCompletionSha256) return null;
  return deepFreeze({
    contract: value.contract, intentRef, claimGeneration, recoveryReadExecutionKey,
    recoveryReadCompletionSha256,
  });
}

function exactStartedWrite(intentRow, intentValue, contractValue, generation) {
  let row;
  try {
    syncExecutionObservations();
    const claim = claimReceipt(claimRow(Number(intentRow.id), Number(generation)));
    row = lifecycleDb.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(claim?.executionKey);
    const observed = row && observeExecution(row);
    const intent = observedExecutionIntent(row);
    if (!claim || !row || !observed || !intent
      || !['started', 'done'].includes(row.status) || row.tool !== 'schrijf_bestand'
      || row.turn_id !== contractValue.executionTurnId
      || !positiveSequence(observed.observation_sequence)) return null;
    const { args, binding } = intent;
    const plan = claimPlan(contractValue, intentValue);
    if (typeof args?.inhoud !== 'string'
      || !plan || canonicalJson(args) !== canonicalJson(plan.args)
      || workspaceTargetAuthority(args.pad).physicalTargetId
        !== intentValue.physicalTargetId
      || hashBytes(Buffer.from(args.inhoud, 'utf8')) !== intentValue.intendedPostimage.sha256
      || Buffer.byteLength(args.inhoud, 'utf8') !== intentValue.intendedPostimage.bytes
      || canonicalJson(binding) !== canonicalJson(plan.semanticBinding)) return null;
    return Object.freeze({
      executionKey: row.execution_key,
      sequence: Number(observed.observation_sequence),
      intentRecordSha256: row.intent_record_sha256,
      claimGeneration: Number(generation), claimRef: claim.claimRef,
      claimSha256: claim.sha256, fenceToken: claim.fenceToken,
    });
  } catch { return null; }
}

function pathWriteHead(physicalTargetId) {
  try {
    syncExecutionObservations();
    const row = lifecycleDb.prepare(`SELECT observation_sequence,execution_key
      FROM main.${OBSERVATION_TABLE} WHERE tool='schrijf_bestand'
        AND physical_target_id=? ORDER BY observation_sequence DESC LIMIT 1`)
      .get(physicalTargetId);
    return row ? {
      sequence: Number(row.observation_sequence), executionKey: row.execution_key,
    } : null;
  } catch { return null; }
}

function currentCasContext(intentRow, intentValue, startedWrite) {
  const intentHeadId = Number(lifecycleDb.prepare(`SELECT max(id) AS id
    FROM main.${INTENT_TABLE} WHERE physical_target_id=?`)
    .get(intentValue.physicalTargetId)?.id);
  const writeHead = pathWriteHead(intentValue.physicalTargetId);
  const versionHead = artifactVersionHead(intentValue.canonicalPath);
  if (!versionHead) return null;
  const conflictFree = intentHeadId === Number(intentRow.id)
    && writeHead?.executionKey === startedWrite.executionKey
    && writeHead.sequence === startedWrite.sequence
    && canonicalJson(versionHead) === canonicalJson({
      id: intentValue.artifactVersionHeadId,
      versionRef: intentValue.artifactVersionHeadRef,
      provenanceSha256: intentValue.artifactVersionHeadProvenanceSha256,
    });
  return deepFreeze({
    contract: 'MaterialEffectCasContext/v1',
    materialIntentHeadId: intentHeadId,
    pathWriteHeadSequence: Number(writeHead?.sequence || 0),
    pathWriteHeadExecutionKey: writeHead?.executionKey || '',
    artifactVersionHeadId: versionHead.id,
    artifactVersionHeadRef: versionHead.versionRef,
    artifactVersionHeadProvenanceSha256: versionHead.provenanceSha256,
    conflictFree,
  });
}

export function issueMaterialEffectReconciliationCapability(hostContext) {
  requireStore();
  let candidate;
  try { candidate = RECOVERY_VERIFIERS[0]?.(hostContext) || null; } catch { return null; }
  requireStore();
  if (!candidate) return null;
  const observation = normalizedRecoveryObservation(candidate);
  if (!observation) throw new TypeError('invalid material effect recovery host observation');
  const row = intentRowByRef(observation.intentRef);
  if (!row) return null;
  const intentValue = intentReceipt(row);
  const contractValue = materialExecutionContract(intentValue.contractRef);
  const startedWrite = exactStartedWrite(
    row, intentValue, contractValue, observation.claimGeneration,
  );
  if (!startedWrite) return null;
  const recoveryRead = exactReadReceipt({
    executionKey: observation.recoveryReadExecutionKey,
    completionSha256: observation.recoveryReadCompletionSha256,
    contractValue, requirementId: intentValue.requirementId,
    canonicalPath: intentValue.canonicalPath,
  });
  if (!recoveryRead || recoveryRead.sequence <= startedWrite.sequence) return null;
  const casContext = currentCasContext(row, intentValue, startedWrite);
  if (!casContext) return null;
  const capability = Object.freeze({ contract: 'MaterialEffectReconciliationCapability/v1' });
  RECONCILIATION_CAPABILITIES.set(capability, deepFreeze({
    ...observation, current: recoveryRead.preimage,
    recoveryRead, startedWrite, casContext,
  }));
  return capability;
}

function sameImage(left, right) {
  const rightKind = right?.kind || (validSha(right?.sha256) ? 'sha256' : '');
  if (left?.kind !== rightKind) return false;
  return left.kind === 'absent'
    || (left.sha256 === right.sha256 && left.bytes === right.bytes);
}

function recoveryWriteExecutionKey(intentValue) {
  return `material-write-recovery:${hashJson({
    contract: 'MaterialEffectRecoveryWriteIdentity/v1',
    intentSha256: intentValue.sha256, generation: 1,
    expectedPreimage: intentValue.preimage,
    intendedPostimage: intentValue.intendedPostimage,
  })}`;
}

function reconciliationDecision(intentRow, intentValue, observation) {
  const generation = Number(observation.claimGeneration);
  if (!observation.casContext.conflictFree
    || Number(observation.casContext.materialIntentHeadId) !== Number(intentRow.id)) return {
    outcome: 'uncertain_manual', reason: 'later_conflict', nextAction: 'manual',
    recoveryGeneration: generation, recoveryWriteExecutionKey: null,
  };
  if (observation.recoveryRead.sequence <= observation.startedWrite.sequence
    || observation.startedWrite.sequence <= intentValue.preReadSequence) return {
    outcome: 'uncertain_manual', reason: 'stale_observation', nextAction: 'manual',
    recoveryGeneration: generation, recoveryWriteExecutionKey: null,
  };
  if (sameImage(observation.current, intentValue.intendedPostimage)) return {
    outcome: 'applied', reason: 'effect_observed', nextAction: 'none',
    recoveryGeneration: generation, recoveryWriteExecutionKey: null,
  };
  if (sameImage(observation.current, intentValue.preimage) && generation === 0) return {
    outcome: 'no_effect', reason: 'preimage_unchanged', nextAction: 'retry_cas',
    recoveryGeneration: 1,
    recoveryWriteExecutionKey: recoveryWriteExecutionKey(intentValue),
  };
  if (sameImage(observation.current, intentValue.preimage)) return {
    outcome: 'uncertain_manual', reason: 'recovery_no_effect', nextAction: 'manual',
    recoveryGeneration: generation, recoveryWriteExecutionKey: null,
  };
  return {
    outcome: 'uncertain_manual',
    reason: observation.current.kind === 'absent' ? 'current_missing' : 'current_mismatch',
    nextAction: 'manual', recoveryGeneration: generation,
    recoveryWriteExecutionKey: null,
  };
}

function reconciliationBody(intentValue, durableObservation, decision) {
  const observationSha256 = hashJson(durableObservation);
  return deepFreeze({
    contract: 'MaterialEffectReconciliationReceipt/v1',
    intentRef: intentValue.intentRef, intentSha256: intentValue.sha256,
    contractRef: intentValue.contractRef, contractSha256: intentValue.contractSha256,
    claimGeneration: durableObservation.claimGeneration,
    claimRef: durableObservation.startedWrite.claimRef,
    claimSha256: durableObservation.startedWrite.claimSha256,
    claimFenceToken: durableObservation.startedWrite.fenceToken,
    requirementId: intentValue.requirementId, canonicalPath: intentValue.canonicalPath,
    startedWriteExecutionKey: durableObservation.startedWrite.executionKey,
    preimage: intentValue.preimage, intendedPostimage: intentValue.intendedPostimage,
    preRead: {
      executionKey: intentValue.preReadExecutionKey,
      completionRecordSha256: intentValue.preReadCompletionSha256,
      sequence: intentValue.preReadSequence,
    },
    startedWrite: durableObservation.startedWrite,
    recoveryRead: durableObservation.recoveryRead,
    casContextSha256: durableObservation.casContextSha256,
    observation: durableObservation, observationSha256,
    ...decision,
  });
}

function reconciliationDurableExecution(row, intentValue, contractValue) {
  if (!intentValue || !contractValue) return Object.freeze({
    startedWrite: null, recoveryRead: null,
  });
  const intentRow = intentRowByRef(intentValue.intentRef);
  return Object.freeze({
    startedWrite: exactStartedWrite(
      intentRow, intentValue, contractValue, Number(row.claim_generation),
    ),
    recoveryRead: exactReadReceipt({
      executionKey: row.recovery_read_execution_key,
      completionSha256: row.recovery_read_completion_sha256,
      contractValue, requirementId: intentValue.requirementId,
      canonicalPath: intentValue.canonicalPath,
    }),
  });
}

function exactReconciliationIdentity({
  row, body, observation, reconciliationSha256, intentRow, intentValue,
}) {
  return [
    Number(intentRow.id) === Number(row.intent_id),
    intentValue.sha256 === row.intent_sha256,
    canonicalJson(body.observation) === canonicalJson(observation),
    hashJson(observation) === row.observation_sha256,
    body.observationSha256 === row.observation_sha256,
    body.intentRef === row.intent_ref,
    body.intentSha256 === row.intent_sha256,
    body.contractRef === row.contract_ref,
    body.canonicalPath === row.canonical_path,
    Number(body.claimGeneration) === Number(row.claim_generation),
    body.claimRef === row.claim_ref,
    body.claimSha256 === row.claim_sha256,
    body.claimFenceToken === row.claim_fence_token,
    body.outcome === row.outcome,
    body.reason === row.reason,
    body.nextAction === row.next_action,
    Number(body.recoveryGeneration) === Number(row.recovery_generation),
    (body.recoveryWriteExecutionKey ?? null) === row.recovery_write_execution_key,
    observation.current?.kind === row.current_kind,
    (observation.current?.sha256 ?? null) === row.current_sha256,
    (observation.current?.bytes ?? null) === (row.current_bytes ?? null),
    row.reconciliation_sha256 === reconciliationSha256,
    row.reconciliation_ref === `material-effect-reconciliation:${reconciliationSha256}`,
  ].every(Boolean);
}

function exactReconciliationReads(row, startedWrite, recoveryRead) {
  if (!startedWrite || !recoveryRead) return false;
  return [
    startedWrite.sequence === Number(row.started_write_sequence),
    startedWrite.intentRecordSha256 === row.started_write_intent_sha256,
    recoveryRead.sequence === Number(row.recovery_read_sequence),
    recoveryRead.executionKey === row.recovery_read_execution_key,
    recoveryRead.completionRecordSha256 === row.recovery_read_completion_sha256,
  ].every(Boolean);
}

function exactReconciliationCas(row, body, casContext) {
  return [
    hashJson(casContext) === row.cas_context_sha256,
    body.casContextSha256 === row.cas_context_sha256,
    Number(casContext.materialIntentHeadId) === Number(row.material_intent_head_id),
    Number(casContext.pathWriteHeadSequence) === Number(row.path_write_head_sequence),
    Number(casContext.artifactVersionHeadId) === Number(row.artifact_version_head_id),
  ].every(Boolean);
}

function reconciliationReceipt(row) {
  if (!row) return null;
  const body = parseCanonical(row.receipt_json, 'material effect reconciliation');
  const observation = parseCanonical(
    row.observation_json, 'material effect recovery observation',
  );
  const reconciliationSha256 = hashJson(body);
  const intentRow = intentRowByRef(row.intent_ref);
  const intentValue = intentReceipt(intentRow);
  if (!intentRow || !intentValue) {
    throw new Error('material effect reconciliation failed integrity verification');
  }
  const contractValue = materialExecutionContract(intentValue.contractRef);
  const { startedWrite, recoveryRead } = reconciliationDurableExecution(
    row, intentValue, contractValue,
  );
  const casContext = parseCanonical(row.cas_context_json, 'material effect CAS context');
  const decision = reconciliationDecision(intentRow, intentValue, observation);
  const expectedBody = reconciliationBody(intentValue, observation, decision);
  const exact = [
    exactReconciliationIdentity({
      row, body, observation, reconciliationSha256, intentRow, intentValue,
    }),
    exactReconciliationReads(row, startedWrite, recoveryRead),
    exactReconciliationCas(row, body, casContext),
    canonicalJson(body) === canonicalJson(expectedBody),
  ].every(Boolean);
  if (!exact) {
    throw new Error('material effect reconciliation failed integrity verification');
  }
  return deepFreeze({
    ...body, reconciliationRef: row.reconciliation_ref,
    sha256: row.reconciliation_sha256, createdAt: row.created_ts,
  });
}

function reconciliationRowByIntent(intentRef, generation = null) {
  return lifecycleDb.prepare(
    `SELECT * FROM main.${RECONCILIATION_TABLE} WHERE intent_ref=?
      AND (? IS NULL OR claim_generation=?)
      ORDER BY claim_generation DESC LIMIT 1`,
  ).get(String(intentRef || ''), generation, generation) || null;
}

export function materialEffectReconciliation(intentRef) {
  requireStore();
  return reconciliationReceipt(reconciliationRowByIntent(intentRef));
}

function insertReconciliation(intentRow, body, reconciliationSha256, ref, createdAt) {
  const current = body.observation.current;
  const inserted = lifecycleDb.prepare(`INSERT INTO main.${RECONCILIATION_TABLE} (
    contract,reconciliation_ref,reconciliation_sha256,intent_id,intent_ref,
    intent_sha256,contract_ref,claim_generation,claim_ref,claim_sha256,
    claim_fence_token,canonical_path,outcome,reason,next_action,
    recovery_generation,recovery_write_execution_key,current_kind,current_sha256,
    current_bytes,recovery_read_execution_key,recovery_read_completion_sha256,
    recovery_read_sequence,started_write_sequence,started_write_intent_sha256,
    material_intent_head_id,path_write_head_sequence,artifact_version_head_id,
    cas_context_json,cas_context_sha256,observation_json,observation_sha256,
    receipt_json,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    body.contract, ref, reconciliationSha256, Number(intentRow.id), body.intentRef,
    body.intentSha256, body.contractRef, body.claimGeneration, body.claimRef,
    body.claimSha256, body.claimFenceToken, body.canonicalPath, body.outcome, body.reason,
    body.nextAction, body.recoveryGeneration, body.recoveryWriteExecutionKey,
    current.kind, current.kind === 'sha256' ? current.sha256 : null,
    current.kind === 'sha256' ? current.bytes : null,
    body.recoveryRead.executionKey, body.recoveryRead.completionRecordSha256,
    body.recoveryRead.sequence, body.startedWrite.sequence,
    body.startedWrite.intentRecordSha256,
    body.observation.casContext.materialIntentHeadId,
    body.observation.casContext.pathWriteHeadSequence,
    body.observation.casContext.artifactVersionHeadId,
    canonicalJson(body.observation.casContext), body.casContextSha256,
    canonicalJson(body.observation), body.observationSha256,
    canonicalJson(body), createdAt,
  );
  if (inserted.changes !== 1) {
    throw new Error('material effect reconciliation insert was not durably applied');
  }
  const stored = reconciliationReceipt(reconciliationRowByIntent(
    body.intentRef, body.claimGeneration,
  ));
  if (!stored || stored.reconciliationRef !== ref
    || stored.sha256 !== reconciliationSha256) {
    throw new Error('material effect reconciliation insert failed exact reread');
  }
  return stored;
}

export function reconcileMaterialEffect(capability) {
  requireStore();
  const observation = consumeCapability(
    RECONCILIATION_CAPABILITIES, capability,
    'MaterialEffectReconciliationCapability/v1',
  );
  return lifecycleDb.transaction(() => {
    requireStore();
    const intentRow = intentRowByRef(observation.intentRef);
    const intentValue = intentReceipt(intentRow);
    if (!intentRow) throw new Error('material effect reconciliation lost its intent');
    const existing = reconciliationRowByIntent(
      observation.intentRef, observation.claimGeneration,
    );
    if (existing) return { kind: 'replay', value: reconciliationReceipt(existing) };
    const contractValue = materialExecutionContract(intentValue.contractRef);
    const startedWrite = exactStartedWrite(
      intentRow, intentValue, contractValue, observation.claimGeneration,
    );
    const recoveryRead = exactReadReceipt({
      executionKey: observation.recoveryReadExecutionKey,
      completionSha256: observation.recoveryReadCompletionSha256,
      contractValue, requirementId: intentValue.requirementId,
      canonicalPath: intentValue.canonicalPath,
    });
    if (!startedWrite || !recoveryRead || recoveryRead.sequence <= startedWrite.sequence) {
      throw new Error('material effect reconciliation lost its durable execution order');
    }
    const casContext = currentCasContext(intentRow, intentValue, startedWrite);
    if (!casContext) {
      throw new Error('material effect reconciliation lost its durable CAS context');
    }
    const durableObservation = deepFreeze({
      contract: observation.contract, intentRef: observation.intentRef,
      claimGeneration: observation.claimGeneration,
      canonicalPath: intentValue.canonicalPath, current: recoveryRead.preimage,
      startedWrite, recoveryRead, casContext,
      casContextSha256: hashJson(casContext),
    });
    const decision = reconciliationDecision(intentRow, intentValue, durableObservation);
    const body = reconciliationBody(intentValue, durableObservation, decision);
    const reconciliationSha256 = hashJson(body);
    const ref = `material-effect-reconciliation:${reconciliationSha256}`;
    const stored = insertReconciliation(
      intentRow, body, reconciliationSha256, ref, nowIso(),
    );
    return { kind: 'created', value: stored };
  }).immediate();
}

export const materialLifecycleContracts = Object.freeze({
  executionContract: 'MaterialExecutionContract/v1',
  effectIntent: 'MaterialEffectIntent/v1',
  recoveryObservation: 'MaterialEffectRecoveryObservation/v1',
  effectReconciliation: 'MaterialEffectReconciliationReceipt/v1',
});
