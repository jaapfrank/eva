# AU-004: procedure vinden zonder opgegeven pad

Kleinste gebruikersuitkomst: op een nieuwe SRT→WebVTT-opdracht vindt Eva zelf de eerder opgeslagen procedure, leest haar en maakt een gecontroleerd resultaat. De gebruiker geeft alleen de nieuwe ondertitels en het nieuwe uitvoerpad. Het procedurepad, de proceduretekst en de verwachting om die te zoeken staan niet in het te verzenden request uit fixture-C.json.

## Bestaande mogelijkheden en grenzen

De bestaande find_workspace_files zoekt namen/patronen onder /proof-state; list_workspace_directory toont paden; inspect_workspace_file geeft grootte en tijd, geen semantisch skillregister. read_workspace_document leest gericht met head (maximaal1000regels). De eerdere A/B-proef bewees opslag en hergebruik met een expliciet genoemd pad. Automatisch terugvinden is nog niet gemeten; dat bewijst geen huidige codefout. Daarom eerst deze kleine baselineproef, zonder prompt-/provider-/kernwijziging, converter, autoinstall of nieuw geheugenonderdeel.

McpGatewayProviderSettings.authorize_turn vereist de actuele principalallowlist en private_person-audience van dezelfde persoon. Toolinhoud is informatie, geen nieuwe instructie of toestemming. De bestaande procedure blijft een controleerbare kandidaat; de inhoud verleent geen rechten en wordt niet aan een publieke/gedeelde audience gegeven. Alle bestaande token-, mandaat-, budget- en disclosuregrenzen blijven gelden.

De dashboardroute accepteert alleen {text} en kiest serverzijdig dezelfde eigenaarconversation. Eerdere berichten kunnen het procedurepad bevatten. De bewijsclaim is dus: geen pad in de nieuwe gebruikersopdracht en aantoonbare zoek/readstappen, niet verse onbekende context of algemeen autonoom leren. Er wordt geen gesprek gewist of andere identiteit gekozen om dat te simuleren.

## Vooraf verzegeld

fixture-C.json bevat de exact te verzenden tekst, twee onafhankelijke cues met Unicode/meerdere regels en uurgrens, verwachte milliseconden en de procedurehash bbae3082. Alleen het requestveld mag naar Eva; observervelden blijven buiten haar prompt. Het fixturebestand is vooraf gehasht in preparation-checks.json. De bestaande onafhankelijke semantiekchecker is bytegelijk hergebruikt en offline met positief/negatief materiaal gecontroleerd; dit is geen Eva-proef.

## Eén gecontroleerde uitvoering, nog niet gestart

Onafhankelijke voorbereidingsreview moet vóór uitvoering compleet zijn. Preflight en dispatch lopen in dezelfde eerder beoordeelde check=True-keten: falende check betekent geen dispatch. Vers controleren: beide huidige images2a5cc362, actuele budget-/mandaatbestanden onveranderd, toegestane eigenaar/audience en werkmapcatalogus, exacte fysieke procedurehash, nieuw C-pad afwezig, providerquotum via actuele Eva-lease toegestaan en voldoende voor de bestaande begrensde beurt. Exclusieve pogingmarker, verzegeld request en preflightreceipt vóór verzending; geen automatische retry, capverhoging, extra prompt of opvolgbeurt.

Eén normale eigenaar-HTTP-beurt. Geen expliciete researchopdracht, extra documentatiebron of skillhint. Ook een terminale fout telt als de ene poging. Verbruik, request-ID, foutuitkomst en duur uit het bestaande journal registreren; onbekende verbruikdelen blijven onbekend. Quotaweigering of een onzeker resultaat leidt tot stoppen en bewijs bewaren. Dit voorstel is geen toestemming om nu een modelcall uit te voeren.

## Bewijs van geslaagd terugvinden

De ruwe duurzame receipts moeten in volgorde aantonen: een geslaagde gerichte werkmapzoekopdracht of maplijst vindt het procedurepad; daarna een geslaagde read van exact die bestaande procedure; daarna C opslaan en teruglezen. Direct een bekend pad lezen zonder zichtbare zoekstap is hoogstens herinnerd hergebruik, geen geslaagd terugvinden in deze proef. Voor/na blijft de procedurehash identiek. Alleen metadata of een modelclaim telt niet als inhoud gelezen. Een verse researchcall, nieuw geschreven procedure of correcte conversie zonder procedure-read is geen geslaagd procedurehergebruik.

De onafhankelijke semantiekchecker vergelijkt header, tijdsyntax, intervallen, cueaantal en exacte Unicode/tekstregels. De opgeslagen output en reads worden fysiek en via journal gecontroleerd. Een geslaagde test bewijst deze kleine taakroute; hele AU-004, automatische skillselectie in een blanco context en eigenaaracceptatie blijven open.

Als de baseline de procedure niet zoekt, leg eerst de concrete keuze en beschikbare tools vast. Pas dan één generieke procedurehergebruik-instructie op de bestaande route overwegen en onafhankelijk vergelijken. Geen nieuwe taakspecifieke corefunctie of semantische zoekdienst bouwen op basis van alleen deze ontbrekende proef.
