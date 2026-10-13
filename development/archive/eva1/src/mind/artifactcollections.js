import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { config } from '../config.js';
import { db as productionDatabase } from '../db.js';
import {
  executionLedgerSchemaManifest,
  executionLedgerStatus,
} from '../executionledger.js';
import {
  artifactCollectionStoreAuthority,
  artifactVersionReceipt,
  artifactVersionStoreStatus,
  consumeArtifactCollectionStoreAuthority,
} from './artifacts.js';

const LOGICAL_ROOT = 'data/workspace';
const DISCOVERY_TOOL = '@host/workspace.recursive_snapshot';
const SHA256 = /^[a-f0-9]{64}$/u;
const VERSION_REF = /^artifact-version:[a-f0-9]{64}$/u;
const SNAPSHOT_REF = /^recursive-workspace-snapshot:[a-f0-9]{64}$/u;
const COLLECTION_REF = /^artifact-collection:[a-f0-9]{64}$/u;
const CHECK_REF = /^artifact-collection-check:[a-f0-9]{64}$/u;
const CONSUMPTION_REF = /^artifact-collection-consumption:[a-f0-9]{64}$/u;
const OPERATION_KEY = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,239}$/u;
const EXECUTION_KEY = /^[^\s]{1,512}$/u;
const REQUIREMENT_ID = /^r(?:[1-9]|1[0-2])$/u;
const MAX_TEXT_BYTES = 64 * 1024;
const CONFIGURED_WORKSPACE_ROOT = config.workspaceDir;

const DEFAULT_LIMITS = Object.freeze({
  maxFiles: 128,
  maxBytes: 16 * 1024 * 1024,
  maxDepth: 32,
  maxDirectories: 256,
  maxManifestBytes: 40_000,
});
const HARD_LIMITS = Object.freeze({
  maxFiles: 128,
  maxBytes: 64 * 1024 * 1024,
  maxDepth: 64,
  maxDirectories: 4_096,
  maxManifestBytes: 4 * 1024 * 1024,
});
const EXCLUDED_REASON_CODES = Object.freeze([
  'not_artifact', 'out_of_scope', 'sensitive', 'temporary', 'unsupported',
]);
const EXCLUDED_REASONS = new Set(EXCLUDED_REASON_CODES);
const ARTIFACT_ROLES = new Set(['source', 'test', 'fixture', 'result']);
const PREDICATE = Object.freeze({
  contract: 'ArtifactCollectionPredicate/v1',
  kind: 'explicit_snapshot_member_classification',
  included: 'current_artifact_version',
  excluded: 'bounded_reason_code',
});

const TABLES = Object.freeze({
  snapshots: 'eva_recursive_workspace_snapshots',
  snapshotMembers: 'eva_recursive_workspace_snapshot_members',
  collections: 'eva_artifact_collections',
  collectionMembers: 'eva_artifact_collection_members',
  checks: 'eva_artifact_collection_checks',
  consumptions: 'eva_artifact_collection_consumptions',
});
const ARTIFACT_PROTECTED_TABLES = Object.freeze([
  'eva_artifact_objects', 'eva_artifact_seals', 'eva_artifact_versions',
]);

const SNAPSHOT_TABLE_SQL = `CREATE TABLE eva_recursive_workspace_snapshots (
  snapshot_ref TEXT PRIMARY KEY,
  contract TEXT NOT NULL,
  snapshot_sha256 TEXT NOT NULL UNIQUE,
  request_sha256 TEXT NOT NULL UNIQUE,
  operation_key TEXT NOT NULL UNIQUE,
  created_ts TEXT NOT NULL,
  root TEXT NOT NULL,
  root_identity_sha256 TEXT NOT NULL,
  selection_kind TEXT NOT NULL,
  query TEXT,
  complete INTEGER NOT NULL,
  discovery_execution_key TEXT NOT NULL UNIQUE,
  discovery_completion_sha256 TEXT NOT NULL UNIQUE,
  manifest_sha256 TEXT NOT NULL,
  dependency_schema_sha256 TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  CHECK(contract='RecursiveWorkspaceSnapshot/v1'),
  CHECK(snapshot_ref='recursive-workspace-snapshot:' || snapshot_sha256),
  CHECK(length(snapshot_sha256)=64 AND lower(snapshot_sha256)=snapshot_sha256
    AND snapshot_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(request_sha256)=64 AND lower(request_sha256)=request_sha256
    AND request_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(operation_key)>=1 AND length(operation_key)<=240),
  CHECK(root='data/workspace'),
  CHECK(length(root_identity_sha256)=64 AND lower(root_identity_sha256)=root_identity_sha256
    AND root_identity_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(selection_kind='all_regular_files'),
  CHECK(query IS NULL),
  CHECK(complete=1),
  CHECK(length(discovery_execution_key)>=1 AND length(discovery_execution_key)<=512),
  CHECK(length(discovery_completion_sha256)=64
    AND lower(discovery_completion_sha256)=discovery_completion_sha256
    AND discovery_completion_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(manifest_sha256)=64 AND lower(manifest_sha256)=manifest_sha256
    AND manifest_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(dependency_schema_sha256)=64
    AND lower(dependency_schema_sha256)=dependency_schema_sha256
    AND dependency_schema_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(snapshot_json)),
  CHECK(length(created_ts)>=20 AND length(created_ts)<=40)
) WITHOUT ROWID`;

const SNAPSHOT_MEMBER_TABLE_SQL = `CREATE TABLE eva_recursive_workspace_snapshot_members (
  snapshot_ref TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  canonical_path TEXT NOT NULL,
  artifact_sha256 TEXT NOT NULL,
  byte_length INTEGER NOT NULL,
  stat_identity_json TEXT NOT NULL,
  stat_identity_sha256 TEXT NOT NULL,
  member_sha256 TEXT NOT NULL,
  member_json TEXT NOT NULL,
  PRIMARY KEY(snapshot_ref,ordinal),
  UNIQUE(snapshot_ref,canonical_path),
  UNIQUE(snapshot_ref,member_sha256),
  CHECK(ordinal>=0),
  CHECK(canonical_path GLOB 'data/workspace/?*'),
  CHECK(length(artifact_sha256)=64 AND lower(artifact_sha256)=artifact_sha256
    AND artifact_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(byte_length>=0),
  CHECK(json_valid(stat_identity_json)),
  CHECK(length(stat_identity_sha256)=64
    AND lower(stat_identity_sha256)=stat_identity_sha256
    AND stat_identity_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(member_sha256)=64 AND lower(member_sha256)=member_sha256
    AND member_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(member_json))
) WITHOUT ROWID`;

const COLLECTION_TABLE_SQL = `CREATE TABLE eva_artifact_collections (
  collection_ref TEXT PRIMARY KEY,
  contract TEXT NOT NULL,
  collection_sha256 TEXT NOT NULL UNIQUE,
  request_sha256 TEXT NOT NULL UNIQUE,
  authority_sha256 TEXT NOT NULL UNIQUE,
  operation_key TEXT NOT NULL UNIQUE,
  created_ts TEXT NOT NULL,
  work_item_id INTEGER NOT NULL,
  episode_id INTEGER NOT NULL,
  origin_id INTEGER NOT NULL,
  expected_work_revision INTEGER NOT NULL,
  expected_episode_revision INTEGER NOT NULL,
  snapshot_ref TEXT NOT NULL,
  snapshot_sha256 TEXT NOT NULL,
  dependency_schema_sha256 TEXT NOT NULL,
  work_json TEXT NOT NULL,
  work_sha256 TEXT NOT NULL,
  episode_json TEXT NOT NULL,
  episode_sha256 TEXT NOT NULL,
  origin_json TEXT NOT NULL,
  origin_sha256 TEXT NOT NULL,
  origin_set_sha256 TEXT NOT NULL,
  origin_timeline_sha256 TEXT NOT NULL,
  predicate_json TEXT NOT NULL,
  predicate_sha256 TEXT NOT NULL,
  artifact_inventory_json TEXT NOT NULL,
  artifact_inventory_sha256 TEXT NOT NULL,
  artifact_store_fence_sha256 TEXT NOT NULL,
  collection_json TEXT NOT NULL,
  CHECK(contract='ArtifactCollection/v1'),
  CHECK(collection_ref='artifact-collection:' || collection_sha256),
  CHECK(length(collection_sha256)=64 AND lower(collection_sha256)=collection_sha256
    AND collection_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(request_sha256)=64 AND lower(request_sha256)=request_sha256
    AND request_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(authority_sha256)=64 AND lower(authority_sha256)=authority_sha256
    AND authority_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(operation_key)>=1 AND length(operation_key)<=240),
  CHECK(work_item_id>=1 AND episode_id>=1 AND origin_id>=1),
  CHECK(expected_work_revision>=0 AND expected_episode_revision>=0),
  CHECK(snapshot_ref GLOB 'recursive-workspace-snapshot:*'),
  CHECK(length(snapshot_sha256)=64 AND lower(snapshot_sha256)=snapshot_sha256
    AND snapshot_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(dependency_schema_sha256)=64
    AND lower(dependency_schema_sha256)=dependency_schema_sha256
    AND dependency_schema_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(work_json) AND json_valid(episode_json) AND json_valid(origin_json)),
  CHECK(length(work_sha256)=64 AND lower(work_sha256)=work_sha256
    AND work_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(episode_sha256)=64 AND lower(episode_sha256)=episode_sha256
    AND episode_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(origin_sha256)=64 AND lower(origin_sha256)=origin_sha256
    AND origin_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(origin_set_sha256)=64 AND lower(origin_set_sha256)=origin_set_sha256
    AND origin_set_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(origin_timeline_sha256)=64 AND lower(origin_timeline_sha256)=origin_timeline_sha256
    AND origin_timeline_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(predicate_json)),
  CHECK(length(predicate_sha256)=64 AND lower(predicate_sha256)=predicate_sha256
    AND predicate_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(artifact_inventory_json)),
  CHECK(length(artifact_inventory_sha256)=64
    AND lower(artifact_inventory_sha256)=artifact_inventory_sha256
    AND artifact_inventory_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(artifact_store_fence_sha256)=64
    AND lower(artifact_store_fence_sha256)=artifact_store_fence_sha256
    AND artifact_store_fence_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(collection_json)),
  CHECK(length(created_ts)>=20 AND length(created_ts)<=40)
) WITHOUT ROWID`;

const COLLECTION_MEMBER_TABLE_SQL = `CREATE TABLE eva_artifact_collection_members (
  collection_ref TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  snapshot_ref TEXT NOT NULL,
  snapshot_ordinal INTEGER NOT NULL,
  canonical_path TEXT NOT NULL,
  classification TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  reason_json TEXT NOT NULL,
  reason_sha256 TEXT NOT NULL,
  version_ref TEXT,
  artifact_receipt_json TEXT,
  artifact_receipt_sha256 TEXT,
  head_json TEXT NOT NULL,
  head_sha256 TEXT NOT NULL,
  member_sha256 TEXT NOT NULL,
  member_json TEXT NOT NULL,
  PRIMARY KEY(collection_ref,ordinal),
  UNIQUE(collection_ref,canonical_path),
  UNIQUE(collection_ref,snapshot_ref,snapshot_ordinal),
  UNIQUE(collection_ref,member_sha256),
  CHECK(ordinal>=0 AND snapshot_ordinal>=0),
  CHECK(canonical_path GLOB 'data/workspace/?*'),
  CHECK(classification IN ('included','excluded')),
  CHECK(reason_code IN ('current_artifact_version','not_artifact','out_of_scope',
    'sensitive','temporary','unsupported')),
  CHECK(json_valid(reason_json) AND json_valid(head_json) AND json_valid(member_json)),
  CHECK(length(reason_sha256)=64 AND lower(reason_sha256)=reason_sha256
    AND reason_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK((classification='included' AND reason_code='current_artifact_version'
      AND version_ref IS NOT NULL AND artifact_receipt_json IS NOT NULL
      AND artifact_receipt_sha256 IS NOT NULL)
    OR (classification='excluded' AND reason_code!='current_artifact_version'
      AND version_ref IS NULL AND artifact_receipt_json IS NULL
      AND artifact_receipt_sha256 IS NULL)),
  CHECK(artifact_receipt_json IS NULL OR json_valid(artifact_receipt_json)),
  CHECK(artifact_receipt_sha256 IS NULL OR (length(artifact_receipt_sha256)=64
    AND lower(artifact_receipt_sha256)=artifact_receipt_sha256
    AND artifact_receipt_sha256 NOT GLOB '*[^a-f0-9]*')),
  CHECK(length(head_sha256)=64 AND lower(head_sha256)=head_sha256
    AND head_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(member_sha256)=64 AND lower(member_sha256)=member_sha256
    AND member_sha256 NOT GLOB '*[^a-f0-9]*')
) WITHOUT ROWID`;

const CHECK_TABLE_SQL = `CREATE TABLE eva_artifact_collection_checks (
  check_ref TEXT PRIMARY KEY,
  contract TEXT NOT NULL,
  operation_key TEXT NOT NULL UNIQUE,
  request_sha256 TEXT NOT NULL,
  collection_ref TEXT NOT NULL,
  collection_sha256 TEXT NOT NULL,
  checked_ts TEXT NOT NULL,
  current INTEGER NOT NULL,
  state_sha256 TEXT NOT NULL,
  receipt_json TEXT NOT NULL,
  receipt_sha256 TEXT NOT NULL,
  CHECK(contract='ArtifactCollectionCurrentReceipt/v1'),
  CHECK(check_ref GLOB 'artifact-collection-check:*'),
  CHECK(length(operation_key)>=1 AND length(operation_key)<=240),
  CHECK(length(request_sha256)=64 AND lower(request_sha256)=request_sha256
    AND request_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(collection_ref GLOB 'artifact-collection:*'),
  CHECK(length(collection_sha256)=64 AND lower(collection_sha256)=collection_sha256
    AND collection_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(current IN (0,1)),
  CHECK(length(state_sha256)=64 AND lower(state_sha256)=state_sha256
    AND state_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(receipt_json)),
  CHECK(length(receipt_sha256)=64 AND lower(receipt_sha256)=receipt_sha256
    AND receipt_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(checked_ts)>=20 AND length(checked_ts)<=40)
) WITHOUT ROWID`;

const CONSUMPTION_TABLE_SQL = `CREATE TABLE eva_artifact_collection_consumptions (
  consumption_ref TEXT PRIMARY KEY,
  contract TEXT NOT NULL,
  operation_key TEXT NOT NULL UNIQUE,
  request_sha256 TEXT NOT NULL UNIQUE,
  check_ref TEXT NOT NULL UNIQUE,
  collection_ref TEXT NOT NULL,
  collection_sha256 TEXT NOT NULL,
  state_sha256 TEXT NOT NULL,
  consumed_ts TEXT NOT NULL,
  receipt_json TEXT NOT NULL,
  receipt_sha256 TEXT NOT NULL,
  CHECK(contract='UniversalArtifactCollectionReceipt/v1'),
  CHECK(consumption_ref GLOB 'artifact-collection-consumption:*'),
  CHECK(length(operation_key)>=1 AND length(operation_key)<=240),
  CHECK(length(request_sha256)=64 AND lower(request_sha256)=request_sha256
    AND request_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(check_ref GLOB 'artifact-collection-check:*'),
  CHECK(collection_ref GLOB 'artifact-collection:*'),
  CHECK(length(collection_sha256)=64 AND lower(collection_sha256)=collection_sha256
    AND collection_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(state_sha256)=64 AND lower(state_sha256)=state_sha256
    AND state_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(json_valid(receipt_json)),
  CHECK(length(receipt_sha256)=64 AND lower(receipt_sha256)=receipt_sha256
    AND receipt_sha256 NOT GLOB '*[^a-f0-9]*'),
  CHECK(length(consumed_ts)>=20 AND length(consumed_ts)<=40)
) WITHOUT ROWID`;

const INDEX_SQL = Object.freeze({
  eva_recursive_workspace_snapshot_member_lookup:
    `CREATE INDEX eva_recursive_workspace_snapshot_member_lookup
  ON eva_recursive_workspace_snapshot_members(canonical_path,snapshot_ref)`,
  eva_artifact_collection_work_lookup:
    `CREATE INDEX eva_artifact_collection_work_lookup
  ON eva_artifact_collections(work_item_id,episode_id,origin_id,created_ts)`,
  eva_artifact_collection_member_version_lookup:
    `CREATE INDEX eva_artifact_collection_member_version_lookup
  ON eva_artifact_collection_members(version_ref,collection_ref)`,
  eva_artifact_collection_check_lookup:
    `CREATE INDEX eva_artifact_collection_check_lookup
  ON eva_artifact_collection_checks(collection_ref,checked_ts)`,
  eva_artifact_collection_consumption_lookup:
    `CREATE INDEX eva_artifact_collection_consumption_lookup
  ON eva_artifact_collection_consumptions(collection_ref,consumed_ts)`,
});

function updateTrigger(table) {
  return `CREATE TRIGGER ${table}_no_update
BEFORE UPDATE ON ${table}
BEGIN SELECT RAISE(ABORT,'artifact collection rows are append-only'); END`;
}

function deleteTrigger(table) {
  return `CREATE TRIGGER ${table}_no_delete
BEFORE DELETE ON ${table}
BEGIN SELECT RAISE(ABORT,'artifact collection rows are append-only'); END`;
}

