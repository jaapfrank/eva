# Architectural Review — Eva

Status: blueprint ter goedkeuring door Jaap, 29 oktober 2026. Geen productcode, schema's, configuratie of installatie gewijzigd; geen tests of modelproeven uitgevoerd voor deze review. Alleen dit ontwerpdocument is toegevoegd. De oorspronkelijke eindvoorwaarden en het huidige bewijs blijven behouden. De directe eigenaaropdracht bevriest verdere implementatie tot goedkeuring van dit ontwerp.

## Besluitvoorstel

Gebruik één Eva-kern met de bestaande bronregistratie, persoonsgrenzen, duurzame uitvoering en terugmelding. Neem SillyTavern op als beoogde gespreksinterface, onder voorbehoud van een beperkte integratieproef na goedkeuring. Behoud LiveKit voor de bestaande spraakroute. Onderzoek een blijvend eigen Eva-model als afzonderlijke ontwikkelrichting. Voeg geen nieuwe geheugenbron, planner, permanente daemon, algemene stemmingdatabase of tweede spraakmodel toe zonder aantoonbaar ontbrekende verantwoordelijkheid.

De huidige grootste ontwerpfrictie is de grens tussen een gebeurtenis of modelvoornemen en een duurzaam toegelaten, controleerbare uitvoering. Een fraaie persoonlijkheid, een lokaal model of een sneller eerste geluid herstelt die overdracht niet vanzelf. De maatregelen hieronder zijn ontwerpvoorstellen; bronlezing bewijst niet dat iedere route in de actuele installatie fout is of werkt.

## Onderzochte basis

- Canonieke kaartcriteria en vervolgstappen in `development/design/product/execution-workboard-v1.json`, met name E1.6, E1.7, E1.8, E4.16, C3.1/C3.4/C3.5/C3.6 en C4.1.
- `application/sensory.py`: bron, bron-event-id, eventtype, tijd, payload en bronconfiguratiebinding.
- `application/resident_attention.py`, `adapters/postgres/initiative_attention.py`: bestaande NextAttention, oorsprong/hash, selection_sequence, checkpoint en controle op stop van de oorspronkelijke intentie.
- `application/free_will.py`: broncontext, feedback, expressiegeldigheid en vervolgaandacht.
- `adapters/postgres/operational_timeline.py`: append-only events met event/operation/thread-identiteit, payloadhash, volgnummer; checkpoint met phase/status/revision.
- `contracts/voice.py`, `application/voice_context.py`, `voice_service/agent.py` en `server.py`: sessie en beurt, antwoordgeneratie, transcriptbinding, BargeIn, afspeelafhandeling, korte gesproken tussenreactie en afzonderlijke taakstop. Een deel van deze bestanden bevat al bestaand ongecommit werk; aanwezigheid in de checkout is geen geïnstalleerd bewijs.
- `application/self_repair.py`, `operations/runtime_repair_target.py`, `bootstrap/runtime_repair_work.py` en `adapters/postgres/self_repair.py`: bestaand foutdoel, bronhashes, beschermde brongebieden, doel/releasebinding, ontwikkel-/beoordelingsscheiding en actuele stop-/mandaatcontrole.
- Eerdere document- en Telegrambevindingen uit de voorafgaande beoordeling; geen nieuwe gebeurtenis of praktijkuitkomst gecreëerd.

Veel state bestaat al in dataclasses, contracten en PostgreSQL-events. PydanticAI verzorgt modelinteractie; het is geen zelfstandig ego of duurzame database. De onderstaande velden zijn vereiste betekenis, niet automatisch nieuwe kolommen. Breng eerst hun huidige opslag en gebruik in kaart. Voeg alleen het werkelijk ontbrekende gegeven toe aan de bestaande eigenaar.

## 1. Initiatief — E1.6, E1.7, E1.8

### Blokkerende paradox

Eva mag zelf betekenis en vervolg kiezen, terwijl herhaalde levering, gewijzigde bevoegdheid, vergeten of stoppen nooit opnieuw een onbedoelde handeling mogen veroorzaken. Callback ontvangen, modelbesluit nemen en een effect uitvoeren zijn verschillende gebeurtenissen. Een connector kan dezelfde bron meermaals leveren; een intern operation_id dedupliceert dan niet noodzakelijk verschillende IDs voor hetzelfde externe effect. Bij onbekende externe uitkomst kan niet tegelijk automatische herhaling en gegarandeerd geen dubbel effect worden beloofd zonder doelondersteuning voor idempotentie of betrouwbare teruglezing.

