import { createHash, generateKeyPairSync } from 'node:crypto';

import {
  TURN_UNDERSTANDING_AUTHORITY_CONTRACT_V2,
  TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME,
  TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_SQL,
  turnUnderstandingAuthorityBody,
  turnUnderstandingAuthorityRecord,
} from '../src/turnauthority.js';
import { redactSecrets } from '../src/security/redaction.js';
import { canonicalJson } from '../src/util.js';
import { normalizeHttpClientAckPublicKey } from '../src/web/httpack.js';

const TABLE = 'eva_turn_understanding_authorities_v1';
const TOOL = '@turn/understanding';

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

const HTTP_SOURCE_TABLE_SQL = `
CREATE TABLE eva_http_ingress_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contract TEXT NOT NULL,
  request_ref TEXT NOT NULL UNIQUE, channel TEXT NOT NULL,
  conversation_id TEXT NOT NULL, request_id TEXT NOT NULL,
  intent_sha256 TEXT NOT NULL, intent_bytes INTEGER NOT NULL,
  started_ts TEXT NOT NULL, UNIQUE(channel,conversation_id,request_id)
);
CREATE TRIGGER eva_http_ingress_requests_no_update
BEFORE UPDATE ON eva_http_ingress_requests
BEGIN SELECT RAISE(ABORT,'HTTP ingress requests are append-only'); END;
CREATE TRIGGER eva_http_ingress_requests_no_delete
BEFORE DELETE ON eva_http_ingress_requests
BEGIN SELECT RAISE(ABORT,'HTTP ingress requests are append-only'); END;
CREATE TABLE eva_http_client_ack_bindings_v3 (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contract TEXT NOT NULL,
  request_row_id INTEGER NOT NULL UNIQUE, request_ref TEXT NOT NULL UNIQUE,
  intent_sha256 TEXT NOT NULL, intent_json TEXT NOT NULL,
  public_key_json TEXT NOT NULL, key_fingerprint TEXT NOT NULL,
  ack_challenge TEXT NOT NULL UNIQUE, ack_challenge_sha256 TEXT NOT NULL UNIQUE,
  binding_json TEXT NOT NULL, created_ts TEXT NOT NULL
);
CREATE TRIGGER eva_http_client_ack_bindings_v3_no_update
BEFORE UPDATE ON eva_http_client_ack_bindings_v3
BEGIN SELECT RAISE(ABORT,'HTTP client ACK bindings are append-only'); END;
CREATE TRIGGER eva_http_client_ack_bindings_v3_no_delete
BEFORE DELETE ON eva_http_client_ack_bindings_v3
BEGIN SELECT RAISE(ABORT,'HTTP client ACK bindings are append-only'); END;
CREATE TABLE eva_http_ingress_claim_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contract TEXT NOT NULL,
  claim_event_ref TEXT NOT NULL UNIQUE, claim_event_sha256 TEXT NOT NULL UNIQUE,
  request_row_id INTEGER NOT NULL, request_ref TEXT NOT NULL,
  phase TEXT NOT NULL, generation INTEGER NOT NULL, sequence INTEGER NOT NULL,
  action TEXT NOT NULL, token_sha256 TEXT NOT NULL, lease_until_ts TEXT NOT NULL,
  event_json TEXT NOT NULL, created_ts TEXT NOT NULL,
  UNIQUE(request_row_id,phase,generation,sequence)
);
CREATE TRIGGER eva_http_ingress_claim_events_no_update
BEFORE UPDATE ON eva_http_ingress_claim_events
BEGIN SELECT RAISE(ABORT,'HTTP ingress claim events are append-only'); END;
CREATE TRIGGER eva_http_ingress_claim_events_no_delete
BEFORE DELETE ON eva_http_ingress_claim_events
BEGIN SELECT RAISE(ABORT,'HTTP ingress claim events are append-only'); END;
`;

let testHttpClientKey = null;

function fixtureHttpClientKey() {
  if (testHttpClientKey) return testHttpClientKey;
  const pair = generateKeyPairSync('ec', { namedCurve: 'P-256' });
  const jwk = pair.publicKey.export({ format: 'jwk' });
  testHttpClientKey = normalizeHttpClientAckPublicKey({
    contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256',
    kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y,
  });
  return testHttpClientKey;
}