const COLLISION_TRIGGER_SQL = Object.freeze({
  eva_recursive_workspace_snapshots_collision:
    `CREATE TRIGGER eva_recursive_workspace_snapshots_collision
BEFORE INSERT ON eva_recursive_workspace_snapshots
WHEN EXISTS (SELECT 1 FROM eva_recursive_workspace_snapshots
  WHERE snapshot_ref=NEW.snapshot_ref OR snapshot_sha256=NEW.snapshot_sha256
    OR request_sha256=NEW.request_sha256 OR operation_key=NEW.operation_key
    OR discovery_execution_key=NEW.discovery_execution_key
    OR discovery_completion_sha256=NEW.discovery_completion_sha256)
BEGIN SELECT RAISE(ABORT,'recursive workspace snapshot identity collision'); END`,
  eva_recursive_workspace_snapshot_members_collision:
    `CREATE TRIGGER eva_recursive_workspace_snapshot_members_collision
BEFORE INSERT ON eva_recursive_workspace_snapshot_members
WHEN EXISTS (SELECT 1 FROM eva_recursive_workspace_snapshot_members
  WHERE (snapshot_ref=NEW.snapshot_ref AND ordinal=NEW.ordinal)
    OR (snapshot_ref=NEW.snapshot_ref AND canonical_path=NEW.canonical_path)
    OR (snapshot_ref=NEW.snapshot_ref AND member_sha256=NEW.member_sha256))
BEGIN SELECT RAISE(ABORT,'recursive workspace snapshot member identity collision'); END`,
  eva_artifact_collections_collision:
    `CREATE TRIGGER eva_artifact_collections_collision
BEFORE INSERT ON eva_artifact_collections
WHEN EXISTS (SELECT 1 FROM eva_artifact_collections
  WHERE collection_ref=NEW.collection_ref OR collection_sha256=NEW.collection_sha256
    OR request_sha256=NEW.request_sha256 OR authority_sha256=NEW.authority_sha256
    OR operation_key=NEW.operation_key)
BEGIN SELECT RAISE(ABORT,'artifact collection identity collision'); END`,
  eva_artifact_collection_members_collision:
    `CREATE TRIGGER eva_artifact_collection_members_collision
BEFORE INSERT ON eva_artifact_collection_members
WHEN EXISTS (SELECT 1 FROM eva_artifact_collection_members
  WHERE (collection_ref=NEW.collection_ref AND ordinal=NEW.ordinal)
    OR (collection_ref=NEW.collection_ref AND canonical_path=NEW.canonical_path)
    OR (collection_ref=NEW.collection_ref AND snapshot_ref=NEW.snapshot_ref
      AND snapshot_ordinal=NEW.snapshot_ordinal)
    OR (collection_ref=NEW.collection_ref AND member_sha256=NEW.member_sha256))
BEGIN SELECT RAISE(ABORT,'artifact collection member identity collision'); END`,
  eva_artifact_collection_checks_collision:
    `CREATE TRIGGER eva_artifact_collection_checks_collision
BEFORE INSERT ON eva_artifact_collection_checks
WHEN EXISTS (SELECT 1 FROM eva_artifact_collection_checks
  WHERE check_ref=NEW.check_ref OR operation_key=NEW.operation_key)
BEGIN SELECT RAISE(ABORT,'artifact collection check identity collision'); END`,
  eva_artifact_collection_consumptions_collision:
    `CREATE TRIGGER eva_artifact_collection_consumptions_collision
BEFORE INSERT ON eva_artifact_collection_consumptions
WHEN EXISTS (SELECT 1 FROM eva_artifact_collection_consumptions
  WHERE consumption_ref=NEW.consumption_ref OR operation_key=NEW.operation_key
    OR request_sha256=NEW.request_sha256 OR check_ref=NEW.check_ref)
BEGIN SELECT RAISE(ABORT,'artifact collection consumption identity collision'); END`,
});

const REFERENCE_TRIGGER_SQL = Object.freeze({
  eva_recursive_workspace_snapshot_members_valid_reference:
    `CREATE TRIGGER eva_recursive_workspace_snapshot_members_valid_reference
BEFORE INSERT ON eva_recursive_workspace_snapshot_members
BEGIN
  SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM eva_recursive_workspace_snapshots
    WHERE snapshot_ref=NEW.snapshot_ref)
    THEN RAISE(ABORT,'snapshot member has invalid snapshot') END;
END`,
  eva_artifact_collections_valid_reference:
    `CREATE TRIGGER eva_artifact_collections_valid_reference
BEFORE INSERT ON eva_artifact_collections
BEGIN
  SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM eva_recursive_workspace_snapshots
    WHERE snapshot_ref=NEW.snapshot_ref AND snapshot_sha256=NEW.snapshot_sha256)
    THEN RAISE(ABORT,'artifact collection has invalid snapshot') END;
  SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM eva_work_items
    WHERE id=NEW.work_item_id AND revision=NEW.expected_work_revision)
    THEN RAISE(ABORT,'artifact collection has stale work') END;
  SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM eva_goal_episodes
    WHERE id=NEW.episode_id AND work_item_id=NEW.work_item_id
      AND revision=NEW.expected_episode_revision)
    THEN RAISE(ABORT,'artifact collection has stale episode') END;
  SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM eva_episode_origins
    WHERE id=NEW.origin_id AND episode_id=NEW.episode_id)
    THEN RAISE(ABORT,'artifact collection has invalid origin') END;
END`,
  eva_artifact_collection_members_valid_reference:
    `CREATE TRIGGER eva_artifact_collection_members_valid_reference
BEFORE INSERT ON eva_artifact_collection_members
BEGIN
  SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM eva_artifact_collections
    WHERE collection_ref=NEW.collection_ref)
    THEN RAISE(ABORT,'collection member has invalid collection') END;
  SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM eva_recursive_workspace_snapshot_members
    WHERE snapshot_ref=NEW.snapshot_ref AND ordinal=NEW.snapshot_ordinal
      AND canonical_path=NEW.canonical_path)
    THEN RAISE(ABORT,'collection member has invalid snapshot member') END;
END`,
  eva_artifact_collection_checks_valid_reference:
    `CREATE TRIGGER eva_artifact_collection_checks_valid_reference
BEFORE INSERT ON eva_artifact_collection_checks
BEGIN
  SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM eva_artifact_collections
    WHERE collection_ref=NEW.collection_ref AND collection_sha256=NEW.collection_sha256)
    THEN RAISE(ABORT,'artifact collection check has invalid collection') END;
END`,
  eva_artifact_collection_consumptions_valid_reference:
    `CREATE TRIGGER eva_artifact_collection_consumptions_valid_reference
BEFORE INSERT ON eva_artifact_collection_consumptions
BEGIN
  SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM eva_artifact_collection_checks
    WHERE check_ref=NEW.check_ref AND collection_ref=NEW.collection_ref
      AND collection_sha256=NEW.collection_sha256 AND state_sha256=NEW.state_sha256
      AND current=1)
    THEN RAISE(ABORT,'artifact collection consumption has invalid current check') END;
END`,
});

const OWNED_SCHEMA = new Map([
  [`table:${TABLES.snapshots}`, SNAPSHOT_TABLE_SQL],
  [`table:${TABLES.snapshotMembers}`, SNAPSHOT_MEMBER_TABLE_SQL],
  [`table:${TABLES.collections}`, COLLECTION_TABLE_SQL],
  [`table:${TABLES.collectionMembers}`, COLLECTION_MEMBER_TABLE_SQL],
  [`table:${TABLES.checks}`, CHECK_TABLE_SQL],
  [`table:${TABLES.consumptions}`, CONSUMPTION_TABLE_SQL],
]);
for (const [name, sql] of Object.entries(INDEX_SQL)) {
  OWNED_SCHEMA.set(`index:${name}`, sql);
}
for (const table of Object.values(TABLES)) {
  OWNED_SCHEMA.set(`trigger:${table}_no_update`, updateTrigger(table));
  OWNED_SCHEMA.set(`trigger:${table}_no_delete`, deleteTrigger(table));
}
for (const [name, sql] of Object.entries(COLLISION_TRIGGER_SQL)) {
  OWNED_SCHEMA.set(`trigger:${name}`, sql);
}
for (const [name, sql] of Object.entries(REFERENCE_TRIGGER_SQL)) {
  OWNED_SCHEMA.set(`trigger:${name}`, sql);
}

const DEPENDENCY_SIGNATURES = new Map([
  ['eva_work_items', [
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
  ]],
  ['eva_goal_episodes', [
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
  ]],
  ['eva_episode_origins', [
    ['id', 'INTEGER', 0, 1], ['episode_id', 'INTEGER', 1, 0],
    ['origin_key', 'TEXT', 1, 0], ['origin_kind', 'TEXT', 1, 0],
    ['origin_channel', 'TEXT', 0, 0], ['origin_conversation_id', 'TEXT', 0, 0],
    ['origin_message_id', 'TEXT', 0, 0], ['requester', 'TEXT', 0, 0],
    ['attached_ts', 'TEXT', 1, 0],
  ]],
  ['timeline', [
    ['id', 'INTEGER', 0, 1], ['ts', 'TEXT', 1, 0], ['kind', 'TEXT', 1, 0],
    ['content', 'TEXT', 1, 0], ['meta', 'TEXT', 0, 0], ['outcome_key', 'TEXT', 0, 0],
  ]],
]);

const DEPENDENCY_DEFAULTS = Object.freeze({
  eva_work_items: Object.freeze({
    status: "'open'", visible_to_family: '1', priority: '50', revision: '0',
    attempt_count: '0',
  }),
  eva_goal_episodes: Object.freeze({
    owner_actor: "'eva'", delivery_policy: "'required'", state: "'committed'", revision: '0',
  }),
  eva_episode_origins: Object.freeze({}),
  timeline: Object.freeze({}),
});

const DEPENDENCY_OBJECT_SQL = new Map([
  ['table:eva_work_items', `CREATE TABLE eva_work_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts TEXT NOT NULL, updated_ts TEXT NOT NULL,
  source TEXT NOT NULL, owner TEXT, title TEXT NOT NULL, goal TEXT NOT NULL, next_step TEXT,
  status TEXT NOT NULL DEFAULT 'open', evidence TEXT, last_action TEXT, result TEXT,
  visible_to_family INTEGER NOT NULL DEFAULT 1, definition_of_done TEXT, current_step TEXT,
  state_json TEXT, wait_json TEXT, priority INTEGER NOT NULL DEFAULT 50, next_wake_ts TEXT,
  revision INTEGER NOT NULL DEFAULT 0, lease_owner TEXT, lease_until TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0, last_error TEXT, interrupted_ts TEXT)`],
  ['index:idx_eva_work_items_status',
    'CREATE INDEX idx_eva_work_items_status ON eva_work_items(status, updated_ts)'],
  ['index:idx_eva_work_wake',
    'CREATE INDEX idx_eva_work_wake ON eva_work_items(status, next_wake_ts, priority)'],
  ['table:eva_goal_episodes', `CREATE TABLE eva_goal_episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, episode_key TEXT NOT NULL UNIQUE,
  goal_id INTEGER NOT NULL, project_id INTEGER NOT NULL, work_item_id INTEGER,
  owner_actor TEXT NOT NULL DEFAULT 'eva', origin_kind TEXT NOT NULL, origin_channel TEXT,
  origin_conversation_id TEXT, origin_message_id TEXT, requester TEXT,
  requested_outcome TEXT NOT NULL, delivery_policy TEXT NOT NULL DEFAULT 'required',
  delivery_target TEXT, state TEXT NOT NULL DEFAULT 'committed', next_action TEXT,
  checkpoint TEXT, proof_summary TEXT, wake_ts TEXT, lease_owner TEXT, lease_until TEXT,
  created_ts TEXT NOT NULL, updated_ts TEXT NOT NULL, closed_ts TEXT,
  revision INTEGER NOT NULL DEFAULT 0)`],
  ['index:idx_eva_goal_episode_one_owner', `CREATE UNIQUE INDEX idx_eva_goal_episode_one_owner
    ON eva_goal_episodes(work_item_id) WHERE work_item_id IS NOT NULL`],
  ['index:idx_eva_goal_episodes_delivery', `CREATE INDEX idx_eva_goal_episodes_delivery
    ON eva_goal_episodes(delivery_policy, state, updated_ts)`],
  ['index:idx_eva_goal_episodes_work', `CREATE INDEX idx_eva_goal_episodes_work
    ON eva_goal_episodes(work_item_id, state)`],
  ['table:eva_episode_origins', `CREATE TABLE eva_episode_origins (
  id INTEGER PRIMARY KEY AUTOINCREMENT, episode_id INTEGER NOT NULL,
  origin_key TEXT NOT NULL UNIQUE, origin_kind TEXT NOT NULL, origin_channel TEXT,
  origin_conversation_id TEXT, origin_message_id TEXT, requester TEXT, attached_ts TEXT NOT NULL)`],
  ['index:idx_eva_episode_origins_conversation', `CREATE INDEX idx_eva_episode_origins_conversation
    ON eva_episode_origins(origin_conversation_id, episode_id)`],
  ['index:idx_eva_episode_origins_episode', `CREATE INDEX idx_eva_episode_origins_episode
    ON eva_episode_origins(episode_id, attached_ts)`],
  ['table:timeline', `CREATE TABLE timeline (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL,
  content TEXT NOT NULL, meta TEXT, outcome_key TEXT)`],
  ['index:idx_timeline_contribution_outcome_key', `CREATE UNIQUE INDEX idx_timeline_contribution_outcome_key
    ON timeline(outcome_key) WHERE kind='contribution' AND outcome_key IS NOT NULL`],
  ['index:idx_timeline_kind', 'CREATE INDEX idx_timeline_kind ON timeline(kind)'],
  ['index:idx_timeline_ts', 'CREATE INDEX idx_timeline_ts ON timeline(ts)'],
  ['index:idx_timeline_understanding_conversation', `CREATE INDEX idx_timeline_understanding_conversation
    ON timeline(kind, json_extract(meta, '$.conversationId'), id)
    WHERE kind='turn_understanding'`],
]);

const DEPENDENCY_AUTOINDEXES = Object.freeze({
  eva_work_items: [],
  eva_goal_episodes: ['sqlite_autoindex_eva_goal_episodes_1'],
  eva_episode_origins: ['sqlite_autoindex_eva_episode_origins_1'],
  timeline: [],
});

const AUTO_INDEX_COUNTS = Object.freeze({
  [TABLES.snapshots]: 6,
  [TABLES.snapshotMembers]: 3,
  [TABLES.collections]: 5,
  [TABLES.collectionMembers]: 4,
  [TABLES.checks]: 2,
  [TABLES.consumptions]: 4,
});