Daarnaast vragen E1.7/E1.8 echte latere dagen, tegenbewijs en gewijzigd gedrag. Die ontbrekende gebruiksdekking kan programmeren of een herhaalde technische proef niet invullen. Dit is een ander soort open werk dan een kapotte callback. De bestaande NextAttention- en stoplogica mag niet opnieuw worden gebouwd.

### Minimale state en opslag

| Betekenis | Al aangetroffen | Alleen toevoegen indien ontbrekend |
| --- | --- | --- |
| Herkomst en persoon | `source`, `source_event_id`, `occurred_at`, payloadhash; tenant/installatie en persoons-/audiencebinding in omliggende registratie | `source_revision` wanneer de provider werkelijk een revisie levert; anders een inhoudshash. Geen verzonnen providerrevisie. |
| Huidige beslissing | decision thread/sequence/hash; `source_context_sha256`; `next_attention`; `selection_sequence` | Geen tweede beslissingsobject. Bind effect aan exacte beslissing en actuele bronversie. |
| Stop, pauze en bevoegdheid | Stopmarkers, checkpoints/revision en actuele mandaatcontrole | Een duurzaam `paused`-gegeven bij de bestaande verantwoordelijkheid indien pauze niet afgedekt is. Bij hervatten oorspronkelijke stop respecteren; nieuwe expliciete toelating moet herleidbaar zijn. |
| Dubbel effect en onzekerheid | Operationele identiteit, journal/checkpoint en werkstatus bestaan | Indien niet afgedekt: `effect_key`, `effect_status` (voorbereid/verzonden/bevestigd/onzeker), `verification_ref`. Sleutel gebaseerd op dezelfde bedoelde handeling, doel en bronversie, niet alleen op transportpoging. Gebruik bestaande effect-/receiptregistratie. |
| Routine en voorspelling | Bestaande persoons-/hypothese- en feedbackroute behouden | Waar nodig `evidence_refs`, `counterevidence_refs`, `hypothesis_revision`, `expected_outcome`, `observed_outcome_ref`; veranderde interpretatie blijft van oorspronkelijk bronfeit gescheiden. Geen nieuwe patroonstore. |

Het ontbreken van bovenstaande aanvullende velden is nog niet bewezen. Dit zijn controlepunten voor de ontwerpuitwerking, geen migratieopdracht.

### Drie opeenvolgende stappen na goedkeuring

1. **State en simulatieontwerp:** leg één bestaande providerroute en de huidige overgangsregistratie vast. Definieer toegestane overgangen bij dubbel ontvangen, reconnect, bronwijziging, stoppen en onbekende uitkomst. Gebruik later een geïsoleerde providersimulator voor technische randgevallen; zo'n simulator telt niet als echt initiatief of meerdaags bewijs.
2. **Beslis- en uitvoerlogica:** laat bestaande adoptie/journal maar één logisch effect toelaten en opnieuw actuele bron, stop en mandaat toetsen vlak voor dispatch. Bij twijfel eerst verzoenen met de externe toestand; als dat niet mogelijk is, zichtbaar blokkeren met één concrete vervolgstap. Werkelijke uitvoering en statusprojectie krijgen elk hun bestaande eigenaar.
3. **Gewone koppeling:** volg één echt doel met toegestane bronkeuze en één echte bronverandering naar vervolg of passende stilte. E1.6 pas sluiten op oorspronkelijke criteria. E1.7/E1.8 volgen daarna echte dagen, uitzondering/correctie en aantoonbaar latere toepassing. Niet iedere stilte of herhaalde kloktik wordt een nieuwe betaalde proef.

## 2. Spraak — C3/C4

### Blokkerende paradox

Vroeg reageren vraagt vooruitlopen op het einde van de zin; correcte beurtgrenzen vragen wachten op voldoende zekerheid. Een te vroege start kan beginwoorden verliezen, een verkeerd doel uitvoeren of een geannuleerd antwoord toch laten horen. Een te late start veroorzaakt stilte. De huidige bron bevat al VAD, interruptie-instellingen, sessiegebonden dispatch en een modelgestuurde korte tussenreactie (`speak_and_continue`). Een extra klein model voor vaste fillers introduceert extra generatie, GPU-concurrentie en annulering; het verwijdert de werkelijke wachttijd niet.

