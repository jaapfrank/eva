# Eva 2 — vaste teamwerkwijze

> Actuele werkwijze, eigenaaropdracht 21 september 2026: één coördinator en
> maximaal twee gelijktijdige uitvoerders, met gescheiden opdrachten en bestanden.
> Onafhankelijke review gebeurt gebundeld binnen dezelfde bezetting. Resultaat,
> eindvoorwaarde en verbruiksraming vooraf; werkelijk meetbaar verbruik achteraf.
> Geen herhaalde proef zonder nieuwe oorzaak, hypothese of ontbrekend bewijs.
> De vaste vierkoppige bezetting, oude agentnamen en werkmappen hieronder zijn
> historische werkafspraken, geen actuele opdracht of autorisatie.
> Zie [de herbeoordeling](HERBEOORDELING_2026-09-21.md) en [het werkbord](WERKBORD.md).

Bijgewerkt: 1 september 2026.

## Waarom deze werkwijze verandert

De eerste bouwfase werkte vooral met wisselende agents per kleine technische
stap. Dat leverde sterke controles en kleine herstelbare Git-checkpoints op,
maar te weinig blijvende verantwoordelijkheid voor een compleet
gebruikersproject. Continuïteit zat vooral in code en documenten, niet in een
vast team dat samen van vraag tot eindproef optrok.

Vanaf nu blijft een vast projectteam betrokken van ontwerp tot live bewijs en
gebruikersacceptatie. Kleine technische stappen blijven nodig, maar een project
is pas af wanneer de begrijpelijke gebruikersuitkomst werkt.

## Het vaste team

Ieder actief hoofdproject heeft vanaf de start vier blijvende rollen:

| Rol | Vaste bezetting | Verantwoordelijkheid van begin tot eind |
|---|---|---|
| Product- en integratielead | Codex-hoofdagent | Bewaakt gebruikersuitkomst, architectuur, afhankelijkheden, planning en samenvoeging met de rest van Eva |
| Core- en reliabilitylead | Volta the 2nd | Bewaakt PostgreSQL-authority, effecten, crashherstel, secrets, A/B en releaseveiligheid |
| Experience- en capabilitylead | Mill the 2nd | Bouwt en vereenvoudigt het zichtbare domein, zoals chat/voice/presence/installer of browser/leren/workers/dashboard |
| Onafhankelijke verificatie- en releaselead | Godel the 2nd | Schrijft vóór de bouw mee aan de eindproef, controleert tussentijds ontwerp en risico's en geeft onafhankelijk stop/go op het geïntegreerde checkpoint |

De actuele opnieuw activeerbare agentmapping is:

- Volta the 2nd: `/root/release_authority`;
- Mill the 2nd: `/root/pydantic_dedup`;
- Godel the 2nd: `/root/proof_review`.

De rolnamen en projectkaarten zijn blijvend; een technische agentsessie kan
worden vervangen. Bij vervanging wordt op het werkbord eerst eigenaar, scope,
laatste groene checkpoint en open review overgedragen. Continu betekent dus
blijvend projecteigenaarschap, niet dat een proces zonder taak CPU blijft
gebruiken.

De concrete bouwer kan per kleine stap wisselen, maar de vier projecteigenaren
blijven. Niemand keurt zijn eigen werk goed. Wanneer een vaste lead zelf code
schrijft, beoordeelt een andere lead die stap onafhankelijk.

Als een canonieke agentsessie technisch moet worden vervangen, neemt de opvolger dezelfde
rol en projectkaart expliciet over; eigenaar, laatste groen checkpoint, open
review en eerstvolgende proef worden vóór nieuw bouwwerk vastgelegd. Zo blijft
de productverantwoordelijkheid continu, ook wanneer een proces niet eeuwig
kan blijven draaien.

De gebruiker blijft producteigenaar. Alleen hij kan waarneembare eigenschappen
zoals natuurlijk gesprek, prettige voice en goede huisaanwezigheid accepteren.

## Werken op projectniveau

Voor ieder project bestaat één kaart in `PROJECTEN_EN_PLANNING.md` met:

- de begrijpelijke gebruikersbelofte;
- wat aantoonbaar af is en wat nog niet;
- vaste leads en onafhankelijke reviewer;
- afhankelijkheden, risico's en resterende raming;
- de eerstvolgende zichtbare demo;
- gerichte tests, echte eindproef, rollback en bewijs;
- status `ONTWORPEN`, `IN BOUW`, `TECHNISCH AF` of `AF`.

Een groen technisch checkpoint verandert de projectstatus alleen wanneer het
een vooraf afgesproken deeluitkomst sluit. Aantallen commits of regels code
zijn nooit projectvoortgang op zichzelf.

## Het uitvoerbord

`PROJECTEN_EN_PLANNING.md` blijft de brede productkaart. De concrete
uitvoerwachtrij staat uitsluitend in `WERKBORD.md`; de machineleesbare bron is
`design/product/execution-workboard-v1.json`.

- Er staat maximaal één primaire implementatietaak op `in_progress`.
- We werken op oplopend volgnummer. Een nieuwe wens of praktijksituatie wordt
  eerst als test bij een bestaande capability geplaatst en onderbreekt de
  actieve kaart niet.
- Vóór een kaart start, staan exacte wijzigingspaden, concrete testcommando's,
  vereiste evidence, eigenaar, onafhankelijke reviewer en `doneWhen` vast.
- Een kaart gaat na implementatie naar `review`; tijdens die controle start de
  integratielead geen nieuwe primaire implementatiekaart.
- `done` betekent dat code, tests, vereist echt bewijs en documentatie aan één
  implementatiecommit zijn gebonden en de onafhankelijke review aan een daarop
  volgende reviewcommit. Een groene losse unit-test is niet genoeg.