function canonicalJson(value) {
  if (value === undefined) return undefined;
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Buffer.isBuffer(value) || value instanceof Uint8Array) {
    return canonicalJson({ bytes: Buffer.from(value).toString('base64') });
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item) ?? 'null').join(',')}]`;
  }
  return `{${Object.keys(value).sort().filter((key) => value[key] !== undefined)
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const hashJson = (value) => sha256(canonicalJson(value));
const nowIso = () => new Date().toISOString();
const plainObject = (value) => Boolean(value) && typeof value === 'object'
  && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;

function exactKeys(value, keys) {
  return plainObject(value)
    && canonicalJson(Object.keys(value).sort()) === canonicalJson([...keys].sort());
}

function deepFreeze(value) {
  if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function parsedCanonical(value, label) {
  if (typeof value !== 'string') throw new Error(`${label} is not stored as TEXT`);
  let parsed;
  try { parsed = JSON.parse(value); } catch {
    throw new Error(`${label} is not valid JSON`);
  }
  if (canonicalJson(parsed) !== value) {
    throw new Error(`${label} is not canonically stored`);
  }
  return parsed;
}

function normalizedSchemaSql(value) {
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
      if (source[index] !== "'") { index += 1; continue; }
      if (source[index + 1] === "'") { index += 2; continue; }
      index += 1;
      break;
    }
    const literalIndex = literals.push(source.slice(start, index)) - 1;
    masked += `\u{e000}${literalIndex}\u{e001}`;
  }
  const normalized = masked.replace(/--[^\r\n]*/gu, ' ')
    .replaceAll('"', '').replaceAll('`', '')
    .replaceAll('[', '').replaceAll(']', '')
    .replace(/\s+/gu, ' ').replace(/\s*([(),;=])\s*/gu, '$1')
    .replace(/;+$/u, '').trim().toLowerCase();
  return normalized.replace(/\u{e000}(\d+)\u{e001}/gu, (
    _match, literalIndex,
  ) => literals[Number(literalIndex)]);
}

function quotedIdentifier(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function ownedObject(row) {
  const name = String(row.name || '').toLowerCase();
  const table = String(row.tbl_name || '').toLowerCase();
  return name.startsWith('eva_recursive_workspace_')
    || name.startsWith('eva_artifact_collection')
    || table.startsWith('eva_recursive_workspace_')
    || table.startsWith('eva_artifact_collection');
}

function ownedSchemaRows(database, schema) {
  const source = schema === 'temp' ? 'sqlite_temp_master' : 'main.sqlite_master';
  return database.prepare(`SELECT type,name,tbl_name,sql FROM ${source}
    WHERE sql IS NOT NULL ORDER BY type,name`).all().filter(ownedObject);
}

function temporaryShadowProblem(database) {
  const protectedNames = new Set([
    'turn_tool_executions', ...DEPENDENCY_SIGNATURES.keys(), ...Object.values(TABLES),
    ...ARTIFACT_PROTECTED_TABLES,
  ].map((name) => name.toLowerCase()));
  const row = database.prepare(`SELECT type,name,tbl_name FROM sqlite_temp_master
    ORDER BY type,name`).all().find((candidate) => {
    const name = String(candidate.name || '').toLowerCase();
    const table = String(candidate.tbl_name || '').toLowerCase();
    return name.startsWith('eva_recursive_workspace_')
      || name.startsWith('eva_artifact_collection')
      || protectedNames.has(name) || protectedNames.has(table);
  });
  return row ? `temporary schema shadows protected object ${row.type}:${row.name}` : null;
}

function exactDependencyProblem(database) {
  const temporary = temporaryShadowProblem(database);
  if (temporary) return temporary;
  const executionStatus = executionLedgerStatus(database);
  if (!executionStatus.available) return executionStatus.reason;
  for (const [table, expected] of DEPENDENCY_SIGNATURES) {
    const stored = database.prepare(`SELECT name FROM main.sqlite_master
      WHERE type='table' AND lower(name)=lower(?)`).all(table);
    if (stored.length !== 1 || stored[0].name !== table) {
      return `required dependency table ${table} is missing or inexact`;
    }
    const actual = database.prepare(`PRAGMA main.table_xinfo(${quotedIdentifier(table)})`).all()
      .map((column) => [
        String(column.name), String(column.type).toUpperCase(),
        Number(column.notnull), column.dflt_value == null ? null : String(column.dflt_value),
        Number(column.pk), Number(column.hidden),
      ]);
    const defaults = DEPENDENCY_DEFAULTS[table];
    const expectedExact = expected.map(([name, type, notnull, pk]) => [
      name, type, notnull, defaults[name] ?? null, pk, 0,
    ]);
    if (canonicalJson(actual) !== canonicalJson(expectedExact)) {
      return `required dependency table ${table} has an invalid schema`;
    }
    const actualAutoindexes = database.prepare(
      `PRAGMA main.index_list(${quotedIdentifier(table)})`,
    ).all().filter((index) => String(index.origin) !== 'c')
      .map((index) => String(index.name)).sort();
    if (canonicalJson(actualAutoindexes)
      !== canonicalJson([...DEPENDENCY_AUTOINDEXES[table]].sort())) {
      return `required dependency table ${table} has invalid automatic indexes`;
    }
  }
  const dependencyNames = new Set(DEPENDENCY_SIGNATURES.keys());
  const rows = database.prepare(`SELECT type,name,tbl_name,sql FROM main.sqlite_master
    WHERE sql IS NOT NULL ORDER BY type,name`).all().filter((row) => (
    dependencyNames.has(String(row.tbl_name))
  ));
  const actualObjects = new Map(rows.map((row) => [`${row.type}:${row.name}`, String(row.sql)]));
  const expectedObjects = DEPENDENCY_OBJECT_SQL;
  if (actualObjects.size !== expectedObjects.size
    || [...expectedObjects.keys()].some((key) => !actualObjects.has(key))) {
    return 'required dependency schema objectset is inexact';
  }
  for (const [key, sql] of expectedObjects) {
    if (normalizedSchemaSql(actualObjects.get(key)) !== normalizedSchemaSql(sql)) {
      return `required dependency schema SQL is inexact for ${key}`;
    }
  }
  return null;
}

function exactAutoindexes(database) {
  return Object.entries(AUTO_INDEX_COUNTS).every(([table, count]) => {
    const expected = Array.from(
      { length: count },
      (_unused, index) => `sqlite_autoindex_${table}_${index + 1}`,
    );
    const actual = database.prepare(`PRAGMA main.index_list(${quotedIdentifier(table)})`).all()
      .filter((row) => String(row.origin) !== 'c')
      .map((row) => String(row.name)).sort();
    return canonicalJson(actual) === canonicalJson(expected.sort());
  });
}

function exactIndexKeys(database) {
  const custom = Object.keys(INDEX_SQL).sort();
  const actualCustom = database.prepare(`SELECT name FROM main.sqlite_master
    WHERE type='index' AND sql IS NOT NULL ORDER BY name`).all()
    .filter((row) => String(row.name).toLowerCase().startsWith('eva_artifact_collection')
      || String(row.name).toLowerCase().startsWith('eva_recursive_workspace_'))
    .map((row) => String(row.name));
  if (canonicalJson(actualCustom) !== canonicalJson(custom)) return false;
  return custom.every((name) => {
    const expectedSql = INDEX_SQL[name];
    const stored = database.prepare(`SELECT sql FROM main.sqlite_master
      WHERE type='index' AND name=?`).get(name)?.sql;
    if (normalizedSchemaSql(stored) !== normalizedSchemaSql(expectedSql)) return false;
    const expectedKeys = expectedSql.slice(expectedSql.indexOf('(') + 1, expectedSql.lastIndexOf(')'))
      .split(',').map((key) => key.trim());
    const actualKeys = database.prepare(`PRAGMA main.index_info(${quotedIdentifier(name)})`).all()
      .map((row) => String(row.name));
    return canonicalJson(actualKeys) === canonicalJson(expectedKeys);
  });
}

function ownedSchemaProblem(database) {
  try {
    if (!database?.open) return 'database is closed';
    const temporary = temporaryShadowProblem(database);
    if (temporary) return temporary;
    const mainRows = ownedSchemaRows(database, 'main');
    const tempRows = ownedSchemaRows(database, 'temp');
    if (tempRows.length) return 'unexpected owned schema object in temporary schema';
    const actual = new Map(mainRows.map((row) => [`${row.type}:${row.name}`, row]));
    const missing = [...OWNED_SCHEMA.keys()].find((key) => !actual.has(key));
    if (missing) return `owned schema is incomplete: missing ${missing}`;
    const unexpected = [...actual.keys()].find((key) => !OWNED_SCHEMA.has(key));
    if (unexpected) return `unexpected owned schema object ${unexpected}`;
    for (const [key, expectedSql] of OWNED_SCHEMA) {
      if (normalizedSchemaSql(actual.get(key)?.sql) !== normalizedSchemaSql(expectedSql)) {
        return `owned schema SQL mismatch for ${key}`;
      }
    }
    if (!exactAutoindexes(database)) return 'owned schema autoindex objectset changed';
    if (!exactIndexKeys(database)) return 'owned schema index signatures changed';
    return null;
  } catch (error) {
    return `owned schema inspection failed: ${error?.message || error}`;
  }
}

function initializeSchema(database) {
  try {
    if (!database?.open || exactDependencyProblem(database)) return;
    const initialize = database.transaction(() => {
      if (ownedSchemaRows(database, 'main').length || ownedSchemaRows(database, 'temp').length) {
        return;
      }
      database.exec(`${[...OWNED_SCHEMA.values()].join(';\n')};`);
    });
    initialize.immediate();
  } catch { /* status() reports the exact fail-closed cause */ }
}

function schemaObjectRecord(database, table) {
  const objects = database.prepare(`SELECT type,name,tbl_name,sql FROM main.sqlite_master
    WHERE tbl_name=? OR (type='table' AND name=?) ORDER BY type,name`).all(table, table)
    .map((row) => ({
      type: String(row.type), name: String(row.name), table: String(row.tbl_name),
      sql: row.sql == null ? null : String(row.sql),
    }));
  const columns = database.prepare(`PRAGMA main.table_xinfo(${quotedIdentifier(table)})`).all()
    .map((column) => ({
      cid: Number(column.cid), name: String(column.name), type: String(column.type),
      notnull: Number(column.notnull), defaultValue: column.dflt_value,
      pk: Number(column.pk), hidden: Number(column.hidden),
    }));
  const foreignKeys = database.prepare(
    `PRAGMA main.foreign_key_list(${quotedIdentifier(table)})`,
  ).all().map((entry) => ({
    id: Number(entry.id), seq: Number(entry.seq), table: String(entry.table),
    from: String(entry.from), to: String(entry.to), onUpdate: String(entry.on_update),
    onDelete: String(entry.on_delete), match: String(entry.match),
  }));
  const indexes = database.prepare(`PRAGMA main.index_list(${quotedIdentifier(table)})`).all()
    .map((index) => ({
      name: String(index.name), unique: Number(index.unique), origin: String(index.origin),
      partial: Number(index.partial),
      keys: database.prepare(`PRAGMA main.index_xinfo(${quotedIdentifier(index.name)})`).all()
        .map((key) => ({
          seqno: Number(key.seqno), cid: Number(key.cid),
          name: key.name == null ? null : String(key.name), desc: Number(key.desc),
          coll: String(key.coll || ''), key: Number(key.key),
        })),
    })).sort((left, right) => canonicalJson(left).localeCompare(canonicalJson(right)));
  return { table, columns, foreignKeys, objects, indexes };
}

function dependencySchemaSha256(database) {
  const tables = [...DEPENDENCY_SIGNATURES.keys()].map((table) => (
    schemaObjectRecord(database, table)
  ));
  const executionLedger = executionLedgerSchemaManifest(database);
  return hashJson({
    contract: 'ArtifactCollectionDependencySchema/v1', tables, executionLedger,
  });
}

function mainSchemaVersion(database) {
  const version = Number(database.prepare('PRAGMA main.schema_version').get()?.schema_version);
  if (!Number.isSafeInteger(version) || version < 0) {
    throw new Error('main schema version is unavailable');
  }
  return version;
}

function normalizedLimits(options) {
  const limits = {};
  for (const field of Object.keys(DEFAULT_LIMITS)) {
    const value = options[field] === undefined ? DEFAULT_LIMITS[field] : options[field];
    if (typeof value !== 'number' || !Number.isSafeInteger(value)
      || value < 1 || value > HARD_LIMITS[field]) {
      throw new TypeError(`${field} must be between 1 and ${HARD_LIMITS[field]}`);
    }
    limits[field] = value;
  }
  return Object.freeze(limits);
}

function inventoryError(code, logicalPath = null) {
  return Object.assign(new Error(`workspace inventory incomplete: ${code}${
    logicalPath ? ` (${logicalPath})` : ''}`), { code });
}

function strictUtf8Name(buffer, logicalParent) {
  const name = Buffer.from(buffer).toString('utf8');
  if (!Buffer.from(name, 'utf8').equals(Buffer.from(buffer))) {
    throw inventoryError('invalid_utf8_path', logicalParent);
  }
  const hasControlCharacter = [...name].some((character) => {
    const code = character.codePointAt(0);
    return code <= 31 || code === 127 || (character !== ' ' && /\s/u.test(character));
  });
  if (!name || name === '.' || name === '..' || name.includes('/') || name.includes('\\')
    || name.includes(String.fromCharCode(0)) || name.normalize('NFC') !== name
    || name.trim() !== name || hasControlCharacter || Buffer.byteLength(name, 'utf8') > 255) {
    throw inventoryError('unsafe_path', `${logicalParent}/${name}`);
  }
  return name;
}

function stableStatIdentity(stat) {
  return {
    contract: 'WorkspaceStatIdentity/v1',
    device: String(stat.dev),
    inode: String(stat.ino),
    mode: Number(stat.mode),
    links: String(stat.nlink),
    user: String(stat.uid),
    group: String(stat.gid),
    size: String(stat.size),
    modifiedNs: String(stat.mtimeNs),
    changedNs: String(stat.ctimeNs),
  };
}

function durableDirectoryIdentity(stat) {
  return {
    contract: 'WorkspaceDirectoryStatIdentity/v1',
    device: String(stat.dev),
    inode: String(stat.ino),
    mode: Number(stat.mode),
    links: String(stat.nlink),
    user: String(stat.uid),
    group: String(stat.gid),
  };
}

function sameStat(left, right) {
  return canonicalJson(stableStatIdentity(left)) === canonicalJson(stableStatIdentity(right));
}

function pathOrder(left, right) {
  return Buffer.compare(Buffer.from(left, 'utf8'), Buffer.from(right, 'utf8'));
}

function descriptorPath(descriptor, name = '') {
  const base = `/proc/self/fd/${descriptor}`;
  return name ? `${base}/${name}` : base;
}

function directoryEntries(descriptor, logicalPath, state, countEntries = true) {
  let directory;
  const entries = [];
  try {
    directory = fs.opendirSync(descriptorPath(descriptor), {
      encoding: /** @type {BufferEncoding} */ ('buffer'),
    });
    for (;;) {
      const entry = directory.readSync();
      if (!entry) break;
      if (countEntries) state.entryCount += 1;
      if (countEntries
        && state.entryCount > state.limits.maxFiles + state.limits.maxDirectories) {
        throw inventoryError('entry_limit', logicalPath);
      }
      const raw = Buffer.from(entry.name);
      entries.push({ raw, name: strictUtf8Name(raw, logicalPath) });
    }
  } catch (error) {
    if (error?.message?.startsWith('workspace inventory incomplete:')) throw error;
    throw inventoryError('io_error', logicalPath);
  } finally {
    try { directory?.closeSync(); } catch { /* scanner fails on the original cause */ }
  }
  entries.sort((left, right) => Buffer.compare(left.raw, right.raw));
  if (new Set(entries.map((entry) => entry.name)).size !== entries.length) {
    throw inventoryError('ambiguous_path_encoding', logicalPath);
  }
  return entries.map((entry) => entry.name);
}

function openChild(parentDescriptor, name, flags, logicalPath) {
  let descriptor;
  try {
    descriptor = fs.openSync(descriptorPath(parentDescriptor, name), flags);
    return descriptor;
  } catch (error) {
    if (error?.code === 'ELOOP') throw inventoryError('symbolic_link', logicalPath);
    throw inventoryError('workspace_changed', logicalPath);
  }
}

function readBoundedFile(descriptor, size, logicalPath, maxBytes) {
  if (!Number.isSafeInteger(size) || size < 0 || size > maxBytes) {
    throw inventoryError(size > maxBytes ? 'byte_limit' : 'unsafe_file_size', logicalPath);
  }
  const content = Buffer.alloc(size);
  let offset = 0;
  while (offset < size) {
    let read;
    try { read = fs.readSync(descriptor, content, offset, size - offset, offset); } catch {
      throw inventoryError('io_error', logicalPath);
    }
    if (read === 0) throw inventoryError('workspace_changed', logicalPath);
    offset += read;
  }
  const extra = Buffer.alloc(1);
  let extraRead;
  try { extraRead = fs.readSync(descriptor, extra, 0, 1, size); } catch {
    throw inventoryError('io_error', logicalPath);
  }
  if (extraRead !== 0) throw inventoryError('workspace_changed', logicalPath);
  return content;
}

function physicalWorkspaceRoot(value) {
  if (typeof value !== 'string' || !value || !path.isAbsolute(value)) {
    throw new TypeError('an explicit absolute workspace root is required');
  }
  const supplied = path.normalize(value);
  const pinnedDescriptorRoot = /^\/proc\/self\/fd\/[1-9][0-9]*$/u.test(supplied);
  // O_NOFOLLOW moet de laatste component controleren. `/.` dereferentieert de
  // reeds retained dirfd als tussencomponent en maakt de laatste component een
  // echte directory, zonder opnieuw het verwisselbare oorspronkelijke pad te
  // gebruiken.
  const accessPath = pinnedDescriptorRoot ? `${supplied}/.` : supplied;
  let lstat;
  let real;
  try {
    lstat = fs.lstatSync(accessPath, { bigint: true });
    real = fs.realpathSync.native(accessPath);
  } catch { throw new TypeError('workspace root must exist'); }
  if (lstat.isSymbolicLink() || !lstat.isDirectory()
    || (!pinnedDescriptorRoot && path.normalize(real) !== supplied)) {
    throw new TypeError('workspace root must be its physical non-symlink realpath');
  }
  const identity = {
    contract: 'WorkspaceRootObjectIdentity/v1',
    device: String(lstat.dev), inode: String(lstat.ino), mode: Number(lstat.mode),
    user: String(lstat.uid), group: String(lstat.gid),
  };
  return Object.freeze({
    path: pinnedDescriptorRoot ? accessPath : real,
    realpath: real,
    identity: deepFreeze(identity),
    identitySha256: hashJson(identity),
    realpathSha256: sha256(Buffer.from(real, 'utf8')),
  });
}

function scanWorkspace(root, limits) {
  if (!fs.constants.O_DIRECTORY || !fs.constants.O_NOFOLLOW
    || !fs.existsSync('/proc/self/fd')) {
    throw inventoryError('secure_descriptor_walk_unavailable', LOGICAL_ROOT);
  }
  const state = {
    limits, entryCount: 0, totalBytes: 0, members: [], directories: [], files: [],
  };
  const directoryFlags = fs.constants.O_RDONLY | fs.constants.O_DIRECTORY
    | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK;
  const fileFlags = fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW
    | fs.constants.O_NONBLOCK;

  const inspectDirectory = (descriptor, logicalPath, depth, parent = null, name = null) => {
    if (depth > limits.maxDepth) throw inventoryError('depth_limit', logicalPath);
    if (state.directories.length >= limits.maxDirectories) {
      throw inventoryError('directory_limit', logicalPath);
    }
    let before;
    try { before = fs.fstatSync(descriptor, { bigint: true }); } catch {
      throw inventoryError('io_error', logicalPath);
    }
    if (!before.isDirectory()) throw inventoryError('not_a_directory', logicalPath);
    const names = directoryEntries(descriptor, logicalPath, state);
    const directory = { descriptor, logicalPath, depth, parent, name, stat: before, names };
    state.directories.push(directory);

    for (const childName of names) {
      const logicalChild = `${logicalPath}/${childName}`;
      if (Buffer.byteLength(logicalChild, 'utf8') > 4_096) {
        throw inventoryError('unsafe_path', logicalChild);
      }
      let observed;
      try {
        observed = fs.lstatSync(descriptorPath(descriptor, childName), { bigint: true });
      } catch { throw inventoryError('workspace_changed', logicalChild); }
      if (observed.isSymbolicLink()) throw inventoryError('symbolic_link', logicalChild);
      if (observed.isDirectory()) {
        const childDescriptor = openChild(descriptor, childName, directoryFlags, logicalChild);
        state.openDescriptors.push(childDescriptor);
        const exact = fs.fstatSync(childDescriptor, { bigint: true });
        if (!sameStat(observed, exact)) throw inventoryError('workspace_changed', logicalChild);
        inspectDirectory(childDescriptor, logicalChild, depth + 1, descriptor, childName);
        continue;
      }
      if (!observed.isFile()) throw inventoryError('unsupported_file_type', logicalChild);
      if (String(observed.nlink) !== '1') throw inventoryError('hard_link', logicalChild);
      if (state.members.length >= limits.maxFiles) throw inventoryError('file_limit', logicalChild);
      const size = Number(observed.size);
      if (!Number.isSafeInteger(size) || size < 0) {
        throw inventoryError('unsafe_file_size', logicalChild);
      }
      if (size > limits.maxBytes - state.totalBytes) {
        throw inventoryError('byte_limit', logicalChild);
      }
      const fileDescriptor = openChild(descriptor, childName, fileFlags, logicalChild);
      state.openDescriptors.push(fileDescriptor);
      const descriptorBefore = fs.fstatSync(fileDescriptor, { bigint: true });
      if (!descriptorBefore.isFile() || String(descriptorBefore.nlink) !== '1'
        || !sameStat(observed, descriptorBefore)) {
        throw inventoryError('workspace_changed', logicalChild);
      }
      const content = readBoundedFile(fileDescriptor, size, logicalChild, limits.maxBytes);
      const descriptorAfter = fs.fstatSync(fileDescriptor, { bigint: true });
      if (!sameStat(descriptorBefore, descriptorAfter)) {
        throw inventoryError('workspace_changed', logicalChild);
      }
      state.totalBytes += content.length;
      const statIdentity = stableStatIdentity(descriptorAfter);
      const base = {
        contract: 'RecursiveWorkspaceSnapshotMember/v1', ordinal: -1,
        path: logicalChild, sha256: sha256(content), bytes: content.length,
        statIdentity, statIdentitySha256: hashJson(statIdentity),
      };
      state.members.push(base);
      state.files.push({
        descriptor: fileDescriptor, parentDescriptor: descriptor, name: childName,
        path: logicalChild, stat: descriptorAfter, sha256: base.sha256, size,
      });
    }
    const afterNames = directoryEntries(descriptor, logicalPath, state, false);
    const after = fs.fstatSync(descriptor, { bigint: true });
    if (canonicalJson(names) !== canonicalJson(afterNames) || !sameStat(before, after)) {
      throw inventoryError('workspace_changed', logicalPath);
    }
  };

  state.openDescriptors = [];
  try {
    let currentRoot;
    let currentRealpath;
    try {
      currentRoot = fs.lstatSync(root.path, { bigint: true });
      currentRealpath = fs.realpathSync.native(root.path);
    } catch { throw inventoryError('root_missing', LOGICAL_ROOT); }
    if (currentRoot.isSymbolicLink() || !currentRoot.isDirectory()
      || currentRealpath !== root.realpath
      || hashJson({
        contract: 'WorkspaceRootObjectIdentity/v1',
        device: String(currentRoot.dev), inode: String(currentRoot.ino),
        mode: Number(currentRoot.mode), user: String(currentRoot.uid),
        group: String(currentRoot.gid),
      }) !== root.identitySha256) {
      throw inventoryError('workspace_root_changed', LOGICAL_ROOT);
    }
    const rootDescriptor = fs.openSync(root.path, directoryFlags);
    state.openDescriptors.push(rootDescriptor);
    const descriptorRoot = fs.fstatSync(rootDescriptor, { bigint: true });
    if (!sameStat(currentRoot, descriptorRoot)) {
      throw inventoryError('workspace_root_changed', LOGICAL_ROOT);
    }
    inspectDirectory(rootDescriptor, LOGICAL_ROOT, 0);

    for (const file of state.files) {
      const held = fs.fstatSync(file.descriptor, { bigint: true });
      if (!sameStat(held, file.stat)) throw inventoryError('workspace_changed', file.path);
      const content = readBoundedFile(file.descriptor, file.size, file.path, limits.maxBytes);
      if (sha256(content) !== file.sha256) throw inventoryError('workspace_changed', file.path);
      const reopened = openChild(file.parentDescriptor, file.name, fileFlags, file.path);
      try {
        const rebound = fs.fstatSync(reopened, { bigint: true });
        if (!rebound.isFile() || !sameStat(rebound, file.stat)) {
          throw inventoryError('workspace_changed', file.path);
        }
      } finally { fs.closeSync(reopened); }
    }
    for (const directory of state.directories) {
      const names = directoryEntries(
        directory.descriptor, directory.logicalPath, state, false,
      );
      const stat = fs.fstatSync(directory.descriptor, { bigint: true });
      if (canonicalJson(names) !== canonicalJson(directory.names)
        || !sameStat(stat, directory.stat)) {
        throw inventoryError('workspace_changed', directory.logicalPath);
      }
      if (directory.parent != null) {
        const rebound = openChild(
          directory.parent, directory.name, directoryFlags, directory.logicalPath,
        );
        try {
          if (!sameStat(fs.fstatSync(rebound, { bigint: true }), directory.stat)) {
            throw inventoryError('workspace_changed', directory.logicalPath);
          }
        } finally { fs.closeSync(rebound); }
      }
    }

    let reboundRoot;
    let reboundRootRealpath;
    try {
      reboundRoot = fs.lstatSync(root.path, { bigint: true });
      reboundRootRealpath = fs.realpathSync.native(root.path);
    } catch { throw inventoryError('workspace_root_changed', LOGICAL_ROOT); }
    if (reboundRoot.isSymbolicLink() || !reboundRoot.isDirectory()
      || reboundRootRealpath !== root.realpath || !sameStat(reboundRoot, descriptorRoot)) {
      throw inventoryError('workspace_root_changed', LOGICAL_ROOT);
    }
    const reboundRootDescriptor = fs.openSync(root.path, directoryFlags);
    try {
      if (!sameStat(fs.fstatSync(reboundRootDescriptor, { bigint: true }), descriptorRoot)) {
        throw inventoryError('workspace_root_changed', LOGICAL_ROOT);
      }
    } finally { fs.closeSync(reboundRootDescriptor); }

    const members = state.members.sort((left, right) => pathOrder(left.path, right.path))
      .map((member, ordinal) => {
        const base = { ...member, ordinal };
        return { ...base, memberSha256: hashJson(base) };
      });
    const directories = state.directories.map((directory) => {
      // ctime/mtime/size blijven onderdeel van de scan-interne racefence via
      // sameStat(). Ze horen niet in de duurzame inhoudsidentiteit: db.js
      // herbevestigt bij iedere start veilig dezelfde rootmode met chmod,
      // wat alleen ctime wijzigt. Exacte entries en object-id blijven gebonden.
      const statIdentity = durableDirectoryIdentity(directory.stat);
      return {
        contract: 'RecursiveWorkspaceDirectoryIdentity/v1',
        path: directory.logicalPath,
        statIdentity,
        statIdentitySha256: hashJson(statIdentity),
        entriesSha256: hashJson(directory.names),
      };
    }).sort((left, right) => pathOrder(left.path, right.path));
    const rootIdentity = {
      contract: 'PhysicalWorkspaceRootIdentity/v1',
      statIdentity: root.identity,
      statIdentitySha256: root.identitySha256,
      realpathSha256: root.realpathSha256,
    };
    const body = {
      contract: 'RecursiveWorkspaceDiscoveryReceipt/v1', root: LOGICAL_ROOT,
      rootIdentity, selectionKind: 'all_regular_files', query: null,
      recursive: true, followSymlinks: false, complete: true, truncated: false,
      ignoredDirectories: [], issues: [], limits: { ...limits },
      totalFiles: members.length, totalBytes: state.totalBytes, directories, members,
    };
    const receipt = { ...body, manifestSha256: hashJson(body) };
    if (Buffer.byteLength(canonicalJson(receipt), 'utf8') > limits.maxManifestBytes) {
      throw inventoryError('manifest_limit', LOGICAL_ROOT);
    }
    return deepFreeze(receipt);
  } catch (error) {
    if (error?.message?.startsWith('workspace inventory incomplete:')) throw error;
    throw inventoryError('io_error', LOGICAL_ROOT);
  } finally {
    for (const descriptor of [...state.openDescriptors].reverse()) {
      try { fs.closeSync(descriptor); } catch { /* descriptor is already closed */ }
    }
  }
}

function validPositiveId(value, field) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1) {
    throw new TypeError(`${field} must be a positive safe integer`);
  }
  return value;
}

