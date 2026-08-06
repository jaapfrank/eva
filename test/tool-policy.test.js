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
  toolNamesForRecovery,
} = await import('../src/mind/toolpolicy.js');
const { executeTool, TOOLS } = await import('../src/mind/tools.js');
const { db } = await import('../src/db.js');
const { registerSensitiveValues } = await import('../src/security/redaction.js');

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
    assert.ok(['bounded', 'full', 'none'].includes(policy.autonomy), name);
    assert.equal(typeof policy.tags, 'string', name);
    if (policy.verification) assert.ok(['explicit', 'ok'].includes(policy.verification), name);
    if (policy.proofRank != null) assert.ok(Number(policy.proofRank) >= 1, name);
    if (policy.diagnosticFallback != null) assert.equal(typeof policy.diagnosticFallback, 'boolean', name);
    if (policy.exclusiveRecovery != null) assert.equal(typeof policy.exclusiveRecovery, 'boolean', name);
    if (policy.recoveryScope != null) assert.equal(typeof policy.recoveryScope, 'string', name);
    if (policy.recoveryTools != null) {
      assert.ok(Array.isArray(policy.recoveryTools), name);
      assert.deepEqual([...new Set(policy.recoveryTools)], policy.recoveryTools, name);
      for (const recoveryTool of policy.recoveryTools) {
        assert.ok(TOOL_POLICY[recoveryTool], `${name}:${recoveryTool}`);
        assert.notEqual(recoveryTool, name, `${name}:${recoveryTool}`);
      }
    }
    if (policy.exclusiveRecovery === true) assert.ok(policy.recoveryTools?.length > 0, name);
    for (const tag of policy.tags.split(/\s+/u).filter(Boolean)) {
      assert.match(tag, /^(?:discovery|house|free|delegated|grounding|lane:(?:house|research|workspace|family)|primary:(?:house|research|workspace|family)|contribution:(?:house|research|making|contact)|proof:(?:house|project)|reasoning:(?:code|mechanical))$/, `${name}:${tag}`);
      if (tag.startsWith('primary:')) assert.ok(policy.tags.split(/\s+/u).includes(tag.replace('primary:', 'lane:')), `${name}:${tag}`);
    }
  }
  const scopes = new Map();
  for (const [name, policy] of Object.entries(TOOL_POLICY)) {
    if (!policy.recoveryScope) continue;
    assert.ok(policy.recoveryScope.trim(), name);
    const members = scopes.get(policy.recoveryScope) || [];
    members.push(name);
    scopes.set(policy.recoveryScope, members);
    assert.equal(policy.effect, TOOL_EFFECT.read, name);
    assert.equal(policy.changesByDefault, false, name);
    assert.equal(policy.durable, false, name);
    assert.notEqual(policy.exclusiveRecovery, true, name);
  }
  for (const [scope, members] of scopes) assert.ok(members.length >= 2, scope);
});

