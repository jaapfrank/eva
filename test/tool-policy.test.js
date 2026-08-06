import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-tool-policy-'));
process.env.EVA_DATA_DIR = dataDir;

const {
  TOOL_EFFECT,
  TOOL_POLICY,
  toolAllowedByPolicy,
  toolChangesByDefault,
  toolEffect,
} = await import('../src/mind/toolpolicy.js');
const { executeTool, TOOLS } = await import('../src/mind/tools.js');
const { db } = await import('../src/db.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('centrale metadata dekt iedere kerntool exact eenmaal', () => {
  const defined = TOOLS.map((tool) => tool.name).sort();
  const classified = Object.keys(TOOL_POLICY).sort();
  assert.deepEqual(classified, defined);
  for (const [name, policy] of Object.entries(TOOL_POLICY)) {
    assert.ok(['read', 'home', 'voice', 'external', 'workspace', 'persistent', 'mixed'].includes(policy.effect), name);
    assert.ok(['allow', 'isolated', 'deny'].includes(policy.synthetic), name);
    assert.equal(typeof policy.report, 'string', name);
    assert.equal(typeof policy.durable, 'boolean', name);
    assert.equal(typeof policy.changesByDefault, 'boolean', name);
  }
});

test('centrale changed-default bewaart exact de bestaande result-normalisatie', () => {
  const expected = [
    'annuleer_herinnering', 'bedien_huis', 'beheer_addon', 'beheer_agentmail',
    'beheer_aanwezigheid', 'beheer_toolpakket', 'beheer_werkcel', 'gebruik_robot',
    'leer_gezicht_van_foto', 'maak_vaardigheid', 'onthoud', 'schrijf_bestand',
    'spreek_in_huis', 'stuur_bericht', 'verkenning_bijhouden', 'voer_uit',
    'zet_herinnering',
  ].sort();
  assert.deepEqual(
    Object.keys(TOOL_POLICY).filter(toolChangesByDefault).sort(),
    expected,
  );
});

test('vaste effecten scheiden lezen van fysieke, externe en persistente handelingen', () => {
  assert.equal(toolEffect('huis_status'), TOOL_EFFECT.read);
  assert.equal(toolEffect('bedien_huis'), TOOL_EFFECT.home);
  assert.equal(toolEffect('spreek_in_huis'), TOOL_EFFECT.voice);
  assert.equal(toolEffect('stuur_bericht'), TOOL_EFFECT.external);
  assert.equal(toolEffect('schrijf_bestand'), TOOL_EFFECT.workspace);
  assert.equal(toolEffect('onthoud'), TOOL_EFFECT.persistent);
  assert.equal(toolEffect('bestaat_niet'), 'unknown');
});

test('mixed tools classificeren hun read-operatie en effectoperatie afzonderlijk', () => {
  const cases = [
    ['gebruik_robot', { actie: 'status' }, 'read'],
    ['gebruik_robot', { actie: 'kaart' }, 'read'],
    ['gebruik_robot', { actie: 'start' }, 'home'],
    ['beheer_aanwezigheid', { actie: 'bekijk' }, 'read'],
    ['beheer_aanwezigheid', { actie: 'stel_trackers_in' }, 'home'],
    ['beheer_agentmail', { actie: 'berichten' }, 'read'],
    ['beheer_agentmail', { actie: 'inbox' }, 'read'],
    ['beheer_agentmail', { actie: 'search' }, 'read'],
    ['beheer_agentmail', { actie: 'versturen' }, 'external'],
    ['maak_vaardigheid', { bewerking: 'lijst' }, 'read'],
    ['maak_vaardigheid', { bewerking: 'activeer' }, 'workspace'],
    ['beheer_addon', { bewerking: 'lijst' }, 'read'],
    ['beheer_addon', { bewerking: 'stage' }, 'workspace'],
    ['beheer_werkcel', { bewerking: 'status' }, 'read'],
    ['beheer_werkcel', { bewerking: 'start' }, 'persistent'],
    ['beheer_toolpakket', { bewerking: 'inspecteer' }, 'read'],
    ['beheer_toolpakket', { bewerking: 'status' }, 'read'],
    ['beheer_toolpakket', { bewerking: 'ontdek' }, 'workspace'],
    ['beheer_toolpakket', { bewerking: 'installeer' }, 'workspace'],
    ['doel_bijhouden', { bewerking: 'list' }, 'read'],
    ['doel_bijhouden', { bewerking: 'create' }, 'persistent'],
    ['verkenning_bijhouden', { bewerking: 'lijst' }, 'read'],
    ['verkenning_bijhouden', { bewerking: 'start' }, 'persistent'],
  ];
  for (const [name, args, expected] of cases) {
    assert.equal(toolEffect(name, args), expected, `${name}:${JSON.stringify(args)}`);
  }
});

