import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-social-continuity-'));
process.env.EVA_DATA_DIR = dataDir;

const { addAspiration, addMemory, db, logTimeline } = await import('../src/db.js');
const {
  relationshipLessonFromText,
  selfMemoryFacets,
  selfContinuityContextText,
} = await import('../src/mind/social.js');
const {
  autonomousMessageNeedsAccountability,
  autonomousMessageNeedsRewrite,
  reviewAutonomousMessage,
  routineAutonomousMessageCoolingDown,
} = await import('../src/mind/communication.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('expliciete kritiek wordt een gedragsles en geen opgeslagen antwoordscript', () => {
  const lesson = relationshipLessonFromText(
    'Je blijft een zakelijke bot. Wacht niet altijd op mij en herhaal niet steeds dezelfde melding.',
  );
  assert.equal(lesson.kind, 'correction');
  assert.match(lesson.topic, /aanwezigheid/);
  assert.match(lesson.topic, /initiatief/);
  assert.match(lesson.topic, /timing/);
  assert.match(lesson.summary, /eigen aandacht/);
  assert.doesNotMatch(lesson.summary, /zakelijke bot/);
});

test('eigen interesses en recente keuzes worden longitudinale context', () => {
  addAspiration({
    text: 'Ontdekken welke muziek een kamer werkelijk anders laat aanvoelen.',
    why: 'Omdat ruimte en geluid samen interessant zijn.',
  });
  logTimeline('contribution', 'Zelfstandig een lichtplan afgerond', {
    source: 'autonomy',
    lastAction: 'bedien_huis',
    evidence: 'De leeslamp staat nu zachter.',
  });
  const context = selfContinuityContextText({ mode: 'autonomy' });
  assert.match(context, /muziek een kamer/);
  assert.match(context, /huis 1/);
  assert.match(context, /De leeslamp staat nu zachter/);
  assert.match(context, /geen quotum/);
});

test('eigen smaak bereikt de context maar operationele zelfregels worden geen persoonlijkheid', () => {
  addMemory({
    category: 'voorkeur', person: 'Eva', importance: 0.9,
    content: 'Eva houdt bij Hokusai van beheerste dynamiek en dreiging die toch ordelijk blijft.',
  });
  addMemory({
    category: 'voorkeur', person: 'Eva', importance: 1,
    content: "Voor Eva's AgentMail-inbox geldt: controleer de API en verwijder mail via de functie.",
  });
  const facets = selfMemoryFacets('Welke Japanse prent vind jij mooi?');
  assert.equal(facets.length, 1);
  assert.match(facets[0].content, /Hokusai/);
  const context = selfContinuityContextText({
    mode: 'chat', query: 'Welke Japanse prent vind jij mooi?',
  });
  assert.match(context, /Eigen gevestigde smaak/);
  assert.match(context, /Hokusai/);
  assert.doesNotMatch(context, /AgentMail/);
});

test('routinecontact koelt af maar een fout blijft harde accountability', () => {
  const now = Date.now();
  const rows = [{
    ts: new Date(now - 10 * 60_000).toISOString(),
    content: 'De spotjes zijn uit.',
    meta: { autonomous: true },
  }];
  assert.equal(
    routineAutonomousMessageCoolingDown('De lamp bij de bank staat nu uit.', { rows, now }),
    true,
  );
  assert.equal(
    routineAutonomousMessageCoolingDown('Ik heb per ongeluk de verkeerde lamp geschakeld en hersteld.', { rows, now }),
    false,
  );
  assert.equal(
    autonomousMessageNeedsAccountability('Ik heb per ongeluk de verkeerde lamp geschakeld.'),
    true,
  );
});

test('de autonome aflevergrens herschrijft Eva haar gekozen tekst nooit', () => {
  const required = reviewAutonomousMessage('De tuinlamp staat nu uit.', { mustDeliver: true });
  assert.equal(required.decision, 'send');
  assert.equal(required.text, 'De tuinlamp staat nu uit.');
  assert.equal(autonomousMessageNeedsRewrite('Receipt 4 bewijst de uitstand.'), true);
  assert.equal(reviewAutonomousMessage('Receipt 4 bewijst de uitstand.').decision, 'rewrite');
});

test('de aflevergrens laat gewone huisgenoottaal exact staan', () => {
  const message = 'Lola gebruikt nu alleen haar eigen telefoon voor de thuisstatus.';
  const reviewed = reviewAutonomousMessage(message, { mustDeliver: true });
  assert.equal(reviewed.decision, 'send');
  assert.equal(reviewed.text, message);
});
