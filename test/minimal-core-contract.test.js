import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const dataDir = fs.mkdtempSync(path.join('/tmp', 'eva-core-contract-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_TEST_SECRET = 'must-not-leak';
process.env.EVA_AUTONOMY_LEVEL = 'bounded';
// Deze suite test ook de expliciet ingeschakelde oude autonome bouwcyclus.
// De productie-default wordt apart fail-closed getest.
process.env.EVA_AUTONOMY_CODE_EXECUTION = 'true';

const { __projectRouteTest } = await import('../src/mind/agent.js');
const {
  __autonomyScheduleTest,
  hasNightUrgentSignal,
  shouldQueueWorldSignal,
  shouldWakeForWorldSignal,
} = await import('../src/mind/autonomy.js');
const { runAgent } = await import('../src/brain/llm.js');
const { __toolsTest, executeTool, toolsForTurn } = await import('../src/mind/tools.js');
const { normalizeVoiceConversationId } = await import('../src/mind/voicebridge.js');
const {
  archiveTelegramMessage,
  claimTurnToolExecution,
  completeTurnToolExecution,
  telegramOutboundByDedupeKey,
} = await import('../src/db.js');
const {
  claimWorkItem,
  createWorkItem,
  listWorkItems,
  recoverWorkLeases,
  releaseWorkLease,
  updateWorkItem,
} = await import('../src/mind/work.js');
const {
  renderCameraNotice,
  renderReminderText,
} = await import('../src/mind/persona.js');

function tool({
  name,
  ok = true,
  verified = true,
  changed = false,
  data = null,
} = {}) {
  return {
    name,
    ok,
    verified,
    changed,
    output: JSON.stringify({
      contract: 'ActionResult/v1',
      ok,
      verified,
      changed,
      data,
    }),
  };
}

test('expliciete opdrachten verplichten de passende kerntools', () => {
  const required = __projectRouteTest.requiredToolsFor;
  assert.deepEqual(required({ text: 'Zet het licht buiten aan' }), ['bedien_huis']);
  assert.deepEqual(required({ text: 'Kun je het licht buiten aanzetten?' }), ['bedien_huis']);
  assert.deepEqual(required({ text: 'Kun je light.unifi_schuur_led uitzetten?' }), ['bedien_huis']);
  assert.deepEqual(required({ text: 'Zoek uit welke warmtepomp nu het zuinigst is' }), ['zoek_internet']);
  assert.deepEqual(required({ text: 'Kun je uitzoeken welke warmtepomp nu het zuinigst is?' }), ['zoek_internet']);
  assert.deepEqual(
    required({ text: 'Bouw een werkende calculator als programma' }),
    ['schrijf_bestand', 'voer_uit', 'lees_bestand'],
  );
  assert.deepEqual(
    required({ text: 'Zet in README-sluipverbruik.txt onderaan een korte uitleg. Draai daarna de test.' }),
    ['lees_bestand', 'schrijf_bestand', 'voer_uit'],
  );
  assert.deepEqual(
    required({ text: 'Werk de README bij met een voorbeeld.' }),
    ['lees_bestand', 'schrijf_bestand'],
  );
  assert.deepEqual(
    required({
      text: 'De spots leken eerst uit. Zoek via Home Assistant uit of ze later weer aan gingen en waardoor.',
    }),
    ['huis_status', 'ha_lees'],
  );
  assert.deepEqual(
    required({ text: 'Verwijder Lola haar oude telefoontracker en gebruik alleen device_tracker.unifi_default_5e_eb_f8_cb_fc_27 voor aanwezigheid.' }),
    ['beheer_aanwezigheid'],
  );
  assert.deepEqual(
    required({ text: 'Erik staat met een sok in zijn mond en Lola kijkt alsof dit een interventie wordt. Wat denk jij?' }),
    [],
  );
  assert.deepEqual(
    required({ text: 'Maak deze calculator af: zorg dat de invoer wordt gevalideerd' }),
    ['schrijf_bestand', 'voer_uit', 'lees_bestand'],
  );
  assert.deepEqual(
    required({ text: 'Ik wil voor elk apparaat een apart dashboard maken. Goed idee toch?' }),
    [],
  );
  assert.deepEqual(
    required({ text: 'Maak een dashboard met de belangrijkste apparaten.' }),
    ['schrijf_bestand', 'lees_bestand'],
  );
  assert.deepEqual(
    required({ text: 'Zoek honderd actuele problemen, kies de beste en bouw een werkende oplossing.' }),
    ['zoek_internet', 'schrijf_bestand', 'voer_uit', 'lees_bestand'],
  );
  assert.deepEqual(
    required({ text: 'Regel iets voor vanavond, bepaal zelf wat past en zet het echt klaar.' }),
    ['schrijf_bestand', 'lees_bestand'],
  );
  assert.deepEqual(
    required({ text: 'Implementeer en test één kleine veilige eigen verbetering aan jezelf.' }),
    ['schrijf_bestand', 'voer_uit', 'lees_bestand', 'maak_vaardigheid'],
  );
  assert.deepEqual(
    required({ text: 'Bekijk je recente echte chatbeurten, kies waar je niet doorpakt en verbeter die zwakte met een kleine wijziging in je eigen systeem en een gerichte test.' }),
    ['schrijf_bestand', 'voer_uit', 'lees_bestand', 'maak_vaardigheid'],
  );
  assert.deepEqual(
    required({ text: 'Onderzoek welke addon-, skill- of pluginmechanismen je werkelijk kunt gebruiken en demonstreer er één veilig.' }),
    ['gebruik_capability'],
  );
  assert.deepEqual(
    required({ text: 'Bouw een kleine Python-CLI boodschappen_samenvoeger.py, schrijf tests, voer ze uit en controleer een echte CLI-run.' }),
    ['schrijf_bestand', 'voer_uit', 'lees_bestand'],
  );
  assert.equal(
    __projectRouteTest.isDurableProjectRequest('Regel iets voor vanavond en zet het echt klaar.'),
    true,
  );
  assert.deepEqual(
    required({
      text: 'Dit is Frank',
      replyEvidence: { imageDataUrl: 'data:image/jpeg;base64,AA==' },
    }),
    ['leer_gezicht_van_foto'],
  );
  assert.deepEqual(
    required({
      text: 'Nee Jaap.',
      replyEvidence: { imageDataUrl: 'data:image/jpeg;base64,AA==' },
    }),
    ['leer_gezicht_van_foto'],
  );
});

test('een beschreven robotscene is geen bedieningsopdracht', () => {
  const requiredToolsFor = __projectRouteTest.requiredToolsFor;
  assert.deepEqual(
    requiredToolsFor({
      text: 'Erik rijdt met de robotstofzuiger rond met een sok half onder zich, en Lola kijkt alsof ze een interventie gaat houden. Wat denk je?',
    }),
    [],
  );
  assert.deepEqual(
    requiredToolsFor({ text: 'De robotstofzuiger draait in de keuken en de hond kijkt ernaar. Grappig toch?' }),
    [],
  );
  assert.deepEqual(requiredToolsFor({ text: 'Erik draait in de keuken.' }), []);
  assert.deepEqual(requiredToolsFor({ text: 'Erik start om acht uur.' }), []);
  assert.deepEqual(requiredToolsFor({ text: 'Erik stopt bij de stoel.' }), []);
  assert.deepEqual(requiredToolsFor({ text: 'Erik keert terug.' }), []);
  assert.deepEqual(requiredToolsFor({ text: 'Laat Erik met rust.' }), []);
  assert.deepEqual(requiredToolsFor({ text: 'Start Erik niet; ik beschrijf alleen een scène.' }), []);
  assert.deepEqual(requiredToolsFor({ text: 'Waar is de gewone stofzuiger?' }), []);
  assert.deepEqual(requiredToolsFor({ text: 'Laat Erik even rondrijden in de keuken.' }), ['gebruik_robot']);
  assert.deepEqual(requiredToolsFor({ text: 'Kan je Erik terugsturen naar zijn dock?' }), ['gebruik_robot']);
  assert.deepEqual(requiredToolsFor({ text: 'Waar is Erik?' }), ['gebruik_robot']);
  assert.deepEqual(requiredToolsFor({ text: 'Erik, terug naar je dock.' }), ['gebruik_robot']);
  assert.deepEqual(requiredToolsFor({ text: 'Stuur Erik naar zijn dock.' }), ['gebruik_robot']);
  assert.deepEqual(requiredToolsFor({ text: 'De robot moet stoppen.' }), ['gebruik_robot']);
  assert.deepEqual(requiredToolsFor({ text: 'Wat doet Erik nu?' }), ['gebruik_robot']);
  assert.deepEqual(requiredToolsFor({ text: 'Rijdt Erik nog?' }), ['gebruik_robot']);
  assert.deepEqual(requiredToolsFor({ text: 'Maak de keuken schoon met Erik.' }), ['gebruik_robot']);
});

test('korte foto-antwoorden worden zonder modelgok als identiteit of bevestiging gelezen', () => {
  const parseName = __projectRouteTest.identityNameFromText;
  assert.equal(parseName('Dat is Lola', 'Jaap'), 'Lola');
  assert.equal(parseName('Nee Jaap.', 'Jaap'), 'Jaap');
  assert.equal(parseName('Dit ben ik', 'Jaap'), 'Jaap');
  assert.equal(parseName('Dat klopt', 'Jaap'), null);

  assert.equal(
    __projectRouteTest.confirmedRecognitionName('Dat klopt', {
      text: 'Jaap is bij G4 Doorbell Pro PoE.\nCamera: G4 Doorbell Pro PoE',
    }),
    'Jaap',
  );
  assert.equal(
    __projectRouteTest.confirmedRecognitionName('Dat klopt', {
      text: 'Er is iemand bij de voordeur.',
    }),
    null,
  );
});

test('alleen een ondubbelzinnige enkelvoudige huisopdracht gebruikt de snelle kernroute', () => {
  const parse = __projectRouteTest.directHomeCommand;
  assert.deepEqual(parse('Zet light.unifi_schuur_led aan.'), {
    doel: 'light.unifi_schuur_led',
    actie: 'aan',
  });
  assert.deepEqual(parse('Zet die schuur-led weer uit.'), {
    doel: 'schuur-led',
    actie: 'uit',
  });
  assert.deepEqual(parse('Sluit het woonkamerrolluik.'), {
    doel: 'woonkamerrolluik',
    actie: 'dicht',
  });
  assert.equal(parse('Zet keuken en woonkamer uit.'), null);
  assert.equal(parse('Zet alle verlichting uit.'), null);
  assert.deepEqual(parse('Kun je het licht buiten aanzetten?'), {
    doel: 'licht buiten',
    actie: 'aan',
  });

  const route = __projectRouteTest.directHomeForTurn;
  assert.deepEqual(route({
    mode: 'chat', requiredTools: ['bedien_huis'],
    userText: 'Zet de spotjes onder de overkapping uit.',
  }), { doel: 'spotjes onder de overkapping', actie: 'uit' });
  assert.equal(route({
    mode: 'chat', requiredTools: ['bedien_huis'], syntheticTest: true,
    userText: 'Zet de spotjes onder de overkapping uit.',
  }), null);
});

test('een voorbereide autonome terugmelding bewaart exact tekst, ontvanger en retry-identiteit', () => {
  assert.deepEqual(__projectRouteTest.pendingDeliveryForWork({
    state_json: JSON.stringify({ pending_delivery: {
      text: 'De spots zijn uit; Buiten liet ik bewust met rust.',
      recipient: 'Jaap', preparedAt: '2026-08-06T00:00:00.000Z',
      attempts: 2, dedupeKey: 'episode:7:prepared-delivery',
    } }),
  }), {
    text: 'De spots zijn uit; Buiten liet ik bewust met rust.',
    recipient: 'Jaap', preparedAt: '2026-08-06T00:00:00.000Z',
    attempts: 2, dedupeKey: 'episode:7:prepared-delivery',
  });
});

test('meerstapswerk blijft een project tot er een echt eindresultaat is', () => {
  const durable = __projectRouteTest.isDurableProjectRequest;
  assert.equal(durable('Zoek uit welke warmtepomp nu het zuinigst is.'), false);
  assert.equal(
    durable('Ga kijken welke problemen je kunt oplossen en waar je geld voor kunt vragen.'),
    true,
  );
  assert.equal(
    durable('Zoek honderd actuele problemen, groepeer ze, kies er drie en bouw de beste oplossing.'),
    true,
  );
  assert.equal(__projectRouteTest.isContinuationRequest('Ga daar mee verder.'), true);
  assert.equal(__projectRouteTest.isContinuationRequest('Maak dit af.'), true);
  assert.equal(__projectRouteTest.isContinuationRequest('Ja, doe dat.'), true);
  assert.equal(__projectRouteTest.isContinuationRequest('Vertel eens verder over Hokusai.'), false);

  const searched = tool({ name: 'zoek_internet' });
  assert.equal(
    __projectRouteTest.contributionFromTools(
      [searched],
      'chat',
      'Zoek honderd actuele problemen, groepeer ze en kies de beste.',
      { durable: true },
    ),
    null,
  );
});

test('natuurlijke AgentMail-vervolgtaal houdt dezelfde uitvoeropdracht vast', () => {
  for (const phrase of [
    'Oké prima ga verder met AgentMail.',
    'Doe dat dan.',
    'Meld je aan.',
    'Je moet je aanmelden.',
  ]) assert.equal(__projectRouteTest.isContinuationRequest(phrase), true, phrase);
  assert.deepEqual(
    __projectRouteTest.requiredToolsFor({ text: 'Meld je aan bij AgentMail.' }),
    ['beheer_agentmail'],
  );
});

test('aanwezigheidsbeheer is uitvoerbaar maar nooit muterend in een synthetische test', () => {
  assert.equal(__projectRouteTest.syntheticToolAllowed('beheer_aanwezigheid'), false);
  const selected = __projectRouteTest.chatToolNamesFor(['beheer_aanwezigheid']);
  assert.equal(selected.has('beheer_aanwezigheid'), true);
  assert.equal(
    __projectRouteTest.chatToolNamesFor(['zoek_internet']).has('beheer_aanwezigheid'),
    false,
  );
});

test('een gedelegeerde klus kan geen ongerelateerde fysieke zijactie uitvoeren', () => {
  const allows = __projectRouteTest.delegatedWorkAllowsTool;
  const presenceWork = {
    owner: 'Jaap',
    goal: 'Laat Lola haar aanwezigheid via wifi volgen.',
    next_step: 'Vervang de person tracker en controleer de thuisstatus.',
  };
  assert.equal(allows('beheer_aanwezigheid', presenceWork), true);
  assert.equal(allows('bedien_huis', presenceWork), false);
  assert.equal(allows('bedien_huis', {
    owner: 'Jaap', goal: 'Zet de lamp in de wc uit en controleer dat hij uit staat.',
  }), true);
  assert.equal(allows('bedien_huis', { owner: null, goal: 'vrije aandacht' }), true);
});

test('een providerdomein in voorkeurstaal is geen programmeeropdracht', () => {
  assert.equal(
    __projectRouteTest.requestsExecutableBuild('Maakt me niet uit, bijvoorbeeld openmail.sh.'),
    false,
  );
  assert.equal(__projectRouteTest.requestsExecutableBuild('Maak data/workspace/proef.sh.'), true);
});

test('gedeeltelijk bewijs met vervolgwerk voldoet niet aan een uitkomstcontract', () => {
  const partial = {
    name: 'zoek_internet', ok: true, verified: true,
    output: JSON.stringify({ ok: true, verified: true, partial: true, needs_followup: true }),
  };
  assert.equal(__projectRouteTest.requiredToolSatisfied('zoek_internet', [partial]), false);
  const waiting = {
    name: 'beheer_agentmail', ok: true, verified: true,
    output: JSON.stringify({ ok: true, verified: true, partial: true, needs_followup: true, waiting_for: 'human_otp' }),
  };
  assert.equal(__projectRouteTest.requiredToolSatisfied('beheer_agentmail', [waiting]), true);
});

test('een HA-tijdlijnvraag vereist werkelijk een entiteitslogboek en geen algemene states-dump', () => {
  const userText = 'Zoek via Home Assistant uit of de spots vanochtend eerst uit en later weer aan gingen.';
  const general = {
    ...tool({ name: 'ha_lees' }),
    args: { pad: '/api/states' },
  };
  const logbook = {
    ...tool({ name: 'ha_lees' }),
    args: {
      pad: '/api/logbook/2026-08-06T05:00:00.000Z?entity=light.dimmable_light_1&end_time=2026-08-06T08:00:00.000Z',
    },
  };
  assert.equal(__projectRouteTest.turnRequirementSatisfied('ha_lees', [general], userText), false);
  assert.equal(__projectRouteTest.turnRequirementSatisfied('ha_lees', [general, logbook], userText), true);
});

test('een uitzoekopdracht eindigt niet met alleen een aangekondigde volgende route', () => {
  const feedback = __projectRouteTest.incompleteRequestedOutcomeFeedback(
    'Ik pak de Home Assistant-logboekroute zelf; dit bronbestand was de verkeerde lade.',
    'Zoek via Home Assistant uit wat er met de spots gebeurde.',
  );
  assert.match(feedback, /alleen een aankondiging/i);
  assert.equal(
    __projectRouteTest.incompleteRequestedOutcomeFeedback(
      'De spots gingen om 07:36 uit. De bron laat niet zien waarom ze later weer aan gingen.',
      'Zoek via Home Assistant uit wat er met de spots gebeurde.',
    ),
    '',
  );
});

test('werk iets uit is geen toevallige huis-uit-opdracht', () => {
  assert.deepEqual(
    __projectRouteTest.requiredToolsFor({ text: 'Doe dit. Werk het meteen uit tot een resultaat.' }),
    [],
  );
  assert.deepEqual(
    __projectRouteTest.requiredToolsFor({ text: 'Doe de lamp in het kantoor uit.' }),
    ['bedien_huis'],
  );
  assert.deepEqual(
    __projectRouteTest.requiredToolsFor({ text: 'Ga uitzoeken waarom je geen toegang hebt tot Home Assistant.' }),
    ['huis_status', 'ha_lees'],
  );
});

test('dubbel verpakte toolresultaten worden gewone mensentaal', () => {
  const inner = JSON.stringify({
    ok: true,
    verified: true,
    message: 'Drie concrete problemen zijn gecontroleerd; nummer twee heeft de duidelijkste koper.',
    sources: ['https://example.test/bron'],
  });
  const outer = JSON.stringify({
    contract: 'ActionResult/v1',
    tool: 'zoek_internet',
    ok: true,
    verified: true,
    human_message: '',
    evidence: { summary: inner },
    data: { truncated: true, text: inner },
  });
  assert.equal(
    __projectRouteTest.humanResultText(outer, { preferDetailed: true }),
    'Drie concrete problemen zijn gecontroleerd; nummer twee heeft de duidelijkste koper.',
  );
  const fallback = __projectRouteTest.evidenceFallback([{
    name: 'zoek_internet',
    ok: true,
    verified: true,
    output: outer,
  }]);
  assert.equal(fallback.startsWith('{'), false);
  assert.equal(fallback.includes('Mijn afrondende antwoord viel weg'), false);
  const requiredFallback = __projectRouteTest.evidenceFallback([
    { name: 'zoek_internet', ok: true, verified: true, output: outer },
    tool({ name: 'voer_uit', changed: true, data: { stdout: 'RUWE INTERNE UITVOER' } }),
  ], { requiredTools: ['zoek_internet'], userText: 'Hoe kunnen we geld verdienen?' });
  assert.match(requiredFallback, /Drie concrete problemen/);
  assert.doesNotMatch(requiredFallback, /RUWE INTERNE UITVOER/);
});

test('zelfopdeling en interne kanaaltaal moeten vóór aflevering worden vernederlandst', () => {
  const needsNarration = __projectRouteTest.replyNeedsNarration;
  assert.equal(needsNarration('In deze beurt zijn de Home Assistant-tools niet aangeboden.'), true);
  assert.equal(needsNarration('Dat hoort bij een ander systeem dan deze route.'), true);
  assert.equal(needsNarration('Ik krijg de overkapping nu niet bereikt.'), false);
});

test('een inhoudelijke correctie wordt geen voortaan-beleidsverklaring', () => {
  const feedback = __projectRouteTest.styleCorrectionPolicyFeedback;
  assert.match(
    feedback(
      'Voortaan neem ik de deurcontext eerst mee.',
      'Je had kunnen weten dat ik het was omdat de voordeur net open was gegaan.',
    ),
    /geen beleid/,
  );
  assert.equal(
    feedback(
      'De net geopende voordeur maakte jou inderdaad de logische persoon.',
      'Je had kunnen weten dat ik het was omdat de voordeur net open was gegaan.',
    ),
    '',
  );
});

test('een capabilitydemonstratie valt terug op controleerbare gewone taal', () => {
  const used = tool({
    name: 'gebruik_capability',
    changed: false,
    data: {
      demonstrated: true,
      receipt_id: 12,
      capability: { kind: 'skill', name: 'antwoord-afmaakpoort', version: 2 },
    },
  });
  const answer = __projectRouteTest.evidenceFallback([used], {
    requiredTools: ['gebruik_capability'],
    userText: 'Demonstreer een skill.',
  });
  assert.match(answer, /antwoord-afmaakpoort v2/);
  assert.match(answer, /receipt #12/);
  assert.doesNotMatch(answer, /\{\"/);
});

test('nieuw bewijs binnen hetzelfde project is geen dubbele autonome melding', () => {
  const first = 'Ik heb een eerste lokale Home Assistant-configuratie-audit gebouwd en getest. Het rapport vond 6 problemen.';
  const progress = 'De regressietest van de Home Assistant-configuratie-audit is nu apart gecontroleerd en slaagt met de bedoelde exitcode.';
  const repeat = 'Ik heb de lokale Home Assistant-configuratie-audit gebouwd en getest; het rapport vond 6 problemen.';
  assert.equal(__toolsTest.autonomousMessagesOverlap(progress, first), true);
  assert.equal(__toolsTest.autonomousMessageHasNewResult(progress, first), true);
  assert.equal(__toolsTest.autonomousMessageHasNewResult(repeat, first), false);
  const liveFirst = 'De ontbrekende regressietest voor de Home Assistant-audit is nu gecontroleerd: alle 4 varianten met lege trigger- of actieblokken worden correct afgekeurd. Dit dicht een echt gat in de audit; het is nog geen verdiend geld.';
  const liveRepeat = 'De ontbrekende regressietest staat nu weer op de juiste plek en is opnieuw uitgevoerd: alle vier lege trigger-/actievarianten worden correct afgekeurd. Daarmee is die concrete blokkade uit het werkboek weg; dit is nog geen verdiend geld.';
  assert.equal(__toolsTest.autonomousMessagesOverlap(liveRepeat, liveFirst), true);
  assert.equal(__toolsTest.autonomousMessageHasNewResult(liveRepeat, liveFirst), false);

  const doorFirst = 'De achterdeur staat open terwijl het buiten koeler is.';
  const doorRepeat = 'Omdat de koelte helpt, kan die deur nog open blijven.';
  const doorChanged = 'De achterdeur staat nog open, maar het regent nu naar binnen.';
  assert.equal(__toolsTest.autonomousMessagesOverlap(doorRepeat, doorFirst), true);
  assert.equal(__toolsTest.autonomousMessageHasNewResult(doorRepeat, doorFirst), false);
  assert.equal(__toolsTest.autonomousMessageHasNewResult(doorChanged, doorFirst), true);
});

test('een expliciete intentie krijgt geen ongerelateerde gereedschappen', () => {
  const select = __projectRouteTest.chatToolNamesFor;
  const research = select(['zoek_internet']);
  assert.equal(research.has('zoek_internet'), true);
  // Na een mislukte voorkeursroute blijven alleen veilige diagnose-/leesroutes
  // beschikbaar; muterende tools worden niet stil toegevoegd.
  for (const name of ['bedien_huis', 'schrijf_bestand', 'voer_uit', 'stuur_bericht']) {
    assert.equal(research.has(name), false);
  }
  const programming = select(['schrijf_bestand', 'voer_uit', 'lees_bestand']);
  for (const name of ['schrijf_bestand', 'voer_uit', 'lees_bestand', 'lijst_bestanden']) {
    assert.equal(programming.has(name), true);
  }
  for (const name of ['bedien_huis', 'stuur_bericht']) {
    assert.equal(programming.has(name), false);
  }
  assert.equal(select([]), null);
});

test('synthetische livegesprekken kunnen productieplanning niet besmetten', () => {
  const allowed = __projectRouteTest.syntheticToolAllowed;
  for (const name of [
    'werk_bijhouden', 'verkenning_bijhouden', 'onthoud', 'zet_herinnering',
    'bedien_huis', 'gebruik_robot', 'stuur_bericht', 'beheer_agentmail',
    'maak_vaardigheid', 'beheer_addon', 'toon_werk', 'zoek_geheugen',
    'zoek_gesprek', 'signalen_afhandelen',
  ]) assert.equal(allowed(name), false, `${name} mocht muteren`);
  for (const name of ['huis_status', 'ha_lees', 'lees_bestand', 'schrijf_bestand', 'voer_uit']) {
    assert.equal(allowed(name), true, `${name} had als geïsoleerde proef beschikbaar moeten blijven`);
  }
  assert.equal(
    __projectRouteTest.syntheticReplyNeedsPersistentState('Morgen om half tien pak ik dit weer op.'),
    true,
  );
  assert.equal(
    __projectRouteTest.syntheticReplyNeedsPersistentState('Ik heb de bron nu gecontroleerd.'),
    false,
  );
});

test('een synthetisch antwoord kan geen echte toekomstige belofte laten staan', () => {
  const reply = 'Ik heb een controleplan gemaakt. Morgenochtend pak ik dat zelfstandig weer op.';
  assert.equal(__projectRouteTest.syntheticReplyNeedsPersistentState(reply), true);
  assert.equal(
    __projectRouteTest.stripSyntheticFutureClaims(reply),
    'Ik heb een controleplan gemaakt.',
  );
});

test('meerstapsuitvoering krijgt een episode zonder vaste projectwoordenschat', () => {
  const bind = __projectRouteTest.shouldBindDurableUserEpisode;
  assert.equal(bind({ explicitTool: true, buildWork: true, requiredToolCount: 3 }), true);
  assert.equal(bind({ explicitTool: true, requiredToolCount: 2 }), true);
  assert.equal(bind({ explicitTool: true, requiredToolCount: 1 }), false);
  assert.equal(bind({ projectWork: true }), true);
});

test('een terugblik leest bestaand bewijs en is geen nieuwe uitvoeropdracht', () => {
  const isRecap = __projectRouteTest.isWorkRecapQuery;
  assert.equal(isRecap('Wat heb je vandaag echt gedaan?'), true);
  assert.equal(isRecap('Waar ben je nu mee bezig?'), true);
  assert.equal(
    isRecap('Waar ben je nu echt mee bezig, wat heb je daarvoor gedaan en wat is de volgende stap?'),
    true,
  );
  assert.equal(isRecap('Wat staat er nog open?'), true);
  assert.equal(isRecap('Wat heb je zojuist met de schuur-led gedaan en hoe weet je dat?'), true);
  assert.equal(isRecap('Voer het prijsprogramma opnieuw uit.'), false);

  const contribution = __projectRouteTest.contributionFromTools;
  assert.equal(
    contribution([tool({ name: 'voer_uit' })], 'chat', 'Wat heb je vandaag gedaan?'),
    null,
  );
  assert.equal(
    contribution([tool({ name: 'voer_uit' })], 'chat', 'Voer het prijsprogramma uit.')?.name,
    'voer_uit',
  );
});

test('autonomie telt geen losse gedachte of ongecontroleerde write als bijdrage', () => {
  const contribution = __projectRouteTest.contributionFromTools;
  assert.equal(contribution([tool({ name: 'onthoud', changed: true })], 'autonomy'), null);
  assert.equal(contribution([tool({ name: 'schrijf_bestand', changed: true })], 'autonomy'), null);

  const checkedArtifact = [
    { ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/notitie.md' } }), args: { pad: 'data/workspace/notitie.md' } },
    { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/notitie.md' } },
  ];
  assert.equal(contribution(checkedArtifact, 'autonomy'), null);

  const groundedMessage = [
    tool({ name: 'zoek_internet' }),
    tool({ name: 'stuur_bericht', changed: true }),
  ];
  assert.equal(contribution(groundedMessage, 'autonomy')?.name, 'stuur_bericht');
});

test('bij meerdere bestanden blijft uitvoerbare broncode het te controleren werkstuk', () => {
  const program = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/audit.py' } }),
    args: { pad: 'data/workspace/audit.py' },
  };
  const fixture = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/fout.yaml' } }),
    args: { pad: 'data/workspace/fout.yaml' },
  };
  const run = {
    ...tool({ name: 'voer_uit' }),
    args: {
      programma: 'python3',
      argumenten: ['audit.py', 'fout.yaml'],
      werkmap: 'data/workspace',
    },
  };
  assert.equal(
    __projectRouteTest.incompleteAutonomousWrite([program, fixture, run])?.args?.pad,
    'data/workspace/audit.py',
  );
  const read = {
    ...tool({ name: 'lees_bestand' }),
    args: { pad: 'data/workspace/audit.py' },
  };
  assert.equal(
    __projectRouteTest.completedAutonomousArtifact([program, fixture, run, read])?.args?.pad,
    'data/workspace/audit.py',
  );
});

test('sensorafhandeling blokkeert een open autonoom werkstuk niet', () => {
  const signal = tool({ name: 'signalen_afhandelen' });
  assert.equal(__projectRouteTest.autonomyLaneForTool('signalen_afhandelen'), null);
  assert.equal(__projectRouteTest.autonomyStopReady([signal], { allowSignalOnly: true }), true);
  assert.equal(__projectRouteTest.autonomyStopReady([signal], { allowSignalOnly: false }), false);
});

test('bestaand open werk mag in stille uren doorgaan en krijgt snel vervolg', () => {
  const open = { open_commitments: [{ id: 8 }] };
  const idle = { open_commitments: [] };
  assert.equal(__autonomyScheduleTest.mayWorkAtNight(open, []), true);
  assert.equal(__autonomyScheduleTest.mayWorkAtNight(idle, []), false);
  assert.equal(
    __autonomyScheduleTest.nextDelay({ contribution: null }, open),
    30 * 60_000,
  );
  assert.equal(
    __autonomyScheduleTest.nextDelay({ progress: { made_progress: true } }, open),
    5 * 60_000,
  );
  const now = Date.parse('2026-08-03T00:00:00.000Z');
  assert.equal(
    __autonomyScheduleTest.schedulerDelay('2026-08-03T00:01:00.000Z', now),
    60_000,
  );
  assert.equal(
    __autonomyScheduleTest.schedulerDelay('2026-08-02T23:59:00.000Z', now),
    1_000,
  );
});

test('een nieuwe aspiratie verdringt geen uitvoerbaar bestaand commitment', () => {
  assert.equal(__autonomyScheduleTest.mayStartFreeAttention([], null), true);
  assert.equal(__autonomyScheduleTest.mayStartFreeAttention([], { item: { id: 41 } }), false);
  assert.equal(__autonomyScheduleTest.mayStartFreeAttention([{ id: 'deur' }], null), false);
});

test('autonoom werk stopt pas na rapportage en daarna het meerdaagse checkpoint', () => {
  const started = tool({ name: 'werk_bijhouden', changed: true, data: { item: { id: 7, status: 'open' } } });
  const written = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/resultaat.md' } }),
    args: { pad: 'data/workspace/resultaat.md' },
  };
  const read = { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/resultaat.md' } };
  const sent = tool({ name: 'stuur_bericht', changed: true });
  const checkpoint = tool({ name: 'werk_bijhouden', changed: true, data: { item: { id: 7, status: 'open' } } });
  assert.equal(__projectRouteTest.autonomyArtifactReportPending([written, read]), true);
  assert.equal(__projectRouteTest.autonomyArtifactReportPending([written, read, sent]), false);
  assert.equal(
    __projectRouteTest.autonomyStopReady([started, written, read], { requiredWorkIds: [7] }),
    false,
  );
  assert.equal(
    __projectRouteTest.autonomyStopReady(
      [started, written, read, sent, checkpoint],
      { requiredWorkIds: [7] },
    ),
    false,
  );
  assert.equal(
    __projectRouteTest.autonomyWorkCheckpointPending([started, written, read, sent], [7]),
    true,
  );
  const done = tool({ name: 'werk_bijhouden', changed: true, data: { item: { id: 7, status: 'done' } } });
  assert.equal(__projectRouteTest.autonomyWorkReportPending([written, read, done]), true);
  assert.equal(__projectRouteTest.autonomyWorkReportPending([written, read, done, sent]), false);
  assert.equal(
    __projectRouteTest.autonomyStopReady(
      [started, written, read, sent, done],
      { requiredWorkIds: [7] },
    ),
    true,
  );

  const executed = {
    ...tool({ name: 'voer_uit' }),
    args: {
      programma: 'node',
      argumenten: ['controle.mjs'],
      werkmap: 'data/workspace',
    },
  };
  const sourceRead = {
    ...tool({ name: 'lees_bestand' }),
    args: { pad: 'data/workspace/controle.mjs' },
  };
  assert.equal(
    __projectRouteTest.autonomyWorkReportPending([executed, sourceRead], [7]),
    false,
  );
  assert.equal(
    __projectRouteTest.autonomyWorkReportPending([executed, sourceRead, sent], [7]),
    false,
  );
  assert.equal(
    __projectRouteTest.autonomyWorkCheckpointPending([executed, sourceRead, done, sent], [7]),
    true,
  );
  assert.equal(
    __projectRouteTest.autonomyStopReady(
      [executed, sourceRead, done, sent],
      { requiredWorkIds: [7] },
    ),
    false,
  );

  const startedHere = tool({
    name: 'werk_bijhouden',
    changed: true,
    data: { item: { id: 8, status: 'open' } },
  });
  const checkpointHere = tool({
    name: 'werk_bijhouden',
    changed: true,
    data: { item: { id: 8, status: 'open' } },
  });
  assert.equal(
    __projectRouteTest.autonomyWorkCheckpointPending([written, read, startedHere, sent]),
    true,
  );
  assert.equal(
    __projectRouteTest.autonomyWorkCheckpointPending(
      [written, read, startedHere, sent, checkpointHere],
    ),
    false,
  );
});

test('routinehuiswerk mag stil blijven maar een ingrijpend effect wordt teruggemeld', () => {
  const changedHouse = {
    ...tool({ name: 'bedien_huis', changed: true }),
    args: { doel: 'overkappingsspots', actie: 'uit' },
  };
  const changedLock = {
    ...tool({ name: 'bedien_huis', changed: true }),
    args: { doel: 'achterdeurslot', actie: 'open' },
  };
  const sent = tool({ name: 'stuur_bericht', changed: true });
  assert.equal(__projectRouteTest.autonomyWorkReportPending([changedHouse]), false);
  assert.equal(__projectRouteTest.autonomyWorkReportPending([changedLock]), true);
  assert.equal(__projectRouteTest.autonomyWorkReportPending([changedLock, sent]), false);
});

test('autonoom werk kan niet vóór het gezinsbericht als afgerond worden geboekt', async () => {
  const item = createWorkItem({
    source: 'test',
    title: 'Rapporteer vóór afronden',
    goal: 'Voer controle.mjs uit en meld het bewezen resultaat.',
    nextStep: 'Voer de controle uit.',
  });
  const result = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden',
    id: item.id,
    resultaat: 'klaar',
  }, {
    mode: 'autonomy',
    priorToolResults: [tool({ name: 'voer_uit' })],
  }));

  assert.equal(result.ok, false);
  assert.match(result.evidence.summary, /pas afgerond nadat/i);
});

test('vrije autonomie kan niet blijven zoeken of oude gesprekken als takenbak gebruiken', () => {
  const offered = toolsForTurn({ mode: 'autonomy' }).map((item) => item.name);
  assert.equal(offered.includes('zoek_gesprek'), false);
  assert.equal(offered.includes('toon_werk'), false);

  const reads = [
    tool({ name: 'huis_status' }),
    tool({ name: 'ha_lees' }),
    tool({ name: 'wie_is_thuis' }),
    tool({ name: 'zoek_geheugen' }),
  ];
  assert.equal(__projectRouteTest.autonomyDiscoveryAllowed('ha_lees', reads), false);
  assert.equal(__projectRouteTest.autonomyDiscoveryAllowed('stuur_bericht', reads), true);

  const written = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/proef.md' } }),
    args: { pad: 'data/workspace/proef.md' },
  };
  assert.equal(__projectRouteTest.autonomyDiscoveryAllowed('lees_bestand', [...reads, written]), true);
  assert.equal(__projectRouteTest.autonomyLaneAllows('lees_bestand', 'research', reads), true);
  assert.equal(__projectRouteTest.autonomyLaneAllows('lees_bestand', 'research', [...reads, written]), true);
  assert.equal(__projectRouteTest.autonomyDiscoveryAllowed('ha_lees', [
    tool({ name: 'huis_status', ok: false, verified: false }),
  ]), true);
  assert.equal(__projectRouteTest.autonomyDiscoveryAllowed('ha_lees', [
    tool({ name: 'ha_lees', ok: false, verified: false }),
    tool({ name: 'ha_lees', ok: false, verified: false }),
  ]), false);

  assert.equal(
    __projectRouteTest.autonomyNoActionReason('to=functions.ha_lees ...', reads),
    'De controles in deze beurt leverden geen nieuw resultaat op dat een gezinsbeslissing of handeling veranderde.',
  );
  assert.equal(
    __toolsTest.emptyAutonomousWebFinding('Voor morgen zijn geen aangekondigde afsluitingen of andere verkeershinder gevonden.'),
    true,
  );
  assert.equal(
    __toolsTest.emptyAutonomousWebFinding('De N304 is morgen van 09:00 tot 16:00 afgesloten.'),
    false,
  );
  assert.equal(
    __toolsTest.autonomousMessagesOverlap(
      'De ramen boven kunnen vannacht openblijven; KNMI verwacht een droge nacht.',
      'Als de ramen boven nog dicht zijn, zet ze nu even open om af te koelen.',
    ),
    true,
  );
  assert.equal(
    __toolsTest.autonomousMessagesOverlap(
      'Er is rook bij de schuur; ga direct naar buiten.',
      'De schuurdeur staat nog open.',
    ),
    false,
  );
});

test('een open autonoom werkpunt blijft uitvoerbaar en echte voortgang is apart zichtbaar', () => {
  const open = tool({
    name: 'werk_bijhouden',
    changed: true,
    data: {
      proofAccepted: true,
      item: {
        id: 8,
        status: 'open',
        title: 'Maak de audit af',
        next_step: 'Herstel de lege action-controle en voer de regressietest opnieuw uit.',
      },
    },
  });
  assert.equal(__projectRouteTest.autonomyHasRunnableWork([open], [8]), true);
  assert.equal(__projectRouteTest.autonomyStopReady([open], { requiredWorkIds: [8] }), false);
  assert.equal(__projectRouteTest.autonomyProgressFromTools([open])?.made_progress, true);

  const unchanged = { ...open, changed: false };
  assert.equal(__projectRouteTest.autonomyProgressFromTools([unchanged]), null);
  assert.equal(__projectRouteTest.autonomyLaneForTool('huis_status'), null);
  assert.equal(__projectRouteTest.autonomyLaneForWork({
    title: 'Home Assistant YAML-audit',
    next_step: 'Pas ha_config_audit.py aan en voer de test uit.',
  }), 'workspace');
  const paidProject = {
    title: 'Een eerste betaalde softwareklus kiezen',
    goal: 'Vind een koper voor een Home Assistant-configuratie-audit.',
    next_step: 'Controleer de Reddit-thread en de betaalbereidheid.',
  };
  assert.equal(__projectRouteTest.autonomyProofRelevantToWork(
    paidProject,
    tool({ name: 'huis_status' }),
  ), false);
  assert.equal(__projectRouteTest.autonomyProofRelevantToWork(
    paidProject,
    tool({ name: 'zoek_internet' }),
  ), true);
  assert.equal(__toolsTest.proofToolFitsSubject(
    `${paidProject.title} ${paidProject.goal} ${paidProject.next_step}`,
    'huis_status',
  ), false);
});

test('gedeeltelijk bereikbare huisgroepen gelden als uitgevoerd met eerlijke opvolging', () => {
  assert.deepEqual(__toolsTest.homeOperationOutcome('on', { state: 'on' }, {
    ok: false,
    reachableVerified: true,
    unavailable: [{ entity_id: 'light.buiten_2' }],
  }), {
    ok: true,
    verified: true,
    partial: true,
    needsFollowup: true,
  });
});

test('identiek werk herschrijven en een onbekende werkbewerking tellen niet als voortgang', async () => {
  const file = path.join(dataDir, 'workspace', 'zelfde.txt');
  const first = JSON.parse(await executeTool('schrijf_bestand', {
    pad: file,
    inhoud: 'zelfde inhoud',
  }, { mode: 'autonomy' }));
  const second = JSON.parse(await executeTool('schrijf_bestand', {
    pad: file,
    inhoud: 'zelfde inhoud',
  }, { mode: 'autonomy' }));
  assert.equal(first.changed, true);
  assert.equal(second.changed, false);

  const invalid = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'doe-maar-iets',
  }, { mode: 'autonomy' }));
  assert.equal(invalid.ok, false);
  assert.equal(invalid.changed, false);
  assert.match(invalid.error, /onbekende werkbewerking/i);
});

