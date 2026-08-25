# Eva 2 tegenover OpenClaw, Hermes, Agent Zero en OpenBot

Bijgewerkt: 25 augustus 2026.

## Het korte antwoord

**Eva 2 is vandaag nog niet beter als compleet product.** Ze is nog in aanbouw.
Op sommige onderdelen is zij al sterk bewezen, zoals Home Assistant-acties met
controle achteraf, gescheiden gezinsgeheugen en doorgaan na een crash. Maar ze
mist nog belangrijke dingen die de andere agents al tonen, zoals een echte
browsercomputer, veel kant-en-klare kanalen, subagents en snelle voice.

**Als de afgesproken eindversie echt klaar en bewezen is, is Eva bedoeld als de
betere agent voor jouw situatie:** niet alleen een chatbot of computerrobot,
maar één herkenbare huisgenoot die mensen kent, zelf initiatief neemt, via chat
en stem bereikbaar is, veilig zelfstandig werk doet en achteraf kan bewijzen
wat werkelijk is gebeurd.

Dat is nu nog een doel, geen gewonnen vergelijking.

## Hoe je deze pagina leest

- **Nu** betekent: het werkt aantoonbaar in de huidige Eva 2-bron of in een
  echte proef.
- **Volledig productdoel** betekent: alles wat we uiteindelijk voor Eva hebben
  afgesproken. Een deel daarvan komt pas na de eerste veilige overgang.
- **Bewezen beter** betekent: Eva en de concurrent deden dezelfde taak met
  dezelfde spelregels en Eva won. Alleen een plan of een mooie demo telt niet.

De eerste veilige overgang van de oude Eva naar Eva 2 is dus een kleinere
tussenstap. Die staat apart in `EVA2_MOGELIJKHEDEN_EN_GATEN.md`.

## Is de eindversie werkelijk beter?

