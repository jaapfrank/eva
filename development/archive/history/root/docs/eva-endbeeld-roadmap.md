# Eva: technisch eindbeeld en ontwikkelroute

Status: actief technisch kompas en uitvoeringsroadmap — bijgewerkt 2026-08-08

## Einddoel

Eva groeit uit tot één zelfstandige, herkenbare digitale huisgenoot die met het gezin samenleeft, eigen doelen kiest, meerstapswerk hervat, zichzelf uitbreidt en aantoonbaar huishoudelijke en economische waarde creëert. Bewijs- en veiligheidsgrenzen beperken concrete claims en effecten, nooit haar doelkeuze.

De kernlus is blijvend:

`waarnemen → doel kiezen → committeren → plannen → handelen → controleren → leren → hervatten/pivoteren/opschalen → passend terugmelden`

Een kwartierpuls is alleen aandacht. Een doel, project of onderneming blijft bestaan totdat Eva bewust afrondt, pauzeert, verandert of loslaat.

## Architectuurbesluiten

1. Eén Eva is eigenaar van de volledige episode: bedoeling, standpunt, belofte, plan, handelingen, bewijs, herstel en eindreactie.
2. Interne workers, tools en workcells zijn vermogens van Eva, geen persoonlijkheden of zichtbare subsystemen.
3. Validators produceren machineleesbare feiten, onzekerheden en `claim_allowed`; ze schrijven niet Eva's antwoord.
4. Een gebruikersopdracht sluit nooit stil. Bewezen voltooiing wordt eerst `delivery_pending` en sluit pas na een idempotent delivery-receipt.
5. Zelfgekozen, niet-materieel intern werk mag onderbouwd stil sluiten. Merkbare huis-, account- of communicatieacties worden passend gemeld.
6. Geen moppenbank, humanizer, catchphrases of stijlquota. Persoonlijkheid ontstaat uit stabiele waarden, relatiegeschiedenis, eigen keuzes en gedeelde ervaringen.
7. Black-boxtests krijgen provenance en mogen relatiegeheugen, doelen, portfolio, autonomieprioriteit of meldingsbeslissingen niet vervuilen.
8. Een eigen herinnering aan Eva is interne planning: zij zet hetzelfde werk én dezelfde episode op wachten, is geen gezinsbijdrage en maakt geen aflevering verplicht.
9. Een geplande `next_wake_ts` is een echte slaapgrens. Slapend werk is niet runnable, versnelt de planner niet en verschijnt niet als actief werk in een vrije aandachtbeurt.

## Canoniek model

- `eva_goals`: zelfgekozen of gedeelde langetermijndoelen met reden, begunstigde, horizon en succesmaten.
- `eva_projects`: toetsbare uitkomst, strategie, fase, mijlpaal, volgende actie en waarde-/leermaten.
- `eva_goal_episodes`: duurzame eigenaar van oorsprong tot bezorging, inclusief oorspronkelijke persoon en conversatie.
- `eva_episode_origins`: alle gesprekken waarin dezelfde doorlopende klus is begonnen of hervat; één klus hoeft daardoor niet kunstmatig te worden verdubbeld en verliest toch geen herkomst.
- `eva_episode_events`: append-only keuze-, voortgang-, herstel- en deliverygeschiedenis.
- `eva_delivery_receipts`: episode, oorspronkelijke conversation/message, bewijs, resultaat, tijd, transport en idempotency-key.
- Later: milestones/dependencies, work sessions, reviews, capability gaps en venture-ledgers.

## Roadmap

### Fase 1 — identiteit en episode-eigenaarschap

- Compacte, herkenbare karakterkern; operationele regels niet langer dominanter dan Eva.
- Geen tweede narrator/humanizer-run.
- Vaste zakelijke append- en foutzinnen verwijderen; validators blokkeren alleen ongegronde claims.
- Portfolio-, project-, episode- en deliveryschema invoeren.
- Nieuwe duurzame gebruikersopdrachten koppelen aan dezelfde episode en oorspronkelijke conversatie.
- Full-autonomy mag user-origin werk niet stil afronden.
- Testprovenance vanaf de echte HTTP-entrypoints.

Acceptatie: een opdracht die na een fout of restart in de achtergrond afkomt, geeft exact eenmaal een natuurlijk eindbericht aan de oorspronkelijke verzoeker.

### Fase 2 — echte portfolio-executive

- `open[0]` volledig vervangen door expliciete keuze uit urgente signalen, due werk, onderbroken episodes, reviews, kansen en nieuwe eigen doelen.
- Keuze maakt herkomst, relevantie, urgentie, gezinswaarde, nieuwsgierigheid, stagnatie, kosten en recente herhaling zichtbaar.
- Eva mag altijd “geen van deze; ik kies X” beslissen.
- Persistent work session en compacte projectcapsule vóór iedere modelcall.
- Exact hervatten na chat, yield of procesrestart.

