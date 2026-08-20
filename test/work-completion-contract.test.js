import './isolated-data-dir.js';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as wait } from 'node:timers/promises';
import test, { after } from 'node:test';

import Database from 'better-sqlite3';

import {
  EXECUTION_LEDGER_INDEX_SQL,
  EXECUTION_LEDGER_TABLE_SQL,
  EXECUTION_LEDGER_TRIGGER_SQL,
} from '../src/executionledger.js';
import { initializeTurnUnderstandingAuthorityStore } from '../src/turnauthority.js';
import { seedAuthorizedTurnUnderstanding } from './turn-understanding-authority-fixture.js';
import {
  createPreparedUnderstandingTurn,
  createTurnUnderstandingRecord,
} from '../src/mind/understanding.js';
import {
  WORK_COMPLETION_CONTRACT_VERSION,
  createWorkCompletionPlanSeal,
  createWorkCompletionStore,
  inspectWorkCompletionHostSchema,
} from '../src/mind/workcompletion.js';

assert.equal(WORK_COMPLETION_CONTRACT_VERSION, 3);

const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-completion-v3-'));
const opened = [];
let sequence = 0;

after(() => {
  for (const database of opened) if (database.open) database.close();
  fs.rmSync(temporaryRoot, { recursive: true, force: true });
});

