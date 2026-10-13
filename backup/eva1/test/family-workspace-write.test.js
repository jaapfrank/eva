import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const projectRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');

test('een gewone gezinsopdracht kan een zichtbaar werkstuk maken, aanpassen en teruglezen', () => {
  const dataDir = fs.realpathSync(fs.mkdtempSync(
    path.join(os.tmpdir(), 'bottie-family-workspace-'),
  ));
  const environment = { ...process.env, EVA_DATA_DIR: dataDir };
  delete environment.NODE_TEST_CONTEXT;
  environment.OPENAI_API_KEY = '';
  environment.TELEGRAM_BOT_TOKEN = '';

  try {
    const source = `
      const { executeTool } = await import('./src/mind/tools.js');
      const { db } = await import('./src/db.js');
      const first = JSON.parse(await executeTool('schrijf_bestand', {
        pad: 'gezinswerk/resultaat.txt', inhoud: 'eerste versie\\n',
      }, { mode: 'chat' }));
      const second = JSON.parse(await executeTool('schrijf_bestand', {
        pad: 'gezinswerk/resultaat.txt', inhoud: 'afgerond gezinsresultaat\\n',
      }, { mode: 'chat' }));
      const readBack = JSON.parse(await executeTool('lees_bestand', {
        pad: 'gezinswerk/resultaat.txt',
      }, { mode: 'chat' }));
      console.log(JSON.stringify({ first, second, readBack }));
      db.close();
    `;
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
      cwd: projectRoot,
      env: environment,
      encoding: 'utf8',
      timeout: 15_000,
    });
    assert.equal(child.status, 0, child.stderr || child.stdout);
    const outcome = JSON.parse(child.stdout.trim().split(/\r?\n/u).at(-1));
    assert.equal(outcome.first.ok, true, JSON.stringify(outcome.first));
    assert.equal(outcome.first.changed, true);
    assert.equal(outcome.second.ok, true, JSON.stringify(outcome.second));
    assert.equal(outcome.second.changed, true);
    assert.equal(outcome.readBack.ok, true, JSON.stringify(outcome.readBack));
    assert.equal(outcome.readBack.data, 'afgerond gezinsresultaat\n');
    assert.equal(fs.readFileSync(
      path.join(dataDir, 'workspace', 'gezinswerk', 'resultaat.txt'), 'utf8',
    ), 'afgerond gezinsresultaat\n');
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