- De workboardchecker weigert meerdere actieve kaarten, vooruitwijzende
  afhankelijkheden, onbekende componenten, ontbrekende bestanden en een
  actieve kaart zonder exacte bestandsscope.

## Eén canonieke schrijfboom

De enige plek waar het team Eva2-bron, tests of projectdocumentatie schrijft
is `/home/administrator/project/Bottie-candidates/v2/eva2`. Alle agents delen
diezelfde canonieke boom. Een kaart wijst vooraf exclusieve bestandspaden toe;
twee agents schrijven nooit tegelijk aan hetzelfde pad.

- Er worden geen extra Git-worktrees of andere Eva2-bronmappen als zichtbare
  sibling onder `/home/administrator/project` gemaakt.
- Tijdelijke test- en buildmappen worden met `mktemp` onder `/tmp` gemaakt en
  na de taak altijd opgeruimd.
- `/home/administrator/project/.eva2-artifacts` is alleen een beheerd
  artifactarchief en vereist een expliciete kaart met eigenaar, doel, exact
  retentiepad en opruimmoment. Het is nooit een schrijfboom.
- `WERKBORD.md`, `PROJECT_STATUS.md` en andere centrale projectdocumenten
  hebben op ieder moment precies één aangewezen schrijver.
- Een agent meldt vóór de eerste wijziging zijn exacte paden. Werk buiten die
  paden stopt totdat de integratielead de kaart zichtbaar heeft aangepast.

## Vaste projectcyclus

1. Het vaste team vertaalt de vraag naar één zichtbare uitkomst en legt vast
   wat uitdrukkelijk buiten scope blijft.
2. De verificatielead schrijft vóór implementatie de positieve, negatieve,
   privacy-, crash-, herstart- en echte provider-/hardwareproef.
3. Leads verdelen exclusieve bestandsscope en spreken interfacecontracten af.
4. Bouwers leveren kleine, testbare wijzigingen in de gedeelde canonieke boom
   en uitsluitend binnen hun niet-overlappende bestandsscope.
5. De reviewer kijkt tijdens de bouw mee bij trustgrenzen en controleert ieder
   gereed checkpoint onafhankelijk. Reviews stapelen niet op tot het einde.
6. De integratielead voegt uitsluitend groene, beoordeelde checkpoints samen.
7. Het vaste team herhaalt regressies op het geïntegreerde checkpoint en voert
   daarna de echte provider-, netwerk- of hardwareproef uit.
8. Projectbord, technische bewijsstukken en resterende raming worden samen
   bijgewerkt. Iedere hoofdoplevering eindigt met een zichtbare demo.
9. Na freeze blijft hetzelfde team eigenaar van de residentproef, problemen,
   rollback en uiteindelijke gebruikersacceptatie.

## Parallel werken zonder elkaars wijzigingen te beschadigen

- Iedere bouwstroom gebruikt exclusieve paden in de ene canonieke schrijfboom.
- Gedeelde interfaces en migraties worden eerst apart vastgelegd en beoordeeld.
- Centrale status- en productdocumenten hebben steeds één schrijver.
- Samenvoegen gebeurt gericht per checkpoint; nooit via een brede `git add`.
- De verificatielead test het samengevoegde resultaat opnieuw. Een groene losse
  branch is geen groen product.
- Maximaal drie bouwstromen lopen tegelijk; één teamrol bewaakt review,
  integratie en release in plaats van een vierde ongecontroleerde bouwstroom.

## Harde bewijsregels

- De bouwer keurt zijn eigen werk niet goed.
- Een unit- of synthetische test is nooit het eindbewijs.
- Een lokale providerstub heet nooit een echte publieke Telegramtest.
- Bewijs van oudere images bewijst niet automatisch de actuele kandidaat.
- `Gedaan` betekent dat Eva de werkelijke doeltoestand onafhankelijk heeft
  gecontroleerd wanneer dat technisch mogelijk is.
- De officiële zevendaagse klok start pas nadat image, configuratie en policy
  zijn bevroren. Een noodzakelijke productwijziging reset die klok.
- Geen proactieve uitgang wordt geopend zolang oude wachtrijitems, TTL,
  routebinding, activeringsgrens of deliverybewijs onduidelijk zijn.
- Productie A blijft onaangeraakt tot de afzonderlijke ondertekende A/B-gate.
- Secrets worden niet in Git, receipts, procesargumenten of container-env
  gepubliceerd.
- Een project heet alleen `AF` volgens de definitie in
  `PROJECTEN_EN_PLANNING.md`.

## Vaste communicatie

- Het projectbord wordt na iedere hoofdoplevering en minimaal wekelijks
  bijgewerkt met bewijs, blocker en nieuwe raming.
- De gebruiker krijgt twee geplande acceptatiemomenten per week zolang er een
  zichtbare kandidaat te beoordelen is.
- Benodigde accounts, secrets, apparaten en testvensters worden vooraf op het
  bord gezet, zodat het team niet pas aan het einde op toegang wacht.
- Een blokkade of afgekeurde veiligheidsreview wordt direct zichtbaar gemaakt;
  hij wordt niet verstopt achter technische voortgang.

## Lopende herstelvoorwaarden

De eerdere resident-shakedown blijft geen officiële acceptatierun. De uitgang
blijft gesloten totdat de residentpolicy duurzaam en atomisch is, sensorbesluit
en levering niet van Hindsight-cold-path afhangen, oude proactieve items veilig
zijn afgehandeld en het Home Assistant-secret van B is geroteerd naar de
goedgekeurde file-secretroute.

Daarna worden core 15/15 en native 9/9 samen op één bevroren image herhaald.
Pas diezelfde image mag de officiële residentrun en overgangsbeslissing ingaan.
