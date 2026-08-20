import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import test, { after } from 'node:test';
import { fileURLToPath } from 'node:url';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-interpretation-handoff-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'test-only-secret';

const { db } = await import('../src/db.js');
const { redactSensitiveText } = await import('../src/security/redaction.js');
const { canonicalJson } = await import('../src/util.js');
const {
  createDurableWork,
  createInterpretationRetryHandoff,
  completeInterpretationRetry,
  interpretationRetryResume,
  resolveInterpretationRetryFailure,
  transitionGoalEpisode,
} = await import('../src/mind/portfolio.js');

after(() => {
  if (db.open) db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function counts() {
  return {
    work: db.prepare('SELECT COUNT(*) AS n FROM eva_work_items').get().n,
    episodes: db.prepare('SELECT COUNT(*) AS n FROM eva_goal_episodes').get().n,
    handoffs: db.prepare(
      'SELECT COUNT(*) AS n FROM eva_interpretation_retry_handoffs',
    ).get().n,
  };
}

function conflict(args) {
  assert.throws(
    () => createInterpretationRetryHandoff(args),
    /conflicterende ChatInterpretationRetry\/v1-handoff/iu,
  );
}

function contentionWorker(args, workerDataDir) {
  const script = `
    const { createInterpretationRetryHandoff } = await import('./src/mind/portfolio.js');
    const { db } = await import('./src/db.js');
    process.stdout.write('__READY__\\n');
    await new Promise((resolve) => process.stdin.once('data', resolve));
    const result = createInterpretationRetryHandoff(${JSON.stringify(args)});
    process.stdout.write('__RESULT__' + JSON.stringify({
      workItemId: result.workItem.id, episodeId: result.episode.id,
    }) + '\\n');
    db.close();
  `;
  const child = spawn(process.execPath, ['--input-type=module', '-e', script], {
    cwd: projectRoot,
    env: { ...process.env, EVA_DATA_DIR: workerDataDir },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stdout = '';
  let stderr = '';
  const ready = new Promise((resolve, reject) => {
    child.stdout.on('data', (chunk) => {
      stdout += String(chunk);
      if (stdout.includes('__READY__')) resolve();
    });
    child.stderr.on('data', (chunk) => { stderr += String(chunk); });
    child.once('error', reject);
    child.once('exit', (code) => {
      if (!stdout.includes('__READY__')) reject(new Error(stderr || `worker stopte vroeg: ${code}`));
    });
  });
  const done = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', (code) => {
      if (code !== 0) return reject(new Error(stderr || stdout || `worker stopte: ${code}`));
      const match = stdout.match(/__RESULT__(\{.*\})/u);
      return match ? resolve(JSON.parse(match[1]))
        : reject(new Error(`workerresultaat ontbreekt: ${stdout}`));
    });
  });
  return { child, ready, done };
}

