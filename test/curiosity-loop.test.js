import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';
import assert from 'node:assert/strict';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-curiosity-'));
process.env.EVA_DATA_DIR = dataDir;

const { db } = await import('../src/db.js');

const {
  startExploration: rawStartExploration,
  updateExploration,
  noteExplorationAction,
  explorationById,
  attentionContextText,
  dueAttention,
  __curiosityTest,
} = await import('../src/mind/curiosity.js');

let topicSequence = 0;
const startExploration = (args) => {
  topicSequence += 1;
  return rawStartExploration({
    topicKey: args.topicKey || `test_topic_${topicSequence}`,
    topicAnchors: args.topicAnchors || [`test:topic-${topicSequence}`, `domain:test-${topicSequence}`],
    ...args,
  });
};

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('testverkenningen blijven buiten productie-aandacht, ook als een proef abrupt stopt', () => {
  const started = startExploration({
    question: `Mag testverkenning ${Date.now()} nooit productieaandacht worden?`,
    whyNow: 'Een afgebroken proef mag geen echt huisgedrag veroorzaken.',
    hypothesis: 'Alleen zelfgekozen productiebronnen komen in de aandachtplanner.',
    expectedObservation: 'De testverkenning ontbreekt in context en due-selectie.',
    stopRule: 'Stop zodra de planner de testbron weigert.',
    source: 'test',
  }).exploration;
  assert.equal(dueAttention(), null);
  assert.equal(attentionContextText().includes(started.question), false);
  db.prepare("DELETE FROM eva_explorations WHERE id=? AND source='test'").run(started.id);
});

test('eigen verkenning bewaart hypothese, actiebudget, bewijs en besluit over herstartbare stappen', () => {
  const unique = `Welke onverwachte mogelijkheid zie ik in proef ${Date.now()}?`;
  const started = startExploration({
    question: unique,
    whyNow: 'Ik wil niet passief op een opdracht blijven wachten.',
    hypothesis: 'Een kleine echte waarneming levert een nieuwe vervolgvraag op.',
    expectedObservation: 'Een bron of apparaat geeft een concreet nieuw gegeven.',
    stopRule: 'Na twee lege waarnemingen stop of verander ik aantoonbaar van aanpak.',
    maxActions: 4,
    source: 'self',
  });
  assert.equal(started.ok, true);
  assert.equal(started.exploration.max_actions, 4);

  noteExplorationAction(started.exploration.id, {
    tool: 'huis_status', ok: true, verified: true, evidence: 'actuele toestand teruggelezen', episodeId: 42,
  });
  const resumed = explorationById(started.exploration.id);
  assert.equal(resumed.action_count, 1);
  assert.equal(resumed.status, 'running');
  assert.match(attentionContextText(), new RegExp(`#${resumed.id}`));

  const closed = updateExploration(resumed.id, {
    status: 'completed',
    result: 'De echte waarneming leverde een bruikbare nieuwe vervolgvraag op.',
    decision: 'Een volgende cyclus begint met die waarneming, niet met een zoekdump.',
    expectedRevision: resumed.revision,
  });
  assert.equal(closed.ok, true);
  assert.equal(closed.exploration.status, 'completed');
  db.prepare("DELETE FROM eva_explorations WHERE id=? AND source='self'").run(resumed.id);
});

test('twee stappen zonder geverifieerd nieuw bewijs dwingen beoordeling af', () => {
  const started = startExploration({
    question: `Lege bewijsroute ${Date.now()} detecteren?`,
    whyNow: 'Doelloos zoeken moet niet doorgaan.',
    hypothesis: 'Twee lege resultaten zijn genoeg om te stoppen.',
    expectedObservation: 'De teller bereikt twee zonder bevestigd bewijs.',
    stopRule: 'Na twee lege stappen beoordelen en stoppen of pivoteren.',
    maxActions: 8,
    source: 'test',
  }).exploration;
  noteExplorationAction(started.id, { tool: 'zoek_internet', ok: true, verified: false });
  const result = noteExplorationAction(started.id, { tool: 'zoek_internet', ok: true, verified: false });
  assert.equal(result.status, 'evaluating');
  assert.match(result.decision, /Twee stappen zonder/);
  const overBudget = noteExplorationAction(started.id, {
    tool: 'zoek_internet', ok: true, verified: true, evidence: 'te late derde stap',
  });
  assert.equal(overBudget.action_count, result.action_count);
  const closed = updateExploration(result.id, {
    status: 'inconclusive',
    result: 'Testcyclus gesloten nadat twee lege waarnemingen de beoordelingspoort activeerden.',
    decision: 'Geen productieverkenning openlaten vanuit een test.',
    expectedRevision: result.revision,
  });
  assert.equal(closed.ok, true);
  db.prepare("DELETE FROM eva_explorations WHERE id=? AND source='test'").run(result.id);
});

