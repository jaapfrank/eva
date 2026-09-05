# CO-002 — correctie van de acceptatiestatus

Datum: 5 september 2026. Auteur: huidige uitvoerende agent `/root`.
Dit is een correctie door de uitvoerder, geen onafhankelijke GO-review.

Implementatie: `3b85a61165ebed4b8c1c74d3037b0c0e057593c1`.
Gelezen bewijs: `evidence/product/CO-002-configurable-identity-after-probe-2026-09-04.json`.
Besluit: **niet geaccepteerd; menselijke beoordeling is niet het enige gat**.

Het bewijsbestand sluit af met een beperkte GO-beslissing. Die beslissing
volstaat niet voor acceptatie van de kaart: de eerste call leverde geen
behouden antwoord, de tweede bundelde twee situaties in één verzoek, en de
beoordelaar was een model terwijl een menselijke beoordeling was opgegeven.
Het oorspronkelijke bestand blijft als historisch verslag behouden.

De opvolgende chat vroeg de eigenaar om GO op twee teksten. De soktekst was
daarbij ingekort ten opzichte van de bewaarde uitvoer. Die vraag kan daarom
niet alsnog als een blinde beoordeling van de exacte oorspronkelijke uitvoer
tellen. Er is geen menselijke goedkeuring ontvangen en er wordt geen
goedkeuring afgeleid uit het verzoek om verder te helpen.

De configureerbaarheid en kortere instructies zijn technische eigenschappen;
ze bewijzen op zichzelf geen warmer gesprek of winst tegenover Eva1 of andere
assistenten. De daadwerkelijke Telegramroute wijst bij deze controle naar
slot 4 met broncommit `881be2c36004b499ac9538eb5a3fb29f9f9d2a72`; de
CO-002-implementatie is daar niet uitgerold.

Vervolg: behoud deze open acceptatiegrens, leg vóór verdere modelmetingen
een afzonderlijke herstelproef met exact broncheckpoint, losse gesprekken,
ongewijzigde antwoorden en een klein expliciet verbruiksbudget vast. Tel die
calls niet achteraf bij het verbruik van de afgesloten tweecallproef op.
Beoordeel de echte gebruikerservaring zodra de kandidaat daarvoor gereed is.
De eigenaar hoeft geen kunstmatige GO te geven om de uitvoerder deze
voorbereiding en technische reparatie te laten doen.

## Afzonderlijke, vooraf begrensde herstelcontrole

- Vier losse antwoorden: de bestaande sok- en rouwsituatie ieder vóór en na
  CO-002. Volgorde: sok vóór/na, rouw na/vóór; geen gedeelde gespreksgeschiedenis.
- Alleen de modeladapter uit `1725401` versus `3b85a61`; overige bron op
  `1b48913`, dezelfde ondertekende standaardidentiteit en lege herinneringen.
  Dit is een ontwikkelvergelijking van de wijziging, niet van live Eva1/Eva2.
- Bestaande lokale Pydantic AI `2.37.0` en OpenAI SDK `3.6.0`; geen installatie
  of upgrade. Model `gpt-5.4-mini`, medium reasoning, maximaal vier generaties.
  De bestaande transportretry kan dit maximaal acht providerpogingen maken;
  bij een fout of onvolledig gemeten verbruik stopt deze proef, zonder herhaling.
- Maximaal 900 outputtokens per generatie via de bestaande lokale gebruiksgrens.
  Die grens is geen garantie voor een harde bovengrens aan providerfacturatie.
- Geen tools, database, privéherinneringen, betaalde beoordelaar of deployment.
  Alleen de bestaande korte modellease wordt gelezen; geen login wordt gekopieerd.
- Beoordeling: juiste situatie, zelfstandig oordeel zonder extra beschuldiging,
  natuurlijk Nederlands, passende warmte, geen geforceerde humor bij rouw,
  geen ongefundeerde actieclaim. Exacte antwoorden en werkelijk gemeten gebruik
  blijven zichtbaar. Geen gemeten voordeel betekent geen kwaliteitswinstclaim.
- Tijdelijk uitvoerscript onder `/tmp/eva2-co002-check.U2TfRB`; verwijderen na
  behoud van het resultaat. Geen nieuw productframework of vaste testmotor.

## Uitkomst van deze controle — geen bruikbare nameting

De lokale voorbereiding weigerde eerst een gewijzigde budgetwaarde zonder
bijbehorende nieuwe hash. Dat gebeurde vóór enige generatie; de proefcode is
gecorrigeerd om het bestaande budgetcontract juist te gebruiken.

Op `2026-09-05T11:58:07Z` startte daarna alleen `sok/before`. Die generatie
eindigde met `ConversationModelError`, zonder behouden antwoord of gemeten
gebruik. De foutketen is niet behouden, dus de oorzaak en het aantal werkelijk
verstuurde providerverzoeken zijn **onbekend**. Er volgde geen retry door de
proefrunner, geen tweede situatie en geen nameting. De alleen-lezen controle
van de lease meldde daarna `ready`, geldig tot 14 september; dat bewijst geen
werkende modelverbinding.

