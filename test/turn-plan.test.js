import assert from 'node:assert/strict';
import test from 'node:test';
import {
  arbitrateTurnPlan,
  effectBoundary,
  identityNameCandidate,
  locationRequest,
  resolveVoiceDirective,
} from '../src/mind/turnplan.js';

const effects = (name) => ({
  bedien_huis: 'home', gebruik_robot: 'home', spreek_in_huis: 'voice',
  schrijf_bestand: 'workspace', voer_uit: 'workspace', stuur_bericht: 'external',
}[name] || 'read');

test('negatie wint van huis-, voice- en werkuitvoering', () => {
  assert.deepEqual(effectBoundary('Zet de lamp niet uit.').denied, ['home']);
  assert.deepEqual(effectBoundary('Spreek nu niets uit; leg alleen uit welke Voice PE je kiest.').denied, ['voice']);
  assert.deepEqual(effectBoundary('Geef een voorbeeld, maar maak in deze proef niets.').denied, ['workspace']);
  const plan = arbitrateTurnPlan({
    text: 'Doe de lamp niet uit. Wat begrijp je?',
    baseContract: { kind: 'conversation' },
    baseRequiredTools: ['bedien_huis'],
    baseDirectAction: { doel: 'lamp niet', actie: 'uit' },
    effectForTool: effects,
  });
  assert.deepEqual(plan.requiredTools, []);
  assert.equal(plan.directAction, null);
  assert.equal(plan.actionIntent, 'explain');
  assert.equal(plan.effectPolicy.deniedEffects.includes('home'), true);
});

test('maak niets laat bij een hypothetische programmeervraag geen halve leescyclus over', () => {
  const plan = arbitrateTurnPlan({
    text: 'Geef een voorbeeld van wat je zou bouwen en hoe je het test. Maak in deze proef niets.',
    baseContract: { kind: 'conversation', requiredTools: [] },
    baseRequiredTools: ['schrijf_bestand', 'lees_bestand'],
    effectForTool: (name) => name === 'schrijf_bestand' ? 'workspace' : 'read',
  });
  assert.deepEqual(plan.requiredTools, []);
  assert.equal(plan.actionIntent, 'forbid');
});

test('een echte maar nog generieke opdracht houdt uitvoerintentie en effectruimte', () => {
  const plan = arbitrateTurnPlan({
    text: 'Repareer dit.', actionRequested: true,
    baseContract: { kind: 'conversation', requiredTools: [] },
    effectForTool: effects,
  });
  assert.equal(plan.actionIntent, 'execute');
  assert.deepEqual(new Set(plan.effectPolicy.allowedEffects), new Set([
    'home', 'voice', 'external', 'workspace', 'persistent',
  ]));
});

test('een contrastieve opdracht wordt niet als globale ontkenning behandeld', () => {
  const boundary = effectBoundary('Zet niet de keukenlamp maar de ganglamp uit.');
  assert.deepEqual(boundary.denied, []);
  assert.equal(boundary.contrastive, true);
  const workspace = effectBoundary('Maak niet dit maar dat.');
  assert.deepEqual(workspace.denied, []);
  assert.equal(workspace.contrastive, true);
});

test('voiceactie begrijpt voorloopwoorden, doel, tekst en vervolg', () => {
  const rows = [
    { role: 'user', content: 'Gebruik voor mij de Voice PE in de overkapping.' },
    { role: 'user', content: 'Oké zeg nu hallo Jaap hoe is het ermee. Beetje gezellig daar in de overkapping. Dan zal ik tellen hoe lang het duurt.' },
  ];
  const first = resolveVoiceDirective({ text: rows[1].content, recentMessages: rows });
  assert.equal(first.requested, true);
  assert.equal(first.target, 'overkapping');
  assert.equal(first.text, 'hallo Jaap hoe is het ermee. Beetje gezellig daar in de overkapping');
  const retry = resolveVoiceDirective({ text: 'Probeer het nogmaals!', recentMessages: rows });
  assert.equal(retry.requested, true);
  assert.equal(retry.inherited, true);
  assert.equal(retry.target, 'overkapping');
  assert.equal(retry.text, first.text);
  const stop = resolveVoiceDirective({ text: 'Probeer het niet opnieuw.', recentMessages: rows });
  assert.equal(stop.requested, false);

  const colonCommand = resolveVoiceDirective({
    text: "Zeg via de Voice PE in de overkapping: Eva's live route werkt.",
  });
  assert.equal(colonCommand.requested, true);
  assert.equal(colonCommand.target, 'overkapping');
  assert.equal(colonCommand.text, "Eva's live route werkt.");
});

test('een Voice PE-kanaalcorrectie bij een foto is nooit een persoonsnaam', () => {
  assert.equal(identityNameCandidate('Nee via de voice pe', 'Jaap'), null);
  assert.equal(identityNameCandidate('Nee, dat is Marjan', 'Jaap'), 'Marjan');
  assert.equal(identityNameCandidate('Dit is Lola', 'Jaap'), 'Lola');
});

test('locatievragen krijgen een actuele read-only planroute', () => {
  assert.equal(locationRequest('Waar ben ik nu volgens jou?'), true);
  const plan = arbitrateTurnPlan({
    text: 'Waar ben ik nu volgens jou?',
    baseContract: { kind: 'conversation', requiredTools: [] },
    effectForTool: effects,
  });
  assert.equal(plan.contract.kind, 'location_check');
  assert.deepEqual(plan.requiredTools, ['ha_lees']);
  assert.deepEqual(plan.effectPolicy.allowedEffects, []);
});
