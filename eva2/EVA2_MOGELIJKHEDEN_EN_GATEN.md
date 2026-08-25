# Eva 2 — wat kan ze nu en wat kan ze straks?

Bijgewerkt: 25 augustus 2026.

## Eerst het belangrijkste onderscheid

Er zijn drie verschillende momenten. Die mogen niet meer door elkaar lopen:

1. **Eva 2 nu:** de huidige kandidaat in ontwikkeling. De oude Eva blijft
   productie.
2. **Klaar voor de overgang:** Eva 2 mag de oude Eva pas vervangen als chat,
   geheugen, huisacties, spontane aanwezigheid, voice, herstel en de eindproef
   op één versie goed werken.
3. **Het volledige productdoel:** daarna kan Eva verder uitgroeien met extra
   kanalen, zelfgemaakte adapters, subagents, een complete computerwerkplek en
   steeds betere zelfgemaakte skills.

“Ontworpen” of “getest met een nagebootste aansluiting” betekent dus niet
automatisch dat jij het vandaag al kunt gebruiken.

## Het overzicht

| Onderdeel | Eva 2 nu | Nodig voor de overgang | Volledig productdoel |
|---|---|---|---|
| Gewoon tekstgesprek | Werkt, maar jij hebt de natuurlijkheid nog niet goedgekeurd | Stabiel en menselijk gesprek via echte Telegram, ook over meerdere beurten | Hetzelfde gesprek via ieder toegestaan kanaal |
| Mensen leren kennen | Persoonsfeiten, voorkeuren en correcties worden onthouden en gescheiden gehouden | Echte gezinstest met meerdere accounts en een groep, zonder privélek | Een begrijpelijk en corrigeerbaar persoonsmodel dat merkbaar beter wordt |
| Langetermijngeheugen | Recente context, langdurige herinneringen en bewijs van acties worden apart en duurzaam bewaard | Zeven dagen laten zien dat herinneren, corrigeren en vergeten goed blijft gaan | Eventueel snellere of diepere extra lagen, alleen als metingen winst tonen |
| Home Assistant | Toestanden lezen en verschillende echte acties met controle achteraf zijn bewezen | De algemene meerstappenketen moet op de eindversie een echte actie uitvoeren en teruglezen | Nieuwe apparaten en diensten via dezelfde veilige aansluiting gebruiken |
| Spontaan aanwezig zijn | De oude Eva doet dit; Eva 2 nog niet via haar definitieve route | Deurbel en één speaker werken, Eva kiest goede momenten en blijft bij ruis stil | Meer toegestane kamers en apparaten zonder opdringerig te worden |
| Praten en onderbreken | Nog geen doorlopend voicegesprek | Snel praten, antwoord horen, onderbreken en verdergaan via `Bel Eva` of Home Assistant | Dezelfde voice-ervaring via andere geschikte apparaten en kanalen |
| Lange opdrachten | Verschillende duurzame doelen en crashgevallen werken; de complete algemene keten is nog niet af | Een echte meerstapstaak hervat na een crash zonder dubbele huisactie of melding | Lang zelfstandig werken, opnieuw plannen en alleen hulp vragen wanneer nodig |
| Browser en computer | Nog geen eigen interactieve browsercomputer | Eén geïsoleerde browserroute met meekijken, stop, overname en controle van het resultaat | Breder computerwerk binnen een duidelijk mandaat, inclusief veilige accounts |
| Zelf skills maken | Eén kleine zelfgekozen verbetering is bewezen | Zelf een ontbrekende kleine skill maken, testen, veilig activeren en terugdraaien | Herhaaltaken aantoonbaar blijven verbeteren zonder skillstore-afhankelijkheid |
| Chatkanalen | Telegram is de eerste route; Web en e-mail bestaan slechts gedeeltelijk | Telegram moet betrouwbaar en persoonsgebonden werken | Eva kan later zelf een officiële route zoals WhatsApp onderzoeken, testen en koppelen |
| Subagents | Het ontwikkelproces gebruikt agents; Eva zelf nog niet | Eva kan begrensde deeltaken uitzetten, resultaten controleren en na uitval verdergaan | Meer gelijktijdig werk via één duurzaam en begrijpelijk Kanbanbord |
| Dashboard | Kleine read-only status- en identiteitspagina | Duidelijke gezondheid, versie, lopende taak, bewijs en stopknoppen | Chat, geheugenbeheer, skills, subagents, kosten en Kanban in één begrijpelijke bediening |
| Installatie | Losse onderdelen en veilige images bestaan, maar installatie vraagt operatorwerk | Eén reproduceerbare eindimage, backup en veilige A/B-overgang | Een wizard voor nieuwe computers met Telegram-, Home Assistant- en e-mailkoppeling, update en herstel |
| Snelheid en omvang | De bron is nog onder de afgesproken grens; enkele gesprekken waren snel | Eindversie haalt de tekst- en voice-reactietijden zonder steeds grotere context | Zeven dagen aantonen dat reactietijd, tokens, geheugen, kosten en code niet blijven oplopen |
| Bewijs | Veel onderdelen hebben tests en enkele echte bewijsstukken, verdeeld over versies | Alle verplichte proeven opnieuw op precies dezelfde bevroren versie | Openbare benchmarks en herhaalbare vergelijkingen met andere agents blijven publiceren |

