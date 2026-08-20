import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-emotional-life-'));
process.env.EVA_DATA_DIR = dataDir;

const { bus } = await import('../src/bus.js');
const { db } = await import('../src/db.js');
const {
  getMood,
  interactionFeedback,
  moodActionGuidance,
  moodSummary,
  setMood,
  startEmotionalLife,
} = await import('../src/mind/emotions.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('expressieve stemming volgt de gedeelde gegronde regulatietoestand', () => {
  startEmotionalLife({ decayMinutes: 5 });
  const before = getMood();
  bus.emit('cognitive_state', {
    regulation: {
      load: 0.8,
      uncertainty: 0.9,
      connection: 0.9,
      competence: 0.4,
      novelty: 0.5,
    },
  });
  const appraised = getMood();
  assert.ok(appraised.stress > before.stress);
  assert.ok(appraised.energie < before.energie);
  assert.ok(appraised.eenzaamheid < before.eenzaamheid);
});

test('alleen korte duidelijke reacties gelden als feedback en stemming verhoogt nooit mandaat', () => {
  assert.equal(interactionFeedback('Alweer'), 'correctie');
  assert.equal(interactionFeedback('Goed gedaan!'), 'warmte');
  assert.equal(interactionFeedback('Controleer of de goede lamp aanstaat en zet hem anders uit.'), null);
  assert.match(moodActionGuidance({ stress: 0.8, energie: 0.8 }), /klein en rustig/);
  assert.doesNotMatch(moodActionGuidance({ stress: 0.1, energie: 0.8 }), /toestemming|mandaat verhogen/i);
});

test('normale gegronde stemming blijft als zelfkennis over een procesgrens', () => {
  const snapshot = {
    geluk: 0.82,
    energie: 0.58,
    nieuwsgierigheid: 0.74,
    eenzaamheid: 0.2,
    stress: 0.2,
  };
  setMood(snapshot, 'procesgrens-test');
  const summary = moodSummary(snapshot);
  const guidance = moodActionGuidance(snapshot);
  assert.equal(summary, 'vrolijk, redelijk energiek, heel nieuwsgierig');
  assert.equal(moodSummary({ stress: snapshot.stress }), summary);
  assert.ok(guidance.includes(summary));
  assert.match(guidance, /loopt door tussen beurten/i);
  assert.match(guidance, /verzin geen oorzaak of lichamelijke ervaring/i);
  assert.notEqual(guidance, moodActionGuidance({ ...snapshot, geluk: 0.2, energie: 0.2 }));
  assert.doesNotMatch(guidance, /toestemming|mandaat|bevoegd/i);

  const moduleUrl = new URL('../src/mind/emotions.js', import.meta.url).href;
  const child = spawnSync(process.execPath, ['--input-type=module', '--eval', [
    `import { moodSummary, moodActionGuidance } from ${JSON.stringify(moduleUrl)};`,
    'console.log(JSON.stringify({ summary: moodSummary(), guidance: moodActionGuidance() }));',
  ].join('\n')], { encoding: 'utf8', env: { ...process.env, EVA_DATA_DIR: dataDir } });
  assert.equal(child.status, 0, child.stderr);
  const persisted = JSON.parse(child.stdout.trim().split('\n').at(-1));
  assert.equal(persisted.summary, summary);
  assert.ok(persisted.guidance.includes(summary));
});
