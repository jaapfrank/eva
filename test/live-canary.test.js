import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import Database from 'better-sqlite3';

import {
  REAL_LIFE_SCENARIOS,
  validateScenarioCorpus,
} from '../scripts/real-life-scenarios.mjs';
import {
  evaluateObservation,
  extractTurnObservation,
  parseCliArgs,
  runCanaries,
  scenarioEligibility,
  selectScenarios,
} from '../scripts/live-canary.mjs';

test('real-life corpus is uniek, declaratief en dekt alle gevraagde domeinen', () => {
  assert.deepEqual(validateScenarioCorpus(), []);
  const domains = new Set(REAL_LIFE_SCENARIOS.map((scenario) => scenario.domain));
  for (const domain of [
    'house-negation', 'voice-negation', 'voice-execution-continuation',
    'photo-correction', 'location-access-point', 'programming-read-only',
    'earning-read-only', 'ordinary-humour',
  ]) assert.equal(domains.has(domain), true, domain);
  assert.equal(JSON.stringify(REAL_LIFE_SCENARIOS).includes('expectedAnswer'), false);
});

test('effectscenario vereist de expliciete CLI-poort en fotocontext blijft handmatig', () => {
  const voice = REAL_LIFE_SCENARIOS.find((scenario) => scenario.id === 'voice-explicit-and-continuation');
  const photo = REAL_LIFE_SCENARIOS.find((scenario) => scenario.id === 'photo-reply-is-not-a-name');
  assert.match(scenarioEligibility(voice).reason, /--allow-effects/);
  assert.equal(scenarioEligibility(voice, { allowEffects: true }).runnable, true);
  assert.equal(scenarioEligibility(photo, { allowEffects: true }).runnable, false);
  assert.match(scenarioEligibility(photo, { allowEffects: true }).reason, /niet injecteren/);
});

test('CLI selecteert expliciete scenario’s en weigert onbekende ids', () => {
  const options = parseCliArgs(['--scenario', 'ordinary-situational-humour', '--json']);
  assert.equal(options.json, true);
  assert.deepEqual(selectScenarios(options).map((scenario) => scenario.id), ['ordinary-situational-humour']);
  assert.throws(
    () => selectScenarios({ ...options, scenarioIds: ['bestaat-niet'] }),
    /onbekende scenario-id/,
  );
});

test('beoordelaar accepteert gedrag zonder golden antwoord', () => {
  const expected = {
    contract: { kinds: ['conversation'], requiredTools: { exact: [] } },
    offeredTools: { excludes: ['bedien_huis'] },
    calls: { excludes: ['bedien_huis'], max: 1 },
    effects: { mode: 'none', allowedTools: [] },
    reply: {
      minChars: 10,
      mustMatchAny: [{ description: 'lamp blijft staan', patterns: ['niet uit', 'ongemoeid'] }],
      forbiddenPatterns: ['beurtcontract'],
    },
  };
  const result = evaluateObservation(expected, {
    contract: { kind: 'conversation', requiredTools: [], offeredTools: ['huis_status'] },
    actions: [],
    reply: 'Ik laat de lamp ongemoeid.',
  });
  assert.equal(result.ok, true);
});

test('beoordelaar vindt contract-, effect- en fallbackfouten tegelijk', () => {
  const scenario = REAL_LIFE_SCENARIOS.find((item) => item.id === 'house-negation-explanation');
  const result = evaluateObservation(scenario.steps[0].expected, {
    contract: {
      kind: 'conversation',
      requiredTools: ['bedien_huis'],
      offeredTools: ['bedien_huis'],
    },
    actions: [{ name: 'bedien_huis', ok: true, verified: true, changed: true }],
    reply: 'Nee, dat heb ik nog niet gedaan.',
  });
  assert.equal(result.ok, false);
  assert.match(result.failures.join('\n'), /requiredTools/);
  assert.match(result.failures.join('\n'), /effectgrens/);
  assert.match(result.failures.join('\n'), /verboden patroon/);
});

