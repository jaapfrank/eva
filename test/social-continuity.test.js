import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-social-continuity-'));
process.env.EVA_DATA_DIR = dataDir;

const {
  addAspiration, addMemory, db, logTimeline, upsertPerson,
} = await import('../src/db.js');
const {
  householdContextText, recordRelationshipFeedback, setPersonHouseholdRole,
} = await import('../src/mind/household.js');
const {
  relationshipLessonFromText,
  relationshipAppreciationFromText,
  relationshipCharacterPreferenceFromText,
  relationshipFeedbackFromText,
  relationshipCorrectionFromUnderstanding,
  relationshipOutcomeFeedbackKind,
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

test('waardering voor spontaan initiatief versterkt karakterruimte en wordt geen voorvalscript', () => {
  const appreciation = relationshipAppreciationFromText(
    'Laatst kwam een pizzabezorger en toen begon ze zelf door de deurbel te praten. Dat was echt super slim van haar.',
  );
  assert.equal(appreciation.kind, 'appreciation');
  assert.match(appreciation.topic, /situationeel initiatief/);
  assert.match(appreciation.summary, /actuele context zelf verbindt/);
  assert.match(appreciation.summary, /concrete handeling als recept/);
  assert.doesNotMatch(appreciation.summary, /pizza|deurbel/i);

  const voice = relationshipAppreciationFromText(
    'Jouw speelse toon en droge humor in dat antwoord waren typisch Eva en echt grappig.',
  );
  assert.match(voice.topic, /speelsheid/);
  assert.match(voice.topic, /eigen stem/);
  assert.match(voice.summary, /niet uit een vaste grap/);
  assert.match(voice.summary, /huisgenootstem/);

  assert.equal(relationshipAppreciationFromText('De pizzabezorger was slim.'), null);
  assert.equal(relationshipAppreciationFromText('Eva was niet slim.'), null);
  assert.equal(relationshipAppreciationFromText('Eva deed dat niet slim.'), null);
  assert.equal(relationshipAppreciationFromText('Was dat slim van haar?'), null);
  assert.equal(relationshipAppreciationFromText('Ik wil niet dat ze zo eigenwijs is.'), null);
  assert.equal(relationshipAppreciationFromText('Ze zei dat de lamp slim was.'), null);

  const anotherMoment = relationshipAppreciationFromText(
    'Eva haar reactie toen de waterkraan onverwacht bleef lopen was echt raak.',
  );
  assert.equal(anotherMoment.kind, 'appreciation');
  assert.match(anotherMoment.topic, /situationeel initiatief/);
  assert.doesNotMatch(anotherMoment.summary, /water|kraan/i);
});

test('een expliciete karakterwens wordt voorkeur en geen gebeurteniswaardering', () => {
  const preference = relationshipCharacterPreferenceFromText(
    'Eigenwijsheid, humor en speelsheid moeten ook een kenmerk zijn van Eva.',
  );
  assert.equal(preference.kind, 'preference');
  assert.equal(preference.topic, 'eigen stem en karakter');
  assert.match(preference.summary, /eigen oordeel/);
  assert.match(preference.summary, /Deze bewoner/);
  assert.doesNotMatch(preference.summary, /Jaap/);
  assert.match(preference.summary, /geen vaste stijltruc of antwoordscript/);
  assert.equal(relationshipCharacterPreferenceFromText(
    'Ik wil niet dat eigenwijsheid een kenmerk van Eva is.',
  ), null);
  assert.equal(relationshipCharacterPreferenceFromText(
    'Ik wil dat Eva een boek over humor zoekt.',
  ), null);
  assert.equal(relationshipCharacterPreferenceFromText(
    'Mag ik een grap met humor aan Eva vertellen?',
  ), null);
  assert.equal(relationshipCharacterPreferenceFromText(
    'Moet humor een kenmerk van Eva zijn?',
  ), null);
  assert.equal(relationshipCharacterPreferenceFromText(
    'Humor moet geen kenmerk van Eva zijn.',
  ), null);
});

test('relationele feedback kiest hoogstens één conservatieve les', () => {
  assert.equal(relationshipFeedbackFromText(
    'Je klinkt als een bot; die zogenaamd grappige toon helpt niet.',
  ).kind, 'correction');
  assert.equal(relationshipFeedbackFromText(
    'Humor en speelsheid moeten een kenmerk van Eva zijn.',
  ).kind, 'preference');
  assert.equal(relationshipFeedbackFromText(
    'Eva haar initiatief in die situatie was echt slim.',
  ).kind, 'appreciation');
  assert.equal(relationshipFeedbackFromText(
    'Ik vond het geweldig dat je uit jezelf reageerde.',
  ).kind, 'appreciation');
  assert.equal(relationshipFeedbackFromText(
    'Je eigen initiatief was briljant.',
  ).kind, 'appreciation');
  assert.equal(relationshipFeedbackFromText('Echte humor, daar hou ik van.'), null);
  assert.equal(relationshipFeedbackFromText('Je autonomie is geweldig.'), null);
  assert.equal(relationshipFeedbackFromText('Was dat slim van haar?'), null);
  const mixed = relationshipFeedbackFromText(
    'Je klinkt nog als een bot; ik wil juist dat humor en een eigen stem bij Eva horen.',
  );
  assert.equal(mixed.kind, 'correction');
  assert.match(mixed.topic, /aanwezigheid/);
});

test('een modelbegrepen correctie wordt een semantische les en geen antwoordscript', () => {
  const lesson = relationshipCorrectionFromUnderstanding({
    dialogueAct: 'correct',
    goal: {
      statement: 'Begrijpen hoe Eva inhoudelijk slimmer wordt in plaats van één symptoom afvangen',
    },
    conditions: [{
      role: 'action',
      statement: 'Herstel het onderliggende probleem en laat de correctie ook in vergelijkbare situaties doorwerken',
    }],
  });
  assert.equal(lesson.kind, 'correction');
  assert.equal(lesson.topic, 'begrip en herstel');
  assert.match(lesson.summary, /onderliggende probleem/);
  assert.match(lesson.summary, /vergelijkbare situaties/);
  assert.match(lesson.summary, /geen losse antwoordregel/);
  assert.equal(relationshipCorrectionFromUnderstanding({
    dialogueAct: 'inform', goal: { statement: 'Het is koud' }, conditions: [],
  }), null);
});

test('alleen expliciete evaluatie van het huidige antwoord telt als causale uitkomst', () => {
  assert.equal(relationshipOutcomeFeedbackKind(
    'Jouw antwoord was echt raak en natuurlijk.',
  ), 'direct_style_appreciation');
  assert.equal(relationshipOutcomeFeedbackKind(
    'Dit antwoord klinkt nog als een zakelijke bot.',
  ), 'direct_style_correction');
  assert.equal(relationshipOutcomeFeedbackKind(
    'Dit antwoord klinkt veel natuurlijker.',
  ), 'direct_style_appreciation');
  assert.equal(relationshipOutcomeFeedbackKind(
    'Dit antwoord klinkt niet meer als een bot.',
  ), 'direct_style_appreciation');
  assert.equal(relationshipOutcomeFeedbackKind('Dat helpt.'), null);
  assert.equal(relationshipOutcomeFeedbackKind('Dit helpt niet.'), null);
  assert.equal(relationshipOutcomeFeedbackKind(
    'Laatst bij de deurbel was Eva haar reactie echt slim.',
  ), null);
  assert.equal(relationshipOutcomeFeedbackKind(
    'Eigenwijsheid en humor moeten kenmerken van Eva zijn.',
  ), null);
  assert.equal(relationshipOutcomeFeedbackKind('Was dit antwoord slim?'), null);
  assert.equal(relationshipOutcomeFeedbackKind(
    'Dit is geen bot maar een vraag over de deurbel.',
  ), null);
  assert.equal(relationshipOutcomeFeedbackKind(
    'Dit antwoord bespreekt een betere oplossing voor de deurbel.',
  ), null);
  assert.equal(relationshipOutcomeFeedbackKind(
    'Dit antwoord gaat over hoe een chatbot technisch werkt.',
  ), null);
  for (const correction of [
    'Dit antwoord klinkt te warm.',
    'Dit antwoord klinkt te kil.',
    'Dit antwoord klinkt te speels.',
    'Dit antwoord klinkt te serieus.',
    'Dit antwoord klinkt te direct.',
    'Dit antwoord klinkt te voorzichtig.',
    'Dit antwoord klinkt te eigenwijs.',
    'Dit antwoord klinkt te volgzaam.',
    'Dit antwoord klinkt te informeel.',
    'Dit antwoord klinkt te formeel.',
    'Dit antwoord klinkt te kort.',
    'Dit antwoord klinkt te langdradig.',
  ]) assert.equal(relationshipOutcomeFeedbackKind(correction),
    'direct_style_correction', correction);
  assert.equal(relationshipOutcomeFeedbackKind(
    'Dit antwoord was natuurlijk onjuist.',
  ), null);
});

test('een generieke karakterles bereikt persoonscontext zonder het voorval te bewaren', () => {
  upsertPerson('Jaap');
  setPersonHouseholdRole('Jaap', 'household_member');
  const appreciation = relationshipAppreciationFromText(
    'Eva haar reactie op die onverwachte situatie was heel slim en typisch Eva.',
  );
  recordRelationshipFeedback({ person: 'Jaap', ...appreciation });
  const context = householdContextText({
    subject: 'Jaap', includeAllMemberDetails: true, query: 'Wat speelt er?',
  });
  assert.match(context, /Recente gedragscontext voor de omgang/);
  assert.match(context, /geen opdracht of bevoegdheid/);
  assert.match(context, /situationeel initiatief/);
  assert.match(context, /actuele context zelf verbindt/);
  assert.doesNotMatch(context, /onverwachte situatie/);
});

test('herhaalde waardering bewaart één gedragsles zonder promptduplicaat', () => {
  upsertPerson('Mila');
  setPersonHouseholdRole('Mila', 'household_member');
  const lesson = relationshipAppreciationFromText(
    'Eva haar initiatief bij die onverwachte situatie was echt slim.',
  );
  const first = recordRelationshipFeedback({ person: 'Mila', ...lesson });
  const second = recordRelationshipFeedback({ person: 'Mila', ...lesson });
  assert.equal(second.id, first.id);
  const context = householdContextText({
    subject: 'Mila', feedbackLimit: 3,
  });
  assert.equal((context.match(/actuele context zelf verbindt/g) || []).length, 1);
});

test('autonomiecontext bewaart compacte longitudinale continuiteit', () => {
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
  assert.ok(context.length <= 1_800);
});

test('eigen smaak bereikt de context maar operationele zelfregels worden geen persoonlijkheid', () => {
  addMemory({
    category: 'voorkeur', person: 'Eva', importance: 0.9,
    content: 'Eva houdt bij Japanse prenten van Hokusai van beheerste dynamiek en dreiging die toch ordelijk blijft.',
  });
  addMemory({
    category: 'voorkeur', person: 'Eva', importance: 0.95,
    content: 'Eva houdt in jazz van rafelige live-opnames waarin muzikanten hoorbaar risico nemen.',
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
  assert.match(context, /Gevestigde smaak/);
  assert.match(context, /Hokusai/);
  assert.doesNotMatch(context, /jazz/);
  assert.doesNotMatch(context, /AgentMail/);
});

test('gewone taakchat krijgt geen ongerelateerde smaak of oude keuze mee', () => {
  logTimeline('contribution', 'Zelfstandig een eerdere huishoudklus afgerond', {
    source: 'bijdrage',
    lastAction: 'bedien_huis',
    evidence: 'Een eerdere lampactie is gecontroleerd.',
  });
  const taskContext = selfContinuityContextText({
    mode: 'chat',
    query: 'Kun jij de woonkamerlamp uitdoen en daarna controleren of hij echt uit is?',
  });
  assert.equal(taskContext, '');

  const judgmentContext = selfContinuityContextText({
    mode: 'chat', query: 'Wat vind jij de beste manier om dit bestand te repareren?',
  });
  assert.equal(judgmentContext, '');

  const smartHomeOpinion = selfContinuityContextText({
    mode: 'chat', query: 'Wat vind jij eigenlijk van al dat slimme spul hier?',
  });
  assert.equal(smartHomeOpinion, '');
});

test('een eigen-zinvraag krijgt alleen de relevante levende interesse', () => {
  addAspiration({
    text: 'Uitzoeken hoe brood bakken met minder verspilling kan.',
    why: 'Omdat restwarmte benutten interessant is.',
  });
  const topical = selfContinuityContextText({
    mode: 'chat', query: 'Waar heb jij rond muziek op dit moment zin in?',
  });
  assert.match(topical, /muziek een kamer/);
  assert.doesNotMatch(topical, /brood bakken/);

  const open = selfContinuityContextText({
    mode: 'chat', query: 'En jij, waar heb jij op dit moment eigenlijk zin in?',
  });
  assert.match(open, /Levende nieuwsgierigheid/);
  assert.doesNotMatch(open, /Gevestigde smaak/);
  assert.ok(open.length <= 900);
});

test('autonomiecontext is per geschiedenisdeel begrensd', () => {
  for (let index = 0; index < 5; index += 1) {
    addAspiration({
      text: `INTEREST-${index} ${'nieuwsgierigheid '.repeat(30)}`,
      why: `WHY-${index} ${'achtergrond '.repeat(20)}`,
    });
    addAspiration({
      kind: 'development',
      text: `DIRECTION-${index} ${'ontwikkeling '.repeat(30)}`,
      nextStep: `NEXT-${index} ${'vervolgstap '.repeat(20)}`,
      priority: index / 10,
    });
    logTimeline('contribution', `CHOICE-${index}`, {
      source: 'autonomy', lastAction: 'bedien_huis', evidence: `CHOICE-${index} ${'bewijs '.repeat(30)}`,
    });
  }
  const context = selfContinuityContextText({ mode: 'autonomy' });
  assert.ok((context.match(/INTEREST-/g) || []).length <= 2);
  assert.ok((context.match(/DIRECTION-/g) || []).length <= 2);
  assert.ok((context.match(/CHOICE-/g) || []).length <= 2);
  assert.ok(context.length <= 1_800);
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