Spraak onderbreken is niet hetzelfde als een reeds aangenomen taak stoppen. De bestaande contracten onderscheiden die twee terecht. Playback aangeboden is bovendien geen bewijs van gehoord, en een herverbinding is geen nieuwe gebruikersopdracht. Niet iedere waargenomen vertraging komt uit LiveKit: einde-beurt, STT, context/geheugen, model, tools, TTS en speakerbuffer moeten worden onderscheiden.

### Minimale state en opslag

| Betekenis | Al aangetroffen | Ontwerpkeuze |
| --- | --- | --- |
| Sessie en definitieve beurt | `session_id`, `voice_turn_id`, `conversation_message_id`, `final_transcript_sha256`, `finalized_at`, `reply_generation` in VoiceTurn; BridgeState heeft sessie en afgeleide message-id | Eenduidig verbinden met de werkelijk gebruikte LiveKit-route; niet aannemen dat aanwezigheid van het private contract dezelfde dekking in iedere speakerroute bewijst. |
| Annulering | BargeIn heeft onderbroken beurt en `cancel_generation`; afspelen/antwoordgeneratie gescheiden van effecten | Alleen actuele generatie mag audio afleveren. Een expliciete taakstop gebruikt bestaande duurzame stop. Geen oude taak opnieuw toelaten na reconnect. |
| Gesproken deel en terugmelding | Delivery handles, onderbrekingsstatus, receipts en werkresultaatbinding aanwezig | Alleen indien ontbrekend een stabiele `utterance_id` of `(message_id, generation, segment_index)` doorgeven aan iedere audio-overdracht. Eén eigenaar bepaalt wat opnieuw aangeboden mag worden. |
| Wachttijd | Niet alle stadiumtijden in deze review vastgesteld | Aan bestaande trace koppelen: einde gebruikersspraak, definitief transcript, modelstart/eerste inhoud, eerste TTS-audio en start speakerafspelen. Monotone klok voor lokale duur; hostovergangen niet met onvergelijkbare klokken aftrekken. Leg begrippen en ontbrekende metingen vast. |
| Huidige sessie | Bestaande open/close/renew en sessieclaims | Na sluiten/muten geen ongevraagde heropening. Controleer huidige sessie vóór oude audiodelivery. |

### Drie opeenvolgende stappen na goedkeuring

1. **State en simulatieontwerp:** één tijdlijn vastleggen voor zachte aanloop, aarzeling, korte vervolgzin, echte interruptie, achtergrondgeluid en reconnect. Simuleer audio-/sessioncallbacks zonder huisacties of nieuwe betaalde modelrondes. Definieer waar een beurt definitief wordt en wie een generatie ongeldig maakt.
2. **Logica en lokale keten:** koppel definitieve beurt en generatie aan de bestaande dispatch en playback. Lokaliseer eerst het dominante tijdsegment. Beoordeel alleen daarvoor aanpassing aan eindpuntdetectie, streaming, contextvoorbereiding of bestaande tussenreactie. Voer geen actie uit op alleen een speculatief transcript. Geen automatisch tweede model voor fillers.
3. **Fysieke koppeling:** installeer uitsluitend de beoordeelde wijziging via de bestaande spraakeigenaar en toets één gerichte route op beide speakers. Tussenvraag, onderbreking en terugmelding blijven gekoppeld aan hun werkelijke beurt. Sneller eerste geluid en sneller inhoudelijk antwoord afzonderlijk rapporteren; fysieke kwaliteit laat de eigenaar beoordelen.

LiveKit biedt upstream mogelijkheden voor beurtgrenzen, interrupties en speculatieve generatie. De actuele documentatie betreft niet automatisch Eva's geïnstalleerde versie. Sommige mogelijkheden vragen cloudinferentie of extra kosten. Geen nieuwe betaalde route of upgrade zonder onderbouwde keuze.

## 3. Zelfherstel — E4.16

### Blokkerende paradox

Een fout kan de uitvoeringsroute treffen waarmee Eva die fout moet onderzoeken en repareren. Tegelijk mag de kandidaat zijn eigen beoordeling, STOP of installatiebevoegdheid niet aanpassen. De geraakte bron kan bovendien inmiddels veranderd zijn. De broncode bevat hiervoor al een hostgestuurd target, bron-/imagehashes, beschermde gebieden en afzonderlijke beoordeling. De recente ontbrekende kandidaat-/werkruimtebinding is een concrete overdrachtsvraag, geen bewijs dat nog een herstelplatform nodig is.