test('een slapende verkenning wordt niet door een ongerelateerde toolactie wakker geschreven', () => {
  const wake = new Date(Date.now() + 86_400_000).toISOString();
  const started = startExploration({
    question: `Welk weer past morgen bij sterrenproef ${Date.now()}?`,
    whyNow: 'De toekomstige voorspelling is nu nog niet betrouwbaar.',
    hypothesis: 'Morgen is een lokale weersverwachting wel bruikbaar.',
    expectedObservation: 'De voorspelling noemt bewolking voor het kijkvenster.',
    stopRule: 'Tot morgen niets opzoeken of melden.',
    source: 'self',
  }).exploration;
  const sleeping = updateExploration(started.id, {
    status: 'waiting', result: null, nextWakeTs: wake, expectedRevision: started.revision,
  }).exploration;
  const after = noteExplorationAction(sleeping.id, {
    tool: 'zet_herinnering', ok: true, verified: true, evidence: 'ongerelateerde herinnering',
  });
  assert.equal(after.status, 'waiting');
  assert.equal(after.action_count, sleeping.action_count);
  assert.equal(after.revision, sleeping.revision);
  const directWake = updateExploration(sleeping.id, {
    status: 'running', evidence: 'Geen nieuw bewijs; alleen een voortijdige update.',
    expectedRevision: sleeping.revision,
  });
  assert.equal(directWake.ok, false);
  assert.match(directWake.reason, /slaapt tot/);
  assert.match(attentionContextText(), new RegExp(`#${sleeping.id}`));
  assert.match(attentionContextText(), /plan hiervoor geen tweede zelfherinnering/);
  db.prepare("DELETE FROM eva_explorations WHERE id=? AND source='self'").run(sleeping.id);
});

test('een recent eigen besluit kan niet stil met een soepelere drempel worden vervangen', () => {
  const marker = Date.now();
  const source = `coherence-test-${marker}`;
  const identity = {
    topicKey: 'outdoor_lighting_presence',
    topicAnchors: ['domain:outdoor-lighting', 'constraint:presence'],
  };
  const first = startExploration({
    ...identity,
    question: `Kan tuinlamp proef ${marker} veilig uit?`,
    whyNow: 'De tuinlamp brandt terwijl het laat is.',
    hypothesis: 'De tuinlamp kan uit als aanwezigheid uitgesloten is.',
    expectedObservation: 'Een actuele aanwezigheidsbron bevestigt dat de tuin leeg is.',
    stopRule: 'Bij ontbrekende aanwezigheid niets bedienen.',
    source,
  }).exploration;
  updateExploration(first.id, {
    status: 'completed',
    result: 'Aanwezigheid was niet aantoonbaar uitgesloten.',
    decision: 'De tuinlamp blijft aan zolang de aanwezigheidsbron ontbreekt.',
  });

  const rejected = startExploration({
    ...identity,
    question: `Mogen vergeten buitenlampen in proef ${marker} worden gedoofd?`,
    whyNow: 'De tuinlamp brandt nog steeds.',
    hypothesis: 'De lampen kunnen nu ook zonder aanwezigheidsbron uit.',
    expectedObservation: 'De lamp staat na bediening uit.',
    stopRule: 'Stop alleen als media actief zijn.',
    source,
  });
  assert.equal(rejected.ok, false);
  assert.equal(rejected.prior_exploration.id, first.id);

  const reopened = startExploration({
    ...identity,
    question: `Kan tuinlamp proef ${marker} veilig uit?`,
    whyNow: 'Er is nu een nieuwe actuele waarneming.',
    hypothesis: 'De tuinlamp kan uit omdat de tuin aantoonbaar leeg is.',
    expectedObservation: 'De actuele camera toont een lege tuin en de lamp kan uit.',
    stopRule: 'Bij iemand in beeld niets bedienen.',
    supersedesExplorationId: first.id,
    newEvidence: 'De actuele camera toont aantoonbaar een lege tuin.',
    newEvidenceVerified: true,
    source,
  });
  assert.equal(reopened.ok, true);
  assert.match(reopened.exploration.why_now, new RegExp(`#${first.id}`));
  const unrelated = startExploration({
    question: `Welke deurcamera toont bezoek bij proef ${marker}?`,
    whyNow: 'Een actueel deurbeeld vraagt om een eigen contextregel.',
    hypothesis: 'De deurcamera en deurstatus kunnen samen een melding gronden.',
    expectedObservation: 'Een deurentiteit en cameraframe hebben passende tijden.',
    stopRule: 'Zonder verse deurstatus niets over identiteit concluderen.',
    source,
  });
  assert.equal(unrelated.ok, true);
  db.prepare('DELETE FROM eva_explorations WHERE id IN (?,?,?) AND source=?')
    .run(first.id, reopened.exploration.id, unrelated.exploration.id, source);
});

test('gedeelde methodewoorden maken een lampvraag niet tot heropening van een camerabesluit', () => {
  const source = `topic-method-test-${Date.now()}`;
  const camera = startExploration({
    question: 'Welke deurentiteit en statusgeschiedenis gronden een cameramelding?',
    whyNow: 'Een recente camerabeoordeling miste de context van een geopende deur.',
    hypothesis: 'De exacte deurentiteit en haar logboek maken een cameraregel toetsbaar.',
    expectedObservation: 'Deur- en cameratimestamps vallen aantoonbaar in dezelfde context.',
    stopRule: 'Zonder verse deurstatus niets over een persoon concluderen.',
    source,
  }).exploration;
  updateExploration(camera.id, {
    status: 'completed',
    result: 'De cameraregel gebruikt alleen een verse deurstatus en bekende aanwezigheid.',
    decision: 'Onderdruk een onbekendmelding alleen bij passende deur- en cameracontext.',
  });
  const lamp = startExploration({
    question: 'Waardoor ging Elements 4DE3 aan en is een veilige lampregel mogelijk?',
    whyNow: 'De actuele huisuitlezing toont deze lamp als enige ingeschakeld.',
    hypothesis: 'De exacte lichtentiteit en recente logboekregels tonen de inschakeltijd.',
    expectedObservation: 'Het Home Assistant-logboek noemt een lampovergang en mogelijk een actor.',
    stopRule: 'Niets schakelen wanneer de oorzaak of actuele bedoeling onbekend blijft.',
    source,
  });
  assert.equal(lamp.ok, true);
  updateExploration(lamp.exploration.id, {
    status: 'completed',
    result: 'De lamp ging zojuist aan; zonder bewezen actor blijft ze aan.',
    decision: 'Een zeer recent aangezette lamp niet als vergeten behandelen.',
  });
  const cooling = startExploration({
    question: 'Is er een stille bruikbare koelactie nodig of is niet storen verstandiger?',
    whyNow: 'De kamers zijn warm en nachtrust weegt zwaar.',
    hypothesis: 'De actuele deurstatus of actieve media maakt duidelijk of ventileren al gebeurt.',
    expectedObservation: 'Deuren, ramen en media geven basis om wel of niet iets te doen.',
    stopRule: 'Eén huisuitlezing; niemand wekken en niets schakelen.',
    source,
  });
  assert.equal(cooling.ok, true);
  const weather = startExploration({
    question: 'Welke ochtendactie helpt het huis tegen oplopende binnentemperatuur?',
    whyNow: 'Het huis is warm en een tijdige ventilatieregel kan helpen.',
    hypothesis: 'Een actuele uurverwachting toont wanneer ramen beter dicht kunnen.',
    expectedObservation: 'De lokale verwachting toont een bruikbaar temperatuuromslagpunt.',
    stopRule: 'Stop als de uurverwachting geen betrouwbaar omslagpunt geeft.',
    source,
  });
  assert.equal(weather.ok, true);
  db.prepare('DELETE FROM eva_explorations WHERE source=?').run(source);
});

