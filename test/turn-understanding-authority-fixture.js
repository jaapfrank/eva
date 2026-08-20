import { createHash } from 'node:crypto';

import { turnUnderstandingAuthorityRecord } from '../src/turnauthority.js';
import { redactSecrets } from '../src/security/redaction.js';
import { canonicalJson } from '../src/util.js';

const CONTRACT = 'TurnUnderstandingExecutionAuthority/v1';
const TABLE = 'eva_turn_understanding_authorities_v1';
const TOOL = '@turn/understanding';

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function canonicalOutput(value) {
  if (value == null) return null;
  const stored = typeof value === 'string' ? value : canonicalJson(value);
  let parsed;
  try { parsed = JSON.parse(stored); } catch {
    throw new Error('test-only turn-authority output is geen JSON');
  }
  if (canonicalJson(parsed) !== stored) {
    throw new Error('test-only turn-authority output is niet canoniek');
  }
  return stored;
}

function completionSha256(row) {
  return sha256(canonicalJson({
    executionContract: 'ExecutionRecord/v2', executionSeq: Number(row.execution_seq),
    executionKey: row.execution_key, turnId: row.turn_id, tool: row.tool,
    args: row.args, semanticHash: row.semantic_hash, semanticBinding: null,
    intentRecordSha256: row.intent_record_sha256, status: 'done',
    startedTs: row.started_ts, finishedTs: row.finished_ts,
    outputSha256: row.output_sha256, materialSha256: null,
  }));
}

function exactReplay(database, expected, storedOutput) {
  const row = database.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(expected.executionKey);
  const semanticHash = sha256(canonicalJson({
    turnId: expected.turnId, tool: TOOL, args: expected.semanticArgs, binding: null,
  }));
  const intentRecordSha256 = sha256(canonicalJson({
    executionContract: 'ExecutionRecord/v2', executionSeq: Number(row?.execution_seq),
    turnId: expected.turnId, tool: TOOL, args: expected.storedArgsValue,
    binding: null, semanticHash,
  }));
  const durable = row && turnUnderstandingAuthorityRecord(database, row);
  const exactIntent = row
    && row.turn_id === expected.turnId && row.tool === TOOL
    && row.args === expected.storedArgs && row.semantic_hash === semanticHash
    && row.semantic_binding === null && row.intent_record_sha256 === intentRecordSha256
    && row.execution_contract === 'ExecutionRecord/v2'
    && row.execution_verification === 'verified';
  const exactSource = durable
    && durable.sourceKind === expected.sourceKind
    && durable.sourceRef === expected.sourceRef
    && durable.sourceSha256 === expected.sourceSha256
    && durable.sourceGeneration === expected.sourceGeneration;
  const exactTerminal = storedOutput == null
    ? row?.status === 'started' && row.finished_ts == null && row.output == null
      && row.output_sha256 == null && row.material_sha256 == null
      && row.completion_record_sha256 == null
    : row?.status === 'done' && row.output === storedOutput
      && row.output_sha256 === sha256(storedOutput) && row.material_sha256 == null
      && Number.isFinite(Date.parse(String(row.finished_ts || '')))
      && row.completion_record_sha256 === completionSha256(row);
  if (!exactIntent || !exactSource || !exactTerminal) {
    throw new Error('test-only turn-authority replay wijkt af van exact bestaand bewijs');
  }
  return Object.freeze({
    executionKey: expected.executionKey, row,
    authority: database.prepare(`SELECT * FROM main.${TABLE}
      WHERE execution_key=? AND source_generation=?`).get(
      expected.executionKey, expected.sourceGeneration,
    ),
  });
}

/**
 * TEST-ONLY direct-SQL fixture. Productiecode importeert dit bestand nooit.
 * Zij simuleert een al door de private agentroute uitgegeven sidecar; zij mint
 * geen runtime-capability en mag daarom alleen in geïsoleerde tests bestaan.
 */
