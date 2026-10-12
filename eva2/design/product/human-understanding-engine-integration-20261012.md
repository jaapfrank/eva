# Human Understanding Engine beoordeling en integratie

Eva moet niet alleen informatie bewaren, maar verwachtingen vormen, uitkomsten herkennen en haar hulp daardoor aanpassen. Dat is de sterkste bijdrage van dit ontwerp. De zes vermogens passen binnen de vier afgesproken huisgenootuitkomsten. Het zijn geen zes nieuwe hoofdproducten of een tweede brein.

## Mijn oordeel

Ik neem de kringloop over: waarneming → voorlopige interpretatie → verwachting → handelen of wachten → echte uitkomst en feedback → bijstellen → later anders handelen. Vooral de expliciete verwachting-uitkomstverbinding ontbreekt nog in de gericht onderzochte routes. Zonder die verbinding kan Eva reflecties opslaan zonder aantoonbaar slimmer te worden.

Het ontwerp is een goede functionele richting, maar geen direct toepasbare implementatiebeschrijving van deze repository. Brainstem, ANIMA, SOUL.md en de vier voorbeeldvelden beschrijven voorgestelde verantwoordelijkheden. Hun namen bewijzen geen aanwezige interfaces. Het aangeleverde document eindigt bij de inleiding van zes bouwfasen; de concrete afrondingsvolgorde staat hieronder.

## Bestaande onderdelen en echte grenzen

| Vermogen | Hergebruiken | Nog af te maken |
| --- | --- | --- |
| Persoonsmodel | Getypeerde bron, tijd, privacy en model_inference_unconfirmed; correctie, vervanging en intrekking | Tegenbewijs koppelen en een voorlopige interpretatie bij later gedrag zichtbaar bijstellen |
| Patronen | Hindsight-synthese, persoonsobservaties en tijdlijn | Onderbouwde herhaling met tegenbewijs daadwerkelijk bij een latere situatie gebruiken |
| Verwachtingen | Bestaand persoonsgeheugen en rustige reflectie | Vooraf verwachting vastleggen, later echte uitkomst verbinden en volgende afweging veranderen |
| Sociale context | Stijlspiegel en beperkt bewezen later gebruik van norm en vorm | Feedback over hulp en communicatie bij een ander gesprek toepassen |
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

## Eerstvolgende bouwuitkomst na de gesloten verbeterproef

De gewone vervolgbeoordeling van het bewaarde codevoorstel is gesloten zonder geldige gezamenlijke uitkomst; één modelaanvraag mist complete usage. Er is niets toegepast. Dezelfde proef wordt niet opnieuw gestart. HUE6.1 blijft open.

HUE1.1 en de afhankelijke HUE3.1 vormen nu één gebruikersuitkomst: Eva bewaart een voorlopige persoonsinterpretatie of verwachting, koppelt later echte tegeninformatie of een uitkomst en gebruikt de herziene afweging bij een volgende relevante situatie. Oude bronnen en onzekerheid blijven zichtbaar.

Gerichte bronanalyse bevestigt dat het [geheugencontract](../../src/eva/contracts/memory.py) al herkomst, tijd, voorlopige inferenties en correcties bevat. De [normale gesprekproducer](../../src/eva/bootstrap/runtime.py) bewaart echte gebruikersbronnen; het [Hindsight-geheugen](../../src/eva/adapters/projection/hindsight.py) verwerkt correcties en houdt syntheses onbevestigd. De duurzame getypeerde relatie tussen eerdere interpretatie, afzonderlijke tegenbron of uitkomst en herziene afweging ontbreekt in de onderzochte gewone route.

Bouw alleen die verbinding via bestaande gespreksacties, geheugenuitvoer en recall. Controleer vóór edits hoe gekoppelde bronverwijzingen Hindsight-consolidatie overleven. Alleen instructietekst toevoegen bewijst die verbinding niet. Een echte uitkomst komt uit een oorspronkelijke gebruikersbron of gecontroleerd uitvoerresultaat, nooit uit Eva's eigen eerdere antwoord.

Eindvoorwaarde: bij een later gesprek beïnvloedt echte correctie of uitkomst de nieuwe afweging; oorspronkelijke verwachting, tegenbron en persoonsscope zijn terug te lezen. Dit is nog te bouwen en geen nieuwe geaccepteerde functie.
