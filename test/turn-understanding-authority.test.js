import assert from 'node:assert/strict';
import { createHash, generateKeyPairSync } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const previousDataDir = process.env.EVA_DATA_DIR;
const testDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-turn-authority-'));
process.env.EVA_DATA_DIR = testDataDir;
process.env.OPENAI_API_KEY = 'test-only';
process.env.EVA_RUNTIME_DISABLE_INFERENCE = 'true';
process.env.EVA_RUNTIME_DISABLE_WORLD_LEARNING = 'true';

const { seedAuthorizedTurnUnderstanding } = await import(
  './turn-understanding-authority-fixture.js'
);

const { canonicalJson } = await import('../src/util.js');
const {
  claimTurnToolExecution,
  completeTurnToolExecution,
  db,
  turnUnderstandingAuthorityStoreStatus,
  transitionTurnToolExecution,
  validateExecutionRecord,
} = await import('../src/db.js');
const { createPreparedUnderstandingTurn } = await import('../src/mind/understanding.js');
const {
  projectTurnUnderstandingTerminalOutcome,
} = await import('../src/mind/turnunderstandingoutcome.js');
const {
  turnUnderstandingAuthorityStoreStatus: inspectTurnUnderstandingAuthorityStore,
} = await import('../src/turnauthority.js');
const {
  classifyTurnEffectManifest,
  turnEffectManifestSpec,
} = await import('../src/executionledger.js');
const {
  beginHttpIngress,
  issueVerifiedHttpResultAuthority,
} = await import('../src/web/httpingress.js');
const {
  handleMessageDetailed,
  preparedUnderstandingIngressReplay,
} = await import('../src/mind/agent.js');

after(() => {
  try {
    db.close();
  } finally {
    if (previousDataDir === undefined) delete process.env.EVA_DATA_DIR;
    else process.env.EVA_DATA_DIR = previousDataDir;
    fs.rmSync(testDataDir, { recursive: true, force: true });
  }
});

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function restoreOwnProperty(target, name, descriptor) {
  if (descriptor) Object.defineProperty(target, name, descriptor);
  else delete target[name];
}

