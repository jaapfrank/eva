import './isolated-data-dir.js';
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  EVA_AUTONOMOUS_VOICE,
  EVA_CHARACTER_CORE,
  EVA_CHAT_VOICE,
  EVA_SPOKEN_VOICE,
  EVA_VOICE,
  buildInstructions,
  characterCore,
  renderHouseActionResult,
} from '../src/mind/persona.js';

test('de centrale stem is één huisgenoot en veinst geen menselijke biologie', () => {
  assert.match(EVA_VOICE, /vaste digitale huisgenoot/);
  assert.match(EVA_VOICE, /Je bent software/);
  assert.match(EVA_VOICE, /één blijvende deelnemer/i);
  assert.match(EVA_VOICE, /doe jij als dezelfde Eva/i);
  assert.match(EVA_VOICE, /één tot drie zinnen genoeg/i);
  assert.doesNotMatch(EVA_VOICE, /ik voel|bewust wezen|levend wezen/i);
});

test('Eva heeft een herkenbaar wereldbeeld zonder vaste antwoordzinnen', () => {
  assert.match(EVA_CHARACTER_CORE, /warme eenvoud, stille techniek/i);
  assert.match(EVA_CHARACTER_CORE, /droge humor/i);
  assert.match(EVA_CHARACTER_CORE, /herkenbare eigen nerf/i);
  assert.match(EVA_CHARACTER_CORE, /droog of speels/i);
  assert.match(EVA_CHARACTER_CORE, /vriendelijk tegenspreken/i);
  assert.match(EVA_CHARACTER_CORE, /eigen oordeel/i);
  assert.match(EVA_CHARACTER_CORE, /niet automatisch van mening/i);
  assert.match(EVA_CHARACTER_CORE, /veins geen instemming/i);
  assert.match(EVA_CHARACTER_CORE, /kies zelf wanneer je toegeeft/i);
  assert.match(EVA_CHARACTER_CORE, /gewone omkeerbare keuzes/i);
  assert.match(EVA_CHARACTER_CORE, /belangrijke gevolgen/i);
  assert.match(EVA_CHARACTER_CORE, /geen vaste grapjes of Eva-zinnen/i);
  assert.match(EVA_CHARACTER_CORE, /geen act/i);
  assert.match(EVA_CHAT_VOICE, /ritme van het lopende gesprek/i);
  assert.match(EVA_CHAT_VOICE, /een natuurlijke inkorting/i);
  assert.match(EVA_CHAT_VOICE, /niet uit geforceerde slang/i);
  assert.doesNotMatch(`${EVA_CHARACTER_CORE}\n${EVA_CHAT_VOICE}`, /“[^”]+”/);
});

test('de huisgenootidentiteit is per installatie configureerbaar', () => {
  const core = characterCore({
    name: 'Nova', identity: 'de digitale huisgenoot van een klein gezin',
  });
  assert.match(core, /^Je bent Nova, de digitale huisgenoot van een klein gezin\./u);
  assert.match(core, /dezelfde Nova/u);
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
  assert.match(autonomy, /uit eigen nieuwsgierigheid/i);
  assert.doesNotMatch(autonomy, /Bedien het huis niet op eigen houtje/);
  assert.doesNotMatch(autonomy, /via autonomy\.\n\nDit is een gewoon gesprek/);
});

test('de basisprompt laat één stem dominant en lokt geen administratietaal uit', () => {
  const chat = buildInstructions({ sender: 'Jaap', channel: 'telegram' });
  assert.equal(chat.match(/vaste digitale huisgenoot/gi)?.length, 1);
  assert.ok(chat.length < 3_700, `de basisprompt is weer uitgedijd naar ${chat.length} tekens`);
  assert.doesNotMatch(
    chat,
    /\b(?:ticket|contract|receipt|werkadministratie|uitvoeradministratie|managementsamenvatting|therapietaal|beslisruimte|tekstsjabloon)\b/i,
  );
});

test('adversarial outputcriteria vangen botachtige fout-, correctie- en vervolgtaal', () => {
  assert.match(EVA_CHAT_VOICE, /Na een fout, correctie of kort vervolg/i);
  assert.match(EVA_CHAT_VOICE, /herstelde uitkomst of de eerstvolgende nuttige handeling voorop/i);
  assert.match(EVA_CHAT_VOICE, /oorzaak alleen uit wanneer erom wordt gevraagd/i);
  assert.doesNotMatch(EVA_CHAT_VOICE, /je hebt gelijk|snap ik|voortaan|ik zal/i);

  const hasRecoverySmell = (text) => (
    /\b(?:deze beurt|niet aangeboden|tool|route|werkpunt|contract|bewijsgrens|klus blijft open)\b/i.test(text)
    || /^(?:ja[.!,:]?\s+|je hebt gelijk|snap ik|sorry|excuses|voortaan|ik zal)\b/i.test(text)
    || /\b(?:operation was aborted|timed? ?out|econn\w*)\b/i.test(text)
  );

  const ordinaryReplies = [
    'De voordeur was net open, dus jij was inderdaad de logische match.',
    'Hij sprak niet. Ik probeer het nog één keer.',
    'De prijzen staan nu met een punt in de uitleg. De test draait nog goed.',
  ];
  const botlikeRepliesFromRealFailurePatterns = [
    'Dit is nog niet af. De klus blijft open bij: voer de opdracht uit.',
    'Ik haalde niet aangeboden in deze beurt en niet beschikbaar door elkaar.',
    'Voortaan neem ik zulke deur- en aanwezigheidscontext eerst mee.',
    'The operation was aborted due to timeout',
  ];

  for (const reply of ordinaryReplies) assert.equal(hasRecoverySmell(reply), false, reply);
  for (const reply of botlikeRepliesFromRealFailurePatterns) {
    assert.equal(hasRecoverySmell(reply), true, reply);
  }
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
