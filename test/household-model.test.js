import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-household-model-'));
process.env.EVA_DATA_DIR = dataDir;

const {
  addMemory,
  db,
  upsertPerson,
} = await import('../src/db.js');
const {
  classifyPerson,
  classifiedPeople,
  householdContextText,
  householdMembers,
  recordRelationshipFeedback,
  relationshipFeedbackForPerson,
  setPersonHouseholdRole,
  syncHouseholdRolesFromPresence,
} = await import('../src/mind/household.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function person(name, notes = null) {
  const id = upsertPerson(name, { notes });
  return db.prepare('SELECT * FROM people WHERE id=?').get(id);
}

test('personen krijgen verschillende rollen zonder iedereen tot gezinslid te maken', () => {
  const ada = person('Ada', 'gezinslid en bewoner');
  person('Bram', 'buurman');
  person('Cara', 'regelmatige bezoeker');
  person('Codex livecontrole', 'bewoner-fixture voor een test');
  person('Onbekende persoon');

  const roles = Object.fromEntries(classifiedPeople().map((row) => [row.name, row.household_role]));
  assert.equal(roles.Ada, 'household_member');
  assert.equal(roles.Bram, 'neighbor');
  assert.equal(roles.Cara, 'visitor');
  assert.equal(roles['Codex livecontrole'], 'test');
  assert.equal(roles['Onbekende persoon'], 'unknown');
  assert.deepEqual(householdMembers().map((row) => row.name), ['Ada']);
  assert.equal(classifyPerson(ada).role_source, 'notes');
});

test('een expliciete duurzame rol wint en valideert invoer', () => {
  const cara = db.prepare("SELECT * FROM people WHERE name='Cara'").get();
  const changed = setPersonHouseholdRole(cara.id, 'gezinslid', { source: 'confirmed_by_household' });
  assert.equal(changed.household_role, 'household_member');
  assert.equal(changed.role_source, 'confirmed_by_household');

  const reloaded = db.prepare("SELECT * FROM people WHERE name='Cara'").get();
  assert.equal(classifyPerson(reloaded).household_role, 'household_member');
  assert.throws(() => setPersonHouseholdRole(cara.id, 'collega'), /ongeldige huishoudrol/);
  assert.throws(() => setPersonHouseholdRole('Bestaat niet', 'visitor'), /niet bekend/);
});

test('betrouwbare HA-personen koppelen alleen bestaande menselijke identiteiten', () => {
  person('Daan');
  const changed = syncHouseholdRolesFromPresence([
    { entity_id: 'person.daan_jansen', state: 'home', attributes: { friendly_name: 'Daan Jansen' } },
    { entity_id: 'person.chatgpt', state: 'home', attributes: { friendly_name: 'Chatgpt' } },
    { entity_id: 'person.mqtt', state: 'unknown', attributes: { friendly_name: 'mqtt' } },
  ]);
  assert.equal(changed.length, 1);
  assert.equal(changed[0].name, 'Daan');
  assert.equal(changed[0].household_role, 'household_member');
  assert.equal(classifiedPeople().some((row) => row.name === 'Chatgpt'), false);
});

test('gezinscontext is gegrond en bevat geen test-, buur- of onbekende personen', () => {
  addMemory({ person: 'Ada', category: 'voorkeur', content: 'Ada drinkt graag thee zonder suiker.', importance: 0.8 });
  addMemory({ person: 'Bram', category: 'feit', content: 'Bram verzamelt modeltreinen.', importance: 0.8 });
  addMemory({ person: 'Codex livecontrole', category: 'feit', content: 'Codex meldt regressietests.', importance: 0.8 });

  const context = householdContextText({
    subject: 'Ada',
    homeContext: 'De voordeur is dicht en de woonkamer is 20 °C.',
  });
  assert.match(context, /Bewoners van dit huis:\n- Ada/);
  assert.match(context, /Ada drinkt graag thee zonder suiker/);
  assert.match(context, /\n- Cara/);
  assert.match(context, /voordeur is dicht/);
  assert.doesNotMatch(context, /Bram/);
  assert.doesNotMatch(context, /Codex/);
  assert.doesNotMatch(context, /Onbekende persoon/);
});

test('feedbackledger bewaart alleen een compacte, herbruikbare gedragsles', () => {
  const feedback = recordRelationshipFeedback({
    person: 'Ada',
    kind: 'correction',
    topic: 'toon',
    summary: 'Geef geen technisch statusrapport; vertel kort wat dit thuis merkbaar oplevert.',
    at: '2026-08-04T10:00:00.000Z',
  });
  assert.equal(feedback.person, 'Ada');
  assert.equal(feedback.valence, -1);
  assert.equal(feedback.kind, 'correction');

  const rows = relationshipFeedbackForPerson('Ada');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].summary, feedback.summary);
  assert.equal(
    db.prepare('SELECT COUNT(*) AS n FROM relationship_feedback WHERE person_id=?').get(feedback.person_id).n,
    1,
  );

  const duplicate = recordRelationshipFeedback({
    person: 'Ada',
    kind: 'correction',
    topic: 'toon',
    summary: 'Geef geen technisch statusrapport; vertel kort wat dit thuis merkbaar oplevert.',
  });
  assert.equal(duplicate.id, feedback.id);
  assert.equal(relationshipFeedbackForPerson('Ada').length, 1);

  const context = householdContextText({ subject: 'Ada' });
  assert.match(context, /Recente lessen voor de omgang/);
  assert.match(context, /geen technisch statusrapport/);
  assert.doesNotMatch(householdContextText({ subject: 'Cara' }), /geen technisch statusrapport/);
});

test('geheimen worden geweigerd en contactgegevens niet letterlijk opgeslagen', () => {
  assert.throws(() => recordRelationshipFeedback({
    person: 'Ada',
    kind: 'boundary',
    summary: 'Het wachtwoord is supergeheim123 en dat mag je onthouden.',
  }), /gevoelige feedback/);

  const safe = recordRelationshipFeedback({
    person: 'Ada',
    kind: 'preference',
    summary: 'Gebruik voor terugvragen niet ada@example.test of +31 6 12345678 maar het lopende gesprek.',
  });
  assert.doesNotMatch(safe.summary, /ada@example|12345678/);
  assert.match(safe.summary, /privégegeven weggelaten/);
  assert.equal(db.prepare("SELECT COUNT(*) AS n FROM relationship_feedback WHERE summary LIKE '%supergeheim123%'").get().n, 0);
});
