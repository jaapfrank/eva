# AU004: procedurekandidaten vóór de eerste planning

De gewone gespreksroute biedt nu relevante metadata van expliciet gemarkeerde werkwijzen aan voordat het model zijn eerste antwoord of actie kiest. Dit repareert het concrete gat dat alleen beschikbare list/resolve-tools niet vanzelf geraadpleegd werden.

De bestaande PostgreSQL-bronregistratie levert maximaal acht eigenaar-/projectgebonden kandidaten via ingebouwde Dutch text search (lexicale overlap, geen semantische zoekclaim). Er is geen nieuwe index, tabel, externe zoekdienst of persoonlijke geheugenpublicatie. Alleen document-ID en maximaal 400 tekens gebruiksbeschrijving worden aangeboden, totaal maximaal 4096 tekens. Metadata is expliciet onbetrouwbare informatie, geen opdracht of nieuwe bevoegdheid. Geen pad of procedure-inhoud gaat mee.

Bronretentie, gewiste oorspronkelijke berichtbron, huidige eigenaarbinding, mandaat en stop worden gecontroleerd. De huidige artifactbytes worden pas via de bestaande resolve-route gelezen en geverifieerd; kandidaatmetadata zegt expliciet current_artifact_verified=false. Een passende kandidaat is geen verplicht te gebruiken procedure: het model beoordeelt de vraag en moet een gekozen verwijzing eerst oplossen en vervolgens via de bestaande leesroute lezen. Er wordt geen toolcall of geslaagde autonome selectie gefingeerd.

## Bewijs

De echte geïsoleerde PostgreSQL-test loopt door PostgresConversationActions en LivingConversation naar de eerste modelaanroep. Een passende nieuwe conversatie krijgt de bestaande document-ID; een niet-passende rekenvraag krijgt deze niet. Twee gesprekken gebruiken exact twee fake-modelaanroepen en geen aanvullende gatewaycalls tijdens kandidaatselectie. Het model en persoonlijke recall zijn offline doubles; er is geen echte autonome selectie of uiteindelijke gebruikerskwaliteit bewezen. De bestaande test blijft actuele bestandwijziging, dubbelzinnigheid, herhaalde stappen, bronverwijdering en ingetrokken mandaat afwijzen. Na bronverwijdering verdwijnt ook de vooraf aangeboden kandidaat.

Echte PG-test: 1 passed in 5.07 s, netwerkloze tijdelijke PG-container verwijderd. Gerichte bestaande contract/providercontroles: 25 passed in 1.08 s. Geen modelverbruik, productieverbinding of live wijziging. Kandidaten voegen maximaal 4096 tekens modelcontext toe; werkelijke tokenkosten en productlatentie zijn nog niet gemeten. Query plus broncontroles zijn lokale DB/mandaatreads; geen aparte betaalde selectieronde.

## Grenzen en volgende proef

Lexicale selectie kan synoniemen zonder gedeelde lexemen missen en overlappende maar niet-passende onderwerpen aanbieden. De acht best scorende actieve records begrenzen het werk; gewiste oorspronkelijke bronnen worden daarna gefilterd. Dit is geen algemene skill-loader of benchmarkwinst. Het huidige gedrag bij databasecorruptie is fail closed, niet een stilzwijgende beschikbaarheidsclaim.

Na onafhankelijke review moet de runtimewriter een exact bronmanifest tegen de actuele parent maken. Eén gewone nieuwe passende opdracht moet aantoonbaar resolve plus documentlezen en toepassing van de aangeleerde regels tonen; daarnaast een niet-passende opdracht zonder proceduregebruik. Geen SRT/VTT-specifieke logica en geen aanwijzing in de proefprompt om een procedure te zoeken. Bewaar model-/toolgebruik en eerste-reactietijd. Afwijzing of geen selectie blijft een eerlijke mislukte proef, geen automatische herhaling.
