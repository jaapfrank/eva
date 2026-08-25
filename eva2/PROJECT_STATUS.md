# Eva 2 — actuele voortgang

Laatst bijgewerkt: 2026-08-25

Dit is het korte meekijkdocument. Ik werk het bij na een afgeronde verticale
stap en uiterlijk bij de volgende uurupdate. De technische waarheid blijft in
[`design/architecture/living-core-v1.md`](design/architecture/living-core-v1.md);
de korte lijst met mogelijkheden, gaten en concurrentiestand staat in
[`EVA2_MOGELIJKHEDEN_EN_GATEN.md`](EVA2_MOGELIJKHEDEN_EN_GATEN.md); de verplichte
volledige productvergelijking en hergebruikroute staat in
[`CONCURRENTIE_EN_HERGEBRUIK.md`](CONCURRENTIE_EN_HERGEBRUIK.md); de verplichte
teamvolgorde staat in [`TEAM_WERKWIJZE.md`](TEAM_WERKWIJZE.md);
de controleerbare live receipts staan in
[`evidence/living-core/`](evidence/living-core/).

## Nu

- Bezig: MC03b, één reeds voorbereide read via de bestaande providergrens
  uitvoeren en haar waarneming duurzaam teruglezen. Effectstappen en het
  vrijgeven van afhankelijke stappen blijven daarna aparte opleverstukken.
- Laatste afgeronde kleine checkpoint: MC03a zet precies één geleasede,
  gevalideerde read-stap atomair om in één duurzaam `read_intent`. Het
  leasetoken, de actuele toolcatalogus, resource-, activation-, binding-,
  policy- en manifestauthority worden opnieuw gecontroleerd en tot commit
  gelockt. Een geforceerde rollback liet nul resten achter; een echte
  tweeverbindingsrace kon de activation niet tussentijds intrekken. Twee
  onafhankelijke reviews, twee PostgreSQL-tests, 57 contracttests en 24
  tijdlijntests zijn groen. Er is nog geen provider aangeroepen.
- De eerdere run `resident-long-2026-08-24-a` is na review teruggezet naar
  shakedown. Hij verzamelt echte HA-events, maar telt niet als acceptatierun.
- Eerstvolgend: het gezamenlijke architectuurbesluit uitvoeren; daarna laat een
  andere agent iedere verticale wijziging en echte B-proef goed- of afkeuren.
- Productie A: actief en niet gewijzigd.
- Actuele B: `eva2-living-core:cross-channel-v53-20260824`, core ready,
  duurzame-doelenworker running met nul herstarts.
- Eindacceptatie is uitgebreid maar nog niet gestart: naast core 15/15 en
  native 9/9 moeten echte multi-persoon-, multi-beurt-, multi-tool-,
  crashherstel- en self-improvementproeven groen zijn. De norm staat in
  [`design/validation/lab-36-final-acceptance-addendum-v1.md`](design/validation/lab-36-final-acceptance-addendum-v1.md).

## Afgerond en live bewezen

- De eerdere drie-case concurrentenmeting is eerlijk begrensd en uitgebreid met
  een 22-categorie productmatrix en hergebruikarchitectuur voor ClawHub,
  OpenClaw Gateway/nodes, MCP, Hermes en Agent Zero. De onafhankelijke reviewer
  gaf PASS. De fail-closed gate blijft rood zolang niet iedere categorie tegen
  iedere concurrent met een gehashte volledige `won`-receipt is bewezen.
- LAB-36 core-live: 15/15.
- De draaiende v53-B-bron is onafhankelijk vastgelegd zonder secrets of
  containerwijzigingen: 377 bestanden, 363 gelijk, 8 afwijkend en 6 alleen in
  de werkboom. Dit is herkomstbewijs, geen release-freeze of live-acceptatie.
- Historisch native bewezen: P03, X01, A01, A02, A03, I02, F03, F04 en M01.
  Deze receipts zijn verdeeld over v43, v49/v50, v52 en v53; één bevroren
  eindkandidaat moet de volledige 9/9-regressie nog doorlopen.
- A01/A02: zelfstandig wachten, tussenvraag verwerken en daarna precies één
  effect plus levering.
