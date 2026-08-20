import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

async function schedulerExplorationScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { createHash } = await import('node:crypto');
  const fs = (await import('node:fs')).default;
  const path = (await import('node:path')).default;
  const {
    claimTurnToolExecution, consumeContributionMutationAuthority, db, kvSet,
    logContributionOutcome, validateExecutionRecord,
  } = await import('./src/db.js');
  const { bus } = await import('./src/bus.js');
  const { canonicalJson } = await import('./src/util.js');
  const { llmRuntimeStatus } = await import('./src/brain/llm.js');
  const { agentStatus, handleMessageDetailed } = await import('./src/mind/agent.js');
  const { autonomyStatus, startAutonomy } = await import('./src/mind/autonomy.js');
  const {
    __curiosityTest, dueAttention, startExploration, updateExploration,
  } = await import('./src/mind/curiosity.js');
  const { setPersonHouseholdRole } = await import('./src/mind/household.js');
  const { upsertPerson } = await import('./src/db.js');
  const withoutExecutionTrigger = (name, operation) => {
    const trigger = db.prepare(`SELECT sql FROM main.sqlite_master
      WHERE type='trigger' AND name=?`).get(name);
    assert.ok(trigger?.sql, `missing execution trigger ${name}`);
    db.exec(`DROP TRIGGER main.${name}`);
    try { return operation(); } finally { db.exec(trigger.sql); }
  };
  upsertPerson('Jaap');
  setPersonHouseholdRole('Jaap', 'household_member');
  const KEY = 'eva_autonomy_scheduler_v1';
  const wait = globalThis.setTimeout.bind(globalThis);
  const timers = [];
  globalThis.setTimeout = (callback, delay, ...args) => {
    const handle = { callback, delay, args, cancelled: false, fired: false,
      scheduler: /\btick\(\)/u.test(String(callback)) };
    timers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => { if (handle) handle.cancelled = true; };
  const completed = (output) => new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output, usage: {
      input_tokens: 1_000, output_tokens: 100, total_tokens: 1_100,
    } },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  const action = (capabilityId, operationId, semanticInput, span) => ({
    capabilityId, operationId, polarity: 'request', semanticInput, sourceSpans: [span],
  });
  const workInput = () => ({
    titel: 'Meetbare gezinsbijdrage uitwerken',
    doel: 'Eén gecontroleerde gezinsbijdrage zichtbaar afronden.',
    volgende_stap: 'Werk het gecontroleerde bronfeit uit tot één zichtbaar resultaat.',
    definitie_klaar: 'Het resultaat is teruggelezen en aan het bronfeit getoetst.',
    hervatten_na: new Date(Date.now() + 30 * 60_000).toISOString(),
  });
  const aspirationAnchorInput = (attentionId, suffix = 'fresh') => ({
    vraag: `Welke concrete huidige waarneming draagt aspiratie ${attentionId} (${suffix})?`,
    aanleiding: 'De verse aspiratie vraagt om één begrensde, duurzame onderzoekscyclus.',
    hypothese: 'Eén actuele contextread levert een toetsbaar aanknopingspunt op.',
    verwachte_waarneming: 'De exacte huidige read levert één controleerbaar contextfeit.',
    stopregel: 'Stop na deze ene huidige waarneming en beoordeel de cyclus opnieuw.',
    max_acties: 2,
    onderwerp_sleutel: `aspiration_anchor_${attentionId}_${suffix}`,
    onderwerp_ankers: [`event:aspiration:${attentionId}`, `constraint:${suffix}`],
    aspiratie_id: attentionId,
  });
  const raw = (payload, actions, {
    durability = 'episode', decision = 'inspect', requestedEffects = null,
    requirementIds = null, conditions = [], temporal = null, delivery = null,
    deniedEffects = [],
  } = {}) => {
    const sentence = 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.';
    const start = payload.turn.text.indexOf(sentence);
    assert.ok(start >= 0, payload.turn.text);
    const span = { start, end: start + sentence.length };
    const projected = actions(span);
    return {
      dialogueAct: 'command', goal: { statement: 'Maak één meetbare gezinsbijdrage.',
        outcome: 'Eén concrete bijdrage is aantoonbaar.', beneficiary: 'household' },
      conditions, provenance: { sourceSpans: [span], contextHashes: [] }, confidence: 0.99,
      actions: projected, requestedEffects: requestedEffects || (!projected.length ? [] : projected.some((item) => (
        item.capabilityId !== 'home.context.read' && item.capabilityId !== 'workspace.file.read'
      )) ? ['read', 'persistent'] : ['read']), deniedEffects,
      temporal: temporal || { mode: 'now', startSourceSpans: [] }, durability,
      delivery: delivery || { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: decision, requirementIds: requirementIds
        || (projected.length ? ['r1'] : []),
        replyDraft: null },
    };
  };
  const insertAttention = (id, {
    text = 'Maak één meetbare gezinsbijdrage.', progress = '',
    lastTouched = '2020-01-01T00:00:00.000Z', cadenceHours = 1, priority = 1,
  } = {}) => db.prepare(`INSERT INTO aspirations
      (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
        evidence,next_step,cadence_hours,priority)
      VALUES (?,'2020-01-01T00:00:00.000Z',?,'scheduler-contract','actief',?,?,'development',
        ?, 'het gezin','','Kies één veilige huidige stap.',?,?)`).run(
    id, text, progress, lastTouched, `scheduler-${id}`, cadenceHours, priority,
  );
  const resetAttention = (id, options = {}) => {
    db.prepare('DELETE FROM aspirations').run();
    db.prepare('DELETE FROM eva_explorations').run();
    db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
    insertAttention(id, options);
    kvSet(KEY, { next_run_ts: new Date(Date.now() - 1_000).toISOString(),
      pending_signals: [], daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0 });
  };
  const createResearchExploration = (suffix, query) => {
    db.prepare('DELETE FROM aspirations').run();
    db.prepare(`UPDATE eva_explorations SET status='completed',next_wake_ts=NULL
      WHERE status IN ('chosen','running','waiting','evaluating')`).run();
    db.prepare("UPDATE eva_work_items SET status='cancelled',lease_owner=NULL,lease_until=NULL").run();
    db.prepare("UPDATE eva_goal_episodes SET state='abandoned',lease_owner=NULL,lease_until=NULL").run();
    db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
    representativeResearchQuery = query;
    const created = startExploration({
      question: query,
      whyNow: `De begrensde providerproef ${suffix} vraagt één actuele bronstap.`,
      hypothesis: 'Eén directe webzoekactie kan een concrete primaire bron opleveren.',
      expectedObservation: 'De uitgegeven query levert een controleerbare bronlink of een exacte providerfout.',
      stopRule: 'Open hoogstens één providerrequest voor deze exploration-revisie.',
      maxActions: 4,
      topicKey: `bounded_provider_${suffix}`,
      topicAnchors: [`event:bounded-provider-${suffix}`, `constraint:one-request-${suffix}`],
      source: 'self',
    });
    assert.equal(created.ok, true, JSON.stringify(created));
    kvSet(KEY, { next_run_ts: new Date(Date.now() - 1_000).toISOString(),
      pending_signals: [], daily_used_ms: 0, daily_productive_ms: 0,
      daily_preempted_ms: 0 });
    return created.exploration;
  };
  const researchExecutionKey = (exploration) => createHash('sha256').update(canonicalJson({
    contract: 'SelfAttentionProviderExecution/v1',
    explorationId: exploration.id,
    explorationRevision: exploration.revision,
    capabilityId: 'internet.research', operationId: 'search',
  })).digest('hex');
  const forceSchedulerDue = () => {
    const stored = JSON.parse(db.prepare('SELECT value FROM kv WHERE key=?').get(KEY).value);
    kvSet(KEY, { ...stored, next_run_ts: new Date(Date.now() - 1_000).toISOString() });
  };
  let previousTimer = null;
  const dispatchedSelections = [];
  const trigger = async (previousRun = null) => {
    const timer = timers.find((item) => item.scheduler && !item.cancelled && !item.fired);
    assert.ok(timer, 'de publieke scheduler moet een timer bewapenen');
    assert.notEqual(timer, previousTimer, 'iedere beurt moet de opnieuw bewapende timer gebruiken');
    timer.fired = true;
    previousTimer = timer;
    timer.callback(...timer.args);
    dispatchedSelections.push({
      phase,
      selection: structuredClone(autonomyStatus().current_selection),
    });
    const deadline = Date.now() + 10_000;
    while ((!autonomyStatus().last_run_ts
      || autonomyStatus().last_run_ts === previousRun) && Date.now() < deadline) {
      await new Promise((resolve) => wait(resolve, 10));
    }
    await new Promise((resolve) => wait(resolve, 0));
    assert.notEqual(autonomyStatus().last_run_ts, previousRun);
    return agentStatus().last_autonomy_turn;
  };

  const artifactContent = '# Gezinsbijdrage\n\nEen klein, lokaal en exact teruggelezen resultaat.\n';
  let phase = 'invalid', invalidCalls = 0, providerBlockedCalls = 0;
  let iterativeBlockedCalls = 0, readFailureCalls = 0, artifactUnderstandingCalls = 0;
  let artifactExecutionCalls = 0, validCalls = 0, concurrentCalls = 0;
  let noActionCalls = 0, nextAspirationCalls = 0;
  let priorityExplorationCalls = 0, priorityWorkCalls = 0;
  let partialPreserveCalls = 0, partialR2FailureCalls = 0;
  let freeRecoveryCalls = 0, freeHomeStateReads = 0, freeHomeLogbookReads = 0;
  let aspirationResearchCalls = 0, explorationResearchUnderstandingCalls = 0;
  let explorationResearchProviderCalls = 0, preemptInteractivePromise = null;
  let aspirationAnchorUnderstandingCalls = 0, aspirationAnchorHomeReads = 0;
  let terminalUnderstandingCalls = 0, terminalPreemptPromise = null;
  let aspirationAnchorRaceSnapshot = null;
  const explorationResearchRequests = [];
  // De live turn #122 bewaarde de exacte `vraag` niet. Deze synthetische query
  // is representatief voor dezelfde capabilityroute, nooit exact replaybewijs.
  let representativeResearchQuery = '', revisionRaceExplorationId = null;
  let nonProviderCalls = 0;
  const artifactBodies = [], artifactWireBodies = [];
  const observedSelections = [];
  globalThis.fetch = async (url, options = {}) => {
    const target = String(url);
    if (phase.startsWith('aspiration-anchor-') && target.endsWith('/api/states')) {
      aspirationAnchorHomeReads += 1;
      if (phase === 'aspiration-anchor-race') {
        const current = db.prepare(`SELECT * FROM eva_explorations
          WHERE aspiration_id=949 ORDER BY id DESC LIMIT 1`).get();
        assert.ok(current, 'r1 moet vóór de awaited r2-read duurzaam bestaan');
        const changed = db.prepare(`UPDATE eva_explorations
          SET question=?,updated_ts=?,revision=revision+1
          WHERE id=? AND revision=?`).run(
          'Gelijktijdig gewijzigde explorationvraag tijdens de r2-read.',
          new Date().toISOString(), current.id, current.revision,
        );
        assert.equal(changed.changes, 1);
        aspirationAnchorRaceSnapshot = canonicalJson(db.prepare(
          'SELECT * FROM eva_explorations WHERE id=?',
        ).get(current.id));
      }
      return new Response(JSON.stringify([
        { entity_id: 'sensor.luchtkwaliteit', state: 'goed',
          last_updated: '2026-08-11T12:00:00.000Z',
          attributes: { friendly_name: 'Luchtkwaliteit' } },
      ]), { status: 200 });
    }
    if (phase.startsWith('free-home-recovery')
      && (target.endsWith('/api/states') || target.includes('/api/logbook/'))) {
      if (target.endsWith('/api/states')) {
        freeHomeStateReads += 1;
        return new Response(JSON.stringify([
          { entity_id: 'automation.tuinlicht_beweging', state: 'on',
            last_updated: '2026-08-11T11:20:00.000Z',
            attributes: { friendly_name: 'Tuinlicht beweging automatisering' } },
          { entity_id: 'light.tuinlicht', state: 'on',
            last_updated: '2026-08-11T11:21:00.000Z',
            attributes: { friendly_name: 'Tuinlicht' } },
          { entity_id: 'binary_sensor.tuin_beweging', state: 'off',
            last_updated: '2026-08-11T11:22:00.000Z',
            attributes: { friendly_name: 'Tuin beweging' } },
        ]), { status: 200 });
      }
      if (target.includes('/api/logbook/')) {
        freeHomeLogbookReads += 1;
        const entityId = decodeURIComponent(target.match(/[?&]entity=([^&]+)/u)?.[1] || '');
        const parsedUrl = new URL(target);
        const start = decodeURIComponent(parsedUrl.pathname.split('/api/logbook/')[1] || '');
        const end = parsedUrl.searchParams.get('end_time');
        const when = new Date((Date.parse(start) + Date.parse(end)) / 2).toISOString();
        return new Response(JSON.stringify(phase === 'free-home-recovery'
          && entityId === 'automation.tuinlicht_beweging' ? [{
          when, entity_id: entityId,
          name: 'Tuinlicht beweging automatisering', state: 'on', domain: 'automation',
          message: 'triggered by binary_sensor.tuin_beweging',
        }] : []), { status: 200 });
      }
    }
    if (!phase.startsWith('free-home-recovery')
      && String(url) !== 'https://chatgpt.com/backend-api/codex/responses') {
      nonProviderCalls += 1;
      return new Response('{}', { status: 503 });
    }
    const request = JSON.parse(String(options.body || '{}'));
    observedSelections.push({
      phase,
      selection: structuredClone(autonomyStatus().current_selection),
    });
    if (phase === 'artifact') artifactWireBodies.push(request);
    if (request.tools?.[0]?.name !== 'commit_turn_understanding') {
      if (phase === 'anchor-real-monitoring') {
        return completed([{ type: 'message', content: [{ type: 'output_text',
          text: 'De monitoring is zonder duurzame episode niet gestart.' }] }]);
      }
      if (phase === 'priority-exploration' || phase === 'priority-work') {
        if (phase === 'priority-exploration') priorityExplorationCalls += 1;
        else priorityWorkCalls += 1;
        return new Response('tijdelijke driverstop voor selectieproef', { status: 503 });
      }
      if (phase.startsWith('exploration-research')
        || phase === 'aspiration-start-research') {
        const webSearch = request.tools?.some((tool) => tool?.type === 'web_search');
        if (!webSearch && phase === 'exploration-research-preempt') {
          return completed([{ type: 'message', content: [{ type: 'output_text',
            text: 'Ik heb de onderbreking ontvangen.' }] }]);
        }
        assert.equal(webSearch, true, JSON.stringify(request.tools));
        explorationResearchProviderCalls += 1;
        explorationResearchRequests.push(request);
        assert.equal(request.tool_choice, 'auto');
        assert.equal(request.parallel_tool_calls, false);
        assert.equal(request.input?.[0]?.content?.[0]?.text, representativeResearchQuery);
        if (phase === 'exploration-research-failure-503') {
          return new Response(JSON.stringify({ error: { code: 'temporarily_unavailable' } }), {
            status: 503, headers: { 'content-type': 'application/json' },
          });
        }
        if (phase === 'exploration-research-failure-429') {
          return new Response(JSON.stringify({ error: { code: 'rate_limit_exceeded' } }), {
            status: 429, headers: { 'content-type': 'application/json' },
          });
        }
        if (phase === 'exploration-research-failure-401') {
          return new Response(JSON.stringify({ error: { code: 'invalid_token' } }), {
            status: 401, headers: { 'content-type': 'application/json' },
          });
        }
        if (phase === 'exploration-research-failure-network') {
          throw new Error('fetch failed: injected bounded research transport error');
        }
        if (phase === 'exploration-research-preempt') {
          preemptInteractivePromise = handleMessageDetailed({
            channel: 'web', sender: 'bewoner', text: 'stop heel even',
            messageContext: { syntheticTest: true, queueKey: 'autonomy' },
          });
        }
        if (phase === 'exploration-research-revision-race') {
          const drift = db.prepare(`UPDATE eva_explorations
            SET revision=revision+1,updated_ts=? WHERE id=?`).run(
            new Date().toISOString(), revisionRaceExplorationId,
          );
          assert.equal(drift.changes, 1);
        }
        return completed([
          { type: 'web_search_call', id: `search-${phase}`, status: 'completed',
            action: { type: 'search', query: 'kleine legale inkomstenkans Nederland' } },
          { type: 'message', content: [{ type: 'output_text',
            text: 'Een actuele primaire bron beschrijft een begrensde kans; de toepasbaarheid voor dit huishouden moet nog worden getoetst. https://example.nl/primair' }] },
        ]);
      }
      assert.equal(phase, 'artifact', `onverwachte providerfase: ${phase}`);
      artifactExecutionCalls += 1;
      artifactBodies.push(request);
      assert.equal(Object.hasOwn(request, 'max_output_tokens'), false);
      assert.equal(request.tools.length, 1);
      const offered = request.tools[0];
      const pad = offered.parameters.properties.pad.enum?.[0]
        || path.join(process.env.EVA_DATA_DIR, 'workspace', 'resultaat', 'gezinsbijdrage.md');
      if (offered.name === 'execution_proof_write') {
        return completed([{ type: 'function_call', call_id: 'artifact-write',
          name: offered.name, arguments: JSON.stringify({ pad, inhoud: artifactContent }) }]);
      }
      assert.equal(offered.name, 'execution_proof_readback');
      return completed([{ type: 'function_call', call_id: 'artifact-readback',
        name: offered.name, arguments: JSON.stringify({ pad }) }]);
    }
    const payload = JSON.parse(request.input[0].content[0].text);
    assert.equal(Object.hasOwn(request, 'max_output_tokens'), false,
      'de OAuth-wirevorm draagt geen onondersteund outputtokenveld');
    if (payload.context.selfAttention?.kind === 'exploration') {
      assert.match(request.instructions, /al hostgebonden exploration/u);
    }
    if (phase.startsWith('exploration-research')) {
      if (payload.turn.origin === 'user') {
        const userSpan = { start: 0, end: payload.turn.text.length };
        return completed([{ type: 'function_call', call_id: 'preempting-interactive-reply',
          name: 'commit_turn_understanding', arguments: JSON.stringify({
            dialogueAct: 'inform', goal: { statement: 'Onderbreek de zelfstandige beurt.',
              outcome: 'De onderbreking is verwerkt.', beneficiary: 'requester' },
            conditions: [], provenance: { sourceSpans: [userSpan], contextHashes: [] },
            confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
            temporal: { mode: 'now', notBefore: null, dueBy: null, condition: null,
              startSourceSpans: [] }, durability: 'turn',
            delivery: { mode: 'none', target: 'none' }, proofNeeds: [], ambiguities: [],
            firstDecision: { kind: 'reply', requirementIds: [],
              replyDraft: 'Ik stop de zelfstandige beurt.' },
          }) }]);
      }
      assert.equal(payload.context.selfAttention.kind, 'exploration');
    } else if (payload.context.selfAttention?.kind === 'aspiration'
      && phase !== 'priority-work' && !phase.startsWith('free-home-recovery')) {
      assert.match(request.instructions,
        /nieuwe duurzame explorationcyclus/u);
    }
    if (phase === 'anchor-real-monitoring') {
      assert.equal(payload.turn.origin, 'user');
      const span = { start: 0, end: payload.turn.text.length };
      const start = aspirationAnchorInput(9, 'monitoring');
      return completed([{ type: 'function_call', call_id: 'real-monitoring-cannot-use-anchor',
        name: 'commit_turn_understanding', arguments: JSON.stringify({
          dialogueAct: 'command', goal: {
            statement: 'Volg de luchtkwaliteit twee uur.',
            outcome: 'Een verandering wordt later gemeld.', beneficiary: 'requester',
          },
          conditions: [], provenance: { sourceSpans: [span], contextHashes: [] },
          confidence: 0.99,
          actions: [
            action('exploration.manage', 'start', start, span),
            action('home.entity.read', 'read', {
              entityId: 'sensor.luchtkwaliteit',
            }, span),
          ],
          requestedEffects: ['persistent', 'read'], deniedEffects: [],
          temporal: { mode: 'now', startSourceSpans: [] }, durability: 'episode',
          delivery: { mode: 'later', target: 'requester' }, proofNeeds: [], ambiguities: [],
          firstDecision: { kind: 'schedule', requirementIds: ['r1', 'r2'], replyDraft: null },
        }) }]);
    }
    if (phase === 'invalid') {
      invalidCalls += 1;
      return completed([{ type: 'function_call', call_id: 'live-invalid-read-work',
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('home.context.read', 'read', { query: 'actuele gezinscontext' }, span),
          action('work.episode.manage', 'start', workInput(), span),
        ])) }]);
    }
    if (phase === 'concurrent') {
      concurrentCalls += 1;
      const attentionId = payload.context.selfAttention.id;
      db.prepare(`UPDATE aspirations SET text=?,progress=? WHERE id=?`).run(
        'Een gelijktijdig bijgestelde aspiratierichting.',
        'Gelijktijdige beheerder wijzigde de inhoud; scheduler-CAS mag niets aanraken.',
        attentionId,
      );
      return completed([{ type: 'function_call', call_id: 'concurrent-invalid-read-work',
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('home.context.read', 'read', { query: 'actuele gezinscontext' }, span),
          action('work.episode.manage', 'start', workInput(), span),
        ])) }]);
    }
    if (phase === 'no-action') {
      noActionCalls += 1;
      const sentence = 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.';
      const start = payload.turn.text.indexOf(sentence);
      const sourceSpans = [{ start, end: start + sentence.length }];
      return completed([{ type: 'function_call', call_id: 'valid-conscious-no-action',
        name: 'commit_turn_understanding', arguments: JSON.stringify({
          dialogueAct: 'inform', goal: { statement: 'Sla deze richting nu bewust over.',
            outcome: 'De aandacht is zonder onbewezen effect afgerond.', beneficiary: 'self' },
          conditions: [], provenance: { sourceSpans, contextHashes: [] }, confidence: 0.99,
          actions: [], requestedEffects: [], deniedEffects: [],
          temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
          delivery: { mode: 'silent', target: 'none' }, proofNeeds: [], ambiguities: [],
          firstDecision: { kind: 'silent', requirementIds: [], replyDraft: null },
        }) }]);
    }
    if (phase === 'next-aspiration') {
      nextAspirationCalls += 1;
      return completed([{ type: 'function_call', call_id: 'next-aspiration-invalid-read-work',
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('home.context.read', 'read', { query: 'actuele gezinscontext' }, span),
          action('work.episode.manage', 'start', workInput(), span),
        ])) }]);
    }
    if (phase === 'priority-exploration' || phase === 'priority-work') {
      if (phase === 'priority-exploration') priorityExplorationCalls += 1;
      else priorityWorkCalls += 1;
      return completed([{ type: 'function_call', call_id: `${phase}-invalid-read-work`,
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('home.context.read', 'read', { query: 'actuele gezinscontext' }, span),
          action('work.episode.manage', 'start', workInput(), span),
        ])) }]);
    }
    if (phase === 'provider') {
      providerBlockedCalls += 1;
      return completed([{ type: 'function_call', call_id: 'blocked-provider-plan',
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('research.workcell.manage', 'hervat', { id: 42 }, span),
        ], { durability: 'turn', decision: 'act', requestedEffects: ['persistent'] })) }]);
    }
    if (phase === 'aspiration-research') {
      aspirationResearchCalls += 1;
      return completed([{ type: 'function_call', call_id: 'aspiration-provider-read',
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('internet.research', 'search', {
            vraag: representativeResearchQuery,
          }, span),
        ], { durability: 'turn', decision: 'inspect', requestedEffects: ['read'] })) }]);
    }
    if (phase === 'aspiration-start-research') {
      aspirationResearchCalls += 1;
      const attentionId = payload.context.selfAttention.id;
      return completed([{ type: 'function_call', call_id: 'aspiration-start-provider-read',
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('exploration.manage', 'start',
            aspirationAnchorInput(attentionId, 'provider'), span),
          action('internet.research', 'search', {
            vraag: representativeResearchQuery,
          }, span),
        ], {
          durability: 'episode', decision: 'inspect',
          requestedEffects: ['persistent', 'read'], requirementIds: ['r1', 'r2'],
        })) }]);
    }
    if (phase.startsWith('aspiration-anchor-') || phase === 'anchor-bound') {
      aspirationAnchorUnderstandingCalls += 1;
      const attentionId = payload.context.selfAttention.id;
      const suffix = phase.replace(/^(?:aspiration-anchor-|anchor-)/u, '');
      const start = aspirationAnchorInput(attentionId, suffix);
      const read = action('home.context.read', 'read', {
        query: `actuele context voor ${suffix}`,
      }, null);
      return completed([{ type: 'function_call', call_id: `${phase}-understanding`,
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => {
          const anchored = action('exploration.manage', 'start',
            phase === 'aspiration-anchor-wrong'
              ? { ...start, aspiratie_id: attentionId + 1 } : start, span);
          const currentRead = { ...read, sourceSpans: [span] };
          if (phase === 'aspiration-anchor-reverse') return [currentRead, anchored];
          if (phase === 'aspiration-anchor-extra') return [anchored, currentRead,
            action('home.context.read', 'read', { query: 'tweede overtollige context' }, span)];
          if (phase === 'aspiration-anchor-unresolved') return [anchored,
            action('workspace.file.read', 'read', { pad: '<resultaat>' }, span)];
          return [anchored, currentRead];
        }, {
          durability: 'episode', decision: 'act',
          requestedEffects: ['persistent', 'read'],
          deniedEffects: phase === 'aspiration-anchor-success'
            ? ['external', 'voice'] : [],
          requirementIds: phase === 'aspiration-anchor-extra'
            ? ['r1', 'r2', 'r3'] : ['r1', 'r2'],
          conditions: phase === 'aspiration-anchor-dependency' ? [{
            statement: 'Voer de read pas na het duurzame anker uit.', role: 'action',
            state: 'required', sourceSpans: [{ start: 0, end: payload.turn.text.length }],
            requirementIds: ['r2'], afterRequirementIds: ['r1'],
          }] : [],
          temporal: phase === 'aspiration-anchor-later' ? {
            mode: 'later', notBefore: new Date(Date.now() + 60_000).toISOString(),
            dueBy: null, condition: null,
            startSourceSpans: [{ start: 0, end: payload.turn.text.length }],
          } : null,
        })) }]);
    }
    if (phase.startsWith('exploration-terminal-')) {
      terminalUnderstandingCalls += 1;
      if (payload.turn.origin === 'user') {
        const userSpan = { start: 0, end: payload.turn.text.length };
        return completed([{ type: 'function_call', call_id: 'terminal-preempt-reply',
          name: 'commit_turn_understanding', arguments: JSON.stringify({
            dialogueAct: 'inform', goal: { statement: 'Onderbreek deze zelfstandige beurt.',
              outcome: 'De onderbreking is verwerkt.', beneficiary: 'requester' },
            conditions: [], provenance: { sourceSpans: [userSpan], contextHashes: [] },
            confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
            temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
            delivery: { mode: 'none', target: 'none' }, proofNeeds: [], ambiguities: [],
            firstDecision: { kind: 'reply', requirementIds: [],
              replyDraft: 'Ik laat deze zelfstandige afronding nu los.' },
          }) }]);
      }
      const exploration = payload.context.selfAttention.exploration;
      const operation = phase === 'exploration-terminal-stop' ? 'stoppen' : 'afronden';
      const semanticInput = {
        id: exploration.id,
        verwachte_revisie: phase === 'exploration-terminal-stale'
          ? exploration.revision + 1 : exploration.revision,
        resultaat: operation === 'stoppen'
          ? 'De cyclus stopt bewust zonder een productieve eindbijdrage.'
          : 'De actuele huiswaarneming levert een begrensde, bruikbare conclusie op.',
        besluit: operation === 'stoppen'
          ? 'Deze verlaten cyclus krijgt geen bijdrage.'
          : 'Gebruik de waarneming alleen zolang een verse status haar blijft bevestigen.',
        ...(phase === 'exploration-terminal-no-evidence'
          ? { bewijs: 'Deze terminale argumenttekst mag nooit als eerder toolbewijs tellen.' }
          : {}),
        ...(phase === 'exploration-terminal-economic-handoff'
          ? { economisch_vervolg: {
            titel: 'Een eigen vreemde vondst duurzaam uitwerken',
            uitkomst: 'Een zelfgekozen kans is met intern bewijs toetsbaar uitgewerkt.',
            definitie_klaar: 'De uitkomst en grens zijn intern teruggelezen en controleerbaar.',
            volgende_stap: 'Werk eerst één interne bewijsstap uit zonder extern contact.',
          } } : {}),
        ...(phase === 'exploration-terminal-behavior-trial'
          ? { gedragsproef: {
            doelpersoon: 'Jaap',
            waargenomen_kloof: 'Mijn antwoorden voelen soms te glad en te weinig situationeel.',
            hypothese: 'Een vrijer eigen oordeel maakt één antwoord natuurlijker zonder het bewijs te veranderen.',
            motivatie: 'Ik wil zelf toetsen of mijn eigen afweging natuurlijker mag meeklinken.',
            dimensie: 'independent_voice',
            richting: 'increase',
            intensiteit: 'moderate',
            contextscope: 'any_direct_reply',
          } } : {}),
      };
      const response = completed([{ type: 'function_call', call_id: `${phase}-understanding`,
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('exploration.manage', operation, semanticInput, span),
        ], { durability: 'turn', decision: 'act', requestedEffects: ['persistent'],
          deniedEffects: ['external', 'voice'] })) }]);
      if (phase === 'exploration-terminal-race') {
        const changed = db.prepare(`UPDATE eva_explorations
          SET question=?,updated_ts=?,revision=revision+1 WHERE id=? AND revision=?`).run(
          'Gelijktijdig gewijzigde terminalvraag vóór de exploration-CAS.',
          new Date().toISOString(), exploration.id, exploration.revision,
        );
        assert.equal(changed.changes, 1);
      }
      if (phase === 'exploration-terminal-preempt') {
        terminalPreemptPromise = handleMessageDetailed({
          channel: 'web', sender: 'bewoner', text: 'stop de afronding nu',
          messageContext: { syntheticTest: true, queueKey: 'autonomy' },
        });
      }
      return response;
    }
    if (phase.startsWith('exploration-research')) {
      explorationResearchUnderstandingCalls += 1;
      if (phase === 'exploration-research-multi') {
        return completed([{ type: 'function_call', call_id: 'multi-issued-provider-read',
          name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
            action('internet.research', 'search', { vraag: representativeResearchQuery }, span),
            action('home.context.read', 'read', { query: 'actuele gezinscontext' }, span),
          ], { durability: 'turn', decision: 'inspect', requestedEffects: ['read'],
            requirementIds: ['r1', 'r2'] })) }]);
      }
      return completed([{ type: 'function_call', call_id: `${phase}-issued-provider-read`,
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('internet.research', 'search', {
            vraag: representativeResearchQuery,
          }, span),
        ], { durability: 'turn', decision: 'inspect', requestedEffects: ['read'] })) }]);
    }
    if (phase === 'iterative') {
      iterativeBlockedCalls += 1;
      return completed([{ type: 'function_call', call_id: 'blocked-executable-plan',
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('workspace.file.write', 'write', { pad: 'resultaat/onveilig.mjs' }, span),
        ], { durability: 'turn', decision: 'act', requestedEffects: ['workspace'] })) }]);
    }
    if (phase === 'read-failure') {
      readFailureCalls += 1;
      const attentionId = payload.context.selfAttention.id;
      return completed([{ type: 'function_call', call_id: 'read-failure-before-artifact',
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('exploration.manage', 'start', {
            vraag: 'Draagt de ontbrekende bron werkelijk een klein werkstuk?',
            aanleiding: 'Alleen het echte bronbestand mag een vervolg dragen.',
            hypothese: 'De bron bestaat en bevat bruikbaar bewijs.',
            verwachte_waarneming: 'De exacte read levert concrete inhoud op.',
            stopregel: 'Stop direct wanneer de bronread mislukt.',
            max_acties: 2, onderwerp_sleutel: 'scheduler_read_failure_artifact',
            onderwerp_ankers: ['artifact:data/workspace/ontbrekende-bron.md',
              `event:aspiration:${attentionId}`], aspiratie_id: attentionId,
          }, span),
          action('workspace.file.read', 'read', {
            pad: 'data/workspace/ontbrekende-bron.md',
          }, span),
          action('workspace.file.write', 'write', {
            pad: 'resultaat/mag-niet-ontstaan.md',
          }, span),
        ], { durability: 'turn', decision: 'act',
          requestedEffects: ['persistent', 'read', 'workspace'] })) }]);
    }
    if (phase === 'artifact') {
      artifactUnderstandingCalls += 1;
      return completed([{ type: 'function_call', call_id: 'bounded-artifact-plan',
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('workspace.file.write', 'write', { pad: 'resultaat/gezinsbijdrage.md' }, span),
        ], { durability: 'turn', decision: 'act', requestedEffects: ['workspace'] })) }]);
    }
    if (phase === 'partial-preserve' || phase === 'partial-r2-fail') {
      if (phase === 'partial-preserve') partialPreserveCalls += 1;
      else partialR2FailureCalls += 1;
      const topicKey = phase === 'partial-preserve'
        ? 'scheduler_partial_read_visible_contribution'
        : 'scheduler_partial_read_r2_conflict';
      const attentionId = payload.context.selfAttention.id;
      return completed([{ type: 'function_call', call_id: `${phase}-issued-read-starts`,
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('home.context.read', 'read', {
            query: 'actuele kleine besparing', history: true,
          }, span),
          action('exploration.manage', 'start', {
            vraag: 'Welke kleine gezinsbijdrage verdient een concrete vervolgcyclus?',
            aanleiding: 'De aspiratie vraagt nu om één zichtbare, begrensde bijdrage.',
            hypothese: 'Een korte vervolgcyclus kan deze bijdrage toetsbaar maken.',
            verwachte_waarneming: 'Een duurzame cyclus bewaart de concrete vervolgstap.',
            stopregel: 'Stop zodra één toetsbare vervolgstap duurzaam is vastgelegd.',
            max_acties: 2, onderwerp_sleutel: topicKey,
            onderwerp_ankers: [`event:aspiration:${attentionId}`, 'constraint:kleine-bijdrage'],
            aspiratie_id: attentionId,
          }, span),
          action('work.episode.manage', 'start', workInput(), span),
        ], { requirementIds: ['r1', 'r2', 'r3'] })) }]);
    }
    if (phase.startsWith('free-home-recovery')) {
      freeRecoveryCalls += 1;
      return completed([{ type: 'function_call', call_id: 'free-home-context-history-read',
        name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
          action('home.context.read', 'read', {
            query: 'tuinlicht recente oorzaak', history: true,
          }, span),
        ], { durability: 'turn', decision: 'inspect', requestedEffects: ['read'] })) }]);
    }
    validCalls += 1;
    const attentionId = payload.context.selfAttention.id;
    return completed([{ type: 'function_call', call_id: 'valid-read-exploration-work',
      name: 'commit_turn_understanding', arguments: JSON.stringify(raw(payload, (span) => [
        action('workspace.file.read', 'read', { pad: 'data/workspace/bron.md' }, span),
        action('exploration.manage', 'start', {
          vraag: 'Draagt dit bronfeit één concrete gezinsbijdrage?',
          aanleiding: 'De eigen aspiratie vraagt nu om aantoonbare gezinswaarde.',
          hypothese: 'Het lokale bronfeit draagt één uitvoerbare vervolgstap.',
          verwachte_waarneming: 'De read bevestigt één bruikbaar feit.',
          stopregel: 'Stop na deze read als het feit geen concrete stap draagt.',
          max_acties: 2, onderwerp_sleutel: 'scheduler_gezinsbijdrage_bron',
          onderwerp_ankers: ['artifact:data/workspace/bron.md', `event:aspiration:${attentionId}`],
          aspiratie_id: attentionId,
        }, span),
        action('work.episode.manage', 'start', workInput(), span),
      ])) }]);
  };

  resetAttention(910);
  let providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  const beforeWork = db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count;
  startAutonomy();
  const invalidTurn = await trigger();
  assert.equal(invalidCalls, 1, 'een ongeldige geattesteerde payload krijgt geen tweede poging');
  assert.deepEqual(invalidTurn.tools, []);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM timeline
    WHERE kind='action' AND json_extract(meta,'$.turnId')=?`).get(invalidTurn.turnId).count, 0);
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count, beforeWork);
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_explorations').get().count, 0);
  assert.equal(nonProviderCalls, 0, 'de afgewezen home-read mag geen I/O bereiken');
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 1,
    'ook de observeerbare wire-fetchdelta bewijst exact één directe providerroute');
  assert.deepEqual(observedSelections.find((item) => item.phase === 'invalid')?.selection, {
    attention: { kind: 'aspiration', id: 910 },
    work_id: null, goal_episode_id: null, signal_batch_id: null, signal_count: 0,
  });
  const invalidAspiration = db.prepare('SELECT * FROM aspirations WHERE id=910').get();
  assert.equal(invalidAspiration.last_touched, '2020-01-01T00:00:00.000Z',
    'een technisch/semantisch ongeldige beurt mag de eigen aspiratie niet als bekeken afboeken');
  assert.equal(invalidAspiration.progress, '',
    'scheduler-settlement schrijft nooit inhoudelijke voortgang');
  assert.deepEqual(dueAttention(), {
    kind: 'aspiration', id: 910, text: 'Maak één meetbare gezinsbijdrage.',
  }, 'de inhoudelijke attention blijft due; alleen de globale scheduler krijgt een korte technische retry');

  db.prepare('DELETE FROM aspirations').run();
  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare("UPDATE eva_work_items SET status='cancelled',lease_owner=NULL,lease_until=NULL").run();
  db.prepare("UPDATE eva_goal_episodes SET state='abandoned',lease_owner=NULL,lease_until=NULL").run();
  db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  kvSet(KEY, { next_run_ts: new Date(Date.now() - 1_000).toISOString(),
    pending_signals: [], daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0 });
  phase = 'free-home-recovery';
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  let previousRun = autonomyStatus().last_run_ts;
  const freeWorkBefore = db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count;
  const freeRecoveryTurn = await trigger(previousRun);
  assert.equal(freeRecoveryCalls, 1,
    `de hele vrije recovery gebruikt alleen de ene modelinterpretatie: ${JSON.stringify({
      turn: freeRecoveryTurn, llm: llmRuntimeStatus(), selection: dispatchedSelections.at(-1),
    })}`);
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 1,
    'de hostgebonden logboekchild opent geen tweede providerroute');
  assert.deepEqual(dispatchedSelections.find(
    (item) => item.phase === 'free-home-recovery',
  )?.selection, {
    attention: null, work_id: null, goal_episode_id: null,
    signal_batch_id: null, signal_count: 0,
  }, 'de proef loopt door de echte private schedulercontext voor ongebonden vrije aandacht');
  assert.equal(freeHomeStateReads, 1);
  assert.ok(freeHomeLogbookReads >= 1 && freeHomeLogbookReads < 24);
  assert.deepEqual(freeRecoveryTurn.tools.map((tool) => tool.name),
    ['huis_status', 'huis_status'], JSON.stringify(freeRecoveryTurn));
  const freeActionRows = db.prepare(`SELECT meta FROM timeline WHERE kind='action'
    AND json_extract(meta,'$.turnId')=? ORDER BY id`).all(freeRecoveryTurn.turnId)
    .map((row) => JSON.parse(row.meta));
  assert.deepEqual(freeActionRows.map((row) => row.tool), ['huis_status', 'huis_status']);
  const freeLedger = db.prepare(`SELECT * FROM turn_tool_executions
    WHERE turn_id=? ORDER BY started_ts,rowid`).all(freeRecoveryTurn.turnId)
    .map((row) => validateExecutionRecord(row));
  assert.equal(freeLedger.length, 2, JSON.stringify(freeLedger));
  assert.ok(freeLedger.every((row) => row.status === 'done'
    && row.completion_record_sha256 && row.output_sha256));
  const freeBindings = freeLedger.map((row) => JSON.parse(row.semantic_binding));
  assert.equal(freeBindings[0].understandingSha256, freeBindings[1].understandingSha256);
  assert.deepEqual(freeBindings.map((binding) => binding.requirementId), ['r1', 'r1']);
  assert.deepEqual(freeBindings.map((binding) => binding.role), ['primary', 'recovery']);
  assert.deepEqual(freeBindings.map((binding) => binding.basisReceiptIds), [[], ['s1']]);
  const freeOutputs = freeLedger.map((row) => JSON.parse(row.output));
  assert.deepEqual(freeOutputs.map((output) => [
    output.ok, output.verified, output.partial, output.needs_followup,
  ]), [[true, true, true, true], [true, true, false, false]],
  'de primary receipt blijft partial; alleen de receiptgebonden child is compleet');
  assert.equal(freeRecoveryTurn.actionFailed, undefined);
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    freeWorkBefore, 'de read-only recovery maakt geen werk, exploration of ander duurzaam effect');

  kvSet(KEY, { next_run_ts: new Date(Date.now() - 1_000).toISOString(),
    pending_signals: [], daily_used_ms: 0, daily_productive_ms: 0, daily_preempted_ms: 0 });
  phase = 'free-home-recovery-empty';
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  previousRun = autonomyStatus().last_run_ts;
  const emptyRecoveryTurn = await trigger(previousRun);
  assert.equal(freeRecoveryCalls, 2);
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 1,
    'ook compleet negatief logboekbewijs vraagt geen model om een vervangende read');
  assert.deepEqual(emptyRecoveryTurn.tools.map((tool) => [tool.name, tool.ok]), [
    ['huis_status', true], ['huis_status', true],
  ]);
  assert.equal(emptyRecoveryTurn.actionFailed, undefined);
  assert.equal(emptyRecoveryTurn.contribution ?? null, null);
  assert.equal(emptyRecoveryTurn.reply, '');
  assert.doesNotMatch(emptyRecoveryTurn.noActionReason || '', /triggered by|oorzaak|gebeurde niets/iu,
    'negatief bereikbewijs wordt geen oorzakelijke of zichtbare resultaatclaim');
  const emptyLedger = db.prepare(`SELECT * FROM turn_tool_executions
    WHERE turn_id=? ORDER BY started_ts,rowid`).all(emptyRecoveryTurn.turnId)
    .map((row) => validateExecutionRecord(row));
  assert.equal(emptyLedger.length, 2);
  assert.deepEqual(emptyLedger.map((row) => {
    const output = JSON.parse(row.output);
    return [output.ok, output.verified, output.partial, output.needs_followup];
  }), [[true, true, true, true], [true, true, false, false]]);
  const emptyOutput = JSON.parse(emptyLedger[1].output).data;
  assert.equal(emptyOutput.responses_complete, true);
  assert.equal(emptyOutput.empty, true);
  assert.equal(emptyOutput.request_count, emptyOutput.completion_count);
  assert.equal(emptyOutput.matched_events, 0);
  assert.equal(emptyOutput.returned_events, 0);
  assert.equal(emptyOutput.events_truncated, false);
  assert.deepEqual(emptyOutput.entity_ids, [
    'automation.tuinlicht_beweging', 'light.tuinlicht',
  ]);
  assert.deepEqual(Object.keys(emptyOutput.window).sort(), ['end', 'start']);
  assert.equal(autonomyStatus().last_result.turn_id, emptyRecoveryTurn.turnId);
  assert.equal(autonomyStatus().last_result.productive, false);
  assert.equal(autonomyStatus().last_result.progressed, false);
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    freeWorkBefore, 'compleet negatief leesbewijs maakt geen werk of zichtbaar effect');
  const emptyEpisode = db.prepare(
    'SELECT * FROM cognitive_episodes WHERE correlation_id=?',
  ).get(emptyRecoveryTurn.turnId);
  assert.equal(emptyEpisode.status, 'completed');
  assert.equal(emptyEpisode.result_class, 'no_action');
  assert.equal(JSON.parse(emptyEpisode.evidence_json).contribution, null);
  assert.equal(JSON.parse(emptyEpisode.effect_json).madeProgress, false);

  resetAttention(911);
  phase = 'provider';
  previousRun = autonomyStatus().last_run_ts;
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  const providerActionRowsBefore = db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='action'").get().count;
  const providerExecutionsBefore = db.prepare('SELECT count(*) AS count FROM turn_tool_executions').get().count;
  const providerBlockedTurn = await trigger(previousRun);
  assert.equal(providerBlockedCalls, 1);
  assert.deepEqual(providerBlockedTurn.tools, []);
  const providerRejectedRow = db.prepare(`SELECT meta FROM timeline
    WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?`).get(
    providerBlockedTurn.turnId,
  );
  const providerRejectedMeta = JSON.parse(providerRejectedRow.meta);
  assert.equal(providerRejectedMeta.attemptFailures[0].validation,
    'self_attention_effect_not_allowed');
  assert.deepEqual(providerRejectedMeta.attemptFailures[0].actionAudit.map((item) => ({
    capabilityId: item.capabilityId, operationId: item.operationId,
    effect: item.effect, bindingMatched: item.bindingMatched,
  })), [{ capabilityId: 'research.workcell.manage', operationId: 'hervat',
    effect: 'persistent', bindingMatched: false }]);
  assert.equal(db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='action'").get().count,
    providerActionRowsBefore);
  assert.equal(db.prepare('SELECT count(*) AS count FROM turn_tool_executions').get().count,
    providerExecutionsBefore);
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 1);
  assert.equal(nonProviderCalls, 0, 'een workcell-hervatting mag niet vóór de budgetgrens starten');
  const providerAspiration = db.prepare('SELECT * FROM aspirations WHERE id=911').get();
  assert.equal(providerAspiration.last_touched, '2020-01-01T00:00:00.000Z',
    'ook een vóór planning afgewezen providervorm boekt de aspiratie niet inhoudelijk af');
  assert.equal(providerAspiration.progress, '');

  resetAttention(940);
  representativeResearchQuery = 'Welke actuele primaire bronnen tonen een kleine legale inkomstenkans voor een Nederlands huishouden?';
  phase = 'aspiration-research';
  previousRun = autonomyStatus().last_run_ts;
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  let researchProviderCallsBefore = explorationResearchProviderCalls;
  const aspirationResearchTurn = await trigger(previousRun);
  assert.equal(aspirationResearchCalls, 1);
  assert.equal(explorationResearchProviderCalls, researchProviderCallsBefore,
    'de provideruitzondering geldt niet voor verse aspiration-aandacht');
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 1);
  assert.deepEqual(aspirationResearchTurn.tools, []);
  assert.equal(aspirationResearchTurn.error, 'material_request_uninterpreted',
    'verse aspiration-aandacht krijgt nooit rechtstreeks internet zonder exploration-start');
  assert.equal(db.prepare(`SELECT count(*) AS count FROM turn_tool_executions
    WHERE turn_id=?`).get(aspirationResearchTurn.turnId).count, 0);

  resetAttention(8);
  representativeResearchQuery = 'Welke actuele primaire bron helpt Eva een eigen interesse in muziek, wetenschap, techniek, cultuur of kunst te kiezen?';
  phase = 'aspiration-start-research';
  previousRun = autonomyStatus().last_run_ts;
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  researchProviderCallsBefore = explorationResearchProviderCalls;
  const providerLedgerBefore = db.prepare(
    'SELECT count(*) AS count FROM turn_tool_executions',
  ).get().count;
  const aspirationStartResearchTurn = await trigger(previousRun);
  assert.equal(aspirationResearchCalls, 2,
    'de livevorm gebruikt één geattesteerde understandingcall');
  assert.equal(explorationResearchProviderCalls - researchProviderCallsBefore, 1,
    'na exact één verse exploration-start opent exact één researchrequest');
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 2,
    'de livevorm gebruikt alleen understanding plus de ene gebonden researchrequest');
  assert.deepEqual(aspirationStartResearchTurn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified, tool.changed,
  ]), [
    ['verkenning_bijhouden', true, true, true],
    ['zoek_internet', true, true, false],
  ], JSON.stringify(aspirationStartResearchTurn));
  const aspirationProviderExploration = db.prepare(`SELECT * FROM eva_explorations
    WHERE aspiration_id=8 ORDER BY id DESC LIMIT 1`).get();
  assert.ok(aspirationProviderExploration,
    'de providerread mag alleen achter de verse aspiration-exploration bestaan');
  assert.equal(aspirationProviderExploration.action_count, 1);
  assert.match(aspirationProviderExploration.evidence_json, /zoek_internet/u);
  const aspirationProviderLedger = db.prepare(`SELECT * FROM turn_tool_executions
    WHERE turn_id=? ORDER BY started_ts,rowid`).all(aspirationStartResearchTurn.turnId)
    .map((row) => validateExecutionRecord(row));
  assert.equal(aspirationProviderLedger.length, 1);
  assert.equal(aspirationProviderLedger[0].tool, 'zoek_internet');
  assert.deepEqual((() => {
    const binding = JSON.parse(aspirationProviderLedger[0].semantic_binding);
    return [binding.requirementId, binding.capabilityId, binding.operationId,
      binding.role, binding.basisReceiptIds];
  })(), ['r2', 'internet.research', 'search', 'primary', []]);
  assert.equal(db.prepare('SELECT count(*) AS count FROM turn_tool_executions').get().count,
    providerLedgerBefore + 1,
  'exploration-start is lokaal; alleen de ene providerread krijgt een ledgerclaim');
  if (process.env.EVA_TEST_STOP_AFTER_ASPIRATION_PROVIDER === '1') {
    db.close();
    return;
  }

  // Dit raakt de authentieke publieke scheduler-/receipt-/catalogusroute voor
  // aspiratie #9. De modelpayload is production-shaped; niet-gearchiveerde
  // ruwe livevelden zoals delivery en conditions worden niet als replay geclaimd.
  resetAttention(9);
  phase = 'aspiration-anchor-success';
  previousRun = autonomyStatus().last_run_ts;
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  const anchorHomeReadsBefore = aspirationAnchorHomeReads;
  const anchorWorkBefore = db.prepare(
    'SELECT count(*) AS count FROM eva_work_items',
  ).get().count;
  const anchorEpisodesBefore = db.prepare(
    'SELECT count(*) AS count FROM eva_goal_episodes',
  ).get().count;
  const anchorTurn = await trigger(previousRun);
  const anchorTurnMeta = JSON.parse(db.prepare(`SELECT meta FROM timeline
    WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?`).get(
    anchorTurn.turnId,
  ).meta);
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 1,
    'authentieke aspiratie #9 gebruikt alleen de ene understanding-providercall');
  assert.equal(aspirationAnchorHomeReads - anchorHomeReadsBefore, 1,
    `r2 is exact één echte lokale catalog-read: ${JSON.stringify({
      turn: anchorTurn, attemptFailures: anchorTurnMeta.attemptFailures,
    })}`);
  assert.deepEqual(dispatchedSelections.find(
    (item) => item.phase === 'aspiration-anchor-success',
  )?.selection, {
    attention: { kind: 'aspiration', id: 9 },
    work_id: null, goal_episode_id: null, signal_batch_id: null, signal_count: 0,
  });
  assert.deepEqual(anchorTurn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified, tool.changed,
  ]), [
    ['verkenning_bijhouden', true, true, true],
    ['huis_status', true, true, false],
  ], JSON.stringify(anchorTurn));
  const anchoredExploration = db.prepare(`SELECT * FROM eva_explorations
    WHERE aspiration_id=9 ORDER BY id DESC LIMIT 1`).get();
  assert.ok(anchoredExploration,
    'de begrensde publieke toolprojectie wordt niet als interne startreceipt geparseerd');
  const anchorLedger = db.prepare(`SELECT * FROM turn_tool_executions
    WHERE turn_id=? ORDER BY started_ts,rowid`).all(anchorTurn.turnId)
    .map((row) => validateExecutionRecord(row));
  assert.equal(anchorLedger.length, 1, JSON.stringify(anchorLedger));
  assert.equal(anchorLedger[0].tool, 'huis_status',
    'alleen de bestaande conversation-receipt van de projectable read wordt duurzaam bewaard');
  assert.equal(anchorLedger[0].status, 'done');
  assert.ok(anchorLedger[0].completion_record_sha256 && anchorLedger[0].output_sha256);
  const anchorReadBinding = JSON.parse(anchorLedger[0].semantic_binding);
  assert.equal(anchorReadBinding.understandingSha256,
    anchorTurnMeta.understanding.sourceSha256);
  assert.deepEqual({
    requirementId: anchorReadBinding.requirementId,
    capabilityId: anchorReadBinding.capabilityId,
    operationId: anchorReadBinding.operationId,
    role: anchorReadBinding.role,
    addressesRequirementIds: anchorReadBinding.addressesRequirementIds,
    basisReceiptIds: anchorReadBinding.basisReceiptIds,
  }, {
    requirementId: 'r2', capabilityId: 'home.context.read', operationId: 'read',
    role: 'primary', addressesRequirementIds: ['r2'], basisReceiptIds: [],
  });
  const anchorActions = db.prepare(`SELECT meta FROM timeline WHERE kind='action'
    AND json_extract(meta,'$.turnId')=? ORDER BY id`).all(anchorTurn.turnId)
    .map((row) => JSON.parse(row.meta));
  assert.deepEqual(anchorActions.map((row) => {
    const binding = row.semanticBinding;
    return [row.tool, binding.requirementId, binding.capabilityId, binding.operationId];
  }), [
    ['verkenning_bijhouden', 'r1', 'exploration.manage', 'start'],
    ['huis_status', 'r2', 'home.context.read', 'read'],
  ]);
  assert.deepEqual(anchorTurnMeta.understanding.deniedEffects,
    ['external', 'voice'],
  'monotone ontkenningen buiten persistent/read blokkeren het private anker niet');
  assert.equal(anchoredExploration.aspiration_id, 9);
  assert.equal(anchoredExploration.source, 'self');
  assert.equal(anchoredExploration.action_count, 1,
    'r2 wordt pas na de verse r1-binding aan exact die exploration toegeschreven');
  assert.match(anchoredExploration.evidence_json, /huis_status/u);
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    anchorWorkBefore, 'het explorationanker maakt geen work-item');
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_goal_episodes').get().count,
    anchorEpisodesBefore, 'het explorationanker maakt geen monitoring-/workepisode');
  assert.equal(anchorTurn.contribution ?? null, null);

  resetAttention(949);
  phase = 'aspiration-anchor-race';
  previousRun = autonomyStatus().last_run_ts;
  const raceHomeReadsBefore = aspirationAnchorHomeReads;
  const raceTurn = await trigger(previousRun);
  assert.deepEqual(raceTurn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified, tool.changed,
  ]), [
    ['verkenning_bijhouden', true, true, true],
    ['huis_status', true, true, false],
  ], JSON.stringify(raceTurn));
  assert.equal(aspirationAnchorHomeReads - raceHomeReadsBefore, 1);
  assert.ok(aspirationAnchorRaceSnapshot,
    'de adversarial moet de exploration tijdens de awaited r2-read wijzigen');
  const racedExploration = db.prepare(`SELECT * FROM eva_explorations
    WHERE aspiration_id=949 ORDER BY id DESC LIMIT 1`).get();
  assert.equal(canonicalJson(racedExploration), aspirationAnchorRaceSnapshot,
    'CAS-miss bewaart de gelijktijdig gewijzigde explorationrij bytegelijk');
  assert.equal(racedExploration.action_count, 0);
  assert.equal(racedExploration.evidence_json, '[]');
  const raceLedger = db.prepare(`SELECT * FROM turn_tool_executions
    WHERE turn_id=? ORDER BY started_ts,rowid`).all(raceTurn.turnId)
    .map((row) => validateExecutionRecord(row));
  assert.equal(raceLedger.length, 1, JSON.stringify(raceLedger));
  assert.equal(raceLedger[0].tool, 'huis_status');
  assert.equal(raceLedger[0].status, 'done',
    'de voltooide r2-readreceipt blijft ondanks de exploration-CAS-miss bewaard');
  assert.ok(raceLedger[0].completion_record_sha256 && raceLedger[0].output_sha256);
  assert.equal(raceTurn.contribution ?? null, null);

  const rejectedAnchorCases = [
    ['reverse', 941], ['extra', 942], ['wrong', 943],
    ['dependency', 944], ['unresolved', 945], ['later', 946],
  ];
  for (const [failure, attentionId] of rejectedAnchorCases) {
    resetAttention(attentionId);
    phase = `aspiration-anchor-${failure}`;
    previousRun = autonomyStatus().last_run_ts;
    const actionsBefore = db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='action'")
      .get().count;
    const explorationCountBefore = db.prepare(
      'SELECT count(*) AS count FROM eva_explorations',
    ).get().count;
    const homeReadsBefore = aspirationAnchorHomeReads;
    const rejected = await trigger(previousRun);
    assert.deepEqual(rejected.tools, [], `${failure}: ${JSON.stringify(rejected)}`);
    assert.equal(aspirationAnchorHomeReads, homeReadsBefore, `${failure} bereikt r2 niet`);
    assert.equal(db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='action'")
      .get().count, actionsBefore, `${failure} publiceert geen action`);
    assert.equal(db.prepare('SELECT count(*) AS count FROM eva_explorations').get().count,
      explorationCountBefore, `${failure} materialiseert geen exploration`);
    const rejectedUnderstanding = db.prepare(`SELECT meta FROM timeline
      WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?`).get(
      rejected.turnId,
    );
    const rejectedMeta = JSON.parse(rejectedUnderstanding.meta);
    assert.ok(rejectedMeta.attemptFailures?.[0]?.validation,
      `${failure} moet vóór planning fail-closed valideren`);
  }

  resetAttention(947);
  const duplicateInput = aspirationAnchorInput(947, 'nochange');
  const duplicateExploration = startExploration({
    question: duplicateInput.vraag, whyNow: duplicateInput.aanleiding,
    hypothesis: duplicateInput.hypothese,
    expectedObservation: duplicateInput.verwachte_waarneming,
    stopRule: duplicateInput.stopregel, maxActions: duplicateInput.max_acties,
    topicKey: duplicateInput.onderwerp_sleutel,
    topicAnchors: duplicateInput.onderwerp_ankers, source: 'self',
  }).exploration;
  assert.equal(db.prepare(`UPDATE eva_explorations SET scheduler_deferred_ts=?
    WHERE id=?`).run(new Date().toISOString(), duplicateExploration.id).changes, 1,
  'de actieve duplicate blijft vindbaar voor start, maar vervangt de verse aspirationselectie niet');
  phase = 'aspiration-anchor-nochange';
  previousRun = autonomyStatus().last_run_ts;
  const nochangeHomeReadsBefore = aspirationAnchorHomeReads;
  const nochangeTurn = await trigger(previousRun);
  assert.deepEqual(nochangeTurn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified, tool.changed,
  ]), [['verkenning_bijhouden', true, true, false]], JSON.stringify(nochangeTurn));
  assert.equal(aspirationAnchorHomeReads, nochangeHomeReadsBefore,
    'duplicate/nochange krijgt nooit r2');
  const duplicateAfter = db.prepare(
    'SELECT action_count,evidence_json,aspiration_id FROM eva_explorations WHERE id=?',
  ).get(duplicateExploration.id);
  assert.deepEqual(duplicateAfter, {
    action_count: 0, evidence_json: '[]', aspiration_id: null,
  }, 'duplicate/nochange wordt geen anchorbinding of vals explorationbewijs');
  assert.equal(nochangeTurn.contribution ?? null, null);
  assert.equal(nochangeTurn.actionFailed, false,
    'de starttoolreceipt is een geldige no-op en wordt niet als toolfout vervalst');
  assert.equal(nochangeTurn.durableProofMissing, true,
    'zonder changed startreceipt blijft de episode-eis fail-closed open');
  assert.equal(nochangeTurn.progress ?? null, null,
    'een geldige no-op wordt niet als zelfstandige voortgang geprojecteerd');

  resetAttention(948);
  phase = 'aspiration-anchor-cas';
  db.exec(`CREATE TEMP TRIGGER abort_aspiration_anchor_insert
    BEFORE INSERT ON eva_explorations
    BEGIN SELECT RAISE(ABORT,'injected aspiration anchor CAS conflict'); END;`);
  previousRun = autonomyStatus().last_run_ts;
  const casHomeReadsBefore = aspirationAnchorHomeReads;
  const casTurn = await trigger(previousRun);
  db.exec('DROP TRIGGER abort_aspiration_anchor_insert');
  assert.deepEqual(casTurn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified, tool.changed,
  ]), [['verkenning_bijhouden', false, false, false]], JSON.stringify(casTurn));
  assert.equal(aspirationAnchorHomeReads, casHomeReadsBefore,
    'CAS-conflict krijgt nooit r2');
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_explorations').get().count, 0);
  assert.equal(casTurn.contribution ?? null, null);
  assert.equal(casTurn.actionFailed, true);

  const boundAnchorExploration = createResearchExploration(
    'anchor-bound', 'Welke gebonden exploration mag nooit een tweede start gebruiken?',
  );
  phase = 'anchor-bound';
  previousRun = autonomyStatus().last_run_ts;
  const boundHomeReadsBefore = aspirationAnchorHomeReads;
  const boundCountBefore = db.prepare(
    'SELECT count(*) AS count FROM eva_explorations',
  ).get().count;
  const boundTurn = await trigger(previousRun);
  assert.deepEqual(boundTurn.tools, []);
  assert.equal(aspirationAnchorHomeReads, boundHomeReadsBefore);
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_explorations').get().count,
    boundCountBefore, 'gebonden attention maakt geen tweede exploration');
  assert.equal(db.prepare('SELECT action_count FROM eva_explorations WHERE id=?')
    .get(boundAnchorExploration.id).action_count, 0);
  assert.equal(aspirationAnchorUnderstandingCalls, 11,
    'iedere positieve/negatieve ankerfixture gebruikt exact één bounded understandingcall');

  const successQuery = 'Welke actuele primaire bronnen tonen een kleine legale inkomstenkans voor een Nederlands huishouden?';
  let researchExploration = createResearchExploration('success-income', successQuery);
  phase = 'exploration-research-success';
  previousRun = autonomyStatus().last_run_ts;
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  researchProviderCallsBefore = explorationResearchProviderCalls;
  const researchTurn = await trigger(previousRun);
  assert.equal(explorationResearchProviderCalls - researchProviderCallsBefore, 1);
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 2,
    'de echte explorationroute gebruikt exact één understanding- en één webrequest');
  assert.deepEqual(dispatchedSelections.find(
    (item) => item.phase === 'exploration-research-success',
  )?.selection, {
    attention: { kind: 'exploration', id: researchExploration.id },
    work_id: null, goal_episode_id: null, signal_batch_id: null, signal_count: 0,
  });
  assert.deepEqual(researchTurn.tools.map((tool) => [tool.name, tool.ok, tool.verified]), [
    ['zoek_internet', true, true],
  ], JSON.stringify(researchTurn));
  const researchLedger = db.prepare(`SELECT * FROM turn_tool_executions
    WHERE turn_id=? ORDER BY started_ts,rowid`).all(researchTurn.turnId)
    .map((row) => validateExecutionRecord(row));
  assert.equal(researchLedger.length, 1, JSON.stringify(researchLedger));
  assert.equal(researchLedger[0].status, 'done');
  assert.deepEqual(JSON.parse(researchLedger[0].args), { vraag: successQuery });
  assert.deepEqual((() => {
    const binding = JSON.parse(researchLedger[0].semantic_binding);
    return [binding.requirementId, binding.capabilityId, binding.operationId,
      binding.role, binding.basisReceiptIds];
  })(), ['r1', 'internet.research', 'search', 'primary', []]);
  assert.equal(JSON.parse(researchLedger[0].output).verified, true);
  const researchedExploration = db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(researchExploration.id);
  assert.equal(researchedExploration.action_count, researchExploration.action_count + 1);
  assert.equal(researchedExploration.revision, researchExploration.revision + 1);
  assert.match(researchedExploration.evidence_json, /zoek_internet/u);

  const failureCases = [
    ['503', 'Welke actuele subsidieregeling kan een Nederlands huishouden legaal benutten?'],
    ['429', 'Welke actuele primaire bron toont een kleine energiebesparing voor een huishouden?'],
    ['401', 'Welke actuele onderwijsregeling noemt een concrete huishoudelijke vergoeding?'],
    ['network', 'Welke actuele primaire bron toont een kleine lokale verkoopkans voor hergebruik?'],
  ];
  for (const [failure, query] of failureCases) {
    researchExploration = createResearchExploration(`failure-${failure}`, query);
    phase = `exploration-research-failure-${failure}`;
    previousRun = autonomyStatus().last_run_ts;
    providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
    researchProviderCallsBefore = explorationResearchProviderCalls;
    const failedResearchTurn = await trigger(previousRun);
    assert.equal(explorationResearchProviderCalls - researchProviderCallsBefore, 1,
      `${failure} mag geen retry of fallbackrequest openen`);
    assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 2,
      `${failure} blijft exact understanding + één researchrequest`);
    assert.deepEqual(failedResearchTurn.tools.map((tool) => [
      tool.name, tool.ok, tool.verified,
    ]), [['zoek_internet', false, false]], JSON.stringify(failedResearchTurn));
    assert.equal(failedResearchTurn.actionFailed, true);
    const failedLedger = db.prepare(`SELECT * FROM turn_tool_executions
      WHERE turn_id=?`).all(failedResearchTurn.turnId).map((row) => (
      validateExecutionRecord(row)
    ));
    assert.equal(failedLedger.length, 1);
    assert.equal(failedLedger[0].status, 'done');
    assert.equal(JSON.parse(failedLedger[0].output).verified, false);
  }
  assert.equal(new Set(explorationResearchRequests.map((request) => request.model)).size, 1,
    'geen enkele bounded fout wisselt naar een fallbackmodel');

  researchExploration = createResearchExploration(
    'deadline-before-wire',
    'Welke actuele primaire bron kan binnen het resterende researchvenster worden gecontroleerd?',
  );
  phase = 'exploration-research-deadline-before-wire';
  previousRun = autonomyStatus().last_run_ts;
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  researchProviderCallsBefore = explorationResearchProviderCalls;
  const realDateNow = Date.now;
  let deadlineChecks = 0, deadlineResearchTurn;
  Date.now = () => {
    if (/selfAttentionProviderTimeout/u.test(String(new Error().stack || ''))) {
      deadlineChecks += 1;
      return realDateNow() + 10 * 60_000;
    }
    return realDateNow();
  };
  try {
    deadlineResearchTurn = await trigger(previousRun);
  } finally {
    Date.now = realDateNow;
  }
  assert.ok(deadlineChecks > 0, 'de fixture moet de post-claim deadlinecheck raken');
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 1,
    'een verlopen post-claim venster opent alleen de understandingrequest');
  assert.equal(explorationResearchProviderCalls, researchProviderCallsBefore,
    'na een verlopen post-claim deadline start geen webrequest');
  assert.deepEqual(deadlineResearchTurn.tools, []);
  assert.match(deadlineResearchTurn.error || '', /researchvenster.+verlopen/u);
  const deadlineLedger = db.prepare(`SELECT * FROM turn_tool_executions
    WHERE turn_id=?`).all(deadlineResearchTurn.turnId).map((row) => validateExecutionRecord(row));
  assert.equal(deadlineLedger.length, 1);
  const deadlineOutput = JSON.parse(deadlineLedger[0].output);
  assert.deepEqual([deadlineOutput.ok, deadlineOutput.verified,
    deadlineOutput.failure_class, deadlineOutput.data.provider_request_completed],
  [false, false, 'timeout', false]);
  const deadlineExploration = db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(researchExploration.id);
  assert.equal(deadlineExploration.revision, researchExploration.revision,
    'een technische timeout boekt ook de exploration-revisie niet inhoudelijk af');
  assert.equal(deadlineExploration.action_count, researchExploration.action_count);
  assert.equal(deadlineExploration.evidence_json, JSON.stringify(researchExploration.evidence));

  researchExploration = createResearchExploration(
    'multi-plan',
    'Welke actuele primaire bron past bij deze concrete explorationvraag?',
  );
  phase = 'exploration-research-multi';
  previousRun = autonomyStatus().last_run_ts;
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  researchProviderCallsBefore = explorationResearchProviderCalls;
  const nonProviderBeforeMulti = nonProviderCalls;
  const ledgerBeforeMulti = db.prepare(
    'SELECT count(*) AS count FROM turn_tool_executions',
  ).get().count;
  const multiResearchTurn = await trigger(previousRun);
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 1);
  assert.equal(explorationResearchProviderCalls, researchProviderCallsBefore);
  assert.equal(nonProviderCalls, nonProviderBeforeMulti);
  assert.deepEqual(multiResearchTurn.tools, []);
  assert.equal(multiResearchTurn.error, 'material_request_uninterpreted',
    'een tweede read naast de provideractie blijft vóór iedere I/O fail-closed');
  assert.equal(db.prepare('SELECT count(*) AS count FROM turn_tool_executions').get().count,
    ledgerBeforeMulti, 'een multi-plan bereikt ook geen ledger-intent');
  assert.equal(db.prepare(`SELECT count(*) AS count FROM timeline
    WHERE kind='action' AND json_extract(meta,'$.turnId')=?`).get(
    multiResearchTurn.turnId,
  ).count, 0);

  researchExploration = createResearchExploration(
    'revision-race',
    'Welke actuele primaire bron blijft geldig bij een gelijktijdige explorationrevisie?',
  );
  revisionRaceExplorationId = researchExploration.id;
  phase = 'exploration-research-revision-race';
  previousRun = autonomyStatus().last_run_ts;
  researchProviderCallsBefore = explorationResearchProviderCalls;
  const revisionRaceTurn = await trigger(previousRun);
  assert.equal(explorationResearchProviderCalls - researchProviderCallsBefore, 1);
  assert.deepEqual(revisionRaceTurn.tools, []);
  assert.match(revisionRaceTurn.error || '', /exploration-revisie is intussen gewijzigd/u);
  const revisionRaceLedger = db.prepare(`SELECT * FROM turn_tool_executions
    WHERE turn_id=?`).all(revisionRaceTurn.turnId).map((row) => validateExecutionRecord(row));
  assert.equal(revisionRaceLedger.length, 1);
  const revisionRaceOutput = JSON.parse(revisionRaceLedger[0].output);
  assert.deepEqual([
    revisionRaceOutput.ok, revisionRaceOutput.verified,
    revisionRaceOutput.failure_class,
    revisionRaceOutput.data.provider_request_completed,
  ], [false, false, 'conflict', true]);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM timeline
    WHERE kind='action' AND json_extract(meta,'$.turnId')=?`).get(
    revisionRaceTurn.turnId,
  ).count, 0, 'stale providerinhoud wordt niet als action gepubliceerd');
  const revisionRacedExploration = db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(researchExploration.id);
  assert.equal(revisionRacedExploration.action_count, researchExploration.action_count);
  assert.equal(revisionRacedExploration.evidence_json,
    JSON.stringify(researchExploration.evidence));

  researchExploration = createResearchExploration(
    'atomic-settlement-rollback',
    'Welke actuele primaire bron bewijst dat providerreceipt en explorationactie atomair landen?',
  );
  const atomicRollbackExplorationBefore = db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(researchExploration.id);
  const atomicRollbackKey = researchExecutionKey(researchExploration);
  db.exec(`CREATE TEMP TRIGGER abort_verified_bounded_research_completion
    BEFORE UPDATE OF status,output ON turn_tool_executions
    WHEN NEW.execution_key='${atomicRollbackKey}' AND NEW.status='done'
      AND json_extract(NEW.output,'$.verified')=1
    BEGIN SELECT RAISE(ABORT,'injected atomic bounded settlement failure'); END;`);
  phase = 'exploration-research-atomic-settlement-rollback';
  previousRun = autonomyStatus().last_run_ts;
  researchProviderCallsBefore = explorationResearchProviderCalls;
  const atomicRollbackTurn = await trigger(previousRun);
  db.exec('DROP TRIGGER abort_verified_bounded_research_completion');
  assert.equal(explorationResearchProviderCalls - researchProviderCallsBefore, 1);
  assert.deepEqual(atomicRollbackTurn.tools, []);
  assert.match(atomicRollbackTurn.error || '', /injected atomic bounded settlement failure/u);
  const atomicRollbackLedger = validateExecutionRecord(db.prepare(
    'SELECT * FROM turn_tool_executions WHERE execution_key=?',
  ).get(atomicRollbackKey));
  const atomicRollbackOutput = JSON.parse(atomicRollbackLedger.output);
  assert.deepEqual([atomicRollbackLedger.status, atomicRollbackOutput.ok,
    atomicRollbackOutput.verified, atomicRollbackOutput.failure_class,
    atomicRollbackOutput.data.provider_request_completed],
  ['done', false, false, 'conflict', true]);
  const atomicRollbackExploration = db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(researchExploration.id);
  assert.equal(atomicRollbackExploration.revision, researchExploration.revision + 1,
    'na transactionrollback blijft alleen de scheduler-backoffmarker over');
  assert.equal(atomicRollbackExploration.action_count, researchExploration.action_count);
  assert.equal(atomicRollbackExploration.evidence_json,
    JSON.stringify(researchExploration.evidence));
  const withoutSchedulerBackoffFields = (row) => {
    const {
      revision: _revision,
      last_selected_ts: _lastSelectedTs,
      scheduler_deferred_ts: _schedulerDeferredTs,
      ...stable
    } = row;
    return stable;
  };
  assert.deepEqual(withoutSchedulerBackoffFields(atomicRollbackExploration),
    withoutSchedulerBackoffFields(atomicRollbackExplorationBefore),
  'de gefaalde atomische settlement wijzigt geen enkel exploration-inhoudsveld');
  assert.ok(atomicRollbackExploration.scheduler_deferred_ts);
  assert.equal(atomicRollbackExploration.last_selected_ts,
    atomicRollbackExploration.scheduler_deferred_ts,
  'revision +1 bestaat uitsluitend uit de ene scheduler-backoff-CAS');
  assert.equal(db.prepare(`SELECT count(*) AS count FROM timeline
    WHERE kind='action' AND json_extract(meta,'$.turnId')=?`).get(
    atomicRollbackTurn.turnId,
  ).count, 0, 'een teruggerolde verified receipt wordt nergens gepubliceerd');

  researchExploration = createResearchExploration(
    'crash-replay',
    'Welke actuele primaire bron mag na een gecrashte providerclaim veilig worden onderzocht?',
  );
  const crashedExecutionKey = researchExecutionKey(researchExploration);
  const crashedClaim = claimTurnToolExecution({
    executionKey: crashedExecutionKey,
    turnId: 'prior-crashed-provider-turn', tool: 'zoek_internet',
    args: { vraag: representativeResearchQuery },
    semanticBinding: {
      understandingSha256: 'a'.repeat(64), requirementId: 'r1',
      addressesRequirementIds: ['r1'], basisReceiptIds: [],
      capabilityId: 'internet.research', operationId: 'search',
      role: 'primary', executionType: null,
    },
  });
  assert.equal(crashedClaim.claimed, true);
  const crashedRowBeforeReplay = db.prepare(
    'SELECT * FROM turn_tool_executions WHERE execution_key=?',
  ).get(crashedExecutionKey);
  phase = 'exploration-research-replay-conflict';
  previousRun = autonomyStatus().last_run_ts;
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  researchProviderCallsBefore = explorationResearchProviderCalls;
  const replayedResearchTurn = await trigger(previousRun);
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 1,
    'een started claim met andere query krijgt geen tweede webrequest of fallback');
  assert.equal(explorationResearchProviderCalls, researchProviderCallsBefore);
  assert.deepEqual(replayedResearchTurn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified,
  ]), [
    ['zoek_internet', false, false],
  ]);
  const replayActionRows = db.prepare(`SELECT meta FROM timeline
    WHERE kind='action' AND json_extract(meta,'$.turnId')=? ORDER BY id`).all(
    replayedResearchTurn.turnId,
  );
  assert.equal(replayActionRows.length, 1);
  const replayAction = JSON.parse(replayActionRows[0].meta);
  const replayActionOutput = JSON.parse(replayAction.output);
  assert.deepEqual([
    replayActionOutput.contract, replayActionOutput.tool,
    replayActionOutput.ok, replayActionOutput.verified,
    replayActionOutput.failure_class, replayActionOutput.retryable,
    replayActionOutput.effect_uncertain,
  ], ['ActionResult/v1', 'zoek_internet', false, false, 'conflict', false, false]);
  assert.equal(replayedResearchTurn.actionFailed, true);
  assert.equal(replayedResearchTurn.contribution ?? null, null);
  assert.equal(autonomyStatus().last_result.productive, false);
  assert.equal(autonomyStatus().last_result.progressed, false);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM turn_tool_executions
    WHERE turn_id=?`).get(replayedResearchTurn.turnId).count, 0,
  'de conflictbeurt maakt geen tweede providerledger');
  const crashedRowAfterReplay = db.prepare(
    'SELECT * FROM turn_tool_executions WHERE execution_key=?',
  ).get(crashedExecutionKey);
  assert.deepEqual(crashedRowAfterReplay, crashedRowBeforeReplay,
    'de oude started claim blijft bytegelijk; de nieuwe beurt publiceert alleen false conflict');
  assert.equal(validateExecutionRecord(crashedRowAfterReplay).status, 'started');
  const replayUnderstanding = JSON.parse(db.prepare(`SELECT meta FROM timeline
    WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?
    ORDER BY id DESC LIMIT 1`).get(replayedResearchTurn.turnId).meta).understanding;
  assert.notEqual(replayUnderstanding.sha256, 'a'.repeat(64),
    'dezelfde query botst uitsluitend door de oudere turn- en understandingbinding');
  const replayAdvanced = db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(researchExploration.id);
  assert.equal(replayAdvanced.revision, researchExploration.revision + 1);
  assert.equal(replayAdvanced.action_count, researchExploration.action_count + 1,
    'de fail-closed toolreceipt voorkomt een permanent vastgezette explorationrevisie');
  const replayEvidence = JSON.parse(replayAdvanced.evidence_json);
  assert.equal(replayEvidence.length, researchExploration.evidence.length + 1);
  assert.deepEqual([replayEvidence.at(-1).tool, replayEvidence.at(-1).verified],
    ['zoek_internet', false]);

  researchExploration = createResearchExploration(
    'claim-storage-failure',
    'Welke actuele bron mag nooit starten wanneer de durable claimopslag faalt?',
  );
  const storageFailureKey = researchExecutionKey(researchExploration);
  db.exec(`CREATE TEMP TRIGGER abort_bounded_research_claim
    BEFORE INSERT ON turn_tool_executions
    WHEN NEW.execution_key='${storageFailureKey}'
    BEGIN SELECT RAISE(ABORT,'injected bounded research claim storage failure'); END;`);
  phase = 'exploration-research-claim-storage-failure';
  previousRun = autonomyStatus().last_run_ts;
  researchProviderCallsBefore = explorationResearchProviderCalls;
  const storageFailureTurn = await trigger(previousRun);
  db.exec('DROP TRIGGER abort_bounded_research_claim');
  assert.equal(explorationResearchProviderCalls, researchProviderCallsBefore);
  assert.deepEqual(storageFailureTurn.tools, []);
  assert.match(storageFailureTurn.error || '', /injected bounded research claim storage failure/u);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM turn_tool_executions
    WHERE execution_key=?`).get(storageFailureKey).count, 0);
  const storageFailureExploration = db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(researchExploration.id);
  assert.equal(storageFailureExploration.revision, researchExploration.revision + 1);
  assert.equal(storageFailureExploration.action_count, researchExploration.action_count);
  assert.equal(storageFailureExploration.evidence_json,
    JSON.stringify(researchExploration.evidence));
  assert.equal(autonomyStatus().last_result.productive, false);
  assert.equal(autonomyStatus().last_result.progressed, false);

  researchExploration = createResearchExploration(
    'corrupt-claim',
    'Welke actuele bron mag nooit starten vanuit een corrupte durable claim?',
  );
  const corruptExecutionKey = researchExecutionKey(researchExploration);
  const corruptClaim = claimTurnToolExecution({
    executionKey: corruptExecutionKey,
    turnId: 'prior-corrupt-provider-turn', tool: 'zoek_internet',
    args: { vraag: representativeResearchQuery },
    semanticBinding: {
      understandingSha256: 'b'.repeat(64), requirementId: 'r1',
      addressesRequirementIds: ['r1'], basisReceiptIds: [],
      capabilityId: 'internet.research', operationId: 'search',
      role: 'primary', executionType: null,
    },
  });
  assert.equal(corruptClaim.claimed, true);
  withoutExecutionTrigger('turn_tool_executions_intent_immutable', () => {
    db.prepare(`UPDATE turn_tool_executions SET intent_record_sha256=?
      WHERE execution_key=?`).run('c'.repeat(64), corruptExecutionKey);
  });
  phase = 'exploration-research-corrupt-claim';
  previousRun = autonomyStatus().last_run_ts;
  researchProviderCallsBefore = explorationResearchProviderCalls;
  const corruptClaimTurn = await trigger(previousRun);
  assert.equal(explorationResearchProviderCalls, researchProviderCallsBefore);
  assert.deepEqual(corruptClaimTurn.tools, []);
  assert.match(corruptClaimTurn.error || '', /duurzame tooluitvoeringssleutel/u);
  const corruptClaimExploration = db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(researchExploration.id);
  assert.equal(corruptClaimExploration.revision, researchExploration.revision + 1);
  assert.equal(corruptClaimExploration.action_count, researchExploration.action_count);
  assert.equal(corruptClaimExploration.evidence_json,
    JSON.stringify(researchExploration.evidence));
  assert.equal(autonomyStatus().last_result.productive, false);
  assert.equal(autonomyStatus().last_result.progressed, false);

  resetAttention(912);
  phase = 'iterative';
  previousRun = autonomyStatus().last_run_ts;
  const iterativeBlockedTurn = await trigger(previousRun);
  assert.equal(iterativeBlockedCalls, 1);
  assert.deepEqual(iterativeBlockedTurn.tools, []);
  assert.match(iterativeBlockedTurn.planningError || '',
    /workspace\.file\.write[\s\S]*unsupported_iterative_target/u);
  assert.notEqual(db.prepare('SELECT last_touched FROM aspirations WHERE id=912').get().last_touched,
    '2020-01-01T00:00:00.000Z');

  resetAttention(913);
  db.exec(`CREATE TEMP TABLE aspiration_touch_audit (
    aspiration_id INTEGER, last_touched TEXT, progress TEXT
  );
  CREATE TEMP TRIGGER audit_aspiration_913_touch
    AFTER UPDATE OF last_touched,progress ON aspirations
    WHEN NEW.id=913
    BEGIN
      INSERT INTO aspiration_touch_audit (aspiration_id,last_touched,progress)
      VALUES (NEW.id,NEW.last_touched,NEW.progress);
    END;`);
  phase = 'read-failure';
  previousRun = autonomyStatus().last_run_ts;
  const failedReadTurn = await trigger(previousRun);
  assert.equal(readFailureCalls, 1);
  assert.deepEqual(failedReadTurn.tools.map((tool) => tool.name),
    ['verkenning_bijhouden', 'lees_bestand'], JSON.stringify(failedReadTurn));
  assert.equal(failedReadTurn.tools[1].ok, false);
  assert.equal(fs.existsSync(path.join(process.env.EVA_DATA_DIR, 'workspace',
    'resultaat', 'mag-niet-ontstaan.md')), false);
  const explorationTouchedAspiration = db.prepare(
    'SELECT progress,last_touched FROM aspirations WHERE id=913',
  ).get();
  assert.match(explorationTouchedAspiration.progress, /^Eigen verkenning #\d+ gestart:/u,
    'de exploration-tool blijft de enige auteur van inhoudelijke aspiratievoortgang');
  assert.notEqual(explorationTouchedAspiration.last_touched,
    '2020-01-01T00:00:00.000Z');
  assert.deepEqual(db.prepare(
    'SELECT progress,last_touched FROM aspiration_touch_audit ORDER BY rowid',
  ).all(), [explorationTouchedAspiration],
  'de exploration-touch is de ene exacte rijmutatie; settlement overschrijft haar timestamp niet');
  db.exec('DROP TRIGGER audit_aspiration_913_touch; DROP TABLE aspiration_touch_audit;');

  resetAttention(914);
  insertAttention(916, {
    text: 'Werk daarna de lagere prioriteitsrichting uit.', priority: 0.9,
  });
  phase = 'artifact';
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  previousRun = autonomyStatus().last_run_ts;
  const beforeArtifactWork = db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count;
  const artifactTurn = await trigger(previousRun);
  assert.equal(artifactUnderstandingCalls, 1);
  assert.equal(artifactExecutionCalls, 2);
  assert.equal(artifactWireBodies.length, 3,
    'interpretatie, materialisatie en hostverplichte readback zijn exact drie providercalls');
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 3,
    'de health-teller ziet exact dezelfde interpretatie-, write- en readbackrequests');
  assert.deepEqual(observedSelections.filter((item) => item.phase === 'artifact')
    .map((item) => item.selection), Array.from({ length: 3 }, () => ({
    attention: { kind: 'aspiration', id: 914 },
    work_id: null, goal_episode_id: null, signal_batch_id: null, signal_count: 0,
  })));
  assert.ok(artifactWireBodies.every((body) => !Object.hasOwn(body, 'max_output_tokens')));
  assert.ok(artifactBodies.every((body) => !Object.hasOwn(body, 'max_output_tokens')));
  assert.deepEqual(artifactTurn.tools.map((tool) => tool.name),
    ['schrijf_bestand', 'lees_bestand'], JSON.stringify(artifactTurn));
  assert.ok(artifactTurn.tools.every((tool) => tool.ok && tool.verified),
    JSON.stringify(artifactTurn));
  const artifactPath = path.join(process.env.EVA_DATA_DIR, 'workspace',
    'resultaat', 'gezinsbijdrage.md');
  assert.equal(fs.readFileSync(artifactPath, 'utf8'), artifactContent);
  const materialSha256 = createHash('sha256').update(artifactContent).digest('hex');
  const writeResult = JSON.parse(artifactTurn.tools[0].output);
  const readResult = JSON.parse(artifactTurn.tools[1].output);
  assert.equal(writeResult.data.material_sha256, materialSha256);
  assert.equal(readResult.artifact_receipt.sha256, materialSha256);
  assert.equal(readResult.artifact_receipt.complete_read, true);
  assert.equal(writeResult.data.path, readResult.artifact_receipt.path);
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    beforeArtifactWork, 'een afgerond turn-artefact maakt niet stil dubbel vervolgwerk');
  const settledArtifactAspiration = db.prepare(
    'SELECT progress,last_touched FROM aspirations WHERE id=914',
  ).get();
  assert.equal(settledArtifactAspiration.progress, '',
    'ook een bijdrage laat progress exclusief aan inhoudelijke tools');
  assert.notEqual(settledArtifactAspiration.last_touched,
    '2020-01-01T00:00:00.000Z');
  assert.equal(autonomyStatus().last_result.productive, true,
    'het teruggelezen artefact is een werkelijk productief schedulerresultaat');
  const settledArtifactTouched = settledArtifactAspiration.last_touched;
  phase = 'next-aspiration';
  forceSchedulerDue();
  previousRun = autonomyStatus().last_run_ts;
  const nextAspirationTurn = await trigger(previousRun);
  assert.equal(nextAspirationCalls, 1);
  assert.deepEqual(nextAspirationTurn.tools, []);
  assert.deepEqual(dispatchedSelections.find(
    (item) => item.phase === 'next-aspiration',
  )?.selection, {
    attention: { kind: 'aspiration', id: 916 },
    work_id: null, goal_episode_id: null, signal_batch_id: null, signal_count: 0,
  }, 'de tweede publieke schedulertick kiest werkelijk de lagere due aspiratie');
  assert.equal(db.prepare('SELECT last_touched FROM aspirations WHERE id=914').get().last_touched,
    settledArtifactTouched, 'de tweede tick raakt de al geroteerde aspiratie niet opnieuw aan');
  assert.notEqual(db.prepare('SELECT last_touched FROM aspirations WHERE id=916').get().last_touched,
    '2020-01-01T00:00:00.000Z');

  fs.mkdirSync(path.join(process.env.EVA_DATA_DIR, 'workspace'), { recursive: true });
  fs.writeFileSync(path.join(process.env.EVA_DATA_DIR, 'workspace', 'bron.md'),
    '# Bron\nEén gecontroleerd lokaal feit.\n');
  resetAttention(915);
  phase = 'valid';
  previousRun = autonomyStatus().last_run_ts;
  const validTurn = await trigger(previousRun);
  assert.equal(validCalls, 1);
  assert.deepEqual(validTurn.tools.map((tool) => tool.name),
    ['lees_bestand', 'verkenning_bijhouden', 'werk_bijhouden'], JSON.stringify(validTurn));
  assert.ok(validTurn.tools.every((tool) => tool.ok && tool.verified), JSON.stringify(validTurn));
  const rows = db.prepare(`SELECT meta FROM timeline WHERE kind='action'
    AND json_extract(meta,'$.turnId')=? ORDER BY id`).all(validTurn.turnId).map((row) => (
    JSON.parse(row.meta)
  ));
  assert.deepEqual(rows.map((row) => row.tool),
    ['lees_bestand', 'verkenning_bijhouden', 'werk_bijhouden']);
  assert.equal(rows.some((row) => /Leg nu eerst je eigen vraag/u.test(row.output)), false);
  const exploration = db.prepare('SELECT * FROM eva_explorations ORDER BY id DESC LIMIT 1').get();
  const work = db.prepare('SELECT * FROM eva_work_items ORDER BY id DESC LIMIT 1').get();
  const workEpisode = db.prepare(
    'SELECT * FROM eva_goal_episodes WHERE work_item_id=?',
  ).get(work.id);
  assert.equal(exploration.aspiration_id, 915);
  assert.equal(exploration.action_count, 1,
    'work:start is na de exploration-binding aan dezelfde trace toegeschreven');
  assert.match(exploration.evidence_json, /werk_bijhouden/u);
  assert.equal(work.title, 'Meetbare gezinsbijdrage uitwerken');
  assert.equal(nonProviderCalls, 0, 'de geldige lokale route gebruikt geen extern netwerk');

  insertAttention(924, {
    text: 'Lagere due aspiratie voor de bestaande commitmentproef.', priority: 0.1,
  });
  phase = 'priority-exploration';
  forceSchedulerDue();
  previousRun = autonomyStatus().last_run_ts;
  await trigger(previousRun);
  assert.ok(priorityExplorationCalls <= 1);
  assert.deepEqual(dispatchedSelections.find(
    (item) => item.phase === 'priority-exploration',
  )?.selection, {
    attention: { kind: 'exploration', id: exploration.id },
    work_id: null, goal_episode_id: null, signal_batch_id: null, signal_count: 0,
  }, 'een unfinished exploration blijft vóór de lagere due aspiratie staan');
  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare(`UPDATE eva_work_items SET status='open',next_wake_ts=NULL,
    lease_owner=NULL,lease_until=NULL WHERE id=?`).run(work.id);
  db.prepare(`UPDATE eva_goal_episodes SET state='waiting',wake_ts=NULL,
    lease_owner=NULL,lease_until=NULL WHERE work_item_id=?`).run(work.id);
  phase = 'priority-work';
  forceSchedulerDue();
  previousRun = autonomyStatus().last_run_ts;
  await trigger(previousRun);
  assert.ok(priorityWorkCalls <= 1);
  assert.deepEqual(dispatchedSelections.find((item) => item.phase === 'priority-work')?.selection, {
    attention: null, work_id: work.id, goal_episode_id: workEpisode.id,
    signal_batch_id: null, signal_count: 0,
  }, 'runnable unfinished werk blijft vóór de lagere due aspiratie staan');
  db.prepare("UPDATE eva_work_items SET status='cancelled' WHERE id=?").run(work.id);
  db.prepare("UPDATE eva_goal_episodes SET state='abandoned' WHERE work_item_id=?").run(work.id);

  resetAttention(925);
  phase = 'partial-preserve';
  previousRun = autonomyStatus().last_run_ts;
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  let networkCallsBefore = nonProviderCalls;
  const workHighwater = db.prepare(
    'SELECT COALESCE(MAX(id),0) AS id FROM eva_work_items',
  ).get().id;
  const explorationHighwater = db.prepare(
    'SELECT COALESCE(MAX(id),0) AS id FROM eva_explorations',
  ).get().id;
  const partialTurn = await trigger(previousRun);
  assert.equal(partialPreserveCalls, 1);
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 1,
    'de partial route gebruikt alleen de ene issued interpretatiecall');
  assert.equal(nonProviderCalls - networkCallsBefore, 1,
    'alleen de modelgekozen huisread bereikt de lokale HA-route');
  const partialActions = db.prepare(`SELECT meta FROM timeline WHERE kind='action'
    AND json_extract(meta,'$.turnId')=? ORDER BY id`).all(partialTurn.turnId)
    .map((row) => JSON.parse(row.meta));
  assert.deepEqual(partialActions.map((row) => row.tool),
    ['huis_status', 'verkenning_bijhouden', 'werk_bijhouden']);
  const partialReadReceipt = JSON.parse(partialActions[0].output);
  assert.equal(partialReadReceipt.ok, true);
  assert.equal(partialReadReceipt.verified, true);
  assert.equal(partialReadReceipt.changed, false);
  assert.equal(partialReadReceipt.partial, true);
  assert.equal(partialReadReceipt.needs_followup, true);
  assert.equal(partialTurn.actionFailed, true,
    'de bewaarde r1-read blijft incompleet na de onafhankelijke starts');
  const partialEpisode = db.prepare(
    'SELECT * FROM cognitive_episodes WHERE correlation_id=?',
  ).get(partialTurn.turnId);
  assert.equal(partialEpisode.status, 'completed');
  assert.equal(partialEpisode.result_class, 'partial_execution');
  assert.equal(JSON.parse(partialEpisode.evidence_json).contribution, null);
  assert.equal(JSON.parse(partialEpisode.effect_json).madeProgress, true);
  const persistedExploration = db.prepare(
    'SELECT * FROM eva_explorations WHERE id>? ORDER BY id LIMIT 1',
  ).get(explorationHighwater);
  const persistedWork = db.prepare(
    'SELECT * FROM eva_work_items WHERE id>? ORDER BY id LIMIT 1',
  ).get(workHighwater);
  assert.equal(persistedExploration.aspiration_id, 925);
  assert.equal(persistedExploration.topic_key,
    'scheduler_partial_read_visible_contribution');
  assert.equal(persistedWork.title, 'Meetbare gezinsbijdrage uitwerken');
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_goal_episodes WHERE work_item_id=?')
    .get(persistedWork.id).count, 1);
  db.prepare("UPDATE eva_work_items SET status='cancelled' WHERE id=?").run(persistedWork.id);
  db.prepare("UPDATE eva_goal_episodes SET state='abandoned' WHERE work_item_id=?")
    .run(persistedWork.id);

  resetAttention(926);
  const conflictingExploration = startExploration({
    question: 'Welke kleine gezinsbijdrage verdient een concrete vervolgcyclus?',
    whyNow: 'Een eerdere begrensde cyclus heeft deze richting al beoordeeld.',
    hypothesis: 'Een korte vervolgcyclus kan deze bijdrage toetsbaar maken.',
    expectedObservation: 'Een duurzame cyclus bewaart de concrete vervolgstap.',
    stopRule: 'Stop zodra één toetsbare vervolgstap duurzaam is vastgelegd.',
    maxActions: 2, topicKey: 'scheduler_partial_read_r2_conflict',
    topicAnchors: ['event:aspiration:926', 'constraint:kleine-bijdrage'],
    source: 'self',
  }).exploration;
  updateExploration(conflictingExploration.id, {
    status: 'completed', result: 'De richting is al begrensd onderzocht.',
    decision: 'Niet opnieuw starten zonder concreet nieuw bewijs.',
  });
  phase = 'partial-r2-fail';
  previousRun = autonomyStatus().last_run_ts;
  providerAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  networkCallsBefore = nonProviderCalls;
  const failedR2WorkHighwater = db.prepare(
    'SELECT COALESCE(MAX(id),0) AS id FROM eva_work_items',
  ).get().id;
  const failedR2Turn = await trigger(previousRun);
  assert.equal(partialR2FailureCalls, 1);
  assert.equal(llmRuntimeStatus().provider_request_attempts - providerAttemptsBefore, 1);
  assert.equal(nonProviderCalls - networkCallsBefore, 1);
  const failedR2Actions = db.prepare(`SELECT meta FROM timeline WHERE kind='action'
    AND json_extract(meta,'$.turnId')=? ORDER BY id`).all(failedR2Turn.turnId)
    .map((row) => JSON.parse(row.meta));
  assert.deepEqual(failedR2Actions.map((row) => row.tool),
    ['huis_status', 'verkenning_bijhouden'],
    'een mislukte r2 stopt vóór r3 zonder retry of reorder');
  assert.equal(JSON.parse(failedR2Actions[0].output).partial, true);
  assert.equal(JSON.parse(failedR2Actions[1].output).ok, false);
  assert.equal(failedR2Turn.actionFailed, true);
  const failedR2Episode = db.prepare(
    'SELECT * FROM cognitive_episodes WHERE correlation_id=?',
  ).get(failedR2Turn.turnId);
  assert.equal(failedR2Episode.status, 'completed');
  assert.equal(failedR2Episode.result_class, 'partial_execution');
  assert.equal(JSON.parse(failedR2Episode.evidence_json).contribution, null);
  assert.equal(JSON.parse(failedR2Episode.effect_json).madeProgress, false);
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_work_items WHERE id>?')
    .get(failedR2WorkHighwater).count, 0);

  resetAttention(917);
  phase = 'no-action';
  previousRun = autonomyStatus().last_run_ts;
  const noActionTurn = await trigger(previousRun);
  assert.equal(noActionCalls, 1);
  assert.deepEqual(noActionTurn.tools, []);
  assert.match(noActionTurn.noActionReason || '', /geen concrete|geen nieuw resultaat/iu);
  const consciouslySkipped = db.prepare(
    'SELECT progress,last_touched FROM aspirations WHERE id=917',
  ).get();
  assert.equal(consciouslySkipped.progress, '');
  assert.notEqual(consciouslySkipped.last_touched, '2020-01-01T00:00:00.000Z',
    'een geldige model-authored silent/no_action roteert eveneens');

  resetAttention(921);
  phase = 'concurrent';
  previousRun = autonomyStatus().last_run_ts;
  await trigger(previousRun);
  assert.equal(concurrentCalls, 1);
  assert.deepEqual(db.prepare(
    'SELECT text,progress,last_touched FROM aspirations WHERE id=921',
  ).get(), {
    text: 'Een gelijktijdig bijgestelde aspiratierichting.',
    progress: 'Gelijktijdige beheerder wijzigde de inhoud; scheduler-CAS mag niets aanraken.',
    last_touched: '2020-01-01T00:00:00.000Z',
  }, 'een inhoudsdrift na selectie maakt de settlement-CAS exact nul');

  resetAttention(922);
  phase = 'artifact';
  db.exec(`CREATE TEMP TRIGGER abort_completed_scheduler_publication
    BEFORE UPDATE OF value ON kv
    WHEN OLD.key='eva_autonomy_scheduler_v1'
      AND json_extract(NEW.value,'$.last_result.productive')=1
    BEGIN
      SELECT RAISE(ABORT, 'injected scheduler publication failure');
    END`);
  previousRun = autonomyStatus().last_run_ts;
  await trigger(previousRun);
  const rolledBackAspiration = db.prepare(
    'SELECT progress,last_touched FROM aspirations WHERE id=922',
  ).get();
  assert.deepEqual(rolledBackAspiration, {
    progress: '', last_touched: '2020-01-01T00:00:00.000Z',
  }, 'een mislukte schedulerpublicatie rolt de aspiration-touch atomair terug');
  assert.match(autonomyStatus().last_result.error || '',
    /injected scheduler publication failure/u);
  assert.equal(Object.hasOwn(autonomyStatus().last_result, 'turn_id'), false,
    'na rollback mag geen vals afgerond schedulerresultaat gepubliceerd zijn');
  db.exec('DROP TRIGGER abort_completed_scheduler_publication');

  resetAttention(923, { cadenceHours: 0, lastTouched: null });
  phase = 'invalid';
  previousRun = autonomyStatus().last_run_ts;
  await trigger(previousRun);
  const oneShotAspiration = db.prepare('SELECT * FROM aspirations WHERE id=923').get();
  assert.ok(oneShotAspiration.last_touched,
    'cadans nul krijgt na één werkelijk afgeronde beurt exact één touch');
  assert.equal(oneShotAspiration.progress, '');
  assert.equal(__curiosityTest.aspirationDue(oneShotAspiration), false,
    'cadans nul betekent one-shot: een aangeraakte richting wordt niet opnieuw due');
  assert.equal(dueAttention(), null);

  researchExploration = createResearchExploration(
    'preempt',
    'Welke actuele primaire bron kan één veilige zelfstandige vervolgstap dragen?',
  );
  phase = 'exploration-research-preempt';
  previousRun = autonomyStatus().last_run_ts;
  researchProviderCallsBefore = explorationResearchProviderCalls;
  const preemptedResearchTurn = await trigger(previousRun);
  assert.equal(explorationResearchProviderCalls - researchProviderCallsBefore, 1);
  assert.equal(autonomyStatus().last_result.turn_id, preemptedResearchTurn.turnId);
  assert.equal(autonomyStatus().last_result.yielded, true,
    'de publieke scheduler projecteert de echte supersession als yielded');
  assert.deepEqual(preemptedResearchTurn.tools, []);
  assert.ok(preemptInteractivePromise);
  await preemptInteractivePromise;
  const preemptLedger = db.prepare(`SELECT * FROM turn_tool_executions
    WHERE turn_id=?`).all(preemptedResearchTurn.turnId).map((row) => validateExecutionRecord(row));
  assert.equal(preemptLedger.length, 1);
  const preemptOutput = JSON.parse(preemptLedger[0].output);
  assert.deepEqual([preemptOutput.ok, preemptOutput.verified,
    preemptOutput.failure_class, preemptOutput.data.provider_request_completed],
  [false, false, 'conflict', true]);
  const preemptedExploration = db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(researchExploration.id);
  assert.equal(preemptedExploration.revision, researchExploration.revision);
  assert.equal(preemptedExploration.action_count, researchExploration.action_count);
  assert.equal(preemptedExploration.evidence_json, JSON.stringify(researchExploration.evidence));
  assert.equal(db.prepare(`SELECT count(*) AS count FROM timeline
    WHERE kind='action' AND json_extract(meta,'$.turnId')=?`).get(
    preemptedResearchTurn.turnId,
  ).count, 0);
  assert.equal(explorationResearchUnderstandingCalls, 13,
    'ieder van de dertien authentieke exploration-scenario’s gebruikt exact één understanding');

  phase = 'anchor-real-monitoring';
  const monitoringExplorationsBefore = db.prepare(
    'SELECT count(*) AS count FROM eva_explorations',
  ).get().count;
  const monitoringActionsBefore = db.prepare(
    "SELECT count(*) AS count FROM timeline WHERE kind='action'",
  ).get().count;
  const monitoringTurn = await handleMessageDetailed({
    channel: 'web', sender: 'bewoner',
    text: 'Blijf sensor.luchtkwaliteit de komende twee uur volgen en meld mij als de waarde verandert.',
    messageContext: { syntheticTest: true, queueKey: 'anchor-real-monitoring' },
  });
  assert.deepEqual(monitoringTurn.tools, [], JSON.stringify(monitoringTurn));
  assert.equal(db.prepare('SELECT count(*) AS count FROM eva_explorations').get().count,
    monitoringExplorationsBefore,
  'een echte bewonersmonitoring kan het private aspiration-anker niet gebruiken');
  assert.equal(db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='action'")
    .get().count, monitoringActionsBefore);
  const monitoringUnderstanding = JSON.parse(db.prepare(`SELECT meta FROM timeline
    WHERE kind='turn_understanding' AND json_extract(meta,'$.turnId')=?`).get(
    monitoringTurn.turnId,
  ).meta);
  assert.equal(monitoringUnderstanding.attemptFailures.length, 3);
  assert.ok(monitoringUnderstanding.attemptFailures.every((failure) => (
    failure.validation === 'selected_denied_action'
  )), `bewonersmonitoring wordt al vóór de private anchorgrens door user-authority gestopt: ${
    JSON.stringify(monitoringUnderstanding.attemptFailures)}`);

  const expireSyntheticConversationPause = () => {
    const status = agentStatus();
    for (const turn of [status.last_turn, status.last_interactive_turn]) {
      if (turn && turn.mode !== 'autonomy') turn.finishedAt = '2020-01-01T00:00:00.000Z';
    }
  };
  // De voorgaande synthetic bewonersadversarial hoort zijn echte runtimepauze
  // te testen, maar mag de daarna expliciet afgevuurde schedulerfixtures niet
  // twee minuten laten wachten.
  expireSyntheticConversationPause();

  const seedTerminalExploration = async (aspirationId, suffix) => {
    resetAttention(aspirationId);
    phase = `aspiration-anchor-terminal-${suffix}`;
    const priorRun = autonomyStatus().last_run_ts;
    const seededTurn = await trigger(priorRun);
    assert.deepEqual(seededTurn.tools.map((tool) => [
      tool.name, tool.ok, tool.verified, tool.changed,
    ]), [
      ['verkenning_bijhouden', true, true, true],
      ['huis_status', true, true, false],
    ], JSON.stringify(seededTurn));
    const exploration = db.prepare(`SELECT * FROM eva_explorations
      WHERE aspiration_id=? ORDER BY id DESC LIMIT 1`).get(aspirationId);
    assert.ok(exploration);
    assert.equal(exploration.action_count, 1);
    assert.match(exploration.evidence_json, /"tool":"huis_status"/u);
    forceSchedulerDue();
    return exploration;
  };
  const terminalOutcomes = () => db.prepare(`SELECT * FROM timeline
    WHERE kind='contribution'
      AND json_extract(meta,'$.lifecycleContract')='ExplorationTerminalContribution/v1'
    ORDER BY id`).all();
  const stableExplorationContent = (row) => {
    const {
      revision: _revision,
      last_selected_ts: _lastSelected,
      scheduler_deferred_ts: _deferred,
      ...stable
    } = row;
    return stable;
  };

  let terminalExploration = await seedTerminalExploration(1001, 'stop');
  phase = 'exploration-terminal-stop';
  previousRun = autonomyStatus().last_run_ts;
  let terminalTurn = await trigger(previousRun);
  assert.deepEqual(terminalTurn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified, tool.changed,
  ]), [['verkenning_bijhouden', true, true, true]], JSON.stringify(terminalTurn));
  assert.equal(db.prepare('SELECT status FROM eva_explorations WHERE id=?')
    .get(terminalExploration.id).status, 'abandoned');
  assert.equal(terminalTurn.contribution ?? null, null);
  assert.equal(terminalOutcomes().length, 0);
  assert.equal(autonomyStatus().last_result.productive, false);
  assert.equal(autonomyStatus().last_result.progressed, false);

  resetAttention(1002);
  terminalExploration = startExploration({
    ...aspirationAnchorInput(1002, 'terminal-no-evidence'),
    question: 'Kan terminale modeltekst zonder eerdere toolreceipt een bijdrage worden?',
    whyNow: 'Deze adversarial isoleert de voorafgaande bewijsgrens.',
    hypothesis: 'Terminaal aangeleverd bewijs zonder eerdere receipt mag niet kwalificeren.',
    expectedObservation: 'Alleen reeds opgeslagen toolbewijs mag de bijdrage dragen.',
    stopRule: 'Rond één keer af en materialiseer zonder voorafgaand bewijs geen bijdrage.',
    maxActions: 2, topicKey: 'terminal_no_prior_evidence',
    topicAnchors: ['event:terminal-no-evidence', 'constraint:prior-receipt'],
    aspirationId: 1002, source: 'self',
  }).exploration;
  forceSchedulerDue();
  phase = 'exploration-terminal-no-evidence';
  previousRun = autonomyStatus().last_run_ts;
  terminalTurn = await trigger(previousRun);
  assert.deepEqual(terminalTurn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified, tool.changed,
  ]), [['verkenning_bijhouden', true, true, true]], JSON.stringify(terminalTurn));
  assert.equal(db.prepare('SELECT status FROM eva_explorations WHERE id=?')
    .get(terminalExploration.id).status, 'completed');
  assert.equal(terminalTurn.contribution ?? null, null,
    'terminale bewijsargumenten promoveren zichzelf nooit tot contribution');
  assert.equal(terminalOutcomes().length, 0);
  assert.equal(autonomyStatus().last_result.productive, false);

  resetAttention(1003);
  terminalExploration = startExploration({
    question: 'Kan een nagebootste evidence-row zonder receiptbinding een bijdrage worden?',
    whyNow: 'Deze adversarial toetst de private issued evidencegrens.',
    hypothesis: 'Een rij zonder cognitieve episode is geen authentieke toolreceipt.',
    expectedObservation: 'De terminale transaction weigert nagebootst bewijs volledig.',
    stopRule: 'Stop zodra de vervalste evidence-row fail-closed is geweigerd.',
    maxActions: 2, topicKey: 'terminal_forged_evidence',
    topicAnchors: ['event:terminal-forged', 'constraint:receipt-binding'],
    aspirationId: 1003, source: 'self',
  }).exploration;
  const forgedEvidence = 'Nagebootste huisstatus zonder echte episodebinding.';
  assert.equal(db.prepare(`UPDATE eva_explorations SET status='running',action_count=1,
    evidence_json=?,revision=revision+1 WHERE id=?`).run(JSON.stringify([{
    tool: 'huis_status', ok: true, verified: true, evidence: forgedEvidence,
    episode_id: null,
  }]), terminalExploration.id).changes, 1);
  terminalExploration = db.prepare('SELECT * FROM eva_explorations WHERE id=?')
    .get(terminalExploration.id);
  forceSchedulerDue();
  phase = 'exploration-terminal-forged';
  previousRun = autonomyStatus().last_run_ts;
  terminalTurn = await trigger(previousRun);
  assert.deepEqual(terminalTurn.tools.map((tool) => [tool.name, tool.ok, tool.verified]),
    [['verkenning_bijhouden', false, false]], JSON.stringify(terminalTurn));
  assert.deepEqual(stableExplorationContent(db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(terminalExploration.id)), stableExplorationContent(terminalExploration));
  assert.equal(terminalTurn.contribution ?? null, null);
  assert.equal(terminalOutcomes().length, 0);

  terminalExploration = await seedTerminalExploration(1004, 'stale');
  phase = 'exploration-terminal-stale';
  previousRun = autonomyStatus().last_run_ts;
  terminalTurn = await trigger(previousRun);
  assert.deepEqual(terminalTurn.tools, []);
  assert.equal(db.prepare('SELECT status FROM eva_explorations WHERE id=?')
    .get(terminalExploration.id).status, terminalExploration.status);
  assert.equal(terminalTurn.contribution ?? null, null);
  assert.equal(terminalOutcomes().length, 0);

  terminalExploration = await seedTerminalExploration(1005, 'race');
  phase = 'exploration-terminal-race';
  previousRun = autonomyStatus().last_run_ts;
  terminalTurn = await trigger(previousRun);
  assert.deepEqual(terminalTurn.tools.map((tool) => [tool.name, tool.ok, tool.verified]),
    [['verkenning_bijhouden', false, false]], JSON.stringify(terminalTurn));
  const racedTerminal = db.prepare('SELECT * FROM eva_explorations WHERE id=?')
    .get(terminalExploration.id);
  assert.match(racedTerminal.question, /Gelijktijdig gewijzigde terminalvraag/u);
  assert.equal(racedTerminal.status, terminalExploration.status);
  assert.equal(terminalTurn.contribution ?? null, null);
  assert.equal(terminalOutcomes().length, 0);

  terminalExploration = await seedTerminalExploration(1006, 'preempt');
  const preemptTerminalBefore = stableExplorationContent(terminalExploration);
  phase = 'exploration-terminal-preempt';
  previousRun = autonomyStatus().last_run_ts;
  terminalTurn = await trigger(previousRun);
  assert.equal(terminalTurn.yielded, true);
  assert.deepEqual(terminalTurn.tools, []);
  assert.ok(terminalPreemptPromise);
  await terminalPreemptPromise;
  expireSyntheticConversationPause();
  assert.deepEqual(stableExplorationContent(db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(terminalExploration.id)), preemptTerminalBefore,
  'preemptie vóór de synchrone CAS materialiseert geen terminal effect');
  assert.equal(terminalOutcomes().length, 0);

  terminalExploration = await seedTerminalExploration(1007, 'insert-failure');
  const insertionExplorationBefore = stableExplorationContent(terminalExploration);
  const insertionAspirationBefore = canonicalJson(db.prepare(
    'SELECT * FROM aspirations WHERE id=1007',
  ).get());
  const emittedTerminalContributions = [];
  const onTerminalTimeline = (row) => {
    if (row?.kind === 'contribution'
      && row?.meta?.lifecycleContract === 'ExplorationTerminalContribution/v1') {
      emittedTerminalContributions.push(row);
    }
  };
  bus.on('timeline', onTerminalTimeline);
  db.exec(`CREATE TEMP TRIGGER abort_terminal_contribution_insert
    BEFORE INSERT ON timeline WHEN NEW.kind='contribution'
      AND json_extract(NEW.meta,'$.lifecycleContract')='ExplorationTerminalContribution/v1'
    BEGIN SELECT RAISE(ABORT,'injected terminal contribution insert failure'); END;`);
  phase = 'exploration-terminal-insert-failure';
  previousRun = autonomyStatus().last_run_ts;
  try {
    terminalTurn = await trigger(previousRun);
  } finally {
    db.exec('DROP TRIGGER abort_terminal_contribution_insert');
    bus.off('timeline', onTerminalTimeline);
  }
  assert.deepEqual(terminalTurn.tools.map((tool) => [tool.name, tool.ok, tool.verified]),
    [['verkenning_bijhouden', false, false]], JSON.stringify(terminalTurn));
  assert.deepEqual(stableExplorationContent(db.prepare(
    'SELECT * FROM eva_explorations WHERE id=?',
  ).get(terminalExploration.id)), insertionExplorationBefore,
  'outcome-insertfout rolt de terminale exploration-CAS volledig terug');
  assert.equal(canonicalJson(db.prepare('SELECT * FROM aspirations WHERE id=1007').get()),
    insertionAspirationBefore, 'de aspiration-touch rolt in dezelfde transaction terug');
  assert.equal(terminalOutcomes().length, 0);
  assert.deepEqual(emittedTerminalContributions, [],
    'een teruggerolde outcome wordt nooit vóór outer commit op de bus gepubliceerd');

  terminalExploration = await seedTerminalExploration(10075, 'behavior-trial');
  const behaviorCountsBefore = {
    experiments: db.prepare('SELECT count(*) AS count FROM eva_behavior_experiments').get().count,
    work: db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    episodes: db.prepare('SELECT count(*) AS count FROM eva_goal_episodes').get().count,
  };
  phase = 'exploration-terminal-behavior-trial';
  previousRun = autonomyStatus().last_run_ts;
  terminalTurn = await trigger(previousRun);
  assert.deepEqual(terminalTurn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified, tool.changed,
  ]), [['verkenning_bijhouden', true, true, true]], JSON.stringify(terminalTurn));
  const behaviorOutcome = terminalOutcomes().find((row) => {
    const item = JSON.parse(row.meta);
    return Number(item.explorationId) === Number(terminalExploration.id);
  });
  assert.ok(behaviorOutcome);
  const behaviorMeta = JSON.parse(behaviorOutcome.meta);
  assert.equal(behaviorMeta.behaviorExperiment.contract, 'BehaviorLearningProposal/v2');
  assert.equal(behaviorMeta.behaviorExperiment.sourceKind, 'self_initiated');
  assert.equal(behaviorMeta.behaviorExperiment.sourceOutcomeKey, behaviorOutcome.outcome_key);
  const behaviorExperiment = db.prepare(`SELECT * FROM eva_behavior_experiments
    WHERE source_outcome_key=?`).get(behaviorOutcome.outcome_key);
  assert.ok(behaviorExperiment);
  assert.equal(behaviorExperiment.status, 'canary_pending');
  assert.equal(behaviorExperiment.revision, 2);
  assert.equal(behaviorExperiment.person_id,
    db.prepare("SELECT id FROM people WHERE lower(name)='jaap'").get().id);
  assert.equal(behaviorExperiment.lens_dimension, 'independent_voice');
  assert.equal(behaviorExperiment.lens_direction, 'increase');
  assert.equal(behaviorExperiment.context_scope, 'any_direct_reply');
  assert.deepEqual({
    experiments: db.prepare('SELECT count(*) AS count FROM eva_behavior_experiments').get().count,
    work: db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    episodes: db.prepare('SELECT count(*) AS count FROM eva_goal_episodes').get().count,
  }, {
    experiments: behaviorCountsBefore.experiments + 1,
    work: behaviorCountsBefore.work,
    episodes: behaviorCountsBefore.episodes,
  }, 'een gedragsproef mint geen werk, episode of externe authority');

  terminalExploration = await seedTerminalExploration(1008, 'economic-handoff');
  const economicCountsBefore = {
    work: db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    projects: db.prepare('SELECT count(*) AS count FROM eva_projects').get().count,
    episodes: db.prepare('SELECT count(*) AS count FROM eva_goal_episodes').get().count,
    origins: db.prepare('SELECT count(*) AS count FROM eva_episode_origins').get().count,
    events: db.prepare('SELECT count(*) AS count FROM eva_episode_events').get().count,
  };
  const economicWorkEvents = [];
  const economicTimelineEvents = [];
  const onEconomicWork = (row) => economicWorkEvents.push(row);
  const onEconomicTimeline = (row) => {
    if (row?.kind === 'contribution') {
      economicTimelineEvents.push(row);
      throw new Error('injected post-commit listener failure');
    }
  };
  bus.on('work_changed', onEconomicWork);
  bus.prependListener('timeline', onEconomicTimeline);
  phase = 'exploration-terminal-economic-handoff';
  previousRun = autonomyStatus().last_run_ts;
  try {
    terminalTurn = await trigger(previousRun);
  } finally {
    bus.off('timeline', onEconomicTimeline);
    bus.off('work_changed', onEconomicWork);
  }
  assert.deepEqual(terminalTurn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified, tool.changed,
  ]), [['verkenning_bijhouden', true, true, true]], JSON.stringify(terminalTurn));
  assert.equal(economicWorkEvents.length, 1);
  assert.equal(economicTimelineEvents.length, 1);
  const economicOutcome = terminalOutcomes().find((row) => (
    Object.hasOwn(JSON.parse(row.meta), 'economicHandoff')
  ));
  assert.ok(economicOutcome);
  const economicMeta = JSON.parse(economicOutcome.meta);
  assert.equal(economicMeta.economicHandoff.contract, 'ExplorationEconomicHandoff/v1');
  assert.deepEqual(economicMeta.economicHandoff.authority, {
    internalWork: true, familyDelivery: true,
    externalContact: false, publication: false, spend: false,
  });
  const economicWork = db.prepare(`SELECT * FROM eva_work_items
    WHERE json_extract(state_json,'$.exploration_economic_handoff.sourceOutcomeKey')=?`)
    .get(economicOutcome.outcome_key);
  assert.ok(economicWork);
  const economicEpisode = db.prepare(`SELECT e.*,g.goal_key,p.goal_id AS project_goal_id
    FROM eva_goal_episodes e JOIN eva_goals g ON g.id=e.goal_id
    JOIN eva_projects p ON p.id=e.project_id WHERE e.work_item_id=?`).get(economicWork.id);
  assert.equal(economicEpisode.goal_key, 'economic_value');
  assert.equal(economicEpisode.goal_id, economicEpisode.project_goal_id);
  assert.equal(economicEpisode.origin_kind, 'self');
  assert.equal(economicEpisode.origin_channel, 'autonomy');
  assert.equal(economicEpisode.delivery_policy, 'milestone');
  assert.equal(economicEpisode.delivery_target, null);
  assert.equal(economicWork.source, 'autonomy');
  assert.equal(economicWork.owner, null);
  assert.equal(economicWork.visible_to_family, 1);
  const economicState = JSON.parse(economicWork.state_json);
  assert.equal(economicState.exploration_economic_handoff.goalKey, 'economic_value');
  assert.deepEqual(economicState.exploration_economic_handoff.commitment, {
    title: 'Een eigen vreemde vondst duurzaam uitwerken',
    outcome: 'Een zelfgekozen kans is met intern bewijs toetsbaar uitgewerkt.',
    definitionOfDone: 'De uitkomst en grens zijn intern teruggelezen en controleerbaar.',
    nextAction: 'Werk eerst één interne bewijsstap uit zonder extern contact.',
  });
  assert.deepEqual({
    work: db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    projects: db.prepare('SELECT count(*) AS count FROM eva_projects').get().count,
    episodes: db.prepare('SELECT count(*) AS count FROM eva_goal_episodes').get().count,
  }, {
    work: economicCountsBefore.work + 1,
    projects: economicCountsBefore.projects + 1,
    episodes: economicCountsBefore.episodes + 1,
  });
  assert.equal(db.prepare(`SELECT count(*) AS count FROM timeline
    WHERE kind IN ('chat_out','telegram_out') AND id>?`).get(economicOutcome.id).count, 0);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM eva_work_items
    WHERE json_extract(state_json,'$.exploration_economic_handoff.contract')=
      'ExplorationEconomicHandoff/v1'`).get().count, 1);

  // Het economische vervolg blijft bestaan, maar mag de hieronder nog te
  // testen aspiration-selectie niet als open schedulerwerk overnemen.
  db.prepare("UPDATE eva_work_items SET status='done',lease_owner=NULL,lease_until=NULL WHERE id=?")
    .run(economicWork.id);
  db.prepare("UPDATE eva_goal_episodes SET state='closed',closed_ts=?,updated_ts=? WHERE id=?")
    .run(new Date().toISOString(), new Date().toISOString(), economicEpisode.id);

  // Een volledig door Eva zelf gekozen vrije exploration heeft bewust geen
  // aspiration_id. Zij mag desondanks op haar eigen bewijs een economisch
  // vervolg kiezen; de host mag daarbij geen aspiratie verzinnen of aanraken.
  db.prepare('DELETE FROM aspirations').run();
  db.prepare(`UPDATE eva_explorations SET status='completed',next_wake_ts=NULL
    WHERE status IN ('chosen','running','waiting','evaluating')`).run();
  db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  const freeEconomic = startExploration({
    question: 'Welke zelfgekozen kleine vondst verdient één intern economisch vervolg?',
    whyNow: 'De vrije aandacht leverde zonder voorafgaande aspiratie een toetsbaar spoor op.',
    hypothesis: 'Eén actuele huisread maakt het spoor begrensd beslisbaar.',
    expectedObservation: 'Een geverifieerde actuele status ondersteunt of verwerpt het spoor.',
    stopRule: 'Stop na deze ene bewijsstap en kies zelf wel of geen vervolg.',
    maxActions: 3, topicKey: 'free_economic_null_aspiration',
    topicAnchors: ['event:free-economic', 'constraint:no-host-aspiration'], source: 'self',
  }).exploration;
  assert.equal(freeEconomic.aspiration_id, null);
  const freeEvidence = 'De actuele huiswaarneming bevestigt één begrensd intern aanknopingspunt.';
  const freeEvidenceTs = new Date().toISOString();
  assert.equal(db.prepare(`UPDATE eva_explorations SET status='running',action_count=1,
      evidence_json=?,updated_ts=?,revision=revision+1 WHERE id=? AND revision=?`).run(
    JSON.stringify([{ ts: freeEvidenceTs, tool: 'huis_status', ok: true, verified: true,
      evidence: freeEvidence, episode_id: 991 }]),
    freeEvidenceTs, freeEconomic.id, freeEconomic.revision,
  ).changes, 1);
  const aspirationsBeforeFreeEconomic = canonicalJson(
    db.prepare('SELECT * FROM aspirations ORDER BY id').all(),
  );
  const freeEconomicCountsBefore = {
    work: db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    projects: db.prepare('SELECT count(*) AS count FROM eva_projects').get().count,
    episodes: db.prepare('SELECT count(*) AS count FROM eva_goal_episodes').get().count,
  };
  forceSchedulerDue();
  phase = 'exploration-terminal-economic-handoff';
  previousRun = autonomyStatus().last_run_ts;
  const freeEconomicTurn = await trigger(previousRun);
  assert.deepEqual(freeEconomicTurn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified, tool.changed,
  ]), [['verkenning_bijhouden', true, true, true]], JSON.stringify(freeEconomicTurn));
  const freeEconomicOutcome = terminalOutcomes().find((row) => (
    Number(JSON.parse(row.meta).explorationId) === Number(freeEconomic.id)
  ));
  assert.ok(freeEconomicOutcome);
  const freeEconomicMeta = JSON.parse(freeEconomicOutcome.meta);
  assert.equal(Object.hasOwn(freeEconomicMeta, 'aspirationId'), true);
  assert.equal(freeEconomicMeta.aspirationId, null);
  assert.equal(canonicalJson(db.prepare('SELECT * FROM aspirations ORDER BY id').all()),
    aspirationsBeforeFreeEconomic, 'een vrije economische keuze raakt geen aspiratierij');
  assert.deepEqual({
    work: db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    projects: db.prepare('SELECT count(*) AS count FROM eva_projects').get().count,
    episodes: db.prepare('SELECT count(*) AS count FROM eva_goal_episodes').get().count,
  }, {
    work: freeEconomicCountsBefore.work + 1,
    projects: freeEconomicCountsBefore.projects + 1,
    episodes: freeEconomicCountsBefore.episodes + 1,
  });
  const freeEconomicWork = db.prepare(`SELECT * FROM eva_work_items
    WHERE json_extract(state_json,'$.exploration_economic_handoff.sourceOutcomeKey')=?`)
    .get(freeEconomicOutcome.outcome_key);
  const freeEconomicEpisode = db.prepare(`SELECT e.*,g.goal_key
    FROM eva_goal_episodes e JOIN eva_goals g ON g.id=e.goal_id
    WHERE e.work_item_id=?`).get(freeEconomicWork.id);
  assert.equal(freeEconomicEpisode.goal_key, 'economic_value');
  assert.equal(db.prepare('SELECT aspiration_id FROM eva_explorations WHERE id=?')
    .get(freeEconomic.id).aspiration_id, null);
  const freeReplay = logContributionOutcome(
    freeEconomicOutcome.outcome_key, freeEconomicOutcome.content, freeEconomicMeta,
  );
  assert.equal(freeReplay.created, false);
  assert.equal(canonicalJson(db.prepare('SELECT * FROM aspirations ORDER BY id').all()),
    aspirationsBeforeFreeEconomic);
  db.prepare("UPDATE eva_work_items SET status='done',lease_owner=NULL,lease_until=NULL WHERE id=?")
    .run(freeEconomicWork.id);
  db.prepare("UPDATE eva_goal_episodes SET state='closed',closed_ts=?,updated_ts=? WHERE id=?")
    .run(new Date().toISOString(), new Date().toISOString(), freeEconomicEpisode.id);

  const terminalSurfaceCounts = () => ({
    messages: db.prepare('SELECT count(*) AS count FROM messages').get().count,
    work: db.prepare('SELECT count(*) AS count FROM eva_work_items').get().count,
    episodes: db.prepare('SELECT count(*) AS count FROM eva_goal_episodes').get().count,
    chatOut: db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='chat_out'").get().count,
    files: fs.readdirSync(process.env.EVA_DATA_DIR, { recursive: true }).sort(),
  });
  resetAttention(10);
  const terminalProviderAttemptsBefore = llmRuntimeStatus().provider_request_attempts;
  const terminalCallsBefore = terminalUnderstandingCalls;
  const successSelectionsBefore = dispatchedSelections.length;
  const terminalSurfacesBefore = terminalSurfaceCounts();
  phase = 'aspiration-anchor-terminal-success-seed';
  previousRun = autonomyStatus().last_run_ts;
  const terminalSeedTurn = await trigger(previousRun);
  terminalExploration = db.prepare(`SELECT * FROM eva_explorations
    WHERE aspiration_id=10 ORDER BY id DESC LIMIT 1`).get();
  assert.ok(terminalExploration);
  forceSchedulerDue();
  phase = 'exploration-terminal-success';
  previousRun = autonomyStatus().last_run_ts;
  const terminalSuccessTurn = await trigger(previousRun);
  assert.equal(llmRuntimeStatus().provider_request_attempts - terminalProviderAttemptsBefore, 2,
    'production-shaped aspiratie #10 gebruikt exact één understandingcall per beurt');
  assert.equal(terminalUnderstandingCalls - terminalCallsBefore, 1);
  assert.deepEqual(dispatchedSelections.slice(successSelectionsBefore).map((item) => (
    item.selection?.attention
  )), [
    { kind: 'aspiration', id: 10 },
    { kind: 'exploration', id: terminalExploration.id },
  ]);
  assert.deepEqual(terminalSeedTurn.tools.map((tool) => tool.name),
    ['verkenning_bijhouden', 'huis_status']);
  assert.deepEqual(terminalSuccessTurn.tools.map((tool) => [
    tool.name, tool.ok, tool.verified, tool.changed,
  ]), [['verkenning_bijhouden', true, true, true]], JSON.stringify(terminalSuccessTurn));
  assert.deepEqual({
    productive: autonomyStatus().last_result.productive,
    progressed: autonomyStatus().last_result.progressed,
    tool: autonomyStatus().last_result.tool,
    summary: autonomyStatus().last_result.summary,
  }, {
    productive: true, progressed: false, tool: 'verkenning_bijhouden',
    summary: 'De actuele huiswaarneming levert een begrensde, bruikbare conclusie op.',
  }, 'de detailed contribution wordt via de publieke schedulerprojectie productief');
  const terminalCompleted = db.prepare('SELECT * FROM eva_explorations WHERE id=?')
    .get(terminalExploration.id);
  assert.equal(terminalCompleted.status, 'completed');
  assert.equal(terminalCompleted.revision, terminalExploration.revision + 1);
  const outcomes = terminalOutcomes().filter((row) => (
    Number(JSON.parse(row.meta).explorationId) === Number(terminalExploration.id)
  ));
  assert.equal(outcomes.length, 1);
  const outcomeMeta = JSON.parse(outcomes[0].meta);
  const expectedTerminalOutcomeKey = createHash('sha256').update(canonicalJson({
    contract: 'ExplorationTerminalContribution/v1',
    explorationId: terminalExploration.id,
    fromRevision: terminalExploration.revision,
    toRevision: terminalExploration.revision + 1,
    terminalStatus: 'completed',
  })).digest('hex');
  assert.equal(outcomes[0].outcome_key, expectedTerminalOutcomeKey);
  assert.deepEqual({
    source: outcomeMeta.source,
    lastAction: outcomeMeta.lastAction,
    lifecycleContract: outcomeMeta.lifecycleContract,
    explorationId: outcomeMeta.explorationId,
    fromRevision: outcomeMeta.fromRevision,
    toRevision: outcomeMeta.toRevision,
    terminalStatus: outcomeMeta.terminalStatus,
    requirementId: outcomeMeta.requirementId,
  }, {
    source: 'bijdrage', lastAction: 'huis_status',
    lifecycleContract: 'ExplorationTerminalContribution/v1',
    explorationId: terminalExploration.id,
    fromRevision: terminalExploration.revision,
    toRevision: terminalExploration.revision + 1,
    terminalStatus: 'completed', requirementId: 'r1',
  });
  assert.match(outcomeMeta.understandingSha256, /^[a-f0-9]{64}$/u);
  assert.equal(autonomyStatus().last_result.productive, true);
  assert.equal(autonomyStatus().last_result.progressed, false);
  assert.equal(terminalSuccessTurn.progress ?? null, null);
  assert.equal(terminalSuccessTurn.noActionReason ?? null, null);
  assert.deepEqual(terminalSurfaceCounts(), terminalSurfacesBefore,
    'de terminale contribution maakt geen bericht, werk, delivery of bestand');
  const terminalStep = agentStatus().today_real_steps.filter((item) => (
    item.last_action === 'huis_status'
      && item.topic.includes('De actuele huiswaarneming levert')
  ));
  assert.equal(terminalStep.length, 1);

  const replayEvents = [];
  const onReplayTimeline = (row) => {
    if (row?.kind === 'contribution') replayEvents.push(row);
  };
  bus.on('timeline', onReplayTimeline);
  try {
    const replayOne = logContributionOutcome(
      outcomes[0].outcome_key, outcomes[0].content, outcomeMeta,
    );
    const replayTwo = logContributionOutcome(
      outcomes[0].outcome_key, outcomes[0].content, outcomeMeta,
    );
    assert.equal(replayOne.created, false);
    assert.equal(replayTwo.created, false);
  } finally {
    bus.off('timeline', onReplayTimeline);
  }
  assert.deepEqual(replayEvents, []);
  assert.equal(terminalOutcomes().length, 4,
    'durable replay publiceert of materialiseert geen tweede outcome');

  const authorityProbeKey = createHash('sha256').update(canonicalJson({
    contract: 'ContributionMutationAuthorityProbe/v1', turn: 'scheduler-focused',
  })).digest('hex');
  const authorityProbeContent = 'Callback-lokale outcome-authority proef.';
  const authorityProbeMeta = {
    contract: 'ContributionMutationAuthorityProbe/v1', syntheticTest: true,
  };
  const authorityProbeClaim = {
    outcomeKey: authorityProbeKey,
    content: authorityProbeContent,
    meta: { ...authorityProbeMeta, outcomeKey: authorityProbeKey },
  };
  let rolledBackAuthority = null;
  assert.throws(() => logContributionOutcome(
    authorityProbeKey, authorityProbeContent, authorityProbeMeta,
    ({ mutationAuthority }) => {
      rolledBackAuthority = mutationAuthority;
      throw new Error('injected_callback_rollback');
    },
  ), /injected_callback_rollback/u);
  assert.equal(db.prepare('SELECT count(*) AS count FROM timeline WHERE outcome_key=?')
    .get(authorityProbeKey).count, 0);
  assert.equal(db.transaction(() => consumeContributionMutationAuthority(
    rolledBackAuthority, authorityProbeClaim,
  )).immediate(), false, 'een uit de callback gevangen authority blijft na rollback ongeldig');

  let committedAuthority = null;
  const authorityRetry = logContributionOutcome(
    authorityProbeKey, authorityProbeContent, authorityProbeMeta,
    ({ mutationAuthority }) => {
      committedAuthority = mutationAuthority;
      assert.equal(consumeContributionMutationAuthority(
        mutationAuthority, authorityProbeClaim,
      ), true, 'de nieuwe callback krijgt één verse exact gebonden authority');
      assert.equal(consumeContributionMutationAuthority(
        mutationAuthority, authorityProbeClaim,
      ), false, 'dezelfde callback-authority is eenmalig');
      return { consumed: true };
    },
  );
  assert.equal(authorityRetry.created, true);
  assert.deepEqual(authorityRetry.effect, { consumed: true });
  assert.equal(db.transaction(() => consumeContributionMutationAuthority(
    committedAuthority, authorityProbeClaim,
  )).immediate(), false, 'na de callback kan de capability niet worden hergebruikt');

  const nestedProbeKey = createHash('sha256').update(canonicalJson({
    contract: 'ContributionMutationOuterTransactionProbe/v1',
  })).digest('hex');
  let nestedCallbackCalls = 0;
  assert.throws(() => db.transaction(() => logContributionOutcome(
    nestedProbeKey, 'Geneste outcome hoort niet te starten.', {
      contract: 'ContributionMutationOuterTransactionProbe/v1', syntheticTest: true,
    }, () => { nestedCallbackCalls += 1; },
  )).immediate(), /eigendom van de buitenste transactie/u);
  assert.equal(nestedCallbackCalls, 0);
  assert.equal(db.prepare('SELECT count(*) AS count FROM timeline WHERE outcome_key=?')
    .get(nestedProbeKey).count, 0);

  db.close();
}

async function terminalContributionRestartScenario() {
  const assert = (await import('node:assert/strict')).default;
  const { db } = await import('./src/db.js');
  const { selfContinuityContextText } = await import('./src/mind/social.js');
  const { replayExplorationEconomicHandoff } = await import('./src/mind/portfolio.js');
  const { replayBehaviorExperiment } = await import('./src/mind/learning.js');
  const rows = db.prepare(`SELECT * FROM timeline WHERE kind='contribution'
    AND json_extract(meta,'$.lifecycleContract')='ExplorationTerminalContribution/v1'`).all();
  assert.equal(rows.length, 4);
  const row = rows.find((candidate) => (
    !Object.hasOwn(JSON.parse(candidate.meta), 'economicHandoff')
      && !Object.hasOwn(JSON.parse(candidate.meta), 'behaviorExperiment')
  ));
  assert.ok(row);
  const meta = JSON.parse(row.meta);
  assert.equal(meta.source, 'bijdrage');
  assert.equal(meta.lastAction, 'huis_status');
  const context = selfContinuityContextText({ mode: 'autonomy' });
  assert.match(context, /De actuele huiswaarneming levert een begrensde/u);
  assert.match(context, /Patroon van de laatste zeven dagen: [^\n]*huis [1-9]/u);
  const economicRows = rows.filter((candidate) => (
    Object.hasOwn(JSON.parse(candidate.meta), 'economicHandoff')
  ));
  assert.equal(economicRows.length, 2);
  for (const economicRow of economicRows) {
    const economicMeta = JSON.parse(economicRow.meta);
    const replayed = replayExplorationEconomicHandoff(economicMeta.economicHandoff);
    assert.equal(replayed.created, false);
    assert.equal(replayed.handoff.sourceOutcomeKey, economicRow.outcome_key);
    assert.equal(db.prepare(`SELECT count(*) AS count FROM eva_work_items
      WHERE json_extract(state_json,'$.exploration_economic_handoff.sourceOutcomeKey')=?`)
      .get(economicRow.outcome_key).count, 1);
  }
  const behaviorRows = rows.filter((candidate) => (
    Object.hasOwn(JSON.parse(candidate.meta), 'behaviorExperiment')
  ));
  assert.equal(behaviorRows.length, 1);
  const behaviorMeta = JSON.parse(behaviorRows[0].meta);
  const replayedBehavior = replayBehaviorExperiment(behaviorMeta.behaviorExperiment);
  assert.equal(replayedBehavior.status, 'canary_pending');
  assert.equal(replayedBehavior.sourceOutcomeKey, behaviorRows[0].outcome_key);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM eva_behavior_experiments
    WHERE source_outcome_key=?`).get(behaviorRows[0].outcome_key).count, 1);
  db.close();
}

