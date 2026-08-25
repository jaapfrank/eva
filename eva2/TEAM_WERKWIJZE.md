# Eva 2 — vaste teamwerkwijze

Bijgewerkt: 25 augustus 2026.

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
| Core- en reliabilitylead | Fermat the 2nd | Bewaakt PostgreSQL-authority, effecten, crashherstel, secrets, A/B en releaseveiligheid |
| Experience- en capabilitylead | Chandrasekhar the 2nd | Bouwt het zichtbare domein, zoals chat/voice/presence/installer of browser/leren/workers/dashboard |
| Onafhankelijke verificatie- en releaselead | Curie the 2nd | Schrijft vóór de bouw mee aan de eindproef, controleert tussentijds ontwerp en risico's en geeft onafhankelijk stop/go op het geïntegreerde checkpoint |

De actuele canonieke agentmapping is:

- Fermat the 2nd: `/root/continuous_team_model`;
- Chandrasekhar the 2nd: `/root/delivery_estimates`;
- Curie the 2nd: `/root/project_status_audit`.

Deze bestaande agents zijn op 25 augustus 2026 expliciet opnieuw geactiveerd
en aan hun vaste rol gekoppeld. Continu betekent hier blijvend
projecteigenaarschap en heractivering bij iedere relevante stap, niet dat drie
processen tussen werkmomenten nutteloos blijven draaien.

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

## Vaste projectcyclus

1. Het vaste team vertaalt de vraag naar één zichtbare uitkomst en legt vast
   wat uitdrukkelijk buiten scope blijft.
2. De verificatielead schrijft vóór implementatie de positieve, negatieve,
   privacy-, crash-, herstart- en echte provider-/hardwareproef.
3. Leads verdelen exclusieve bestandsscope en spreken interfacecontracten af.
4. Bouwers leveren kleine, testbare Git-checkpoints in een eigen werkboom of
   niet-overlappende bestandsscope.
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

- Iedere bouwstroom gebruikt een eigen Git-werkboom of expliciet exclusieve
  paden.
- Gedeelde interfaces en migraties worden eerst apart vastgelegd en beoordeeld.
- Centrale status- en productdocumenten hebben tijdens een integratieslag één
  schrijver.
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