test('interpretatiehandoff is exact-origin-idempotent over lifecycle en restart', () => {
  const forbiddenSemanticMarker = 'DENIED-SEMANTIC-INPUT-MUST-NOT-PERSIST';
  const forbiddenActionMarker = 'DENIED-ACTION-MUST-NOT-PERSIST';
  const originalText = 'Herlees mijn opdracht met api-key: test-only-secret en probeer later opnieuw.';
  const args = {
    channel: 'web',
    conversationId: 'web:handoff-idempotency',
    messageId: 'inbound-421',
    requester: 'Jaap',
    originalText,
    failureClass: 'interpreter_timeout',
    sessionRef: 'turn:first',
    supersessionScope: 'web:handoff-idempotency',
    supersessionToken: 'ingress-token-421',
    deniedActions: [{ capabilityId: forbiddenActionMarker }],
    semanticInput: { geheim: forbiddenSemanticMarker },
  };
  const before = counts();
  const first = createInterpretationRetryHandoff(args);
  const afterCreate = counts();
  assert.deepEqual(afterCreate, {
    work: before.work + 1,
    episodes: before.episodes + 1,
    handoffs: before.handoffs + 1,
  });

  const replay = createInterpretationRetryHandoff({
    ...args,
    failureClass: 'selected_denied_action',
    sessionRef: 'turn:transport-replay',
  });
  assert.equal(replay.workItem.id, first.workItem.id);
  assert.equal(replay.episode.id, first.episode.id);
  assert.deepEqual(counts(), afterCreate);

  const row = db.prepare(`SELECT * FROM eva_interpretation_retry_handoffs
    WHERE episode_id=?`).get(first.episode.id);
  const state = JSON.parse(first.workItem.state_json).interpretation_retry;
  const expectedText = redactSensitiveText(originalText);
  const expectedRawTextSha256 = createHash('sha256').update(originalText).digest('hex');
  const expectedTextSha256 = createHash('sha256').update(expectedText).digest('hex');
  const expectedHandoffKey = createHash('sha256').update(canonicalJson({
    contract: 'ChatInterpretationRetry/v1',
    channel: args.channel,
    conversationId: args.conversationId,
    messageId: args.messageId,
    rawOriginalTextSha256: expectedRawTextSha256,
    originalTextSha256: expectedTextSha256,
  })).digest('hex');
  assert.equal(row.handoff_key, expectedHandoffKey);
  assert.equal(row.raw_original_text_sha256, expectedRawTextSha256);
  assert.equal(row.original_text_sha256, expectedTextSha256);
  assert.equal(state.handoffKey, expectedHandoffKey);
  assert.equal(state.rawOriginalTextSha256, expectedRawTextSha256);
  assert.equal(state.originalText, expectedText);
  assert.doesNotMatch(JSON.stringify({ row, work: first.workItem, episode: first.episode }),
    new RegExp(`${forbiddenSemanticMarker}|${forbiddenActionMarker}`, 'u'));

  const conflictCounts = counts();
  conflict({ ...args, originalText: `${originalText} Dit is andere inhoud.` });
  conflict({ ...args, requester: 'Anouk' });
  conflict({ ...args, supersessionScope: 'web:andere-scope' });
  conflict({ ...args, supersessionToken: 'ingress-token-422' });
  assert.deepEqual(counts(), conflictCounts);
  const sameRedactionA = 'Controleer api-key: alphaalphaalpha later.';
  const sameRedactionB = 'Controleer api-key: betabetabetab later.';
  assert.equal(redactSensitiveText(sameRedactionA), redactSensitiveText(sameRedactionB));
  const collisionArgs = {
    ...args, conversationId: 'web:redaction-collision', messageId: 'inbound-collision',
    originalText: sameRedactionA,
  };
  const collision = createInterpretationRetryHandoff(collisionArgs);
  const afterCollision = counts();
  conflict({ ...collisionArgs, originalText: sameRedactionB });
  assert.deepEqual(counts(), afterCollision);
  assert.notEqual(collision.workItem.id, first.workItem.id);

  const beforeInvalid = counts();
  for (const invalid of [
    { ...args, conversationId: '' },
    { ...args, messageId: `m${'x'.repeat(160)}` },
    { ...args, requester: `J${'a'.repeat(80)}` },
    { ...args, conversationId: ' web:niet-canoniek' },
    { ...args, requester: 'Jaap  Dubbel' },
    { ...args, supersessionScope: 'scope-zonder-token', supersessionToken: '' },
  ]) assert.throws(() => createInterpretationRetryHandoff(invalid), /interpretatieherstel/iu);
  assert.throws(() => createInterpretationRetryHandoff({
    ...args,
    conversationId: 'web:oversized-state',
    messageId: 'oversized-state',
    originalText: 'x'.repeat(12_100),
  }), /overschrijdt de duurzame limiet/iu);
  assert.deepEqual(counts(), beforeInvalid);

  assert.throws(() => db.prepare(`UPDATE eva_interpretation_retry_handoffs
    SET binding_sha256=? WHERE episode_id=?`).run('0'.repeat(64), first.episode.id),
  /registry is onveranderlijk/iu);
  assert.throws(() => db.prepare(
    'DELETE FROM eva_interpretation_retry_handoffs WHERE episode_id=?',
  ).run(first.episode.id), /registry is onveranderlijk/iu);
  assert.throws(() => db.prepare(`INSERT OR REPLACE
    INTO eva_interpretation_retry_handoffs
    SELECT * FROM eva_interpretation_retry_handoffs WHERE episode_id=?`)
    .run(first.episode.id), /registryidentiteit is onveranderlijk/iu);
  assert.deepEqual(
    db.prepare('SELECT * FROM eva_interpretation_retry_handoffs WHERE episode_id=?')
      .get(first.episode.id),
    row,
  );
  assert.deepEqual(counts(), afterCollision);

  const currentWork = db.prepare('SELECT * FROM eva_work_items WHERE id=?').get(first.workItem.id);
  const currentEpisode = db.prepare('SELECT * FROM eva_goal_episodes WHERE id=?')
    .get(first.episode.id);
  const retry = interpretationRetryResume(currentWork, currentEpisode);
  const blocked = resolveInterpretationRetryFailure({
    item: currentWork, episode: currentEpisode,
    retry: { ...retry, stopReason: 'attempt_limit' },
    sessionRef: 'test:block',
  });
  assert.equal(blocked.kind, 'blocked');
  assert.equal(blocked.episode.state, 'blocked');
  assert.equal(JSON.parse(blocked.workItem.state_json).interpretation_retry, undefined);
  const blockedReplay = createInterpretationRetryHandoff(args);
  assert.equal(blockedReplay.workItem.id, first.workItem.id);
  assert.equal(blockedReplay.episode.id, first.episode.id);

  const terminalState = JSON.parse(blockedReplay.workItem.state_json);
  const closed = transitionGoalEpisode(first.episode.id, {
    state: 'closed', deliveryPolicy: 'none', checkpoint: 'Testresultaat gecontroleerd.',
    proofSummary: 'Testresultaat gecontroleerd.',
    work: {
      state: terminalState, clearWait: true, nextWakeTs: '',
      evidence: 'Testresultaat gecontroleerd.', lastAction: 'test_afgerond',
    },
  });
  assert.equal(closed.episode.state, 'closed');
  assert.equal(closed.workItem.status, 'done');
  const closedReplay = createInterpretationRetryHandoff(args);
  assert.equal(closedReplay.workItem.id, first.workItem.id);
  assert.equal(closedReplay.episode.id, first.episode.id);
  assert.deepEqual(counts(), afterCollision);

  const childScript = `
    const { createInterpretationRetryHandoff } = await import('./src/mind/portfolio.js');
    const { db } = await import('./src/db.js');
    const result = createInterpretationRetryHandoff(${JSON.stringify(args)});
    console.log('__HANDOFF_RESULT__' + JSON.stringify({
      workItemId: result.workItem.id, episodeId: result.episode.id,
      state: result.episode.state, status: result.workItem.status,
    }));
    db.close();
  `;
  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', childScript], {
    cwd: projectRoot,
    env: { ...process.env, EVA_DATA_DIR: dataDir },
    encoding: 'utf8',
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const output = restarted.stdout.match(/__HANDOFF_RESULT__(\{.*\})/u);
  assert.ok(output, restarted.stdout);
  assert.deepEqual(JSON.parse(output[1]), {
    workItemId: first.workItem.id,
    episodeId: first.episode.id,
    state: 'closed',
    status: 'done',
  });
});