## Wat Eva 2 vandaag echt goed kan

De belangrijkste werkende bouwstenen zijn:

- gesprekken en recente context na een herstart hervatten;
- persoonlijke feiten, voorkeuren en correcties onthouden;
- geheugen van verschillende personen en projecten scheiden;
- Home Assistant lezen, bedienen en daarna de toestand opnieuw controleren;
- langlopende doelen bewaren en verschillende crashsituaties hervatten;
- mislukking, annulering, levering en echte uitvoering uit elkaar houden;
- begrensde documenten, bestanden, codetaken en onderzoek uitvoeren;
- als aparte kandidaat naast productie draaien zonder de oude Eva te vervangen.

Dat betekent nog niet dat al deze onderdelen al samen in één definitieve Eva
werken. De eindimage en de gezamenlijke eindproef ontbreken nog.

## Wat de overgang vandaag blokkeert

Eva 2 vervangt de oude Eva nog niet, vooral omdat dit nog ontbreekt:

- jij hebt het echte Telegramgesprek nog niet als natuurlijk genoeg aanvaard;
- de echte gezinstest met meerdere personen en een groep is nog niet gedaan;
- de algemene meerstappenketen heeft nog geen volledige fysieke readback en
  afronding;
- spontane deurbel-/speakerpresence van de oude Eva is nog niet terug;
- onderbreekbare, snelle voice is nog niet gebouwd;
- de definitieve versie is nog niet bevroren en als geheel getest;
- de officiële zevendaagse huisproef moet nog beginnen;
- de A/B-overgang, terugrol en open secretmigratie moeten veilig worden gesloten.

## Wat “klaar” voor Eva betekent

Eva is niet klaar omdat alle vakjes code bevatten. Ze is klaar wanneer jij in
het echte gebruik kunt merken dat zij:

- dezelfde persoon blijft in chat, voice en het huis;
- ieder gezinslid kent zonder privézaken door elkaar te halen;
- snel antwoordt en zich laat onderbreken;
- soms zelf iets nuttigs zegt en vaak bewust niets zegt;
- zelfstandig een opdracht afmaakt binnen jouw grenzen;
- nooit beweert dat iets gelukt is zonder het resultaat te controleren;
- na een crash rustig verdergaat zonder dezelfde echte actie te herhalen;
- uitlegbaar toont wat ze doet, wat het kost en hoe je haar direct stopt.

## Waar de precieze techniek staat

Deze pagina gebruikt bewust gewone taal. Voor controle en ontwikkeling bestaan
aparte documenten:

- technische mogelijkheden en alle open gates:
  `TECHNISCHE_MOGELIJKHEDEN_EN_GATEN.md`;
- begrijpelijke concurrentievergelijking: `CONCURRENTIE_EN_HERGEBRUIK.md`;
- volledig technisch bewijsbord: `TECHNISCHE_PRODUCTGATES.md`;
- oude Eva tegenover Eva 2: `OUDE_EVA_VS_EVA2.md`;
- actuele werkstatus: `PROJECT_STATUS.md`;
- architectuur, beoordelingen en bewijsstukken: `design/architecture/living-core-v1.md`,
  `reviews/` en `evidence/living-core/`.