test('onbekende of ontbrekende mixed operaties vallen onder het effect en nooit onder read', () => {
  assert.equal(toolEffect('gebruik_robot', {}), TOOL_EFFECT.home);
  assert.equal(toolEffect('beheer_agentmail', { actie: 'onbekend' }), TOOL_EFFECT.external);
  assert.equal(toolEffect('beheer_werkcel', { bewerking: 'onbekend' }), TOOL_EFFECT.persistent);
});

test('geen effectpolicy bewaart backwards-compatible toegang', () => {
  assert.equal(toolAllowedByPolicy('bedien_huis', {}, null), true);
  assert.equal(toolAllowedByPolicy('bestaat_niet', {}, null), true);
});

test('read-only policy laat reads door en weigert ieder effectdomein', () => {
  for (const name of ['huis_status', 'ha_lees', 'lees_bestand', 'inspecteer_vermogen']) {
    assert.equal(toolAllowedByPolicy(name, {}, 'read_only'), true, name);
  }
  for (const name of ['bedien_huis', 'spreek_in_huis', 'stuur_bericht', 'schrijf_bestand', 'onthoud']) {
    assert.equal(toolAllowedByPolicy(name, {}, 'read_only'), false, name);
  }
  assert.equal(toolAllowedByPolicy('gebruik_robot', { actie: 'status' }, 'read_only'), true);
  assert.equal(toolAllowedByPolicy('gebruik_robot', { actie: 'start' }, 'read_only'), false);
  assert.equal(toolAllowedByPolicy('bestaat_niet', {}, 'read_only'), false);
});

test('read-only authorization volgt voor iedere tool uitsluitend haar centrale effectclassificatie', () => {
  for (const tool of TOOLS) {
    const effect = toolEffect(tool.name, {});
    assert.equal(
      toolAllowedByPolicy(tool.name, {}, 'read_only'),
      effect === TOOL_EFFECT.read,
      `${tool.name}:${effect}`,
    );
  }
});

test('scoped allow en deny behandelen reads standaard als toegestaan en deny als leidend', () => {
  const workspaceOnly = { allowedEffects: ['workspace'] };
  assert.equal(toolAllowedByPolicy('lees_bestand', {}, workspaceOnly), true);
  assert.equal(toolAllowedByPolicy('schrijf_bestand', {}, workspaceOnly), true);
  assert.equal(toolAllowedByPolicy('bedien_huis', {}, workspaceOnly), false);

  const voiceExcepted = { allowedEffects: ['voice', 'home'], deniedEffects: ['voice'] };
  assert.equal(toolAllowedByPolicy('spreek_in_huis', {}, voiceExcepted), false);
  assert.equal(toolAllowedByPolicy('bedien_huis', {}, voiceExcepted), true);
  assert.equal(toolAllowedByPolicy('huis_status', {}, voiceExcepted), true);
  assert.equal(toolAllowedByPolicy('huis_status', {}, { mode: 'allow', allowRead: false }), false);
});

test('executeTool blokkeert een verboden effect vóór de handler en geeft ActionResult policybewijs', async () => {
  const raw = await executeTool('bedien_huis', { doel: 'lamp', actie: 'uit' }, {
    effectPolicy: 'read_only',
  });
  const result = JSON.parse(raw);
  assert.equal(result.contract, 'ActionResult/v1');
  assert.equal(result.tool, 'bedien_huis');
  assert.equal(result.ok, false);
  assert.equal(result.changed, false);
  assert.equal(result.verified, false);
  assert.equal(result.failure_class, 'policy');
  assert.equal(result.retryable, false);
  assert.match(result.error, /effect home.*niet toegestaan/);
});

test('executeTool laat onder dezelfde read-only policy een echte readhandler werken', async () => {
  const raw = await executeTool('toon_herinneringen', {}, { effectPolicy: 'read_only' });
  const result = JSON.parse(raw);
  assert.equal(result.contract, 'ActionResult/v1');
  assert.equal(result.tool, 'toon_herinneringen');
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.equal(result.changed, false);
});
