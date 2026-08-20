import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-capabilities-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';

const { db } = await import('../src/db.js');
const { capabilityContextText } = await import('../src/mind/capabilities.js');
const { executeTool } = await import('../src/mind/tools.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('een eigen vaardigheid wordt pas met een exact versiegebonden proefreceipt actief', async () => {
  const workspace = path.join(dataDir, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });
  const testFile = path.join(workspace, 'kort-onderzoek.test.mjs');
  fs.writeFileSync(testFile, "if (!process.argv.includes('--self-test')) process.exit(2); console.log('gerichte proef geslaagd');\n", 'utf8');
  const proofArgs = { programma: 'node', argumenten: [testFile, '--self-test'], werkmap: workspace };
  const proofRaw = await executeTool('voer_uit', proofArgs, { mode: 'autonomy' });
  const proofParsed = JSON.parse(proofRaw);
  assert.equal(proofParsed.ok, true);
  const priorToolResults = [{
    name: 'voer_uit', args: proofArgs, output: proofRaw,
    ok: proofParsed.ok, verified: proofParsed.verified,
  }];
  const staged = JSON.parse(await executeTool('maak_vaardigheid', {
    bewerking: 'stage',
    naam: 'Kort onderzoek afronden',
    beschrijving: 'Een onderzoek compact en besluitvaardig afronden',
    instructies: 'Lees primaire bronnen, kies de bruikbare conclusie en stop zonder rapportballast.',
    triggers: ['onderzoek', 'primaire bronnen'],
    testbestand: testFile,
    testruntime: 'node',
  }, { mode: 'autonomy', priorToolResults }));
  assert.equal(staged.ok, true);
  assert.equal(staged.data.capability.status, 'staged');

  const refused = JSON.parse(await executeTool('maak_vaardigheid', {
    bewerking: 'activeer', naam: 'Kort onderzoek afronden', receipt_id: 999999,
  }, { mode: 'autonomy' }));
  assert.equal(refused.ok, false);

  const tested = JSON.parse(await executeTool('maak_vaardigheid', {
    bewerking: 'beproef', naam: 'Kort onderzoek afronden',
  }, { mode: 'autonomy' }));
  assert.equal(tested.ok, true);
  assert.match(tested.data.receipt.content_hash, /^[a-f0-9]{64}$/);

  const active = JSON.parse(await executeTool('maak_vaardigheid', {
    bewerking: 'activeer', naam: 'Kort onderzoek afronden', receipt_id: tested.data.receipt.id,
  }, { mode: 'autonomy' }));
  assert.equal(active.ok, true);
  assert.match(capabilityContextText('onderzoek met primaire bronnen'), /Kort onderzoek afronden/);
  const demonstrated = JSON.parse(await executeTool('gebruik_capability', {
    naam: 'Kort onderzoek afronden',
  }, { mode: 'chat' }));
  assert.equal(demonstrated.ok, true);
  assert.equal(demonstrated.data.demonstrated, true);
  assert.equal(demonstrated.data.capability.status, 'active');
});

test('een add-on is checksumgebonden en blijft in de geïsoleerde werkruimte', async () => {
  const workspace = path.join(dataDir, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });
  const entrypoint = path.join(workspace, 'voorbeeld-addon.mjs');
  fs.writeFileSync(entrypoint, "if (process.argv.includes('--verwachte-fout')) { console.error('verwachte fout'); process.exit(1); } console.log('ok');\n", 'utf8');
  const staged = JSON.parse(await executeTool('beheer_addon', {
    bewerking: 'stage',
    naam: 'Voorbeeld add-on',
    beschrijving: 'Een kleine gecontroleerde capability',
    entrypoint,
    runtime: 'node',
    bron: 'eigen werkruimte',
    bronversie: '1',
    permissions: ['workspace:read-write', 'network:none'],
    triggers: ['voorbeeld'],
  }, { mode: 'autonomy' }));
  assert.equal(staged.ok, true);
  assert.match(staged.data.capability.manifest.sha256, /^[a-f0-9]{64}$/);

  const tested = JSON.parse(await executeTool('beheer_addon', {
    bewerking: 'beproef', naam: 'Voorbeeld add-on',
  }, { mode: 'autonomy' }));
  assert.equal(tested.ok, true);

  const active = JSON.parse(await executeTool('beheer_addon', {
    bewerking: 'activeer', naam: 'Voorbeeld add-on', receipt_id: tested.data.receipt.id,
  }, { mode: 'autonomy' }));
  assert.equal(active.ok, true);
  assert.equal(active.verified, true);
  const expectedFailure = JSON.parse(await executeTool('gebruik_capability', {
    naam: 'Voorbeeld add-on',
    argumenten: ['--verwachte-fout'],
    verwachte_exitcodes: [1],
  }, { mode: 'chat' }));
  assert.equal(expectedFailure.ok, true);
  assert.equal(expectedFailure.verified, true);
  assert.equal(expectedFailure.data.demonstrated, true);
  assert.equal(expectedFailure.data.exit_code, 1);
  fs.writeFileSync(entrypoint, "console.log('gewijzigd');\n", 'utf8');
  const stale = JSON.parse(await executeTool('beheer_addon', {
    bewerking: 'activeer', naam: 'Voorbeeld add-on', receipt_id: tested.data.receipt.id,
  }, { mode: 'autonomy' }));
  assert.equal(stale.ok, false);
  assert.match(stale.error, /gestagede versie|veranderde sinds staging/i);
});

test('bewijs van een andere of vorige capabilityversie wordt geweigerd', async () => {
  const workspace = path.join(dataDir, 'workspace');
  const fileA = path.join(workspace, 'skill-a.test.mjs');
  const fileB = path.join(workspace, 'skill-b.test.mjs');
  fs.writeFileSync(fileA, "console.log('a');\n", 'utf8');
  fs.writeFileSync(fileB, "console.log('b');\n", 'utf8');
  const stage = async (name, testbestand, instructions) => executeTool('maak_vaardigheid', {
    bewerking: 'stage', naam: name, beschrijving: name, instructies: instructions,
    triggers: [name], testbestand, testruntime: 'node',
  }, { mode: 'autonomy' });
  await stage('Skill A', fileA, 'Doe werkwijze A en controleer het resultaat.');
  await stage('Skill B', fileB, 'Doe werkwijze B en controleer het resultaat.');
  const proofA = JSON.parse(await executeTool('maak_vaardigheid', {
    bewerking: 'beproef', naam: 'Skill A',
  }, { mode: 'autonomy' }));
  const wrongCapability = JSON.parse(await executeTool('maak_vaardigheid', {
    bewerking: 'activeer', naam: 'Skill B', receipt_id: proofA.data.receipt.id,
  }, { mode: 'autonomy' }));
  assert.equal(wrongCapability.ok, false);

  await stage('Skill A', fileA, 'Nieuwe werkwijze A met een extra gerichte controle.');
  const oldVersion = JSON.parse(await executeTool('maak_vaardigheid', {
    bewerking: 'activeer', naam: 'Skill A', receipt_id: proofA.data.receipt.id,
  }, { mode: 'autonomy' }));
  assert.equal(oldVersion.ok, false);
  assert.match(oldVersion.error, /exact deze capabilityversie/i);
});