test('semantische lamp- en aanwezigheidsparafrase omzeilt een recent stopbesluit niet', () => {
  const source = `semantic-coherence-${Date.now()}`;
  const first = startExploration({
    topicKey: 'garden_lamp_presence_gate',
    topicAnchors: ['domain:outdoor-lighting', 'constraint:presence'],
    question: 'Moet de tuinlamp branden totdat Jaap aantoonbaar terug in huis is?',
    whyNow: 'De tuinlamp brandt en de actuele aanwezigheid is onzeker.',
    hypothesis: 'Een beeld bij de schuur is nodig voordat de tuinlamp veilig uit kan.',
    expectedObservation: 'Een verse waarneming bevestigt dat Jaap binnen is en buiten leeg.',
    stopRule: 'Zonder actueel beeld niets uitschakelen.',
    source,
  }).exploration;
  updateExploration(first.id, {
    status: 'completed',
    result: 'Er was geen actueel beeld dat afwezigheid buiten aantoonde.',
    decision: 'De tuinlamp blijft aan totdat aanwezigheid met een actuele bron bewezen is.',
  });
  const bypass = startExploration({
    topicKey: 'exterior_illumination_occupancy_rule',
    topicAnchors: ['domain:exterior-illumination', 'constraint:occupancy'],
    question: 'Can the exterior illumination be switched off because all residents are probably indoors?',
    whyNow: 'The garden lighting looks forgotten but there is no new occupancy proof.',
    hypothesis: 'Router devices are enough evidence of resident occupancy.',
    expectedObservation: 'Connected resident devices indicate that the garden is empty.',
    stopRule: 'Do not switch when a person is detected outdoors.',
    source,
  });
  assert.equal(bypass.ok, false);
  assert.equal(bypass.prior_exploration.id, first.id);
  for (const [index, wording] of [
    {
      question: 'Kan ik stoppen met het laten branden van de buitenverlichting?',
      hypothesis: 'Zonder automatisering hoeft het buitenlicht niet te blijven branden.',
    },
    {
      question: 'Mag de stroom van de tuinlamp er nu af?',
      hypothesis: 'Zonder actieve trigger is voeding niet langer nodig.',
    },
  ].entries()) {
    const paraphrase = startExploration({
      topicKey: `outdoor_light_paraphrase_${index}`,
      topicAnchors: ['domain:outdoor-lighting', `intent:power-removal-${index}`],
      question: wording.question,
      whyNow: 'Dezelfde buitenlamp lijkt nog steeds vergeten.',
      hypothesis: wording.hypothesis,
      expectedObservation: 'Het logboek noemt wel of geen automatisering.',
      stopRule: 'Stop als de voedingstoestand niet betrouwbaar te bepalen is.',
      source,
    });
    assert.equal(paraphrase.ok, false);
    assert.equal(paraphrase.prior_exploration.id, first.id);
  }
  const omittedLocation = startExploration({
    topicKey: 'light_cause_without_location',
    topicAnchors: ['domain:lighting', 'intent:cause-then-switch'],
    question: 'Waardoor brandt de verlichting en kan die nu worden uitgeschakeld?',
    whyNow: 'De lamp lijkt vergeten maar de locatie wordt in deze formulering weggelaten.',
    hypothesis: 'Als geen actor wordt gevonden kan het licht uit.',
    expectedObservation: 'Het logboek noemt wel of geen actor.',
    stopRule: 'Bij ontbrekende actor de verlichting uitschakelen.',
    source,
  });
  assert.equal(omittedLocation.ok, false);
  assert.equal(omittedLocation.prior_exploration.id, first.id);
  const readOnly = startExploration({
    topicKey: 'outdoor_light_readonly_cause',
    topicAnchors: ['domain:outdoor-lighting', 'intent:read-only-cause'],
    question: 'Waardoor brandt de buitenverlichting?',
    whyNow: 'De oorzaak kan later nuttig zijn zonder de veiligheidsgrens te veranderen.',
    hypothesis: 'Alleen het logboek uitlezen kan de actor tonen.',
    expectedObservation: 'De logboekregel noemt een actor of automatisering.',
    stopRule: 'Read-only: niets schakelen of bedienen.',
    source,
  });
  assert.equal(readOnly.ok, true);
  const contradictory = startExploration({
    topicKey: 'outdoor_light_fake_readonly',
    topicAnchors: ['domain:outdoor-lighting', 'intent:research-then-switch'],
    question: 'Kan ik alleen onderzoeken waarom de tuinlamp brandt en hem daarna uitschakelen?',
    whyNow: 'De oorzaak is onbekend maar de lamp brandt nog.',
    hypothesis: 'Na het oorzaakonderzoek kan de buitenverlichting uit.',
    expectedObservation: 'Het logboek noemt een actor.',
    stopRule: 'Eerst alleen onderzoeken en daarna de lamp uitschakelen.',
    source,
  });
  assert.equal(contradictory.ok, false);
  assert.equal(contradictory.prior_exploration.id, first.id);
  db.prepare('DELETE FROM eva_explorations WHERE source=?').run(source);
});

