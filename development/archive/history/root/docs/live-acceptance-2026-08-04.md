# Live acceptatie Eva — 4 augustus 2026

Deze controle gebruikt echte beurten via `POST /api/chat` tegen de draaiende
`eva.service`. De vaste tests zijn alleen regressievangrails; een scenario telt
pas als geslaagd wanneer het antwoord en het vastgelegde toolbewijs bij elkaar
passen.

## Baseline

- Een vraag naar extra gezinsbijdrage gaf vooral algemene beloften zonder actie.
- `Doe dit.` duurde 120 seconden, gebruikte ongerelateerde middelen en eindigde
  met alleen een intern bestandspad.
- Een concrete geldopdracht duurde meer dan vier minuten en leverde geen
  bereikbare koper of bewezen prijs op.
- Voice PE-voorkeuren bestonden als geheugen, maar waren niet als exact fysiek
  doel afgedwongen.
- In recente Telegram-gesprekken stonden technische statusberichten, ruwe
  tooluitvoer en herhaalde foutmeldingen.

## Live gevonden en tijdens deze sessie hersteld

1. De eerste exacte Voice PE-aanroep gebruikte het juiste domein maar een
   onjuiste WebSocket-servicevorm. Home Assistant gaf HTTP 500. De aanroep
   gebruikt nu een afzonderlijk target en `return_response: false`.
2. De zin “spreek je jezelf tegen?” activeerde ten onrechte de omroeptool. Een
   spraakactie vereist nu een werkelijk fysiek spraakdoel; het losse werkwoord
   is geen routeringssignaal meer.
3. Een geldvraag met het woord “bijdrage” werd als huiscontrole behandeld.
   Het specifiekere onderzoeksdoel wint nu bij overlappende intenties.
4. Een lange onderzoeksbeurt stuurde bij de tijdgrens ruwe scriptuitvoer en
   gescrapete HTML terug. Interne uitvoer is nu een harde kanaalgrens en kan
   niet meer als gezinsantwoord dienen.
5. `Doe dit.` bood na een verkoopgesprek ook huisbediening en code-uitvoering
   aan. Vervolgbeurten gebruiken nu de directe vorige uitkomst om irrelevante
   tooldomeinen weg te laten.
6. Een gecontroleerd document viel terug op zijn interne pad. De fallback geeft
   nu de teruggelezen inhoud en niet het workspacepad of de bestandsnaam.
7. De gevonden Freelancer-opdracht bleek bij onafhankelijke broncontrole al op
   23 juli verlopen. Het werkpunt is teruggezet naar geblokkeerd en de gemaakte
   biedtekst geldt alleen nog als sjabloon. Commercieel onderzoek mag een listing
   zonder exacte toekomstige sluitingsdatum niet meer als bereikbare koper zien.
8. “Doe dit. Werk het uit” werd door de losse woorden “doe” en “uit” als
   huisbediening gelezen. Een huisactie vereist nu ook een werkelijk apparaat,
   kamerdoel of entity-id.

## Post-fix live resultaten

| Scenario | Uitkomst | Tijd | Bewijs |
|---|---|---:|---|
| Zelf een actuele bijdrage kiezen | Eva controleerde huiscontext en liet de open achterdeur bewust ongemoeid omdat buiten koeler was; geen onbewezen actieclaim | 24,9 s | `huis_status`, geverifieerd |
| Exact via woonkamer-Voice PE spreken | Home Assistant accepteerde de omroep op de woonkamer-Assist-satellite; geen speaker-, versterker- of Chromecastfallback | 41,6 s | `assist_satellite.announce`, exact target |
| Volledige autonomie uitleggen en tegenspraak corrigeren | Eva corrigeerde zichzelf: geen opdracht of toestemming nodig, wel een technische activatie door haar eigen ritme/gebeurtenis | 8,4 s | runtimecapaciteit in beurtcontext |
| Geldmogelijkheid onderzoeken | De eerste kandidaat bleek verlopen en is ongeldig verklaard. Een nieuwe proef vond wel een betaalde vacature met sluitingsdatum 2026-08-31, maar wees die eerlijk af als niet-passend dienstverband; geen bereikbare losse koper bewezen | 48,2 s | één geslaagde actuele webcontrole + commerciële bewijsgrens |
| Letterlijk `Doe dit.` na eigen keuze | Volledige Engelstalige biedtekst gemaakt en teruggegeven. Na broncorrectie is dit een herbruikbaar sjabloon, niet een plaatsbare bieding voor de verlopen opdracht | 25,5 s | schrijven + exact teruglezen |
| Vrije concrete gezinsbijdrage buiten huisbediening | Een afgebakende betaalde eerste mijlpaal met levering, invoer, acceptatiecriteria en grenzen gemaakt | 48,3 s | bestaand bewijs + schrijven + teruglezen |
| Lang natuurlijk vervolg `Doe dit. Werk …` | De oude routeringsfout werd gevonden en verwijderd. Eva maakte daarna een nieuwe kamerkoeltewijzer; de eerste synthese miste huiscontext, waarna gerichte recovery de badkamer correct als koelste kamer aanwees | 119,6 s + 9,4 s | schrijven + teruglezen; daarna actuele `huis_status` |
| Schone langere vervolgproef op de definitieve runtime | Na een nieuwe keuze voor een vertrekcheck begreep Eva `Doe dit. Werk het direct uit …`, schreef en las exact het nieuwe resultaat terug en gaf uitsluitend de bruikbare checklist | 13,7 s | schrijven + exact teruglezen; geen pad, dump of ongerelateerde tool |

## Acceptatiegrenzen

- Geen succesclaim zonder geverifieerd toolbewijs.
- Geen ruwe JSON, HTML, stdout/stderr, exitcodes, workspacepaden of interne
  bestandsnamen naar bewoners.
- Een vervolgzinnetje verwijst naar de directe gesprekstaak, niet naar het
  oudste globale werkpunt.
- Een fysiek Voice PE-doel gebruikt exact één toegestane `assist_satellite` en
  wijkt bij fouten nooit uit.
- Volledige autonomie betekent vrije doelkeuze. Veiligheids- en bewijsgrenzen
  beperken claims en externe gevolgen, niet Eva's denk- of antwoordstijl.

## Nog te bewaken

- Actueel webonderzoek is met ongeveer 80 seconden nog traag.
- Een nieuwe eenvoudige bouwopdracht kan nog te veel stappen nemen; de
  interactieve toolpogingen zijn daarom begrensd, maar dit vraagt vervolgmeting.
- Een geaccepteerde omroep bewijst de Home Assistant-serviceaanroep; hoorbaar
  geluid blijft een fysieke waarneming van een bewoner.
- De kwaliteit van eigen initiatief moet over meerdere echte autonome cycli
  worden beoordeeld, niet uit één geforceerde chatopdracht.