test('de autonome event-inbox bewaart openingen en echte temperatuurstappen, geen sluitruis', () => {
  const temperature = (previous, current) => ({
    domain: 'sensor',
    oldState: String(previous),
    newState: String(current),
    attributes: { deviceClass: 'temperature', unit_of_measurement: '°C' },
  });
  const contact = (state) => ({
    domain: 'binary_sensor',
    newState: state,
    attributes: { deviceClass: 'door' },
  });

  assert.equal(shouldQueueWorldSignal(temperature(20, 20.6)), false);
  assert.equal(shouldQueueWorldSignal(temperature(20, 21)), true);
  assert.equal(shouldQueueWorldSignal(contact('off')), false);
  assert.equal(shouldQueueWorldSignal(contact('on')), true);
  assert.equal(shouldWakeForWorldSignal(contact('on')), true);
  assert.equal(shouldWakeForWorldSignal(temperature(20, 21)), false);
  assert.equal(hasNightUrgentSignal([{
    key: 'binary_sensor.achterdeur',
    type: 'entity_state',
    text: 'Achterdeur: off -> on',
  }]), true);
  assert.equal(hasNightUrgentSignal([{
    key: 'sensor.temperatuur',
    type: 'entity_state',
    text: 'Temperatuur: 20 -> 21',
  }]), false);
});

