import assert from 'node:assert/strict';
import fs from 'node:fs';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync('/tmp/bottie-requester-home-');
process.env.EVA_DATA_DIR = dataDir;
process.env.HA_URL = 'http://requester-home.test';
process.env.HA_TOKEN = 'requester-home-test-token';
process.env.ALLOW_HOME_CONTROL = 'true';

const { executeTool } = await import('../src/mind/tools.js');
const { addMemory, db, upsertPerson } = await import('../src/db.js');
const { setPersonHouseholdRole } = await import('../src/mind/household.js');
const { homeEffectClaimFeedback } = await import('../src/mind/communication.js');

for (const name of ['Noor', 'Mila']) {
  const personId = upsertPerson(name, { notes: 'bewoner' });
  setPersonHouseholdRole(personId, 'household_member', { source: 'requester-home-test' });
}
addMemory({
  category: 'feit', person: 'Noor', importance: 0.95,
  content: 'Noor heeft een Elements/Leaf-lamp op haar slaapkamer.',
});
addMemory({
  category: 'correctie', person: 'Noor', importance: 0.98,
  content: 'Het was geen Hue/MB-staanlamp maar een Elements/Leaf-lamp op Noors slaapkamer.',
});
// Productie bevat veel belangrijkere maar niet-identificerende huisnotities.
// Die mogen de concrete eigenaar→apparaat-koppeling niet uit het kleine
// recallvenster drukken, zoals bij Jaaps echte Elements/Leaf-lamp gebeurde.
for (let index = 0; index < 12; index += 1) {
  addMemory({
    category: 'feit', person: 'Noor', importance: 0.99,
    content: `Algemene huisnotitie ${index}: controleer licht en slaapkamer zorgvuldig, zonder apparaatalias.`,
  });
}

const originalFetch = globalThis.fetch;
let states = [];
let serviceCalls = [];

function state(entityId, value, name) {
  return { entity_id: entityId, state: value, attributes: { friendly_name: name } };
}

function resetStates(nextStates) {
  states = nextStates.map((item) => structuredClone(item));
  serviceCalls = [];
}

globalThis.fetch = async (url, options = {}) => {
  const parsed = new URL(String(url));
  if (parsed.pathname === '/api/states') {
    return new Response(JSON.stringify(states), { status: 200 });
  }
  if (parsed.pathname.startsWith('/api/states/')) {
    const entityId = decodeURIComponent(parsed.pathname.slice('/api/states/'.length));
    const current = states.find((item) => item.entity_id === entityId);
    return current
      ? new Response(JSON.stringify(current), { status: 200 })
      : new Response('', { status: 404 });
  }
  if (parsed.pathname === '/api/services/light/turn_on' && options.method === 'POST') {
    const body = JSON.parse(String(options.body || '{}'));
    serviceCalls.push(body);
    const current = states.find((item) => item.entity_id === body.entity_id);
    if (current) current.state = 'on';
    return new Response('[]', { status: 200 });
  }
  throw new Error(`onverwachte HA-testrequest: ${options.method || 'GET'} ${parsed.pathname}`);
};

