import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-workspace-manifest-'));
process.env.EVA_DATA_DIR = dataDir;

const {
  __toolsTest,
  executeTool,
  revalidateWorkspaceFileManifest,
  validWorkspaceFileManifest,
} = await import('../src/mind/tools.js');
const { db } = await import('../src/db.js');
const { canonicalJson } = await import('../src/util.js');
const { WORKSPACE_FILE_MANIFEST_LIMITS } = __toolsTest;

const workspace = path.join(dataDir, 'workspace');
fs.mkdirSync(workspace, { recursive: true });

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function resealManifest(manifest, changes = {}) {
  const body = { ...structuredClone(manifest), ...changes };
  delete body.sha256;
  return {
    ...body,
    sha256: createHash('sha256').update(canonicalJson(body)).digest('hex'),
  };
}

test('lijst_bestanden levert een volledig, gesorteerd filename-manifest zonder het data-arraycontract te wijzigen', async () => {
  const directory = path.join(workspace, 'inkomsten');
  fs.mkdirSync(directory, { recursive: true });
  for (const name of [
    'inkomstenkans-start-nu-uitwerken.md',
    'inkomstenkans-7dagen.md',
    'inkomstenkans-start.md',
  ]) fs.writeFileSync(path.join(directory, name), 'Nog te controleren.\n');
  fs.writeFileSync(path.join(directory, 'alleen-inhoud.md'), 'Deze inhoud noemt een inkomstenkans.\n');

  const result = JSON.parse(await executeTool('lijst_bestanden', {
    pad: directory,
    zoek: 'Inkomstenkans',
  }, { mode: 'chat' }));

  assert.equal(result.ok, true);
  assert.equal(Array.isArray(result.data), true, 'ActionResult.data blijft de bestaande array');
  assert.equal(result.data.some((entry) => entry.name.endsWith('alleen-inhoud.md')), true,
    'inhoudzoeking blijft beschikbaar in de gewone tooldata');
  assert.deepEqual(result.file_manifest, {
    contract: 'WorkspaceFileManifest/v1',
    root: 'data/workspace/inkomsten',
    query: 'inkomstenkans',
    matchKind: 'filename',
    paths: [
      'data/workspace/inkomsten/inkomstenkans-7dagen.md',
      'data/workspace/inkomsten/inkomstenkans-start-nu-uitwerken.md',
      'data/workspace/inkomsten/inkomstenkans-start.md',
    ],
    complete: true,
    truncated: false,
    generated: 'pre_effect',
    snapshotSha256: result.file_manifest.snapshotSha256,
    sha256: result.file_manifest.sha256,
  });
  assert.equal(result.file_manifest.paths.some((name) => name.endsWith('alleen-inhoud.md')), false,
    'een content-only treffer verleent geen filename-scope');
  assert.match(result.file_manifest.sha256, /^[a-f0-9]{64}$/u);
  assert.equal(validWorkspaceFileManifest(result.file_manifest, {
    root: directory,
    query: 'Inkomstenkans',
  }), true);
  assert.equal(revalidateWorkspaceFileManifest(result.file_manifest, {
    root: directory,
    query: 'Inkomstenkans',
  }), true, 'de host kan de uitgegeven snapshot exact opnieuw inventariseren');

  const forged = structuredClone(result.file_manifest);
  forged.paths[0] = 'data/workspace/inkomsten/niet-uitgegeven.md';
  assert.equal(validWorkspaceFileManifest(forged, {
    root: directory,
    query: 'Inkomstenkans',
  }), false, 'een inhoudelijk gewijzigde receipt faalt op de oorspronkelijke hash');
  assert.equal(validWorkspaceFileManifest({
    ...result.file_manifest,
    sha256: 'f'.repeat(64),
  }, { root: directory, query: 'Inkomstenkans' }), false);

  const nonexistent = resealManifest(result.file_manifest, {
    paths: [...result.file_manifest.paths,
      'data/workspace/inkomsten/inkomstenkans-niet-bestaand.md'].sort(),
  });
  assert.equal(validWorkspaceFileManifest(nonexistent, {
    root: directory, query: 'Inkomstenkans',
  }), true, 'de gewone validator blijft bewust uitsluitend syntactisch');
  assert.equal(revalidateWorkspaceFileManifest(nonexistent, {
    root: directory, query: 'Inkomstenkans',
  }), false, 'een opnieuw gehashte maar niet-bestaande target krijgt geen host-authority');
});