test('corrupte actieve retrystate faalt gesloten zonder duplicaat', () => {
  const args = {
    channel: 'web', conversationId: 'web:corrupt-retry', messageId: 'corrupt-1',
    requester: 'Jaap', originalText: 'Pak deze opdracht na een tijdelijke fout opnieuw op.',
  };
  const created = createInterpretationRetryHandoff(args);
  const before = counts();
  const corrupted = JSON.parse(created.workItem.state_json);
  corrupted.interpretation_retry.originalTextSha256 = '0'.repeat(64);
  db.prepare('UPDATE eva_work_items SET state_json=? WHERE id=?')
    .run(JSON.stringify(corrupted), created.workItem.id);
  conflict(args);
  assert.deepEqual(counts(), before);

  db.prepare("UPDATE eva_work_items SET state_json='{}' WHERE id=?").run(created.workItem.id);
  conflict(args);
  assert.deepEqual(counts(), before);
});

test('losse gekopieerde retrystate is zonder exacte registry-lifecyclebinding niet hervatbaar', () => {
  const args = {
    channel: 'web', conversationId: 'web:forged-retry', messageId: 'forged-1',
    requester: 'Jaap', originalText: 'Hervat alleen via de exact geregistreerde oorsprong.',
  };
  const registered = createInterpretationRetryHandoff(args);
  const alteredProjection = interpretationRetryResume({
    ...registered.workItem, next_step: 'Voer een niet-geregistreerde stap uit.',
  }, registered.episode);
  assert.equal(alteredProjection.valid, false);
  assert.equal(alteredProjection.stopReason, 'invalid');
  const forged = createDurableWork({
    source: args.channel, owner: args.requester,
    title: 'Niet-geregistreerde kopie', goal: 'Deze kopie mag nooit uitvoerauthority krijgen.',
    nextStep: 'Stop zonder uitvoering.', visibleToFamily: true,
    definitionOfDone: 'Nooit via gekopieerde retrystate uitvoeren.', priority: 75,
  }, {
    originKind: 'user', originChannel: args.channel,
    originConversationId: args.conversationId, originMessageId: args.messageId,
    requester: args.requester, deliveryPolicy: 'required', deliveryTarget: args.requester,
  });
  const copiedState = JSON.parse(registered.workItem.state_json);
  const waiting = transitionGoalEpisode(forged.episode.id, {
    state: 'waiting', wakeTs: registered.episode.wake_ts,
    checkpoint: 'Gekopieerde staat zonder registryrij.',
    nextAction: 'Stop zonder uitvoering.',
    work: {
      state: copiedState,
      nextWakeTs: registered.episode.wake_ts,
      wait: { reason: 'interpretation_retry', resumeAfter: registered.episode.wake_ts },
    },
  });

  const retry = interpretationRetryResume(waiting.workItem, waiting.episode);
  assert.equal(retry.valid, false);
  assert.equal(retry.stopReason, 'invalid');
  assert.throws(() => completeInterpretationRetry(
    waiting.workItem, waiting.episode, 'test:forged', 'a'.repeat(64), 'b'.repeat(64),
  ), /duurzaam gebonden uitvoering/iu);
  assert.equal(
    db.prepare('SELECT state FROM eva_goal_episodes WHERE id=?').get(waiting.episode.id).state,
    'waiting',
  );
});

