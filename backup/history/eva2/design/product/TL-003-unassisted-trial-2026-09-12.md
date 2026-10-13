# TL-003 — één ononderbroken gewone opdracht

Status: voorbereid, niet uitgevoerd. Geen nieuwe runtimewijziging of eigenaaracceptatie.

## Waarom deze proef

Het bestaande bewijs `TL-003-completion-2026-09-10.json` registreert zeven
geverifieerde toolstappen en een teruggelezen resultaat van 2322 bytes. Het zegt
ook expliciet `ordinaryHttpFinalSuccess=false`: ontwikkelaars repareerden fouten
en hervatten dezelfde opgeslagen ingress. Daarmee is aansluiten en gebruiken
gebouwd, maar zelfstandig afronden vanuit één verse opdracht nog niet bewezen.
De eerdere concrete fouten (eigenaarbinding, geldig parameterloos schema,
endpoint als connectorreferentie, dubbele resultaatcontext) zijn al gerepareerd.
Deze audit vond geen resterende, reproduceerbare connectorfout die nieuwe code
rechtvaardigt. Daarom eerst de bestaande route beproeven.

## Uitvoering door de coördinator

1. Wacht totdat de lopende helperupdate afgerond is. Leg de actuele core- en
   workerimage, connectorbevoegdheden en bestaande context-/kostenlimieten vast.
   Lees alleen gerichte velden; schrijf geen tokens of privécontext in bewijs.
2. Gebruik een nieuw synthetisch testgesprek via de bestaande geauthenticeerde
   eigenaar-HTTP-ingang. Geen Telegrambericht versturen. Controleer vooraf dat
   de geselecteerde openbare MCP-endpoint nog niet geregistreerd is voor deze
   eigenaar. Bestaande verbindingen niet verwijderen om een proef te forceren.
3. Kies een onderhouden openbare documentatiedienst die binnen het bestaande
   mandaat valt. Laat Eva de primaire documentatie, endpoint en herkomst zelf
   onderzoeken. Gebruik GitMCP alleen met expliciete beperking: een nieuw
   repository-endpoint bewijst een nieuwe aansluiting, niet een onbekend
   protocol of onbekende aanbieder. Bewaar dit onderscheid in het resultaat.
4. Stuur precies één gewone opdracht, bijvoorbeeld:

   > Onderzoek een openbare MCP-documentatiedienst die je nog niet hebt
   > aangesloten. Sluit die via je bestaande mogelijkheden aan en gebruik haar
   > om uit te leggen hoe een Python-contextmanager bestanden ook bij een fout
   > sluit. Bewaar een korte Nederlandse uitleg met primaire bronlinks in een
   > nieuw bestand in je toegestane werkmap en lees het terug. Gebruik geen
   > persoonlijke accounts, doe geen aankopen en publiceer niets.

   De concrete vraag mag vóór verzending worden aangepast aan de gekozen
   dienst. Leg de exacte uiteindelijke opdracht en de nieuwe message-ID vast.
5. Observeer de bestaande journal-/resultaatroute tot de beurt voltooid is of
   de bestaande maximale uitvoertijd verstrijkt. Geen developer-resume,
   handmatige registratie, extra modelprompt, budgetverhoging, deployment of
   vervangende opdracht tijdens deze poging. Eén modelgestuurde gebruikersbeurt
   is de hele proef; stop bij een fout en bewaar de eerste concrete oorzaak.
6. Lees het opgeslagen resultaat en de bij deze message-ID behorende stappen
   onafhankelijk terug. Geen nieuwe modelaanroep voor deze bewijscontrole.

## Wanneer technisch geslaagd

- De endpoint was vooraf aantoonbaar niet geregistreerd in deze eigenaarsscope.
- Dezelfde nieuwe beurt onderzoekt, registreert, ontdekt en gebruikt de dienst
  via `connect_service`, `discover_connector_tools`, `use_connector_tool` en de
  bestaande bestandsroute; de bevoegdheden blijven ongewijzigd.
- De gevraagde inhoud en primaire bronlinks staan in het werkelijk opgeslagen
  en teruggelezen bestand; pad, bytes en SHA-256 zijn vastgelegd.
- Precies één definitief antwoord is opgeslagen en vermeldt het echte resultaat.
- Geen tussenkomst van ontwikkelaar of eigenaar en geen verhoging van limieten.
- HTTP 200 zonder resultaat, of alleen een al aangesloten dienst hergebruiken,
  telt niet als deze volledige proef.

## Registratie en afhandeling

Bewaar image-ID, opdracht-/ingress-ID, voorafgaande afwezigheid, geverifieerde
stappen, outputhash, doorlooptijd, werkelijk gemeten verbruik en alle fouten in
nieuw bewijs. Het historische september-10-bewijs blijft ongewijzigd.
Bij succes: technische zelfstandige aansluiting bewezen binnen deze dienst en
opdracht; eigenaarsoordeel en concurrentievergelijking blijven afzonderlijk.
Bij mislukking: rapporteer exact de eerste blokkade en repareer alleen dat
concrete defect in een nieuwe kandidaat. Nooit het oude resultaat als succes
van deze nieuwe proef tellen.

## Auditbasis

Gelezen op 12 september: PROJECTDOEL.md, WERKBORD.md,
TL-003-completion-2026-09-10.json, providers/mcp_registration.py,
providers/mcp_discovery.py en test_mcp_registration_tools.py. De bestaande test
`test_endpoint_discovery_absence_allows_connection_without_hiding_denials`
dekt afwezigheid plus behoud van eigenaar-/intrekkingscontrole. Geen bestaande
tests opnieuw uitgevoerd: deze oplevering wijzigt uitsluitend de proefinstructie.