test('een gewone herinnering of gezichtsnaam wordt geen verzonnen gezinsbijdrage', () => {
  const contribution = __projectRouteTest.contributionFromTools;
  assert.equal(
    contribution([tool({ name: 'onthoud', changed: true })], 'chat', 'Dat klopt'),
    null,
  );
  assert.equal(
    contribution([tool({ name: 'leer_gezicht_van_foto', changed: true })], 'chat', 'Dit is Lola'),
    null,
  );
  assert.equal(
    contribution([tool({ name: 'onthoud', changed: true })], 'chat', 'Onthoud dat Lola van thee houdt')?.name,
    'onthoud',
  );
});

test('kritiek of een losse herhaling wordt door toevallig toolgebruik geen bijdrage', () => {
  const contribution = __projectRouteTest.contributionFromTools;
  assert.equal(
    contribution([tool({ name: 'schrijf_bestand', changed: true })], 'chat', 'Daar heb ik niks aan'),
    null,
  );
  assert.equal(
    contribution([tool({ name: 'voer_uit', changed: true })], 'chat', 'Alweer'),
    null,
  );
});

test('een half gebouwd programma telt niet als afgeronde bijdrage', () => {
  const contribution = __projectRouteTest.contributionFromTools;
  const request = 'Bouw een werkend programma in een bestand';
  assert.equal(
    contribution([tool({ name: 'schrijf_bestand', changed: true })], 'chat', request),
    null,
  );
  assert.equal(
    contribution([
      { ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/rekenaar.mjs' } }), args: { pad: 'data/workspace/rekenaar.mjs' } },
      { ...tool({ name: 'voer_uit', changed: true }), args: { programma: 'node', argumenten: ['rekenaar.mjs'], werkmap: 'data/workspace' } },
      { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/rekenaar.mjs' } },
    ], 'chat', request)?.name,
    'voer_uit',
  );
});

test('een bewezen bouwcyclus stopt na schrijven, uitvoeren en teruglezen', () => {
  const reply = __projectRouteTest.completedBuildReply([
    { ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/rekenaar.mjs' } }), args: { pad: 'data/workspace/rekenaar.mjs' } },
    { ...tool({ name: 'voer_uit', changed: true, data: { exit_code: 0, stdout: 'uitkomst: 42' } }), args: { programma: 'node', argumenten: ['rekenaar.mjs'], werkmap: 'data/workspace' } },
    { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/rekenaar.mjs' } },
  ]);
  assert.match(reply, /gemaakt, uitgevoerd en daarna gecontroleerd/i);
  assert.match(reply, /uitkomst: 42/);
  assert.doesNotMatch(reply, /data\/workspace/);
});

test('een gecontroleerd document valt terug op de inhoud en nooit op het interne pad', () => {
  const reply = __projectRouteTest.evidenceFallback([
    { ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/bieding.md' } }), args: { pad: 'data/workspace/bieding.md' } },
    {
      ...tool({ name: 'lees_bestand', data: { text: 'Hello, I propose a small first milestone with three concrete deliverables.' } }),
      args: { pad: 'data/workspace/bieding.md' },
    },
  ], { userText: 'Doe dit.' });
  assert.match(reply, /small first milestone/);
  assert.doesNotMatch(reply, /data\/workspace/);
});

test('vervolgtaal wordt op betekenis herkend zonder gewone uitleg te kapen', () => {
  for (const phrase of ['Doe dit.', 'Regel dat', 'Zorg dat dit werkt', 'Probeer het opnieuw']) {
    assert.equal(__projectRouteTest.isContinuationRequest(phrase), true, phrase);
  }
  assert.equal(__projectRouteTest.isContinuationRequest('Doe dit. Werk het meteen concreet uit.'), true);
  assert.equal(__projectRouteTest.isContinuationRequest('Ga hiermee verder en herstel het.'), true);
  assert.equal(__projectRouteTest.isContinuationRequest('Hoe werkt dit eigenlijk?'), false);
});

test('een reflectieve vraag biedt geen bijwerking als vervanging voor begrip', () => {
  assert.equal(__projectRouteTest.isReflectiveQuestion('Werkt de woonkamer Voice PE?'), true);
  assert.equal(__projectRouteTest.isReflectiveQuestion('Waarom spreek je jezelf tegen?'), true);
  assert.equal(__projectRouteTest.isReflectiveQuestion('Kun je het kantoorlicht uitzetten?'), false);
  assert.equal(__projectRouteTest.isReflectiveQuestion('Hoe kunnen we geld verdienen? Onderzoek dit.'), false);
});

test('beurtcontract begrenst bewijs maar schrijft Eva haar antwoord niet voor', () => {
  const contribution = __projectRouteTest.turnContractFor(
    'Wat zou jij vandaag praktisch voor ons huishouden kunnen betekenen?',
  );
  assert.equal(contribution.kind, 'household_contribution');
  assert.equal(contribution.requiredTools.includes('huis_status'), true);
  assert.equal(typeof contribution.outcome, 'string');
  assert.equal(
    __projectRouteTest.turnContractFor('Spreek je jezelf nu tegen?').kind,
    'conversation',
  );
  assert.equal(
    __projectRouteTest.turnContractFor('Zeg dit via de woonkamer Voice PE.').kind,
    'voice_action',
  );
  assert.equal(
    __projectRouteTest.turnContractFor('Werkt de woonkamer Voice PE?').kind,
    'conversation',
  );
  assert.equal(
    __projectRouteTest.turnContractFor('Welke bijdrage kan ons echt geld opleveren en tegen welke prijs?').kind,
    'earning_route',
  );
});

test('een read-only geldvraag dwingt geen ondernemingsartefact af', () => {
  const contract = __projectRouteTest.turnContractFor(
    'We willen 100 euro verdienen. Geef alleen de eerste stap. Verander of verstuur in deze test niets.',
  );
  assert.equal(contract.kind, 'earning_route');
  assert.match(contract.outcome, /zonder iets te maken of te wijzigen/);
  assert.equal(contract.allowedTools.has('zoek_internet'), true);
  assert.equal(contract.allowedTools.has('schrijf_bestand'), false);
});

test('interne autonome voortgang blijft buiten het gezinskanaal', () => {
  assert.equal(__toolsTest.autonomousMessageIsInternalProgress(
    'De regressietest van de Home Assistant-configuratie-audit slaagt met exitcode 0.',
  ), true);
  assert.equal(__toolsTest.autonomousMessageIsInternalProgress(
    'De achterdeur staat nog open en het gaat regenen; zal ik jullie over tien minuten nog eens waarschuwen?',
  ), false);
});

test('commercieel onderzoek degradeert een open-claim zonder toekomstige sluitingsdatum', () => {
  const bounded = __toolsTest.commercialResearchEvidenceBoundary(
    'Vind een betaalde freelance opdracht',
    'Deze opdracht staat open en reageren kan. Peildatum 2026-08-04.',
    new Date('2026-08-04T10:00:00Z'),
  );
  assert.match(bounded, /actuele open status.*niet bewezen/i);
  const proven = __toolsTest.commercialResearchEvidenceBoundary(
    'Vind een betaalde freelance opdracht',
    'De directe bron meldt open; sluitingsdatum 2026-08-09.',
    new Date('2026-08-04T10:00:00Z'),
  );
  assert.doesNotMatch(proven, /niet bewezen/i);
});

test('ruwe uitvoer en gescrapete html zijn nooit een gezinsantwoord', () => {
  assert.equal(__projectRouteTest.looksLikeInternalOutput(
    '<p _ngcontent-webapp>Scope of Work</p> stdout exitcode 0',
  ), true);
  assert.equal(__projectRouteTest.looksLikeInternalOutput(
    'Ik heb een biedtekst gemaakt met één concrete mijlpaal en drie vragen.',
  ), false);
  assert.equal(__projectRouteTest.looksLikeInternalOutput(
    'Hij staat klaar als `freelancer-first-milestone-scope.md`.',
  ), true);
});

test('een autonome vervolgsessie bewaart de concrete uitvoerfout', () => {
  const written = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/audit.py' } }),
    args: { pad: 'data/workspace/audit.py' },
  };
  const failed = {
    ...tool({
    name: 'voer_uit',
    ok: false,
    verified: false,
    data: {
      exit_code: 2,
      stderr: "No such file or directory: 'foute_automations.yaml'",
    },
    }),
    args: { programma: 'python3', argumenten: ['audit.py'], werkmap: 'data/workspace' },
  };
  const step = __projectRouteTest.autonomyContinuationStep(
    'data/workspace/audit.py',
    false,
    [written, failed],
  );
  assert.match(step, /foute_automations\.yaml/);
  assert.match(step, /exitcode 2/);

  const stale = __projectRouteTest.autonomyContinuationStep(
    'data/workspace/audit.py',
    false,
    [failed, written],
  );
  assert.doesNotMatch(stale, /foute_automations\.yaml/);
});

test('een bedoelde niet-nul exitcode telt als gecontroleerde proef', async () => {
  const workspace = path.join(dataDir, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });
  fs.writeFileSync(path.join(workspace, 'verwachte-exit.mjs'), 'process.exit(1);\n', 'utf8');
  const result = JSON.parse(await executeTool('voer_uit', {
    programma: 'node',
    argumenten: ['verwachte-exit.mjs'],
    werkmap: workspace,
    verwachte_exitcodes: [1],
  }, { mode: 'autonomy' }));
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.equal(result.data.exit_code, 1);
  assert.deepEqual(result.data.expected_exit_codes, [1]);
});

test('een verwachte exitcode maakt een gemeld ontbrekend geheim niet succesvol', async () => {
  const workspace = path.join(dataDir, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });
  fs.writeFileSync(
    path.join(workspace, 'ontbrekend-token.mjs'),
    "process.stderr.write('HA_TOKEN ontbreekt in de uitvoeromgeving\\n'); process.exit(2);\n",
    'utf8',
  );
  const result = JSON.parse(await executeTool('voer_uit', {
    programma: 'node',
    argumenten: ['ontbrekend-token.mjs'],
    werkmap: workspace,
    verwachte_exitcodes: [0, 1, 2],
  }, { mode: 'autonomy' }));
  assert.equal(result.ok, false);
  assert.equal(result.verified, false);
  assert.equal(result.changed, false);
  assert.match(result.error, /uitvoerfout/);
});

test('een falende bouwproef stuurt terug naar repareren en telt niet als voltooid', () => {
  const write = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/proef.py' } }),
    args: { pad: 'data/workspace/proef.py' },
  };
  const failed = {
    ...tool({ name: 'voer_uit', ok: true, data: { exit_code: 1, stderr: 'AssertionError' } }),
    args: { programma: 'python3', argumenten: ['proef.py'], werkmap: 'data/workspace' },
  };
  assert.deepEqual(
    __projectRouteTest.buildMissionRemaining(
      [write, failed],
      ['schrijf_bestand', 'voer_uit', 'lees_bestand'],
      true,
    ),
    ['schrijf_bestand'],
  );
});

test('vrij gekozen code na een document blijft open tot die code echt is uitgevoerd en gelezen', () => {
  const documentWrite = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/route.md' } }),
    args: { pad: 'data/workspace/route.md' },
  };
  const documentRead = { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/route.md' } };
  const scriptWrite = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/demo.py' } }),
    args: { pad: 'data/workspace/demo.py' },
  };
  const base = [documentWrite, documentRead, scriptWrite];
  assert.equal(__projectRouteTest.buildMissionComplete(base, false), false);
  assert.deepEqual(
    __projectRouteTest.buildMissionRemaining(base, ['schrijf_bestand', 'lees_bestand'], false),
    ['voer_uit'],
  );
  const run = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0 } }),
    args: { programma: 'python3', argumenten: ['demo.py'], werkmap: 'data/workspace' },
  };
  assert.deepEqual(
    __projectRouteTest.buildMissionRemaining([...base, run], ['schrijf_bestand', 'lees_bestand'], false),
    ['lees_bestand'],
  );
  const scriptRead = { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/demo.py' } };
  assert.equal(__projectRouteTest.buildMissionComplete([...base, run, scriptRead], false), true);
});

