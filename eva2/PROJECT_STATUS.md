# Eva 2 — duidelijk projectbord

Laatst bijgewerkt: 30 augustus 2026, 13:12 CEST

Betekenis:

- ✅ Werkt nu en is echt getest.
- 🟡 Werkt deels of alleen binnen een veilige proefgrens.
- ❌ Moet nog worden afgemaakt.

## Praten met Eva2

- ✅ Je kunt Eva2 via `/eva2` op Telegram gebruiken.
- ✅ Gewone berichten zonder `/eva2` blijven naar Eva1 gaan.
- ✅ Eva2 geeft echte antwoorden via de bestaande Codex-koppeling.
- ✅ Eva2 heeft haar eigen warme, jonge, nieuwsgierige, grappige en
  eigenwijze persoonlijkheid.
- ✅ Gesprekken blijven na een herstart bewaard.
- 🟡 Antwoorden worden al in stukjes opgebouwd, maar Telegram toont nog niet
  de volledige echte live tekststroom.

## Jaap, Lola en groepen

- ✅ Jaap en Lola hebben ieder een eigen profiel en eigen geheugen.
- ✅ Hun privégesprekken en herinneringen blijven van elkaar gescheiden.
- ✅ Een echte Telegramgroep met Jaap, Lola en Eva is getest.
- ✅ Groepsinformatie gaat vóór toestemming niet naar het model of iemands
  privégeheugen.
- ✅ Toestemming kan per persoon worden ingetrokken.
- 🟡 De beheerde onderzoeksknoppen via Telegram zijn technisch getest, maar
  staan normaal uit.

## Geheugen en privacy

- ✅ PostgreSQL bewaart gesprekken.
- ✅ Hindsight bewaart langetermijngeheugen.
- ✅ Geheugen bleef bestaan na het stoppen en starten van Eva2, Hindsight en
  beide databases.
- ✅ Een persoon kan een eigen gekozen gesprek uit PostgreSQL en Hindsight
  laten wissen.
- ✅ Een ander persoon kan dat niet voor hem of haar doen.
- ❌ Het oude geheugen van Eva1 is nog niet volledig naar Eva2 overgezet.

## Het huis bedienen

- ✅ Eva2 kan Home Assistant lezen.
- ✅ Bij een onduidelijke lampnaam vraagt Eva welke lamp je bedoelt.
- ✅ Alleen Jaaps vertrouwde profiel mag een huisactie starten.
- ✅ Jaap kan de 59 nu bereikbare lampen aan- en uitzetten.
- ✅ Eva controleert na een actie apart of de lamp werkelijk goed staat.
- ✅ Een herhaald bericht bedient dezelfde lamp niet per ongeluk opnieuw.
- 🟡 Onbereikbare lampen en onveilige dubbelen staan bewust niet in de lijst.
- 🟡 Algemene huisbediening hoeft niet per apparaatgroep te worden gebouwd.
  De officiële Home Assistant MCP Server kan alle bewust aan Assist
  blootgestelde apparaten en scripts als algemene tools aanbieden.
- ✅ De officiële MCP/Assist-route is nu in dit huis geconfigureerd. Eva las de
  echte catalogus met 25 tools en `GetLiveContext`; er is niets bediend.
- ✅ De generieke kandidaat controleert catalogus, invoer en verplichte velden.
  De zes grensproeven zijn groen.
- ✅ Eén echte MCP-huisactie liep via Jaaps bestaande persoonsmandaat en Eva's
  duurzame actielogboek. REST controleerde apart dat de al uitstaande lamp uit
  bleef; een nieuw proces voerde dezelfde actie niet opnieuw uit.
- ✅ De gewone `/eva2`-route gebruikt voor huisacties nu ook de officiële MCP-
  adapter. Jaaps persoonsgrens, het duurzame actielogboek, controle achteraf en
  bescherming tegen dubbel uitvoeren zijn hetzelfde gebleven.
- ✅ De schone MCP-image draait in beheerd slot 4. Beide databases zijn
  verliesloos gekopieerd; Eva1 bleef gezond.
- 🟡 Een al gestarte huisactie kan veilig als onzeker worden bewaard, maar een
  gewone Telegramknop om zo'n lopende actie te stoppen is nog niet aangesloten.
- ❌ Nieuwe automatiseringen maken, integraties wijzigen en Home Assistant
  bijwerken blijft een aparte beheerroute. Eva moet daar eerst een voorstel en
  controle van maken; pas na toestemming mag zij schrijven.
- 🟡 Voor Tuya zijn 7 LocalTuya-kandidaten, 2 betere Matter-routes en 3 opnieuw
  te bouwen scènes gevonden. Dit is een migratie per apparaat, geen tekstuele
  zoek-en-vervangactie.
- ✅ Eva heeft die inventaris na de MCP-omschakeling opnieuw gelezen en als
  duurzaam, niet-schrijvend migratieplan vastgelegd.
- ❌ LocalTuya is nog niet geïnstalleerd en geen Tuya-apparaat is al omgezet.
  Eerst volgen expliciete toelating van `xZetsubou/hass-localtuya` release
  `2026.7.0`, de lokale sleutels en één terugrolbare proef.

## Zelfstandig onderzoek doen