function insertStartedExecution({ executionKey, turnId, tool, args }) {
  const executionSeq = Number(db.prepare(
    'SELECT coalesce(max(execution_seq),0)+1 AS value FROM main.turn_tool_executions',
  ).get().value);
  const semanticHash = sha256(canonicalJson({ turnId, tool, args, binding: null }));
  const intentRecordSha256 = sha256(canonicalJson({
    executionContract: 'ExecutionRecord/v2', executionSeq,
    turnId, tool, args, binding: null, semanticHash,
  }));
  db.prepare(`INSERT INTO main.turn_tool_executions (
    execution_key,turn_id,tool,args,status,started_ts,semantic_hash,
    semantic_binding,intent_record_sha256,execution_seq,execution_contract,
    execution_verification
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    executionKey, turnId, tool, canonicalJson(args), 'started',
    new Date().toISOString(), semanticHash, null, intentRecordSha256,
    executionSeq, 'ExecutionRecord/v2', 'verified',
  );
  return db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(executionKey);
}

function readOnlyDatabaseProxy({ tamperGet = null, tamperAll = null } = {}) {
  return {
    open: db.open,
    prepare(sql) {
      const statement = db.prepare(sql);
      return {
        get(...args) {
          const value = statement.get(...args);
          return tamperGet ? tamperGet(String(sql), args, value) : value;
        },
        all(...args) {
          const value = statement.all(...args);
          return tamperAll ? tamperAll(String(sql), args, value) : value;
        },
      };
    },
  };
}

function inspectTemporarySchemaMutation(sql, expectedReason) {
  db.exec('BEGIN IMMEDIATE');
  try {
    db.exec(sql);
    const status = turnUnderstandingAuthorityStoreStatus();
    assert.equal(status.available, false);
    assert.match(status.reason, expectedReason);
  } finally {
    db.exec('ROLLBACK');
  }
  assert.equal(turnUnderstandingAuthorityStoreStatus().available, true);
}

function clientAckPublicKey() {
  const pair = generateKeyPairSync('ec', { namedCurve: 'P-256' });
  const jwk = pair.publicKey.export({ format: 'jwk' });
  return {
    contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256',
    kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y,
  };
}

test('de publieke execution-ledger kan geen @turn authority of sleutelprefix minten', () => {
  const reserved = (callback) => assert.throws(callback, (error) => (
    error?.code === 'EVA_RESERVED_EXECUTION_AUTHORITY'
      && /gereserveerd|reserved|authority/iu.test(error.message)
  ));
  reserved(() => claimTurnToolExecution({
    executionKey: `turn-understanding:${'1'.repeat(64)}`,
    turnId: 'ingress:web:web:forged:forged-request-1',
    tool: '@turn/understanding',
    args: { channel: 'web', conversationId: 'web:forged', messageId: 'forged-request-1' },
  }));

  reserved(() => claimTurnToolExecution({
    executionKey: `turn-understanding:${'2'.repeat(64)}`,
    turnId: 'ordinary-turn', tool: 'lees_bestand', args: { pad: 'bewijs.md' },
  }));
  reserved(() => claimTurnToolExecution({
    executionKey: 'ordinary-looking-exact-turn-claim',
    turnId: 'ordinary-turn', tool: '@turn', args: { forged: true },
  }));

  for (const [executionKey, tool] of [
    ['ordinary-looking-exact-turn-row', '@turn'],
    ['ordinary-looking-turn-prefix-row', '@turn/forged'],
  ]) {
    const row = insertStartedExecution({
      executionKey, turnId: `forged:${executionKey}`, tool, args: { forged: true },
    });
    assert.throws(() => validateExecutionRecord(row), /store-owned authority/iu);
    reserved(() => completeTurnToolExecution(
      executionKey, canonicalJson({ delivered: true }),
    ));
    reserved(() => transitionTurnToolExecution(executionKey, {
      from: 'started', status: 'done', output: canonicalJson({ delivered: true }),
    }));
  }
});

test('de test-only sidecarfixture herhaalt alleen exact bewijs en weigert divergentie', () => {
  const identity = {
    database: db,
    channel: 'web',
    conversationId: 'web:test-fixture-exact-replay',
    messageId: 'http-request:test-fixture-exact-replay:1',
    requester: 'Jaap',
    originalText: 'Bewijs dat deze testfixture exact idempotent is.',
    sessionRef: 'web:test-fixture-exact-replay',
    syntheticTest: true,
  };
  const output = createPreparedUnderstandingTurn({
    channel: identity.channel,
    conversationId: identity.conversationId,
    originMessageId: identity.messageId,
    turnId: 'turn:test-fixture-exact-replay',
    reply: 'Exact hetzelfde bewijs wordt hergebruikt.',
    syntheticTest: true,
  });
  const first = seedAuthorizedTurnUnderstanding({ ...identity, output });
  const replay = seedAuthorizedTurnUnderstanding({ ...identity, output });
  assert.equal(replay.row.execution_seq, first.row.execution_seq);
  assert.equal(replay.row.completion_record_sha256, first.row.completion_record_sha256);
  assert.equal(replay.authority.authority_sha256, first.authority.authority_sha256);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM main.turn_tool_executions
    WHERE execution_key=?`).get(first.executionKey).count, 1);
  assert.equal(db.prepare(`SELECT count(*) AS count
    FROM main.eva_turn_understanding_authorities_v1
    WHERE execution_key=?`).get(first.executionKey).count, 1);
  assert.throws(() => seedAuthorizedTurnUnderstanding({
    ...identity,
    output: { ...output, reply: 'Dit is afwijkend bewijs.' },
  }), /replay wijkt af van exact bestaand bewijs/u);
});