test('canonieke lineage houdt verschillende sterke lamp-IDs uit elkaar en dezelfde ID bijeen', () => {
  const source = `entity-lineage-${Date.now()}`;
  const first = startExploration({
    topicKey: 'reading_light_schedule',
    topicAnchors: ['entity:light.reading_corner', 'constraint:schedule'],
    question: 'Moet entity light.reading_corner volgens een avondschema schakelen?',
    whyNow: 'De leeslamp heeft nog geen bruikbare routine.',
    hypothesis: 'Een tijdvenster past bij het gebruik van light.reading_corner.',
    expectedObservation: 'De planning noemt een stabiel tijdstip voor de leeslamp.',
    stopRule: 'Zonder stabiel tijdstip niets automatiseren.',
    source,
  }).exploration;
  updateExploration(first.id, {
    status: 'completed', result: 'Het schema is zonder nieuwe gebruiksdata niet stabiel.',
    decision: 'light.reading_corner krijgt nog geen tijdschema.',
  });
  const sameKeyWithoutDimensionWord = startExploration({
    topicKey: 'reading_light_schedule',
    topicAnchors: ['entity:light.reading_corner', 'constraint:identity'],
    question: 'Zoek opnieuw uit wie entity light.reading_corner activeerde.',
    whyNow: 'Dezelfde gevalideerde lamp en onderwerpsleutel mogen geen parallelle cyclus maken.',
    hypothesis: 'De logboekregel van light.reading_corner bevat een naam.',
    expectedObservation: 'Een concrete naam staat in dezelfde logboekregel.',
    stopRule: 'Alleen lezen; stop na één logboekregel.',
    source,
  });
  assert.equal(sameKeyWithoutDimensionWord.ok, false);
  assert.equal(sameKeyWithoutDimensionWord.prior_exploration.id, first.id);
  const other = startExploration({
    topicKey: 'bed_light_schedule',
    topicAnchors: ['entity:light.bed', 'constraint:schedule'],
    question: 'Kan entity light.bed een vast nachtschema krijgen?',
    whyNow: 'De bedlamp wordt vaak rond hetzelfde uur gebruikt.',
    hypothesis: 'Een tijdvenster past bij light.bed.',
    expectedObservation: 'De planning toont een vast moment voor de bedlamp.',
    stopRule: 'Zonder stabiele planning niets automatiseren.',
    source,
  });
  assert.equal(other.ok, true);
  const same = startExploration({
    topicKey: 'corner_lamp_timing',
    topicAnchors: ['entity:light.reading_corner', 'dimension:timing'],
    question: 'Is een timer verstandig voor entity light.reading_corner?',
    whyNow: 'De hoeklamp heeft mogelijk een voorspelbaar patroon.',
    hypothesis: 'light.reading_corner kan op een herhaalbaar tijdstip aan.',
    expectedObservation: 'Het schema toont een stabiel tijdvenster.',
    stopRule: 'Zonder stabiel schema niets veranderen.',
    source,
  });
  assert.equal(same.ok, false);
  assert.equal(same.prior_exploration.id, first.id);
  db.prepare('DELETE FROM eva_explorations WHERE source=?').run(source);
});

test('vrije labels alleen zijn niet beslissend en onbekende inhoud wordt expliciet geweigerd', () => {
  const result = rawStartExploration({
    topicKey: 'mystery_domain',
    topicAnchors: ['domain:made-up-one', 'constraint:made-up-two'],
    question: 'Welke florp past het best bij de zibble?',
    whyNow: 'Dit onbekende onderwerp gebruikt alleen vrije labels.',
    hypothesis: 'Een florp kan mogelijk bij een zibble passen.',
    expectedObservation: 'Er verschijnt een onbekende maar concrete uitkomst.',
    stopRule: 'Stop na de eerste onbekende waarneming.',
    source: `unknown-identity-${Date.now()}`,
  });
  assert.equal(result.ok, false);
  assert.match(result.reason, /canonieke onderwerpidentiteit/);
  const fakeGoal = rawStartExploration({
    topicKey: 'fake_goal_identity',
    topicAnchors: ['goal:florp', 'constraint:zibble'],
    question: 'Welke florp past het best bij de zibble?',
    whyNow: 'Een verzonnen goal mag geen identiteit verlenen.',
    hypothesis: 'Goal florp bestaat niet in het duurzame doelenboek.',
    expectedObservation: 'De start wordt geweigerd.',
    stopRule: 'Stop na de identiteitscontrole.',
    source: `fake-goal-${Date.now()}`,
  });
  assert.equal(fakeGoal.ok, false);
});