- ✅ Een begrensde hulpagent heeft een echte onderzoeksopdracht uitgevoerd.
- ✅ Die hulpagent had geen toegang tot het huis, Docker, de live database of
  bestanden.
- ✅ Een geslaagde taak en een vastgelopen taak zijn allebei duurzaam en eerlijk
  bewaard.
- ✅ Na een crash start Eva een onduidelijke modeloproep niet blind opnieuw.
- 🟡 Starten, volgen en stoppen via Telegram is met de echte adapter tegen een
  lokale testprovider bewezen.
- ❌ Deze bediening staat nog niet standaard aan in jouw gewone Telegramchat.

## Snelheid en betrouwbaarheid

- ✅ De laatste proef gaf 200 van 200 gewone antwoorden.
- ✅ De meeste antwoorden begonnen binnen twee seconden; p95 was 1,918 seconde.
- ✅ De actuele Eva2-image heeft nul hoge en nul kritieke scanbevindingen.
- ✅ Eva2 draait automatisch herstartbaar op poort `44711`.
- ✅ De oude route en oude proefcontainers staan intact maar stil. Terugrollen
  kopieert eerst nieuwe data terug en schakelt daarna pas om.
- ✅ Een nieuwe controle weigert omschakelen zolang een oude proefcontainer nog
  dezelfde database gebruikt.
- ✅ De bron blijft met 74.979 regels onder de grens van 75.000 regels.
- 🟡 De echte Windows/WSL-herstartproef staat klaar.
- ❌ De Windows-pc moet nog één keer volledig opnieuw worden opgestart om die
  proef af te ronden.
- ❌ De lange praktijktest van zeven dagen moet nog worden uitgevoerd.

## Praten met stem

- 🟡 Lokale spraakherkenning, spraakuitvoer en technisch onderbreken zijn in
  losse proeven gelukt.
- ❌ Een echt gesprek met microfoon en speaker is nog niet uitgevoerd.
- ❌ Tegelijk luisteren en praten met snelle onderbreking is nog niet als
  compleet product klaar.

## Browser en computer bedienen

- 🟡 Eva kan binnen een afgesloten proef één toegestane pagina lezen of één
  toegestane klik uitvoeren.
- ❌ De browseromgeving is nog niet veilig genoeg voor algemeen internetgebruik.
- ❌ Zelfstandig brede computertaken uitvoeren en live door een mens laten
  overnemen is nog niet klaar.

## Installeren, herstellen en bijwerken

- ✅ De installatiewizard kan identiteit en basiskeuzes bewaren, activeren en
  terugzetten.
- ✅ Een kleine installatie kan veilig naar een lege database worden hersteld.
- ✅ Eva2 kan veilig tussen de huidige en vorige versie omschakelen.
- 🟡 De technische installatieonderdelen werken, maar zijn nog bedoeld voor de
  beheerder.
- ❌ Een gewone gebruiker kan Eva2 nog niet volledig met één eenvoudige wizard
  installeren, herstellen en bijwerken.

## Vergelijken met andere assistenten

- 🟡 Er zijn begrensde ontwikkelproeven voor AgentBench en GAIA.
- 🟡 Eva heeft acht vooraf gekozen AgentBench-ontwikkelcases echt uitgevoerd:
  drie waren goed en vijf fout. De foutgevallen laten vooral zien dat haar
  gereedschap- en eindantwoordformaat nog robuuster moet worden.
- ✅ De tijdelijke benchmarkomgeving is daarna volledig opgeruimd. Vragen,
  antwoorden en verborgen beoordelingsgegevens staan niet in dit projectbord.
- ❌ Er zijn nog geen officiële AgentBench-, GAIA- of OSWorld-eindscores.
- ❌ De volledige eerlijke vergelijking met OpenClaw, Hermes en Agent Zero is
  nog niet afgerond.

## Wat jij nu al kunt doen

- ✅ Privé met Eva2 praten via `/eva2`.
- ✅ Met Jaap en Lola controleren dat hun gesprekken gescheiden blijven.
- ✅ Eva vragen naar de toestand van Home Assistant.
- ✅ Als Jaap een bereikbare lamp laten aan- of uitzetten.
- 🟡 De pc volledig herstarten zodra de lopende wijzigingen veilig zijn
  vastgelegd; daarna controleert Eva automatisch of alles goed terugkomt.

## Eerstvolgende stappen

1. De bewapende Windows/WSL-herstartproef afronden.
2. De beheerde Telegrambediening voor onderzoek echt in de gebruikersroute
   beproeven.
3. Tuya veilig per apparaat naar LocalTuya of Matter omzetten, met één proef en
   terugrolcontrole voordat de rest volgt.
4. Een aparte voorstel- en goedkeuringsroute voor automatiseringen en updates
   maken.
5. Echte voice met microfoon en speaker afmaken en daarna de zeven dagen lange
   praktijktest uitvoeren.

Technische details en bewijs staan in
[PROJECTEN_EN_PLANNING.md](PROJECTEN_EN_PLANNING.md),
[TECHNISCHE_PRODUCTGATES.md](TECHNISCHE_PRODUCTGATES.md) en
[evidence/living-core/](evidence/living-core/).