test('de sidecarstatus faalt gesloten op TEMP-shadow en append-only drift', () => {
  assert.deepEqual(turnUnderstandingAuthorityStoreStatus(), {
    contract: 'TurnUnderstandingAuthorityStoreStatus/v1', available: true,
  });
  db.exec(`CREATE TEMP TABLE eva_turn_understanding_authorities_v1 (
    execution_key TEXT PRIMARY KEY
  )`);
  const shadowed = turnUnderstandingAuthorityStoreStatus();
  assert.equal(shadowed.available, false);
  assert.match(shadowed.reason, /TEMP shadow/u);
  db.exec('DROP TABLE temp.eva_turn_understanding_authorities_v1');
  assert.equal(turnUnderstandingAuthorityStoreStatus().available, true);
  for (const sql of [
    `CREATE TEMP TABLE "EvA_TuRn_UnDeRsTaNdInG_AuThOrItIeS_v1" (
      execution_key TEXT PRIMARY KEY
    )`,
    `CREATE TEMP VIEW "EVA_TURN_UNDERSTANDING_AUTHORITY_EXTRA"
      AS SELECT execution_key FROM main.eva_turn_understanding_authorities_v1`,
  ]) inspectTemporarySchemaMutation(sql, /TEMP shadow/u);
  inspectTemporarySchemaMutation(
    `CREATE VIEW eva_turn_understanding_authority_extra AS
      SELECT execution_key FROM eva_turn_understanding_authorities_v1`,
    /namespace/u,
  );
  inspectTemporarySchemaMutation(
    `CREATE INDEX eva_turn_understanding_authority_wrong_owner
      ON messages(id)`,
    /namespace/u,
  );

  const literalTamper = readOnlyDatabaseProxy({
    tamperGet(sql, args, value) {
      if (args[0] !== 'eva_turn_understanding_authorities_v1'
        || !/SELECT sql FROM main\.sqlite_master/u.test(sql) || !value?.sql) return value;
      return {
        ...value,
        sql: value.sql.replace(
          "'TurnUnderstandingExecutionAuthority/v1'",
          "'turnUnderstandingExecutionAuthority/v1'",
        ),
      };
    },
  });
  assert.match(inspectTurnUnderstandingAuthorityStore(literalTamper).reason, /table DDL/u);

  const indexTamper = readOnlyDatabaseProxy({
    tamperAll(sql, _args, value) {
      if (!/index_xinfo\('sqlite_autoindex_eva_turn_understanding_authorities_v1_1'\)/u
        .test(sql)) return value;
      return value.map((entry, index) => index === 0
        ? { ...entry, coll: 'NOCASE' } : entry);
    },
  });
  assert.match(inspectTurnUnderstandingAuthorityStore(indexTamper).reason, /indexes/u);

  for (const [channel, sourceKind, suffix] of [
    ['telegram', 'telegram_inbound', 'telegram'],
    ['voice', 'ha_voice_ingress', 'ha-voice'],
  ]) {
    const conversationId = `${channel}:unsupported-sidecar`;
    const messageId = `unsupported-sidecar-${suffix}`;
    const identity = { channel, conversationId, messageId };
    const row = insertStartedExecution({
      executionKey: `turn-understanding:${sha256(canonicalJson(identity))}`,
      turnId: `ingress:${channel}:${conversationId}:${messageId}`,
      tool: '@turn/understanding',
      args: {
        ...identity, requester: 'Jaap', originalText: 'Niet ondersteunde bron.',
        sessionRef: '', supersessionScope: null, supersessionToken: null,
        syntheticTest: false,
      },
    });
    assert.throws(() => db.prepare(`INSERT INTO
      main.eva_turn_understanding_authorities_v1 (
        contract,execution_key,execution_seq,intent_record_sha256,channel,
        conversation_id,message_id,source_kind,source_ref,source_sha256,
        source_generation,authority_sha256,issued_ts
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      'TurnUnderstandingExecutionAuthority/v1', row.execution_key,
      row.execution_seq, row.intent_record_sha256, channel, conversationId,
      messageId, sourceKind, `unsupported-source:${suffix}`,
      '7'.repeat(64), 1, '8'.repeat(64), new Date().toISOString(),
    ), /CHECK constraint failed|requires exact started V2 execution/iu);
  }

  const zeroGenerationIdentity = {
    channel: 'web', conversationId: 'web:zero-generation',
    messageId: 'zero-generation-request-1',
  };
  const zeroGenerationRow = insertStartedExecution({
    executionKey: `turn-understanding:${sha256(canonicalJson(zeroGenerationIdentity))}`,
    turnId: `ingress:web:${zeroGenerationIdentity.conversationId}:${zeroGenerationIdentity.messageId}`,
    tool: '@turn/understanding',
    args: {
      ...zeroGenerationIdentity, requester: 'Jaap', originalText: 'Oude generatie.',
      sessionRef: '', supersessionScope: null, supersessionToken: null,
      syntheticTest: false,
    },
  });
  for (const [generation, suffix] of [[0, 'zero'], [65, 'too-high']]) {
    assert.throws(() => db.prepare(`INSERT INTO
      main.eva_turn_understanding_authorities_v1 (
        contract,execution_key,execution_seq,intent_record_sha256,channel,
        conversation_id,message_id,source_kind,source_ref,source_sha256,
        source_generation,authority_sha256,issued_ts
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      'TurnUnderstandingExecutionAuthority/v1', zeroGenerationRow.execution_key,
      zeroGenerationRow.execution_seq, zeroGenerationRow.intent_record_sha256,
      'web', zeroGenerationIdentity.conversationId, zeroGenerationIdentity.messageId,
      'http_ingress', `http-ingress:${suffix}-generation`, '9'.repeat(64), generation,
      `${generation === 0 ? 'a' : 'b'}`.repeat(64), new Date().toISOString(),
    ), /CHECK constraint failed/iu);
  }

  assert.throws(() => db.prepare(`INSERT INTO
    main.eva_turn_understanding_authorities_v1 (
      contract,execution_key,execution_seq,intent_record_sha256,channel,
      conversation_id,message_id,source_kind,source_ref,source_sha256,
      source_generation,authority_sha256,issued_ts
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    'TurnUnderstandingExecutionAuthority/v1',
    `turn-understanding:${'3'.repeat(64)}`, 1, '4'.repeat(64),
    'web', 'web:no-ledger', 'request-no-ledger', 'http_ingress',
    'http-ingress:no-ledger', '5'.repeat(64), 1, '6'.repeat(64),
    new Date().toISOString(),
  ), /requires exact started V2 execution/u);
});

test('terminal-outcomes zijn exact getypeerd en de pure projector begrenst uitvoer', () => {
  const args = {
    channel: 'web', conversationId: 'web:outcome-validation',
    messageId: 'outcome-validation-request-1', originalText: 'Test de grens.',
    requester: 'Jaap', sessionRef: 'session:outcome-validation',
    supersessionScope: null, supersessionToken: null, syntheticTest: false,
  };
  const handoff = projectTurnUnderstandingTerminalOutcome({
    args, kind: 'durable_handoff', value: {
      workItemId: 1, goalEpisodeId: 2, turnId: 'turn:handoff',
    },
  });
  assert.equal(handoff.contract, 'PreparedUnderstandingTurn/v1');
  assert.equal(handoff.metadata.durableHandoff, true);
  assert.equal(handoff.workItemId, 1);
  assert.equal(handoff.goalEpisodeId, 2);
  const recovery = projectTurnUnderstandingTerminalOutcome({
    args, kind: 'recovery_disposition', value: {
      disposition: 'effect_reconciliation_required', evidenceSha256: 'a'.repeat(64),
    },
  });
  assert.equal(recovery.error, 'effect_reconciliation_required');
  assert.equal(
    recovery.metadata.noPlanDecision,
    'recovery:effect_reconciliation_required',
  );
  for (const input of [
    { args, kind: 'delivered', value: { turnId: 'turn:delivered' } },
    { args, kind: 'transport_terminal', value: { status: 'delivery_uncertain' } },
    { args, kind: 'delivered', value: { turnId: 'turn:x', forged: true } },
    { args, kind: 'transport_terminal', value: { status: 'processing' } },
    { args, kind: 'durable_handoff', value: {
      workItemId: 0, goalEpisodeId: 1, turnId: 'turn:handoff',
    } },
    { args, kind: 'synthetic_interrupted', value: null },
    { args, kind: 'recovery_disposition', value: {
      disposition: 'effect_reconciliation_required', evidenceSha256: 'a'.repeat(64),
      forged: true,
    } },
    { args, kind: 'recovery_disposition', value: {
      disposition: 'verified_empty', evidenceSha256: 'a'.repeat(64),
    } },
    { args, kind: 'delivered', value: null, forged: true },
    Object.assign({ args, kind: 'delivered', value: null }, {
      [Symbol('forged')]: true,
    }),
    { args: { ...args, channel: 'email' }, kind: 'superseded', value: {
      turnId: 'turn:unsupported-channel',
    } },
  ]) assert.throws(
    () => projectTurnUnderstandingTerminalOutcome(input),
    /ongeldig|exact|terminaal|synthetische|ids/iu,
  );

  assert.throws(() => projectTurnUnderstandingTerminalOutcome({
    args, kind: 'prepared', value: {
      channel: args.channel, conversationId: args.conversationId,
      originMessageId: args.messageId, turnId: 'turn:oversized',
      reply: 'x'.repeat(40_001), syntheticTest: false,
    },
  }), /veilig begrensd/iu);
});

test('een exact leeg effectmanifest blijft geldig na een unrelated globale sequenceclaim', () => {
  const parent = {
    execution_key: `turn-understanding:${'1'.repeat(64)}`,
    execution_seq: 40,
    turn_id: 'ingress:web:web:manifest-race:request-1',
    tool: '@turn/understanding',
    status: 'started',
    intent_record_sha256: '2'.repeat(64),
    args: canonicalJson({ sessionRef: 'web:manifest-race:request-1' }),
  };
  const spec = turnEffectManifestSpec(parent);
  const manifest = {
    execution_key: spec.executionKey,
    execution_seq: 42,
    turn_id: spec.turnId,
    tool: spec.tool,
    status: 'done',
    execution_verification: 'verified',
    args: canonicalJson(spec.args),
    output: canonicalJson(spec.output),
    intent_record_sha256: '3'.repeat(64),
    completion_record_sha256: '4'.repeat(64),
  };
  assert.equal(classifyTurnEffectManifest({
    parent, records: [manifest], invalidRecords: [],
  }).disposition, 'verified_empty');

  const child = {
    execution_key: 'unrelated-to-manifest-key-but-bound-to-parent-turn',
    execution_seq: 43,
    turn_id: 'web:manifest-race:request-1',
    tool: 'ha_zet', status: 'started', execution_verification: 'verified',
    intent_record_sha256: '5'.repeat(64), completion_record_sha256: null,
  };
  assert.equal(classifyTurnEffectManifest({
    parent, records: [manifest, child], invalidRecords: [],
  }).disposition, 'effect_reconciliation_required');
});

test('een ruwe HTTP-ledgermint geeft geen durable result-authority', () => {
  const conversationId = 'web:raw-ledger-mint';
  const requestId = 'http-request:raw-ledger-mint:1';
  const intent = {
    contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Familie',
    rawText: 'Geef alleen een aantoonbaar antwoord.', conversationId,
    syntheticTest: false, clientAckPublicKey: clientAckPublicKey(),
  };
  const begun = beginHttpIngress({ channel: 'web', conversationId, requestId, intent });
  assert.equal(begun.kind, 'started');
  const identity = { channel: 'web', conversationId, messageId: requestId };
  const executionKey = `turn-understanding:${sha256(canonicalJson(identity))}`;
  const runtimeResult = {
    channel: 'web', conversationId, originMessageId: requestId,
    turnId: 'turn:raw-ledger-mint', reply: 'Dit antwoord is niet echt uitgevoerd.',
    syntheticTest: false,
  };
  const prepared = createPreparedUnderstandingTurn(runtimeResult);

  // Simuleer een historisch correct gehashte V2-eindrij die vóór de sidecargrens
  // bestond. Ook zo'n intern consistente rij is voortaan nooit @turn-authority.
  const args = {
    ...identity, requester: intent.sender, originalText: intent.rawText.trim(),
    sessionRef: '', syntheticTest: false,
    supersessionScope: null, supersessionToken: null,
  };
  const storedArgs = canonicalJson(args);
  const turnId = `ingress:web:${conversationId}:${requestId}`;
  const tool = '@turn/understanding';
  const executionSeq = Number(db.prepare(
    'SELECT coalesce(max(execution_seq),0)+1 AS value FROM main.turn_tool_executions',
  ).get().value);
  const semanticHash = sha256(canonicalJson({ turnId, tool, args, binding: null }));
  const intentRecordSha256 = sha256(canonicalJson({
    executionContract: 'ExecutionRecord/v2', executionSeq,
    turnId, tool, args, binding: null, semanticHash,
  }));
  const output = canonicalJson(prepared);
  const outputSha256 = sha256(output);
  const startedTs = new Date().toISOString();
  const finishedTs = new Date(Date.parse(startedTs) + 1).toISOString();
  const completionRecordSha256 = sha256(canonicalJson({
    executionContract: 'ExecutionRecord/v2', executionSeq,
    executionKey, turnId, tool, args: storedArgs,
    semanticHash, semanticBinding: null, intentRecordSha256,
    status: 'done', startedTs, finishedTs, outputSha256, materialSha256: null,
  }));
  db.prepare(`INSERT INTO main.turn_tool_executions (
    execution_key,turn_id,tool,args,status,started_ts,finished_ts,output,
    semantic_hash,semantic_binding,intent_record_sha256,output_sha256,
    material_sha256,completion_record_sha256,execution_seq,execution_contract,
    execution_verification
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    executionKey, turnId, tool, storedArgs, 'done', startedTs, finishedTs, output,
    semanticHash, null, intentRecordSha256, outputSha256, null,
    completionRecordSha256, executionSeq, 'ExecutionRecord/v2', 'verified',
  );
  assert.throws(() => validateExecutionRecord(db.prepare(`SELECT * FROM
    main.turn_tool_executions WHERE execution_key=?`).get(executionKey)),
  /store-owned authority/iu);
  const authority = issueVerifiedHttpResultAuthority(
    runtimeResult, begun.preparationAuthority,
  );
  assert.equal(authority, null, 'historische V2-ledger zonder sidecar werd vertrouwd');
});

test('publieke voice- en Telegram-issuers kunnen geen @turn-authority minten', async () => {
  const voiceMessageId = 'voice-public-issuer-1';
  const voiceSatellite = 'assist_satellite.keuken';
  const voiceContext = {
    conversationId: 'voice:public-issuer', messageId: voiceMessageId,
    voiceSatellite, voiceLaneKey: `voice-lane:${voiceSatellite}`,
    voiceUnderstandingAuthority: Object.freeze({}),
    identityTrusted: true, syntheticTest: true,
  };
  assert.throws(() => handleMessageDetailed({
    channel: 'voice', sender: 'Jaap', text: 'Zeg hallo.',
    conversationId: voiceContext.conversationId, messageContext: voiceContext,
  }), (error) => error?.code === 'EVA_TURN_AUTHORITY_INGRESS_MISMATCH');

  const telegramContext = {
    conversationId: 'telegram:12345', chatId: '12345',
    messageId: 'telegram-public-issuer-1', identityTrusted: true, syntheticTest: true,
  };
  assert.throws(() => handleMessageDetailed({
    channel: 'telegram', sender: 'Jaap', text: 'Zeg hallo.',
    conversationId: telegramContext.conversationId, messageContext: telegramContext,
  }), (error) => error?.code === 'EVA_TURN_AUTHORITY_INGRESS_UNAVAILABLE');

  const count = db.prepare(`SELECT count(*) AS value
    FROM main.eva_turn_understanding_authorities_v1
    WHERE message_id IN (?,?)`).get(voiceMessageId, telegramContext.messageId).value;
  assert.equal(count, 0);
});

function modelResponse(output) {
  const bytes = Buffer.from(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`);
  return { ok: true, status: 200, body: Object.freeze({
    async *[Symbol.asyncIterator]() { yield bytes; },
  }) };
}

function understandingOutput(text, reply, callId) {
  return [{
    type: 'function_call', call_id: callId,
    name: 'commit_turn_understanding', arguments: JSON.stringify({
      dialogueAct: 'chat',
      goal: { statement: text, outcome: 'Een eerlijk antwoord.', beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft: reply },
    }),
  }];
}

test('oversized Prepared-uitvoer rolt de private CAS terug en blijft herstelbaar', async () => {
  const conversationId = 'web:oversized-private-completion';
  const requestId = 'http-request:oversized-private-completion:1';
  const text = 'Geef een synthetisch antwoord voor de grensproef.';
  const intent = {
    contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Jaap',
    rawText: text, conversationId, syntheticTest: true,
    clientAckPublicKey: clientAckPublicKey(),
  };
  const begun = beginHttpIngress({ channel: 'web', conversationId, requestId, intent });
  assert.equal(begun.kind, 'started');
  const fetchDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'fetch');
  let calls = 0;
  Object.defineProperty(globalThis, 'fetch', { configurable: true, writable: true,
    value: async () => {
      calls += 1;
      return calls === 1
        ? modelResponse(understandingOutput(text, 'Veilige basis.', 'understand-oversized'))
        : modelResponse([{
          type: 'message', role: 'assistant',
          content: [{ type: 'output_text', text: 'x'.repeat(25_000) }],
        }]);
    } });
  try {
    await assert.rejects(handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text,
      messageContext: {
        conversationId, messageId: requestId, identityTrusted: true,
        syntheticTest: true, httpIngressAuthority: begun.preparationAuthority,
        httpIngressIntent: intent,
      },
    }), (error) => error?.code === 'EVA_TURN_UNDERSTANDING_OUTPUT_INVALID');
  } finally {
    restoreOwnProperty(globalThis, 'fetch', fetchDescriptor);
  }
  const identity = { channel: 'web', conversationId, messageId: requestId };
  const executionKey = `turn-understanding:${sha256(canonicalJson(identity))}`;
  const unchanged = db.prepare(`SELECT status,finished_ts,output,output_sha256,
    material_sha256,completion_record_sha256 FROM main.turn_tool_executions
    WHERE execution_key=?`).get(executionKey);
  assert.deepEqual(unchanged, {
    status: 'started', finished_ts: null, output: null, output_sha256: null,
    material_sha256: null, completion_record_sha256: null,
  });
  assert.deepEqual(db.prepare(`SELECT source_generation FROM
    main.eva_turn_understanding_authorities_v1 WHERE execution_key=?`).all(executionKey), [
    { source_generation: 1 },
  ]);

  const recovered = await handleMessageDetailed({
    channel: 'web', sender: 'Jaap', text,
    messageContext: {
      conversationId, messageId: requestId, identityTrusted: true,
      syntheticTest: true, httpIngressAuthority: begun.preparationAuthority,
      httpIngressIntent: intent,
    },
  });
  assert.equal(recovered.replayed, true);
  assert.equal(recovered.error, 'runtime_interrupted');
  assert.equal(db.prepare(`SELECT status FROM main.turn_tool_executions
    WHERE execution_key=?`).get(executionKey).status, 'done');
});

test('een current HTTP-takeover voegt een event toe en rondt dezelfde started rij af', async () => {
  const conversationId = 'web:takeover-authority';
  const requestId = 'http-request:takeover-authority:1';
  const text = 'Geef een kort antwoord.';
  const reply = 'Kort en controleerbaar.';
  const intent = {
    contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Jaap',
    rawText: text, conversationId, syntheticTest: true,
    clientAckPublicKey: clientAckPublicKey(),
  };
  const begun = beginHttpIngress({ channel: 'web', conversationId, requestId, intent });
  assert.equal(begun.kind, 'started');
  const fetchDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'fetch');
  const nowDescriptor = Object.getOwnPropertyDescriptor(Date, 'now');
  let secondCallStarted;
  const secondCall = new Promise((resolve) => { secondCallStarted = resolve; });
  let resolveSecondCall;
  const delayedSecondResponse = new Promise((resolve) => { resolveSecondCall = resolve; });
  let calls = 0;
  Object.defineProperty(globalThis, 'fetch', { configurable: true, writable: true,
    value: async () => {
      calls += 1;
      if (calls === 1) {
        return modelResponse(understandingOutput(text, reply, 'understand-takeover'));
      }
      secondCallStarted();
      return delayedSecondResponse;
    } });
  const running = handleMessageDetailed({
    channel: 'web', sender: 'Jaap', text,
    messageContext: {
      conversationId, messageId: requestId, identityTrusted: true, syntheticTest: true,
      httpIngressAuthority: begun.preparationAuthority, httpIngressIntent: intent,
    },
  });
  let takeover;
  try {
    await secondCall;
    const takeoverAt = Date.now() + 31_000;
    Object.defineProperty(Date, 'now', { configurable: true, writable: true,
      value: () => takeoverAt });
    takeover = beginHttpIngress({ channel: 'web', conversationId, requestId, intent });
    restoreOwnProperty(Date, 'now', nowDescriptor);
    assert.equal(takeover.kind, 'resume');
    assert.equal(takeover.claim.generation, 2);
    resolveSecondCall(modelResponse([{
      type: 'message', role: 'assistant',
      content: [{ type: 'output_text', text: reply }],
    }]));
    await assert.rejects(running, /mutation-authority is niet meer actueel/u);
  } finally {
    restoreOwnProperty(Date, 'now', nowDescriptor);
    restoreOwnProperty(globalThis, 'fetch', fetchDescriptor);
  }
  const row = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE tool='@turn/understanding' AND json_extract(args,'$.messageId')=?`).get(requestId);
  assert.equal(row.status, 'started');
  assert.equal(row.output, null);
  assert.deepEqual(db.prepare(`SELECT source_generation
    FROM main.eva_turn_understanding_authorities_v1
    WHERE execution_key=? ORDER BY source_generation`).all(row.execution_key), [
    { source_generation: 1 },
  ]);
  const resumed = await handleMessageDetailed({
    channel: 'web', sender: 'Jaap', text,
    messageContext: {
      conversationId, messageId: requestId, identityTrusted: true, syntheticTest: true,
      httpIngressAuthority: takeover.preparationAuthority, httpIngressIntent: intent,
    },
  });
  assert.equal(resumed.error, 'runtime_interrupted');
  assert.equal(resumed.replayed, true);
  const completed = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(row.execution_key);
  assert.equal(completed.status, 'done');
  assert.equal(JSON.parse(completed.output).contract, 'PreparedUnderstandingTurn/v1');
  const events = db.prepare(`SELECT source_generation,source_ref,source_sha256
    FROM main.eva_turn_understanding_authorities_v1
    WHERE execution_key=? ORDER BY source_generation`).all(row.execution_key);
  assert.deepEqual(events.map(({ source_generation: generation }) => generation), [1, 2]);
  assert.equal(events[0].source_ref, events[1].source_ref);
  assert.equal(events[0].source_sha256, events[1].source_sha256);
  assert.throws(() => db.prepare(`UPDATE main.eva_turn_understanding_authorities_v1
    SET issued_ts=issued_ts WHERE execution_key=? AND source_generation=1`)
    .run(row.execution_key), /append-only/u);
});

test('een started V2-rij zonder manifest faalt na genuine generation-adoptie gesloten',
  async () => {
    const conversationId = 'web:same-generation-adopt';
    const requestId = 'http-request:same-generation-adopt:1';
    const text = 'Herstel deze synthetische beurt veilig.';
    const intent = {
      contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Jaap',
      rawText: text, conversationId, syntheticTest: true,
      clientAckPublicKey: clientAckPublicKey(),
    };
    const begun = beginHttpIngress({ channel: 'web', conversationId, requestId, intent });
    assert.equal(begun.kind, 'started');
    const identity = { channel: 'web', conversationId, messageId: requestId };
    const executionKey = `turn-understanding:${sha256(canonicalJson(identity))}`;
    insertStartedExecution({
      executionKey, turnId: `ingress:web:${conversationId}:${requestId}`,
      tool: '@turn/understanding', args: {
        ...identity, requester: 'Jaap', originalText: text,
        sessionRef: `web:${sha256(canonicalJson({ conversationId, messageId: requestId }))}`,
        syntheticTest: true, supersessionScope: null, supersessionToken: null,
      },
    });
    const result = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text,
      messageContext: {
        conversationId, messageId: requestId, identityTrusted: true,
        syntheticTest: true, httpIngressAuthority: begun.preparationAuthority,
        httpIngressIntent: intent,
      },
    });
    assert.equal(result.error, 'effect_reconciliation_required');
    assert.equal(result.noPlanDecision, 'recovery:effect_reconciliation_required');
    const row = db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(executionKey);
    const sidecar = db.prepare(`SELECT * FROM main.eva_turn_understanding_authorities_v1
      WHERE execution_key=?`).get(executionKey);
    assert.equal(row.status, 'done');
    assert.equal(sidecar.source_generation, 1);
    assert.equal(JSON.parse(row.output).contract, 'PreparedUnderstandingTurn/v1');
  });

