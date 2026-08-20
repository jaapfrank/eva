import assert from 'node:assert/strict';
import test from 'node:test';

import {
  effectBoundary,
  localProjectReadRequest,
  monitoringGuardValid,
  monitoringRequestSpec,
  projectMonitoringStartArgs,
  turnToolGuard,
} from '../src/mind/turnplan.js';

test('inspecteer-eerst houdt de hele beurt read-only zonder een latere opdracht te verzinnen', () => {
  const boundary = effectBoundary(
    'Kun jij alles van Tuya omzetten? Niet meteen doen; kijk eerst of je dat kunt en vertel per apparaat wat nodig is.',
  );
  assert.equal(boundary.inspectionOnly, true);
  assert.deepEqual([...boundary.denied].sort(), [
    'external', 'home', 'persistent', 'runtime', 'voice', 'workspace',
  ]);

  const explicitlyContinues = effectBoundary(
    'Niet meteen doen; kijk eerst of het kan en zet hem daarna om.',
  );
  assert.equal(explicitlyContinues.inspectionOnly, true);
  assert.deepEqual([...explicitlyContinues.denied].sort(), [
    'external', 'home', 'persistent', 'runtime', 'voice', 'workspace',
  ]);

  const nothing = effectBoundary(
    'Voer niets uit; controleer eerst wat nodig is. Het bestand heet resultaat.md.',
  );
  assert.equal(nothing.inspectionOnly, true);
  assert.equal(nothing.denied.includes('workspace'), true);

  const standalone = effectBoundary('Voer niets uit. De lamp is alleen achtergrond.');
  assert.equal(standalone.inspectionOnly, true);
  assert.deepEqual([...standalone.denied].sort(), [
    'external', 'home', 'persistent', 'runtime', 'voice', 'workspace',
  ]);
  assert.equal(standalone.inspectionReleaseAt, null);
});

