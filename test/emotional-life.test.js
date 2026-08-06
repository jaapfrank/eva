import assert from 'node:assert/strict';
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