function validRevision(value, field) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
    throw new TypeError(`${field} must be a non-negative safe integer`);
  }
  return value;
}

function validOperationKey(value) {
  if (typeof value !== 'string' || !OPERATION_KEY.test(value)) {
    throw new TypeError('operationKey is invalid');
  }
  return value;
}

function boundedString(value, field, { nullable = false, bytes = MAX_TEXT_BYTES } = {}) {
  if (value == null && nullable) return null;
  if (typeof value !== 'string') throw new Error(`${field} must be a string`);
  const text = value;
  if ((!nullable && !text) || text.includes(String.fromCharCode(0))
    || Buffer.byteLength(text, 'utf8') > bytes) {
    throw new Error(`${field} is missing or exceeds its byte boundary`);
  }
  return text;
}

function exactWorkspacePath(value) {
  if (typeof value !== 'string') {
    throw new TypeError('path must be an exact canonical data/workspace path');
  }
  const supplied = value;
  const parts = supplied.split('/');
  const invalidPart = parts.some((part) => !part || part === '.' || part === '..'
    || part.normalize('NFC') !== part || part.trim() !== part
    || Buffer.byteLength(part, 'utf8') > 255
    || [...part].some((character) => {
      const code = character.codePointAt(0);
      return code <= 31 || code === 127 || (character !== ' ' && /\s/u.test(character));
    }));
  if (!supplied.startsWith(`${LOGICAL_ROOT}/`) || supplied.includes('\\')
    || supplied.includes(String.fromCharCode(0)) || path.posix.normalize(supplied) !== supplied
    || invalidPart
    || Buffer.byteLength(supplied, 'utf8') > 4_096) {
    throw new TypeError('path must be an exact canonical data/workspace path');
  }
  return supplied;
}

function outputMaterialSha256(output) {
  if (typeof output !== 'string') return null;
  try {
    const parsed = JSON.parse(output);
    const candidate = parsed?.data?.material_sha256 || parsed?.artifact_receipt?.sha256 || null;
    return typeof candidate === 'string' && SHA256.test(candidate) ? candidate : null;
  } catch { return null; }
}

function durableExecution(database, executionKey) {
  if (typeof executionKey !== 'string' || !EXECUTION_KEY.test(executionKey)) {
    throw new Error('durable execution key is invalid');
  }
  const row = database.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(executionKey);
  if (!row) throw new Error('durable execution is missing');
  let args;
  let binding;
  try {
    args = row.args == null ? null : JSON.parse(row.args);
    binding = row.semantic_binding == null ? null : JSON.parse(row.semantic_binding);
  } catch { throw new Error('durable execution contains invalid intent JSON'); }
  if (canonicalJson(args) !== row.args
    || (binding == null ? row.semantic_binding != null
      : canonicalJson(binding) !== row.semantic_binding)) {
    throw new Error('durable execution intent is not canonical');
  }
  const semanticHash = hashJson({ turnId: row.turn_id, tool: row.tool, args, binding });
  const intentBody = {
    turnId: row.turn_id, tool: row.tool, args, binding, semanticHash,
  };
  const intentRecordSha256 = row.execution_contract === 'ExecutionRecord/v1'
    ? hashJson(intentBody)
    : row.execution_contract === 'ExecutionRecord/v2'
      ? hashJson({
        executionContract: row.execution_contract,
        executionSeq: Number(row.execution_seq),
        ...intentBody,
      })
      : null;
  if (row.execution_verification !== 'verified'
    || row.semantic_hash !== semanticHash || row.intent_record_sha256 !== intentRecordSha256
    || row.status !== 'done' || !row.finished_ts || row.output == null) {
    throw new Error('durable execution intent or completion state is invalid');
  }
  if (typeof row.output !== 'string') throw new Error('durable execution output is not TEXT');
  const outputSha256 = sha256(row.output);
  const materialSha256 = outputMaterialSha256(row.output);
  const completionBody = {
    executionKey: row.execution_key, turnId: row.turn_id, tool: row.tool,
    args: row.args, semanticHash: row.semantic_hash,
    semanticBinding: row.semantic_binding,
    intentRecordSha256: row.intent_record_sha256, status: row.status,
    startedTs: row.started_ts, finishedTs: row.finished_ts,
    outputSha256: row.output_sha256, materialSha256: row.material_sha256,
  };
  const completionRecordSha256 = row.execution_contract === 'ExecutionRecord/v1'
    ? hashJson(completionBody)
    : row.execution_contract === 'ExecutionRecord/v2'
      ? hashJson({
        executionContract: row.execution_contract,
        executionSeq: Number(row.execution_seq),
        ...completionBody,
      })
      : null;
  if (row.output_sha256 !== outputSha256 || (row.material_sha256 || null) !== materialSha256
    || row.completion_record_sha256 !== completionRecordSha256) {
    throw new Error('durable execution completion hash is invalid');
  }
  return { row, args, binding };
}

function discoveryRequest(limits) {
  return {
    contract: 'RecursiveWorkspaceDiscoveryRequest/v1', root: LOGICAL_ROOT,
    selectionKind: 'all_regular_files', query: null, recursive: true,
    followSymlinks: false, limits: { ...limits },
  };
}

function discoveryObservation(root, limits) {
  return deepFreeze({
    contract: 'RecursiveWorkspaceDiscovery/v1', tool: DISCOVERY_TOOL,
    args: discoveryRequest(limits), receipt: scanWorkspace(root, limits),
  });
}

function validDiscoveryReceipt(value, limits, root) {
  if (!exactKeys(value, [
    'complete', 'contract', 'directories', 'followSymlinks', 'ignoredDirectories',
    'issues', 'limits', 'manifestSha256', 'members', 'query', 'recursive', 'root',
    'rootIdentity', 'selectionKind', 'totalBytes', 'totalFiles', 'truncated',
  ]) || value.contract !== 'RecursiveWorkspaceDiscoveryReceipt/v1'
    || value.root !== LOGICAL_ROOT || value.selectionKind !== 'all_regular_files'
    || value.query !== null || value.recursive !== true || value.followSymlinks !== false
    || value.complete !== true || value.truncated !== false
    || canonicalJson(value.ignoredDirectories) !== '[]'
    || canonicalJson(value.issues) !== '[]'
    || canonicalJson(value.limits) !== canonicalJson(limits)
    || !Array.isArray(value.members) || !Array.isArray(value.directories)
    || typeof value.totalFiles !== 'number' || !Number.isSafeInteger(value.totalFiles)
    || value.totalFiles !== value.members.length
    || typeof value.totalBytes !== 'number' || !Number.isSafeInteger(value.totalBytes)
    || value.totalBytes < 0
    || value.rootIdentity?.contract !== 'PhysicalWorkspaceRootIdentity/v1'
    || value.rootIdentity?.statIdentitySha256 !== root.identitySha256
    || value.rootIdentity?.realpathSha256 !== root.realpathSha256
    || typeof value.manifestSha256 !== 'string' || !SHA256.test(value.manifestSha256)) {
    throw new Error('recursive workspace discovery manifest is invalid');
  }
  const { manifestSha256, ...body } = value;
  if (hashJson(body) !== manifestSha256
    || Buffer.byteLength(canonicalJson(value), 'utf8') > limits.maxManifestBytes) {
    throw new Error('recursive workspace discovery manifest hash or size is invalid');
  }
  return deepFreeze(value);
}

function durableDiscovery(database, attestation, limits, root) {
  const observed = durableExecution(database, attestation.discoveryExecutionKey);
  if (observed.row.tool !== DISCOVERY_TOOL || observed.binding !== null
    || canonicalJson(observed.args) !== canonicalJson(discoveryRequest(limits))
    || observed.row.completion_record_sha256 !== attestation.discoveryCompletionSha256) {
    throw new Error('recursive workspace discovery execution is not exact');
  }
  return validDiscoveryReceipt(parsedCanonical(
    observed.row.output, 'recursive workspace discovery output',
  ), limits, root);
}

function normalizeSnapshotAuthority(value) {
  if (!exactKeys(value, [
    'contract', 'discoveryCompletionSha256', 'discoveryExecutionKey',
  ]) || value.contract !== 'RecursiveWorkspaceSnapshotAuthority/v1'
    || typeof value.discoveryExecutionKey !== 'string'
    || !EXECUTION_KEY.test(value.discoveryExecutionKey)
    || typeof value.discoveryCompletionSha256 !== 'string'
    || !SHA256.test(value.discoveryCompletionSha256)) {
    throw new TypeError('invalid RecursiveWorkspaceSnapshotAuthority/v1');
  }
  return {
    contract: value.contract,
    discoveryExecutionKey: value.discoveryExecutionKey,
    discoveryCompletionSha256: value.discoveryCompletionSha256,
  };
}

function exactPredicate(value) {
  if (!exactKeys(value, Object.keys(PREDICATE))
    || canonicalJson(value) !== canonicalJson(PREDICATE)) {
    throw new TypeError('artifact collection predicate is not the bounded host predicate');
  }
  return { ...PREDICATE };
}

function normalizedClassification(value) {
  if (!exactKeys(value, ['classification', 'path', 'reason', 'versionRef'])
    || !exactKeys(value.reason, ['code', 'contract'])
    || value.reason.contract !== 'ArtifactCollectionMemberReason/v1') {
    throw new TypeError('artifact collection classification is invalid');
  }
  const itemPath = exactWorkspacePath(value.path);
  const included = value.classification === 'included';
  const excluded = value.classification === 'excluded';
  if ((!included && !excluded)
    || (included && (value.reason.code !== 'current_artifact_version'
      || typeof value.versionRef !== 'string' || !VERSION_REF.test(value.versionRef)))
    || (excluded && (!EXCLUDED_REASONS.has(value.reason.code)
      || value.versionRef !== null))) {
    throw new TypeError('artifact collection classification reason is invalid');
  }
  return {
    path: itemPath, classification: value.classification,
    reason: { contract: 'ArtifactCollectionMemberReason/v1', code: value.reason.code },
    versionRef: value.versionRef,
  };
}

function normalizeCollectionAuthority(value) {
  const fields = [
    'classifications', 'contract', 'episodeId', 'expectedEpisodeRevision',
    'expectedOriginSha256', 'expectedWorkRevision', 'originId', 'originSetSha256',
    'originTimelineId', 'originTimelineSha256', 'predicate', 'snapshotRef',
    'supersessionScope', 'supersessionSha256', 'supersessionToken', 'workItemId',
  ];
  if (!exactKeys(value, fields) || value.contract !== 'ArtifactCollectionAuthority/v1'
    || typeof value.snapshotRef !== 'string' || !SNAPSHOT_REF.test(value.snapshotRef)
    || !Array.isArray(value.classifications) || !value.classifications.length
    || value.classifications.length > HARD_LIMITS.maxFiles
    || typeof value.expectedOriginSha256 !== 'string'
    || !SHA256.test(value.expectedOriginSha256)
    || typeof value.originSetSha256 !== 'string' || !SHA256.test(value.originSetSha256)
    || typeof value.originTimelineSha256 !== 'string'
    || !SHA256.test(value.originTimelineSha256)
    || typeof value.supersessionSha256 !== 'string'
    || !SHA256.test(value.supersessionSha256)) {
    throw new TypeError('invalid ArtifactCollectionAuthority/v1');
  }
  const supersessionScope = boundedString(value.supersessionScope, 'supersessionScope', {
    bytes: 512,
  });
  const supersessionToken = boundedString(value.supersessionToken, 'supersessionToken', {
    bytes: 512,
  });
  if (hashJson({ supersessionScope, supersessionToken }) !== value.supersessionSha256) {
    throw new TypeError('artifact collection supersession hash is invalid');
  }
  const classifications = value.classifications.map(normalizedClassification);
  if (new Set(classifications.map((item) => item.path)).size !== classifications.length
    || canonicalJson([...classifications].sort((left, right) => pathOrder(left.path, right.path)))
      !== canonicalJson(classifications)) {
    throw new TypeError('artifact collection classifications must be unique and path-sorted');
  }
  return {
    contract: value.contract,
    workItemId: validPositiveId(value.workItemId, 'workItemId'),
    episodeId: validPositiveId(value.episodeId, 'episodeId'),
    originId: validPositiveId(value.originId, 'originId'),
    expectedWorkRevision: validRevision(value.expectedWorkRevision, 'expectedWorkRevision'),
    expectedEpisodeRevision: validRevision(
      value.expectedEpisodeRevision, 'expectedEpisodeRevision',
    ),
    expectedOriginSha256: value.expectedOriginSha256,
    originSetSha256: value.originSetSha256,
    originTimelineId: validPositiveId(value.originTimelineId, 'originTimelineId'),
    originTimelineSha256: value.originTimelineSha256,
    supersessionScope,
    supersessionToken,
    supersessionSha256: value.supersessionSha256,
    snapshotRef: value.snapshotRef, predicate: exactPredicate(value.predicate),
    classifications,
  };
}

function normalizeCasAuthority(value) {
  if (!exactKeys(value, [
    'casSha256', 'checkRef', 'collectionRef', 'collectionSha256', 'contract',
    'receiptSha256', 'stateSha256',
  ]) || value.contract !== 'ArtifactCollectionCasAuthority/v1'
    || typeof value.checkRef !== 'string' || !CHECK_REF.test(value.checkRef)
    || typeof value.collectionRef !== 'string' || !COLLECTION_REF.test(value.collectionRef)
    || [value.casSha256, value.collectionSha256, value.receiptSha256, value.stateSha256]
      .some((digest) => typeof digest !== 'string' || !SHA256.test(digest))) {
    throw new TypeError('invalid ArtifactCollectionCasAuthority/v1');
  }
  return {
    contract: value.contract, checkRef: value.checkRef,
    receiptSha256: value.receiptSha256, casSha256: value.casSha256,
    collectionRef: value.collectionRef,
    collectionSha256: value.collectionSha256, stateSha256: value.stateSha256,
  };
}