test('lexicale niet-samenstellingen ontkennen geen latere workspaceactie', () => {
  for (const [text, denied] of [
    ['Bewijs een niet-nul resultaat en voer het programma uit.', false],
    ['Bouw een niet-lineair model en schrijf de uitkomst terug.', false],
    ['Voer het programma niet uit.', true],
    ['Schrijf het bestand niet.', true],
    ['Niet-schrijven, alleen lezen.', true],
    ['Schrijf dit bestand niet-uit.', true],
    ['Je moet dit niet-uitvoeren.', true],
    ['Niet schrijven.', true], ['Je moet niets schrijven.', true],
    ['Niet uitvoeren.', true], ['Niet implementeren.', true],
    ['Probeer dit niet te verbeteren vandaag.', true],
    ['Probeer dat niet te repareren morgen.', true],
  ]) assert.equal(effectBoundary(text).denied.includes('workspace'), denied, text);
  for (const [text, effect] of [
    ['Niet-aanzetten.', 'home'], ['Niet-openen.', 'home'], ['Niet-docken.', 'home'],
    ['Niet-mailen.', 'external'], ['Niet-wijzigen.', 'workspace'],
    ['Niet-monitoren.', 'persistent'],
    ['Niet mailen.', 'external'], ['Je moet niemand mailen.', 'external'],
    ['Niet monitoren.', 'persistent'], ['Je moet de lamp niet aanzetten.', 'home'],
    ['Niet omroepen.', 'voice'], ['Je moet niets zeggen.', 'voice'],
    ['Niet bewaren.', 'persistent'], ['Je moet dit niet onthouden.', 'persistent'],
    ['Maak een rapport zonder het te versturen.', 'external'],
    ['Maak een rapport zonder te mailen.', 'external'],
    ['Lees de sensor zonder de lamp aan te zetten.', 'home'],
    ['Vergeet de sleutel, probeer niet te mailen.', 'external'],
    ['Vergeet de sleutel en probeer niet te mailen.', 'external'],
    ['Stuur mijn kind niet alleen.', 'external'],
    ['Mail hem niet alleen.', 'external'],
    ['Zet de lamp niet alleen aan.', 'home'],
    ['Laat de robot niet alleen rijden.', 'home'],
  ]) assert.equal(effectBoundary(text).denied.includes(effect), true, text);
  for (const text of [
    'Jezelf niet-verbeteren.', 'Je eigen runtime niet-wijzigen.',
    'Wijzig je eigen code niet.', 'Repareer je eigen runtime niet.',
    'Niet-repareer je eigen runtime.', 'Je eigen code niet-debuggen.',
    'Je moet je eigen runtime niet wijzigen.', 'Probeer jezelf niet te verbeteren.',
    'Je hoeft je eigen runtime niet te repareren.', 'Je mag je eigen code niet veranderen.',
    'Je runtime niet aanpassen.', 'Je runtime niet repareren.', 'Je runtime niet debuggen.',
    'Geen wijzigingen aan je runtime.', 'Breng geen veranderingen aan je eigen runtime aan.',
    'Geen losse suggesties, ideeënlijstjes of wijzigingen aan je runtime.',
  ]) assert.equal(effectBoundary(text).denied.includes('runtime'), true, text);
  for (const text of [
    'Verbeter jezelf met een niet-wijzigbare configuratie.',
    'Maak een niet-uitvoerbaar ontwerp en verbeter jezelf.',
    'Monitor niet-lineaire trends.', 'Schrijf een niet-publiceerbaar concept.',
    'Zeg letterlijk "monitor dit niet" en monitor daarna de temperatuur.',
    'Schrijf op wat "monitor dit niet" betekent en monitor daarna de sensor.',
    'Leg uit wat "geen wijzigingen aan je runtime" betekent en verbeter jezelf.',
    'Zet de lamp niet te fel.',
    'Vergeet niet te mailen.',
    'Niet vergeten om te mailen.',
    'Doe deze controle later.',
    'Zeg dit morgen.',
    'Maak niet alleen het rapport maar ook de tabel.',
    'Zet niet alleen de keukenlamp maar ook de ganglamp aan.',
    'Zeg niet alleen hallo maar ook welkom.',
    'Stuur niet alleen de mail maar ook het bericht.',
    'Bewaar niet alleen het rapport maar ook de tabel.',
    'Verbeter niet alleen je runtime maar ook je communicatie.',
    'Zeg letterlijk "zonder iets te wijzigen" en maak daarna een rapport.',
    'Schrijf de tekst "doe nu niets" in een bestand.',
    "Schrijf Eva's tekst 'doe niets' naar een bestand.",
  ]) assert.deepEqual(effectBoundary(text).denied, [], text);
  assert.equal(effectBoundary('Maak alleen het rapport niet.').denied.includes('workspace'), true);
  assert.equal(effectBoundary('Maak het rapport niet, maar bewaar de tabel.').denied.includes('workspace'), true);
  assert.equal(effectBoundary("Wijzig Eva's runtime niet. Zeg 'hallo'.").denied.includes('runtime'), true);
  const quotedPossessive = effectBoundary("Monitor Eva's sensor niet en zeg 'klaar'.");
  assert.equal(quotedPossessive.denied.includes('persistent'), true);
  assert.equal(quotedPossessive.denied.includes('voice'), false);
  const localOnly = effectBoundary('Maak een rapport, maar stuur niets.');
  assert.equal(localOnly.denied.includes('external'), true);
  assert.equal(localOnly.denied.includes('workspace'), false);
  assert.deepEqual(effectBoundary('Zeg letterlijk: "het licht is niet-uit".').denied, []);

  const live = effectBoundary('Maak in je eigen werkmap een echt bruikbaar commandoregelprogramma voor onze huishoudkosten. Het krijgt een CSV-bestand als argument en levert per maand categorietotalen, de drie grootste uitgaven en één uit de cijfers afgeleide bespaarkans. Regel zelf geldige voorbeelddata én een foutgeval, bewijs een niet-nul exit op kapotte invoer, voer daarna een geslaagde rapportage uit en lees code en resultaat volledig terug.');
  assert.equal(live.requestedEffects.includes('workspace'), true);
  assert.equal(live.denied.includes('workspace'), false);
});