test('de echte HTTP preparation mint atomair één sidecar en typed Prepared-resultaat', async () => {
  const conversationId = 'web:store-owned-authority';
  const requestId = 'http-request:store-owned-authority:1';
  const text = 'Vertel in één zin hoe je erbij zit.';
  const reply = 'Ik ben rustig, alert en bij dit gesprek.';
  const intent = {
    contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Jaap',
    rawText: text, conversationId, syntheticTest: true,
    clientAckPublicKey: clientAckPublicKey(),
  };
  const begun = beginHttpIngress({ channel: 'web', conversationId, requestId, intent });
  assert.equal(begun.kind, 'started');
  let calls = 0;
  const fetchDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'fetch');
  Object.defineProperty(globalThis, 'fetch', { configurable: true, writable: true,
    value: async () => {
    calls += 1;
    if (calls === 1) return modelResponse([{
      type: 'function_call', call_id: 'understand-store-owned',
      name: 'commit_turn_understanding', arguments: JSON.stringify({
        dialogueAct: 'chat',
        goal: { statement: text, outcome: 'Een eerlijk antwoord.', beneficiary: 'requester' },
        conditions: [],
        provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
        confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
        temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
        firstDecision: { kind: 'reply', requirementIds: [], replyDraft: reply },
      }),
    }]);
    return modelResponse([{
      type: 'message', role: 'assistant',
      content: [{ type: 'output_text', text: reply }],
    }]);
    } });
  let result;
  try {
    result = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text,
      messageContext: {
        conversationId, messageId: requestId, identityTrusted: true, syntheticTest: true,
        httpIngressAuthority: begun.preparationAuthority,
        httpIngressIntent: intent,
      },
    });
  } finally {
    restoreOwnProperty(globalThis, 'fetch', fetchDescriptor);
  }
  assert.equal(result.reply, reply);
  const row = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE tool='@turn/understanding' AND json_extract(args,'$.messageId')=?`).get(requestId);
  const sidecar = db.prepare(`SELECT * FROM main.eva_turn_understanding_authorities_v1
    WHERE execution_key=?`).get(row.execution_key);
  assert.equal(row.status, 'done');
  assert.equal(row.execution_contract, 'ExecutionRecord/v2');
  assert.equal(sidecar.contract, 'TurnUnderstandingExecutionAuthority/v1');
  assert.equal(sidecar.execution_seq, row.execution_seq);
  assert.equal(sidecar.intent_record_sha256, row.intent_record_sha256);
  assert.equal(sidecar.source_kind, 'http_ingress');
  assert.equal(sidecar.source_sha256, begun.request.intentSha256);
  assert.equal(JSON.parse(row.output).contract, 'PreparedUnderstandingTurn/v1');
  assert.equal(preparedUnderstandingIngressReplay({
    channel: 'web', conversationId, messageId: requestId, sender: 'Jaap',
  })?.reply, reply);
  assert.ok(issueVerifiedHttpResultAuthority(result, begun.preparationAuthority));
  assert.throws(() => db.prepare(`UPDATE main.eva_turn_understanding_authorities_v1
    SET issued_ts=issued_ts WHERE execution_key=?`).run(row.execution_key), /append-only/u);
  assert.throws(() => db.prepare(`DELETE FROM main.eva_turn_understanding_authorities_v1
    WHERE execution_key=?`).run(row.execution_key), /append-only/u);
  assert.throws(() => db.prepare(`UPDATE main.eva_turn_understanding_authorities_v1
    SET source_body_json='{}' WHERE execution_key=?`).run(row.execution_key), /append-only/u);
  assert.throws(() => db.prepare(`INSERT OR REPLACE INTO
    main.eva_turn_understanding_authorities_v1 (
      contract,execution_key,execution_seq,intent_record_sha256,channel,
      conversation_id,message_id,source_kind,source_ref,source_sha256,
      source_body_json,source_generation,authority_sha256,issued_ts
    ) SELECT contract,execution_key,execution_seq,intent_record_sha256,channel,
      conversation_id,message_id,source_kind,source_ref,source_sha256,
      source_body_json,source_generation,authority_sha256,issued_ts
      FROM main.eva_turn_understanding_authorities_v1 WHERE execution_key=?`).run(
    row.execution_key,
  ), /replacement is forbidden|append-only|requires exact started V2 execution/u);
});