| Wat jij merkt | Eva 2 nu | Volledig productdoel | Wie staat vandaag voor? | Is de volledige Eva dan beter? |
|---|---|---|---|---|
| Een natuurlijk gesprek | Tekstgesprek werkt, maar jij hebt de ervaring nog niet goedgekeurd | Dezelfde herkenbare Eva in tekst en stem, met meerbeurts begrip en korte natuurlijke antwoorden | Nog geen duidelijke winnaar; de gelijke test was te klein | **Beoogd: ja voor jou.** Pas bewezen wanneer jij Eva verkiest in blinde gesprekken en dagelijks gebruik |
| Eva kent jou echt | Persoonsfeiten en correcties worden onthouden en gescheiden gehouden | Eva leert voorkeuren, doelen, gewoontes en relaties per persoon en laat fouten makkelijk corrigeren | Hermes beschrijft vandaag de rijkste groeicyclus | **Beoogd: ja.** Zij moet mensen minstens zo goed kennen zonder privélek of onbeheersbare kosten |
| Praten zoals ChatGPT Voice | Nog niet aanwezig | Direct praten, antwoord horen, onderbreken en verdergaan via `Bel Eva` of Home Assistant | Andere producten hebben meer voicebouwstenen | **Beoogd: ja voor jouw Eva.** Niet door méér voicefuncties, maar doordat stem exact dezelfde Eva als chat en huis gebruikt |
| Aanwezig zijn in huis | Oude Eva praat soms spontaan via speaker of deurbel; Eva 2 nog niet | Eva kiest zelf nuttige momenten, praat via een gekozen speaker of deurbel en weet ook wanneer ze moet zwijgen | De oude Eva staat hier voor op Eva 2 | **Ja, als jij dat zo ervaart.** De zeven dagen huisproef en jouw oordeel beslissen dit |
| Iets in huis regelen | Meerdere Home Assistant-taken zijn echt uitgevoerd en teruggelezen | Algemene opdrachten kunnen uit meerdere stappen bestaan; Eva controleert na iedere belangrijke actie de echte toestand | Eva heeft in onze opstelling het beste echte Home Assistant-bewijs | **Beoogd: ja.** Vooral doordat “gedaan” pas volgt nadat Eva de gevraagde toestand werkelijk terugziet |
| Zelfstandig computerwerk | Nog geen eigen browsercomputer | Eva kan websites gebruiken, formulieren voorbereiden, bestanden verwerken en binnen een duidelijke afspraak zelfstandig doorgaan | OpenBot en Agent Zero staan hier zichtbaar voor | **Niet per se als losse computeragent.** Wel beter voor jou als dit één geheel vormt met Eva's geheugen, huis en afspraken |
| Lang werk en herstel na een crash | Verschillende doelen en herstelgevallen zijn bewezen; de complete keten is nog niet af | Eva hervat plannen, vraagt alleen hulp wanneer nodig en veroorzaakt geen dubbele betaling, actie of melding | Geen eerlijke volledige vergelijking uitgevoerd | **Beoogd: ja.** Dezelfde crashtest moet bij Eva geen dubbele echte actie en wel een eerlijk eindantwoord tonen |
| Zelf nieuwe vaardigheden maken | Eén begrensde zelfgekozen verbetering is bewezen; geen volledige leercyclus | Eva maakt zelf een kleine skill wanneer die ontbreekt, test haar, zet haar veilig aan en draait terug bij verslechtering | Hermes staat vandaag voor in beschreven skillgroei | **Beoogd: ja voor veilig zelfleren.** Pas bewezen als herhaaltaken verbeteren zonder store, code-explosie of ruimere rechten |
| Extra chatkanalen toevoegen | Telegram is de eerste route; WhatsApp kan Eva nog niet zelf toevoegen | Eva kan vanuit een gesprek een officiële kanaalroute onderzoeken, een kleine aansluiting maken, testen en koppelen | OpenClaw en Hermes hebben veel meer kanalen klaarstaan | **Niet per se in aantal.** Eva wint alleen als een nieuw toegestaan kanaal zonder breinwijziging of persoonsverwisseling werkt |
| Werken met subagents | Alleen het ontwikkelteam gebruikt nu agents | Eva verdeelt begrensde deeltaken, controleert resultaten en bewaart alles op één werkbord | Hermes, OpenClaw en Agent Zero tonen al multi-agentmogelijkheden | **Nog onbekend.** Eva moet na uitval hervatten, dubbel werk voorkomen en één duidelijke opdrachteigenaar houden |
| Dashboard en Kanban | Alleen een kleine alleen-lezen status- en identiteitspagina | Chat, geheugen, doelen, subagents, kosten, bewijs, stopknoppen en Kanban zijn begrijpelijk zichtbaar | Hermes en OpenBot hebben vandaag een rijker beheeroppervlak | **Niet automatisch.** Eva's dashboard is beter voor jou als het begrijpelijker is en exact dezelfde waarheid als haar database toont |
| Installeren op een andere computer | Technisch mogelijk in delen, maar nog geen eenvoudige installatie | Een wizard stelt gewone vragen en koppelt Telegram, Home Assistant en e-mail; backup, update en rollback zijn ingebouwd | Concurrenten zijn meestal makkelijker te starten | **Nog onbekend.** Een gewone gebruiker moet Eva kunnen installeren en herstellen zonder YAML- of containerzoekwerk |
| Snel en betaalbaar reageren | In twee kleine tests was Eva sneller, maar er is geen eindmeting | Tekst en voice hebben harde reactietijdbudgetten; geheugen, tokens, kosten en codeomvang worden begrensd | Onbeslist | **Nog onbekend.** Eva moet ook na zeven dagen snel blijven zonder steeds meer context, geheugen of achtergrondwerk |

## Waarom Eva anders moet worden dan de rest

De concurrenten hebben sterke onderdelen. Eva hoeft die niet allemaal opnieuw
te bouwen. Haar voordeel moet de **combinatie** zijn:

1. **Eén persoon, geen verzameling bots.** De Eva in Telegram, voice, het huis
   en de browser heeft dezelfde identiteit en hetzelfde toegestane geheugen.
2. **Een echte huisgenoot.** Ze wacht niet altijd op een prompt, maar kiest
   soms zelf een passend moment en blijft stil wanneer spreken niet helpt.
3. **Handelen zonder stoerdoenerij.** “Gedaan” betekent dat Eva het resultaat
   opnieuw heeft bekeken. Alleen een technische ontvangstmelding is niet genoeg.
4. **Zelfstandig binnen jouw afspraak.** Je geeft doel, grenzen en stopregels
   één keer aan. Binnen die afspraak mag Eva doorwerken zonder bij iedere klik
   opnieuw toestemming te vragen.
5. **Leren zonder los te slaan.** Eva mag mensen beter leren kennen en zelf
   skills maken, maar niet ongemerkt meer rechten nemen of onbeperkt code,
   geheugen en kosten opstapelen.
6. **Herstellen zonder dubbel effect.** Na een crash zoekt Eva eerst uit wat al
   gebeurd is. Ze drukt niet voor de zekerheid nog een keer op dezelfde knop.