test('lokale projectuitlezing vraagt aantoonbaar om Eva haar eigen bron', () => {
  assert.equal(localProjectReadRequest(
    'Hoe staat het met die architectuurcontracten? Zoek het in onze eigen repo uit.',
  ), true);
  assert.equal(localProjectReadRequest('Lees de documentatie van Home Assistant.'), false);
  assert.equal(localProjectReadRequest('Verander niets in onze eigen repository.'), false);
});

test('monitoring gebruikt één analyse voor duur, onderwerp, vervolg en aflevering', () => {
  const now = Date.parse('2026-08-07T10:00:00.000Z');
  const active = monitoringRequestSpec({
    text: 'Monitor de temperatuur de komende twee uur.', requester: 'Jaap', now,
  });
  assert.equal(active.requested, true);
  assert.deepEqual(active.topicTokens, ['temperatuur']);
  assert.equal(active.minWakeTs, now + 15_000);
  assert.equal(active.maxWakeTs, now + 15 * 60_000);
  assert.equal(active.endsAt, new Date(now + 2 * 60 * 60_000).toISOString());

  const oneCheck = monitoringRequestSpec({
    text: 'Kijk over een minuut opnieuw en waarschuw me alleen als ik moet bijsturen.',
    requester: 'Jaap', now,
  });
  assert.equal(oneCheck.minWakeTs, now + 1);
  assert.equal(oneCheck.maxWakeTs, now + 2 * 60_000);
  assert.equal(oneCheck.endsAt, null);

  const silent = monitoringRequestSpec({
    text: 'Monitor de temperatuur twee uur, maar stuur geen updates.', requester: 'Jaap', now,
  });
  assert.equal(silent.requested, true);
  assert.equal(silent.deliveryRequired, false);
  assert.equal(silent.endsAt, new Date(now + 2 * 60 * 60_000).toISOString());
  const dynamicComparison = monitoringRequestSpec({
    text: 'geef aan wanneer ik de ramen meot openen als het buiten kouder is dan binnen.',
    requester: 'Jaap', now,
  });
  assert.equal(dynamicComparison.minimumSourceReads, 2,
  'een vergelijking tussen twee veranderlijke kanten vereist twee actuele bronnen');
  assert.deepEqual(dynamicComparison.sourceReferenceTexts, ['het buiten', 'binnen'],
    'de host bewaart de twee letterlijke vergelijkingskanten voor generieke bronbinding');
  assert.equal(monitoringRequestSpec({
    text: 'Laat me weten wanneer de wasmachine klaar is.', requester: 'Jaap', now,
  }).minimumSourceReads, 1,
  'een enkelvoudige toestand blijft een een-bronmonitor');
  const fixedThreshold = monitoringRequestSpec({
    text: 'Laat me weten wanneer de temperatuur lager is dan 18 graden.', requester: 'Jaap', now,
  });
  assert.equal(fixedThreshold.minimumSourceReads, 1,
  'een vaste numerieke grens is geen tweede actuele bron');
  assert.deepEqual(fixedThreshold.sourceReferenceTexts, []);
  assert.equal(monitoringRequestSpec({
    text: 'Laat me weten wanneer de temperatuur lager is dan achttien graden.',
    requester: 'Jaap', now,
  }).minimumSourceReads, 1,
  'een uitgeschreven vaste numerieke grens is evenmin een tweede actuele bron');
  for (const text of [
    'Monitor dit twee uur en stuur de mail niet, maar bewaar hem.',
    'Blijf dit volgen; stuur niet maar bewaar de resultaten.',
    'Monitor de temperatuur twee uur en stuur geen updates.',
  ]) {
    const spec = monitoringRequestSpec({ text, requester: 'Jaap', now });
    assert.equal(spec.requested, true, text);
    assert.equal(spec.deliveryRequired, false, text);
  }
  assert.equal(monitoringRequestSpec({
    text: 'Monitor de temperatuur twee uur en stuur niet alleen mail maar ook app.', requester: 'Jaap', now,
  }).deliveryRequired, true);
  assert.equal(monitoringRequestSpec({
    text: 'Monitor dit niet en stuur niets.', requester: 'Jaap', now,
  }).requested, false);
  assert.equal(monitoringRequestSpec({
    text: 'Monitor niet alleen de temperatuur maar ook de luchtvochtigheid twee uur.', requester: 'Jaap', now,
  }).requested, true);
  for (const [text, deniedEffect] of [
    ['Monitor de temperatuur twee uur en zeg niets hardop.', 'voice'],
    ['Monitor de temperatuur twee uur en zet de lamp niet aan.', 'home'],
    ['Monitor de temperatuur twee uur en schrijf niets.', 'workspace'],
  ]) {
    assert.equal(monitoringRequestSpec({ text, requester: 'Jaap', now }).requested, true, text);
    assert.equal(effectBoundary(text).denied.includes(deniedEffect), true, text);
    assert.equal(effectBoundary(text).denied.includes('persistent'), false, text);
  }
  for (const text of [
    'Monitor de keuken niet, maar volg de woonkamer twee uur.',
    'Monitor de keuken twee uur, maar volg de woonkamer niet.',
  ]) assert.equal(monitoringRequestSpec({ text, requester: 'Jaap', now }).requested, true, text);

  const rows = [{ content: 'De MEATER meldt voor de BBQ nu 84 graden en de kern 41 graden.' }];
  const followUp = monitoringRequestSpec({
    text: 'Blijf dit twee uur volgen en app me alleen als ik moet bijstellen.',
    recentThreadRows: rows, requester: 'Jaap', now,
  });
  assert.equal(followUp.requested, true);
  assert.deepEqual(followUp.topicTokens, ['meater', 'bbq', 'graden', 'kern']);
  assert.equal(followUp.recipient, 'Jaap');
  const voicePe = monitoringRequestSpec({
    text: 'Blijf dit twee uur volgen en laat het weten als de huidige status verandert.',
    recentThreadRows: [
      { content: 'Gebruik de Voice PE onder de overkapping voor deze route.' },
      { content: 'De huidige status is unavailable.' },
    ], requester: 'Jaap', now,
  });
  assert.deepEqual(voicePe.topicTokens, ['voice', 'overkapping']);
  assert.equal(voicePe.recipient, 'Jaap');
  assert.equal(monitoringRequestSpec({
    text: 'Blijf de temperatuur volgen en laat Vincent weten als die verandert.', requester: 'Jaap', now,
  }).recipient, 'Jaap');
  assert.equal(monitoringRequestSpec({
    text: 'Blijf de temperatuur volgen en laat het aan Vincent weten als die verandert.', requester: 'Jaap', now,
  }).recipient, 'Vincent');
  assert.equal(monitoringRequestSpec({ text: 'Hou dit in de gaten.', now }).requested, false);
  const continuity = monitoringRequestSpec({
    text: 'Wil je nu opletten thuis omdat ik weg ben?', requester: 'Jaap', now,
  });
  assert.equal(continuity.requested, false);
  assert.equal(continuity.continuityRequested, true);
  assert.equal(continuity.minWakeTs, now + 15_000);
  assert.equal(continuity.maxWakeTs, now + 15 * 60_000);
  for (const text of ['Let niet op het huis.', 'Houd niets in de gaten.', 'Let op: dit is uitleg.']) {
    assert.equal(monitoringRequestSpec({ text, requester: 'Jaap', now }).continuityRequested, false, text);
  }
  assert.equal(monitoringRequestSpec({
    text: 'Houd de woonkamer niet in de gaten; geef alleen de temperatuur van nu.', now,
  }).requested, false);
});

