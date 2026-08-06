import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-autonomy-safety-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_LEVEL = 'bounded';
delete process.env.EVA_AUTONOMY_CODE_EXECUTION;

const { db } = await import('../src/db.js');
const { executeTool } = await import('../src/mind/tools.js');
const { __projectRouteTest } = await import('../src/mind/agent.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('vrije en bestaande autonomie krijgen standaard geen schrijf- of uitvoerrecht', () => {
  assert.equal(__projectRouteTest.autonomyToolAllowed('schrijf_bestand', { hasOpenWork: true }), false);
  assert.equal(__projectRouteTest.autonomyToolAllowed('voer_uit', { hasOpenWork: true }), false);
  assert.equal(__projectRouteTest.autonomyToolAllowed('huis_status'), true);
  assert.equal(__projectRouteTest.autonomyToolAllowed('zoek_internet'), false);
});

test('de toolgrens blokkeert autonome code ook als een model de tool toch aanroept', async () => {
  const raw = await executeTool('schrijf_bestand', {
    pad: 'data/workspace/mag-niet.txt',
    inhoud: 'niet schrijven',
  }, { mode: 'autonomy' });
  const result = JSON.parse(raw);
  assert.equal(result.ok, false);
  assert.match(result.evidence.summary, /standaard uitgeschakeld/);
  assert.equal(fs.existsSync(path.join(dataDir, 'workspace', 'mag-niet.txt')), false);
});
