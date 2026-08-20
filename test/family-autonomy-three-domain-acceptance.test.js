import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync('/tmp/bottie-family-autonomy-acceptance-');
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'family-autonomy-acceptance-test-only';
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.SLEEP_START = '00:00';
process.env.WAKE_TIME = '00:00';
process.env.HA_URL = 'http://homeassistant.acceptance.test';
process.env.HA_TOKEN = 'acceptance-test-only';
process.env.WEB_HOST = '127.0.0.1';
process.env.WEB_PORT = '0';
process.env.NODE_ENV = 'test';

const { config } = await import('../src/config.js');
const { addAspiration, db, kvSet } = await import('../src/db.js');
const { agentStatus } = await import('../src/mind/agent.js');
const { autonomyStatus, startAutonomy } = await import('../src/mind/autonomy.js');
const { renderAutonomyCard } = await import('../src/web/public/autonomycard.js');
const { startWeb } = await import('../src/web/server.js');

const SCHEDULER_KEY = 'eva_autonomy_scheduler_v1';
let server = null;

after(async () => {
  if (server) await new Promise((resolve) => server.close(resolve));
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function completed(output) {
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: {
      output, usage: { input_tokens: 800, output_tokens: 120, total_tokens: 920 },
    },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

function functionCall(name, arguments_, callId) {
  return completed([{
    type: 'function_call', name, call_id: callId,
    arguments: JSON.stringify(arguments_),
  }]);
}

function action(capabilityId, operationId, semanticInput, sourceSpan) {
  return {
    capabilityId, operationId, polarity: 'request', semanticInput,
    sourceSpans: [sourceSpan],
  };
}

function understanding(payload, actions, {
  durability, requestedEffects, requirementIds, decisionKind = 'act',
} = {}) {
  const sourceSpan = { start: 0, end: payload.turn.text.length };
  const projected = actions(sourceSpan);
  return {
    dialogueAct: 'command',
    goal: {
      statement: 'Kies en voltooi één concrete eigen gezinsbijdrage.',
      outcome: 'Eén actuele bron levert een bruikbaar en controleerbaar gezinsresultaat.',
      beneficiary: 'household',
    },
    conditions: [],
    provenance: { sourceSpans: [sourceSpan], contextHashes: [] },
    confidence: 0.99,
    actions: projected,
    requestedEffects,
    deniedEffects: ['external', 'voice', 'home'],
    temporal: { mode: 'now', startSourceSpans: [] },
    durability,
    delivery: { mode: 'silent', target: 'none' },
    proofNeeds: ['source_read'], ambiguities: [],
    firstDecision: { kind: decisionKind, requirementIds, replyDraft: null },
  };
}

function cardElements() {
  return {
    now: { textContent: '' }, why: { textContent: '' }, result: { textContent: '' },
  };
}

test('Eva rondt via één schedulerroute drie unieke gezinsresultaten uit drie domeinen af', async () => {
  fs.mkdirSync(config.workspaceDir, { recursive: true });
  const planningPath = path.join(config.workspaceDir, 'gezinsplanning.txt');
  fs.writeFileSync(planningPath,
    'De vaste boodschappenlijst bij vertrek voorkwam deze week twee dubbele ritten. '
    + 'De gezinsplanning gebruikt dezelfde boodschappenlijst voortaan voor vertrek.\n');

  const planningResult = 'De vaste boodschappenlijst bij vertrek voorkwam twee dubbele ritten en verbetert de gezinsplanning.';
  const planningFamily = {
    titel: 'Vaste boodschappenlijst voorkomt dubbele ritten',
    gezinsnut: 'Het gezin kan de vaste boodschappenlijst bij vertrek gebruiken om dubbele ritten te voorkomen.',
  };
  const planningHypothesis = 'De vaste boodschappenlijst bij vertrek voorkomt dubbele ritten in de gezinsplanning.';
  const planningObservation = 'De planningbron bevestigt boodschappenlijst, vertrek en voorkomen van dubbele ritten.';
  const domains = [{
    key: 'wooncomfort',
    aspirationText: 'Onderzoek één actuele verbetering voor rustig woonkamercomfort.',
    why: 'De actuele temperatuur kan een concrete keuze voor de avond ondersteunen.',
    question: 'Bevestigt de woonkamer temperatuur van 18 °C een rustig avondcomfort?',
    hypothesis: 'De woonkamer temperatuur van 18 °C ondersteunt een rustig avondcomfort.',
    expectedObservation: 'De Home Assistant woonkamer temperatuur toont 18 °C als actueel avondcomfort.',
    topicKey: 'woonkamer_avondcomfort_1',
    topicAnchors: ['entity:sensor.woonkamer_temperatuur', 'event:avondcomfort-meting'],
    read: {
      capabilityId: 'home.context.read', operationId: 'read',
      semanticInput: { query: 'woonkamer temperatuur avondcomfort' }, tool: 'huis_status',
    },
    result: 'De Home Assistant woonkamer temperatuur van 18 °C bevestigt rustig avondcomfort.',
    family: {
      titel: 'Woonkamer temperatuur ondersteunt avondcomfort',
      gezinsnut: 'Het gezin kan de woonkamer temperatuur van 18 °C gebruiken voor rustig avondcomfort.',
    },
  }, {
    key: 'planning',
    aspirationText: 'Onderzoek één praktische verbetering voor de boodschappenplanning.',
    why: 'De bestaande gezinsplanning bevat een concrete terugleesbare weekmeting.',
    question: 'Bevestigt de vaste boodschappenlijst bij vertrek dat dubbele ritten worden voorkomen?',
    hypothesis: planningHypothesis,
    expectedObservation: planningObservation,
    topicKey: 'planning_boodschappenlijst_1',
    topicAnchors: ['event:planning-weekmeting-een', 'constraint:vertrek-lijst-een'],
    read: {
      capabilityId: 'workspace.file.read', operationId: 'read',
      semanticInput: { pad: 'data/workspace/gezinsplanning.txt' }, tool: 'lees_bestand',
    },
    result: planningResult,
    family: planningFamily,
  }, {
    key: 'onderzoek',
    aspirationText: 'Onderzoek één actuele bron voor comfortabel omgaan met hitte.',
    why: 'Een actuele publieke bron kan vandaag een concrete gezinskeuze ondersteunen.',
    question: 'Bevestigt de actuele KNMI hittewaarschuwing schaduw en drinkpauzes voor gezinnen?',
    hypothesis: 'De actuele KNMI hittewaarschuwing adviseert schaduw en regelmatige drinkpauzes.',
    expectedObservation: 'Een actuele webbron noemt KNMI hittewaarschuwing, schaduw en drinkpauzes.',
    topicKey: 'actuele_knmi_hittewaarschuwing_1',
    topicAnchors: ['event:knmi-hittewaarschuwing', 'constraint:schaduw-drinkpauze'],
    read: {
      capabilityId: 'internet.research', operationId: 'search',
      semanticInput: {
        vraag: 'Welke actuele KNMI hittewaarschuwing adviseert schaduw en drinkpauzes voor gezinnen?',
      },
      tool: 'zoek_internet',
    },
    result: 'De actuele KNMI hittewaarschuwing adviseert gezinnen schaduw en regelmatige drinkpauzes.',
    family: {
      titel: 'KNMI hittewaarschuwing adviseert schaduw en drinkpauzes',
      gezinsnut: 'Het gezin kan bij de actuele hittewaarschuwing schaduw en regelmatige drinkpauzes plannen.',
    },
  }, {
    key: 'planning-duplicate',
    aspirationText: 'Controleer de boodschappenplanning nog één keer op een nieuwe gezinsvondst.',
    why: 'Een tweede actuele teruglezing toetst of dezelfde conclusie werkelijk nieuw is.',
    question: 'Bevestigt nu de vaste boodschappenlijst bij vertrek dat dubbele ritten worden voorkomen?',
    hypothesis: planningHypothesis,
    expectedObservation: planningObservation,
    topicKey: 'planning_boodschappenlijst_2',
    topicAnchors: ['event:planning-weekmeting-twee', 'constraint:vertrek-lijst-twee'],
    read: {
      capabilityId: 'workspace.file.read', operationId: 'read',
      semanticInput: { pad: 'data/workspace/gezinsplanning.txt' }, tool: 'lees_bestand',
    },
    result: planningResult,
    family: planningFamily,
    duplicate: true,
  }];

  db.prepare('DELETE FROM aspirations').run();
  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare("UPDATE eva_goals SET next_review_ts='2099-01-01T00:00:00.000Z'").run();
  for (const [index, domain] of domains.entries()) {
    domain.aspirationId = addAspiration({
      text: domain.aspirationText, why: domain.why,
      kind: 'development', goalKey: `family_acceptance_${domain.key}`,
      beneficiary: 'het gezin', cadenceHours: 1, priority: 1,
    });
    db.prepare('UPDATE aspirations SET last_touched=? WHERE id=?').run(
      new Date(Date.UTC(2020, 0, index + 1)).toISOString(), domain.aspirationId,
    );
  }
  kvSet(SCHEDULER_KEY, {
    next_run_ts: new Date(Date.now() - 1_000).toISOString(),
    pending_signals: [], daily_used_ms: 0, daily_productive_ms: 0,
    daily_preempted_ms: 0,
  });

  server = await startWeb();
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const baseUrl = `http://127.0.0.1:${address.port}`;
  const originalFetch = globalThis.fetch;
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  const timers = [];
  let haReads = 0;
  let webReads = 0;
  let fileReadReceipts = 0;
  let understandingCalls = 0;
  let providerBarrierCount = 0;
  let providerBarrier = null;
  const holdUnderstandingResponse = (response) => new Promise((resolve) => {
    assert.equal(providerBarrier, null, 'iedere schedulerbeurt heeft één providerbarrière');
    providerBarrierCount += 1;
    const barrier = {
      release() {
        assert.equal(providerBarrier, barrier);
        providerBarrier = null;
        resolve(response);
      },
    };
    providerBarrier = barrier;
  });

  globalThis.setTimeout = (callback, delay, ...args) => {
    const scheduler = /\btick\(\)/u.test(String(callback));
    if (!scheduler) return originalSetTimeout(callback, delay, ...args);
    const handle = {
      callback, delay, args, scheduler: true, cancelled: false, fired: false,
      ref() { return this; }, unref() { return this; }, hasRef() { return false; },
    };
    timers.push(handle);
    return handle;
  };
  globalThis.clearTimeout = (handle) => {
    if (handle?.scheduler === true) handle.cancelled = true;
    else originalClearTimeout(handle);
  };

  globalThis.fetch = async (input, options = {}) => {
    const url = String(input);
    if (url.startsWith(config.haUrl)) {
      haReads += 1;
      assert.ok(url.endsWith('/api/states'), url);
      return new Response(JSON.stringify([{
        entity_id: 'sensor.woonkamer_temperatuur', state: '18.0',
        last_updated: new Date().toISOString(),
        attributes: {
          friendly_name: 'Woonkamer temperatuur avondcomfort', unit_of_measurement: '°C',
        },
      }]), { status: 200, headers: { 'content-type': 'application/json' } });
    }

    const request = JSON.parse(String(options.body || '{}'));
    if (request.tools?.some((tool) => String(tool?.type || '').startsWith('web_search'))) {
      webReads += 1;
      assert.equal(request.input?.[0]?.content?.[0]?.text,
        domains[2].read.semanticInput.vraag);
      return completed([
        { type: 'web_search_call', id: 'family-acceptance-web-search', status: 'completed',
          action: { type: 'search', query: domains[2].read.semanticInput.vraag } },
        { type: 'message', role: 'assistant', content: [{ type: 'output_text',
          text: 'De actuele KNMI hittewaarschuwing adviseert gezinnen schaduw en regelmatige drinkpauzes. https://www.knmi.nl/waarschuwingen' }] },
      ]);
    }

    assert.equal(request.tools?.[0]?.name, 'commit_turn_understanding',
      `onverwachte modelroute: ${JSON.stringify(request.tools || [])}`);
    understandingCalls += 1;
    const payload = JSON.parse(request.input[0].content[0].text);
    const attention = payload.context.selfAttention;
    if (attention.kind === 'aspiration') {
      const domain = domains.find((candidate) => candidate.aspirationId === attention.id);
      assert.ok(domain, `onbekende aspiration ${attention.id}`);
      const start = (span) => action('exploration.manage', 'start', {
          vraag: domain.question,
          aanleiding: domain.why,
          hypothese: domain.hypothesis,
          verwachte_waarneming: domain.expectedObservation,
          stopregel: 'Stop na deze ene geverifieerde bronread en beoordeel exact één resultaat.',
          max_acties: 2,
          onderwerp_sleutel: domain.topicKey,
          onderwerp_ankers: domain.topicAnchors,
          aspiratie_id: domain.aspirationId,
        }, span);
      const research = domain.key === 'onderzoek';
      return holdUnderstandingResponse(functionCall('commit_turn_understanding', understanding(payload, (span) => [
        start(span),
        ...(research ? [] : [action(domain.read.capabilityId, domain.read.operationId,
          domain.read.semanticInput, span)]),
      ], research ? {
        durability: 'turn', requestedEffects: ['persistent'], requirementIds: ['r1'],
      } : {
        durability: 'episode', requestedEffects: ['persistent', 'read'],
        requirementIds: ['r1', 'r2'],
      }), `start-${domain.key}`));
    }
    assert.equal(attention.kind, 'exploration');
    const exploration = db.prepare('SELECT * FROM eva_explorations WHERE id=?')
      .get(attention.exploration.id);
    assert.ok(exploration);
    const domain = domains.find((candidate) => candidate.aspirationId === exploration.aspiration_id);
    assert.ok(domain, `exploration ${exploration.id} mist acceptatiedomein`);
    const evidence = JSON.parse(exploration.evidence_json || '[]');
    if (domain.key === 'onderzoek' && exploration.action_count === 0 && evidence.length === 0) {
      return holdUnderstandingResponse(functionCall('commit_turn_understanding', understanding(payload, (span) => [
        action(domain.read.capabilityId, domain.read.operationId,
          domain.read.semanticInput, span),
      ], {
        durability: 'turn', requestedEffects: ['read'], requirementIds: ['r1'],
        decisionKind: 'inspect',
      }), `read-${domain.key}`));
    }
    if (domain.key === 'onderzoek') {
      assert.equal(exploration.action_count, 1);
      assert.equal(evidence.length, 1);
    }
    return holdUnderstandingResponse(functionCall('commit_turn_understanding', understanding(payload, (span) => [
      action('exploration.manage', 'afronden', {
        id: attention.exploration.id,
        verwachte_revisie: attention.exploration.revision,
        resultaat: domain.result,
        besluit: 'Gebruik deze bronconclusie alleen zolang een nieuwe actuele read haar bevestigt.',
        gezinsresultaat: domain.family,
      }, span),
    ], {
      durability: 'turn', requestedEffects: ['persistent'], requirementIds: ['r1'],
    }), `complete-${domain.key}`));
  };

  const forceDue = () => {
    const stored = JSON.parse(db.prepare('SELECT value FROM kv WHERE key=?')
      .get(SCHEDULER_KEY).value);
    kvSet(SCHEDULER_KEY, {
      ...stored, next_run_ts: new Date(Date.now() - 1_000).toISOString(),
    });
  };

  const trigger = async (expectedAttention) => {
    const timer = timers.find((candidate) => (
      candidate.scheduler && !candidate.cancelled && !candidate.fired
    ));
    assert.ok(timer, 'startAutonomy moet iedere beurt opnieuw met haar echte timer bewapenen');
    timer.fired = true;
    timer.callback(...timer.args);
    const deadline = Date.now() + 10_000;
    while ((!autonomyStatus().running || !providerBarrier) && Date.now() < deadline) {
      await new Promise((resolve) => originalSetTimeout(resolve, 10));
    }
    assert.ok(providerBarrier, 'de echte understanding-response moet test-only blijven wachten');
    const activeStatus = await apiStatus();
    assert.equal(activeStatus.autonomy.running, true);
    assert.deepEqual(activeStatus.autonomy.current_selection?.attention, expectedAttention);
    const selected = expectedAttention.kind === 'aspiration'
      ? db.prepare('SELECT text AS choice,why AS reason FROM aspirations WHERE id=?')
        .get(expectedAttention.id)
      : db.prepare(`SELECT question AS choice,why_now AS reason
        FROM eva_explorations WHERE id=?`).get(expectedAttention.id);
    assert.ok(selected);
    const activeElements = cardElements();
    renderAutonomyCard(activeElements, activeStatus);
    assert.equal(activeElements.now.textContent, `Nu gekozen: ${selected.choice}`);
    assert.equal(activeElements.why.textContent, `Waarom: ${selected.reason}`);
    const barrier = providerBarrier;
    barrier.release();
    while (autonomyStatus().running && Date.now() < deadline) {
      await new Promise((resolve) => originalSetTimeout(resolve, 10));
    }
    await new Promise((resolve) => originalSetTimeout(resolve, 0));
    const completedStatus = await apiStatus();
    assert.equal(completedStatus.autonomy.running, false);
    assert.equal(completedStatus.autonomy.display.choice_label, null);
    assert.equal(completedStatus.autonomy.display.reason_label, null);
    const completedElements = cardElements();
    renderAutonomyCard(completedElements, completedStatus);
    assert.equal(completedElements.now.textContent, '');
    assert.equal(completedElements.why.textContent, '');
    return {
      selection: activeStatus.autonomy.current_selection,
      turn: agentStatus().last_autonomy_turn,
    };
  };

  const apiStatus = async () => {
    const response = await originalFetch(`${baseUrl}/api/workstatus`);
    assert.equal(response.status, 200);
    return response.json();
  };

  const assertUiResult = async (expected, terminal, explorationId) => {
    const status = await apiStatus();
    const evidence = status.autonomy.last_result?.summary || expected;
    const matchingContribution = db.prepare(`SELECT content,meta,outcome_key FROM timeline
      WHERE kind='contribution' AND json_extract(meta,'$.evidence')=?
      ORDER BY id DESC LIMIT 1`).get(evidence);
    const explorationState = db.prepare(`SELECT evidence_json,status,revision
      FROM eva_explorations WHERE id=?`).get(explorationId);
    const diagnostic = {
      last_result: status.autonomy.last_result,
      turn_contribution: terminal?.turn?.contribution || null,
      matching_contribution: matchingContribution || null,
      exploration: explorationState || null,
    };
    assert.equal(status.autonomy.display.result_label, expected, JSON.stringify(diagnostic));
    const elements = cardElements();
    renderAutonomyCard(elements, status);
    assert.equal(elements.result.textContent,
      expected ? `Laatste resultaat: ${expected}` : '');
  };

  const completedExplorations = [];
  try {
    startAutonomy();
    for (const [index, domain] of domains.entries()) {
      if (index > 0) forceDue();
      const seeded = await trigger({ kind: 'aspiration', id: domain.aspirationId });
      assert.deepEqual(seeded.selection?.attention, {
        kind: 'aspiration', id: domain.aspirationId,
      });
      assert.deepEqual(seeded.turn.tools.map((tool) => tool.name), domain.key === 'onderzoek'
        ? ['verkenning_bijhouden'] : ['verkenning_bijhouden', domain.read.tool],
      JSON.stringify(seeded.turn));
      let exploration = db.prepare(`SELECT * FROM eva_explorations
        WHERE aspiration_id=? ORDER BY id DESC LIMIT 1`).get(domain.aspirationId);
      assert.ok(exploration);
      assert.equal(exploration.action_count, domain.key === 'onderzoek' ? 0 : 1);

      if (domain.key !== 'onderzoek') {
        const evidence = JSON.parse(exploration.evidence_json || '[]');
        assert.equal(evidence.length, 1);
        assert.deepEqual([evidence[0].tool, evidence[0].ok, evidence[0].verified],
          [domain.read.tool, true, true]);
        if (domain.read.tool === 'huis_status') {
          assert.match(evidence[0].evidence,
            /Woonkamer temperatuur avondcomfort.*sensor\.woonkamer_temperatuur.*18\.0\s*°C/iu,
            'het opgeslagen HA-bewijs moet de werkelijk geselecteerde entiteit en waarde dragen');
        }
        assert.match(evidence[0].receipt?.outputSha256 || '', /^[a-f0-9]{64}$/u);
        if (domain.read.tool === 'lees_bestand') fileReadReceipts += 1;
      }

      if (domain.key === 'onderzoek') {
        forceDue();
        const researched = await trigger({ kind: 'exploration', id: exploration.id });
        assert.deepEqual(researched.selection?.attention, {
          kind: 'exploration', id: exploration.id,
        });
        assert.deepEqual(researched.turn.tools.map((tool) => tool.name),
          ['zoek_internet'], JSON.stringify(researched.turn));
        exploration = db.prepare('SELECT * FROM eva_explorations WHERE id=?')
          .get(exploration.id);
        assert.equal(exploration.action_count, 1);
        const evidence = JSON.parse(exploration.evidence_json || '[]');
        assert.equal(evidence.length, 1);
        assert.deepEqual([evidence[0].tool, evidence[0].ok, evidence[0].verified],
          ['zoek_internet', true, true]);
        assert.match(evidence[0].receipt?.outputSha256 || '', /^[a-f0-9]{64}$/u);
      }

      forceDue();
      const terminal = await trigger({ kind: 'exploration', id: exploration.id });
      assert.deepEqual(terminal.selection?.attention, {
        kind: 'exploration', id: exploration.id,
      });
      assert.deepEqual(terminal.turn.tools.map((tool) => tool.name),
        ['verkenning_bijhouden'], JSON.stringify(terminal.turn));
      completedExplorations.push(exploration.id);
      await assertUiResult(domain.duplicate ? null : domain.result, terminal, exploration.id);
      const visibleCount = db.prepare(`SELECT count(*) AS count FROM timeline
        WHERE kind='contribution' AND json_extract(meta,'$.familyVisible')=1`).get().count;
      assert.equal(visibleCount, Math.min(index + 1, 3));
    }
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }

  assert.equal(understandingCalls, 9, 'iedere schedulerbeurt gebruikt één echte understanding');
  assert.equal(providerBarrierCount, 9,
    'alle negen understanding-responses zijn tijdens de zichtbaarheidstest vastgehouden');
  assert.equal(haReads, 1);
  assert.equal(webReads, 1);
  assert.equal(fileReadReceipts, 2);
  const evidenceExecutions = db.prepare(`SELECT tool,status FROM turn_tool_executions
    WHERE tool IN ('huis_status','lees_bestand','zoek_internet') ORDER BY execution_seq`).all();
  assert.deepEqual(evidenceExecutions.map((row) => row.tool),
    ['huis_status', 'zoek_internet']);
  assert.ok(evidenceExecutions.every((row) => row.status === 'done'));

  const outcomes = db.prepare(`SELECT * FROM timeline
    WHERE kind='contribution'
      AND json_extract(meta,'$.lifecycleContract')='ExplorationTerminalContribution/v1'
    ORDER BY id`).all().map((row) => ({ ...row, meta: JSON.parse(row.meta) }));
  assert.equal(outcomes.length, 4);
  const visible = outcomes.filter((row) => row.meta.familyVisible === true);
  assert.equal(visible.length, 3);
  assert.deepEqual(visible.map((row) => row.meta.lastAction),
    ['huis_status', 'lees_bestand', 'zoek_internet']);
  assert.deepEqual(visible.map((row) => row.meta.evidence),
    domains.slice(0, 3).map((domain) => domain.result));
  const outcomeKeys = visible.map((row) => row.outcome_key);
  assert.ok(outcomeKeys.every((key) => /^[a-f0-9]{64}$/u.test(key)));
  assert.equal(new Set(outcomeKeys).size, 3);
  const noveltyClaims = visible.map((row) => row.meta.familyResult?.noveltyClaimSha256);
  assert.ok(noveltyClaims.every((claim) => /^[a-f0-9]{64}$/u.test(claim)));
  assert.equal(new Set(noveltyClaims).size, 3);
  for (const row of visible) {
    assert.equal(row.meta.familyResult.sourceOutcomeKey, row.outcome_key);
  }
  const duplicate = outcomes.find((row) => (
    Number(row.meta.explorationId) === Number(completedExplorations.at(-1))
  ));
  assert.ok(duplicate);
  assert.equal(duplicate.meta.internalOnly, true);
  assert.equal(duplicate.meta.familyVisible ?? false, false);
  assert.equal(Object.hasOwn(duplicate.meta, 'familyResult'), false);
  assert.equal((await apiStatus()).autonomy.display.result_label, null,
    'de vierde semantisch gelijke claim publiceert niet en maskeert oudere zichtbaarheid');
});