test('een eenmaal gebonden understanding kan bij hetzelfde retryplan niet worden vervangen', () => {
  const created = createInterpretationRetryHandoff({
    channel: 'web', conversationId: 'web:frozen-understanding', messageId: 'frozen-1',
    requester: 'Jaap', originalText: 'Voer het eenmaal gebonden plan exact uit.',
  });
  const planSha256 = '1'.repeat(64);
  const understandingSha256 = '2'.repeat(64);
  const materialAuthoritySha256 = '3'.repeat(64);
  const understood = completeInterpretationRetry(
    created.workItem, created.episode, 'first-understanding',
    planSha256, understandingSha256, materialAuthoritySha256,
  );
  assert.equal(
    interpretationRetryResume(understood.workItem, understood.episode).understandingSha256,
    understandingSha256,
  );
  assert.throws(() => completeInterpretationRetry(
    understood.workItem, understood.episode, 'replacement-understanding',
    planSha256, '4'.repeat(64), materialAuthoritySha256,
  ), /duurzaam gebonden uitvoering/iu);
  const freshWork = db.prepare('SELECT * FROM eva_work_items WHERE id=?')
    .get(created.workItem.id);
  const freshEpisode = db.prepare('SELECT * FROM eva_goal_episodes WHERE id=?')
    .get(created.episode.id);
  assert.equal(
    interpretationRetryResume(freshWork, freshEpisode).understandingSha256,
    understandingSha256,
  );
});