Ook de proefopzet bleek niet aan haar eigen nul-toolvoorwaarde te voldoen:
`generate(..., available_actions=())` voegt zelf nog `search_personal_memory` aan de
catalogus toe. Er was geen database of uitvoerder gekoppeld, maar de aanname
dat nul tools werden aangeboden is dus ongeldig. Bovendien kan `generate`
een tweede fase voor taalherstel starten. De vooraf genoemde acht pogingen
waren daarom geen door de runner afgedwongen harde bovengrens. De proef is
gestopt, niet opnieuw verruimd of uitgevoerd. Dit zijn fouten in deze
controleopzet; zij bewijzen geen kwaliteitsgebrek of verbetering van Eva.

Gebruikte ontwikkelomgeving: Python `3.12.3` (niet de release-Python),
Pydantic AI `2.37.0`, OpenAI SDK `3.6.0`.
Adapter vóór: `5d4f5008de87ef56df04072d5948447f6ae2f8b1c76109fab27d893a3e18c967`.
Adapter na: `eb9cb3e91c4fb1526cabaf22f993b248163df09fe8e5a5f01c777a548443b670`.
Tijdelijke runner: `69d4eee649438b566c2e935b1b66bb9d352396fde244159270dc8c14b58d3fa0`.

Er is geen productiecode gewijzigd, geen runtime omgeschakeld en geen
privégeheugen gelezen. De bronwijziging is niet als beter geaccepteerd.
Vóór een volgende modelmeting moet de bestaande modelroute eerst lokaal
worden gecontroleerd op werkelijke requestbegrenzing en bruikbare,
geheimvrije foutdiagnostiek. Geen nieuwe benchmarkinfrastructuur hiervoor.

Het tijdelijke script en resultaat zijn na bovenstaande vastlegging verwijderd.

## Vervolg: oorzaak lokaal gereproduceerd

Met beide adapterversies, dezelfde identiteit, vraag en invoergrens geeft de
gewone `generate`-route `conversation request exceeds the runtime input budget`.
Een verboden providerfactory is in beide gevallen **nul keer** aangeroepen.
De outputgrens is in die invoercontrole niet betrokken. Dit verklaart de
vorige mislukte proef zonder een nieuwe providercall: de afwijzing zit vóór
credentialinvoer en netwerk. De eerder onbekende fout is dus lokaal verklaard,
niet met providerlogs achteraf gemeten.

De bestaande methode `generate_tool_free_text` past wel bij de aangekondigde
proef. Zij krijgt de ongewijzigde instructies van iedere adapter, biedt geen
tools aan en doet geen aparte taalherstelgeneratie. Vervolgcontrole: maximaal
dezelfde vier afzonderlijke vóór/na-antwoorden en maximaal twee bestaande
transportpogingen per antwoord; stoppen bij een fout of gedeeltelijk gebruik.
Geen productieaanpassing en geen dependencywijziging. De 93 bestaande model-,
provider- en contracttests slaagden vooraf lokaal in 6,43 seconden.

## Vier afzonderlijke antwoorden zijn nu bewaard

De [toolvrije vóór/na-proef](../evidence/product/CO-002-separate-before-after-2026-09-05.json)
voltooide alle vier gesprekken. Ieder gesprek deed precies één modelcall en
nul toolcalls: totaal 4.192 inputtokens en 815 outputtokens, samen 5.007.
De exacte instructies, antwoorden, bronhashes en timings staan in het bestand.
De Python-ontwikkelomgeving is nog steeds geen bewijs voor de releaseomgeving.

| Situatie | Vóór | Na |
|---|---|---|
| Onterechte beschuldiging | 79 woorden; 6.243 ms totaal | 66 woorden; 5.491 ms totaal |
| Verdriet | 65 woorden; 3.875 ms totaal | 74 woorden; 4.099 ms totaal |

De afzonderlijke reviewer `/root/identity_pair_review`, model `gpt-5.6-luna`
met laag redeneerniveau, kreeg alleen de exacte teksten, situaties en criteria.
Mapping: sok X=na/Y=vóór; rouw X=vóór/Y=na. Geen bestandsinzage, extra
benchmarkprovider- of betaalde judge-call door de proefrunner. De agentreview
zelf gebruikt wel Codex-credits; het exacte aantal daarvan is hier niet gemeten.

De reviewer koos bij de sok nipt X en bij rouw Y: in beide gevallen de nieuwe
variant. Bij rouw noemt hij het ontbreken van “We missen hem ook” en “Kom
maar hier” gunstiger voor een digitale spreker. Hij noemt de uitkomst expliciet
geen algemene winst en geen menselijke of releasegoedkeuring.

Eén detail in de review klopt niet: hij noemt sok-Y compacter, terwijl de
daadwerkelijke woordtelling juist 79 versus 66 is. Die subjectieve uitspraak
wordt niet als meetfeit overgenomen. Ook de nieuwe rouwvariant is niet
sneller of korter. De proef ondersteunt dus hoogstens een beperkte inhoudelijke
voorkeur in deze twee gevallen, niet algemene snelheid-, kosten- of kwaliteitswinst.

CO-002 blijft in review. Technisch onderzoek kan voortgaan; menselijke
acceptatie is nog open en moet betrekking hebben op de werkelijke
gebruikersroute. De installatie is niet aangepast. Voor de zichtbare
oplevering is nu het verschil tussen de geteste bron en de actieve route
de eerstvolgende technische afhankelijkheid, niet nog een ronde persona-tuning.