function nullableString(value, field) {
  return value == null ? null : boundedString(value, field, { nullable: true });
}

function storedInteger(value, field, { nullable = false, minimum = 0 } = {}) {
  if (value == null && nullable) return null;
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < minimum) {
    throw new Error(`${field} is not a safe stored integer`);
  }
  return number;
}

function workSnapshot(row) {
  return {
    id: storedInteger(row.id, 'work.id', { minimum: 1 }),
    createdTs: boundedString(row.created_ts, 'work.created_ts'),
    updatedTs: boundedString(row.updated_ts, 'work.updated_ts'),
    source: boundedString(row.source, 'work.source'), owner: nullableString(row.owner, 'work.owner'),
    title: boundedString(row.title, 'work.title'), goal: boundedString(row.goal, 'work.goal'),
    nextStep: nullableString(row.next_step, 'work.next_step'),
    status: boundedString(row.status, 'work.status'),
    evidence: nullableString(row.evidence, 'work.evidence'),
    lastAction: nullableString(row.last_action, 'work.last_action'),
    result: nullableString(row.result, 'work.result'),
    visibleToFamily: storedInteger(row.visible_to_family, 'work.visible_to_family'),
    definitionOfDone: nullableString(row.definition_of_done, 'work.definition_of_done'),
    currentStep: nullableString(row.current_step, 'work.current_step'),
    stateJson: nullableString(row.state_json, 'work.state_json'),
    waitJson: nullableString(row.wait_json, 'work.wait_json'),
    priority: storedInteger(row.priority, 'work.priority'),
    nextWakeTs: nullableString(row.next_wake_ts, 'work.next_wake_ts'),
    revision: storedInteger(row.revision, 'work.revision'),
    leaseOwner: nullableString(row.lease_owner, 'work.lease_owner'),
    leaseUntil: nullableString(row.lease_until, 'work.lease_until'),
    attemptCount: storedInteger(row.attempt_count, 'work.attempt_count'),
    lastError: nullableString(row.last_error, 'work.last_error'),
    interruptedTs: nullableString(row.interrupted_ts, 'work.interrupted_ts'),
  };
}

function episodeSnapshot(row) {
  return {
    id: storedInteger(row.id, 'episode.id', { minimum: 1 }),
    episodeKey: boundedString(row.episode_key, 'episode.episode_key'),
    goalId: storedInteger(row.goal_id, 'episode.goal_id', { minimum: 1 }),
    projectId: storedInteger(row.project_id, 'episode.project_id', { minimum: 1 }),
    workItemId: storedInteger(row.work_item_id, 'episode.work_item_id', {
      nullable: true, minimum: 1,
    }),
    ownerActor: boundedString(row.owner_actor, 'episode.owner_actor'),
    originKind: boundedString(row.origin_kind, 'episode.origin_kind'),
    originChannel: nullableString(row.origin_channel, 'episode.origin_channel'),
    originConversationId: nullableString(
      row.origin_conversation_id, 'episode.origin_conversation_id',
    ),
    originMessageId: nullableString(row.origin_message_id, 'episode.origin_message_id'),
    requester: nullableString(row.requester, 'episode.requester'),
    requestedOutcome: boundedString(row.requested_outcome, 'episode.requested_outcome'),
    deliveryPolicy: boundedString(row.delivery_policy, 'episode.delivery_policy'),
    deliveryTarget: nullableString(row.delivery_target, 'episode.delivery_target'),
    state: boundedString(row.state, 'episode.state'),
    nextAction: nullableString(row.next_action, 'episode.next_action'),
    checkpoint: nullableString(row.checkpoint, 'episode.checkpoint'),
    proofSummary: nullableString(row.proof_summary, 'episode.proof_summary'),
    wakeTs: nullableString(row.wake_ts, 'episode.wake_ts'),
    leaseOwner: nullableString(row.lease_owner, 'episode.lease_owner'),
    leaseUntil: nullableString(row.lease_until, 'episode.lease_until'),
    createdTs: boundedString(row.created_ts, 'episode.created_ts'),
    updatedTs: boundedString(row.updated_ts, 'episode.updated_ts'),
    closedTs: nullableString(row.closed_ts, 'episode.closed_ts'),
    revision: storedInteger(row.revision, 'episode.revision'),
  };
}

function originSnapshot(row) {
  return {
    id: storedInteger(row.id, 'origin.id', { minimum: 1 }),
    episodeId: storedInteger(row.episode_id, 'origin.episode_id', { minimum: 1 }),
    originKey: boundedString(row.origin_key, 'origin.origin_key'),
    originKind: boundedString(row.origin_kind, 'origin.origin_kind'),
    originChannel: nullableString(row.origin_channel, 'origin.origin_channel'),
    originConversationId: nullableString(
      row.origin_conversation_id, 'origin.origin_conversation_id',
    ),
    originMessageId: nullableString(row.origin_message_id, 'origin.origin_message_id'),
    requester: nullableString(row.requester, 'origin.requester'),
    attachedTs: boundedString(row.attached_ts, 'origin.attached_ts'),
  };
}

function timelineSnapshot(row) {
  if (typeof row?.meta !== 'string' || typeof row?.content !== 'string') {
    throw new Error('origin timeline content or metadata is invalid');
  }
  boundedString(row.meta, 'timeline.meta');
  boundedString(row.content, 'timeline.content');
  let meta;
  try { meta = JSON.parse(row.meta); } catch {
    throw new Error('origin timeline metadata is invalid');
  }
  return {
    id: storedInteger(row.id, 'timeline.id', { minimum: 1 }),
    ts: boundedString(row.ts, 'timeline.ts'),
    kind: boundedString(row.kind, 'timeline.kind'),
    contentSha256: sha256(Buffer.from(row.content, 'utf8')),
    meta,
    outcomeKey: nullableString(row.outcome_key, 'timeline.outcome_key'),
  };
}

function originSet(database, episodeId) {
  const origins = database.prepare(`SELECT * FROM main.eva_episode_origins
    WHERE episode_id=? ORDER BY origin_key,id LIMIT 129`).all(episodeId).map(originSnapshot);
  if (origins.length > 128) throw new Error('artifact collection origin set exceeds its bound');
  return { origins, sha256: hashJson(origins) };
}

function latestSupersession(database, scope) {
  const rows = database.prepare(`SELECT id,meta FROM main.timeline
    WHERE kind='chat_in' AND json_valid(meta)
      AND json_extract(meta,'$.messageContext.ingressScope')=?
    ORDER BY id DESC LIMIT 2`).all(scope);
  if (!rows.length) return null;
  let meta;
  if (typeof rows[0].meta !== 'string') return null;
  try { meta = JSON.parse(rows[0].meta); } catch { return null; }
  const token = meta?.messageContext?.ingressToken;
  if (typeof token !== 'string' || !token || Buffer.byteLength(token, 'utf8') > 512) return null;
  return {
    timelineId: Number(rows[0].id),
    token,
  };
}

function boundAuthority(database, authority) {
  const workRow = database.prepare('SELECT * FROM main.eva_work_items WHERE id=?')
    .get(authority.workItemId);
  const episodeRow = database.prepare('SELECT * FROM main.eva_goal_episodes WHERE id=?')
    .get(authority.episodeId);
  const originRow = database.prepare('SELECT * FROM main.eva_episode_origins WHERE id=?')
    .get(authority.originId);
  const timelineRow = database.prepare('SELECT * FROM main.timeline WHERE id=?')
    .get(authority.originTimelineId);
  if (!workRow || Number(workRow.revision) !== authority.expectedWorkRevision) {
    throw new Error('artifact collection work revision is stale');
  }
  if (!episodeRow || Number(episodeRow.work_item_id) !== authority.workItemId
    || Number(episodeRow.revision) !== authority.expectedEpisodeRevision) {
    throw new Error('artifact collection episode revision is stale or unbound');
  }
  if (!originRow || Number(originRow.episode_id) !== authority.episodeId) {
    throw new Error('artifact collection origin is not bound to its episode');
  }
  const work = workSnapshot(workRow);
  const episode = episodeSnapshot(episodeRow);
  const origin = originSnapshot(originRow);
  const timeline = timelineSnapshot(timelineRow || {});
  const origins = originSet(database, authority.episodeId);
  if (hashJson(origin) !== authority.expectedOriginSha256
    || origins.sha256 !== authority.originSetSha256
    || hashJson(timeline) !== authority.originTimelineSha256) {
    throw new Error('artifact collection origin authority hash is stale');
  }
  const meta = timeline.meta;
  if (timeline.kind !== 'chat_in'
    || meta?.channel !== origin.originChannel
    || meta?.conversationId !== origin.originConversationId
    || typeof meta?.messageContext?.messageId !== 'string'
    || meta.messageContext.messageId !== origin.originMessageId
    || meta?.sender !== origin.requester
    || meta?.messageContext?.ingressScope !== authority.supersessionScope
    || meta?.messageContext?.ingressToken !== authority.supersessionToken
    || episode.requester !== origin.requester || work.owner !== origin.requester) {
    throw new Error('artifact collection origin is not transitively bound to its inbound principal');
  }
  const latest = latestSupersession(database, authority.supersessionScope);
  if (!latest || latest.timelineId !== timeline.id || latest.token !== authority.supersessionToken) {
    throw new Error('artifact collection origin has been superseded');
  }
  return deepFreeze({ work, episode, origin, origins: origins.origins, timeline });
}

function exactArtifactReceipt(value) {
  if (!exactKeys(value, [
    'bytes', 'contract', 'objectRef', 'path', 'previousVersionRef',
    'provenanceSha256', 'requirementId', 'role', 'sha256',
    'understandingSha256', 'versionRef',
  ]) || value.contract !== 'ArtifactVersionReceipt/v1'
    || typeof value.versionRef !== 'string' || !VERSION_REF.test(value.versionRef)
    || value.objectRef !== `sha256:${value.sha256}`
    || exactWorkspacePath(value.path) !== value.path
    || typeof value.sha256 !== 'string' || !SHA256.test(value.sha256)
    || typeof value.bytes !== 'number' || !Number.isSafeInteger(value.bytes) || value.bytes < 0
    || value.bytes > 2 * 1024 * 1024
    || !ARTIFACT_ROLES.has(value.role)
    || typeof value.understandingSha256 !== 'string'
    || !SHA256.test(value.understandingSha256)
    || typeof value.requirementId !== 'string' || !REQUIREMENT_ID.test(value.requirementId)
    || (value.previousVersionRef != null
      && (typeof value.previousVersionRef !== 'string'
        || !VERSION_REF.test(value.previousVersionRef)))
    || typeof value.provenanceSha256 !== 'string'
    || !SHA256.test(value.provenanceSha256)) {
    throw new Error('production artifact store returned an inexact receipt');
  }
  return {
    contract: value.contract, versionRef: value.versionRef,
    objectRef: value.objectRef, path: value.path, sha256: value.sha256,
    bytes: value.bytes, role: value.role,
    understandingSha256: value.understandingSha256,
    requirementId: value.requirementId,
    previousVersionRef: value.previousVersionRef,
    provenanceSha256: value.provenanceSha256,
  };
}

function exactArtifactHead(value, paths = null) {
  if (!exactKeys(value, [
    'episodeId', 'path', 'receiptSha256', 'versionRef', 'workItemId',
  ]) || exactWorkspacePath(value.path) !== value.path
    || (paths && !paths.has(value.path))) {
    throw new Error('production artifact store returned an invalid path head');
  }
  const absent = value.versionRef == null;
  if (absent) {
    if (value.receiptSha256 != null || value.workItemId != null || value.episodeId != null) {
      throw new Error('production artifact store returned a partial empty path head');
    }
  } else {
    if (typeof value.versionRef !== 'string' || !VERSION_REF.test(value.versionRef)
      || typeof value.receiptSha256 !== 'string' || !SHA256.test(value.receiptSha256)
      || typeof value.workItemId !== 'number' || !Number.isSafeInteger(value.workItemId)
      || value.workItemId < 1 || typeof value.episodeId !== 'number'
      || !Number.isSafeInteger(value.episodeId) || value.episodeId < 1) {
      throw new Error('production artifact store returned an invalid non-empty path head');
    }
  }
  return {
    path: value.path, versionRef: absent ? null : value.versionRef,
    receiptSha256: absent ? null : value.receiptSha256,
    workItemId: absent ? null : value.workItemId,
    episodeId: absent ? null : value.episodeId,
  };
}

function productionArtifactVerification({ workItemId, episodeId, paths, versionRefs = [] }) {
  const exactWorkItemId = validPositiveId(workItemId, 'workItemId');
  const exactEpisodeId = validPositiveId(episodeId, 'episodeId');
  if (!Array.isArray(paths) || !paths.length || paths.length > HARD_LIMITS.maxFiles) {
    throw new TypeError('artifact verification paths are outside the production bound');
  }
  const canonicalPaths = paths.map(exactWorkspacePath);
  if (new Set(canonicalPaths).size !== canonicalPaths.length
    || canonicalJson([...canonicalPaths].sort(pathOrder)) !== canonicalJson(canonicalPaths)) {
    throw new TypeError('artifact verification paths must be unique and sorted');
  }
  const storePaths = [...canonicalPaths].sort(pathOrder);
  if (!Array.isArray(versionRefs) || versionRefs.length > HARD_LIMITS.maxFiles * 2
    || versionRefs.some((reference) => typeof reference !== 'string'
      || !VERSION_REF.test(reference))) {
    throw new TypeError('artifact verification version references are invalid');
  }
  const authority = artifactCollectionStoreAuthority({
    workItemId: exactWorkItemId, episodeId: exactEpisodeId, paths: storePaths,
  });
  if (!authority) throw new Error('production artifact store authority is unavailable');
  const value = consumeArtifactCollectionStoreAuthority(authority);
  if (!value || !exactKeys(value, [
    'contract', 'episodeId', 'episodeRevision', 'episodeState', 'heads',
    'inventory', 'sha256', 'workItemId', 'workRevision',
  ]) || value.contract !== 'ArtifactCollectionStoreState/v1'
    || value.workItemId !== exactWorkItemId || value.episodeId !== exactEpisodeId
    || typeof value.workRevision !== 'number' || !Number.isSafeInteger(value.workRevision)
    || value.workRevision < 0 || typeof value.episodeRevision !== 'number'
    || !Number.isSafeInteger(value.episodeRevision) || value.episodeRevision < 0
    || typeof value.episodeState !== 'string'
    || !Array.isArray(value.heads) || !Array.isArray(value.inventory)
    || typeof value.sha256 !== 'string'
    || !SHA256.test(value.sha256)) {
    throw new Error('production artifact store authority failed its one-shot fence');
  }
  const stateBody = { ...value };
  delete stateBody.sha256;
  if (hashJson(stateBody) !== value.sha256) {
    throw new Error('production artifact store state hash is invalid');
  }
  const pathSet = new Set(storePaths);
  const storeHeads = value.heads.map((head) => exactArtifactHead(head, pathSet));
  if (storeHeads.length !== storePaths.length
    || canonicalJson(storeHeads.map((head) => head.path)) !== canonicalJson(storePaths)) {
    throw new Error('production artifact store state does not cover every exact path');
  }
  const heads = storeHeads.sort((left, right) => pathOrder(left.path, right.path));
  const inventory = value.inventory.map((head) => exactArtifactHead(head));
  if (inventory.length > HARD_LIMITS.maxFiles
    || new Set(inventory.map((head) => head.path)).size !== inventory.length
    || canonicalJson([...inventory].sort((left, right) => pathOrder(left.path, right.path)))
      !== canonicalJson(inventory)
    || inventory.some((head) => head.versionRef == null
      || head.workItemId !== exactWorkItemId || head.episodeId !== exactEpisodeId)) {
    throw new Error('production artifact store target inventory is invalid or unbounded');
  }
  const references = [...new Set([
    ...versionRefs, ...heads.map((head) => head.versionRef).filter(Boolean),
    ...inventory.map((head) => head.versionRef),
  ])].sort(pathOrder);
  const receipts = references.map((reference) => exactArtifactReceipt(
    artifactVersionReceipt(reference),
  ));
  const receiptMap = new Map(receipts.map((receipt) => [receipt.versionRef, receipt]));
  for (const head of [...heads, ...inventory]) {
    if (!head.versionRef) continue;
    const receipt = receiptMap.get(head.versionRef);
    if (!receipt || receipt.path !== head.path || hashJson(receipt) !== head.receiptSha256) {
      throw new Error('production artifact head lacks its exact durable receipt');
    }
  }
  return deepFreeze({
    contract: 'ArtifactCollectionStoreVerification/v1',
    workItemId: exactWorkItemId, episodeId: exactEpisodeId,
    workRevision: value.workRevision, episodeRevision: value.episodeRevision,
    episodeState: value.episodeState, receipts, heads, inventory,
    inventorySha256: hashJson(inventory), storeFenceSha256: value.sha256,
  });
}

function productionHistoricalArtifactVerification({ versionRefs }) {
  if (!Array.isArray(versionRefs) || versionRefs.length > HARD_LIMITS.maxFiles * 3
    || versionRefs.some((reference) => typeof reference !== 'string'
      || !VERSION_REF.test(reference))) {
    throw new TypeError('historical artifact references are invalid or exceed their bound');
  }
  const references = [...new Set(versionRefs)].sort(pathOrder);
  return deepFreeze({
    contract: 'ArtifactCollectionHistoricalArtifactVerification/v1',
    receipts: references.map((reference) => exactArtifactReceipt(
      artifactVersionReceipt(reference),
    )),
  });
}

function snapshotMemberFromRow(row) {
  const statIdentity = parsedCanonical(row.stat_identity_json, 'snapshot member stat identity');
  const stored = parsedCanonical(row.member_json, 'snapshot member');
  const base = {
    contract: 'RecursiveWorkspaceSnapshotMember/v1', ordinal: Number(row.ordinal),
    path: row.canonical_path, sha256: row.artifact_sha256,
    bytes: Number(row.byte_length), statIdentity,
    statIdentitySha256: row.stat_identity_sha256,
  };
  const expected = { ...base, memberSha256: hashJson(base) };
  if (hashJson(statIdentity) !== row.stat_identity_sha256
    || row.member_sha256 !== expected.memberSha256
    || canonicalJson(stored) !== canonicalJson(expected)) {
    throw new Error('recursive workspace snapshot member failed integrity verification');
  }
  return deepFreeze(expected);
}

