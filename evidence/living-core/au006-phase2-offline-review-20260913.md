# Fase 2: exacte hostpreview en begrensde activering ter review

Status: uitsluitend offline voorbereiding. Fase 1 is onafhankelijk gesloten in `63090eb5`. Er is geen hostseal uitgevoerd, geen core of worker gestart/vervangen, geen mount schrijfbaar gemaakt en geen model- of documenteffect uitgevoerd. De activatieblokkade blijft aanwezig.

## Werkelijke inputs en preview

De private receipt betreft gateway `83690bd3da2e425f8e376ebbb1e69df8` en de twee werkelijk toegewezen private tools. De preview gebruikt de reeds beoordeelde `DocumentGoalPlan`, `PreparedDocumentGoal` en catalogusparser uit de kandidaatbron; zij berekent geen nieuw plan met een model. De volledige doeltekst/eigenaar/inhoud blijft in de private preview.

- Goal: `4ff68e4d5d4c5492503dc6bbfda8af4c99fbe4e2e3072413c4f9ae775adf7fe0`.
- Plan: `8cab242e7619feb02a3ce69aa3d9b383f5ac5c68622e12386c919b43da30c52b`.
- Toolconfiguratie: `230c60856bfdb1c5c4eb4cd227323f3294d39f4533bf5510d6aca4b948f7a951`.

De vier beoogde canonical outputs zijn afzonderlijk gehasht in de JSON. `phase2-offline-review/preview.json` bevat alleen reviewdata, niet de live projectconfiguratie. De generator verifieert de echte receipt, private toolvisibility, schema en providerbinding, huidig hostmandaat, GoalSpec, scope, paden en directory-identiteit. De bestaande runtimeprojectbestanden zijn bytegelijk gebleven; het journal is leeg. Generaties 0 en 1 zijn alleen verwachtingen voor de twee toekomstige stappen.

## Exacte configuratie

Core blijft het bestaande Compose-project `eva2-managed-b-slot3`. De kandidaat verandert uitsluitend image `83298332…` naar de eerder beoordeelde `2393a1d5…`, voegt de twee `EVA_DOCUMENT_GOAL_*_FILE`-instellingen toe en mount het private project read-only. De bestaande MCP-autonomyconfiguratie, statische catalogus, modelconfiguratie, ingress en overige services blijven behouden.

De nieuwe provider blijft image `0ef19e69…`, vast IP `172.30.0.5`, dezelfde root-/journal-inodes en dezelfde binding. Alleen `/workspace` en het eigen `/journal` worden read-write; rootfs en `/run/eva2` blijven read-only. De nieuwe documentworker gebruikt de kandidaatimage en bestaande workerloop, exact hostplan, eigen catalogus en de bestaande veilige DSN/token-/mandaatbestanden. Hij heeft uitsluitend de bestaande private runtime- en workspacenetwerken. De andere gedelegeerde worker wordt niet vervangen.

De gateway wordt in fase 2 niet gerecreated. De huidige endpointpolicy én preserve-resourcesoverride blijven intact. Iedere latere gatewayterugweg moet deze guardbasis behouden; base-only blijft ongeldig.

## Rechten en effectgrens

De drie argumentloze gesprekstools zijn alleen voor de exacte private eigenaar, tenant/installatie en `conversation_project_id` uit de definitie. Owner/project/pad/inhoud/generatie zijn geen modelargumenten. Start behoudt verse execute-authority; status en stop gebruiken hun bestaande observe-/stopregels. De worker kiest uitsluitend deze goal-ID met de exact gebonden GoalSpec in `waiting` en voert vervolgens de bestaande host-/catalogus-/authoritycontroles uit. Er wordt geen nieuwe raw-toolcatalogus aan de core gegeven en geen nieuwe SQL-MCP-connector toegelaten.

Het toegestane projecteffect blijft de twee vaste nieuwe documenten onder `/proof-state`, in de afgesproken volgorde met eigen operation/requestbindings en generaties. Onzekere native toestand geeft geen tweede effectpoging. Bestaande tests voor eigenaargrenzen, stop en crashherstel blijven hun beperkte bewijs; er worden geen crash-/stopproeven op gedeelde diensten toegevoegd.

## Voorgestelde volgorde na afzonderlijke fase-2-GO