test('gevraagde tests en een echte CLI-run zijn afzonderlijke acceptatiebewijzen', () => {
  const prompt = 'Schrijf gerichte tests, voer ze uit en controleer ook één echte CLI-run.';
  const testRun = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0, stderr: 'Ran 3 tests in 0.01s\nOK' } }),
    args: { programma: 'python3', argumenten: ['test_cli.py'], werkmap: 'data/workspace' },
  };
  const helpRun = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0, stdout: 'usage: cli.py' } }),
    args: { programma: 'python3', argumenten: ['cli.py', '--help'], werkmap: 'data/workspace' },
  };
  assert.equal(__projectRouteTest.programmingAcceptanceStatus([testRun, helpRun], prompt).complete, false);
  const cliRun = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0, stdout: '[{"naam":"Melk","aantal":3}]' } }),
    args: { programma: 'python3', argumenten: ['cli.py', 'een.json', 'twee.json'], werkmap: 'data/workspace' },
  };
  assert.equal(__projectRouteTest.programmingAcceptanceStatus([testRun, cliRun], prompt).complete, true);
  const wrapperRun = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0, stdout: '[...]', stderr: 'CLI-CONTROLE: OK' } }),
    args: { programma: 'python3', argumenten: ['voer_cli_demo.py'], werkmap: 'data/workspace' },
  };
  assert.equal(__projectRouteTest.programmingAcceptanceStatus([testRun, wrapperRun], prompt).complete, true);
});