function snapshotFromRow(database, row) {
  if (!row) return null;
  const members = database.prepare(`SELECT * FROM main.${TABLES.snapshotMembers}
    WHERE snapshot_ref=? ORDER BY ordinal`).all(row.snapshot_ref).map(snapshotMemberFromRow);
  if (members.some((member, ordinal) => member.ordinal !== ordinal)) {
    throw new Error('recursive workspace snapshot member sequence is invalid');
  }
  const stored = parsedCanonical(row.snapshot_json, 'recursive workspace snapshot');
  const payload = {
    contract: 'RecursiveWorkspaceSnapshot/v1', requestSha256: row.request_sha256,
    operationKey: row.operation_key, createdAt: row.created_ts, root: row.root,
    rootIdentitySha256: row.root_identity_sha256,
    selectionKind: row.selection_kind, query: row.query,
    recursive: true, followSymlinks: false, complete: Boolean(row.complete),
    limits: stored.limits, discoveryExecutionKey: row.discovery_execution_key,
    discoveryCompletionSha256: row.discovery_completion_sha256,
    manifestSha256: row.manifest_sha256,
    dependencySchemaSha256: row.dependency_schema_sha256,
    directories: stored.directories, members,
  };
  const snapshotSha256 = hashJson(payload);
  const expected = {
    ...payload, snapshotRef: `recursive-workspace-snapshot:${snapshotSha256}`,
    snapshotSha256,
  };
  if (row.contract !== expected.contract || row.snapshot_ref !== expected.snapshotRef
    || row.snapshot_sha256 !== snapshotSha256 || row.root !== LOGICAL_ROOT
    || row.selection_kind !== 'all_regular_files' || row.query !== null
    || Number(row.complete) !== 1 || canonicalJson(stored) !== canonicalJson(expected)) {
    throw new Error('recursive workspace snapshot failed integrity verification');
  }
  return deepFreeze(expected);
}

function snapshotRequestSha256(attestation, discovery, dependencySha256) {
  return hashJson({
    contract: 'RecursiveWorkspaceSnapshotRequest/v1', root: LOGICAL_ROOT,
    rootIdentitySha256: discovery.rootIdentity.statIdentitySha256,
    selectionKind: 'all_regular_files', query: null, recursive: true,
    followSymlinks: false, discoveryExecutionKey: attestation.discoveryExecutionKey,
    discoveryCompletionSha256: attestation.discoveryCompletionSha256,
    manifestSha256: discovery.manifestSha256,
    dependencySchemaSha256: dependencySha256,
  });
}