test('verse aspiration-start bindt exact één providerread aan haar nieuwe exploration', () => {
  const childDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-aspiration-provider-'));
  fs.writeFileSync(path.join(childDir, 'auth.json'), JSON.stringify({
    access_token: 'test-oauth-token', account_id: 'test-account',
    expires_at: Date.now() + 60 * 60_000,
  }));
  const result = spawnSync(process.execPath, ['--input-type=module'], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 20_000,
    input: `(${schedulerExplorationScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`,
    env: { ...process.env, EVA_DATA_DIR: childDir, EVA_AUTONOMY_ENABLED: 'true', EVA_AUTONOMY_LEVEL: 'full',
      EVA_TEST_STOP_AFTER_ASPIRATION_PROVIDER: '1',
      OPENAI_API_KEY: 'test-only', SLEEP_START: '00:00', WAKE_TIME: '00:00' },
  });
  fs.rmSync(childDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});

test('publieke scheduler bindt verse self-attention vóór tweede inhoudelijke actie', () => {
  const childDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-attention-exploration-'));
  fs.writeFileSync(path.join(childDir, 'auth.json'), JSON.stringify({
    access_token: 'test-oauth-token', account_id: 'test-account',
    expires_at: Date.now() + 60 * 60_000,
  }));
  const result = spawnSync(process.execPath, ['--input-type=module'], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 35_000,
    input: `(${schedulerExplorationScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`,
    env: { ...process.env, EVA_DATA_DIR: childDir, EVA_AUTONOMY_ENABLED: 'true', EVA_AUTONOMY_LEVEL: 'full',
      OPENAI_API_KEY: 'test-only', SLEEP_START: '00:00', WAKE_TIME: '00:00' },
  });
  const restart = result.status === 0 ? spawnSync(process.execPath, ['--input-type=module'], {
    cwd: process.cwd(), encoding: 'utf8', timeout: 15_000,
    input: `(${terminalContributionRestartScenario.toString()})().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); })`,
    env: { ...process.env, EVA_DATA_DIR: childDir, EVA_AUTONOMY_ENABLED: 'true', EVA_AUTONOMY_LEVEL: 'full',
      OPENAI_API_KEY: 'test-only', SLEEP_START: '00:00', WAKE_TIME: '00:00' },
  }) : null;
  fs.rmSync(childDir, { recursive: true, force: true });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.equal(restart?.status, 0, `${restart?.stdout || ''}\n${restart?.stderr || ''}`);
});