test('de bouwmissie biedt tests, voorbeeldbestand en CLI-run in vaste fasen aan', () => {
  const prompt = 'Schrijf gerichte tests en controleer een echte CLI-run met een apart voorbeeldbestand.';
  const required = ['schrijf_bestand', 'voer_uit', 'lees_bestand'];
  const sourceWrite = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/balans.js' } }),
    args: { pad: 'data/workspace/balans.js' },
  };
  const base = [sourceWrite];
  assert.deepEqual(__projectRouteTest.buildMissionRemaining(base, required, true, prompt), ['schrijf_bestand']);

  const testWrite = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/test_balans.js' } }),
    args: { pad: 'data/workspace/test_balans.js' },
  };
  assert.deepEqual(__projectRouteTest.buildMissionRemaining([...base, testWrite], required, true, prompt), ['voer_uit']);
  const testRun = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0, stdout: 'tests passed' } }),
    args: { programma: 'node', argumenten: ['test_balans.js'], werkmap: 'data/workspace' },
  };
  assert.deepEqual(__projectRouteTest.buildMissionRemaining([...base, testWrite, testRun], required, true, prompt), ['schrijf_bestand']);
  const fixtureWrite = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/voorbeeld.json' } }),
    args: { pad: 'data/workspace/voorbeeld.json' },
  };
  const withFixture = [...base, testWrite, testRun, fixtureWrite];
  assert.deepEqual(__projectRouteTest.buildMissionRemaining(withFixture, required, true, prompt), ['voer_uit']);
  const cliRun = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0, stdout: '{"ok":true}' } }),
    args: { programma: 'node', argumenten: ['balans.js', 'voorbeeld.json'], werkmap: 'data/workspace' },
  };
  const completed = [...withFixture, cliRun];
  assert.deepEqual(__projectRouteTest.buildMissionRemaining(completed, required, true, prompt), ['lees_bestand']);
  const sourceRead = { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/balans.js' } };
  const proven = [...completed, sourceRead];
  assert.deepEqual(__projectRouteTest.buildMissionRemaining(proven, required, true, prompt), []);
  assert.equal(__projectRouteTest.phasedProgrammingBuildStatus(proven, prompt).complete, true);
  const facts = __projectRouteTest.programmingOutcomeFacts(proven, prompt);
  assert.equal(facts.cliOutput, '{"ok":true}');
  assert.match(facts.facts, /afzonderlijke CLI-run/);
});

