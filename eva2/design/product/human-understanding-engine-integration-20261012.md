# Human Understanding Engine beoordeling en integratie

Eva moet niet alleen informatie bewaren, maar verwachtingen vormen, uitkomsten herkennen en haar hulp daardoor aanpassen. Dat is de sterkste bijdrage van dit ontwerp. De zes vermogens passen binnen de vier afgesproken huisgenootuitkomsten. Het zijn geen zes nieuwe hoofdproducten of een tweede brein.

## Mijn oordeel

Ik neem de kringloop over: waarneming → voorlopige interpretatie → verwachting → handelen of wachten → echte uitkomst en feedback → bijstellen → later anders handelen. De expliciete verwachting-uitkomstverbinding is inmiddels gebouwd en geïnstalleerd. HUE1.1 en HUE3.1 zijn onafhankelijk afgerond: een nieuw gesprek gebruikt de met tegeninformatie herziene afweging. HUE2.1 en HUE4.1 zijn nu ook afgerond en geïnstalleerd: een ander onderwerp gebruikt meerdere eerdere ervaringen inclusief tegenvoorbeeld en feedback over gewenste hulp. Opslag alleen was daarvoor onvoldoende.

Het ontwerp is een goede functionele richting, maar geen direct toepasbare implementatiebeschrijving van deze repository. Brainstem, ANIMA, SOUL.md en de vier voorbeeldvelden beschrijven voorgestelde verantwoordelijkheden. Hun namen bewijzen geen aanwezige interfaces. Het aangeleverde document eindigt bij de inleiding van zes bouwfasen; de concrete afrondingsvolgorde staat hieronder.

## Bestaande onderdelen en echte grenzen

| Vermogen | Hergebruiken | Nog af te maken |
| --- | --- | --- |
| Persoonsmodel | Getypeerde bron, tijd, privacy en model_inference_unconfirmed; correctie, vervanging en intrekking | HUE1.1 afgerond: tegenbewijs herziet voorlopige interpretatie en volgend relevant antwoord |
| Patronen | Hindsight-synthese, persoonsobservaties en tijdlijn | HUE2.1 afgerond: later gebruik van meerdere ervaringen met herkenbare bronnen en zichtbaar tegenvoorbeeld |
| Verwachtingen | Bestaand persoonsgeheugen en rustige reflectie | HUE3.1 afgerond: verwachting gekoppeld aan echte gemelde uitkomst en volgende afweging |
| Sociale context | Stijlspiegel en bestaande gewone feedbackroute | HUE4.1 afgerond: gewenste hulp bij ander onderwerp toegepast; behoefte blijft een vraag |
| Initiatief | Initiatiefgeschiedenis, algemene werkuitvoering en privéresultaatroute | Authentieke voltooiing, ontvangerreactie en daarop aangepast later initiatief samen aantonen |
| Leren en droommodus | Bestaande codevoorstellen, varianten, geïsoleerde beoordeling en toepassing | Agentaansturing en code of uitbreiding door de hele kringloop laten gaan, inclusief later gebruik |

Bron en bewijs blijven apart. Het geheugencontract ondersteunt voorlopige inferenties; dat bewijst geen gekalibreerde kansschatting. De feedbackproef bewijst één latere technische toepassing, geen algemene menskennis. De geïnstalleerde initiatiefbridge bewijst geen nieuwe zelfgekozen bijdrage.

Bronverwijzingen:
- Persoonsfeiten en correcties: [geheugencontract](../../src/eva/contracts/memory.py).
- Patroonsynthese: [Hindsight-adapter](../../src/eva/adapters/projection/hindsight.py).
- Werkelijke gesprekcontext: [gespreksmodel](../../src/eva/adapters/model/pydantic_conversation.py).
- Feedback: [beperkte latere toepassing](../../evidence/product/learning-feedback-source-native-result-20261011.json).
- Initiatief: [geschiedenis](../../src/eva/adapters/postgres/sensory_events.py), [installatie en grenzen](../../evidence/product/native-initiative-digital-installed-20261011.json).
- Volledige verbetercyclus: [behouden diagnose](../../evidence/product/native-work-completion-cause-diagnosis-20261012.json).