function ensureFixtureHttpTables(database) {
  const names = [
    'eva_http_ingress_requests', 'eva_http_client_ack_bindings_v3',
    'eva_http_ingress_claim_events',
  ];
  const present = names.filter((name) => database.prepare(
    'SELECT 1 FROM main.sqlite_master WHERE type=\'table\' AND name=?',
  ).get(name));
  if (!present.length) database.exec(HTTP_SOURCE_TABLE_SQL);
  else if (present.length !== names.length) {
    throw new Error('test-only HTTP-sourcefixture ziet een incompleet ingressschema');
  }
  const boundary = database.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='trigger' AND name=?`).get(
    TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_NAME,
  );
  if (!boundary) database.exec(TURN_UNDERSTANDING_HTTP_AUTHORITY_TRIGGER_SQL);
}

function fixtureHttpClaim(database, request, generation, startedTs) {
  let latestClaimEventRef = null;
  for (let current = 1; current <= generation; current += 1) {
    const tokenSha256 = sha256(`test-only-http-claim:${request.request_ref}:${current}`);
    const createdTs = new Date(Date.parse(startedTs) + ((current - 1) * 3)).toISOString();
    const leaseUntil = new Date(Math.max(
      Date.parse(createdTs) + 30_000, Date.now() + 30_000,
    )).toISOString();
    const acquired = {
      action: 'acquired', contract: 'HttpIngressClaimEvent/v1', generation: current,
      leaseUntil, phase: 'execution', requestRef: request.request_ref,
      sequence: 0, tokenSha256,
    };
    const acquiredJson = canonicalJson(acquired);
    const acquiredSha256 = sha256(acquiredJson);
    database.prepare(`INSERT INTO main.eva_http_ingress_claim_events (
      contract,claim_event_ref,claim_event_sha256,request_row_id,request_ref,
      phase,generation,sequence,action,token_sha256,lease_until_ts,event_json,created_ts
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      acquired.contract, `http-claim-event:${acquiredSha256}`, acquiredSha256,
      request.id, request.request_ref, acquired.phase, current, 0,
      acquired.action, tokenSha256, leaseUntil, acquiredJson, createdTs,
    );
    latestClaimEventRef = `http-claim-event:${acquiredSha256}`;
    if (current < generation) {
      const released = { ...acquired, action: 'released', sequence: 1 };
      const releasedJson = canonicalJson(released);
      const releasedSha256 = sha256(releasedJson);
      database.prepare(`INSERT INTO main.eva_http_ingress_claim_events (
        contract,claim_event_ref,claim_event_sha256,request_row_id,request_ref,
        phase,generation,sequence,action,token_sha256,lease_until_ts,event_json,created_ts
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
        released.contract, `http-claim-event:${releasedSha256}`, releasedSha256,
        request.id, request.request_ref, released.phase, current, 1,
        released.action, tokenSha256, leaseUntil, releasedJson,
        new Date(Date.parse(createdTs) + 1).toISOString(),
      );
    }
  }
  return latestClaimEventRef;
}

function ensureFixtureHttpSource(database, {
  channel, conversationId, messageId, requester, originalText, syntheticTest,
  sourceGeneration, startedTs,
}) {
  ensureFixtureHttpTables(database);
  const existing = database.prepare(`SELECT * FROM main.eva_http_ingress_requests
    WHERE channel=? AND conversation_id=? AND request_id=?`).get(
    channel, conversationId, messageId,
  );
  if (existing) {
    const claim = database.prepare(`SELECT claim_event_ref FROM
      main.eva_http_ingress_claim_events WHERE request_row_id=? AND phase='execution'
        AND generation=? AND action IN ('acquired','renewed')
      ORDER BY sequence DESC LIMIT 1`).get(existing.id, sourceGeneration);
    return {
      sourceRef: existing.request_ref, sourceSha256: existing.intent_sha256,
      sourceGeneration, sourceClaimEventRef: claim?.claim_event_ref,
    };
  }
  const key = fixtureHttpClientKey();
  const intent = {
    contract: 'PublicHttpIntent/v1', route: channel === 'voice' ? '/api/voice' : '/api/chat',
    sender: requester, rawText: originalText, conversationId,
    ...(channel === 'voice' ? { shouldSpeak: false, speaker: null } : {}),
    syntheticTest, clientAckPublicKey: key.publicKey,
  };
  const intentJson = canonicalJson(intent);
  const intentSha256 = sha256(intentJson);
  const sourceRef = `http-ingress:${sha256(canonicalJson({
    channel, conversationId, requestId: messageId,
  }))}`;
  database.prepare(`INSERT INTO main.eva_http_ingress_requests (
    contract,request_ref,channel,conversation_id,request_id,intent_sha256,
    intent_bytes,started_ts
  ) VALUES (?,?,?,?,?,?,?,?)`).run(
    'HttpIngressRequest/v1', sourceRef, channel, conversationId, messageId,
    intentSha256, Buffer.byteLength(intentJson, 'utf8'), startedTs,
  );
  const request = database.prepare(`SELECT * FROM main.eva_http_ingress_requests
    WHERE request_ref=?`).get(sourceRef);
  const challenge = sha256(`test-only-http-ack:${sourceRef}`);
  const challengeSha256 = sha256(challenge);
  const binding = {
    algorithm: 'ES256', challenge, challengeSha256,
    contract: 'HttpClientAckBinding/v3', intentSha256,
    keyFingerprint: key.keyFingerprint, publicKey: key.publicKey, requestRef: sourceRef,
  };
  database.prepare(`INSERT INTO main.eva_http_client_ack_bindings_v3 (
    contract,request_row_id,request_ref,intent_sha256,intent_json,public_key_json,
    key_fingerprint,ack_challenge,ack_challenge_sha256,binding_json,created_ts
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
    binding.contract, request.id, sourceRef, intentSha256, intentJson,
    key.publicKeyJson, key.keyFingerprint, challenge, challengeSha256,
    canonicalJson(binding), startedTs,
  );
  const sourceClaimEventRef = fixtureHttpClaim(
    database, request, sourceGeneration, startedTs,
  );
  return {
    sourceRef, sourceSha256: intentSha256, sourceGeneration, sourceClaimEventRef,
  };
}

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
    && durable.sourceGeneration === expected.sourceGeneration
    && durable.sourceClaimEventRef === expected.sourceClaimEventRef;
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
  sourceKind = null,
  startedTs = null,
  finishedTs = null,
} = {}) {
  const exactSourceKind = sourceKind || (channel === 'voice'
    ? 'http_voice_ingress' : 'http_ingress');
  if (!database?.open || !['web', 'voice'].includes(channel)
    || exactSourceKind !== (channel === 'voice' ? 'http_voice_ingress' : 'http_ingress')
    || !Number.isSafeInteger(sourceGeneration)
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
  const storedOutput = canonicalOutput(output);
  const actualStartedTs = startedTs || new Date().toISOString();
  return database.transaction(() => {
    const { sourceRef, sourceSha256, sourceClaimEventRef } = ensureFixtureHttpSource(database, {
      channel, conversationId: identity.conversationId,
      messageId: identity.messageId, requester: args.requester,
      originalText: args.originalText, syntheticTest: args.syntheticTest,
      sourceGeneration, startedTs: actualStartedTs,
    });
    const expected = {
      executionKey, turnId, semanticArgs: args, storedArgsValue, storedArgs,
      sourceKind: exactSourceKind, sourceRef,
      sourceSha256, sourceGeneration, sourceClaimEventRef,
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
    database.prepare(`INSERT INTO main.turn_tool_executions (
      execution_key,turn_id,tool,args,status,started_ts,semantic_hash,
      semantic_binding,intent_record_sha256,execution_seq,execution_contract,
      execution_verification
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      executionKey, turnId, TOOL, storedArgs, 'started', actualStartedTs,
      semanticHash, null, intentRecordSha256, executionSeq,
      'ExecutionRecord/v2', 'verified',
    );
    const issuedClaim = database.prepare(`SELECT * FROM
      main.eva_http_ingress_claim_events WHERE claim_event_ref=?`).get(
      sourceClaimEventRef,
    );
    if (!issuedClaim || issuedClaim.claim_event_ref !== sourceClaimEventRef
      || issuedClaim.request_ref !== sourceRef || issuedClaim.phase !== 'execution'
      || !['acquired', 'renewed'].includes(issuedClaim.action)
      || Number(issuedClaim.generation) !== sourceGeneration) {
      throw new Error('test-only turn-authority fixture mist exact gebruikt claimevent');
    }
    const issuedTs = issuedClaim.created_ts;
    const authority = {
      contract: TURN_UNDERSTANDING_AUTHORITY_CONTRACT_V2,
      execution_key: executionKey, execution_seq: executionSeq,
      intent_record_sha256: intentRecordSha256, channel,
      conversation_id: identity.conversationId, message_id: identity.messageId,
      source_kind: exactSourceKind, source_ref: sourceRef,
      source_sha256: sourceSha256, source_body_json: null,
      source_generation: sourceGeneration,
      source_claim_event_ref: sourceClaimEventRef,
      authority_sha256: '', issued_ts: issuedTs,
    };
    const execution = database.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(executionKey);
    authority.authority_sha256 = sha256(canonicalJson(
      turnUnderstandingAuthorityBody(authority, execution),
    ));
    database.prepare(`INSERT INTO main.${TABLE} (
      contract,execution_key,execution_seq,intent_record_sha256,channel,
      conversation_id,message_id,source_kind,source_ref,source_sha256,
      source_body_json,source_generation,source_claim_event_ref,authority_sha256,issued_ts
    ) VALUES (@contract,@execution_key,@execution_seq,@intent_record_sha256,@channel,
      @conversation_id,@message_id,@source_kind,@source_ref,@source_sha256,
      @source_body_json,@source_generation,@source_claim_event_ref,
      @authority_sha256,@issued_ts)`).run(authority);

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