after(() => {
  globalThis.fetch = originalFetch;
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

const trustedContext = (sender) => ({
  mode: 'chat',
  sender,
  userText: 'Doe even het licht aan in mijn slaapkamer.',
  messageContext: { identityTrusted: true, isCurrent: () => true },
});

function recordedHomeResult(args, output) {
  const parsed = JSON.parse(output);
  return {
    name: 'bedien_huis', args, output,
    ok: parsed.ok, verified: parsed.verified, changed: parsed.changed,
    partial: parsed.partial, needs_followup: parsed.needs_followup,
  };
}

const bedroomStates = () => [
  state('person.noor_de_vries', 'home', 'Noor de Vries'),
  state('person.mila', 'home', 'Mila'),
  state('light.elements_4de3', 'off', 'Elements 4DE3'),
  state('light.mb_staanlamp', 'off', 'MB staanlamp'),
  state('light.slaapkamer_mila', 'off', 'Slaapkamer Mila'),
];

test('een natuurlijke mijn-opdracht bedient alleen de kamer van de trusted requester', async () => {
  resetStates(bedroomStates());

  const result = JSON.parse(await executeTool('bedien_huis', {
    doel: 'mijn slaapkamer', actie: 'aan',
  }, trustedContext('Noor')));

  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.equal(result.data.entity_id, 'light.elements_4de3');
  assert.deepEqual({
    contract: result.data.target_binding.contract,
    requester: result.data.target_binding.requester,
    requestedTarget: result.data.target_binding.requested_target,
    method: result.data.target_binding.method,
    resolvedEntityId: result.data.target_binding.resolved_entity_id,
  }, {
    contract: 'HomeRequesterTargetBinding/v1',
    requester: 'Noor',
    requestedTarget: 'mijn slaapkamer',
    method: 'person_memory',
    resolvedEntityId: 'light.elements_4de3',
  });
  assert.ok(result.data.target_binding.source_memory_ids.length >= 1);
  assert.ok(result.data.target_binding.alias_evidence.includes('elements'));
  assert.match(result.data.target_binding.binding_sha256, /^[a-f0-9]{64}$/u);
  assert.deepEqual(serviceCalls, [{ entity_id: 'light.elements_4de3' }]);
  assert.equal(states.find((item) => item.entity_id === 'light.slaapkamer_mila').state, 'off');
});

test('mijn zonder trusted requester faalt gesloten en bedient geen bewonerskamer', async () => {
  resetStates(bedroomStates());

  const result = JSON.parse(await executeTool('bedien_huis', {
    doel: 'mijn slaapkamer', actie: 'aan',
  }, {
    mode: 'chat', sender: 'Noor',
    messageContext: { identityTrusted: false, isCurrent: () => true },
  }));

  assert.equal(result.ok, false);
  assert.equal(result.changed, false);
  assert.deepEqual(serviceCalls, []);
});

test('een expliciet genoemde bewonerskamer houdt de bestaande resolutieroute', async () => {
  resetStates(bedroomStates());

  const result = JSON.parse(await executeTool('bedien_huis', {
    doel: 'slaapkamer Mila', actie: 'aan',
  }, trustedContext('Noor')));

  assert.equal(result.ok, true);
  assert.equal(result.data.entity_id, 'light.slaapkamer_mila');
  assert.deepEqual(serviceCalls, [{ entity_id: 'light.slaapkamer_mila' }]);
});

test('een naamloze kamer zonder requesterfeit wordt niet uit uitsluiting gegokt', async () => {
  resetStates([
    state('person.noor_de_vries', 'home', 'Noor de Vries'),
    state('person.mila', 'home', 'Mila'),
    state('light.slaapkamer', 'off', 'Slaapkamer'),
    state('light.slaapkamer_mila', 'off', 'Slaapkamer Mila'),
  ]);

  const result = JSON.parse(await executeTool('bedien_huis', {
    doel: 'mijn slaapkamer', actie: 'aan',
  }, trustedContext('Noor')));

  assert.equal(result.ok, false);
  assert.equal(result.changed, false);
  assert.deepEqual(serviceCalls, []);
});

test('alleen een expliciet aan een ander gekoppelde kamer leidt tot verduidelijking', async () => {
  resetStates([
    state('person.noor_de_vries', 'home', 'Noor de Vries'),
    state('person.mila', 'home', 'Mila'),
    state('light.slaapkamer_mila', 'off', 'Slaapkamer Mila'),
  ]);

  const result = JSON.parse(await executeTool('bedien_huis', {
    doel: 'mijn slaapkamer', actie: 'aan',
  }, trustedContext('Noor')));

  assert.equal(result.ok, false);
  assert.equal(result.changed, false);
  assert.deepEqual(serviceCalls, []);
  assert.match(`${result.human_message} ${result.error}`, /dubbelzinnig|precies|geen passend/iu);
});

test('replycontrole accepteert alleen de intacte hostbinding voor het possessieve doel', async () => {
  resetStates(bedroomStates());
  const args = { doel: 'mijn slaapkamer', actie: 'aan' };
  const output = await executeTool('bedien_huis', args, trustedContext('Noor'));
  const receipt = recordedHomeResult(args, output);
  const intent = { ...args, requester: 'Noor' };

  assert.equal(homeEffectClaimFeedback('Het licht staat aan.', intent, [receipt]), '');

  const envelope = JSON.parse(output);
  const wrongRequester = structuredClone(envelope);
  wrongRequester.data.target_binding.requester = 'Mila';
  assert.match(homeEffectClaimFeedback('Het licht staat aan.', intent, [
    recordedHomeResult(args, JSON.stringify(wrongRequester)),
  ]), /niet voor precies/iu);

  const wrongEntity = structuredClone(envelope);
  wrongEntity.data.target_binding.resolved_entity_id = 'light.slaapkamer_mila';
  assert.match(homeEffectClaimFeedback('Het licht staat aan.', intent, [
    recordedHomeResult(args, JSON.stringify(wrongEntity)),
  ]), /niet voor precies/iu);

  const noHostBinding = structuredClone(envelope);
  delete noHostBinding.data.target_binding;
  assert.match(homeEffectClaimFeedback('Het licht staat aan.', intent, [
    recordedHomeResult(args, JSON.stringify(noHostBinding)),
  ]), /niet voor precies/iu);
});