test('declaratieve herstelroutering bewaart het bestaande aanbod exact', () => {
  const diagnostics = [
    'huis_status', 'ha_lees', 'wie_is_thuis', 'zoek_internet', 'lees_bestand',
    'lijst_bestanden', 'zoek_geheugen', 'zoek_gesprek', 'toon_herinneringen', 'toon_werk',
  ];
  assert.deepEqual(
    Object.entries(TOOL_POLICY)
      .filter(([, policy]) => policy.diagnosticFallback === true)
      .map(([name]) => name)
      .sort(),
    [...diagnostics].sort(),
  );
  assert.deepEqual(
    Object.entries(TOOL_POLICY)
      .filter(([, policy]) => policy.exclusiveRecovery === true)
      .map(([name]) => name),
    ['bedien_huis'],
  );

  const expectedGeneric = (required, companions = []) => [
    ...new Set([...required, ...diagnostics, ...companions]),
  ].sort();
  assert.equal(toolNamesForRecovery([]), null);
  assert.deepEqual([...toolNamesForRecovery(['bedien_huis'])].sort(), ['bedien_huis', 'ha_lees']);
  assert.deepEqual(
    [...toolNamesForRecovery(['schrijf_bestand'])].sort(),
    expectedGeneric(['schrijf_bestand'], ['lijst_bestanden', 'voer_uit']),
  );
  assert.deepEqual([...toolNamesForRecovery(['lees_bestand'])].sort(), ['lees_bestand', 'lijst_bestanden']);
  assert.deepEqual([...toolNamesForRecovery(['lijst_bestanden'])].sort(), ['lees_bestand', 'lijst_bestanden']);
  assert.deepEqual(
    [...toolNamesForRecovery(['lijst_bestanden', 'lees_bestand'])].sort(),
    ['lees_bestand', 'lijst_bestanden'],
  );
  assert.deepEqual(
    [...toolNamesForRecovery(['beheer_agentmail'])].sort(),
    expectedGeneric(['beheer_agentmail'], ['zoek_internet']),
  );
  assert.deepEqual(
    [...toolNamesForRecovery(['beheer_aanwezigheid', 'beheer_aanwezigheid'])].sort(),
    expectedGeneric(['beheer_aanwezigheid']),
  );
});

test('projectzoeking vindt veilig gerangschikte inhoud zonder afgeschermde bestanden', async () => {
  const root = path.join(dataDir, 'workspace');
  const nested = path.join(root, 'docs', 'diep');
  fs.mkdirSync(nested, { recursive: true });
  fs.writeFileSync(path.join(nested, 'kaart.md'), [
    '# Kaart',
    'Een losse contractverwijzing.',
    'Hier staan acht doelcontracten met hun geplande status.',
  ].join('\n'));
  fs.writeFileSync(path.join(root, '.env.backup'), 'contracten=mag-nooit-lekken');
  fs.writeFileSync(path.join(root, 'contract-alleen-naam.md'), 'Geen relevante inhoud.');
  fs.mkdirSync(path.join(root, 'docs'), { recursive: true });
  fs.mkdirSync(path.join(root, 'src'), { recursive: true });
  fs.writeFileSync(path.join(root, 'docs', 'een-term.md'), 'Alleen alpha.');
  fs.writeFileSync(path.join(root, 'src', 'volledige-treffer.js'), 'alpha beta gamma');
  fs.writeFileSync(path.join(root, 'groot.md'), `acht contracten gepland ${'x'.repeat(520_000)}`);
  fs.writeFileSync(path.join(root, 'binair.md'), Buffer.from('acht contracten gepland\0verborgen'));
  fs.writeFileSync(path.join(root, 'ongeldig.md'), Buffer.from([0xff, 0xfe, ...Buffer.from('acht contracten gepland')]));
  fs.mkdirSync(path.join(root, '.GIT'), { recursive: true });
  fs.writeFileSync(path.join(root, '.GIT', 'verstopt.md'), 'acht contracten gepland');
  fs.symlinkSync(path.join(nested, 'kaart.md'), path.join(root, 'contract-link.md'));
  const secret = 'search-canary-value-92841';
  const expansionSecret = 'cny92841';
  registerSensitiveValues([
    ['SEARCH_CANARY', secret],
    ['SEARCH_EXPANSION_CANARY_WITH_A_VERY_LONG_LABEL_FOR_BUDGET_TESTING', expansionSecret],
  ]);
  fs.writeFileSync(path.join(root, 'publiek.md'), `acht contracten gepland ${secret} ${`${expansionSecret} `.repeat(42)}`);

  const result = JSON.parse(await executeTool('lijst_bestanden', {
    pad: root,
    zoek: 'acht|contracten|gepland',
  }, { mode: 'chat' }));
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.equal(result.data[0].name.endsWith('docs/diep/kaart.md'), true);
  assert.equal(result.data[0].line, 3);
  assert.match(result.data[0].excerpt, /acht doelcontracten/i);
  assert.equal(result.data.some((entry) => entry.name.includes('.env')), false);
  assert.equal(result.data.some((entry) => /groot|binair|ongeldig|contract-link|\.GIT/.test(entry.name)), false);
  assert.equal(JSON.stringify(result).includes(secret), false);
  assert.match(JSON.stringify(result), /AFGESCHERMD:SEARCH_CANARY/);
  assert.ok(JSON.stringify(result).length <= 3_000);

  const repeated = JSON.parse(await executeTool('lijst_bestanden', {
    pad: root,
    zoek: 'acht|contracten|gepland',
  }, { mode: 'chat' }));
  assert.deepEqual(repeated.data.map((entry) => entry.name), result.data.map((entry) => entry.name));
  const coverageRank = JSON.parse(await executeTool('lijst_bestanden', {
    pad: root,
    zoek: 'alpha|beta|gamma',
  }, { mode: 'chat' }));
  assert.equal(coverageRank.data[0].name.endsWith('src/volledige-treffer.js'), true);
});