test('schema-objecttamper na startup blokkeert resume, completion en nieuwe create', () => {
  for (const fixture of ['dropped_trigger', 'weakened_index']) {
    const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), `bottie-handoff-runtime-${fixture}-`));
    try {
      const script = `
        const fixture = ${JSON.stringify(fixture)};
        const { db } = await import('./src/db.js');
        const {
          createInterpretationRetryHandoff, completeInterpretationRetry,
          interpretationRetryResume,
        } = await import('./src/mind/portfolio.js');
        const created = createInterpretationRetryHandoff({
          channel:'web',conversationId:'web:runtime-tamper',messageId:'tamper-1',
          requester:'Jaap',originalText:'Deze opdracht mag alleen via een intact register hervatten.',
        });
        if (fixture === 'dropped_trigger') {
          db.exec('DROP TRIGGER trg_interpretation_retry_handoff_immutable_delete');
        } else {
          db.exec(\`DROP INDEX idx_eva_interpretation_retry_origin;
            CREATE INDEX idx_eva_interpretation_retry_origin
            ON eva_interpretation_retry_handoffs(origin_channel)\`);
        }
        const item = db.prepare('SELECT * FROM eva_work_items WHERE id=?').get(created.workItem.id);
        const episode = db.prepare('SELECT * FROM eva_goal_episodes WHERE id=?').get(created.episode.id);
        const retry = interpretationRetryResume(item, episode);
        let completionError = '';
        try {
          completeInterpretationRetry(item, episode, 'test:tamper', 'a'.repeat(64), 'b'.repeat(64));
        } catch (error) { completionError = String(error?.message || error); }
        let createError = '';
        try {
          createInterpretationRetryHandoff({
            channel:'web',conversationId:'web:runtime-tamper-new',messageId:'tamper-2',
            requester:'Jaap',originalText:'Deze tweede opdracht mag niet worden aangemaakt.',
          });
        } catch (error) { createError = String(error?.message || error); }
        const persisted = db.prepare('SELECT state FROM eva_goal_episodes WHERE id=?')
          .get(created.episode.id);
        console.log('__RUNTIME_TAMPER__' + JSON.stringify({
          valid: retry.valid, stopReason: retry.stopReason, completionError, createError,
          episodeState: persisted.state,
          work: db.prepare('SELECT count(*) n FROM eva_work_items').get().n,
          episodes: db.prepare('SELECT count(*) n FROM eva_goal_episodes').get().n,
          handoffs: db.prepare('SELECT count(*) n FROM eva_interpretation_retry_handoffs').get().n,
        }));
        db.close();
      `;
      const child = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
        cwd: projectRoot,
        env: { ...process.env, EVA_DATA_DIR: fixtureDir },
        encoding: 'utf8',
      });
      assert.equal(child.status, 0, child.stderr || child.stdout);
      const match = child.stdout.match(/__RUNTIME_TAMPER__(\{.*\})/u);
      assert.ok(match, child.stdout);
      const result = JSON.parse(match[1]);
      assert.equal(result.valid, false);
      assert.equal(result.stopReason, 'invalid');
      assert.match(result.completionError, /duurzaam gebonden uitvoering/iu);
      assert.match(result.createError, /registryschema is niet exact/iu);
      assert.deepEqual({
        episodeState: result.episodeState,
        work: result.work, episodes: result.episodes, handoffs: result.handoffs,
      }, { episodeState: 'waiting', work: 1, episodes: 1, handoffs: 1 });
    } finally {
      fs.rmSync(fixtureDir, { recursive: true, force: true });
    }
  }
});