Een beoordeelde kandidaat is niet geïnstalleerd, en een gezond proces bewijst niet dat het oorspronkelijke probleem is opgelost. Zeker bij een onbekende eerdere externe uitkomst mag de herstelproef de oorspronkelijke huisactie niet blind opnieuw uitvoeren. Ook een technische deelreparatie sluit de volledige autonome keten en later hergebruik niet.

### Minimale state en opslag

| Betekenis | Al aangetroffen | Alleen ontbrekende verbinding toevoegen |
| --- | --- | --- |
| Incident en bevoegdheid | `incident_id`, incident-eventhash, `scope_sha256`, `mandate_sha256`, bronhash, oorspronkelijke intentie en framehashes | Geen tweede incidentenstore. Zelfde oorzaak/release bundelt bewijs bij huidig doel; een gewijzigde release is expliciet herkenbaar. |
| Doel en kandidaat | `target_id`, core/worker images en configuratiehashes, source-map, kandidaat- en vergelijkingshash | `workspace_ref`/handoff-ref duurzaam aan exact work/target/candidate binden als die overdracht werkelijk ontbreekt. Een bestandspad alleen bewijst bestaan, integriteit of toegang niet. |
| Fase en uitkomst | Bestaande phase/status/revision, beoordeling, kandidaat, installatie en broncontrole | Ontbrekende `review_ref`, `application_ref`, `behavior_verification_ref` koppelen aan bestaande receipts. Geen modeltekst als statusbewijs gebruiken. |
| Fout of onzeker effect | Journal, oorspronkelijke fout en stopfences bestaan | Indien niet afgedekt: gestructureerde `blocked_reason`, `next_action`, `effect_outcome` en verwijzing naar de laatste controle. Geen automatische herindiening bij onbekende submit-/installatie-uitkomst. |

### Drie opeenvolgende stappen na goedkeuring

1. **State en simulatieontwerp:** maak een overdrachtsdiagram van één bestaand incident naar target, werkruimte, kandidaat, onafhankelijke review en toepassing. Controleer alleen bestaan/binding/toegankelijkheid met bewaarde gegevens. Simuleer gewijzigde bron, ontbrekend bestand, dubbel submit, ingetrokken mandaat en onzekere toepassing. Dit sluit de volledige live kaart niet.
2. **Faselogica:** repareer de aangetoonde ontbrekende overdracht bij de bestaande eigenaar. Kandidaat-submit beëindigt de bouwfase; review en toepassing zijn afzonderlijke fasen. Iedere overgang controleert actuele stop, bronnen en release. Een geblokkeerde toestand krijgt een concrete eigenaar en volgende stap, geen nieuwe kringloop.
3. **Werkelijke koppeling:** laat één veilig gebonden echt herstel door de bestaande uitvoerder en onafhankelijke beoordelaar lopen; pas na actuele claim-/profielcontrole installeren en het oorspronkelijke gedrag controleren. Geen onzekere oude taak opnieuw starten. Een latere afzonderlijke fout en werkelijk hergebruik blijven oorspronkelijke eindvoorwaarden.

## 4. Persoonlijkheid, gevoel en interface

### SillyTavern

Opnemen als ontwerpkeuze voor de gespreksinterface, nog niet als bewezen vervanger van alle bediening. Eén smalle brug brengt de actuele gebruiker/gespreksidentiteit naar Eva en levert streaming en achtergrondresultaten terug. Eva blijft eigenaar van de werkelijke bronfeiten, rechten, geheugen, taken en effecten. Een character card presenteert de bestaande identiteit; zij geeft geen rechten en creëert geen tweede autobiografie. Regenereren/bewerken van een chatantwoord mag geen eerder effect herhalen of het operationele verleden overschrijven. SillyTavern Data Bank en World Info pas aansluiten als opslag, rechten, wijzigen/wissen en herkomst aansluiten op Eva's geheugen. De eigen beheeromgeving blijft voor werkzaamheden die nog geen equivalente ST-bediening hebben.

### Stemming en energie

Voorwaardelijk bruikbaar als afgeleide gespreksstijl/Inner State, niet als willekeurig dalende energie na elke taak. Werkbelasting is iets anders dan vermoeidheid. Gebruik bestaande activiteit/taakstatus, gebruikersfeedback en voorkeuren als herleidbare basis. Eventueel afgeleid `interaction_mode`, `focus_ref`, `workload_band`, `basis_refs`, `updated_at`, `expires_at`, `style_revision`, met onbekend als geldige uitkomst. Geen migratie voorgesteld voordat de bestaande Inner State/Personality Delta-dekking is vastgesteld. Simulatie van stemming moet als zodanig begrijpelijk blijven en mag werkelijk werk of toegang niet blokkeren op een verzonnen menselijke behoefte.

