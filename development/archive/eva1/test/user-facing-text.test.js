import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { normalizeUserFacingText } from '../src/util.js';

const EN_DASH = String.fromCodePoint(0x2013);
const EM_DASH = String.fromCodePoint(0x2014);
const MINUS_SIGN = String.fromCodePoint(0x2212);

test('gebruikersuitvoer bewaart bereik, minteken en zinsbetekenis per codepoint', () => {
  const examples = [
    [`Open van 18 ${EN_DASH} 20 uur.`, 'Open van 18-20 uur.'],
    [`Open van 18 ${EM_DASH} 20 uur.`, 'Open van 18-20 uur.'],
    [`Buiten is het ${MINUS_SIGN}24 °C.`, 'Buiten is het -24 °C.'],
    [`waarde: ${EN_DASH}24`, 'waarde: 24'],
    [`waarde: ${EM_DASH}24`, 'waarde: 24'],
    [`Eerst dit ${EM_DASH} daarna dat.`, 'Eerst dit, daarna dat.'],
  ];

  for (const [input, expected] of examples) {
    const output = normalizeUserFacingText(input);
    assert.equal(output, expected);
    assert.equal(normalizeUserFacingText(output), output);
    assert.equal(output.includes(EN_DASH), false);
    assert.equal(output.includes(EM_DASH), false);
  }
});

test('gebruikersuitvoer bevat geen en- of em-dash en blijft natuurlijk leesbaar', () => {
  const examples = [
    ['Buiten is het 20,7 °C — nu kunnen de ramen open.', 'Buiten is het 20,7 °C, nu kunnen de ramen open.'],
    [`Buiten is het ${MINUS_SIGN}5 °C.`, 'Buiten is het -5 °C.'],
    ['De bezetting is ma–vr.', 'De bezetting is ma-vr.'],
    ['Wacht ff—hier klopt iets niet.', 'Wacht ff, hier klopt iets niet.'],
    ['Open van 09–17 uur.', 'Open van 09-17 uur.'],
    ['Eerst dit – daarna dat.', 'Eerst dit, daarna dat.'],
  ];

  for (const [input, expected] of examples) {
    const output = normalizeUserFacingText(input);
    assert.equal(output, expected);
    assert.equal(output.includes(String.fromCodePoint(0x2013)), false);
    assert.equal(output.includes(String.fromCodePoint(0x2014)), false);
  }
});

test('gebruikersuitvoer bewaart regels, gewone koppeltekens en bestaande leestekens', () => {
  assert.equal(
    normalizeUserFacingText('Eettafel links is offline. — Probeer ik later.\nEen-twee blijft staan.'),
    'Eettafel links is offline. Probeer ik later.\nEen-twee blijft staan.',
  );
  assert.equal(normalizeUserFacingText(null), '');
});

test('de canonieke Telegramtekst en fotocaption gebruiken dezelfde bewonersgrens', async () => {
  const previous = process.env.EVA_DATA_DIR;
  const dataDir = fs.mkdtempSync(path.join('/tmp', 'eva-user-facing-text-'));
  process.env.EVA_DATA_DIR = dataDir;
  const {
    canonicalTelegramCaption,
    canonicalTelegramText,
    db,
  } = await import('../src/db.js');
  try {
    assert.equal(
      canonicalTelegramText('Keuken uit — twee lampen zijn offline.'),
      'Keuken uit, twee lampen zijn offline.',
    );
    assert.equal(
      canonicalTelegramCaption('Iemand bij de deur – beeld niet beschikbaar.'),
      'Iemand bij de deur, beeld niet beschikbaar.',
    );
  } finally {
    db.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
    if (previous === undefined) delete process.env.EVA_DATA_DIR;
    else process.env.EVA_DATA_DIR = previous;
  }
});
