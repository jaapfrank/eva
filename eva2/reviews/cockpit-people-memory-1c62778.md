# Onafhankelijke review — cockpit Mijn kring en geheugen

Datum: 26 augustus 2026.

Beoordeelde productkandidaat:
`1c627787e73f33e6c29ab58be95ebd2c75de6995`.

## Besluit

De drie vaste reviewers geven inhoudelijk **GO voor de status `tested`** zodra
de receipt, deze review, alle gebruikte bronnen en alle tests door de
releasechecker aan één volgende Git-commit zijn gebonden. Dit is uitdrukkelijk
geen `live_proven`, B-deploy- of productieclaim.

De technische reviewer bevestigde dat zijn eerdere semantische blockers zijn
gesloten. De release-/testreviewer heeft de exacte kandidaat onafhankelijk
opnieuw uitgevoerd. De productreviewer gaf GO op taal, screenshot en
begrijpelijkheid nadat `live status` is vervangen door `actuele status`.

## Onafhankelijk opnieuw uitgevoerd

- Een verse disposable PostgreSQL `16.13`-database doorliep migratie naar head,
  acht people-/memoryproeven en downgrade naar base: **8 geslaagd**. De lege
  Eva-namespace werd voor de proef vastgesteld en na afloop waren nul
  `eva_%`-schema's over; de tijdelijke container is verwijderd.
- De API-, application- en geheugenreeks gaf **47 geslaagd**. Alleen de
  zelfreferentiële evidence-hashcheck was bij de kandidaat tijdelijk
  uitgesloten; die moet op de volgende bindingcommit zonder uitzondering groen
  zijn.
- Ruff was groen op de zeven exacte kandidaatpaden.
- De echte runtimecompositie gaf in een netwerkloze, read-only container drie
  groene assertions: gezond Hindsight wordt `ready`, een fout wordt
  `attention` en de runtime gebruikt de privacyveilige overviewpoort.

De uitvoeringsdetails en exacte omgeving staan in
`evidence/cockpit/people-memory-1c62778-test-receipt.json`, receipt-SHA-256
`b37d205cc0214e7a64a8be79b70bd4b999c8e326fc74488ac297e72ff9b74b2a`.

## Gesloten eerdere blockers

- Telegram-user-ID `0` kan niet als gekoppeld account verschijnen.
- Een groepsconsent langer dan 31 dagen of zonder geldige duurzame
  consentreferentie kan niet als actief delen verschijnen.
- Een hash-consistente maar onvolledige of verkeerd geordende geheugentijdlijn
  kan niet als `bijgewerkt` verschijnen.
- Volledige eventvolgorde, aaneengesloten sequence, runtime-scope, threadhash,
  operation-ID, retryteller en succesreceipt/contenthash worden gecontroleerd.
- De duurzame geheugenparser wordt gedeeld met de echte outboxroute.
- De wire-vorm bevat alleen gesloten statussen en geen namen, identifiers,
  herinneringsinhoud, hashes, foutdetails of exacte tijden.

## Verplichte laatste binding

Voor definitieve vrijgave moet de volgende commit:

1. receipt en review als exacte bronnen van `capability/cockpit` opnemen;
2. alle werkelijk gebruikte code, tests en het runtimesmokescript opnemen;
3. de cockpit-evidence-index en managed-B-authority mechanisch herhashen;
4. de volledige dashboardtest zonder deselectie groen maken;
5. de exacte Git-archive met `managed_b_release_inputs` laten slagen.

Als één van deze controles rood is, blijft het besluit NO-GO. Deze review
promoveert de module nooit boven `tested` en bewijst geen namenlijst,
geheugeninhoud-editor, Hindsight Control Plane-koppeling of B-livegebruik.