1. Verse read-only preflight: huidige guard/policy/55 toolrijen en twee geselecteerde catalogusbindings, echte eigenaar en actief mandaat, kandidaat-/parentimage en bronhashes, coreconfig, protected services, veilige mountrechten en host-/journal-inodes. Controleer afwezige doelbestanden, leeg journal, geen reeds geadmitteerde doelstaat en geen documentworker. Neem verse quiescence/readiness vlak voor corecutover. Een afwijking stopt de uitvoering; geen capverhoging, herregistratie of automatische retry.
2. Seal uitsluitend na expliciete toestemming: bewaar de bestaande blokkade privé, laat de gebonden sealer exact de vier canonical outputs met exclusieve file creation schrijven naar de eigen projectdirectory, uid 65532/mode 0400. Controleer ieder outputhash tegen deze preview en herlaad plan/definitie/catalogus. Bij fout: blokkade terug, gedeeltelijke bestanden behouden voor onderzoek, geen herschrijven of runtimecutover. De sealer geeft zelf geen toestemming voor runtime-effecten.
3. Maak uitsluitend de eigen providermounts schrijfbaar en controleer dezelfde image/IP/native binding, schema en inode-identiteit. Laat de documentworker nog uit. Vervang daarna alleen de core met de gebonden kandidaatcommand na verse quiescence. Controleer health/readiness, behoud van bestaande configuratie en het aanbieden van de drie gesloten documentacties in de juiste private scope; de raw tools blijven afwezig. Verifieer dat er nog geen doeladmissie, journalmutatie of doelbestand is.
4. Start alleen de nieuwe documentworker met exact de JSON-command. Controleer image/command/netwerken/read-only secrets, procesliveness over meerdere pollintervallen en foutlogs, plus nog ontbrekende doelstaat en leeg journal. Er is geen aparte HTTP-healthcheck op deze worker; een draaiend proces wordt niet als geslaagde documentuitvoering gepresenteerd.
5. Alleen wanneer de GO ook de gewone gebruikersproef omvat: één bestaande geauthenticeerde private gespreksingang met “Maak het afgesproken documentproject af en laat het eindresultaat zien.” Geen directe SQL-admit, gescripte toolkeuze of fictieve ConversationTurn. Zo nodig één statusvraag: “Laat het eindresultaat van het documentproject zien.” Verwacht start/inplanning → status → geverifieerd completed met exact twee receipts/documenten. Een startantwoord mag niet voortijdig voltooiing claimen. Leg werkelijke calls, wachttijd en kosten vast; onbekende kosten blijven onbekend.
6. Vergelijk completed-projectie, beide native receipts/generaties en echte bestandsbytes met het hostplan. De gewone actie levert finale inhoud en het bestaande private pad. Toon een bestands-/artifactlink pas nadat de eigenaar die via een bestaande private route daadwerkelijk kan openen; verzin geen download-URL. Een eventueel afzonderlijk privé-exportbestand wordt als export onderscheiden van het originele workspacebestand. Als bereikbare artifacttoegang nog ontbreekt, blijft dat expliciet open en wordt alleen de aantoonbare inhoud/padstatus gerapporteerd.

Een fase-2-activerings-GO zonder probe-GO laat stap 5–6 open; stilte geldt niet als model-/effecttoestemming. Deze reviewvoorbereiding heeft geen van de stappen uitgevoerd.

## Gerichte terugweg

Stop uitsluitend de nieuw aangemaakte documentworker op zijn vastgelegde ID. Herstel uitsluitend de core met de oorspronkelijke volledige Compose-basis, zonder de nieuwe opt-in override; een parentimage bovenop de nieuwe env/mounts is geen volledige terugweg. Stop zo nodig alleen de nieuwe provider of zet zijn twee mounts terug naar read-only met dezelfde vaste-IP-basis. Laat de bestaande gedelegeerde worker en de zeven beschermde diensten ongemoeid.

Behoud sealed inputs, doel-/timelinegegevens, beide documenten, journal en registratiereceipt. Geen DELETE, catalogusrestore, journalreset of geforceerde replay. Een actief/onzeker doel blijft zichtbaar als niet verder uitgevoerd. De gatewayguard en endpointpolicy blijven buiten deze terugweg; eventuele gatewayactie vereist de actuele guardbasis en aparte controle.

Technische activering, geslaagde gewone modelroute, bereikbare private bestandslink en eigenaaracceptatie zijn afzonderlijke uitkomsten. Dit plan verleent geen van deze goedkeuringen en claimt nog geen bruikbaar chatproject.