## Technische keuzes

De bestaande PostgreSQL/Hindsight-opslag en Sol/none voor eenvoudig contact, Astra minimaal medium voor inhoudelijk werk blijven het vertrekpunt. SQLite en lokale Qwen-modellen zijn voorstellen in de aangeleverde tekst, geen reden om een tweede opslag of modelroute te bouwen. Een latere vervanging vereist een concreet voordeel en gegevensbehoud.

Code bewaart herkomst, tijd, privébereik, voorlopigheid, uitkomsten en tegenbewijs. Het model geeft betekenis en kiest hulp. Vermoedens over emoties worden geen diagnoses of feiten. Een schijnprecies getal als 0,68 is niet vanzelf een gemeten betrouwbaarheid.

Relevante gebeurtenissen activeren bestaande verwerking; ongewijzigde toestand vereist geen modelaanvraag. Rustige reflectie gebruikt dezelfde achtergrondroute en wijkt voor gesprek en stoppen. HA, camera's en agenda zijn optionele bronnen die Eva op behoefte kan aansluiten; de voorbeeldsituaties worden geen hardgeprogrammeerde functies. De genoemde cooldowns en externe actiebudgetten worden afgestemd op werkelijk bestaande afspraken, niet als nieuwe algemene beperkingen ingevoerd.

De voorgestelde 90% geheugen, betere Brier-score en 70% initiatiefnut zijn ontwikkeldoelen. Eén geslaagde episode bewijst zulke percentages niet. Echte gezinsfeedback blijft een menselijke beoordeling. Dit ontwerp start geen nieuwe testmatrix of concurrentenserie.

## Zes zichtbare afrondingen

| Taak | Eindresultaat |
| --- | --- |
| HUE1.1 | Tegenbewijs verandert een persoonsbeeld en een later antwoord |
| HUE2.1 | Een onderbouwd patroon wordt later relevant gebruikt |
| HUE3.1 | Een verwachting krijgt een echte uitkomst die de volgende afweging verandert |
| HUE4.1 | Sociale feedback verandert een nieuw gesprek zonder alles opnieuw uit te leggen |
| HUE5.1 | De reactie op echt initiatief verandert later initiatief of stil blijven |
| HUE6.1 | Rustige reflectie en AIDE² leveren alleen bewezen toegepaste verbetering met later gebruik |

Eerst de lopende AIDE²-aansluiting afmaken. Daarna HUE1.1 en HUE3.1 als één menselijke leercyclus verbinden; HUE2.1 en HUE4.1 gebruiken die kringloop voor patroon en gesprek. HUE5.1 sluit initiatief erop aan. HUE6.1 gebruikt gedurende deze stappen dezelfde bestaande verbeterroute; geen parallel platform.

Iedere taak vraagt eerst hergebruik van aanwezige bron en bewijs, daarna alleen de ontbrekende verbinding. De oorspronkelijke 39 van 48 kleine afrondingen blijven exact behouden. Het uitgebreide werkbord bevat 54 kleine taken, waarvan 39 afgerond; dit is geen percentage van productkwaliteit. M3.3, geheugenacceptaties en L3.4–L3.7 worden niet heropend. Het volledige projectgoal blijft actief. Spraak blijft geparkeerd.

## Actuele afronding 13 oktober — later gebruik van de geïnstalleerde leerroute

De [getypeerde leerroute](../../src/eva/application/human_understanding.py), [gewone gespreksactie](../../src/eva/providers/human_understanding_actions.py), brongebonden recall en verwachtingrevisies zijn gebouwd en geïnstalleerd. Zie [installatie van de levenscyclus](../../evidence/product/hue-expectation-lifecycle-installed-20261012.json). Er wordt geen tweede geheugen of nieuwe HUE-implementatie gebouwd.