test('geldige programmeerbewijzen blijven geldig als de bron vóór de CLI-run is teruggelezen', () => {
  const prompt = 'Schrijf gerichte tests en controleer een echte CLI-run met een apart voorbeeldbestand.';
  const sourceWrite = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/woorden.js' } }),
    args: { pad: 'data/workspace/woorden.js' },
  };
  const testWrite = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/woorden.test.js' } }),
    args: { pad: 'data/workspace/woorden.test.js' },
  };
  const testRun = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0, stdout: '4 tests geslaagd' } }),
    args: { programma: 'node', argumenten: ['woorden.test.js'], werkmap: 'data/workspace' },
  };
  const sourceRead = { ...tool({ name: 'lees_bestand' }), args: { pad: 'data/workspace/woorden.js' } };
  const fixtureWrite = {
    ...tool({ name: 'schrijf_bestand', changed: true, data: { path: 'data/workspace/demo.json' } }),
    args: { pad: 'data/workspace/demo.json' },
  };
  const cliRun = {
    ...tool({ name: 'voer_uit', data: { exit_code: 0, stdout: '{"aantalUniekeWoorden":3}' } }),
    args: { programma: 'node', argumenten: ['woorden.js', 'demo.json'], werkmap: 'data/workspace' },
  };
  const results = [sourceWrite, testWrite, testRun, sourceRead, fixtureWrite, cliRun];
  assert.equal(__projectRouteTest.phasedProgrammingBuildStatus(results, prompt).complete, true);
  assert.deepEqual(__projectRouteTest.buildMissionRemaining(
    results,
    ['schrijf_bestand', 'voer_uit', 'lees_bestand'],
    true,
    prompt,
  ), []);
  assert.equal(__projectRouteTest.programmingOutcomeFacts(results, prompt).cliOutput, '{"aantalUniekeWoorden":3}');
});