test('gedeeltelijk genoemde nep-entity-ankers kunnen dezelfde lampkwestie niet splitsen', () => {
  const source = `fake-entity-${Date.now()}`;
  const first = startExploration({
    topicKey: 'fake_light_one',
    topicAnchors: ['entity:light.fake_one', 'constraint:schedule'],
    question: 'Can a light follow a schedule?',
    whyNow: 'A generic light schedule needs a decision.',
    hypothesis: 'The light can use a nightly schedule.',
    expectedObservation: 'The schedule shows a stable time.',
    stopRule: 'Without a stable schedule, change nothing.',
    source,
  }).exploration;
  assert.deepEqual(first.topic_features.strong_ids, []);
  updateExploration(first.id, {
    status: 'completed', result: 'No stable schedule was proven.',
    decision: 'Do not schedule this light without better usage evidence.',
  });
  const bypass = startExploration({
    topicKey: 'fake_light_two',
    topicAnchors: ['entity:light.fake_two', 'constraint:timing'],
    question: 'Should the same light use a nightly timer?',
    whyNow: 'The generic light still has no stable timing evidence.',
    hypothesis: 'A timer might be useful for the light.',
    expectedObservation: 'A schedule provides a stable time.',
    stopRule: 'Without a stable schedule, change nothing.',
    source,
  });
  assert.equal(bypass.ok, false);
  assert.equal(bypass.prior_exploration.id, first.id);
  db.prepare('DELETE FROM eva_explorations WHERE source=?').run(source);
});

test('verschillende softwarecomponenten delen geen lineage door alleen verbeteringstaal', () => {
  const source = `software-components-${Date.now()}`;
  const dashboard = startExploration({
    topicKey: 'temperature_dashboard_improvement',
    topicAnchors: ['component:temperature-dashboard', 'intent:improvement'],
    question: 'Welke softwareverbetering maakt het temperatuurdashboard duidelijker?',
    whyNow: 'Het dashboard toont temperatuur te onrustig.',
    hypothesis: 'Een kleine software-aanpassing verbetert het dashboard.',
    expectedObservation: 'De dashboardweergave wordt rustiger.',
    stopRule: 'Zonder meetbare verbetering niets wijzigen.',
    source,
  }).exploration;
  updateExploration(dashboard.id, {
    status: 'completed', result: 'Het temperatuurdashboard kreeg een rustiger ontwerp.',
    decision: 'Behoud deze dashboardweergave.',
  });
  const wakeword = startExploration({
    topicKey: 'wakeword_improvement',
    topicAnchors: ['component:wakeword', 'intent:improvement'],
    question: 'Welke softwareverbetering maakt het wakeword betrouwbaarder?',
    whyNow: 'Het wakeword mist nog oproepen.',
    hypothesis: 'Een software-aanpassing kan wakeword-detectie verbeteren.',
    expectedObservation: 'De wakeword-meting toont minder missers.',
    stopRule: 'Zonder meetbare verbetering niets wijzigen.',
    source,
  });
  assert.equal(wakeword.ok, true);
  db.prepare('DELETE FROM eva_explorations WHERE source=?').run(source);
});

test('een bestaande aanwezigheidsgrens blijft gelden bij herformulering als oorzaakonderzoek', () => {
  const source = `house-safety-conflict-${Date.now()}`;
  const first = startExploration({
    topicKey: 'garden_light_presence',
    topicAnchors: ['domain:outdoor-lighting', 'constraint:presence'],
    question: 'Moet de tuinlamp aanblijven zonder betrouwbaar aanwezigheidsbewijs?',
    whyNow: 'De tuin kan nog gebruikt worden.',
    hypothesis: 'Zonder bewezen afwezigheid blijft de buitenverlichting aan.',
    expectedObservation: 'Een actuele bron toont of iemand buiten is.',
    stopRule: 'Zonder aanwezigheidsbewijs de lamp niet doven.',
    source,
  }).exploration;
  updateExploration(first.id, {
    status: 'completed', result: 'Afwezigheid buiten was niet bewezen.',
    decision: 'De tuinlamp blijft aan zonder betrouwbaar aanwezigheidsbewijs.',
  });
  const bypass = startExploration({
    topicKey: 'outdoor_light_cause',
    topicAnchors: ['domain:exterior-illumination', 'intent:root-cause'],
    question: 'Waardoor brandt de buitenverlichting en kan die nu worden uitgeschakeld als geen automatisering actief is?',
    whyNow: 'Dezelfde buitenlamp lijkt vergeten.',
    hypothesis: 'Als geen actor wordt gevonden kan de lamp uit.',
    expectedObservation: 'Het logboek noemt wel of geen automatisering.',
    stopRule: 'Alleen bij een actieve automatisering niets uitschakelen.',
    source,
  });
  assert.equal(bypass.ok, false);
  assert.equal(bypass.prior_exploration.id, first.id);
  db.prepare('DELETE FROM eva_explorations WHERE source=?').run(source);
});