De vorige gewone episode heeft een persoonscorrectie opgeslagen, maar stopte vóór de derde, nieuwe conversatie. Een terminale verwachtingrevisie zonder nieuw controlemoment werd onterecht geweigerd; die oorzaak is geïnstalleerd opgelost. De afzonderlijke meetroute controleerde compleet verbruik voordat reeds gestarte geheugenhelpers konden afronden. Alleen die afwikkeling wordt voor een nieuwe, eigen geïsoleerde episode hergebruikt en gecorrigeerd. Oude gesloten proefbestanden, fouten en UNKNOWN blijven ongewijzigd.

HUE1.1 en HUE3.1 blijven open tot een later gewoon gesprek met lege recente geschiedenis de herziene persoonsinterpretatie en echte verwachtinguitkomst gebruikt. De oorspronkelijke bronnen, onzekerheid, verwijzingen en persoonsscope moeten terug te lezen zijn. Eén noodzakelijke episode gebruikt de oorspronkelijke criteria: drie beurten, maximaal 24 fysieke aanvragen inclusief helpers, 12 tools en 150.000 geregistreerde tokens; alle inhoudelijke aanvragen Astra/medium. Creditkosten blijven onbekend.

De algemene taakafronding is daarnaast geïnstalleerd en door Eva gebruikt om haar eigen onderbouwde voorstel af te ronden. Die afzonderlijke uitkomst bewijst niet de menselijke leercyclus of volledige AIDE². Het volledige projectgoal blijft actief; er is nu geen handeling van Jaap nodig.

## Oplevering 13 oktober — correctie én later veranderd gedrag

HUE1.1 en HUE3.1 zijn afgerond op de oorspronkelijke criteria. Eva bewaart beide voorlopige records, herziet ze met echte gemelde tegeninformatie en uitkomst, en past in een nieuw gesprek haar advies aan zonder recente gesprekshistorie. [Resultaat](../../evidence/product/hue-learning-later-use-result-20261013.json) en [onafhankelijke beoordeling](../../reviews/hue-learning-later-use-review-20261013.json) leggen scope en bewijs vast. De code was al geïnstalleerd; de proef gebruikte die exacte bron.

16 volledige aanvragen inclusief negen geheugenhelpers, 58.874 tokens en vier acties. Oude UNKNOWN en de afzonderlijke ontbrekende helper-afsluitACK blijven zichtbaar. Een synthetische private episode vervangt geen gezinsacceptatie of algemene privacygarantie. Nu 41/54 kleine taken afgerond; oorspronkelijke 39/48 behouden. Volgende bestaande uitkomst: HUE2.1/HUE4.1 voor patroon en sociale feedback, via dezelfde leerroute. Het volledige goal en AIDE² blijven open.

## Oplevering 13 oktober: patronen en sociale feedback

HUE2.1 en HUE4.1 zijn volgens hun oorspronkelijke criteria onafhankelijk PASS en geïnstalleerd. De bestaande route houdt actuele herkomst en tegenbewijs bij. Eén nieuw gesprek gebruikt drie eerdere gebeurtenissen en communicatiefeedback; 15 volledige aanvragen/56.164 tokens/2 acties. Dit is begrensd later gedrag, geen gezinsacceptatie, volledige AIDE² of algemene voorsprong.

- [Resultaat en meetgegevens](../../evidence/product/hue-pattern-social-later-use-installed-20261013.json).
- [Onafhankelijke beoordeling](../../reviews/hue-pattern-social-later-use-installed-review-20261013.json).

HUE5.1 blijft open voor een echte reactie op bestaande eigen bijdrage en later aangepast initiatief. De technische feedbackroute bestaat al; een geforceerde nieuwe bijdrage of tweede feedbackplatform is niet nodig.

HUE5.1 registratiecontrole:7zelfgekozen initiatieven,3afgeronde eigen bijdragen,0gekoppelde ontvangerreacties. De route is aanwezig; echte feedback kan niet worden ingevuld door Codex. HUE6.1 gaat intussen zelfstandig verder, zonder nieuwe vergelijkingsserie.