test('filename-manifest begrenst matches expliciet en claimt dan geen volledige inventaris', async () => {
  const directory = path.join(workspace, 'veel');
  fs.mkdirSync(directory, { recursive: true });
  const matchCap = __toolsTest.WORKSPACE_FILE_MANIFEST_LIMITS.matchCap;
  for (let index = 0; index <= matchCap; index += 1) {
    fs.writeFileSync(path.join(directory,
      `inkomstenkans-${String(index).padStart(4, '0')}.md`), `${index}\n`);
  }

  const result = JSON.parse(await executeTool('lijst_bestanden', {
    pad: directory,
    zoek: 'inkomstenkans',
  }, { mode: 'chat' }));
  assert.equal(Array.isArray(result.data), true);
  assert.equal(result.file_manifest.paths.length, matchCap);
  assert.equal(result.file_manifest.complete, false);
  assert.equal(result.file_manifest.truncated, true);
  assert.equal(result.ok, true, 'de begrensde data-array blijft bruikbaar');
  assert.equal(result.verified, false);
  assert.equal(result.partial, true);
  assert.equal(result.needs_followup, true);
  assert.equal(result.failure_class, 'partial_result');
  assert.equal(validWorkspaceFileManifest(result.file_manifest, {
    root: directory,
    query: 'inkomstenkans',
  }), true);
  assert.equal(revalidateWorkspaceFileManifest(result.file_manifest, {
    root: directory, query: 'inkomstenkans',
  }), false, 'een begrensde inventaris kan geen complete host-authority worden');
});

test('een geïsoleerde beleidsmatig genegeerde map maakt de inventaris expliciet onvolledig', async () => {
  const directory = path.join(workspace, 'alleen-genegeerd');
  const ignored = path.join(directory, '.git');
  fs.mkdirSync(ignored, { recursive: true });
  fs.writeFileSync(path.join(directory, 'inkomstenkans-toegestaan.md'), 'ok\n');
  fs.writeFileSync(path.join(ignored, 'inkomstenkans-genegeerd.md'), 'nee\n');

  const result = JSON.parse(await executeTool('lijst_bestanden', {
    pad: directory,
    zoek: 'inkomstenkans',
  }, { mode: 'chat' }));
  assert.deepEqual(result.file_manifest.paths,
    ['data/workspace/alleen-genegeerd/inkomstenkans-toegestaan.md']);
  assert.equal(result.file_manifest.complete, false);
  assert.equal(result.file_manifest.truncated, false);
  assert.equal(result.verified, false);
  assert.equal(result.partial, true);
  assert.equal(result.needs_followup, true);
  assert.equal(revalidateWorkspaceFileManifest(result.file_manifest, {
    root: directory, query: 'inkomstenkans',
  }), false);
});

test('symlinks krijgen ook met een opnieuw berekende syntactische hash geen manifest-authority', async () => {
  const directory = path.join(workspace, 'grenzen');
  const ignored = path.join(directory, '.git');
  fs.mkdirSync(ignored, { recursive: true });
  fs.writeFileSync(path.join(directory, 'inkomstenkans-toegestaan.md'), 'ok\n');
  fs.writeFileSync(path.join(ignored, 'inkomstenkans-genegeerd.md'), 'nee\n');
  fs.symlinkSync(path.join(directory, 'inkomstenkans-toegestaan.md'),
    path.join(directory, 'inkomstenkans-link.md'));

  const result = JSON.parse(await executeTool('lijst_bestanden', {
    pad: directory,
    zoek: 'inkomstenkans',
  }, { mode: 'chat' }));
  assert.deepEqual(result.file_manifest.paths,
    ['data/workspace/grenzen/inkomstenkans-toegestaan.md']);
  assert.equal(result.file_manifest.complete, false,
    'een overgeslagen symlink maakt de scope aantoonbaar onvolledig');
  assert.equal(result.file_manifest.truncated, false,
    'een ambigu pad is geen cap-truncatie');
  assert.equal(result.verified, false);
  assert.equal(result.partial, true);
  assert.equal(result.needs_followup, true);
  assert.equal(validWorkspaceFileManifest(result.file_manifest, {
    root: directory,
    query: 'inkomstenkans',
  }), true);
  const forged = resealManifest(result.file_manifest, {
    complete: true,
    paths: [
      'data/workspace/grenzen/inkomstenkans-link.md',
      'data/workspace/grenzen/inkomstenkans-toegestaan.md',
    ],
  });
  assert.equal(validWorkspaceFileManifest(forged, {
    root: directory, query: 'inkomstenkans',
  }), true);
  assert.equal(revalidateWorkspaceFileManifest(forged, {
    root: directory, query: 'inkomstenkans',
  }), false, 'de sterke hostcontrole herkent de symlink ondanks een geldige syntactische hash');
});