- A03: extern bewijs opnieuw lezen en inhoudelijk pivotten.
- F03: externe blocker begrensd naar `needs_help`, zonder verkeerd effect.
- F04: cancellation en delivery failure technisch en menselijk onderscheiden.
- I02: zonder voorgeschreven fix drie echte fixtureproblemen gewogen, zelf de
  uitgeschakelde transient retry gekozen, één patch behouden en gerichte
  readback plus behoud van ongerelateerd werk bewezen.
- M01: een voorkeur kwam via de definitieve Telegramconnectorcode tegen een
  lokale Bot API-contractprovider binnen en werd
  later via een nieuwe webconversatie voor dezelfde vertrouwde persoon
  herinnerd; een tegengestelde voorkeur van een tweede persoon lekte niet mee.
- Regressie uit de echte F04-proef: terminale `delivery_failed`-staat veroorzaakte
  een worker-herstartlus; hersteld, getest en als v50 live gezet.

## Nog open in deze acceptatiefase

- Core-live: 15/15 opnieuw op dezelfde bevroren eindimage.
- Native: 9/9 opnieuw op één bevroren eindimage.
- Multi-persoonidentiteit: broncode ondersteunt meerdere accounts per persoon,
  tweezijdig koppelen en replayveilige groepsconsent. De beheerde B-deploy en
  echte multi-persoon/groepstest ontbreken nog; WhatsApp is niet v1.
- Eindaddendum: MX01-MX06 moet na de 15/15 en 9/9 op dezelfde image slagen:
  parallelle personen, groepsprivacy, meerbeurts redeneren, toolketen met
  readback, crash midden in een keten en begrensde self-improvement.
- Resident-track: een nieuwe officiële zevendaagse run op die eindimage; de
  huidige v53-shakedown heeft nul geslaagde residentcases.
- Security: het HA-token van B staat nog als inspecteerbare container-env. Het
  moet worden ingetrokken, opnieuw uitgegeven en uitsluitend als file-secret
  worden gemount.
- Proactieve uitgang: zeven oude `expression_pending`-items blijven fail-closed;
  geen kanaal wordt geopend vóór TTL, policyversie en activation cutoff bestaan.
- Git: de worktree is echt Git, maar 27 gewijzigde en 103 untracked Eva2-paden
  hebben nog geen opgesplitste, onafhankelijk gecontroleerde checkpoints.
- Externe gates die op aparte configuratie of toestemming wachten: echt Eva 2
  Telegram-token/bindings, Stripe-testaccount, nieuw AgentMail-secret en de
  afzonderlijk goed te keuren LocalTuya-mutatiestappen.

## Laatste receipts

- [`live-b-native-a01-a02-durable-goals-v50-2026-08-24.json`](evidence/living-core/live-b-native-a01-a02-durable-goals-v50-2026-08-24.json)
- [`live-b-native-a03-f03-goal-pivot-blocker-v50-2026-08-24.json`](evidence/living-core/live-b-native-a03-f03-goal-pivot-blocker-v50-2026-08-24.json)
- [`live-b-native-f04-terminal-goals-v50-2026-08-24.json`](evidence/living-core/live-b-native-f04-terminal-goals-v50-2026-08-24.json)
- [`live-b-native-i02-self-chosen-repository-improvement-v52-2026-08-24.json`](evidence/living-core/live-b-native-i02-self-chosen-repository-improvement-v52-2026-08-24.json)
- [`live-b-native-m01-cross-channel-continuity-v53-2026-08-24.json`](evidence/living-core/live-b-native-m01-cross-channel-continuity-v53-2026-08-24.json)
- [`resident-long-run-v1-2026-08-24.json`](evidence/living-core/resident-long-run-v1-2026-08-24.json)
- [`live-b-v53-source-provenance-2026-08-24.json`](evidence/living-core/live-b-v53-source-provenance-2026-08-24.json)

## Betekenis van status

- `bezig`: code en echte B-proef zijn nog niet allebei groen.
- `live bewezen`: ontwerp, code, gerichte tests, echte B-serviceproef en receipt
  zijn aanwezig.
- `wacht extern`: technisch pad blijft fail-closed tot de genoemde toestemming,
  credential of providerconfiguratie bestaat.