test('een lampgrens zonder locatie kaapt geen airco- of stofzuigerdoel', () => {
  const lampPrior = {
    question: 'Moet de lamp aanblijven zolang bewonersaanwezigheid onzeker is?',
    hypothesis: 'Zonder bewezen afwezigheid blijft het licht aan.',
    expected_observation: 'Een actuele bron toont of bewoners thuis zijn.',
    stop_rule: 'Zonder aanwezigheidsbewijs niets uitschakelen.',
  };
  const cooling = {
    question: 'Mag de airco in de slaapkamer nu aan vanwege de hitte?',
    hypothesis: 'De airco kan de slaapkamer veilig koelen.',
    expected_observation: 'De temperatuur daalt na inschakelen.',
    stop_rule: 'Stop bij een onverwachte toestand.',
  };
  const vacuum = {
    question: 'Kan de robotstofzuiger de woonkamer nu schoonmaken?',
    hypothesis: 'De robotstofzuiger kan zonder hinder starten.',
    expected_observation: 'De stofzuiger begint aan de woonkamer.',
    stop_rule: 'Stop als iemand hinder ondervindt.',
  };
  const liveCoolingWithIncidentalLightStop = {
    question: 'Is er voor donderdagochtend een stille, concrete koelactie of herinnering zinvol zonder iemand te wekken? Gebruik entity:calendar.gezin en entity:weather.forecast_thuis samen met actuele deuren, media en aanwezigheid.',
    hypothesis: 'Als alles dicht is, media inactief zijn en er geen vroege gezinsafspraak zichtbaar is, is hoogstens een later geplande gerichte herinnering nuttig; anders is stil niets plannen beter.',
    expected_observation: 'Een actuele huisuitlezing toont deuren, ramen, media en relevante aanwezigheid.',
    stop_rule: 'Na één gerichte huisuitlezing beslissen. Niet spreken, geen licht bedienen en geen herinnering plannen zonder duidelijk tijdstip en ontvanger.',
  };
  assert.equal(__curiosityTest.explorationSafetyConflict(lampPrior, cooling), false);
  assert.equal(__curiosityTest.explorationSafetyConflict(lampPrior, vacuum), false);
  assert.equal(__curiosityTest.explorationSafetyConflict(lampPrior, liveCoolingWithIncidentalLightStop), false);
  assert.equal(__curiosityTest.deriveTopicFeatures(liveCoolingWithIncidentalLightStop).subjects.includes('lighting'), false);
  assert.equal(__curiosityTest.deriveTopicFeatures(liveCoolingWithIncidentalLightStop).focus_subjects.includes('lighting'), false);
  assert.equal(__curiosityTest.deriveTopicFeatures(cooling).effects.includes('switch_on'), true);
  assert.equal(__curiosityTest.deriveTopicFeatures(vacuum).subjects.includes('vacuum'), true);
});

test('canonieke locatiecontext scheidt twee lampvragen zonder entity-ID', () => {
  const source = `location-lineage-${Date.now()}`;
  const outdoor = startExploration({
    topicKey: 'outdoor_light_schedule',
    topicAnchors: ['domain:outdoor-lighting', 'constraint:schedule'],
    question: 'Welk tijdschema past bij de tuinlamp?',
    whyNow: 'De buitenverlichting heeft geen vast ritme.',
    hypothesis: 'Een avondschema past bij de tuinlamp.',
    expectedObservation: 'De planning toont een bruikbaar tijdstip.',
    stopRule: 'Zonder stabiel schema niets veranderen.',
    source,
  }).exploration;
  updateExploration(outdoor.id, {
    status: 'completed', result: 'Er is nog geen stabiel avondschema.',
    decision: 'De tuinlamp krijgt voorlopig geen timer.',
  });
  const bedroom = startExploration({
    topicKey: 'bedroom_light_schedule',
    topicAnchors: ['domain:bedroom-lighting', 'constraint:schedule'],
    question: 'Welk tijdschema past bij de slaapkamerlamp?',
    whyNow: 'De slaapkamerverlichting heeft geen vast ritme.',
    hypothesis: 'Een nachtschema past bij de slaapkamerlamp.',
    expectedObservation: 'De planning toont een bruikbaar tijdstip.',
    stopRule: 'Zonder stabiel schema niets veranderen.',
    source,
  });
  assert.equal(bedroom.ok, true);
  db.prepare('DELETE FROM eva_explorations WHERE source=?').run(source);
});

test('ontkende auditwoorden injecteren geen cameraonderwerp', () => {
  const features = __curiosityTest.deriveTopicFeatures({
    question: 'Dit is geen cameraonderwerp: welk schema past bij de tuinlamp?',
    hypothesis: 'De buitenverlichting kan een vast tijdvenster volgen.',
    expectedObservation: 'De planning toont het tijdstip voor de lamp.',
    stop_rule: 'Zonder schema niets wijzigen.',
  });
  assert.deepEqual(features.subjects, ['lighting']);
});

test('een beschreven inschakeltijd is geen voorgenomen schakelactie', () => {
  const features = __curiosityTest.deriveTopicFeatures({
    question: 'Waardoor ging de lamp aan zonder nu iets te schakelen?',
    hypothesis: 'Het logboek toont een inschakeltijd en mogelijk een actor.',
    expectedObservation: 'De light-entiteit bevat een recente toestandsovergang.',
    stop_rule: 'Niets schakelen; alleen de oorzaak teruglezen.',
  });
  assert.deepEqual(features.effects, []);
  assert.deepEqual(features.dimensions, ['cause']);
  assert.equal(features.read_only, true);
});