test('lijst_bestanden zonder query attesteert exact de toegestane directe bestanden', async () => {
  const directory = path.join(workspace, 'direct');
  fs.mkdirSync(path.join(directory, 'nested'), { recursive: true });
  fs.writeFileSync(path.join(directory, 'b.md'), 'b\n');
  fs.writeFileSync(path.join(directory, 'a.md'), 'a\n');
  fs.writeFileSync(path.join(directory, 'nested', 'c.md'), 'c\n');

  const result = JSON.parse(await executeTool('lijst_bestanden', {
    pad: directory,
  }, { mode: 'chat' }));
  assert.equal(Array.isArray(result.data), true);
  assert.deepEqual(result.file_manifest.paths, [
    'data/workspace/direct/a.md',
    'data/workspace/direct/b.md',
  ]);
  assert.equal(result.file_manifest.query, null);
  assert.equal(result.file_manifest.complete, true);
  assert.equal(result.file_manifest.truncated, false);
  assert.equal(validWorkspaceFileManifest(result.file_manifest, { root: directory, query: '' }), true);
  assert.equal(revalidateWorkspaceFileManifest(result.file_manifest, {
    root: directory, query: '',
  }), true);
});

test('verliesgevende korte, te lange en te brede queries worden geweigerd', async () => {
  const directory = path.join(workspace, 'query-grenzen');
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(path.join(directory, 'inkomstenkans.md'), 'ok\n');
  const invalidQueries = [
    'ai',
    'x'.repeat(WORKSPACE_FILE_MANIFEST_LIMITS.queryBytesCap + 1),
    Array.from({ length: WORKSPACE_FILE_MANIFEST_LIMITS.queryTermCap + 1 },
      (_, index) => `term${index}`).join(' '),
  ];
  for (const zoek of invalidQueries) {
    const result = JSON.parse(await executeTool('lijst_bestanden', {
      pad: directory, zoek,
    }, { mode: 'chat' }));
    assert.equal(result.ok, false);
    assert.equal(result.verified, false);
    assert.equal(result.needs_followup, true);
    assert.equal(Object.hasOwn(result, 'file_manifest'), false);
    assert.match(result.error, /manifestzoekvraag/u);
  }
});

test('snapshot-hercontrole detecteert een scopewijziging na de pre-effect inventaris', async () => {
  const directory = path.join(workspace, 'toctou');
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(path.join(directory, 'inkomstenkans-een.md'), 'een\n');
  const result = JSON.parse(await executeTool('lijst_bestanden', {
    pad: directory, zoek: 'inkomstenkans',
  }, { mode: 'chat' }));
  assert.equal(result.file_manifest.complete, true);
  assert.equal(revalidateWorkspaceFileManifest(result.file_manifest, {
    root: directory, query: 'inkomstenkans',
  }), true);

  fs.writeFileSync(path.join(directory, 'inkomstenkans-twee.md'), 'twee\n');
  assert.equal(validWorkspaceFileManifest(result.file_manifest, {
    root: directory, query: 'inkomstenkans',
  }), true, 'de syntactische receipt muteert niet mee');
  assert.equal(revalidateWorkspaceFileManifest(result.file_manifest, {
    root: directory, query: 'inkomstenkans',
  }), false, 'de host herkent de gewijzigde scope vóór completion-authority');
});
