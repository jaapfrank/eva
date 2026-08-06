import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-llm-recovery-'));
process.env.EVA_DATA_DIR = dataDir;

const { db } = await import('../src/db.js');
const { chatComplete, msg } = await import('../src/brain/llm.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('twee korte providerstoringen herstellen binnen dezelfde gebruikersbeurt', async () => {
  const models = [];
  const waits = [];
  let attempts = 0;
  const result = await chatComplete({
    instructions: 'Antwoord kort.',
    input: [msg('user', 'Kies één dashboardrichting.')],
    model: 'gpt-5.6-terra',
    effort: 'low',
    request: async (body) => {
      attempts += 1;
      models.push(body.model);
      if (attempts <= 2) throw new Error('fetch failed');
      return {
        output: [{
          type: 'message', role: 'assistant',
          content: [{ type: 'output_text', text: 'Zo min mogelijk dashboards.' }],
        }],
        usage: null,
      };
    },
    pause: async (ms) => { waits.push(ms); },
  });

  assert.equal(attempts, 3);
  assert.deepEqual(waits, [500, 1_500]);
  assert.equal(result.text, 'Zo min mogelijk dashboards.');
  assert.equal(models[0], 'gpt-5.6-terra');
  assert.ok(models.slice(1).every(Boolean));
});
