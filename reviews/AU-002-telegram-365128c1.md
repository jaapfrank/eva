# AU-002 — onafhankelijke review van Telegram-brugherstel

**GO voor de begrensde technische brugreparatie en het beschreven lifecyclebewijs.**
Nieuwe menselijke Telegramacceptatie blijft open; geen volledige productacceptatie.

Reviewer: Codex-taak `01a07e5b-3ef4-7ef3-8a68-3197a780694e`.
Reviewmoment: 2026-09-11T08:07:33.629611+00:00.
Implementatie: `365128c160f247aa4291e36e85ba6d7ffe752134`.
Bewijscommit: `631b999ce55de2966a0da3bcdd4238a2f65d04e8`.
Bewijs: `eva2/evidence/product/AU-002-telegram-lifecycle-2026-09-11.json`.

## Bron en installatie

De actuele vertrouwde private Telegramalias wordt aan de bestaande eigenaar en
binding gekoppeld, met behoud van de oorspronkelijke gespreks-/geheugenidentiteit
en de UUID-eigenaar in taakopslag. Onbekende alias, andere scope, groep, onleesbare
binding en intrekking blijven geweigerd. De reviewbevinding over opgeslagen
legacyantwoorden is opgelost: actuele databasebevoegdheid wordt vóór journaltoegang
en replay gecontroleerd. Geen nieuwe rechten, modelroute of budgetwaarden.

De reviewer inspecteerde beide live containers: core en worker gebruiken
`sha256:563aff68d0cb4ea941a8b6c36d688e5430685654e35c955d739539ca0392cc05`.
De drie bestanden zijn onafhankelijk vanuit de actieve core gehasht:

- runtime: `6922c7a1a4be7f8e6d3577f5a5483d3f41b5e4c36fb198802dd541948b320e90`;
- owner_service: `934465533028e525ba75f062621d47ffcbf8e6d7eec6a1b53ef1d5edea0e1566`;
- commands: `b2b5812f75d6d83b8bc38f15c51627a1e60919d5871188a51fbfc4df9dd44504`.

De runtimebytes uit de behouden slot3-parent en de actieve slot4 zijn onafhankelijk
vergeleken: uitsluitend de beoordeelde aliasloader, originele turn en controle vóór
replay verschillen. De andere twee bestanden matchen de beoordeelde bronhashes.
Dit is een samengestelde image op parent54c3, geen volledige nieuwe bronbuild.

## Werkelijk bewezen route

Het gelezen `bridge_lifecycle.py` gebruikt uitsluitend de interne canary-HTTProute,
nieuwe lokale QA-berichtidentiteiten en de actuele vertrouwde senderkoppeling.
Het controleert dat er geen andere actieve hulpopdracht is, volgt de nieuwe taak
alleen-lezen in de database en stuurt daarna een apart stopverzoek. Geen Telegram-
sendfunctie of nieuwe modelaanroep door de reviewer. Het script en zijn log tonen
start via het model, een afzonderlijke legacy-stop tijdens `call_started`, status,
expliciete resume via het model en resultaat ophalen via het legacycommando.

Eigen onafhankelijke live databasereadback bevestigt:

- `work:eva-chat:9f8786fc-d4a3-5d56-b4a6-0c909d737f52`: generatie1, call-id aanwezig,
  gestopt, geen resultaat, onbekend verbruik bewaard;
- `work:eva-resume:0710eb87-57ae-5d9d-a20c-800554e75226`: generatie2 met juiste
  parent, geslaagd, één voltooid modelantwoord, nul tools, 391 invoer- en 470
  uitvoertokens, totaal861; eigenaar en binding ongewijzigd.

De SHA256 van de werkelijk opgeslagen uitvoer en de resultaattekst in het HTTP-log
zijn zelfstandig berekend en identiek:
`432d650306d775910177057ce8f0420f2f71e4501a905d13d1a05c0477cfbd79`.
Alle dertien gearchiveerde bewijsbestanden matchen hun vastgelegde hashes.
De raw logs van 17 alias/database/APIchecks, vijf eerdere commandochecks en vijftien
finale replay/APIchecks zijn gelezen; dit zijn deels overlappende groepen, geen
claim van 37 unieke proeven. Tests zijn niet door de reviewer herhaald.

## Grenzen

De echte menselijke Telegramproef van 09:40NL faalde vóór deze reparatie en blijft
als zodanig bewaard. De interne brugproef bewijst geen aflevering in de Telegramapp
of acceptatie door Jaap. Stop blokkeert resultaatlevering; beëindiging bij de externe
modelprovider en p95-stoplatentie zijn niet bewezen. 643 USD-micros is uitsluitend
referentieboekhouding van de voltooide hulpagentcall, geen gemeten factuur of totaal.

De legacyresultaattekst toont nog JSON. Het startantwoord kan na de snellere
stopbevestiging aankomen en bevat dan oudere planningstaal. Deze presentatiegrenzen
blijven zichtbaar voor de gebruikersproef; de onafhankelijk gecontroleerde opslag
blijft gestopt. Tijdstempels van antwoorden zijn geen nauwkeurige eind-/latentiemeting.

Cutover- en voice-readinessreceipts zijn gelezen; geen fysieke spraakproef of nieuwe
AU-008-acceptatie afgeleid. Voor de core vermeldt het ruwe readinessbestand alleen
HTTP200 (zonder contractveld); het uitgebreide bewijs noemt het contract. Deze
review gebruikt HTTP200 als waargenomen grens en claimt geen eigen nieuwe
contract-/hardwarecontrole.

Geen blokkerende bevinding voor deze beperkte technische reparatie. Laat nieuwe
menselijke Telegramacceptatie en presentatievervolg afzonderlijk op het werkbord.