test('conditionele afleververzoeken starten generieke monitoring zonder observatiewerkwoord', () => {
  const now = Date.parse('2026-08-14T18:25:57.000Z');
  for (const text of [
    'Wil je doorgeven wanneer het buiten kouder is dan binnen?',
    'geef aan wanneer ik de ramen meot openen als het buiten kouder is dan binnen.',
    'Geef mij aan wanneer ik de ramen moet openen als het buiten kouder is dan binnen.',
    'Kun je aangeven wanneer het buiten kouder is dan binnen?',
    'Zorg dat je aangeeft wanneer het buiten kouder is dan binnen.',
    'Meld me zodra de buitentemperatuur lager is dan binnen.',
    'Laat het me weten als de schuurdeur opengaat.',
    'Waarschuw me wanneer de wasmachine klaar is.',
    'Laat me weten wanneer de was klaar is.',
    'Laat me weten wanneer dit is gebeurd.',
    'Waarschuw me zodra de wasmachine is gestopt.',
    'Laat me weten wanneer de wasmachine is gestopt.',
    'Waarschuw zodra de robot is gestart.',
    'Meld wanneer de deur is opengegaan.',
    'Als de deur opengaat, laat me weten.',
    'Als context: gisteren ging de lamp uit; laat me weten wanneer dat weer gebeurt.',
    'Als achtergrond gold dat de lamp gisteren uitging; laat me weten wanneer dat weer gebeurt.',
  ]) {
    const spec = monitoringRequestSpec({ text, requester: 'Jaap', now });
    assert.equal(spec.requested, true, text);
    assert.equal(spec.deliveryRequired, true, text);
    assert.equal(spec.recipient, 'Jaap', text);
  }

  for (const text of [
    'Wanneer is het buiten kouder dan binnen?',
    'Wil je doorgeven wat de temperatuur nu is?',
    'Geef aan wat de temperatuur nu is.',
    'Geef niet aan wanneer het buiten kouder is dan binnen.',
    'Kun je aangeven wanneer dit gisteren gebeurde?',
    'Kun je doorgeven wanneer dit gisteren gebeurde?',
    'Kun je doorgeven wanneer dit gisteren is gebeurd?',
    'Wil je doorgeven wanneer de lamp gisteren uitging?',
    'Laat me weten wanneer het gisteren buiten kouder was dan binnen.',
    'Kun je doorgeven wanneer dit gisteren was?',
    'Laat me weten wanneer de deur open was.',
    'Kun je doorgeven wanneer de temperatuur lager was dan binnen?',
    'Wil je niet doorgeven wanneer het buiten kouder is dan binnen?',
    'Leg uit wat "doorgeven wanneer" betekent.',
  ]) {
    assert.equal(monitoringRequestSpec({ text, requester: 'Jaap', now }).requested, false, text);
  }
});