test('duurzaam werk kan niet zonder bewijs als klaar worden geboekt', () => {
  const item = createWorkItem({
    title: 'Controleer een proef',
    goal: 'Een teruggelezen en gecontroleerd resultaat',
    nextStep: 'Voer de proef uit',
  });
  assert.throws(
    () => updateWorkItem(item.id, { status: 'done', result: 'klaar' }),
    /pas klaar/,
  );
  const done = updateWorkItem(item.id, {
    status: 'done',
    evidence: 'Uitvoer opnieuw gelezen: 42',
    result: 'De proef geeft 42',
    proofAccepted: true,
  });
  assert.equal(done.status, 'done');
});

test('een lege tussenstap wist eerder werkbewijs niet', () => {
  const item = createWorkItem({
    title: 'Bewijs vasthouden',
    goal: 'Bewaar het laatste echte testresultaat',
    nextStep: 'Voer de eerste proef uit',
  });
  updateWorkItem(item.id, { evidence: 'Regressietest geslaagd: 6 controles.' });
  const updated = updateWorkItem(item.id, {
    nextStep: 'Voer de volgende proef uit',
    evidence: '',
  });
  assert.equal(updated.evidence, 'Regressietest geslaagd: 6 controles.');
});

test('duurzaam werk gebruikt revisies, leases en herstelbare checkpoints', () => {
  const item = createWorkItem({
    title: 'Hervatbare flow',
    goal: 'Lever drie gecontroleerde stappen',
    nextStep: 'Voer stap één uit',
    definitionOfDone: 'Drie stappen zijn getest en teruggelezen',
    priority: 80,
  });
  const claimed = claimWorkItem(item.id, { owner: 'worker-a', leaseMs: 60_000 });
  assert.equal(claimed.lease_owner, 'worker-a');
  assert.equal(claimed.attempt_count, 1);
  assert.equal(claimWorkItem(item.id, { owner: 'worker-b', leaseMs: 60_000 }), null);
  assert.throws(() => updateWorkItem(item.id, {
    expectedRevision: claimed.revision - 1,
    currentStep: 'botsende update',
  }), /veranderde intussen/);
  const checkpoint = updateWorkItem(item.id, {
    expectedRevision: claimed.revision,
    currentStep: 'Voer stap twee uit',
    state: { stap_1: 'geslaagd' },
  });
  assert.equal(checkpoint.current_step, 'Voer stap twee uit');
  assert.deepEqual(JSON.parse(checkpoint.state_json), { stap_1: 'geslaagd' });
  releaseWorkLease(item.id, 'worker-a');
  assert.ok(claimWorkItem(item.id, { owner: 'worker-b', leaseMs: 60_000 }));
  assert.equal(recoverWorkLeases() >= 1, true);
  const recovered = listWorkItems({ statuses: ['open'], limit: 200 })
    .find((candidate) => candidate.id === item.id);
  assert.equal(recovered.lease_owner, null);
  assert.match(recovered.last_error, /procesherstart/i);
});

test('autonome programmas krijgen geen Eva-secrets mee', async () => {
  const workspace = path.join(dataDir, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });
  const program = path.join(workspace, 'environment-check.mjs');
  fs.writeFileSync(
    program,
    "console.log(process.env.EVA_TEST_SECRET || 'afgeschermd');\n",
    'utf8',
  );
  const raw = await executeTool('voer_uit', {
    programma: 'node',
    argumenten: ['environment-check.mjs'],
    werkmap: workspace,
  }, { mode: 'chat' });
  const result = JSON.parse(raw);
  assert.equal(result.ok, true);
  assert.equal(result.verified, true);
  assert.doesNotMatch(JSON.stringify(result.data), /must-not-leak/);
});

test('autonome programmas zitten in een echte mount- en procesisolatie', async () => {
  const workspace = path.join(dataDir, 'workspace');
  fs.mkdirSync(workspace, { recursive: true });
  const program = path.join(workspace, 'sandbox-check.mjs');
  fs.writeFileSync(program, `
import fs from 'node:fs';
import path from 'node:path';
const probe = (target, mode) => {
  try {
    if (mode === 'write') fs.writeFileSync(target, 'escape');
    else fs.readFileSync(target, 'utf8');
    return 'accessible';
  } catch (error) { return error.code || 'blocked'; }
};
console.log(JSON.stringify({
  source: probe('/home/administrator/project/Bottie/src/mind/agent.js', 'read'),
  env: probe('/home/administrator/project/Bottie/.env', 'read'),
  docker: probe('/var/run/docker.sock', 'read'),
  outside: probe(path.resolve(process.cwd(), '..', 'escape.txt'), 'write'),
  interfaces: fs.readdirSync('/sys/class/net').sort(),
}));
`, 'utf8');
  const raw = await executeTool('voer_uit', {
    programma: 'node',
    argumenten: ['sandbox-check.mjs'],
    werkmap: workspace,
  }, { mode: 'autonomy' });
  const result = JSON.parse(raw);
  assert.equal(result.ok, true);
  const probe = JSON.parse(result.data.stdout);
  for (const name of ['source', 'env', 'docker']) {
    assert.notEqual(probe[name], 'accessible', `${name} ontsnapte uit de sandbox`);
  }
  assert.deepEqual(probe.interfaces, ['lo']);
  // De private /tmp mag beschrijfbaar zijn, maar die write mag niet op de host verschijnen.
  assert.equal(fs.existsSync(path.join(dataDir, 'escape.txt')), false);
});