Acceptatie: drie aandachtcycli en een tussengesprek blijven op hetzelfde project/episode-id, tenzij Eva een gelogde inhoudelijke switch kiest.

### Fase 3 — evalueren en zichzelf verbeteren

- Hypothese → baseline → kandidaat → live canary → onafhankelijke beoordeling → behouden/rollback.
- Relatiecorrecties semantisch koppelen aan de mislukte reactie, gewenste verandering en latere proef.
- Capability gaps ontstaan alleen uit herhaalde bewezen blokkades.
- Package/add-on/corewijzigingen via provenance, receipt, canary en quarantaine.

Acceptatie: verbetering generaliseert naar onbekende echte gesprekken en kritieke regressies rollen automatisch terug.

### Fase 4 — huishoudelijke waarde en ondernemingen

- Eén bewezen end-to-end waardeproject voordat opschaling begint.
- Per onderneming: probleem, klant, aanbod, kanaal, verplichtingen, kosten, omzet, KPI's, volgende actie en stop-/schaalbesluit.
- Meerdere ventures delen Eva's identiteit en portfolio, maar mengen geen klanten, geldstromen of bewijs.

Acceptatie: twee ventures draaien gelijktijdig met gescheiden ledgers en ieder een gecontroleerde volgende actie; “omzet” telt alleen bij een transactiebron.

## Live beoordelingspoort

Unit-tests bewaken schema, veiligheid en receipts. Werkelijke kwaliteit wordt beoordeeld via onverwachte meerbeurtgesprekken op de productie-entrypoint. Minimaal scoren: uitstraling, chatspraak, situationele humor, coherentie, initiatief, doorpakken, documentatieonderzoek, foutreparatie en langetermijncontinuïteit. Doelgrens: minstens 75/100, zonder kritieke fout op coherentie, herstel of stille gebruikersbeloften.

## Huidige bewezen stand — 2026-08-06