test('onbewezen materiaalresultaat herhaalt alleen bij exact dubbelgebonden foutklasse', () => {
  const deniedMarker = 'DENIED-MATERIAL-ACTION-MUST-NOT-COPY';
  const makeHandoff = (suffix) => createInterpretationRetryHandoff({
    channel: 'web', conversationId: `web:material-retry-${suffix}`,
    messageId: `material-${suffix}`, requester: 'Jaap',
    originalText: `Controleer materiaalopdracht ${suffix} zonder externe actie.`,
  });
  const current = (created) => ({
    item: db.prepare('SELECT * FROM eva_work_items WHERE id=?').get(created.workItem.id),
    episode: db.prepare('SELECT * FROM eva_goal_episodes WHERE id=?').get(created.episode.id),
  });

  const created = makeHandoff('exact');
  const before = current(created);
  const retry = interpretationRetryResume(before.item, before.episode);
  const retried = resolveInterpretationRetryFailure({
    ...before, retry,
    error: 'material_completion_unproven',
    validation: 'material_completion_unproven',
    failureClass: 'material_completion_unproven',
    deniedActions: [{ capabilityId: deniedMarker }],
    semanticInput: { deniedMarker },
    sessionRef: 'test:material-retry',
  });
  assert.equal(retried.kind, 'retry');
  assert.equal(retried.episode.state, 'waiting');
  const retryState = JSON.parse(retried.workItem.state_json).interpretation_retry;
  assert.equal(retryState.attempts, retry.attempts + 1);
  assert.ok(Date.parse(retried.episode.wake_ts) <= Date.parse(retry.deadlineAt));
  assert.doesNotMatch(JSON.stringify(retried), new RegExp(deniedMarker, 'u'));

  for (const [suffix, error, validation] of [
    ['wrong-validation', 'material_completion_unproven', 'selected_denied_action'],
    ['wrong-error', 'interpreter_malformed', 'material_completion_unproven'],
  ]) {
    const candidate = makeHandoff(suffix);
    const rows = current(candidate);
    const stopped = resolveInterpretationRetryFailure({
      ...rows, retry: interpretationRetryResume(rows.item, rows.episode),
      error, validation,
      deniedActions: [{ capabilityId: deniedMarker }], semanticInput: { deniedMarker },
      sessionRef: `test:material-${suffix}`,
    });
    assert.equal(stopped.kind, 'stopped');
    assert.equal(stopped.episode.state, 'abandoned');
    assert.equal(JSON.parse(stopped.workItem.state_json).interpretation_retry, undefined);
    assert.doesNotMatch(JSON.stringify(stopped), new RegExp(deniedMarker, 'u'));
  }
});