function canonicalJson(value) {
  if (value === undefined) return undefined;
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item) ?? 'null').join(',')}]`;
  }
  return `{${Object.keys(value).sort().filter((key) => value[key] !== undefined)
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const hashJson = (value) => sha256(canonicalJson(value));
const clone = (value) => structuredClone(value);

function openProductionHost(label = 'host') {
  sequence += 1;
  const file = path.join(temporaryRoot, `${label}-${sequence}.sqlite`);
  const database = new Database(file);
  database.pragma('foreign_keys = ON');
  database.pragma('journal_mode = WAL');
  database.exec(`
    CREATE TABLE timeline (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      kind TEXT NOT NULL,
      content TEXT NOT NULL,
      meta TEXT,
      outcome_key TEXT
    );
    CREATE INDEX idx_timeline_ts ON timeline(ts);
    CREATE INDEX idx_timeline_kind ON timeline(kind);
    CREATE INDEX idx_timeline_understanding_conversation
      ON timeline(kind, json_extract(meta, '$.conversationId'), id)
      WHERE kind='turn_understanding';
    CREATE UNIQUE INDEX idx_timeline_contribution_outcome_key
      ON timeline(outcome_key)
      WHERE kind='contribution' AND outcome_key IS NOT NULL;

    CREATE TABLE messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      channel TEXT NOT NULL,
      conversation_id TEXT,
      sender TEXT NOT NULL,
      role TEXT NOT NULL,
      content TEXT NOT NULL
    );
    CREATE INDEX idx_messages_ts ON messages(ts);
    CREATE INDEX idx_messages_conversation ON messages(conversation_id, id);

    ${EXECUTION_LEDGER_TABLE_SQL};
    ${Object.values(EXECUTION_LEDGER_INDEX_SQL).join(';\n')};
    ${Object.values(EXECUTION_LEDGER_TRIGGER_SQL).join(';\n')};

    CREATE TABLE eva_work_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_ts TEXT NOT NULL,
      updated_ts TEXT NOT NULL,
      source TEXT NOT NULL,
      owner TEXT,
      title TEXT NOT NULL,
      goal TEXT NOT NULL,
      next_step TEXT,
      status TEXT NOT NULL DEFAULT 'open',
      evidence TEXT,
      last_action TEXT,
      result TEXT,
      visible_to_family INTEGER NOT NULL DEFAULT 1,
      definition_of_done TEXT,
      current_step TEXT,
      state_json TEXT,
      wait_json TEXT,
      priority INTEGER NOT NULL DEFAULT 50,
      next_wake_ts TEXT,
      revision INTEGER NOT NULL DEFAULT 0,
      lease_owner TEXT,
      lease_until TEXT,
      attempt_count INTEGER NOT NULL DEFAULT 0,
      last_error TEXT,
      interrupted_ts TEXT
    );
    CREATE INDEX idx_eva_work_items_status
      ON eva_work_items(status, updated_ts);
    CREATE INDEX idx_eva_work_wake
      ON eva_work_items(status, next_wake_ts, priority);

    CREATE TABLE eva_goal_episodes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      episode_key TEXT NOT NULL UNIQUE,
      goal_id INTEGER NOT NULL,
      project_id INTEGER NOT NULL,
      work_item_id INTEGER,
      owner_actor TEXT NOT NULL DEFAULT 'eva',
      origin_kind TEXT NOT NULL,
      origin_channel TEXT,
      origin_conversation_id TEXT,
      origin_message_id TEXT,
      requester TEXT,
      requested_outcome TEXT NOT NULL,
      delivery_policy TEXT NOT NULL DEFAULT 'required',
      delivery_target TEXT,
      state TEXT NOT NULL DEFAULT 'committed',
      next_action TEXT,
      checkpoint TEXT,
      proof_summary TEXT,
      wake_ts TEXT,
      lease_owner TEXT,
      lease_until TEXT,
      created_ts TEXT NOT NULL,
      updated_ts TEXT NOT NULL,
      closed_ts TEXT,
      revision INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_eva_goal_episodes_work
      ON eva_goal_episodes(work_item_id, state);
    CREATE UNIQUE INDEX idx_eva_goal_episode_one_owner
      ON eva_goal_episodes(work_item_id) WHERE work_item_id IS NOT NULL;
    CREATE INDEX idx_eva_goal_episodes_delivery
      ON eva_goal_episodes(delivery_policy, state, updated_ts);

    CREATE TABLE eva_episode_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      episode_id INTEGER NOT NULL,
      ts TEXT NOT NULL,
      kind TEXT NOT NULL,
      session_ref TEXT,
      transition_key TEXT,
      payload_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX idx_eva_episode_events_episode
      ON eva_episode_events(episode_id, id);
    CREATE UNIQUE INDEX idx_eva_episode_transition_key
      ON eva_episode_events(episode_id, transition_key)
      WHERE transition_key IS NOT NULL;

    CREATE TABLE eva_episode_origins (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      episode_id INTEGER NOT NULL,
      origin_key TEXT NOT NULL UNIQUE,
      origin_kind TEXT NOT NULL,
      origin_channel TEXT,
      origin_conversation_id TEXT,
      origin_message_id TEXT,
      requester TEXT,
      attached_ts TEXT NOT NULL
    );
    CREATE INDEX idx_eva_episode_origins_episode
      ON eva_episode_origins(episode_id, attached_ts);
    CREATE INDEX idx_eva_episode_origins_conversation
      ON eva_episode_origins(origin_conversation_id, episode_id);

    CREATE TABLE eva_interpretation_retry_handoffs (
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
    );
    CREATE INDEX idx_eva_interpretation_retry_origin
      ON eva_interpretation_retry_handoffs(
        origin_channel, origin_conversation_id, origin_message_id
      );

    CREATE TABLE eva_delivery_receipts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      episode_id INTEGER NOT NULL,
      origin_conversation_id TEXT,
      origin_message_id TEXT,
      delivered_conversation_id TEXT,
      delivered_channel TEXT NOT NULL,
      delivery_target TEXT,
      result_text TEXT NOT NULL,
      proof_summary TEXT,
      idempotency_key TEXT NOT NULL UNIQUE,
      delivered_ts TEXT NOT NULL,
      transport_json TEXT NOT NULL DEFAULT '{}'
    );
  `);
  installProductionHandoffTriggers(database);
  initializeTurnUnderstandingAuthorityStore(database);
  opened.push(database);
  return { database, file };
}

function installProductionHandoffTriggers(database) {
  database.exec(`
    CREATE TRIGGER trg_interpretation_retry_handoff_insert_guard
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
    END;
    CREATE TRIGGER trg_interpretation_retry_handoff_immutable_update
    BEFORE UPDATE ON eva_interpretation_retry_handoffs
    BEGIN
      SELECT RAISE(ABORT, 'ChatInterpretationRetry/v1-registry is onveranderlijk');
    END;
    CREATE TRIGGER trg_interpretation_retry_handoff_immutable_delete
    BEFORE DELETE ON eva_interpretation_retry_handoffs
    BEGIN
      SELECT RAISE(ABORT, 'ChatInterpretationRetry/v1-registry is onveranderlijk');
    END;
  `);
}

function iso(offsetMs = 0) {
  return new Date(Date.now() + offsetMs).toISOString();
}

function issuedUnderstanding(text, workItemId) {
  const sourceSpans = [{ start: 0, end: text.length }];
  const body = {
    contract: 'TurnUnderstanding/v1',
    source: {
      kind: 'model', turnId: `issued:${workItemId}`,
      messageId: `source:${workItemId}`, textSha256: sha256(text),
    },
    dialogueAct: 'command',
    goal: {
      statement: text,
      outcome: 'Het resultaat is exact geschreven, teruggelezen en afgerond.',
      beneficiary: 'requester',
    },
    conditions: [{
      statement: 'Rond pas af nadat het materiaal exact is teruggelezen.',
      role: 'completion', state: 'required', requirementIds: ['r2'],
      afterRequirementIds: ['r1'], sourceSpans,
    }],
    provenance: {}, confidence: 1,
    temporal: {
      mode: 'now', notBefore: null, dueBy: null, condition: null,
      startSourceSpans: [],
    },
    durability: 'turn',
    delivery: { mode: 'reply', target: 'origin' },
    requestedEffects: ['workspace', 'persistent'], deniedEffects: [],
    proofNeeds: ['verified_effect'], ambiguities: [], monitoring: null,
    actions: [
      {
        requirementId: 'r1', capabilityId: 'workspace.file.write',
        operationId: 'write', polarity: 'request', effect: 'workspace',
        authorization: 'allowed',
        semanticInput: {
          pad: `data/workspace/completion-${workItemId}.md`,
          inhoud: `# Voltooid ${workItemId}\n`,
        },
        sourceSpans, boundaryReasons: [],
      },
      {
        requirementId: 'r2', capabilityId: 'work.episode.manage',
        operationId: 'afronden', polarity: 'request', effect: 'persistent',
        authorization: 'allowed',
        semanticInput: {
          bewerking: 'afronden', id: workItemId,
          resultaat: 'Het resultaat is gecontroleerd en duurzaam.',
          verwachte_revisie: 7,
        },
        sourceSpans, boundaryReasons: [],
      },
    ],
    firstDecision: {
      kind: 'act', requirementIds: ['r1', 'r2'], replyDraft: null,
    },
    decisionProjection: {
      proposedKind: 'act', proposedRequirementIds: ['r1', 'r2'],
      acceptedRequirementIds: ['r1', 'r2'], rejectedRequirementIds: [],
      unknownRequirementIds: [], hostAddedRequirementIds: [],
    },
  };
  return { ...body, sha256: hashJson(body) };
}

function productionPlans(record, workItemId) {
  const semantic = (requirementId, capabilityId, operationId) => ({
    understandingSha256: record.sourceSha256,
    requirementId, addressesRequirementIds: [requirementId], basisReceiptIds: [],
    capabilityId, operationId, role: 'primary', executionType: null,
  });
  const proofOperations = [
    { capabilityId: 'workspace.file.write', operationId: 'write', type: 'write' },
    { capabilityId: 'workspace.file.read', operationId: 'read', type: 'readback' },
  ];
  const recoveryOperations = [
    { capabilityId: 'workspace.file.read', operationId: 'read', type: 'inspect' },
    { capabilityId: 'workspace.file.write', operationId: 'write', type: 'repair' },
  ];
  return [
    {
      requirementId: 'r1', capabilityId: 'workspace.file.write', operationId: 'write',
      toolName: 'schrijf_bestand', args: {
        pad: `data/workspace/completion-${workItemId}.md`,
        inhoud: `# Voltooid ${workItemId}\n`,
      },
      effect: 'workspace', idempotency: 'turn_scoped',
      proofFloor: ['verified_effect', 'material_readback'], supportsDeferred: false,
      execution: {
        mode: 'iterative', materializable: ['inhoud'],
        target: { field: 'pad', boundary: 'workspace', sameDirectory: true,
          artifactLimit: 4 },
        proofOperations, recoveryOperations, fileArguments: null,
      },
      proofOperations, recoveryOperations,
      semanticBinding: semantic('r1', 'workspace.file.write', 'write'),
    },
    {
      requirementId: 'r2', capabilityId: 'work.episode.manage',
      operationId: 'afronden', toolName: 'werk_bijhouden',
      args: {
        bewerking: 'afronden', id: workItemId,
        resultaat: 'Het resultaat is gecontroleerd en duurzaam.',
        verwachte_revisie: 7,
      },
      effect: 'persistent', idempotency: 'cas', proofFloor: ['verified_effect'],
      supportsDeferred: false,
      execution: {
        mode: 'direct', materializable: [], target: null,
        proofOperations: [], recoveryOperations: [], fileArguments: null,
      },
      proofOperations: [], recoveryOperations: [],
      semanticBinding: semantic('r2', 'work.episode.manage', 'afronden'),
    },
  ];
}

function insertProductionExecution(database, {
  executionKey, turnId, tool, args, semanticBinding = null,
  status = 'done', output = null, executionContract = 'ExecutionRecord/v1',
}) {
  const storedArgs = canonicalJson(args);
  const storedBinding = semanticBinding == null ? null : canonicalJson(semanticBinding);
  const semanticHash = hashJson({
    turnId, tool, args, binding: semanticBinding,
  });
  const executionSeq = Number(database.prepare(`SELECT coalesce(max(execution_seq),0)+1 AS value
    FROM main.turn_tool_executions`).get().value);
  const intentBody = {
    turnId, tool, args, binding: semanticBinding, semanticHash,
  };
  const intentRecordSha256 = executionContract === 'ExecutionRecord/v1'
    ? hashJson(intentBody)
    : hashJson({ executionContract, executionSeq, ...intentBody });
  const startedTs = iso(-100);
  const finishedTs = status === 'done' ? iso() : null;
  const storedOutput = status === 'done' ? JSON.stringify(output) : null;
  const outputSha256 = status === 'done' ? sha256(storedOutput) : null;
  const candidateMaterial = output?.data?.material_sha256
    || output?.artifact_receipt?.sha256 || null;
  const materialSha256 = /^[a-f0-9]{64}$/u.test(String(candidateMaterial || ''))
    ? candidateMaterial : null;
  const completionBody = {
    executionKey, turnId, tool, args: storedArgs, semanticHash,
    semanticBinding: storedBinding, intentRecordSha256, status,
    startedTs, finishedTs, outputSha256, materialSha256,
  };
  const completionRecordSha256 = status !== 'done' ? null
    : executionContract === 'ExecutionRecord/v1'
      ? hashJson(completionBody)
      : hashJson({ executionContract, executionSeq, ...completionBody });
  database.prepare(`INSERT INTO main.turn_tool_executions
    (execution_key,turn_id,tool,args,semantic_hash,semantic_binding,
     intent_record_sha256,status,started_ts,finished_ts,output,output_sha256,
     material_sha256,completion_record_sha256,execution_seq,execution_contract,
     execution_verification)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    executionKey, turnId, tool, storedArgs, semanticHash, storedBinding,
    intentRecordSha256, status, startedTs, finishedTs, storedOutput,
    outputSha256, materialSha256, completionRecordSha256, executionSeq,
    executionContract, 'verified',
  );
  return database.prepare(`SELECT * FROM
    main.turn_tool_executions WHERE execution_key=?`).get(executionKey);
}

function completeResult(tool, data, { changed = true } = {}) {
  return {
    contract: 'ActionResult/v1', tool, ok: true, changed, verified: true,
    evidence: { source: tool, summary: 'Gecontroleerd.' },
    needs_followup: false, partial: false, risk: 'normal', human_message: '', data,
  };
}

function failedResult(tool) {
  return {
    contract: 'ActionResult/v1', tool, ok: false, changed: false, verified: false,
    evidence: null, needs_followup: true, partial: false,
    failure_class: 'transient', retryable: true, effect_uncertain: false,
    risk: 'normal', human_message: '', error: 'tijdelijke fout', data: {},
  };
}

function childBinding(record, {
  requirementId = 'r1', capabilityId, operationId, role = 'proof',
  executionType, basisReceiptIds = [],
}) {
  return {
    understandingSha256: record.sourceSha256,
    requirementId, addressesRequirementIds: [requirementId], basisReceiptIds,
    capabilityId, operationId, role, executionType,
  };
}

function createFixture(label, {
  deliveryPolicy = 'required', ingressExecutionContract = 'ExecutionRecord/v2',
} = {}) {
  const { database, file } = openProductionHost(label);
  const workItemId = sequence * 10 + 1;
  const episodeId = sequence * 10 + 2;
  const requester = 'Jaap';
  const conversationId = `web:completion:${workItemId}`;
  const originMessageId = `request-${workItemId}`;
  const supersessionScope = `web:${conversationId}`;
  const supersessionToken = `token-${workItemId}`;
  const text = `Schrijf het resultaat voor ${label}, lees het terug en rond het werk af.`;
  const understanding = issuedUnderstanding(text, workItemId);
  const record = createTurnUnderstandingRecord(understanding);
  const plans = productionPlans(record, workItemId);
  const planSeal = createWorkCompletionPlanSeal({
    decision: { kind: 'act', requirementIds: ['r1', 'r2'], replyDraft: null },
    plans,
  });
  const createdAt = new Date(Date.now() - 1_000);
  const rawOriginalTextSha256 = sha256(text);
  const originalTextSha256 = sha256(text);
  const handoffKey = hashJson({
    contract: 'ChatInterpretationRetry/v1', channel: 'web', conversationId,
    messageId: originMessageId, rawOriginalTextSha256, originalTextSha256,
  });
  const origin = {
    channel: 'web', conversationId, messageId: originMessageId, requester,
    deliveryPolicy, deliveryTarget: deliveryPolicy === 'required' ? requester : '',
  };
  const retry = {
    contract: 'ChatInterpretationRetry/v1', handoffKey,
    rawOriginalTextSha256, originalText: text, originalTextSha256,
    originSha256: sha256(JSON.stringify(origin)),
    createdAt: createdAt.toISOString(),
    deadlineAt: new Date(createdAt.getTime() + 6 * 60 * 60_000).toISOString(),
    attempts: 0, phase: 'understood', lastFailureClass: 'temporary',
    planSha256: planSeal.sha256, understandingSha256: record.sourceSha256,
    semanticAuthoritySha256: sha256(`semantic:${label}`),
    materialAuthoritySha256: sha256(`material:${label}`),
    supersessionScope, supersessionToken,
    supersessionSha256: sha256(`${supersessionScope}\n${supersessionToken}`),
  };
  database.prepare(`INSERT INTO main.eva_work_items
    (id,created_ts,updated_ts,source,owner,title,goal,next_step,status,evidence,
     last_action,result,visible_to_family,definition_of_done,current_step,state_json,
     wait_json,priority,next_wake_ts,revision,lease_owner,lease_until,attempt_count,
     last_error,interrupted_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`)
    .run(workItemId, iso(-2_000), iso(-1_000), 'web', requester, `Werk ${label}`,
      `Doel ${label}`, 'Schrijf en controleer.', 'open', null, null, null, 1,
      'Exact materiaal plus readback en afronding.', 'uitvoeren',
      JSON.stringify({ interpretation_retry: retry }), null, 50, null, 7,
      null, null, 0, null, null);
  database.prepare(`INSERT INTO main.eva_goal_episodes
    (id,episode_key,goal_id,project_id,work_item_id,owner_actor,origin_kind,
     origin_channel,origin_conversation_id,origin_message_id,requester,
     requested_outcome,delivery_policy,delivery_target,state,next_action,checkpoint,
     proof_summary,wake_ts,lease_owner,lease_until,created_ts,updated_ts,closed_ts,revision)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    episodeId, `episode:${episodeId}`, 1, 1, workItemId, 'eva', 'user', 'web',
    conversationId, originMessageId, requester, `Uitkomst ${label}`, deliveryPolicy,
    deliveryPolicy === 'required' ? requester : '', 'executing',
    'Schrijf en controleer.', null, null, null, null, null,
    iso(-2_000), iso(-1_000), null, 11,
  );
  database.prepare(`INSERT INTO main.eva_episode_origins
    (episode_id,origin_key,origin_kind,origin_channel,origin_conversation_id,
     origin_message_id,requester,attached_ts) VALUES (?,?,?,?,?,?,?,?)`).run(
    episodeId, `origin:${episodeId}`, 'user', 'web', conversationId,
    originMessageId, requester, iso(-1_500),
  );
  database.prepare(`INSERT INTO main.eva_episode_events
    (episode_id,ts,kind,session_ref,transition_key,payload_json)
    VALUES (?,?,?,?,?,?)`).run(
    episodeId, iso(-1_000), 'state:executing', `turn:${label}`,
    `initial:${episodeId}`, JSON.stringify({ from: 'waiting', to: 'executing' }),
  );
  const bindingSha256 = hashJson({
    handoffKey, rawOriginalTextSha256, originalTextSha256,
    requesterSha256: sha256(requester),
    supersessionScopeSha256: sha256(supersessionScope),
    supersessionTokenSha256: sha256(supersessionToken),
    workItemId, episodeId,
  });
  database.prepare(`INSERT INTO main.eva_interpretation_retry_handoffs
    (handoff_key,contract,origin_channel,origin_conversation_id,origin_message_id,
     raw_original_text_sha256,original_text_sha256,requester_sha256,
     supersession_scope_sha256,supersession_token_sha256,work_item_id,episode_id,
     binding_sha256,created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    handoffKey, 'ChatInterpretationRetry/v1', 'web', conversationId,
    originMessageId, rawOriginalTextSha256, originalTextSha256, sha256(requester),
    sha256(supersessionScope), sha256(supersessionToken), workItemId, episodeId,
    bindingSha256, createdAt.toISOString(),
  );
  database.prepare(`INSERT INTO main.messages
    (ts,channel,conversation_id,sender,role,content) VALUES (?,?,?,?,?,?)`).run(
    iso(-800), 'web', conversationId, requester, 'user', text,
  );
  const turnId = `turn:${label}`;
  database.prepare(`INSERT INTO main.timeline
    (ts,kind,content,meta,outcome_key) VALUES (?,?,?,?,NULL)`).run(
    iso(-700), 'turn_understanding', 'command: exact resultaat', JSON.stringify({
      internalOnly: true, turnId, conversationId, source: 'model', attempts: 1,
      attemptFailures: [], understanding: record,
      interpretationSucceeded: true, error: null,
    }),
  );
  const prepared = createPreparedUnderstandingTurn({
    channel: 'web', conversationId, originMessageId, turnId: `ingress:${label}`,
    reply: '', workItemId, goalEpisodeId: episodeId, syntheticTest: false,
    durableHandoff: true, executionPending: true,
  });
  const ingressIdentity = { channel: 'web', conversationId, messageId: originMessageId };
  if (ingressExecutionContract === 'ExecutionRecord/v2') {
    seedAuthorizedTurnUnderstanding({
      database, channel: 'web', conversationId, messageId: originMessageId,
      requester, originalText: text, sessionRef: `session:${label}`,
      syntheticTest: false, supersessionScope, supersessionToken, output: prepared,
    });
  } else {
    insertProductionExecution(database, {
      executionKey: `turn-understanding:${sha256(canonicalJson(ingressIdentity))}`,
      turnId: `ingress:web:${conversationId}:${originMessageId}`,
      tool: '@turn/understanding',
      args: {
        channel: 'web', conversationId, messageId: originMessageId, requester,
        originalText: text, sessionRef: `session:${label}`, syntheticTest: false,
        supersessionScope, supersessionToken,
      },
      output: prepared,
      executionContract: ingressExecutionContract,
    });
  }
  const store = createWorkCompletionStore(database);
  return {
    database, file, store, label, workItemId, episodeId, requester,
    conversationId, originMessageId, supersessionScope, supersessionToken,
    text, understanding, record, plans, planSeal, turnId, retry,
  };
}

function bindFixture(fixture) {
  const request = {
    workItemId: fixture.workItemId,
    episodeId: fixture.episodeId,
    planSeal: fixture.planSeal,
  };
  const capability = fixture.store.issueContractCapability(request);
  assert.ok(capability);
  return fixture.store.bindContract(capability, request).value;
}

function insertProofWrite(fixture, contract, {
  key = `proof-write:${fixture.label}:${sequence}`,
  status = 'done', output = null, basisReceiptIds = [],
  role = 'proof', executionType = 'write', inhoud = null,
} = {}) {
  const plan = contract.proofPlans[0];
  const args = { ...plan.args,
    ...(inhoud == null ? {} : { inhoud }) };
  const materialSha256 = sha256(args.inhoud);
  return insertProductionExecution(fixture.database, {
    executionKey: key, turnId: contract.executionTurnId,
    tool: 'schrijf_bestand', args, status,
    semanticBinding: childBinding(fixture.record, {
      capabilityId: 'workspace.file.write', operationId: 'write',
      role, executionType, basisReceiptIds,
    }),
    output: output ?? completeResult('schrijf_bestand', {
      ok: true, changed: true, path: args.pad,
      bytes: Buffer.byteLength(args.inhoud, 'utf8'), material_sha256: materialSha256,
    }),
  });
}

function insertProofReadback(fixture, contract, {
  key = `proof-readback:${fixture.label}:${sequence}`,
  basisReceiptIds = ['s1'], materialSha256 = null, effectUncertain = false,
} = {}) {
  const plan = contract.proofPlans[0];
  const digest = materialSha256 || sha256(plan.args.inhoud);
  const output = completeResult('lees_bestand', {}, { changed: false });
  output.artifact_receipt = {
    path: plan.args.pad, bytes: Buffer.byteLength(plan.args.inhoud, 'utf8'),
    source_bytes: Buffer.byteLength(plan.args.inhoud, 'utf8'),
    sha256: digest, complete_read: true,
  };
  if (effectUncertain) output.effect_uncertain = true;
  return insertProductionExecution(fixture.database, {
    executionKey: key, turnId: contract.executionTurnId,
    tool: 'lees_bestand', args: { pad: plan.args.pad },
    semanticBinding: childBinding(fixture.record, {
      capabilityId: 'workspace.file.read', operationId: 'read',
      executionType: 'readback', basisReceiptIds,
    }),
    output,
  });
}

function reconcileFixture(fixture, contract) {
  const request = { contractRef: contract.contractRef };
  const capability = fixture.store.issueReconciliationCapability(request);
  assert.ok(capability);
  return fixture.store.reconcile(capability, request);
}

function proveFixture(fixture, contract) {
  insertProofWrite(fixture, contract);
  insertProofReadback(fixture, contract);
  const result = reconcileFixture(fixture, contract);
  assert.equal(result.kind, 'created');
  return result.value;
}

function claimFixture(fixture, contract, {
  claimant = 'worker-a', expected = 0, leaseMs = 30_000,
} = {}) {
  const request = {
    contractRef: contract.contractRef,
    claimantSha256: sha256(claimant),
    expectedClaimGeneration: expected,
    requestedLeaseMs: leaseMs,
  };
  const capability = fixture.store.issueSettlementClaimCapability(request);
  assert.ok(capability);
  return fixture.store.claimSettlement(capability, request).value;
}

function settlementIntent(fixture, claim, claimant = 'worker-a') {
  const request = { claimRef: claim.claimRef, claimantSha256: sha256(claimant) };
  const capability = fixture.store.issueSettlementExecutionCapability(request);
  assert.ok(capability);
  return fixture.store.consumeSettlementExecutionCapability(capability, request);
}

function settleHost(fixture, contract, intent) {
  const { database } = fixture;
  const required = contract.deliveryRequired;
  const currentWork = database.prepare('SELECT * FROM main.eva_work_items WHERE id=?')
    .get(fixture.workItemId);
  const currentEpisode = database.prepare(
    'SELECT * FROM main.eva_goal_episodes WHERE id=?',
  ).get(fixture.episodeId);
  const proof = 'Gecontroleerd materiaal en exacte readback.';
  const nextStep = required
    ? 'Bezorg het gecontroleerde resultaat aan de oorspronkelijke verzoeker.' : '';
  database.prepare(`UPDATE main.eva_work_items SET
    updated_ts=?,status=?,next_step=?,evidence=?,last_action='schrijf_bestand',
    result=?,current_step=?,wait_json=?,next_wake_ts=NULL,revision=revision+1
    WHERE id=?`).run(
    iso(), required ? 'open' : 'done', nextStep, proof,
    contract.settlementPlan.args.resultaat, nextStep,
    required ? '{}' : null, fixture.workItemId,
  );
  database.prepare(`UPDATE main.eva_goal_episodes SET state=?,next_action=?,
    checkpoint=?,proof_summary=?,updated_ts=?,closed_ts=?,revision=revision+1
    WHERE id=?`).run(
    required ? 'delivery_pending' : 'closed', nextStep, proof, proof, iso(),
    required ? null : iso(), fixture.episodeId,
  );
  const event = database.prepare(`INSERT INTO main.eva_episode_events
    (episode_id,ts,kind,session_ref,transition_key,payload_json)
    VALUES (?,?,?,?,?,?)`).run(
    fixture.episodeId, iso(), required ? 'final_delivery_armed' : 'state:closed',
    contract.executionTurnId, null, JSON.stringify({
      from: currentEpisode.state, to: required ? 'delivery_pending' : 'closed',
      workItemId: fixture.workItemId,
    }),
  );
  assert.ok(Number(event.lastInsertRowid));
  const item = database.prepare('SELECT * FROM main.eva_work_items WHERE id=?')
    .get(fixture.workItemId);
  assert.equal(item.revision, currentWork.revision + 1);
  insertProductionExecution(database, {
    executionKey: intent.executionKey, turnId: intent.turnId, tool: intent.tool,
    args: intent.args, semanticBinding: intent.semanticBinding,
    output: completeResult('werk_bijhouden', {
      ok: true, changed: true, proofAccepted: true,
      selfReviewInstead: false, item,
    }),
  });
}

function recordSettlement(fixture, contract, claim) {
  const request = { contractRef: contract.contractRef, claimRef: claim.claimRef };
  const capability = fixture.store.issueSettlementResultCapability(request);
  assert.ok(capability);
  return fixture.store.recordSettlementResult(capability, request).value;
}

test('planseal v3 bewaart echte governed bindingen en scheidt parent-mode van executionType', () => {
  const source = issuedUnderstanding('Maak exact materiaal en rond af.', 7);
  const record = createTurnUnderstandingRecord(source);
  const plans = productionPlans(record, 7);
  const seal = createWorkCompletionPlanSeal({
    decision: { kind: 'act', requirementIds: ['r1', 'r2'], replyDraft: null },
    plans,
  });
  assert.equal(seal.plans[0].semanticBinding.executionType, null);
  assert.deepEqual(seal.plans[0].semanticBinding.basisReceiptIds, []);
  assert.deepEqual(seal.plans[0].proofOperations, seal.plans[0].execution.proofOperations);
  assert.throws(() => createWorkCompletionPlanSeal({
    decision: seal.decision,
    plans: seal.plans.map((plan, index) => index ? plan : {
      ...plan, proofOperations: [],
    }),
  }), /invalid governed production plan/u);
  assert.throws(() => createWorkCompletionPlanSeal({
    decision: seal.decision,
    plans: seal.plans.map((plan, index) => index ? plan : {
      ...plan,
      semanticBinding: { ...plan.semanticBinding,
        basisReceiptIds: ['receipt:host-number'] },
    }),
  }), /invalid governed production plan/u);
});

test('store eist exact main production schema, immutable handofftriggers en geen TEMP-shadow', () => {
  const { database } = openProductionHost('schema');
  const inspected = inspectWorkCompletionHostSchema(database);
  assert.equal(inspected.contract, 'WorkCompletionHostSchema/v3');
  const store = createWorkCompletionStore(database);
  assert.deepEqual(store.status(), {
    ready: true, reason: null, contract: 'WorkCompletionStoreStatus/v3',
    productionReachable: false, finalDeliveryIntegrated: false,
  });
  database.exec('CREATE TEMP TABLE messages(value TEXT)');
  assert.equal(store.status().ready, false);
  assert.match(store.status().reason, /TEMP schema shadows protected messages/u);
  database.exec('DROP TABLE temp.messages');
  database.exec('DROP TRIGGER trg_interpretation_retry_handoff_immutable_update');
  assert.equal(store.status().ready, false);
  assert.match(store.status().reason, /handoff trigger manifest/u);
});

test('bind herleidt het echte production record, teksthash, handoffformules en ingress-head', () => {
  const fixture = createFixture('production-bind');
  assert.equal(Object.hasOwn(fixture.record.actions[0], 'semanticInput'), false);
  assert.equal(Object.hasOwn(fixture.record.firstDecision, 'replyDraft'), false);
  assert.equal(fixture.record.firstDecision.replyDraftSha256, null);
  assert.notEqual(fixture.record.sourceSha256, fixture.record.sourceTextSha256);
  assert.equal(fixture.retry.originalTextSha256, fixture.record.sourceTextSha256);
  const contract = bindFixture(fixture);
  assert.equal(contract.source.understanding.sha256, fixture.record.sha256);
  assert.equal(contract.understandingSha256, fixture.record.sourceSha256);
  assert.equal(contract.planSeal.plans[0].semanticBinding.executionType, null);
  assert.equal(contract.generation, 1);
});

test('workcompletion weigert historische v1 en consumeert alleen sidecar-gebonden v2 ingress', () => {
  const legacy = createFixture('ledger-v1', {
    ingressExecutionContract: 'ExecutionRecord/v1',
  });
  assert.equal(legacy.store.issueContractCapability({
    workItemId: legacy.workItemId, episodeId: legacy.episodeId,
    planSeal: legacy.planSeal,
  }), null);

  const fixture = createFixture('ledger-v2', {
    ingressExecutionContract: 'ExecutionRecord/v2',
  });
  const ingress = fixture.database.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE tool='@turn/understanding'`).get();
  const contract = bindFixture(fixture);
  assert.equal(contract.source.ingress.row.row.execution_key, ingress.execution_key);
  assert.equal(contract.source.ingress.row.row.completion_record_sha256,
    ingress.completion_record_sha256);
});

test('bind weigert fake semanticInput/replyDraft, structurele retryhash en niet-production executionType', () => {
  for (const kind of ['record', 'retry-hash', 'execution-type']) {
    const fixture = createFixture(`reject-${kind}`);
    if (kind === 'record') {
      const timeline = fixture.database.prepare(
        "SELECT * FROM main.timeline WHERE kind='turn_understanding'",
      ).get();
      const meta = JSON.parse(timeline.meta);
      meta.understanding.actions[0].semanticInput = fixture.plans[0].args;
      meta.understanding.firstDecision.replyDraft = null;
      fixture.database.prepare('UPDATE main.timeline SET meta=? WHERE id=?')
        .run(JSON.stringify(meta), timeline.id);
    } else if (kind === 'retry-hash') {
      const work = fixture.database.prepare(
        'SELECT state_json FROM main.eva_work_items WHERE id=?',
      ).get(fixture.workItemId);
      const state = JSON.parse(work.state_json);
      state.interpretation_retry.originalTextSha256 = fixture.record.sourceSha256;
      fixture.database.prepare('UPDATE main.eva_work_items SET state_json=? WHERE id=?')
        .run(JSON.stringify(state), fixture.workItemId);
    } else {
      fixture.planSeal = createWorkCompletionPlanSeal({
        decision: fixture.planSeal.decision,
        plans: fixture.planSeal.plans.map((plan) => ({
          ...plan,
          semanticBinding: { ...plan.semanticBinding,
            executionType: plan.execution.mode },
        })),
      });
    }
    const capability = fixture.store.issueContractCapability({
      workItemId: fixture.workItemId, episodeId: fixture.episodeId,
      planSeal: fixture.planSeal,
    });
    assert.equal(capability, null, kind);
  }
});

test('TTL, attempts, understood-plan en duurzame supersession-head worden telkens herbeoordeeld', () => {
  for (const kind of ['expired', 'attempts', 'plan', 'superseded']) {
    const fixture = createFixture(`active-${kind}`);
    if (kind === 'superseded') {
      seedAuthorizedTurnUnderstanding({
        database: fixture.database, channel: 'web',
        conversationId: fixture.conversationId, messageId: 'newer',
        requester: fixture.requester, originalText: 'nieuwere opdracht',
        sessionRef: 'newer', syntheticTest: false,
        supersessionScope: fixture.supersessionScope,
        supersessionToken: 'newer-token', output: createPreparedUnderstandingTurn({
          channel: 'web', conversationId: fixture.conversationId,
          originMessageId: 'newer', turnId: 'newer', reply: '',
          workItemId: fixture.workItemId, goalEpisodeId: fixture.episodeId,
          syntheticTest: false, durableHandoff: true,
        }),
      });
    } else {
      const work = fixture.database.prepare(
        'SELECT state_json FROM main.eva_work_items WHERE id=?',
      ).get(fixture.workItemId);
      const state = JSON.parse(work.state_json);
      if (kind === 'expired') {
        const created = new Date(Date.now() - 7 * 60 * 60_000);
        state.interpretation_retry.createdAt = created.toISOString();
        state.interpretation_retry.deadlineAt = new Date(
          created.getTime() + 6 * 60 * 60_000,
        ).toISOString();
      }
      if (kind === 'attempts') state.interpretation_retry.attempts = 4;
      if (kind === 'plan') state.interpretation_retry.planSha256 = sha256('ander plan');
      fixture.database.prepare('UPDATE main.eva_work_items SET state_json=? WHERE id=?')
        .run(JSON.stringify(state), fixture.workItemId);
    }
    assert.equal(fixture.store.issueContractCapability({
      workItemId: fixture.workItemId, episodeId: fixture.episodeId,
      planSeal: fixture.planSeal,
    }), null, kind);
  }
});

test('proof-ready vereist declared proofoperations, echte post-write readback en effect_uncertain=false', () => {
  const fixture = createFixture('proof-matrix');
  const contract = bindFixture(fixture);
  let pending = reconcileFixture(fixture, contract);
  assert.equal(pending.kind, 'pending');
  assert.match(pending.value.reason, /missing_exact_proof:r1/u);

  insertProofWrite(fixture, contract);
  pending = reconcileFixture(fixture, contract);
  assert.equal(pending.kind, 'pending');
  assert.match(pending.value.reason, /missing_exact_proof:r1/u);

  insertProofReadback(fixture, contract, {
    key: 'uncertain-readback', effectUncertain: true,
  });
  pending = reconcileFixture(fixture, contract);
  assert.equal(pending.kind, 'pending');
  assert.match(pending.value.reason, /missing_exact_proof:r1/u);
  assert.equal(fixture.store.proofReady(contract.contractRef), null);
});

test('recoveryoperation vereist een eerdere veilige failure-receipt en valide sN-basis', () => {
  const invalid = createFixture('recovery-invalid');
  const invalidContract = bindFixture(invalid);
  insertProofWrite(invalid, invalidContract, {
    key: 'invalid-repair', role: 'recovery', executionType: 'repair',
    basisReceiptIds: [],
  });
  assert.throws(
    () => reconcileFixture(invalid, invalidContract),
    /recovery execution lacks an exact failed basis receipt/u,
  );

  const fixture = createFixture('recovery-valid');
  const contract = bindFixture(fixture);
  insertProofWrite(fixture, contract, {
    key: 'failed-write', output: failedResult('schrijf_bestand'),
  });
  insertProofWrite(fixture, contract, {
    key: 'repair-write', role: 'recovery', executionType: 'repair',
    basisReceiptIds: ['s1'],
  });
  insertProofWrite(fixture, contract, {
    key: 'verified-write', basisReceiptIds: ['s2'],
  });
  insertProofReadback(fixture, contract, {
    key: 'verified-readback', basisReceiptIds: ['s3'],
  });
  const ready = reconcileFixture(fixture, contract);
  assert.equal(ready.kind, 'created');
  assert.equal(ready.value.proofs[0].materialReadback.writeReceiptId, 's3');
  assert.equal(ready.value.proofs[0].materialReadback.readbackReceiptId, 's4');
});

test('store-owned receiptvolgorde en proofhead overleven restart en VACUUM zonder callbacknummer', () => {
  const fixture = createFixture('stable-sequence');
  const contract = bindFixture(fixture);
  const ready = proveFixture(fixture, contract);
  const before = clone(ready);
  fixture.database.close();
  const reopened = new Database(fixture.file);
  reopened.pragma('foreign_keys = ON');
  reopened.exec('VACUUM');
  opened.push(reopened);
  const restarted = createWorkCompletionStore(reopened);
  assert.equal(restarted.status().ready, true);
  assert.deepEqual(restarted.proofReady(contract.contractRef), before);
  const bindings = reopened.prepare(`SELECT receipt_id FROM
    main.eva_work_completion_execution_bindings_v3
    WHERE contract_ref=? ORDER BY id`).all(contract.contractRef);
  assert.deepEqual(bindings.map(({ receipt_id: id }) => id), ['s1', 's2']);
});

test('settlementlease gebruikt uitsluitend SQLite-klok en generatie/token hekken late workers af', async () => {
  const fixture = createFixture('lease-fence');
  const contract = bindFixture(fixture);
  proveFixture(fixture, contract);
  const first = claimFixture(fixture, contract, { leaseMs: 1_000 });
  const firstIntent = settlementIntent(fixture, first, 'worker-a');
  assert.equal(fixture.store.settlementExecutionFenceCurrent(firstIntent), true);
  await wait(1_100);
  const second = claimFixture(fixture, contract, {
    claimant: 'worker-b', expected: 1, leaseMs: 30_000,
  });
  assert.equal(second.claimGeneration, 2);
  assert.notEqual(second.executionKey, first.executionKey);
  assert.equal(fixture.store.settlementExecutionFenceCurrent(firstIntent), false);
  const secondIntent = settlementIntent(fixture, second, 'worker-b');
  assert.equal(fixture.store.settlementExecutionFenceCurrent(secondIntent), true);
});

test('settlement accepteert alleen claimgebonden execution key plus exacte productie-readback', () => {
  const fixture = createFixture('settlement-exact', { deliveryPolicy: 'none' });
  const contract = bindFixture(fixture);
  proveFixture(fixture, contract);
  const claim = claimFixture(fixture, contract);
  const intent = settlementIntent(fixture, claim);
  settleHost(fixture, contract, intent);
  const settlement = recordSettlement(fixture, contract, claim);
  assert.equal(settlement.state, 'terminal');
  const request = { contractRef: contract.contractRef };
  const capability = fixture.store.issueFinalCapability(request);
  assert.ok(capability);
  const receipt = fixture.store.finalize(capability, request).value;
  assert.equal(receipt.deliveryResultRef, null);
  assert.equal(receipt.current, true);
});

test('required delivery blijft fail-disabled ondanks een caller-accepted receipt', () => {
  const fixture = createFixture('delivery-disabled');
  const contract = bindFixture(fixture);
  proveFixture(fixture, contract);
  const claim = claimFixture(fixture, contract);
  const intent = settlementIntent(fixture, claim);
  settleHost(fixture, contract, intent);
  const settlement = recordSettlement(fixture, contract, claim);
  assert.equal(settlement.state, 'delivery_proof_pending');
  fixture.database.prepare(`INSERT INTO main.eva_delivery_receipts
    (episode_id,origin_conversation_id,origin_message_id,delivered_conversation_id,
     delivered_channel,delivery_target,result_text,proof_summary,idempotency_key,
     delivered_ts,transport_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
    fixture.episodeId, fixture.conversationId, fixture.originMessageId,
    fixture.conversationId, 'web', fixture.requester,
    contract.settlementPlan.args.resultaat, 'Gecontroleerd.',
    `fake:${fixture.episodeId}`, iso(), JSON.stringify({ accepted: true }),
  );
  assert.equal(fixture.store.issueFinalCapability({
    contractRef: contract.contractRef,
  }), null);
  assert.equal(fixture.store.receipt(contract.contractRef), null);
  const pending = fixture.store.pendingReconciliations()
    .find((entry) => entry.contractRef === contract.contractRef);
  assert.equal(pending.state, 'delivery_proof_pending');
  assert.equal(pending.action.kind,
    'await_durable_http_client_ack_or_telegram_reply_proof');
  assert.equal(pending.action.mayExecute, false);
});

test('proof-ready valideert current main rows opnieuw en accepteert geen post-write herschrijving', () => {
  const fixture = createFixture('readback-tamper');
  const contract = bindFixture(fixture);
  proveFixture(fixture, contract);
  const row = fixture.database.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE tool='lees_bestand' AND turn_id=?`).get(contract.executionTurnId);
  const parsed = JSON.parse(row.output);
  parsed.artifact_receipt.sha256 = sha256('andere bytes');
  const storedOutput = JSON.stringify(parsed);
  const outputSha256 = sha256(storedOutput);
  const completionRecordSha256 = hashJson({
    executionKey: row.execution_key, turnId: row.turn_id, tool: row.tool,
    args: row.args, semanticHash: row.semantic_hash,
    semanticBinding: row.semantic_binding,
    intentRecordSha256: row.intent_record_sha256, status: row.status,
    startedTs: row.started_ts, finishedTs: row.finished_ts,
    outputSha256, materialSha256: parsed.artifact_receipt.sha256,
  });
  const terminalTrigger = fixture.database.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name='turn_tool_executions_terminal_immutable'`).get().sql;
  fixture.database.exec('DROP TRIGGER main.turn_tool_executions_terminal_immutable');
  try {
    fixture.database.prepare(`UPDATE main.turn_tool_executions SET output=?,
      output_sha256=?,material_sha256=?,completion_record_sha256=?
      WHERE execution_key=?`).run(
      storedOutput, outputSha256, parsed.artifact_receipt.sha256,
      completionRecordSha256, row.execution_key,
    );
  } finally { fixture.database.exec(terminalTrigger); }
  assert.equal(fixture.store.proofReady(contract.contractRef), null);
});

test('owned lifecycle is append-only en OR REPLACE/collision kan geen authority herschrijven', () => {
  const fixture = createFixture('append-only');
  const contract = bindFixture(fixture);
  proveFixture(fixture, contract);
  assert.throws(() => fixture.database.prepare(`UPDATE
    main.eva_work_completion_contracts_v3 SET plan_sha256=? WHERE contract_ref=?`)
    .run(sha256('ander'), contract.contractRef), /append-only/u);
  assert.throws(() => fixture.database.prepare(`DELETE FROM
    main.eva_work_completion_execution_bindings_v3 WHERE contract_ref=?`)
    .run(contract.contractRef), /append-only/u);
  assert.throws(() => fixture.database.prepare(`INSERT OR REPLACE INTO
    main.eva_work_completion_proof_ready_v3
    SELECT * FROM main.eva_work_completion_proof_ready_v3 WHERE contract_ref=?`)
    .run(contract.contractRef), /append-only/u);
  assert.equal(fixture.store.status().ready, true);
});