test('achterdeursensor-diagnose wordt niet verward met een eerder lamp-oorzaakonderzoek', () => {
  const source = `live-door-lineage-${Date.now()}`;
  const lamp = startExploration({
    topicKey: 'elements_4de3_recent_activation',
    topicAnchors: ['entity:light.elements_4de3', 'domain:lighting-activation'],
    question: 'Waardoor is Elements 4DE3 vannacht aangegaan, en is daar een veilige beslisregel uit te halen zonder nu iets te schakelen?',
    whyNow: 'Een eerdere huisuitlezing meldde geen ingeschakelde lampen en de nieuwe toestand wijkt daarvan af.',
    hypothesis: 'De exacte entiteit en recente Home Assistant-logboekregels tonen een concrete inschakeltijd en mogelijk een actor.',
    expectedObservation: 'De light-entiteit bestaat en het logboek noemt een automatisering, service of gebruikerscontext.',
    stopRule: 'Nog maximaal twee read-only uitlezingen; niets schakelen en niemand wekken.',
    source,
  }).exploration;
  updateExploration(lamp.id, {
    status: 'completed', result: 'De lamp ging recent aan; de actor is niet bewezen.',
    decision: 'Laat deze lamp aan en onderzoek alleen opnieuw bij herhaling.',
  });
  const doorArgs = {
    topicKey: 'achterdeur_sensor_aggregatie_discrepantie',
    topicAnchors: ['entity:binary_sensor achterdeurcontact', 'event:bewoner-meldt-aggregatieconflict'],
    question: 'Welke exacte Home Assistant-entiteit vertegenwoordigt de achterdeur, en waarom mist de actuele samenvatting de open deur?',
    whyNow: 'De aggregaatsamenvatting spreekt een eerdere concrete bewonerswaarneming tegen.',
    hypothesis: 'De achterdeur gebruikt een onverwachte entity-id of device_class en valt daardoor buiten de samenvatting.',
    expectedObservation: 'Er staat een open contactentiteit voor achterdeur of keuken, of er blijkt geen bruikbare sensor aanwezig.',
    stopRule: 'Maximaal drie leesacties; niets bedienen en niemand berichten.',
    source,
  };
  const wrongReopen = startExploration({
    ...doorArgs,
    supersedesExplorationId: lamp.id,
    newEvidence: 'De actuele huisuitlezing noemt geen open deuren.',
    newEvidenceVerified: true,
  });
  assert.equal(wrongReopen.ok, false);
  assert.match(wrongReopen.reason, /hoort inhoudelijk niet/);
  const door = startExploration(doorArgs);
  assert.equal(door.ok, true);
  assert.deepEqual(lamp.topic_features.focus_subjects, ['lighting']);
  assert.deepEqual(door.exploration.topic_features.focus_subjects, ['access_sensor']);
  assert.equal(door.exploration.topic_features.dimensions.includes('presence'), false);
  assert.deepEqual(door.exploration.topic_features.contexts, ['back_door', 'kitchen']);
  db.prepare('DELETE FROM eva_explorations WHERE source=?').run(source);
});

test('hoofdonderwerpsignatuur voorkomt dat een gecombineerde deur-en-lampvraag alleen via overlap terugvalt', () => {
  const prior = {
    topic_key: 'lamp_actor_check',
    question: 'Waardoor ging de lamp vannacht aan?',
    hypothesis: 'Het light-logboek toont de actor.',
    expected_observation: 'De light-entiteit toont een overgang.',
    stop_rule: 'Alleen lezen en niets schakelen.',
  };
  const combined = {
    topic_key: 'achterdeur_bronconflict',
    question: 'Welke achterdeursensor verklaart het bronconflict terwijl entity:light.elements_4de3 aan is?',
    hypothesis: 'De device_class van het achterdeurcontact verklaart waarom de deuraggregatie afwijkt.',
    expected_observation: 'Een contactentiteit toont de deurtoestand.',
    stop_rule: 'Alleen lezen en niets schakelen.',
  };
  assert.deepEqual(__curiosityTest.deriveTopicFeatures(prior).focus_subjects, ['lighting']);
  assert.deepEqual(__curiosityTest.deriveTopicFeatures(combined).focus_subjects, ['access_sensor', 'lighting']);
  assert.equal(__curiosityTest.deriveTopicFeatures(combined).focus_subject, 'access_sensor');
  assert.equal(__curiosityTest.sameExplorationTopic(prior, combined), false);
  const reversed = {
    ...combined,
    question: 'De tuinlamp is aan; welke achterdeursensor verklaart het bronconflict?',
  };
  assert.equal(__curiosityTest.deriveTopicFeatures(reversed).focus_subject, 'access_sensor');
  assert.equal(__curiosityTest.sameExplorationTopic(prior, reversed), false);
  assert.equal(__curiosityTest.sameExplorationTopic(
    prior, { ...combined, topic_key: prior.topic_key },
  ), false, 'een hergebruikt vrij topic_key mag een ander hoofdonderwerp niet kapen');
});

test('dezelfde focus en dimensie blijven lineage bij een later toegevoegde kamercontext', () => {
  const prior = {
    topic_key: 'elements_4de3_recent_activation',
    question: 'Waardoor ging de lamp aan?', hypothesis: 'Het light-logboek toont de actor.',
    expected_observation: 'De light-entiteit toont een overgang.', stop_rule: 'Alleen lezen.',
  };
  const followup = {
    topic_key: 'elements_4de3_recent_activation',
    question: 'Waardoor ging de keukenlamp opnieuw aan?', hypothesis: 'Het light-logboek toont een actor in de keuken.',
    expected_observation: 'De light-entiteit toont de overgang.', stop_rule: 'Alleen lezen.',
  };
  assert.equal(__curiosityTest.sameExplorationTopic(prior, followup), true);
});

test('voor- en achterdeur zijn zonder gedeelde target geen verwisselbare locatiecontext', () => {
  const front = __curiosityTest.deriveTopicFeatures({
    question: 'Welke voordeurcontact-entiteit mist de samenvatting?',
    hypothesis: 'De voordeur heeft een afwijkende device_class.',
  });
  const back = __curiosityTest.deriveTopicFeatures({
    question: 'Welke achterdeurcontact-entiteit mist de samenvatting?',
    hypothesis: 'De achterdeur heeft een afwijkende device_class.',
  });
  assert.deepEqual(front.contexts, ['front_door']);
  assert.deepEqual(back.contexts, ['back_door']);
  assert.equal(__curiosityTest.sameExplorationTopic(
    { question: 'voordeurcontrole', topic_features: front },
    { question: 'achterdeurcontrole', topic_features: back },
  ), false);
});