test('monitoringgrens bindt exact werk, tijdvak, onderwerp, ontvanger en aflevermodus', () => {
  const now = Date.parse('2026-08-07T10:00:00.000Z');
  const guard = {
    workId: 41,
    now,
    monitoring: {
      minWakeTs: now + 300_000,
      maxWakeTs: now + 600_000,
      topicTokens: ['bbq', 'temperatuur'],
      sourceReadBound: true,
      deliveryRequired: false,
    },
    args: {
      bewerking: 'update',
      id: 41,
      hervatten_na: new Date(now + 400_000).toISOString(),
      volgende_stap: 'Meet de actuele BBQ-temperatuur en controleer de status.',
    },
  };
  assert.equal(monitoringGuardValid(guard), true);
  assert.equal(monitoringGuardValid({
    ...guard,
    args: { ...guard.args, id: 42 },
  }), false);
  assert.equal(monitoringGuardValid({
    ...guard, monitoring: { ...guard.monitoring, sourceReadBound: false },
  }), false);
  assert.equal(monitoringGuardValid({
    ...guard,
    args: { ...guard.args, volgende_stap: 'Meet de BBQ-temperatuur en stuur Jaap de status.' },
  }), false);
  assert.equal(monitoringGuardValid({
    ...guard,
    monitoring: { ...guard.monitoring, deliveryRequired: true, recipient: 'Jaap' },
    args: { ...guard.args, volgende_stap: 'Meet de BBQ-temperatuur en stuur Jaap de status.' },
  }), true);

  assert.equal(turnToolGuard({
    ...guard, turnKind: 'monitoring', name: 'werk_bijhouden',
  }), '');
  assert.match(turnToolGuard({
    ...guard,
    turnKind: 'monitoring',
    name: 'werk_bijhouden',
    args: { ...guard.args, hervatten_na: new Date(now + 60_000).toISOString() },
  }), /afgesproken tijdvak/iu);
  assert.equal(turnToolGuard({ ...guard, turnKind: 'chat', name: 'werk_bijhouden' }), '');
});