test('registry migreert alleen de bekende raw-hash-voorloper naar exact schema', () => {
  const migrationDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-handoff-migration-'));
  try {
    const script = `
      const { db } = await import('./src/db.js');
      db.exec(\`CREATE TABLE eva_interpretation_retry_handoffs (
        handoff_key TEXT PRIMARY KEY,
        contract TEXT NOT NULL,
        origin_channel TEXT NOT NULL,
        origin_conversation_id TEXT NOT NULL,
        origin_message_id TEXT NOT NULL,
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
        );\`);
      const hash = 'a'.repeat(64);
      db.prepare(\`INSERT INTO eva_interpretation_retry_handoffs (
        handoff_key,contract,origin_channel,origin_conversation_id,origin_message_id,
        original_text_sha256,requester_sha256,supersession_scope_sha256,
        supersession_token_sha256,work_item_id,episode_id,binding_sha256,created_ts
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)\`).run(
        hash,'ChatInterpretationRetry/v1','web','web:legacy','legacy-1',
        hash,hash,hash,hash,900,901,hash,'2026-08-12T10:00:00.000Z',
      );
      const { createInterpretationRetryHandoff } = await import('./src/mind/portfolio.js');
      let legacyError = '';
      try {
        createInterpretationRetryHandoff({
          channel:'web',conversationId:'web:legacy',messageId:'legacy-1',
          requester:'Jaap',originalText:'Deze tekst kan niet uit de oude hash worden hersteld.',
        });
      } catch (error) { legacyError = String(error?.message || error); }
      const created = createInterpretationRetryHandoff({
        channel:'web',conversationId:'web:new',messageId:'new-1',requester:'Jaap',
        originalText:'Bewaar deze nieuwe opdracht exact voor een gecontroleerde hervatting.',
      });
      let replaceError = '';
      try {
        db.prepare(\`INSERT OR REPLACE INTO eva_interpretation_retry_handoffs
          SELECT * FROM eva_interpretation_retry_handoffs WHERE handoff_key=?\`).run(hash);
      } catch (error) { replaceError = String(error?.message || error); }
      const columns = db.prepare('PRAGMA table_info(eva_interpretation_retry_handoffs)').all();
      const output = {
        legacyError, replaceError,
        oldRaw: db.prepare(\`SELECT raw_original_text_sha256 value
          FROM eva_interpretation_retry_handoffs WHERE handoff_key=?\`).get(hash)?.value,
        allNotNull: columns.every((column) => column.notnull === 1),
        rawColumn: columns.find((column) => column.name === 'raw_original_text_sha256'),
        indexCount: db.prepare('PRAGMA index_list(eva_interpretation_retry_handoffs)').all().length,
        triggerCount: db.prepare(\`SELECT count(*) n FROM sqlite_master
          WHERE type='trigger' AND tbl_name='eva_interpretation_retry_handoffs'\`).get().n,
        work: db.prepare('SELECT count(*) n FROM eva_work_items').get().n,
        episodes: db.prepare('SELECT count(*) n FROM eva_goal_episodes').get().n,
        handoffs: db.prepare('SELECT count(*) n FROM eva_interpretation_retry_handoffs').get().n,
        createdWork: created.workItem.id,
      };
      console.log('__MIGRATION__' + JSON.stringify(output));
      db.close();
    `;
    const migrated = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
      cwd: projectRoot,
      env: { ...process.env, EVA_DATA_DIR: migrationDir },
      encoding: 'utf8',
    });
    assert.equal(migrated.status, 0, migrated.stderr || migrated.stdout);
    const match = migrated.stdout.match(/__MIGRATION__(\{.*\})/u);
    assert.ok(match, migrated.stdout);
    const result = JSON.parse(match[1]);
    assert.match(result.legacyError, /conflicterende ChatInterpretationRetry\/v1-handoff/iu);
    assert.match(result.replaceError, /registryidentiteit is onveranderlijk/iu);
    assert.equal(result.oldRaw, '');
    assert.equal(result.allNotNull, true);
    assert.equal(result.rawColumn.dflt_value, null);
    assert.equal(result.indexCount, 5);
    assert.equal(result.triggerCount, 3);
    assert.deepEqual({ work: result.work, episodes: result.episodes, handoffs: result.handoffs }, {
      work: 1, episodes: 1, handoffs: 2,
    });
    assert.equal(result.createdWork, 1);
  } finally {
    fs.rmSync(migrationDir, { recursive: true, force: true });
  }
});

test('afwijkende gelijknamige trigger of index blokkeert registry vóór lifecyclemutatie', () => {
  for (const fixture of ['weak_trigger', 'weak_index']) {
    const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), `bottie-handoff-${fixture}-`));
    try {
      const script = `
        const fixture = ${JSON.stringify(fixture)};
        const { db } = await import('./src/db.js');
        db.exec(\`CREATE TABLE eva_interpretation_retry_handoffs (
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
        )\`);
        if (fixture === 'weak_index') {
          db.exec(\`CREATE INDEX idx_eva_interpretation_retry_origin
            ON eva_interpretation_retry_handoffs(origin_channel)\`);
        } else {
          db.exec(\`CREATE INDEX idx_eva_interpretation_retry_origin
            ON eva_interpretation_retry_handoffs(
              origin_channel,origin_conversation_id,origin_message_id
            );
          CREATE TRIGGER trg_interpretation_retry_handoff_insert_guard
            BEFORE INSERT ON eva_interpretation_retry_handoffs
            BEGIN SELECT 1; END\`);
        }
        const { createInterpretationRetryHandoff } = await import('./src/mind/portfolio.js');
        let error = '';
        try {
          createInterpretationRetryHandoff({
            channel:'web',conversationId:'web:blocked-schema',messageId:'blocked-1',
            requester:'Jaap',originalText:'Deze poging mag niets duurzaam aanmaken.',
          });
        } catch (caught) { error = String(caught?.message || caught); }
        console.log('__BLOCKED__' + JSON.stringify({
          error,
          work: db.prepare('SELECT count(*) n FROM eva_work_items').get().n,
          episodes: db.prepare('SELECT count(*) n FROM eva_goal_episodes').get().n,
          handoffs: db.prepare('SELECT count(*) n FROM eva_interpretation_retry_handoffs').get().n,
          triggerSql: db.prepare(\`SELECT sql FROM sqlite_master
            WHERE type='trigger' AND name='trg_interpretation_retry_handoff_insert_guard'\`)
            .get()?.sql || '',
          indexColumns: db.prepare(\`SELECT name FROM pragma_index_xinfo(
            'idx_eva_interpretation_retry_origin'
          ) WHERE key=1 ORDER BY seqno\`).all().map((row) => row.name),
        }));
        db.close();
      `;
      const blocked = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
        cwd: projectRoot,
        env: { ...process.env, EVA_DATA_DIR: fixtureDir },
        encoding: 'utf8',
      });
      assert.equal(blocked.status, 0, blocked.stderr || blocked.stdout);
      const match = blocked.stdout.match(/__BLOCKED__(\{.*\})/u);
      assert.ok(match, blocked.stdout);
      const result = JSON.parse(match[1]);
      assert.match(result.error, /registryschema is niet exact; handoff geweigerd/iu);
      assert.deepEqual({ work: result.work, episodes: result.episodes, handoffs: result.handoffs }, {
        work: 0, episodes: 0, handoffs: 0,
      });
      if (fixture === 'weak_trigger') assert.match(result.triggerSql, /BEGIN SELECT 1; END/iu);
      if (fixture === 'weak_index') assert.deepEqual(result.indexColumns, ['origin_channel']);
    } finally {
      fs.rmSync(fixtureDir, { recursive: true, force: true });
    }
  }
});

