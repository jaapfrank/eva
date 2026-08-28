# Eva 2 — korte projectstatus

Laatst bijgewerkt: 28 augustus 2026, 12:34 CEST

## Heel kort

Eva2 werkt nu echt als aparte proef naast Eva1.

Je kunt via `/eva2` met haar praten. Ze gebruikt een echte Codex-koppeling,
heeft haar eigen persoonlijkheid, bewaart het lopende gesprek in PostgreSQL en
kan de actuele toestand van Home Assistant lezen.

Eva2 is nog geen volledige vervanger voor Eva1. Schrijven naar het huis,
gezinsgroepen, volledige voice, algemene browserbediening en de definitieve
installatie zijn nog niet klaar.

## Wat werkt al

- Eva1 draait nog gewoon en blijft de standaard.
- Een bericht met `/eva2` gaat naar de aparte Eva2-kandidaat.
- Eva2 geeft echte modelantwoorden via de bestaande Codex-login van Eva1.
- Eva2 klinkt volgens haar ingestelde jonge, warme, nieuwsgierige en eigenwijze
  persoonlijkheid.
- Een gesprek blijft bewaard na een herstart van Eva2 en PostgreSQL.
- Eva2 draait in een eigen container met een eigen PostgreSQL-database.
- Hindsight is aangesloten als langetermijngeheugen van de kandidaat.
- Eva2 kan Home Assistant echt lezen.
- De vraag `Staan de spotjes overkapping nu aan of uit?` kreeg het echte
  antwoord: `De spotjes overkapping staan uit.`
- Die huisvraag werd na een herstart in 51 ms uit het logboek teruggegeven,
  zonder Home Assistant nog een keer te lezen.
- Bij een vage huisnaam vraagt Eva nu gewoon welke lamp je bedoelt. Ze gokt
  niet en leest of bedient het huis dan niet.
- Huisbediening staat standaard uit. Eva2 kan nu dus niets per ongeluk
  aanzetten, uitzetten of automatiseren.
- De actuele Eva2-image kan nu veilig worden klaargezet en omgeschakeld.
- De echte omschakeling heen, terug en opnieuw heen bleef groen. Bij een fout
  zet de operator automatisch de vorige route terug.
- Er is een private back-up van Eva1 gemaakt en gecontroleerd.
- Het vorige Eva2-slot blijft beschikbaar als directe rollback.
- Een kleine echte gesprek- en geheugenset kan naar een lege nieuwe database
  worden hersteld.
- Versleutelde back-ups, objectherstel en herstelcontrole zijn technisch
  beproefd.
- Een subagent kan begrensd worden gestart, gevolgd en gestopt.
- Een browser kan in een afgesloten proef een vaste pagina lezen en één vaste
  klik uitvoeren.
- Lokale spraak kan via Piper en Whisper door Eva's gesprekcore lopen.
- De installer kan een `SOUL.md` laten zien als veilig voorstel, zonder hem
  meteen te activeren.
- De AgentBench-omgeving en 60 officiële DBBench-devcases zijn veilig geladen.

## Wat nog niet klaar is

- Huisbediening met echte aan/uit-acties staat nog uit.
- De volledige Telegramproef met twee personen en een groep is nog niet groen.
- Eva1-geheugen is nog niet naar Eva2 gekopieerd. Eerst moet duidelijk zijn
  welk oud persoonlabel bij welk nieuw profiel hoort.
- Full-duplex praten, luisteren en onderbreken met een echte microfoon en
  speaker is nog niet getest.
- De browserimages zijn nog niet veilig genoeg voor algemeen gebruik.
- De gewone gebruiker kan Eva2 nog niet zelf installeren, herstellen en
  bijwerken met één eenvoudige wizard.
- Eva2 is nog niet als beheerde B-versie gepromoveerd.
- De volledige rollback-, reboot- en zevendaagse praktijktest moet nog.
- Officiële AgentBench-, GAIA- en OSWorld-scores zijn nog niet uitgevoerd.
- De volledige eindvergelijking met OpenClaw, Hermes en Agent Zero is nog niet
  groen.

## Waar ik nu aan werk

1. De echte Telegramroute met gezinscontext verder sluiten.
2. Daarna pas begrensde huisbediening met onafhankelijke controle aanzetten.
3. De reboot- en langere praktijktest uitvoeren.

## Belangrijk verschil

- **Eva1:** de huidige standaardservice in `/home/administrator/project/Bottie`
  met SQLite.
- **Eva2:** de aparte kandidaat in
  `/home/administrator/project/Bottie-candidates/v2/eva2` met PostgreSQL en
  Hindsight.
- Alleen een expliciet `/eva2`-bericht gaat nu naar Eva2.

## Laatste bewijs

- [Echte alleen-lezen huisvraag](evidence/living-core/eva2-home-assistant-readonly-live-2026-08-28.json)
- [Veilige echte omschakeling en rollback](evidence/living-core/eva2-atomic-canary-cutover-live-2026-08-28.json)
- [Normale vraag bij een onduidelijke lampnaam](evidence/living-core/eva2-home-target-clarification-live-2026-08-28.json)
- [Gesprek blijft bestaan na herstart](evidence/living-core/eva2-durable-conversation-restart-2026-08-28.json)
- [Live `/eva2`-koppeling en Eva1-back-up](evidence/living-core/eva2-current-candidate-live-canary-2026-08-28.json)

Meer details staan in [PROJECTEN_EN_PLANNING.md](PROJECTEN_EN_PLANNING.md),
[EVA2_MOGELIJKHEDEN_EN_GATEN.md](EVA2_MOGELIJKHEDEN_EN_GATEN.md) en
[TECHNISCHE_PRODUCTGATES.md](TECHNISCHE_PRODUCTGATES.md).