test('monitoringstart projecteert de host-owned uitvoeringsschedule uit bron en aflevering', () => {
  const now = Date.parse('2026-08-07T10:00:00.000Z');
  const monitoring = {
    minWakeTs: now + 15_000,
    maxWakeTs: now + 15 * 60_000,
    endsAt: new Date(now + 2 * 60 * 60_000).toISOString(),
    topicTokens: ['bbq', 'kern'],
    deliveryRequired: true,
    deliveryMode: 'later',
    deliveryTarget: 'requester',
    deliveryBound: true,
    sourceReadBound: true,
    hostProjectsWake: true,
    recipient: 'Jaap',
  };
  const rawArgs = {
    bewerking: 'start',
    titel: 'BBQ volgen',
    doel: 'Volg de BBQ twee uur.',
    hervatten_na: monitoring.endsAt,
    afleveren_aan: 'iemand anders',
    volgende_stap: '',
  };
  monitoring.hostProjectsSchedule = true;
  const projected = projectMonitoringStartArgs({ args: rawArgs, monitoring, now });

  assert.deepEqual(projected, {
    ...rawArgs,
    hervatten_na: new Date(now + 5 * 60_000).toISOString(),
    afleveren_aan: 'Jaap',
    volgende_stap: 'Lees alle gebonden monitoringbronnen opnieuw en beoordeel de opgeslagen meldvoorwaarde; meld Jaap alleen wanneer die voorwaarde waar is.',
    definitie_klaar: 'De eerste geverifieerde match met de opgeslagen meldvoorwaarde is exact eenmaal aan Jaap afgeleverd, of de afgesproken eindtijd is zonder match bereikt en vastgelegd.',
    fase: 'Wacht op de volgende geverifieerde meting.',
  }, 'de host projecteert alleen de procedure die al door het monitoringcontract is begrensd');
  assert.equal(monitoringGuardValid({
    args: rawArgs, monitoring, now, allowStart: true,
  }), true);
  assert.equal(monitoringGuardValid({
    args: {
      ...rawArgs,
      volgende_stap: 'Lees de twee verse waarden opnieuw en beoordeel de opgeslagen voorwaarde.',
    },
    monitoring,
    now,
    allowStart: true,
  }), true,
  'een al structured gebonden ontvanger hoeft niet nogmaals in vrije taaktekst te staan');
  assert.equal(monitoringGuardValid({
    args: rawArgs, monitoring: { ...monitoring, deliveryBound: false }, now, allowStart: true,
  }), false);

  const silentMonitoring = {
    ...monitoring, deliveryRequired: false, recipient: null,
    deliveryMode: 'silent', deliveryTarget: 'none',
  };
  const silentProjected = projectMonitoringStartArgs({
    args: rawArgs, monitoring: silentMonitoring, now,
  });
  assert.equal(silentProjected.afleveren_aan, '');
  assert.match(silentProjected.volgende_stap, /bewaar uitsluitend de meettoestand/iu);
  assert.equal(monitoringGuardValid({
    args: rawArgs, monitoring: silentMonitoring, now, allowStart: true,
  }), true, 'hostprojectie mag een stille monitoring niet als aflevering classificeren');
});
