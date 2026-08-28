# Eva 2 — korte projectstatus

Laatst bijgewerkt: 28 augustus 2026, 13:32 CEST

## Heel kort

Eva2 werkt nu echt als aparte proef naast Eva1.

Je kunt via `/eva2` met haar praten. Ze gebruikt een echte Codex-koppeling,
heeft haar eigen persoonlijkheid, bewaart het lopende gesprek in PostgreSQL en
kan Home Assistant lezen. Eén al uitgeschakelde lamp kan ze nu ook veilig
uitzetten en daarna apart controleren.

Eva2 is nog geen volledige vervanger voor Eva1. Brede huisbediening,
gezinsgroepen, volledige voice, algemene browserbediening en de definitieve
installatie zijn nog niet klaar.

## Wat werkt al

- Eva1 draait nog gewoon en blijft de standaard.
- Een bericht met `/eva2` gaat naar de aparte Eva2-kandidaat.
- Twee verschillende privéaccounts krijgen nu ieder hun eigen Eva2-profiel.
- Een Telegramgroep zonder gekoppelde toestemming wordt geweigerd en kan dus
  niet per ongeluk in iemands privégeheugen belanden.
- Na een echt gezien groepsbericht kan die persoon privé tijdelijk delen.
  Intrekken sluit alleen die persoon direct weer af.
- Vóór toestemming bewaart Eva geen groepsberichttekst en stuurt zij niets
  daarvan naar het model.
- Eva2 gebruikt Eva1's Codex-login, maar ziet alleen een automatisch vernieuwde toegangssleutel en nooit de volledige login.
- Eva2 klinkt volgens haar ingestelde jonge, warme, nieuwsgierige en eigenwijze
  persoonlijkheid.
- Een gesprek blijft bewaard na een herstart van Eva2 en PostgreSQL.
- Eva2 draait in een eigen container met een eigen PostgreSQL-database.
- Hindsight is aangesloten als langetermijngeheugen van de kandidaat.
- Eva2 kan Home Assistant echt lezen. Op de vraag naar de spotjes overkapping
  antwoordde ze terecht dat die uit stonden.
- Bij een vage huisnaam vraagt Eva nu gewoon welke lamp je bedoelt. Ze gokt
  niet en leest of bedient het huis dan niet.
- Via een gewoon `/eva2`-bericht kan ze die spotjes uitzetten en apart
  controleren of ze echt uit staan.
- Alleen die ene lamp en alleen `uitzetten` zijn toegestaan. Bij `aanzetten`
  legt Eva normaal uit wat ze niet mag en schrijft ze nul huisacties.
- Na een echte herstart gaf dezelfde opdracht binnen 0,1 seconde het bewaarde
  resultaat terug, zonder de lamp nogmaals te bedienen.
- De actuele Eva2-image kan nu veilig worden klaargezet en omgeschakeld.
- De echte omschakeling heen, terug en opnieuw heen bleef groen. Bij een fout
  zet de operator automatisch de vorige route terug.
- Er is een private back-up van Eva1 gemaakt en gecontroleerd.
- De vorige code draait als veilige rollback en ziet evenmin de volledige login.
- Een kleine gesprek- en geheugenset kan veilig naar een lege database worden
  hersteld.

## Wat nog niet klaar is

- Brede huisbediening staat nog uit. Nu werkt alleen één veilig begrensde
  `uitzetten`-actie; andere lampen en `aanzetten` zijn nog geblokkeerd.
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

1. Begrensde huisbediening stap voor stap uitbreiden zonder de harde controle
   en herstartveiligheid kwijt te raken.
2. De echte Telegramgroeptest later met twee gezinsleden uitvoeren.
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
- [Echte begrensde huisactie](evidence/living-core/eva2-home-assistant-bounded-effect-live-2026-08-28.json) en [natuurlijke weigering zonder huisactie](evidence/living-core/eva2-home-assistant-natural-denial-live-2026-08-28.json)
- [Veilige echte omschakeling en rollback](evidence/living-core/eva2-atomic-canary-cutover-live-2026-08-28.json)
- [Aparte privéprofielen voor Telegramaccounts](evidence/living-core/eva2-telegram-private-principal-isolation-2026-08-28.json)
- [Persoonsgebonden toestemming voor Telegramgroepen](evidence/living-core/eva2-telegram-group-consent-live-2026-08-28.json)
- [Normale vraag bij een onduidelijke lampnaam](evidence/living-core/eva2-home-target-clarification-live-2026-08-28.json)
- [Gesprek blijft bestaan na herstart](evidence/living-core/eva2-durable-conversation-restart-2026-08-28.json)
- [Live `/eva2`-koppeling](evidence/living-core/eva2-current-candidate-live-canary-2026-08-28.json) en [afgeschermde, vernieuwbare Codex-toegang](evidence/living-core/eva2-rotating-access-only-model-lease-live-2026-08-28.json)

Meer details staan in [PROJECTEN_EN_PLANNING.md](PROJECTEN_EN_PLANNING.md),
[EVA2_MOGELIJKHEDEN_EN_GATEN.md](EVA2_MOGELIJKHEDEN_EN_GATEN.md) en
[TECHNISCHE_PRODUCTGATES.md](TECHNISCHE_PRODUCTGATES.md).