export function seedAuthorizedTurnUnderstanding({
  database,
  channel,
  conversationId,
  messageId,
  requester,
  originalText,
  sessionRef = '',
  supersessionScope = null,
  supersessionToken = null,
  syntheticTest = false,
  output = null,
  sourceGeneration = 1,
  sourceKind = 'http_ingress',
  startedTs = null,
  finishedTs = null,
} = {}) {
  if (!database?.open || !['web', 'voice'].includes(channel)
    || sourceKind !== 'http_ingress' || !Number.isSafeInteger(sourceGeneration)
    || sourceGeneration < 1 || sourceGeneration > 64) {
    throw new Error('test-only turn-authority fixture mist database of kanaal');
  }
  const identity = {
    channel, conversationId: String(conversationId), messageId: String(messageId),
  };
  const args = {
    ...identity, requester: String(requester), originalText: String(originalText),
    sessionRef: String(sessionRef), supersessionScope, supersessionToken,
    syntheticTest: syntheticTest === true,
  };
  const storedArgsValue = redactSecrets(args);
  const executionKey = `turn-understanding:${sha256(canonicalJson(identity))}`;
  const turnId = `ingress:${channel}:${identity.conversationId}:${identity.messageId}`;
  const storedArgs = canonicalJson(storedArgsValue);
  const sourceBody = {
    contract: 'TestOnlyTurnUnderstandingSource/v1', executionKey,
    channel, conversationId: identity.conversationId,
    messageId: identity.messageId, sourceGeneration,
  };
  const sourceSha256 = sha256(canonicalJson(sourceBody));
  const sourceRef = `test-turn-source:${sourceSha256}`;
  const storedOutput = canonicalOutput(output);
  return database.transaction(() => {
    const expected = {
      executionKey, turnId, semanticArgs: args, storedArgsValue, storedArgs,
      sourceKind, sourceRef,
      sourceSha256, sourceGeneration,
    };
    if (database.prepare(`SELECT 1 FROM main.turn_tool_executions
      WHERE execution_key=?`).get(executionKey)) {
      return exactReplay(database, expected, storedOutput);
    }
    const executionSeq = Number(database.prepare(
      'SELECT coalesce(max(execution_seq),0)+1 AS value FROM main.turn_tool_executions',
    ).get().value);
    const semanticHash = sha256(canonicalJson({ turnId, tool: TOOL, args, binding: null }));
    const intentRecordSha256 = sha256(canonicalJson({
      executionContract: 'ExecutionRecord/v2', executionSeq,
      turnId, tool: TOOL, args: storedArgsValue, binding: null, semanticHash,
    }));
    const actualStartedTs = startedTs || new Date().toISOString();
    database.prepare(`INSERT INTO main.turn_tool_executions (
      execution_key,turn_id,tool,args,status,started_ts,semantic_hash,
      semantic_binding,intent_record_sha256,execution_seq,execution_contract,
      execution_verification
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      executionKey, turnId, TOOL, storedArgs, 'started', actualStartedTs,
      semanticHash, null, intentRecordSha256, executionSeq,
      'ExecutionRecord/v2', 'verified',
    );
    const issuedTs = actualStartedTs;
    const authority = {
      contract: CONTRACT, execution_key: executionKey, execution_seq: executionSeq,
      intent_record_sha256: intentRecordSha256, channel,
      conversation_id: identity.conversationId, message_id: identity.messageId,
      source_kind: sourceKind, source_ref: sourceRef,
      source_sha256: sourceSha256, source_generation: sourceGeneration,
      authority_sha256: '', issued_ts: issuedTs,
    };
    authority.authority_sha256 = sha256(canonicalJson({
      contract: authority.contract, executionKey, executionSeq,
      intentRecordSha256, channel, conversationId: identity.conversationId,
      messageId: identity.messageId, sourceKind, sourceRef, sourceSha256,
      sourceGeneration, issuedTs, turnId, tool: TOOL, args: storedArgs,
    }));
    database.prepare(`INSERT INTO main.${TABLE} (
      contract,execution_key,execution_seq,intent_record_sha256,channel,
      conversation_id,message_id,source_kind,source_ref,source_sha256,
      source_generation,authority_sha256,issued_ts
    ) VALUES (@contract,@execution_key,@execution_seq,@intent_record_sha256,@channel,
      @conversation_id,@message_id,@source_kind,@source_ref,@source_sha256,
      @source_generation,@authority_sha256,@issued_ts)`).run(authority);

    if (storedOutput != null) {
      const terminalTs = finishedTs || new Date(
        Math.max(Date.parse(actualStartedTs) + 1, Date.now()),
      ).toISOString();
      const outputSha256 = sha256(storedOutput);
      const terminal = {
        execution_contract: 'ExecutionRecord/v2', execution_seq: executionSeq,
        execution_key: executionKey, turn_id: turnId, tool: TOOL, args: storedArgs,
        semantic_hash: semanticHash, intent_record_sha256: intentRecordSha256,
        started_ts: actualStartedTs, finished_ts: terminalTs, output_sha256: outputSha256,
      };
      database.prepare(`UPDATE main.turn_tool_executions
        SET status='done',finished_ts=?,output=?,output_sha256=?,material_sha256=NULL,
          completion_record_sha256=? WHERE execution_key=? AND status='started'`).run(
        terminalTs, storedOutput, outputSha256, completionSha256(terminal), executionKey,
      );
    }
    return exactReplay(database, expected, storedOutput);
  }).immediate();
}