### Emotionele betekenis van herinneringen

Behoud het oorspronkelijke feit letterlijk en brongebonden. Een voorlopige interpretatie van betekenis of zorg is een afzonderlijke herzienbare annotatie met `subject`, `interpretation`, `evidence_refs`, `uncertainty`, revisie en wisbinding. Maak van 'Jaap werkte laat' niet zonder onderbouwing een vermeende herinnering dat Eva zich zorgen maakte. Gebruik de bestaande mensbegrip-/feedbackroute; geen automatische emotionele classificatie van ieder bericht. Privé-informatie gaat niet vanzelf naar een gedeelde character card of een trainingsbestand.

### Innerlijke spanning

Ontwerp herkenbare voorkeuren en afwegingen, bijvoorbeeld zorgvuldig zijn én iemand ruimte geven. Vermijd willekeurige conflicterende systeeminstructies of verzonnen angst als bewijs van bewustzijn. Het eigen oordeel volgt echte ervaringen en de bestaande personality-delta-route. PydanticAI is de technische uitvoerder van modelinteractie, geen ego op zichzelf.

### Prompts, fine-tuning en RAG

Dit zijn verschillende hulpmiddelen, geen verplichte opeenvolging. Een character card geeft context; zij verandert modelgewichten niet. Fine-tuning kan gedrag beïnvloeden maar maakt het niet permanent of onveranderlijk en garandeert geen geheugen. RAG zoekt relevante toegestane bronnen; het vereist niet dat alles wordt opgeslagen of dat alle bronnen in één vectorstore gaan. Persoonlijke inhoud bewaren, corrigeren en vergeten blijft bij de bestaande persoonsgrenzen. Een eigen Eva-model en Ollama/LM Studio/Unsloth/Axolotl blijven aparte te beoordelen opties; geen modelinstallatie, trainingsset, trainingsschema of automatische nachttaak onderdeel van deze goedkeuring.

## 5. Voorkomen van herhaalde rondes

Een absolute belofte dat er nooit een nieuwe fout of hertest komt is onjuist. Wel is te voorkomen dat dezelfde ongewijzigde proef zichzelf blijft starten. Vervolg is alleen gerechtvaardigd door gewijzigde oorzaak, nieuwe hypothese of aantoonbaar ontbrekend bewijs; vermeld vooraf wat die wijziging is. Een mislukking leidt tot diagnose of zichtbare blokkade. Een onzekere externe uitkomst leidt eerst tot verzoening. Een geslaagde beperkte uitkomst wordt niet opnieuw geopend voor nieuw toegevoegde criteria.

Na goedkeuring per hoofdfase één concrete uitvoeringseigenaar en afzonderlijke bestandsverantwoordelijkheid, met onafhankelijke review op de geleverde verandering. Geen nieuwe chats of subagents op basis van dit ontwerp. Houd technische voltooiing, geïnstalleerd gebruik, echte lateredagencriteria en eigenaaracceptatie gescheiden. Vermeld tijd/verbruik waar gemeten; onbekend blijft onbekend. Wachten op echte dagen blokkeert een andere afgesproken onafhankelijke stap niet. Het eerdere 'eerst documenten' wordt niet automatisch doorgezet tijdens deze formele ontwerpstop.

## Goedkeuringsbesluit

Dit document vraagt goedkeuring voor de beschreven architectuurgrenzen en gefaseerde uitvoering. Het is geen toestemming voor een nieuw commercieel account, een extra model of trainingsrun, nieuwe gevoelens als feiten, een databaseherbouw of brede productvervanging. Na goedkeuring wordt het bestaande werkbord aangepast op de gekozen eerste fase; oorspronkelijke criteria en historische uitkomsten blijven bewaard. Tot dat moment geen productcode, tests of installatie.

## Primaire bronnen

- [Eva's canonieke werkbord](../design/product/execution-workboard-v1.json) en de hierboven genoemde huidige bronbestanden.
- [SillyTavern providerintegratie](https://docs.sillytavern.app/for-contributors/provider-integrations/).
- [SillyTavern Data Bank](https://docs.sillytavern.app/usage/core-concepts/data-bank/).
- [LiveKit beurtgrenzen en interrupties](https://docs.livekit.io/agents/logic/turns/tuning/).
- [LiveKit tijdmetingen](https://docs.livekit.io/reference/agents/session-traces/).
