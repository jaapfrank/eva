# AU-004 — kleine proef met een herbruikbare werkinstructie

Status: bronaudit en voorstel. Geen modelproef uitgevoerd, geen skill gemaakt of gepromoveerd, geen eigenaaracceptatie.

## Auditbevinding

De gewone gesprekstaak kan via `save_workspace_document`, `read_workspace_document`, `list_workspace_directory`, `find_workspace_files` en `inspect_workspace_file` duurzame eigen bestanden maken en teruglezen. `research_current_sources` kan primaire documentatie onderzoeken. TL-003 bewees deze algemene route live in één beurt; het bewijs zegt niets over procedureel leren.

In de getrackte `eva2/src`-code is geen aparte skillloader, `create_skill`-tool of algemene Python/shell-uitvoerder gevonden. De gebundelde providers zijn AgentMail, Home Assistant, MCP Gateway en MCP Autonomy. Een geregistreerde externe connector kan aanvullende tools leveren; dit is geen bewijs dat code-uitvoering momenteel is toegelaten. Browsertoegang is in deze fase niet beschikbaar voor deze proef en wordt niet geopend om een testroute te construeren.

De ontwerpnotitie `learning-and-person-model-v1.md` noemt de volledige gesloten leercyclus nog onvoltooid. Een Markdown-bestand met een werkwijze is daarom hoogstens een opgeslagen procedurele kandidaat: geen automatische installatie, vindbaarheid, promotie of zelfverbeteringscyclus.

De audit vond ook een concrete opstartafhankelijkheid: de gateway bevat 53 tools maar de ongepagineerde lijst retourneert er 50. Daardoor ontbreekt de vereiste workspace-read-tool bij nieuwe runtimecompositie. De bestaande core blijft werken; de afgebakende paginatiefix gaat voor op deze proef. Geen tool wordt uit de vereiste catalogus geschrapt.

## Voorgestelde kleine gebruikersuitkomst

Eva leert een eenvoudig SRT-ondertitelbestand om te zetten naar WebVTT en bewaart daarvoor zelf een herbruikbare Nederlandstalige werkinstructie. Daarna past zij dezelfde opgeslagen instructie toe op een tweede, vooraf niet getoonde invoervariant.

Dit gebruikt de bestaande taalgestuurde taakuitvoerder en bestandsroute. Een specifieke converter of eerdere WebVTT/SRT-proef is niet gevonden in `eva2/src`, `eva2/tests` of `eva2/evidence`. Dat betekent geen bewezen onkunde van het basismodel: de concrete ontbrekende bouwsteen is een opgeslagen, geteste en opnieuw toegepaste Eva-procedure. Er komt geen converter in de kern.

Primaire documentatie: [W3C WebVTT](https://www.w3.org/TR/2026/CRD-webvtt1-20260520/). Eva onderzoekt die zelf tijdens de eventuele proef. De proef beperkt SRT-invoer expliciet tot genummerde cues met HH:MM:SS,mmm-tijden en gewone tekst; geen styling, vertaling, video of universele SRT-compatibiliteitsclaim.

## Uitvoering na afstemming met de coördinator

1. Controleer eerst dat de paginatiefix onafhankelijk is beoordeeld en dat verse corecompositie alle vereiste tools valideert. Leg bestaande image, mandaat en budgethashen vast. Controleer dat het gekozen nieuwe procedurepad nog niet bestaat; niets verwijderen om nieuwheid te forceren.
2. Verzegel vooraf twee kleine synthetische invoervarianten en een onafhankelijke verwachte semantiek. Variant A bevat twee cues, Unicode en een tekstregel met komma. Variant B blijft buiten de eerste modelcontext en bevat andere tijden, een uurgrens en meerregelige tekst. Eén ongeldig voorbeeld heeft een eindtijd vóór de begintijd en moet als ongeldig worden gemeld. De coördinator levert testdata, geen implementatie of converter.
3. Eerste gewone eigenaar-HTTP-beurt: laat Eva de primaire specificatie onderzoeken, zelf een procedure met bronnen, toepassingsgrenzen en controlelijst schrijven onder een nieuw toegestaan werkmappad, een kleine eigen controlevector maken, variant A omzetten en output plus controlevastlegging teruglezen. Vraag geen automatische globale installatie. Geen extra prompt of developer-resume binnen deze beurt.
4. Lees procedure, output, bronreceipt en toolstappen onafhankelijk terug. Een tekstuele zelfcontrole telt alleen als zelfcontrole; de onafhankelijke controle moet werkelijk header, tijdsyntax, begin/eindvolgorde, cueaantal, Unicode en behoud van tekstregels vergelijken. Geen claim dat Eva code of een parser uitvoerde wanneer zij alleen tekst toepaste.
5. Alleen na een terminale eerste beurt volgt één afzonderlijke gewone eigenaarbeurt met variant B en het pad van de opgeslagen procedure. Laat Eva die procedure daadwerkelijk lezen en toepassen zonder de procedure opnieuw te laten schrijven. De tweedevariant en het ongeldige voorbeeld zijn de vooraf verzegelde controles. Ongewijzigde procedurehash vóór/na en expliciete read-receipt moeten hergebruik aantonen. Een nieuw bestand dat toevallig hetzelfde resultaat geeft, telt niet als hergebruik.
6. Bewaar beide message-ID’s, alle eerste fouten, eventuele menselijke hulp, exacte procedure-/input-/outputhashen, twee terminale antwoorden, werkelijk gemeten verbruik en duur. Geen retries, modelbudgetverhoging, nieuwe connectorrechten, externe berichten, publicatie of livecutover tijdens de proef. Bij een defect eerst oorzaak vastleggen; alleen een afzonderlijk beoordeelde kandidaat mag runtimecode wijzigen.

## Afsluitgrens

Technisch bewijs voor deze mijlpaal vereist een door Eva gemaakte duurzame procedure, een gecontroleerd eerste resultaat, correcte toepassing op de tweedevariant, herkenbare afwijzing van de ongeldige invoer en exact gebonden hergebruik zonder taakspecifieke kernwijziging. Onafhankelijke review beoordeelt of dit werkelijk procedureel hergebruik toont.

Deze proef bewijst geen uitvoerbare nieuwe code-tool, automatische skillselectie, automatische promotie, algemene leercapaciteit of eigenaaracceptatie. Als een uitvoerbare tool in plaats van een taalgestuurde skill nodig blijkt voor de gewenste AU-004-uitkomst, blijft dat expliciet een ontbrekend onderdeel; het wordt niet stil als bewezen aangevinkt. De huidige opdracht is uitsluitend audit en dit concrete plan, niet uitvoering.
