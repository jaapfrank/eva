# Gewone documentproef mislukt; gerichte terugweg uitgevoerd

De goedgekeurde fase-2-seal schreef vier bestanden met exact de onafhankelijk gereproduceerde hashes, uid 65532/mode 0400. De kandidaatcore werd ready; de eigen provider kreeg uitsluitend twee schrijfbare mounts en de nieuwe worker bleef eerst idle zonder goal of journalmutatie.

Daarna is precies één gewone private dashboardvraag verzonden: “Maak het afgesproken documentproject af en laat het eindresultaat zien.” De geauthenticeerde serverroute bepaalde de echte eigenaar en ConversationTurn. De vraag kreeg na 33,82 seconden een vastgelegd antwoord: het project was gestart maar had `needs_help`, nul voltooide stappen en geen einddocument. Het model claimde geen succes en startte het project niet opnieuw. Er is geen tweede gebruikers-/statusvraag verstuurd.

De opgeslagen gebruiksrecords bevatten vijf modelpogingen, 36.985 inputtokens en 292 outputtokens. Zij melden vier voorgestelde modeltoolcalls; drie gesprekstooladmissions zijn gevonden: één startactie en twee statusacties. Deze interne model-/toolstappen zijn geen extra door de operator verzonden vragen. Prijskosten zijn niet vastgesteld. De JSON bewaart het oorspronkelijke message-ID, antwoordhash en de private events/usage; vijf recorded modelpogingen worden niet zonder bewijs gelijkgesteld aan alle eventuele transportsubrequests.

## Native grens en oorzaak

Het doel heeft één effectreservering en nul voltooide stappen. De eerste document-step bevat `TOOL_REJECTED` met “MCP gateway rejected the tool call”; de tweede document-step ontbreekt. De gatewaylog koppelt dit aan een -32601 “Tool not found” voor de werkelijk geregistreerde create-tool. Het native journal is leeg en beide doelbestanden ontbreken. Er is dus één afgewezen gateway-effectaanroep, geen geverifieerde documentpublicatie.

De werkertoken bevat een geverifieerde gebruiker waarvan de email overeenkomt met de private toolowner, met adminclaim en null-teamscope. De gateway-RPC-handler verwijdert bij die combinatie de eigenaaremail. De geïnstalleerde servicepolicy weigert terecht private tools voor die vervolgens anoniem gemaakte admincontext. Metadata/list-toegang bewijst daarom hier geen invocation-toegang. De geïsoleerde bronproef reproduceert het verlies van eigen private toegang en het herstel door uitsluitend deze email te behouden.

## Terugweg en behouden gegevens

De nieuwe documentworker en provider zijn op hun vastgelegde IDs gestopt. Na verse quiescence is alleen de core met de volledige oorspronkelijke basis teruggezet naar image `83298332…`; health/readiness en de uitgeschakelde opt-in zijn gecontroleerd. De gateway is niet gerecreated: dezelfde ID, endpointpolicy en preserve-resourcesguard blijven. De overige beschermde containers hebben ongewijzigde IDs/images/starttijden.

Alle sealed inputs, de mislukte goal/timeline, effectreservering en oorspronkelijke proef zijn behouden. Geen SQL-reset, journalreset, tweede start, statusvraag, herregistratie of automatische retry is uitgevoerd. De blokkade staat op de mislukte gewone proef. Het eerdere herstel van de vier bestaande toolrijen blijft afgerond.

Er is geen einddocument, bereikbare artifactlink of eigenaaracceptatie bewezen. De gatewaycorrectie uit het afzonderlijke reviewpakket is niet live en maakt het bestaande `needs_help`-doel niet vanzelf hervatbaar. Een eventueel herstel van die doeltoestand vereist een afzonderlijk beoordeelde route; dit pakket voert dat niet uit.