Als één van deze zes punten ontbreekt, is Eva misschien een goede agent, maar
niet de betere levende assistent die we hier proberen te bouwen.

## De concurrenten in gewone taal

| Product | Waar het vandaag sterk in is | Wat Eva daarvan nodig heeft | Waar Eva uiteindelijk beter moet zijn |
|---|---|---|---|
| **OpenClaw** | Veel kanalen, plugins, gateway en gekoppelde apparaten | De bestaande gateway en nodes mogen losse uitvoerders voor Eva worden | Eén consistent persoon en geheugen, plus streng bewijs na iedere echte actie |
| **Hermes** | Veel tools en kanalen, persoonsgeheugen, zelf skills maken en verbeteren, subagents en werkbord | Goede standaarden en eventueel losse tools hergebruiken | Dezelfde groei combineren met gezinsprivacy, huisaanwezigheid, fysieke controle en crashveilig handelen |
| **Agent Zero** | Browser, desktop, terminal, projecten en subagents | Een geïsoleerde computer kan als verwisselbare uitvoerder dienen | De computer blijft een hand van Eva, niet een tweede brein met eigen waarheid of geheugen |
| **OpenBot** | Een eigen browsercomputer per bot, live meekijken en overnemen, duidelijk beheer van tools en credentials | Het computer- en takeoverpatroon is een goede referentie | Computerautonomie verbinden met Eva's huis, relaties, initiatief, lange doelen en bewezen eindtoestand |
| **Oude Eva** | Voelt al aanwezig door spontaan spreken via deurbel en speakers | Die beleving moet behouden blijven | Dezelfde aanwezigheid combineren met beter geheugen, privacy, herstel en controleerbare acties |

## Wat Eva wel en niet hergebruikt

Eva mag bestaande techniek gebruiken wanneer dat sneller en beter is:

- MCP-tools en `SKILL.md` als standaard aansluitingen;
- OpenClaw Gateway of nodes voor apparaten en kanalen;
- een geïsoleerde browsercomputer van OpenBot, Agent Zero of een andere
  geschikte provider;
- Hindsight als vast langetermijngeheugen;
- eventueel Mnemosyne voor een snelle lokale geheugenlaag en Honcho voor een
  beter persoonsmodel, maar alleen wanneer metingen echte winst tonen.

Wat Eva niet uitbesteedt: haar naam en karakter, wie iemand is, wat privé is,
welke toestemming geldt, welke taak loopt, of iets echt gelukt is en wat zij aan
de gebruiker antwoordt.

## Wanneer we eerlijk mogen zeggen dat Eva beter is

We mogen niet zeggen “Eva is beter” omdat haar ontwerp uitgebreider klinkt. De
claim mag pas na deze vier soorten bewijs:

1. **Dezelfde openbare tests:** AgentBench, GAIA en OSWorld met de officiële
   scorer, dezelfde release en heropenbare resultaten.
2. **Dezelfde concurrentietaken:** Eva, OpenClaw, Hermes, Agent Zero en OpenBot
   krijgen hetzelfde model, budget, gereedschap en dezelfde taak.
3. **Echt dagelijks gebruik:** zeven dagen met echte gesprekken, huisgebeurtenissen,
   voice, computerwerk, stiltemomenten, crashes en herstel.
4. **Jouw oordeel:** jij moet Eva natuurlijk, snel, behulpzaam en herkenbaar
   vinden. Een technische groene vink kan dat niet vervangen.

Tot die tijd is de eerlijke formulering:

> Eva 2 heeft een duidelijke route om voor deze persoonlijke huisassistent beter
> te worden dan de concurrenten, maar het complete voordeel is nog niet bewezen.

## Waar de technische details staan

Deze pagina blijft bewust leesbaar. De controleerbare details zijn niet
verdwenen:

- volledige gates, checkpoints en wat nog niet bewezen is:
  `TECHNISCHE_PRODUCTGATES.md`;
- wat vandaag echt werkt en wat ontbreekt: `EVA2_MOGELIJKHEDEN_EN_GATEN.md`;
- oude Eva tegenover Eva 2: `OUDE_EVA_VS_EVA2.md`;
- technische architectuur: `design/architecture/living-core-v1.md`;
- benchmarkregels: `design/product/comparable-agent-benchmarks-v1.md`;
- actuele voortgang: `PROJECT_STATUS.md`;
- onafhankelijke beoordelingen en echte bewijzen: `reviews/` en
  `evidence/living-core/`.