test('expliciete onderwerpidentiteit koppelt Perseïden niet aan een energiecheckplan', () => {
  const source = `identity-separation-${Date.now()}`;
  const energy = startExploration({
    topicKey: 'home_energy_check_plan',
    topicAnchors: ['domain:household-energy', 'artifact:energy-check-plan'],
    question: 'Welke opgeslagen energiecheck kan onder normale omstandigheden worden uitgevoerd?',
    whyNow: 'Er ligt een eerder energieplan dat op een bruikbaar moment gecontroleerd kan worden.',
    hypothesis: 'Een korte huisuitlezing toont of de energiecheck nu zinvol is.',
    expectedObservation: 'Het plan en de actuele vermogensstand leveren een concrete volgende stap.',
    stopRule: 'Stop zonder nieuwe uitvoering als de omstandigheden niet passen.',
    source,
  }).exploration;
  updateExploration(energy.id, {
    status: 'abandoned',
    result: 'De energiecheck is nu niet passend en blijft bewust liggen.',
    decision: 'Alleen hervatten bij een actuele afwijking in het energiegebruik.',
  });
  const perseids = startExploration({
    topicKey: 'perseids_2026_observation',
    topicAnchors: ['event:perseids-2026', 'domain:astronomy-observation'],
    question: 'Wanneer is de uitzonderlijk lange waarneemperiode van de Perseïden in 2026?',
    whyNow: 'Een actuele astronomische bron kan een nuttig waarnemingsmoment opleveren.',
    hypothesis: 'De maanstand en pieknacht bepalen een concrete kijktijd.',
    expectedObservation: 'Een primaire bron noemt piek, maanlicht en zichtbare uren.',
    stopRule: 'Na twee bronnen stoppen zonder herinnering als het moment onzeker blijft.',
    source,
  });
  assert.equal(perseids.ok, true);
  updateExploration(perseids.exploration.id, {
    status: 'completed', result: 'De Perseïden hebben een eigen kijkvenster.',
    decision: 'Alleen de Perseïden rond hun eigen piek beoordelen.',
  });
  const geminids = startExploration({
    topicKey: 'geminids_2026_observation',
    topicAnchors: ['event:geminiden-2026', 'domain:astronomy-observation'],
    question: 'Wanneer hebben de Geminiden in 2026 vanuit Ugchelen een helder kijkvenster?',
    whyNow: 'Deze meteorenzwerm heeft een andere piek dan de Perseïden.',
    hypothesis: 'De Geminiden hebben in december een eigen pieknacht en weerkans.',
    expectedObservation: 'Astronomische bronnen en weerinformatie noemen een eigen waarneemvenster.',
    stopRule: 'Na twee bronnen stoppen als het Geminidenvenster onzeker blijft.',
    source,
  });
  assert.equal(geminids.ok, true);
  assert.deepEqual(geminids.exploration.topic_features.strong_ids, ['event:geminids-2026']);
  const eclipse = startExploration({
    topicKey: 'lunar_eclipse_2026_observation',
    topicAnchors: ['event:maansverduistering-2026', 'domain:astronomy-observation'],
    question: 'Wanneer heeft de astronomische maansverduistering 2026 een helder kijkvenster?',
    whyNow: 'Dit hemelverschijnsel staat los van meteorenzwermen.',
    hypothesis: 'De maansverduistering heeft een eigen tijdstip en lokale weerbehoefte.',
    expectedObservation: 'Een astronomische bron en weerbron noemen het eigen kijkvenster.',
    stopRule: 'Stop als het kijkvenster niet betrouwbaar is.',
    source,
  });
  assert.equal(eclipse.ok, true);
  assert.deepEqual(eclipse.exploration.topic_features.strong_ids, ['event:lunar-eclipse-2026']);
  const fakeEvent = startExploration({
    topicKey: 'fake_astronomy_event',
    topicAnchors: ['event:florp-2026', 'domain:astronomy-observation'],
    question: 'Wanneer heeft de astronomische florp-2026 een helder kijkvenster?',
    whyNow: 'Een vrij callerlabel mag geen sterk event-ID maken.',
    hypothesis: 'De astronomische florp-2026 zou een eigen weerkans hebben.',
    expectedObservation: 'Een weerbron noemt een kijkvenster.',
    stopRule: 'Stop zonder controleerbaar event.',
    source,
  });
  assert.equal(fakeEvent.ok, false);
  assert.deepEqual(fakeEvent.prior_exploration.topic_features.strong_ids, ['event:perseids-2026']);
  db.prepare('DELETE FROM eva_explorations WHERE source=?').run(source);
});

test('een productie-exploratie zonder duurzame onderwerpidentiteit wordt geweigerd', () => {
  const missing = rawStartExploration({
    question: 'Kan deze identiteitloze vraag productieaandacht krijgen?',
    whyNow: 'Een vrije tekst alleen is geen betrouwbare lineage.',
    hypothesis: 'De start wordt zonder onderwerpidentiteit geweigerd.',
    expectedObservation: 'Er ontstaat geen nieuwe exploration-rij.',
    stopRule: 'Stop zodra de identiteitsvalidatie dit weigert.',
    source: 'self',
  });
  assert.equal(missing.ok, false);
  assert.match(missing.reason, /onderwerp_sleutel/);
});