- Full-autonomy blijft doelvrij: Eva mag zelf nieuwe doelen kiezen. De aandachtspuls loopt iedere 15 minuten; een lopende sessie krijgt binnen het dagbudget maximaal twee uur in plaats van vijftien minuten bouwtijd.
- Een echte voice-opdracht las een bestaand README-bestand, herstelde zelf van een verkeerd pad, wijzigde exact het doelbestand, draaide de echte tests met exitcode 0 en antwoordde kort: “Toegevoegd. De test is net gedraaid en geslaagd.”
- Diezelfde beurt heeft één workitem, één required episode, de exacte voice-herkomst, één delivery-receipt en sluit workitem plus episode pas bij bezorging.
- Twee tijdelijke providerfouten worden binnen dezelfde beurt herprobeerd; dit is met geïnjecteerde transportfouten gecontroleerd.
- Synthetische live-evaluaties kunnen geen productie-workitems, episodes, projecten, relatiegeheugen of huisacties maken. Bestandsuitvoer gaat naar een afzonderlijke `test-artifacts`-map en toekomstbeloften zonder duurzame planning worden tegengehouden.
- Productie-aandacht kan `test-artifacts` niet oplijsten, lezen of uitvoeren. Een nieuwe HTTP-voiceproef schreef en las daar wel een bestand, maar liet aantoonbaar nul berichten, doelen, projecten, episodes en herinneringen achter.
- De familie-REST-tijdlijn en live SSE-stroom verbergen synthetische en gequarantaineerde auditregels; de append-only audit blijft intern intact. Een live meting na de reparatie vond nul zichtbare proefregels.
- Een oudere synthetische energieproef bleek vóór deze grens toch echte autonomie te hebben gestuurd. Werk #39, episode #6, herinnering #35 en verkenningen #43/#46 zijn daarom niet weggepoetst maar expliciet in quarantaine gezet. Vier afgeleide bestanden stonden tijdelijk in `test-artifacts`; na beoordeling is die gegenereerde bestandskopie op 8 augustus verwijderd terwijl de gequarantaineerde auditevents behouden bleven.
- Een volledig vrije productiecyclus koos daarna zelf voor een actuele nachtelijke huisvraag. Eva controleerde lampen, deuren, media en aanwezigheid, maar bediende niets omdat de beslissende wifi-locatie ontbrak. Haar eigen conclusie was: “Ik heb het nachtelijke buitenlicht bewust laten branden … zonder actuele wifi-locatie van Jaap ga ik niet opnieuw gokken dat de overkapping leeg is.”
- Crashherstel maakt ook een reeds `open` required/delivery-pending episode onmiddellijk selecteerbaar door wake, wait en oude lease te wissen. Telegram prepared recovery bewaart episode-, work- én message-herkomst.
- Intentieherkenning onderscheidt een meningsvraag van een opdracht. De exacte tegenproef “Ik wil voor elk apparaat een apart dashboard maken. Goed idee toch?” riep na reparatie geen gereedschap aan en kreeg een inhoudelijk Eva-antwoord; “Maak een dashboard …” blijft wel uitvoerwerk.
- De volledige lokale suite stond op dit meetmoment op 180/180 groen. Vaste tests zijn daarbij vangrails; de productieproeven hierboven blijven de inhoudelijke acceptatiebron. De actuele aantallen staan uitsluitend in de runtimekaart en de laatste gate-uitvoer.
- Een zelfstandige productiecyclus koos zonder gebruikersopdracht een economische onderzoeksvraag, zocht actuele softwarebounties, startte parallel onderzoek, schreef en controleerde een besluitstuk en verwierp kandidaten zonder bewezen uitbetaling.
- Een latere vrije productiecyclus koos zelf een klein huishoudelijk doel: Eva onderzocht lampen, deuren en media, legde haar hypothese en stopregel vast, schakelde alleen de aantoonbaar ongebruikte overkappingsspots uit en verifieerde de nieuwe toestand. De losse lamp `Buiten` liet ze bewust ongemoeid omdat het bewijs daarvoor zwakker was.
- De niet-urgente terugmelding van die echte huisactie staat als required episode duurzaam te wachten tot 07:30. Een procesherstart bewaart deze bewuste nachtwacht; pas een werkelijk transportreceipt mag het werk sluiten. Tot die ontvangst is dit dus bewezen handelen, maar nog geen bewezen aflevering.
- De teamreview vond dat Eva tien minuten vóór die actie zelf een strengere aanwezigheidsdrempel had gekozen. Dat conflict is niet weggepoetst: het staat bij de episode als correctie. Recente eigen stopregels worden nu opnieuw in iedere aandachtcontext getoond; een vergelijkbare verkenning kan ze alleen heropenen met het eerdere id én concreet nieuw bewijs.
- Een uitgesteld autonoom bericht bewaart nu de exacte tekst, ontvanger, vaste deduplicatie-identiteit en retryteller in de werkstaat. Nachtwacht en transportbackoff overleven een herstart; de ochtendbeurt verzendt die voorbereide tekst rechtstreeks in plaats van haar opnieuw te bedenken.
- Zelf-oplosbaar bewijsgebrek mag geen permanent `blocked` werk meer opleveren. Het economische werk #8 staat open met een concrete review-wake en andere bewijs-/pivotroute; `blocked` is gereserveerd voor een benoemde externe afhankelijkheid.
- Gesloten episodes reconciliëren hun projectstatus. Bestaande projecten #3–#5 zijn daardoor niet langer ten onrechte `active/discover`, maar aantoonbaar voltooid.
- Eva kan met `doel_bijhouden` een eigen langetermijndoel creëren, bijstellen, pauzeren, hervatten of afronden met reden, horizon en succesmaat. Nieuw werk kan zich aan die zelfgekozen doelsleutel binden. Actieve eigen doelen blijven ook zonder open werk in haar keuzecontext staan en een verstreken `next_review_ts` wordt nu zelfstandig door de aandachtplanner geselecteerd.
- Nieuwe merkbare autonome effecten krijgen vóór de uitvoering al een duurzame bedoeling, stopregel, workitem en episode. Een procescrash tussen huisactie en rapportage verliest daardoor niet langer het oorspronkelijke waarom.
- Een nieuwe aspiratie verdringt geen uitvoerbaar bestaand commitment meer. De portefeuille maakt eerst de al gekozen klus af of zet haar bewust op wachten; vrije aandacht komt daarna terug.
- Een echte autonome codecyclus legde een regressietest vast, voerde haar tweemaal met exitcode 0 uit en las het bestand terug. De eerste implementatie heropende dat reeds afgeronde werk daarna driemaal; dit teamtegenbewijs is behouden. Turnfinalisatie respecteert nu terminale werkstatus, milestone-episodes sluiten direct zonder gezinsdelivery, en werk #41 / episode #8 / project #8 zijn met het sterkste uitvoerbewijs gereconcilieerd naar `done` / `closed` / `completed`.
- Een cameraonderzoek was ten onrechte als heropening van een ongerelateerd sluipverbruikbesluit geboekt. Die live koppeling is expliciet gecorrigeerd. Onderwerpcontinuïteit vereist nu twee betekenisdragende overeenkomsten, terwijl heropening bovendien het exacte eerdere id en passend geverifieerd toolbewijs uit dezelfde beurt vereist.
- De eerste finale herstart bracht nog een ernstiger fout aan het licht: een achtergebleven `source=test`-verkenning werd door echte autonomie geselecteerd en leidde tot read-only huis- en cameracalls. De beurt en vier testverkenningen zijn als contaminatie in quarantaine behouden. Curiosity-tests gebruiken nu een eigen tijdelijke database, en productiecontext plus planner vragen op SQL-niveau uitsluitend `source=self`; ook honderden testregels kunnen een echte rij daardoor niet meer verdringen.
- Terminaliteit is nu op drie lagen monotone state: de werkopslag weigert `done/cancelled → open`, de episodestore weigert `closed/abandoned → actief`, en de portfolioselector slaat iedere open werkregel met een terminale episode over. Een adversarial proef die afgerond werk via `blokkeer` probeerde te heropenen faalt nu gesloten.
- Een doelreview is een duurzame claim van 45 minuten in plaats van telkens dezelfde top-1 peek. Daardoor roteren meerdere due eigen doelen ook over een herstart; als Eva niets wijzigt, komt het doel vanzelf terug. `doel_bijhouden` beheert alleen `source=self` en kan de gedeelde, door het gezin bepaalde hoofdrichtingen niet pauzeren of herschrijven.
- De eerste schone cycli brachten overtuigend tegenbewijs tegen iedere lexicale onderwerpheuristiek: camera, lamp, koeling, ochtendweer en zelfs Perseïden raakten door toevallige woorddelen verkeerd gekoppeld, terwijl `tuinlamp` versus `buitenverlichting` dezelfde veiligheidsdrempel juist kon omzeilen. Ook vrij door het model gekozen semantische labels bleken nog omzeilbaar met synoniemen. De lineage wordt daarom nu door de runtime uit de inhoud afgeleid en duurzaam opgeslagen: canoniek onderwerp, beslisdimensie, locatiecontext, component, gebeurtenis en alleen volledig genoemde of tegen duurzame records gevalideerde IDs. Vrije keys en ankers beslissen niet. Continuïteit en veiligheidsconflict zijn apart: verschillende softwarecomponenten en verschillende meteorenzwermen blijven los, terwijl een bestaande aanwezigheidsgrens voor dezelfde buitenverlichting standaard blijft gelden ongeacht de effectparafrase. Alleen expliciet read-only oorzaakonderzoek mag er zonder nieuw bewijs naast bestaan. Nep-entities en niet-bestaande doelen verlenen geen identiteit. Een toekomstig slapende verkenning kan bovendien in de opslaglaag niet vóór haar wekdatum terug naar `running`. Onbekende inhoud zonder controleerbare identiteit faalt zichtbaar en legacyrecords krijgen deterministisch dezelfde afleiding. De live correcties #120/#121 zijn bijgewerkt en synthetische #43/#46 staan nu werkelijk buiten `source=self`.
- Een latere echte cyclus leverde opnieuw nuttig tegenbewijs: een read-only onderzoek naar de achterdeursensor werd via de te algemene combinatie `household + presence` ten onrechte aan lampbesluit #120 gekoppeld. `presence` kwam zelfs uit zinnen als “sensor aanwezig” en “niemand wekken”. Featureversie 9 scheidt daarom het gewogen hoofdonderwerp van brede veiligheidscontext: `household` is geen zelfstandig onderwerp, deur-/raamcontacten hebben een eigen focusdomein, bewonersaanwezigheid is niet hetzelfde als entity-beschikbaarheid en stopregelboilerplate bepaalt geen lineage. Veiligheid blijft fail-closed wanneer een muterende lampvraag haar locatie weglaat. Gelijke update-tijden krijgen een stabiele `id`-tie-break. De exacte live formulering, omgekeerde woordvolgorde, ontbrekende locatie en voor-/achterdeurscheiding staan als regressies vast.
- Autonome gesprekscontext bevat nu per bericht de echte tijdstempel. Een zes uur oude bewonersmelding over een open deur is historisch bewijs van een mogelijk aggregatieprobleem, geen actuele deurstand en mag niet als “zojuist” worden beschreven; voor een fysieke actie moet Eva de huidige bron opnieuw lezen.
- De safetyvergelijking erft een eerdere huisgrens alleen binnen hetzelfde focus-apparaatdomein uit vraag en hypothese. Een lampbesluit kan daardoor niet langer een airco-, koel- of robotstofzuigerdoel kapen doordat een verwachte waarneming deuren noemt of een stopregel juist zegt “geen licht bedienen”; binnen hetzelfde lampdomein blijft een weggelaten locatie juist onbekend en dus geen veiligheidsomweg.

Nog geen eindclaim: meerdere echte dagen van zelfstandig hervatten, waarde leveren en leren blijven de acceptatiepoort voor fase 2 en 3. Nieuwe prompts veranderen dit kompas niet; ze worden als gesprek, opdracht of correctie binnen dezelfde blijvende richting verwerkt.