test('gelijktijdige processen materialiseren exact één handoff', async () => {
  const contentionDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-handoff-contention-'));
  try {
    const bootstrap = spawnSync(process.execPath, ['--input-type=module', '-e', `
      const { db } = await import('./src/db.js');
      await import('./src/mind/portfolio.js');
      db.close();
    `], {
      cwd: projectRoot,
      env: { ...process.env, EVA_DATA_DIR: contentionDir },
      encoding: 'utf8',
    });
    assert.equal(bootstrap.status, 0, bootstrap.stderr || bootstrap.stdout);
    const args = {
      channel: 'web', conversationId: 'web:real-contention', messageId: 'same-inbound',
      requester: 'Jaap',
      originalText: 'Bewaar exact deze opdracht en hervat haar gecontroleerd.',
      failureClass: 'interpreter_timeout',
      supersessionScope: 'web:real-contention', supersessionToken: 'token:one',
    };
    const workers = Array.from({ length: 6 }, () => contentionWorker(args, contentionDir));
    await Promise.all(workers.map((worker) => worker.ready));
    for (const worker of workers) worker.child.stdin.end('go\n');
    const results = await Promise.all(workers.map((worker) => worker.done));
    assert.equal(new Set(results.map((result) => result.workItemId)).size, 1);
    assert.equal(new Set(results.map((result) => result.episodeId)).size, 1);

    const inspect = spawnSync(process.execPath, ['--input-type=module', '-e', `
      const { db } = await import('./src/db.js');
      await import('./src/mind/portfolio.js');
      console.log('__COUNTS__' + JSON.stringify({
        work: db.prepare('SELECT COUNT(*) n FROM eva_work_items').get().n,
        episodes: db.prepare('SELECT COUNT(*) n FROM eva_goal_episodes').get().n,
        handoffs: db.prepare('SELECT COUNT(*) n FROM eva_interpretation_retry_handoffs').get().n,
      }));
      db.close();
    `], {
      cwd: projectRoot,
      env: { ...process.env, EVA_DATA_DIR: contentionDir },
      encoding: 'utf8',
    });
    assert.equal(inspect.status, 0, inspect.stderr || inspect.stdout);
    const match = inspect.stdout.match(/__COUNTS__(\{.*\})/u);
    assert.ok(match, inspect.stdout);
    assert.deepEqual(JSON.parse(match[1]), { work: 1, episodes: 1, handoffs: 1 });
  } finally {
    fs.rmSync(contentionDir, { recursive: true, force: true });
  }
});
