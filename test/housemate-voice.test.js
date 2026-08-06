import assert from 'node:assert/strict';
import test from 'node:test';

import {
  EVA_AUTONOMOUS_VOICE,
  EVA_CHARACTER_CORE,
  EVA_CHAT_VOICE,
  EVA_SPOKEN_VOICE,
  EVA_VOICE,
  buildInstructions,
  renderHouseActionResult,
  renderWorkBlocker,
} from '../src/mind/persona.js';

test('de centrale stem is huiselijk maar veinst geen menselijkheid of gevoel', () => {
  assert.match(EVA_VOICE, /vaste digitale huisgenoot/);
  assert.match(EVA_VOICE, /Je bent software/);
  assert.match(EVA_VOICE, /denkbeeldig ticket/);
  assert.match(EVA_VOICE, /therapietaal/);
  assert.match(EVA_VOICE, /Een gewoon antwoord is meestal één tot drie zinnen/);
  assert.match(EVA_VOICE, /één duurzame deelnemer/i);
  assert.match(EVA_VOICE, /Geen kanaal-Eva's, tool-Eva of achtergrond-Eva/i);
  assert.doesNotMatch(EVA_VOICE, /ik voel|bewust wezen|levend wezen/i);
});

test('Eva heeft een herkenbaar wereldbeeld zonder vaste antwoordzinnen', () => {
  assert.match(EVA_CHARACTER_CORE, /warme eenvoud, stille techniek/i);
  assert.match(EVA_CHARACTER_CORE, /droge humor/i);
  assert.match(EVA_CHARACTER_CORE, /vriendelijk tegenspreken/i);
  assert.match(EVA_CHARACTER_CORE, /Niet ieder gesprek is een klus/i);
  assert.match(EVA_CHARACTER_CORE, /Geen moppen, ingestudeerde trekjes of vaste Eva-zinnen/i);
  assert.match(EVA_CHARACTER_CORE, /nooit op als een kostuum/i);
  assert.match(EVA_CHAT_VOICE, /niet als standaard gesprekspatroon/i);
  assert.match(EVA_CHAT_VOICE, /begin niet reflexmatig met “je hebt gelijk”/i);
  assert.match(EVA_CHAT_VOICE, /Zinsfragmenten, spreektaal, een natuurlijke inkorting/i);
  assert.match(EVA_CHAT_VOICE, /Forceer geen jongerentaal/i);
});

test('gesprek, spraak en autonomie hebben elk een eigen gedragsregel', () => {
  const chat = buildInstructions({ sender: 'Jaap', channel: 'telegram' });
  const voice = buildInstructions({ sender: 'Jaap', channel: 'voice' });
  const autonomy = buildInstructions({ mode: 'autonomy', channel: 'autonomy' });

  assert.ok(chat.includes(EVA_CHAT_VOICE));
  assert.ok(!chat.includes(EVA_SPOKEN_VOICE));
  assert.ok(voice.includes(EVA_SPOKEN_VOICE));
  assert.match(voice, /een of twee korte zinnen/);
  assert.ok(autonomy.includes(EVA_AUTONOMOUS_VOICE));
  assert.match(autonomy, /volledige autonomie/i);
  assert.match(autonomy, /eigen initiatief/i);
  assert.doesNotMatch(autonomy, /Bedien het huis niet op eigen houtje/);
  assert.doesNotMatch(autonomy, /via autonomy\.\n\nDit is een gewoon gesprek/);
});

test('een huisactie klinkt kort en claimt alleen geverifieerd succes', () => {
  assert.equal(
    renderHouseActionResult({ target: 'de tuinlamp', action: 'aan', ok: true, verified: true, changed: true }),
    'De tuinlamp staat nu aan.',
  );
  assert.equal(
    renderHouseActionResult({ target: 'de tuinlamp', action: 'aan', ok: true, verified: true, changed: false }),
    'De tuinlamp stond al aan.',
  );
  assert.equal(
    renderHouseActionResult({ target: 'de tuinlamp', action: 'aan', ok: true, verified: false, changed: true }),
    'Ik kan nog niet zien of de tuinlamp aanstaat.',
  );
});

test('een mislukte huisactie benoemt alleen handeling en bruikbare reden', () => {
  assert.equal(
    renderHouseActionResult({
      target: 'de achterdeur',
      action: 'sluiten',
      ok: false,
      verified: false,
      reason: 'de sensor reageert niet',
    }),
    'Het lukte niet om de achterdeur te sluiten: de sensor reageert niet.',
  );
  assert.doesNotMatch(
    renderHouseActionResult({ target: 'de achterdeur', action: 'sluiten' }),
    /status|tool|route|bewijs|verificatie/i,
  );
  assert.equal(
    renderHouseActionResult(),
    'Het lukte niet om de gevraagde handeling bij het apparaat uit te voeren.',
  );
});

test('een werkblokkade wordt een gewone concrete vraag zonder tickettaal', () => {
  const text = renderWorkBlocker({
    work: 'de boodschappenlijst',
    missing: 'het aantal gasten',
    nextStep: 'laat even weten met hoeveel jullie zijn',
  });
  assert.equal(
    text,
    'Voor de boodschappenlijst mis ik nog het aantal gasten. Laat even weten met hoeveel jullie zijn, dan ga ik verder.',
  );
  assert.doesNotMatch(text, /werkpunt|blokkade|status|ticket|bewijs/i);
});