test('Telegram-uitvoer is terug te vinden op de duurzame deduplicatiesleutel', () => {
  archiveTelegramMessage({
    chatId: '42',
    messageId: '99',
    role: 'assistant',
    content: 'Een antwoord',
    meta: { dedupeKey: 'user-reply:turn:telegram:42:12' },
  });
  const found = telegramOutboundByDedupeKey('user-reply:turn:telegram:42:12');
  assert.equal(found?.message_id, '99');
});

test('een duurzame toolontvangst voorkomt blinde dubbele uitvoering na herstel', () => {
  const first = claimTurnToolExecution({
    executionKey: 'receipt-test', turnId: 'telegram:42:99', tool: 'bedien_huis', args: { doel: 'lamp', actie: 'uit' },
  });
  assert.equal(first.claimed, true);
  completeTurnToolExecution('receipt-test', '{"ok":true,"verified":true}');
  const replay = claimTurnToolExecution({
    executionKey: 'receipt-test', turnId: 'telegram:42:99', tool: 'bedien_huis', args: { doel: 'lamp', actie: 'uit' },
  });
  assert.equal(replay.claimed, false);
  assert.equal(replay.row.status, 'done');
  assert.equal(replay.row.output, '{"ok":true,"verified":true}');
});

test('camera en herinnering gebruiken de centrale Eva-woordkeuze', () => {
  assert.match(
    renderCameraNotice({
      place: 'de tuin',
      camera: 'G5 tuin',
      recognition: { recognized: true, person: 'Frank' },
    }),
    /^Frank is bij de tuin\./,
  );
  assert.equal(
    renderReminderText('Laat Jaap weten: het raam kan dicht'),
    'het raam kan dicht',
  );
});

test('een autonoom signaal verdwijnt niet zonder inhoudelijke beoordeling', async () => {
  const context = {
    mode: 'autonomy',
    messageContext: {
      pendingSignals: [{
        id: 'signal-123',
        key: 'sensor.achterdeur_temperature',
        text: 'Achterdeur Temperature: 26.4 -> 25.8',
      }],
    },
  };
  const empty = JSON.parse(await executeTool('signalen_afhandelen', {
    ids: ['signal-123'],
    oordeel: '',
  }, context));
  assert.equal(empty.ok, false);
  assert.equal(empty.verified, false);

  const unrelated = JSON.parse(await executeTool('signalen_afhandelen', {
    ids: ['signal-123'],
    oordeel: 'Aardappels zijn lekker.',
  }, context));
  assert.equal(unrelated.ok, false);
  assert.equal(unrelated.verified, false);

  const assessed = JSON.parse(await executeTool('signalen_afhandelen', {
    ids: ['signal-123'],
    oordeel: 'Geen actie nodig: dit was alleen een normale temperatuurdaling.',
  }, context));
  assert.equal(assessed.ok, true);
  assert.equal(assessed.verified, true);
});

test('voice-sessies hebben altijd precies een bruikbare voice-prefix', () => {
  assert.equal(normalizeVoiceConversationId('voice:', 'Lola'), 'voice:lola');
  assert.equal(normalizeVoiceConversationId('voice:voice:Jaap', 'Lola'), 'voice:jaap');
  assert.equal(normalizeVoiceConversationId('VOICE : VOICE : Jaap', 'Lola'), 'voice:jaap');
  assert.equal(normalizeVoiceConversationId('', ''), 'voice:huisgenoot');
});

test('een niet meer aangeboden verplichte tool dwingt geen willekeurige modelcalls af', async () => {
  let modelCalls = 0;
  const result = await runAgent({
    instructions: 'test',
    input: [],
    tools: [
      { name: 'annuleer_herinnering', description: 'test', parameters: { type: 'object' } },
      { name: 'huis_status', description: 'test', parameters: { type: 'object' } },
    ],
    executeTool: async () => '{}',
    complete: async () => {
      modelCalls += 1;
      return { output: [], text: '', toolCalls: [] };
    },
    maxRounds: 10,
    requireToolCall: true,
    requiredToolsRemaining: () => ['annuleer_herinnering'],
    selectTools: ({ tools }) => tools.filter((tool) => tool.name !== 'annuleer_herinnering'),
  });
  assert.equal(modelCalls, 0);
  assert.equal(result.exhausted, true);
  assert.equal(result.requiredToolUnavailable, 'annuleer_herinnering');
});

test('een mislukte voorkeursroute opent diagnose en een materieel andere aanpak', async () => {
  const offeredPerRound = [];
  const calls = [];
  let searchSucceeded = false;
  let round = 0;
  const result = await runAgent({
    instructions: 'test',
    input: [],
    tools: [
      { name: 'zoek_internet', description: 'zoek', parameters: { type: 'object' } },
      { name: 'ha_lees', description: 'diagnose', parameters: { type: 'object' } },
    ],
    complete: async ({ tools }) => {
      offeredPerRound.push(tools.map((item) => item.name));
      if (round === 0) {
        round += 1;
        return {
          output: [{ type: 'function_call', call_id: 'a', name: 'zoek_internet', arguments: '{"vraag":"breed"}' }],
          text: '', toolCalls: [{ call_id: 'a', name: 'zoek_internet', arguments: '{"vraag":"breed"}' }],
        };
      }
      if (round === 1) {
        round += 1;
        return {
          output: [{ type: 'function_call', call_id: 'b', name: 'ha_lees', arguments: '{}' }],
          text: '', toolCalls: [{ call_id: 'b', name: 'ha_lees', arguments: '{}' }],
        };
      }
      if (round === 2) {
        round += 1;
        return {
          output: [{ type: 'function_call', call_id: 'c', name: 'zoek_internet', arguments: '{"vraag":"directe officiële documentatie"}' }],
          text: '', toolCalls: [{ call_id: 'c', name: 'zoek_internet', arguments: '{"vraag":"directe officiële documentatie"}' }],
        };
      }
      return { output: [], text: 'Gelukt via een andere route.', toolCalls: [] };
    },
    executeTool: async (name, args) => {
      calls.push({ name, args });
      if (name === 'zoek_internet' && args.vraag === 'breed') {
        return JSON.stringify({ ok: false, verified: false, needs_followup: true, failure_class: 'transient', error: 'timeout' });
      }
      if (name === 'zoek_internet') searchSucceeded = true;
      return JSON.stringify({ ok: true, verified: true, needs_followup: false });
    },
    maxRounds: 5,
    requireToolCall: () => !searchSucceeded,
    requiredToolsRemaining: () => searchSucceeded ? [] : ['zoek_internet'],
  });
  assert.deepEqual(offeredPerRound[0], ['zoek_internet']);
  assert.deepEqual(offeredPerRound[1], ['zoek_internet', 'ha_lees']);
  assert.deepEqual(calls.map((call) => call.name), ['zoek_internet', 'ha_lees', 'zoek_internet']);
  assert.equal(result.text, 'Gelukt via een andere route.');
});

test('een geverifieerde eenvoudige actie kan zonder tweede modelronde afronden', async () => {
  let modelCalls = 0;
  let toolDone = false;
  const result = await runAgent({
    instructions: 'test',
    input: [],
    tools: [{ name: 'bedien_huis', description: 'test', parameters: { type: 'object' } }],
    complete: async () => {
      modelCalls += 1;
      return {
        output: [{ type: 'function_call', call_id: 'call-1', name: 'bedien_huis', arguments: '{}' }],
        text: '',
        toolCalls: [{ call_id: 'call-1', name: 'bedien_huis', arguments: '{}' }],
      };
    },
    executeTool: async () => {
      toolDone = true;
      return JSON.stringify({ ok: true, verified: true, human_message: 'Schuur-led staat aan.' });
    },
    maxRounds: 5,
    stopAfterToolRound: () => toolDone && 'Schuur-led staat aan.',
  });
  assert.equal(modelCalls, 1);
  assert.equal(result.text, 'Schuur-led staat aan.');
});

test('na gereedschap komt de oorspronkelijke gesprekstaak weer als laatste context terug', async () => {
  let round = 0;
  let finalInput = [];
  const result = await runAgent({
    instructions: 'test',
    input: [{ type: 'message', role: 'user', content: [{ type: 'input_text', text: 'Praat gewoon.' }] }],
    tools: [{ name: 'huis_status', description: 'test', parameters: { type: 'object' } }],
    complete: async ({ input }) => {
      if (round++ === 0) return {
        output: [{ type: 'function_call', call_id: 'ctx-1', name: 'huis_status', arguments: '{}' }],
        text: '',
        toolCalls: [{ call_id: 'ctx-1', name: 'huis_status', arguments: '{}' }],
      };
      finalInput = input;
      return { output: [], text: 'Gewoon antwoord.', toolCalls: [] };
    },
    executeTool: async () => JSON.stringify({ ok: true, verified: true }),
    maxRounds: 3,
    finalContextReminder: 'Terug naar de tafeltoon en de oorspronkelijke vraag.',
  });
  const latest = finalInput.at(-1)?.content?.[0]?.text || '';
  assert.match(latest, /tafeltoon/);
  assert.equal(result.text, 'Gewoon antwoord.');
});