test('projectzoeking weigert runtime-data maar houdt de werkruimte beschikbaar', async () => {
  fs.mkdirSync(path.join(dataDir, 'runtime'), { recursive: true });
  fs.writeFileSync(path.join(dataDir, 'runtime', 'status.txt'), 'zoekterm runtime');
  const blocked = JSON.parse(await executeTool('lijst_bestanden', {
    pad: dataDir,
    zoek: 'zoekterm',
  }, { mode: 'chat' }));
  assert.equal(blocked.ok, false);
  assert.match(blocked.error, /(?:runtime-data is afgeschermd|buiten Eva haar project)/i);

  const allowed = JSON.parse(await executeTool('lijst_bestanden', {
    pad: path.join(dataDir, 'workspace'),
    zoek: 'doelcontracten',
  }, { mode: 'chat' }));
  assert.equal(allowed.ok, true);
});

test('afgekorte tooldata blijft geldige expliciet gedeeltelijke ActionResult', async () => {
  const file = path.join(dataDir, 'workspace', 'lang.txt');
  fs.writeFileSync(file, `${'lange regel '.repeat(24)}\n`.repeat(2_000));
  const output = await executeTool('lees_bestand', { pad: file, aantal_regels: 2_000 }, { mode: 'chat' });
  const result = JSON.parse(output);
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.equal(result.partial, true);
  assert.equal(result.needs_followup, true);
  assert.equal(result.data.truncated, true);
});

test('gerichte bestandzoeking geeft de beste samenhangende passage en geen trefferstort', async () => {
  const file = path.join(dataDir, 'workspace', 'gericht.md');
  const lines = Array.from({ length: 180 }, (_, index) => `regel ${index + 1}: alleen alpha`);
  lines[139] = 'alpha beta gamma: hier begint het relevante overzicht';
  lines[145] = 'Worker Runtime | gepland';
  lines[146] = 'Attention Runtime | grotendeels actief';
  fs.writeFileSync(file, lines.join('\n'));
  const output = await executeTool('lees_bestand', {
    pad: file,
    zoek: 'alpha|beta|gamma',
  }, { mode: 'chat' });
  const result = JSON.parse(output);
  assert.equal(result.partial, false);
  assert.match(result.data, /alpha beta gamma/);
  assert.match(result.data, /Worker Runtime \| gepland/);
  assert.match(result.data, /Attention Runtime \| grotendeels actief/);
  assert.doesNotMatch(result.data, /^1: /m);

  const contextOnly = path.join(dataDir, 'workspace', 'alleen-context.md');
  fs.writeFileSync(contextOnly, 'architectuur gepland maar zonder de expliciete zoekterm');
  const noDirectHit = JSON.parse(await executeTool('lees_bestand', {
    pad: contextOnly,
    zoek: 'unieke-naald',
  }, { mode: 'chat', userText: 'Waar staat de architectuur gepland?' }));
  assert.equal(noDirectHit.ok, false);
  assert.match(noDirectHit.error, /geen treffers/i);
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