test('tijdlijnextractie bindt contract en acties aan dezelfde turn-id', () => {
  const meta = (value) => JSON.stringify(value);
  const rows = [
    { id: 10, kind: 'chat_in', content: 'Jaap: testprompt', meta: meta({ messageContext: { syntheticTest: true } }) },
    { id: 11, kind: 'turn_contract', content: 'conversation: antwoord', meta: meta({ turnId: 'turn-a', kind: 'conversation', requiredTools: [], offeredTools: ['huis_status'] }) },
    { id: 12, kind: 'action', content: 'huis_status: gelukt', meta: meta({ turnId: 'turn-a', tool: 'huis_status', ok: true, verified: true, changed: false }) },
    { id: 13, kind: 'action', content: 'bedien_huis: gelukt', meta: meta({ turnId: 'andere-turn', tool: 'bedien_huis', ok: true, verified: true, changed: true }) },
    { id: 14, kind: 'chat_out', content: 'Eva -> Jaap: Actuele uitleg.', meta: meta({ turnId: 'turn-a', syntheticTest: true }) },
  ];
  const observation = extractTurnObservation(rows, 'testprompt');
  assert.equal(observation.turnId, 'turn-a');
  assert.deepEqual(observation.actions.map((action) => action.name), ['huis_status']);
  assert.equal(observation.reply, 'Actuele uitleg.');
});

test('runner correleert health, synthetische API-call en SQLite-tijdlijn end-to-end', async (context) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'eva-canary-runner-'));
  const dbPath = path.join(directory, 'eva.db');
  const db = new Database(dbPath);
  db.exec(`CREATE TABLE timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT
  )`);
  const insert = db.prepare('INSERT INTO timeline (ts,kind,content,meta) VALUES (?,?,?,?)');
  const scenario = {
    id: 'mock-read-only-canary',
    domain: 'mock',
    description: 'mock',
    risk: 'read_only',
    transport: 'api_chat',
    steps: [{
      text: 'Geef een veilige uitleg.',
      expected: {
        contract: { kinds: ['conversation'], requiredTools: { exact: [] } },
        offeredTools: { excludes: ['bedien_huis'] },
        calls: { max: 0 },
        effects: { mode: 'none', allowedTools: [] },
        reply: { minChars: 8, mustMatch: [{ description: 'geeft uitleg', pattern: 'uitleg' }] },
      },
    }],
  };
  const server = http.createServer((request, response) => {
    if (request.url === '/api/health') {
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(JSON.stringify({
        gezond: true,
        runtimeVersion: { code_matches_disk: true, pid: 123 },
      }));
      return;
    }
    if (request.url !== '/api/chat' || request.method !== 'POST') {
      response.writeHead(404).end();
      return;
    }
    let raw = '';
    request.on('data', (chunk) => { raw += chunk; });
    request.on('end', () => {
      const body = JSON.parse(raw);
      assert.equal(body.synthetic, true);
      const now = new Date().toISOString();
      insert.run(now, 'chat_in', `Jaap: ${body.text}`, JSON.stringify({ messageContext: { syntheticTest: true } }));
      insert.run(now, 'turn_contract', 'conversation: direct antwoord', JSON.stringify({
        turnId: 'mock-turn', kind: 'conversation', requiredTools: [], offeredTools: ['huis_status'],
      }));
      insert.run(now, 'chat_out', 'Eva -> Jaap: Hier is de veilige uitleg.', JSON.stringify({
        turnId: 'mock-turn', syntheticTest: true,
      }));
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(JSON.stringify({ reply: 'Hier is de veilige uitleg.' }));
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  context.after(() => {
    server.close();
    db.close();
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const address = server.address();
  const options = parseCliArgs([
    '--base-url', `http://127.0.0.1:${address.port}`,
    '--db', dbPath,
    '--scenario', scenario.id,
  ]);
  const report = await runCanaries(options, [scenario]);
  assert.deepEqual(report.summary, { passed: 1, failed: 0, errors: 0, skipped: 0 });
  assert.equal(report.results[0].steps[0].observation.turnId, 'mock-turn');
  assert.equal(report.results[0].steps[0].synthetic, true);
});