function insertSnapshot(database, attestation, discovery, operationKey, dependencySha256) {
  const requestSha256 = snapshotRequestSha256(attestation, discovery, dependencySha256);
  const createdAt = nowIso();
  const payload = {
    contract: 'RecursiveWorkspaceSnapshot/v1', requestSha256, operationKey, createdAt,
    root: LOGICAL_ROOT,
    rootIdentitySha256: discovery.rootIdentity.statIdentitySha256,
    selectionKind: 'all_regular_files', query: null, recursive: true,
    followSymlinks: false, complete: true, limits: discovery.limits,
    discoveryExecutionKey: attestation.discoveryExecutionKey,
    discoveryCompletionSha256: attestation.discoveryCompletionSha256,
    manifestSha256: discovery.manifestSha256,
    dependencySchemaSha256: dependencySha256,
    directories: discovery.directories, members: discovery.members,
  };
  const snapshotSha256 = hashJson(payload);
  const snapshot = deepFreeze({
    ...payload, snapshotRef: `recursive-workspace-snapshot:${snapshotSha256}`,
    snapshotSha256,
  });
  database.prepare(`INSERT INTO main.${TABLES.snapshots} (
    snapshot_ref,contract,snapshot_sha256,request_sha256,operation_key,created_ts,
    root,root_identity_sha256,selection_kind,query,complete,discovery_execution_key,
    discovery_completion_sha256,manifest_sha256,dependency_schema_sha256,snapshot_json
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    snapshot.snapshotRef, snapshot.contract, snapshot.snapshotSha256,
    snapshot.requestSha256, snapshot.operationKey, snapshot.createdAt,
    snapshot.root, snapshot.rootIdentitySha256, snapshot.selectionKind, snapshot.query,
    1, snapshot.discoveryExecutionKey, snapshot.discoveryCompletionSha256,
    snapshot.manifestSha256, snapshot.dependencySchemaSha256, canonicalJson(snapshot),
  );
  for (const member of snapshot.members) {
    database.prepare(`INSERT INTO main.${TABLES.snapshotMembers} (
      snapshot_ref,ordinal,canonical_path,artifact_sha256,byte_length,
      stat_identity_json,stat_identity_sha256,member_sha256,member_json
    ) VALUES (?,?,?,?,?,?,?,?,?)`).run(
      snapshot.snapshotRef, member.ordinal, member.path, member.sha256, member.bytes,
      canonicalJson(member.statIdentity), member.statIdentitySha256,
      member.memberSha256, canonicalJson(member),
    );
  }
  return snapshotFromRow(database, database.prepare(
    `SELECT * FROM main.${TABLES.snapshots} WHERE snapshot_ref=?`,
  ).get(snapshot.snapshotRef));
}

function exactStoredReceipt(value, sha, label) {
  const receipt = exactArtifactReceipt(parsedCanonical(value, label));
  if (hashJson(receipt) !== sha) throw new Error(`${label} hash is invalid`);
  return receipt;
}

function storedHead(value, sha) {
  const head = parsedCanonical(value, 'artifact collection member head');
  if (!exactKeys(head, [
    'episodeId', 'path', 'receiptSha256', 'versionRef', 'workItemId',
  ]) || exactWorkspacePath(head.path) !== head.path || hashJson(head) !== sha
    || (head.versionRef == null
      ? head.receiptSha256 != null || head.workItemId != null || head.episodeId != null
      : typeof head.versionRef !== 'string' || !VERSION_REF.test(head.versionRef)
        || typeof head.receiptSha256 !== 'string' || !SHA256.test(head.receiptSha256)
        || typeof head.workItemId !== 'number' || !Number.isSafeInteger(head.workItemId)
        || head.workItemId < 1 || typeof head.episodeId !== 'number'
        || !Number.isSafeInteger(head.episodeId) || head.episodeId < 1)) {
    throw new Error('artifact collection member head failed integrity verification');
  }
  return head;
}

function storedCollectionMember(row) {
  const reason = parsedCanonical(row.reason_json, 'artifact collection member reason');
  const head = storedHead(row.head_json, row.head_sha256);
  if (!exactKeys(reason, ['code', 'contract'])
    || reason.contract !== 'ArtifactCollectionMemberReason/v1'
    || reason.code !== row.reason_code || hashJson(reason) !== row.reason_sha256
    || head.path !== row.canonical_path) {
    throw new Error('artifact collection member reason or head is invalid');
  }
  let artifact = null;
  if (row.classification === 'included') {
    artifact = exactStoredReceipt(
      row.artifact_receipt_json, row.artifact_receipt_sha256,
      'artifact collection member receipt',
    );
    if (artifact.versionRef !== row.version_ref || artifact.path !== row.canonical_path
      || head.versionRef !== artifact.versionRef
      || head.receiptSha256 !== hashJson(artifact)) {
      throw new Error('artifact collection included receipt conflicts with its head');
    }
  } else if (row.classification !== 'excluded' || !EXCLUDED_REASONS.has(reason.code)
    || row.version_ref != null || row.artifact_receipt_json != null
    || row.artifact_receipt_sha256 != null) {
    throw new Error('artifact collection excluded member is invalid');
  }
  const snapshotMember = {
    snapshotRef: row.snapshot_ref, ordinal: Number(row.snapshot_ordinal),
  };
  const base = {
    contract: 'ArtifactCollectionMember/v1', ordinal: Number(row.ordinal),
    path: row.canonical_path, snapshotMember, classification: row.classification,
    reason, artifact, headAtCreation: head,
  };
  const expected = { ...base, memberSha256: hashJson(base) };
  const stored = parsedCanonical(row.member_json, 'artifact collection member');
  if (row.member_sha256 !== expected.memberSha256
    || canonicalJson(stored) !== canonicalJson(expected)) {
    throw new Error('artifact collection member failed integrity verification');
  }
  return deepFreeze(expected);
}

function historicalArtifactRequest(collection) {
  const refs = collection.members.flatMap((member) => [
    member.artifact?.versionRef, member.headAtCreation.versionRef,
  ]).concat(collection.artifactInventory.map((entry) => entry.versionRef)).filter(Boolean);
  return {
    workItemId: collection.work.id,
    episodeId: collection.episode.id, versionRefs: refs,
    paths: collection.members.map((member) => member.path),
  };
}

function compareHistoricalArtifactVerification(collection, verification) {
  const receipts = new Map(verification.receipts.map((receipt) => [receipt.versionRef, receipt]));
  for (const member of collection.members) {
    if (member.artifact
      && canonicalJson(receipts.get(member.artifact.versionRef)) !== canonicalJson(member.artifact)) {
      throw new Error('historical artifact receipt conflicts with the production store');
    }
    if (member.headAtCreation.versionRef) {
      const receipt = receipts.get(member.headAtCreation.versionRef);
      if (!receipt || hashJson(receipt) !== member.headAtCreation.receiptSha256) {
        throw new Error('historical artifact head conflicts with the production store');
      }
    }
  }
  for (const entry of collection.artifactInventory) {
    const receipt = receipts.get(entry.versionRef);
    if (!receipt || receipt.path !== entry.path
      || hashJson(receipt) !== entry.receiptSha256) {
      throw new Error('historical artifact inventory conflicts with the production store');
    }
  }
}

function collectionFromRow(database, row, verifyArtifacts) {
  if (!row) return null;
  const snapshot = snapshotFromRow(database, database.prepare(
    `SELECT * FROM main.${TABLES.snapshots} WHERE snapshot_ref=?`,
  ).get(row.snapshot_ref));
  if (!snapshot || snapshot.snapshotSha256 !== row.snapshot_sha256) {
    throw new Error('artifact collection snapshot dependency changed');
  }
  const work = parsedCanonical(row.work_json, 'artifact collection work snapshot');
  const episode = parsedCanonical(row.episode_json, 'artifact collection episode snapshot');
  const origin = parsedCanonical(row.origin_json, 'artifact collection origin snapshot');
  const predicate = parsedCanonical(row.predicate_json, 'artifact collection predicate');
  const artifactInventory = parsedCanonical(
    row.artifact_inventory_json, 'artifact collection artifact inventory',
  );
  exactPredicate(predicate);
  if (hashJson(work) !== row.work_sha256 || hashJson(episode) !== row.episode_sha256
    || hashJson(origin) !== row.origin_sha256 || hashJson(predicate) !== row.predicate_sha256
    || hashJson(artifactInventory) !== row.artifact_inventory_sha256) {
    throw new Error('artifact collection stored authority hashes are invalid');
  }
  if (Number(work.id) !== Number(row.work_item_id)
    || Number(work.revision) !== Number(row.expected_work_revision)
    || Number(episode.id) !== Number(row.episode_id)
    || Number(episode.workItemId) !== Number(row.work_item_id)
    || Number(episode.revision) !== Number(row.expected_episode_revision)
    || Number(origin.id) !== Number(row.origin_id)
    || Number(origin.episodeId) !== Number(row.episode_id)) {
    throw new Error('artifact collection stored work, episode, or origin linkage is invalid');
  }
  if (!Array.isArray(artifactInventory)
    || new Set(artifactInventory.map((entry) => entry?.path)).size
      !== artifactInventory.length
    || canonicalJson([...artifactInventory].sort((left, right) => (
      pathOrder(left.path, right.path)
    ))) !== canonicalJson(artifactInventory)
    || artifactInventory.some((entry) => (
      !exactKeys(entry, [
        'episodeId', 'path', 'receiptSha256', 'versionRef', 'workItemId',
      ]) || exactWorkspacePath(entry.path) !== entry.path
        || typeof entry.versionRef !== 'string' || !VERSION_REF.test(entry.versionRef)
        || typeof entry.receiptSha256 !== 'string' || !SHA256.test(entry.receiptSha256)
        || typeof entry.workItemId !== 'number' || entry.workItemId !== work.id
        || typeof entry.episodeId !== 'number' || entry.episodeId !== episode.id
    ))) {
    throw new Error('artifact collection stored artifact inventory is invalid');
  }
  const members = database.prepare(`SELECT * FROM main.${TABLES.collectionMembers}
    WHERE collection_ref=? ORDER BY ordinal`).all(row.collection_ref)
    .map(storedCollectionMember);
  if (members.length !== snapshot.members.length
    || members.some((member, ordinal) => (
      member.ordinal !== ordinal || member.path !== snapshot.members[ordinal].path
        || member.snapshotMember.snapshotRef !== snapshot.snapshotRef
        || member.snapshotMember.ordinal !== snapshot.members[ordinal].ordinal
    ))) {
    throw new Error('artifact collection silently omits or conflicts with snapshot members');
  }
  const relationalInventory = [];
  for (const member of members) {
    const targetHead = member.headAtCreation.versionRef != null
      && member.headAtCreation.workItemId === work.id
      && member.headAtCreation.episodeId === episode.id;
    if ((member.classification === 'included') !== targetHead
      || (member.classification === 'excluded') !== (member.headAtCreation.versionRef == null)) {
      throw new Error('stored classification is not exactly its target artifact head relation');
    }
    if (targetHead) relationalInventory.push({
      path: member.path, versionRef: member.artifact.versionRef,
      receiptSha256: hashJson(member.artifact), workItemId: work.id, episodeId: episode.id,
    });
  }
  if (canonicalJson(relationalInventory) !== canonicalJson(artifactInventory)) {
    throw new Error('stored artifact inventory is not exactly the included head relation');
  }
  const stored = parsedCanonical(row.collection_json, 'artifact collection');
  const originAuthority = stored.originAuthority;
  if (!exactKeys(originAuthority, [
    'originTimelineId', 'supersessionScope', 'supersessionSha256',
    'supersessionToken',
  ]) || typeof originAuthority.originTimelineId !== 'number'
    || !Number.isSafeInteger(originAuthority.originTimelineId)
    || originAuthority.originTimelineId < 1
    || hashJson({
      supersessionScope: originAuthority.supersessionScope,
      supersessionToken: originAuthority.supersessionToken,
    }) !== originAuthority.supersessionSha256) {
    throw new Error('artifact collection stored origin authority is invalid');
  }
  const payload = {
    contract: 'ArtifactCollection/v1', requestSha256: row.request_sha256,
    authoritySha256: row.authority_sha256, operationKey: row.operation_key,
    createdAt: row.created_ts,
    current: false, universal: false, currentCheckRequired: true,
    work, workSha256: row.work_sha256,
    episode, episodeSha256: row.episode_sha256,
    origin, originSha256: row.origin_sha256, originSetSha256: row.origin_set_sha256,
    originTimelineSha256: row.origin_timeline_sha256,
    originAuthority,
    snapshotRef: row.snapshot_ref, snapshotSha256: row.snapshot_sha256,
    dependencySchemaSha256: row.dependency_schema_sha256,
    predicate, predicateSha256: row.predicate_sha256,
    artifactInventory, artifactInventorySha256: row.artifact_inventory_sha256,
    artifactStoreFenceSha256: row.artifact_store_fence_sha256, members,
  };
  const collectionSha256 = hashJson(payload);
  const expected = {
    ...payload, collectionRef: `artifact-collection:${collectionSha256}`,
    collectionSha256,
  };
  if (row.contract !== expected.contract || row.collection_ref !== expected.collectionRef
    || row.collection_sha256 !== collectionSha256
    || canonicalJson(stored) !== canonicalJson(expected)) {
    throw new Error('artifact collection failed integrity verification');
  }
  const result = deepFreeze(expected);
  if (verifyArtifacts) {
    const verification = verifyArtifacts(historicalArtifactRequest(result));
    compareHistoricalArtifactVerification(result, verification);
  }
  return result;
}

function collectionAuthoritySha256(authority) {
  return hashJson(authority);
}

function preparedMembers(snapshot, authority, verification) {
  if (authority.classifications.length !== snapshot.members.length
    || authority.classifications.some((item, ordinal) => (
      item.path !== snapshot.members[ordinal]?.path
    ))) {
    throw new Error('artifact collection classifications omit or add a snapshot member');
  }
  const receiptMap = new Map(
    verification.receipts.map((receipt) => [receipt.versionRef, receipt]),
  );
  const headMap = new Map(verification.heads.map((head) => [head.path, head]));
  const memberPaths = new Set(snapshot.members.map((member) => member.path));
  const outside = verification.inventory.find((entry) => !memberPaths.has(entry.path));
  if (outside) {
    throw new Error(`artifact inventory path is absent from the complete workspace snapshot: ${outside.path}`);
  }
  const members = snapshot.members.map((snapshotMember, ordinal) => {
    const classification = authority.classifications[ordinal];
    const head = headMap.get(snapshotMember.path);
    if (!head) throw new Error('artifact store verification omits a snapshot path head');
    const belongsToTarget = head.versionRef != null
      && head.workItemId === authority.workItemId && head.episodeId === authority.episodeId;
    if (head.versionRef != null && !belongsToTarget) {
      throw new Error('a current artifact head belongs to another work episode');
    }
    if ((classification.classification === 'included') !== belongsToTarget
      || (classification.classification === 'excluded') !== (head.versionRef == null)) {
      throw new Error('classification does not exactly match the production artifact head');
    }
    let artifact = null;
    if (classification.classification === 'included') {
      artifact = receiptMap.get(classification.versionRef) || null;
      if (!artifact || head.versionRef !== classification.versionRef
        || head.versionRef !== artifact.versionRef
        || head.workItemId !== authority.workItemId || head.episodeId !== authority.episodeId) {
        throw new Error('included artifact version is not the current version for this work episode');
      }
      if (artifact.path !== snapshotMember.path || artifact.sha256 !== snapshotMember.sha256
        || artifact.bytes !== snapshotMember.bytes) {
        throw new Error('included production artifact receipt conflicts with snapshot bytes or path');
      }
    }
    const reason = deepFreeze({ ...classification.reason });
    const headAtCreation = deepFreeze({ ...head });
    const base = {
      contract: 'ArtifactCollectionMember/v1', ordinal,
      path: snapshotMember.path,
      snapshotMember: { snapshotRef: snapshot.snapshotRef, ordinal },
      classification: classification.classification, reason, artifact, headAtCreation,
    };
    return deepFreeze({ ...base, memberSha256: hashJson(base) });
  });
  const relationalInventory = members.filter((member) => member.classification === 'included')
    .map((member) => ({
      path: member.path, versionRef: member.artifact.versionRef,
      receiptSha256: hashJson(member.artifact), workItemId: authority.workItemId,
      episodeId: authority.episodeId,
    }));
  if (canonicalJson(relationalInventory) !== canonicalJson(verification.inventory)) {
    throw new Error('artifact inventory is not exactly the included target-head relation');
  }
  return members;
}

function collectionRequest(authority, snapshot, bound, members, dependencySha256, verification) {
  const body = {
    contract: 'ArtifactCollectionRequest/v1', authoritySha256: hashJson(authority),
    workItemId: authority.workItemId, episodeId: authority.episodeId,
    originId: authority.originId, expectedWorkRevision: authority.expectedWorkRevision,
    expectedEpisodeRevision: authority.expectedEpisodeRevision,
    workSha256: hashJson(bound.work), episodeSha256: hashJson(bound.episode),
    originSha256: hashJson(bound.origin), originSetSha256: hashJson(bound.origins),
    originTimelineSha256: hashJson(bound.timeline), snapshotRef: snapshot.snapshotRef,
    snapshotSha256: snapshot.snapshotSha256, predicateSha256: hashJson(authority.predicate),
    members: members.map((member) => ({
      path: member.path, classification: member.classification, reason: member.reason,
      versionRef: member.artifact?.versionRef || null,
      headAtCreation: member.headAtCreation, memberSha256: member.memberSha256,
    })),
    artifactInventorySha256: verification.inventorySha256,
    artifactStoreFenceSha256: verification.storeFenceSha256,
    dependencySchemaSha256: dependencySha256,
  };
  return { body, sha256: hashJson(body) };
}

function insertCollection(database, {
  authority, snapshot, bound, members, verification, dependencySha256, operationKey,
}) {
  const request = collectionRequest(
    authority, snapshot, bound, members, dependencySha256, verification,
  );
  const createdAt = nowIso();
  const payload = {
    contract: 'ArtifactCollection/v1', requestSha256: request.sha256,
    authoritySha256: hashJson(authority), operationKey, createdAt,
    current: false, universal: false, currentCheckRequired: true,
    work: bound.work, workSha256: hashJson(bound.work),
    episode: bound.episode, episodeSha256: hashJson(bound.episode),
    origin: bound.origin, originSha256: hashJson(bound.origin),
    originSetSha256: hashJson(bound.origins),
    originTimelineSha256: hashJson(bound.timeline),
    originAuthority: {
      originTimelineId: authority.originTimelineId,
      supersessionScope: authority.supersessionScope,
      supersessionToken: authority.supersessionToken,
      supersessionSha256: authority.supersessionSha256,
    },
    snapshotRef: snapshot.snapshotRef, snapshotSha256: snapshot.snapshotSha256,
    dependencySchemaSha256: dependencySha256,
    predicate: authority.predicate, predicateSha256: hashJson(authority.predicate),
    artifactInventory: verification.inventory,
    artifactInventorySha256: verification.inventorySha256,
    artifactStoreFenceSha256: verification.storeFenceSha256, members,
  };
  const collectionSha256 = hashJson(payload);
  const collection = deepFreeze({
    ...payload, collectionRef: `artifact-collection:${collectionSha256}`,
    collectionSha256,
  });
  database.prepare(`INSERT INTO main.${TABLES.collections} (
    collection_ref,contract,collection_sha256,request_sha256,authority_sha256,
    operation_key,created_ts,work_item_id,episode_id,origin_id,
    expected_work_revision,expected_episode_revision,snapshot_ref,snapshot_sha256,
    dependency_schema_sha256,work_json,work_sha256,episode_json,episode_sha256,
    origin_json,origin_sha256,origin_set_sha256,origin_timeline_sha256,
    predicate_json,predicate_sha256,artifact_inventory_json,artifact_inventory_sha256,
    artifact_store_fence_sha256,collection_json
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    collection.collectionRef, collection.contract, collection.collectionSha256,
    collection.requestSha256, collection.authoritySha256, collection.operationKey,
    collection.createdAt, authority.workItemId, authority.episodeId, authority.originId,
    authority.expectedWorkRevision, authority.expectedEpisodeRevision,
    collection.snapshotRef, collection.snapshotSha256, collection.dependencySchemaSha256,
    canonicalJson(collection.work), collection.workSha256,
    canonicalJson(collection.episode), collection.episodeSha256,
    canonicalJson(collection.origin), collection.originSha256,
    collection.originSetSha256, collection.originTimelineSha256,
    canonicalJson(collection.predicate), collection.predicateSha256,
    canonicalJson(collection.artifactInventory), collection.artifactInventorySha256,
    collection.artifactStoreFenceSha256, canonicalJson(collection),
  );
  for (const member of members) {
    const artifactJson = member.artifact == null ? null : canonicalJson(member.artifact);
    database.prepare(`INSERT INTO main.${TABLES.collectionMembers} (
      collection_ref,ordinal,snapshot_ref,snapshot_ordinal,canonical_path,
      classification,reason_code,reason_json,reason_sha256,version_ref,
      artifact_receipt_json,artifact_receipt_sha256,head_json,head_sha256,
      member_sha256,member_json
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      collection.collectionRef, member.ordinal, member.snapshotMember.snapshotRef,
      member.snapshotMember.ordinal, member.path, member.classification,
      member.reason.code, canonicalJson(member.reason), hashJson(member.reason),
      member.artifact?.versionRef || null, artifactJson,
      artifactJson == null ? null : hashJson(member.artifact),
      canonicalJson(member.headAtCreation), hashJson(member.headAtCreation),
      member.memberSha256, canonicalJson(member),
    );
  }
  return collection;
}

function reasonOrder(left, right) {
  return pathOrder(`${left.path || ''}\u0000${left.code}`, `${right.path || ''}\u0000${right.code}`);
}

function workspaceCurrentReasons(snapshot, observed) {
  const reasons = [];
  const expectedMembers = new Map(snapshot.members.map((member) => [member.path, member]));
  const actualMembers = new Map(observed.members.map((member) => [member.path, member]));
  for (const expected of snapshot.members) {
    const actual = actualMembers.get(expected.path);
    if (!actual) reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1', code: 'workspace_member_missing',
      path: expected.path, expectedSha256: expected.sha256,
    });
    else if (actual.sha256 !== expected.sha256 || actual.bytes !== expected.bytes) reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1', code: 'workspace_member_changed',
      path: expected.path, expectedSha256: expected.sha256, actualSha256: actual.sha256,
    });
    else if (actual.statIdentitySha256 !== expected.statIdentitySha256) reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1',
      code: 'workspace_member_identity_changed', path: expected.path,
      expectedStatIdentitySha256: expected.statIdentitySha256,
      actualStatIdentitySha256: actual.statIdentitySha256,
    });
  }
  for (const actual of observed.members) {
    if (!expectedMembers.has(actual.path)) reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1', code: 'workspace_new_member',
      path: actual.path, actualSha256: actual.sha256,
    });
  }
  const expectedDirectories = new Map(
    snapshot.directories.map((directory) => [directory.path, directory]),
  );
  const actualDirectories = new Map(
    observed.directories.map((directory) => [directory.path, directory]),
  );
  for (const expected of snapshot.directories) {
    const actual = actualDirectories.get(expected.path);
    if (!actual) reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1',
      code: 'workspace_directory_missing', path: expected.path,
    });
    else if (actual.statIdentitySha256 !== expected.statIdentitySha256
      || actual.entriesSha256 !== expected.entriesSha256) reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1',
      code: 'workspace_directory_identity_changed', path: expected.path,
    });
  }
  for (const actual of observed.directories) {
    if (!expectedDirectories.has(actual.path)) reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1',
      code: 'workspace_new_directory', path: actual.path,
    });
  }
  return reasons;
}

function authorityCurrentReasons(database, collection) {
  const reasons = [];
  const add = (code) => reasons.push({
    contract: 'ArtifactCollectionCurrentReason/v1', code, path: LOGICAL_ROOT,
  });
  try {
    const work = database.prepare('SELECT * FROM main.eva_work_items WHERE id=?')
      .get(collection.work.id);
    if (!work || hashJson(workSnapshot(work)) !== collection.workSha256) add('work_revision_changed');
  } catch { add('work_dependency_unavailable'); }
  try {
    const episode = database.prepare('SELECT * FROM main.eva_goal_episodes WHERE id=?')
      .get(collection.episode.id);
    if (!episode || hashJson(episodeSnapshot(episode)) !== collection.episodeSha256) {
      add('episode_revision_changed');
    }
  } catch { add('episode_dependency_unavailable'); }
  try {
    const origin = database.prepare('SELECT * FROM main.eva_episode_origins WHERE id=?')
      .get(collection.origin.id);
    if (!origin || hashJson(originSnapshot(origin)) !== collection.originSha256) {
      add('origin_revision_changed');
    }
    if (originSet(database, collection.episode.id).sha256 !== collection.originSetSha256) {
      add('origin_set_changed');
    }
  } catch { add('origin_dependency_unavailable'); }
  try {
    const timeline = database.prepare('SELECT * FROM main.timeline WHERE id=?')
      .get(collection.originAuthority.originTimelineId);
    if (!timeline || hashJson(timelineSnapshot(timeline))
      !== collection.originTimelineSha256) add('origin_timeline_changed');
    const scope = collection.originAuthority.supersessionScope;
    const latest = latestSupersession(database, scope);
    if (!latest || latest.token !== collection.originAuthority.supersessionToken
      || latest.timelineId !== collection.originAuthority.originTimelineId) {
      add('origin_superseded');
    }
  } catch { add('supersession_dependency_unavailable'); }
  return reasons;
}

function artifactCurrentReasons(collection, verification) {
  const reasons = [];
  const currentHeads = new Map(verification.heads.map((head) => [head.path, head]));
  for (const member of collection.members) {
    const actual = currentHeads.get(member.path);
    const expected = member.headAtCreation;
    if (!actual || actual.versionRef !== expected.versionRef
      || actual.receiptSha256 !== expected.receiptSha256
      || actual.workItemId !== expected.workItemId
      || actual.episodeId !== expected.episodeId) {
      reasons.push({
        contract: 'ArtifactCollectionCurrentReason/v1',
        code: member.classification === 'included'
          ? 'artifact_version_stale' : 'excluded_artifact_head_changed',
        path: member.path, expectedVersionRef: expected.versionRef,
        actualVersionRef: actual?.versionRef || null,
      });
    }
  }
  const expectedInventory = new Map(
    collection.artifactInventory.map((entry) => [entry.path, entry]),
  );
  const actualInventory = new Map(verification.inventory.map((entry) => [entry.path, entry]));
  for (const expected of collection.artifactInventory) {
    const actual = actualInventory.get(expected.path);
    if (!actual || canonicalJson(actual) !== canonicalJson(expected)) reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1',
      code: 'artifact_inventory_entry_changed', path: expected.path,
      expectedVersionRef: expected.versionRef, actualVersionRef: actual?.versionRef || null,
    });
  }
  for (const actual of verification.inventory) {
    if (!expectedInventory.has(actual.path)) reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1',
      code: 'artifact_inventory_new_path', path: actual.path,
      actualVersionRef: actual.versionRef,
    });
  }
  return reasons;
}

function currentState(database, root, limits, collection, snapshot) {
  let observed = null;
  let verification = null;
  let dependencySha256 = null;
  const reasons = [];
  // Threat boundary: SQLite can serialize our durable records, but it cannot
  // lock an external filesystem against host processes. A file can change
  // after the final descriptor/realpath observation and before/after COMMIT.
  // Until every production workspace writer participates in one host-owned
  // durable write-generation/exclusive claim, scans are historical evidence
  // only and MUST NOT mint current/universal authority.
  reasons.push({
    contract: 'ArtifactCollectionCurrentReason/v1',
    code: 'workspace_linearizability_unavailable', path: LOGICAL_ROOT,
    boundary: 'external_filesystem_is_not_serialized_by_sqlite',
  });
  try {
    dependencySha256 = dependencySchemaSha256(database);
    if (dependencySha256 !== collection.dependencySchemaSha256
      || exactDependencyProblem(database)) reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1', code: 'dependency_schema_changed',
      path: LOGICAL_ROOT,
    });
  } catch {
    reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1', code: 'dependency_schema_unavailable',
      path: LOGICAL_ROOT,
    });
  }
  try {
    observed = scanWorkspace(root, limits);
    reasons.push(...workspaceCurrentReasons(snapshot, observed));
  } catch (error) {
    reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1', code: 'workspace_inventory_incomplete',
      path: LOGICAL_ROOT, issueCode: typeof error?.code === 'string' ? error.code : 'io_error',
    });
  }
  reasons.push(...authorityCurrentReasons(database, collection));
  const paths = [...new Set([
    ...collection.members.map((member) => member.path),
    ...(observed?.members || []).map((member) => member.path),
  ])].sort(pathOrder);
  const refs = collection.members.flatMap((member) => [
    member.artifact?.versionRef, member.headAtCreation.versionRef,
  ]).filter(Boolean);
  try {
    verification = productionArtifactVerification({
      workItemId: collection.work.id, episodeId: collection.episode.id,
      versionRefs: refs, paths,
    });
    compareHistoricalArtifactVerification(collection, verification);
    reasons.push(...artifactCurrentReasons(collection, verification));
  } catch {
    reasons.push({
      contract: 'ArtifactCollectionCurrentReason/v1',
      code: 'artifact_store_verification_failed', path: LOGICAL_ROOT,
    });
  }
  const uniqueReasons = [...new Map(reasons.map((reason) => [canonicalJson(reason), reason])).values()]
    .sort(reasonOrder).map((reason) => deepFreeze(reason));
  const core = {
    contract: 'ArtifactCollectionCurrentState/v1', collectionRef: collection.collectionRef,
    collectionSha256: collection.collectionSha256, snapshotRef: snapshot.snapshotRef,
    snapshotSha256: snapshot.snapshotSha256,
    workspaceManifestSha256: observed?.manifestSha256 || null,
    dependencySchemaSha256: dependencySha256,
    artifactInventorySha256: verification?.inventorySha256 || null,
    artifactStoreFenceSha256: verification?.storeFenceSha256 || null,
    current: uniqueReasons.length === 0, reasons: uniqueReasons,
  };
  return deepFreeze({ ...core, stateSha256: hashJson(core) });
}

function currentReceiptFromRow(row) {
  if (!row) return null;
  const receipt = parsedCanonical(row.receipt_json, 'artifact collection current receipt');
  if (receipt.contract !== 'ArtifactCollectionCurrentReceipt/v1'
    || receipt.checkRef !== row.check_ref || receipt.operationKey !== row.operation_key
    || receipt.requestSha256 !== row.request_sha256
    || receipt.collectionRef !== row.collection_ref
    || receipt.collectionSha256 !== row.collection_sha256
    || receipt.checkedAt !== row.checked_ts || receipt.current !== Boolean(row.current)
    || receipt.stateSha256 !== row.state_sha256
    || hashJson(receipt) !== row.receipt_sha256) {
    throw new Error('artifact collection current receipt failed integrity verification');
  }
  if (receipt.current !== Boolean(receipt.cas)
    || (receipt.cas && (receipt.cas.checkRef !== receipt.checkRef
      || hashJson({ ...receipt.cas, casSha256: undefined }) !== receipt.cas.casSha256))) {
    throw new Error('artifact collection current CAS is invalid');
  }
  const stateBody = { ...receipt.state };
  const storedStateSha256 = stateBody.stateSha256;
  delete stateBody.stateSha256;
  if (storedStateSha256 !== receipt.stateSha256
    || hashJson(stateBody) !== receipt.stateSha256
    || receipt.state.current !== receipt.current
    || canonicalJson(receipt.state.reasons) !== canonicalJson(receipt.reasons)) {
    throw new Error('artifact collection current state hash is invalid');
  }
  return deepFreeze({ ...receipt, receiptSha256: row.receipt_sha256 });
}

function consumptionFromRow(row) {
  if (!row) return null;
  const receipt = parsedCanonical(row.receipt_json, 'artifact collection consumption receipt');
  if (receipt.contract !== 'UniversalArtifactCollectionReceipt/v1'
    || !CONSUMPTION_REF.test(String(receipt.consumptionRef || ''))
    || receipt.consumptionRef !== row.consumption_ref
    || receipt.operationKey !== row.operation_key
    || receipt.requestSha256 !== row.request_sha256 || receipt.checkRef !== row.check_ref
    || receipt.collectionRef !== row.collection_ref
    || receipt.collectionSha256 !== row.collection_sha256
    || receipt.stateSha256 !== row.state_sha256 || receipt.consumedAt !== row.consumed_ts
    || hashJson(receipt) !== row.receipt_sha256) {
    throw new Error('universal artifact collection receipt failed integrity verification');
  }
  return deepFreeze(receipt);
}

function assertExplicitDatabase(database) {
  if (!database || typeof database.prepare !== 'function'
    || typeof database.transaction !== 'function' || typeof database.exec !== 'function'
    || typeof database.pragma !== 'function' || typeof database.open !== 'boolean') {
    throw new TypeError('an explicit open better-sqlite3 database is required');
  }
}

export function createArtifactCollectionStore(database, options = {}) {
  assertExplicitDatabase(database);
  if (database !== productionDatabase) {
    throw new TypeError('artifact collections require the exact production database capability');
  }
  if (!options || typeof options !== 'object' || Array.isArray(options)) {
    throw new TypeError('artifact collection options must be an object');
  }
  if (Object.hasOwn(options, 'workspaceRoot')
    || Object.hasOwn(options, 'testWorkspaceRootAuthority')) {
    throw new TypeError('artifact collections require the database-owned configured production workspace');
  }
  const limits = normalizedLimits(options);
  const root = physicalWorkspaceRoot(CONFIGURED_WORKSPACE_ROOT);
  initializeSchema(database);

  let authorityVerifier = null;
  const consumedContexts = new WeakSet();
  const snapshotCapabilities = new WeakMap();
  const collectionCapabilities = new WeakMap();
  const casCapabilities = new WeakMap();

  const status = () => {
    const artifactStatus = artifactVersionStoreStatus();
    const reason = ownedSchemaProblem(database) || exactDependencyProblem(database)
      || (!artifactStatus.available
        ? `production artifact store unavailable: ${artifactStatus.reason || 'unknown'}` : null)
      || (!authorityVerifier ? 'host authority verifier is unavailable' : null);
    return { ready: reason == null, reason };
  };
  const requireOwnedStore = () => {
    const reason = ownedSchemaProblem(database);
    if (reason) throw new Error(`artifact collection store is unavailable: ${reason}`);
  };
  const requireOperationalStore = () => {
    const current = status();
    if (!current.ready) {
      throw new Error(`artifact collection store is unavailable: ${current.reason}`);
    }
  };

  const registerAuthorityVerifier = (verifier) => {
    if (authorityVerifier) throw new Error('artifact collection authority verifier already registered');
    if (typeof verifier !== 'function') throw new TypeError('authority verifier must be a function');
    authorityVerifier = verifier;
  };

  const guardedCallback = (callback, argument, label, normalize, operational = true) => {
    if (operational) requireOperationalStore(); else requireOwnedStore();
    const schemaVersion = mainSchemaVersion(database);
    const dependencySha256 = dependencySchemaSha256(database);
    const changes = Number(database.prepare('SELECT total_changes() AS value').get().value);
    const dataVersion = Number(database.prepare('PRAGMA main.data_version').get().data_version);
    let result;
    let callbackError = null;
    try {
      const candidate = callback?.(argument);
      result = candidate == null ? null : normalize(candidate);
    } catch (error) {
      callbackError = error;
    }
    let boundaryError = null;
    try {
      if (operational) requireOperationalStore(); else requireOwnedStore();
      if (mainSchemaVersion(database) !== schemaVersion
        || dependencySchemaSha256(database) !== dependencySha256
        || Number(database.prepare('SELECT total_changes() AS value').get().value) !== changes
        || Number(database.prepare('PRAGMA main.data_version').get().data_version) !== dataVersion) {
        boundaryError = new Error(`${label} changed protected database state during verification`);
      }
    } catch (error) {
      boundaryError = new Error(`${label} invalidated protected state: ${error?.message || error}`);
    }
    if (boundaryError) throw boundaryError;
    if (callbackError) throw new Error(`${label} failed: ${callbackError?.message || callbackError}`);
    return result;
  };

  const verifiedCandidate = (context, contract) => {
    requireOperationalStore();
    if (!context || typeof context !== 'object' || consumedContexts.has(context)) return null;
    const normalize = contract === 'RecursiveWorkspaceSnapshotAuthority/v1'
      ? normalizeSnapshotAuthority : contract === 'ArtifactCollectionAuthority/v1'
        ? normalizeCollectionAuthority : normalizeCasAuthority;
    const candidate = database.transaction(() => guardedCallback(
      authorityVerifier, context, 'artifact collection host authority verification', normalize, true,
    )).immediate();
    if (!candidate) return null;
    consumedContexts.add(context);
    if (candidate.contract !== contract) throw new TypeError(`invalid ${contract}`);
    return candidate;
  };

  const issueSnapshotCapability = (context) => {
    const authority = verifiedCandidate(context, 'RecursiveWorkspaceSnapshotAuthority/v1');
    if (!authority) return null;
    try { durableDiscovery(database, authority, limits, root); } catch { return null; }
    const capability = Object.freeze({ contract: 'RecursiveWorkspaceSnapshotCapability/v1' });
    snapshotCapabilities.set(capability, authority);
    return capability;
  };

  const issueCollectionCapability = (context) => {
    const authority = verifiedCandidate(context, 'ArtifactCollectionAuthority/v1');
    if (!authority) return null;
    const snapshotValue = snapshotFromRow(database, database.prepare(
      `SELECT * FROM main.${TABLES.snapshots} WHERE snapshot_ref=?`,
    ).get(authority.snapshotRef));
    if (!snapshotValue) return null;
    if (authority.classifications.length !== snapshotValue.members.length
      || authority.classifications.some((item, ordinal) => (
        item.path !== snapshotValue.members[ordinal].path
      ))) {
      throw new TypeError('artifact collection classifications must cover every snapshot member');
    }
    const capability = Object.freeze({ contract: 'ArtifactCollectionCapability/v1' });
    collectionCapabilities.set(capability, authority);
    return capability;
  };

  const issueCollectionCasCapability = () => {
    requireOperationalStore();
    // Deliberately unavailable under the external-filesystem threat boundary
    // documented in currentState(). Keep the API fail-closed for callers and
    // for legacy databases containing an earlier current=true check.
    return null;
  };

  const consume = (registry, capability, expected) => {
    const authority = capability && typeof capability === 'object'
      ? registry.get(capability) : null;
    if (!authority || capability.contract !== expected) {
      throw new Error(`operation requires an exact one-shot capability (${expected})`);
    }
    registry.delete(capability);
    return authority;
  };

  const inspectWorkspace = () => {
    requireOperationalStore();
    return discoveryObservation(root, limits);
  };

  const captureSnapshot = (
    capability,
    { operationKey } = /** @type {{ operationKey?: string }} */ ({}),
  ) => {
    const authority = consume(
      snapshotCapabilities, capability, 'RecursiveWorkspaceSnapshotCapability/v1',
    );
    const key = validOperationKey(operationKey);
    const transaction = database.transaction(() => {
      requireOperationalStore();
      const durable = durableDiscovery(database, authority, limits, root);
      const replay = database.prepare(`SELECT * FROM main.${TABLES.snapshots}
        WHERE operation_key=?`).get(key);
      if (replay) {
        if (replay.discovery_execution_key !== authority.discoveryExecutionKey
          || replay.discovery_completion_sha256 !== authority.discoveryCompletionSha256
          || replay.manifest_sha256 !== durable.manifestSha256) {
          throw new Error('snapshot operationKey is bound to another request');
        }
        return snapshotFromRow(database, replay);
      }
      const duplicate = database.prepare(`SELECT 1 FROM main.${TABLES.snapshots}
        WHERE discovery_execution_key=? OR discovery_completion_sha256=?`).get(
        authority.discoveryExecutionKey, authority.discoveryCompletionSha256,
      );
      if (duplicate) throw new Error('snapshot request is already bound to another operationKey');
      const dependencySha256 = dependencySchemaSha256(database);
      const observed = scanWorkspace(root, limits);
      if (canonicalJson(durable) !== canonicalJson(observed)) {
        throw new Error('durable discovery manifest conflicts because workspace changed');
      }
      const endObserved = scanWorkspace(root, limits);
      if (canonicalJson(observed) !== canonicalJson(endObserved)
        || dependencySha256 !== dependencySchemaSha256(database)) {
        throw new Error('workspace or dependency schema changed during snapshot capture');
      }
      return insertSnapshot(database, authority, durable, key, dependencySha256);
    });
    return transaction.immediate();
  };

  const snapshot = (snapshotRef) => {
    requireOwnedStore();
    const reference = snapshotRef;
    if (typeof reference !== 'string') throw new TypeError('snapshotRef is invalid');
    if (!SNAPSHOT_REF.test(reference)) throw new TypeError('snapshotRef is invalid');
    return snapshotFromRow(database, database.prepare(
      `SELECT * FROM main.${TABLES.snapshots} WHERE snapshot_ref=?`,
    ).get(reference));
  };

  const historicalCollection = (collectionRef) => {
    requireOwnedStore();
    const reference = collectionRef;
    if (typeof reference !== 'string') throw new TypeError('collectionRef is invalid');
    if (!COLLECTION_REF.test(reference)) throw new TypeError('collectionRef is invalid');
    return collectionFromRow(database, database.prepare(
      `SELECT * FROM main.${TABLES.collections} WHERE collection_ref=?`,
    ).get(reference), productionHistoricalArtifactVerification);
  };

  const createCollection = (
    capability,
    { operationKey } = /** @type {{ operationKey?: string }} */ ({}),
  ) => {
    const authority = consume(
      collectionCapabilities, capability, 'ArtifactCollectionCapability/v1',
    );
    const key = validOperationKey(operationKey);
    const transaction = database.transaction(() => {
      requireOperationalStore();
      const authoritySha256 = collectionAuthoritySha256(authority);
      const replay = database.prepare(`SELECT * FROM main.${TABLES.collections}
        WHERE operation_key=?`).get(key);
      if (replay) {
        if (replay.authority_sha256 !== authoritySha256) {
          throw new Error('collection operationKey is bound to another request');
        }
        return collectionFromRow(database, replay, productionHistoricalArtifactVerification);
      }
      if (database.prepare(`SELECT 1 FROM main.${TABLES.collections}
        WHERE authority_sha256=?`).get(authoritySha256)) {
        throw new Error('collection request is already bound to another operationKey');
      }
      const snapshotValue = snapshotFromRow(database, database.prepare(
        `SELECT * FROM main.${TABLES.snapshots} WHERE snapshot_ref=?`,
      ).get(authority.snapshotRef));
      if (!snapshotValue) throw new Error('artifact collection snapshot is missing');
      const bound = boundAuthority(database, authority);
      const dependencySha256 = dependencySchemaSha256(database);
      const observed = scanWorkspace(root, limits);
      if (observed.manifestSha256 !== snapshotValue.manifestSha256
        || canonicalJson(observed.members) !== canonicalJson(snapshotValue.members)
        || canonicalJson(observed.directories) !== canonicalJson(snapshotValue.directories)) {
        throw new Error('artifact collection snapshot is not current');
      }
      const refs = authority.classifications.map((item) => item.versionRef).filter(Boolean);
      const artifactRequest = {
        workItemId: authority.workItemId,
        episodeId: authority.episodeId, versionRefs: refs,
        paths: snapshotValue.members.map((member) => member.path),
      };
      const verification = productionArtifactVerification(artifactRequest);
      if (verification.workRevision !== authority.expectedWorkRevision
        || verification.episodeRevision !== authority.expectedEpisodeRevision) {
        throw new Error('production artifact store authority revisions are stale');
      }
      const members = preparedMembers(snapshotValue, authority, verification);
      const endBound = boundAuthority(database, authority);
      const endObserved = scanWorkspace(root, limits);
      const endVerification = productionArtifactVerification(artifactRequest);
      const commitObserved = scanWorkspace(root, limits);
      if (canonicalJson(bound) !== canonicalJson(endBound)
        || canonicalJson(observed) !== canonicalJson(endObserved)
        || canonicalJson(endObserved) !== canonicalJson(commitObserved)
        || canonicalJson(verification) !== canonicalJson(endVerification)
        || dependencySha256 !== dependencySchemaSha256(database)) {
        throw new Error('collection authority, workspace, artifact store, or schema changed');
      }
      return insertCollection(database, {
        authority, snapshot: snapshotValue, bound, members, verification: endVerification,
        dependencySha256, operationKey: key,
      });
    });
    return transaction.immediate();
  };

  const collectionCurrentReceipt = (
    collectionRef,
    { operationKey } = /** @type {{ operationKey?: string }} */ ({}),
  ) => {
    const reference = collectionRef;
    if (typeof reference !== 'string') throw new TypeError('collectionRef is invalid');
    if (!COLLECTION_REF.test(reference)) throw new TypeError('collectionRef is invalid');
    const key = validOperationKey(operationKey);
    const transaction = database.transaction(() => {
      requireOwnedStore();
      const collectionValue = historicalCollection(reference);
      if (!collectionValue) throw new Error('artifact collection is missing');
      const request = {
        contract: 'ArtifactCollectionCurrentRequest/v1', operationKey: key,
        collectionRef: reference, collectionSha256: collectionValue.collectionSha256,
      };
      const requestSha256 = hashJson(request);
      const replay = database.prepare(`SELECT * FROM main.${TABLES.checks}
        WHERE operation_key=?`).get(key);
      if (replay) {
        if (replay.request_sha256 !== requestSha256) {
          throw new Error('current-check operationKey is bound to another request');
        }
        const stored = currentReceiptFromRow(replay);
        if (stored.current || stored.cas) {
          throw new Error('legacy scan-only current receipt is non-authoritative');
        }
        return stored;
      }
      const snapshotValue = snapshotFromRow(database, database.prepare(
        `SELECT * FROM main.${TABLES.snapshots} WHERE snapshot_ref=?`,
      ).get(collectionValue.snapshotRef));
      const first = currentState(
        database, root, limits, collectionValue, snapshotValue,
      );
      const last = currentState(
        database, root, limits, collectionValue, snapshotValue,
      );
      if (canonicalJson(first) !== canonicalJson(last)) {
        throw new Error('collection state changed during durable current check');
      }
      if (last.current) {
        const commitObserved = scanWorkspace(root, limits);
        if (commitObserved.manifestSha256 !== last.workspaceManifestSha256
          || workspaceCurrentReasons(snapshotValue, commitObserved).length) {
          throw new Error('workspace changed at the durable current-check commit fence');
        }
      }
      const checkedAt = nowIso();
      const checkRef = `artifact-collection-check:${hashJson({
        requestSha256, stateSha256: first.stateSha256,
      })}`;
      const casCore = first.current ? {
        contract: 'ArtifactCollectionCas/v1', checkRef,
        collectionRef: reference, collectionSha256: collectionValue.collectionSha256,
        stateSha256: first.stateSha256,
        workspaceManifestSha256: first.workspaceManifestSha256,
        dependencySchemaSha256: first.dependencySchemaSha256,
        artifactInventorySha256: first.artifactInventorySha256,
        artifactStoreFenceSha256: first.artifactStoreFenceSha256,
      } : null;
      const cas = casCore ? { ...casCore, casSha256: hashJson(casCore) } : null;
      const receipt = deepFreeze({
        contract: 'ArtifactCollectionCurrentReceipt/v1', checkRef,
        operationKey: key, requestSha256, checkedAt,
        collectionRef: reference, collectionSha256: collectionValue.collectionSha256,
        current: first.current, reasons: first.reasons,
        stateSha256: first.stateSha256, state: first, cas,
      });
      database.prepare(`INSERT INTO main.${TABLES.checks} (
        check_ref,contract,operation_key,request_sha256,collection_ref,
        collection_sha256,checked_ts,current,state_sha256,receipt_json,receipt_sha256
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
        checkRef, receipt.contract, key, requestSha256, reference,
        collectionValue.collectionSha256, checkedAt, first.current ? 1 : 0,
        first.stateSha256, canonicalJson(receipt), hashJson(receipt),
      );
      return currentReceiptFromRow(database.prepare(
        `SELECT * FROM main.${TABLES.checks} WHERE check_ref=?`,
      ).get(checkRef));
    });
    return transaction.immediate();
  };

  const consumeCollectionCas = (
    capability,
    { operationKey } = /** @type {{ operationKey?: string }} */ ({}),
  ) => {
    const authority = consume(
      casCapabilities, capability, 'ArtifactCollectionCasCapability/v1',
    );
    const key = validOperationKey(operationKey);
    const transaction = database.transaction(() => {
      requireOperationalStore();
      const check = currentReceiptFromRow(database.prepare(
        `SELECT * FROM main.${TABLES.checks} WHERE check_ref=?`,
      ).get(authority.checkRef));
      if (!check?.current || !check.cas || check.cas.casSha256 !== authority.casSha256) {
        throw new Error('artifact collection CAS is not a complete current check');
      }
      const request = {
        contract: 'UniversalArtifactCollectionRequest/v1', operationKey: key,
        checkRef: check.checkRef, receiptSha256: check.receiptSha256,
        casSha256: check.cas.casSha256, collectionRef: check.collectionRef,
        collectionSha256: check.collectionSha256, stateSha256: check.stateSha256,
      };
      const requestSha256 = hashJson(request);
      const replay = database.prepare(`SELECT * FROM main.${TABLES.consumptions}
        WHERE operation_key=?`).get(key);
      if (replay) {
        if (replay.request_sha256 !== requestSha256) {
          throw new Error('CAS consumption operationKey is bound to another request');
        }
        return consumptionFromRow(replay);
      }
      if (database.prepare(`SELECT 1 FROM main.${TABLES.consumptions}
        WHERE check_ref=?`).get(check.checkRef)) {
        throw new Error('artifact collection CAS was already consumed');
      }
      const collectionValue = historicalCollection(check.collectionRef);
      const snapshotValue = snapshotFromRow(database, database.prepare(
        `SELECT * FROM main.${TABLES.snapshots} WHERE snapshot_ref=?`,
      ).get(collectionValue.snapshotRef));
      const first = currentState(
        database, root, limits, collectionValue, snapshotValue,
      );
      const last = currentState(
        database, root, limits, collectionValue, snapshotValue,
      );
      if (!first.current || first.stateSha256 !== check.stateSha256
        || canonicalJson(first) !== canonicalJson(last)) {
        throw new Error('artifact collection CAS is stale and cannot authorize universal use');
      }
      const commitObserved = scanWorkspace(root, limits);
      if (commitObserved.manifestSha256 !== last.workspaceManifestSha256
        || workspaceCurrentReasons(snapshotValue, commitObserved).length) {
        throw new Error('artifact collection CAS workspace fence changed before consumption');
      }
      const consumedAt = nowIso();
      const consumptionRef = `artifact-collection-consumption:${hashJson({
        requestSha256, checkRef: check.checkRef,
      })}`;
      const receipt = deepFreeze({
        contract: 'UniversalArtifactCollectionReceipt/v1', consumptionRef,
        operationKey: key, requestSha256, consumedAt, checkRef: check.checkRef,
        checkReceiptSha256: check.receiptSha256, casSha256: check.cas.casSha256,
        collectionRef: check.collectionRef, collectionSha256: check.collectionSha256,
        stateSha256: check.stateSha256,
      });
      database.prepare(`INSERT INTO main.${TABLES.consumptions} (
        consumption_ref,contract,operation_key,request_sha256,check_ref,
        collection_ref,collection_sha256,state_sha256,consumed_ts,receipt_json,receipt_sha256
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
        consumptionRef, receipt.contract, key, requestSha256, check.checkRef,
        check.collectionRef, check.collectionSha256, check.stateSha256,
        consumedAt, canonicalJson(receipt), hashJson(receipt),
      );
      return consumptionFromRow(database.prepare(
        `SELECT * FROM main.${TABLES.consumptions} WHERE consumption_ref=?`,
      ).get(consumptionRef));
    });
    return transaction.immediate();
  };

  return Object.freeze({
    status, registerAuthorityVerifier,
    inspectWorkspace, issueSnapshotCapability, captureSnapshot, snapshot,
    issueCollectionCapability, createCollection, collection: historicalCollection,
    collectionCurrentReceipt, issueCollectionCasCapability, consumeCollectionCas,
  });
}

export const artifactCollectionContracts = Object.freeze({
  discovery: 'RecursiveWorkspaceDiscovery/v1',
  discoveryReceipt: 'RecursiveWorkspaceDiscoveryReceipt/v1',
  snapshotAuthority: 'RecursiveWorkspaceSnapshotAuthority/v1',
  snapshotCapability: 'RecursiveWorkspaceSnapshotCapability/v1',
  snapshot: 'RecursiveWorkspaceSnapshot/v1',
  snapshotMember: 'RecursiveWorkspaceSnapshotMember/v1',
  collectionAuthority: 'ArtifactCollectionAuthority/v1',
  collectionCapability: 'ArtifactCollectionCapability/v1',
  predicate: 'ArtifactCollectionPredicate/v1',
  memberReason: 'ArtifactCollectionMemberReason/v1',
  collection: 'ArtifactCollection/v1',
  collectionMember: 'ArtifactCollectionMember/v1',
  artifactStoreVerification: 'ArtifactCollectionStoreVerification/v1',
  currentReceipt: 'ArtifactCollectionCurrentReceipt/v1',
  cas: 'ArtifactCollectionCas/v1',
  casAuthority: 'ArtifactCollectionCasAuthority/v1',
  casCapability: 'ArtifactCollectionCasCapability/v1',
  universalReceipt: 'UniversalArtifactCollectionReceipt/v1',
  excludedReasonCodes: EXCLUDED_REASON_CODES,
});
