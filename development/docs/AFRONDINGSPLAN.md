# Eva — gecombineerd afrondingsplan

**Eén bron voor actuele uitvoering en bewezen voortgang:** [execution-workboard-v1.json](../design/product/execution-workboard-v1.json), met `currentWork` en `audit.deliveryPlan`. [WERKBORD.md](WERKBORD.md) is een projectie, geen tweede status. Op instructie van de eigenaar (13 november 2026) zijn de losse P0/P1/P2-stappen en de out-of-the-box producteisen samengevoegd tot **vijf gecombineerde afrondingsfasen (Fase 1 t/m Fase 5)**, met volledig behoud van alle 103 geregistreerde deeltaken, veiligheidscriteria en oorspronkelijke bewijseisen.

## Uitgangspositie — GitHub, 5 november 2026 (exact behouden ijkpunt)

- **83/103** kleine taken technisch afgerond; **20 open**: 9 bezig, 10 gepland, 1 geblokkeerd (waarvan `E2.6` door eigenaar ingetrokken → 19 open). Slechts **4/24** oorspronkelijke uitkomsten afgesloten. Getelde deeltaken zijn geen percentage productgereedheid.
- **E4.16 staat op `in_progress`**. De eerdere begrensde correcties en compact overzicht zijn **geïnstalleerd en onafhankelijk teruggelezen**. Lokaal is ook de begrensde 429-hervatting geïnstalleerd; de opvolger stopte op HTTP400 zonder exact operatiebewijs. Volledig autonome foutafhandeling en later hergebruik zijn **niet** bewezen. Geen werk dubbel uitvoeren.
- **E4.18 is technisch afgesloten.** Dat zegt niet dat álle actieve dubbele code verdwenen is. De **110 importgrensschendingen** zijn de **oude meting van 13 oktober**; op het werkbord zijn inmiddels 169 werkmapsignalen gemeten en actieve ontvangst-/onderzoeksgrenzen hersteld.
- Productproblemen die deze gecombineerde fasen oplossen: stijve/ambtelijke gesprekshouding, beperkte 3-provider LLM-keuze, te technische kanaal-/persoonskoppeling, afhankelijkheid van vooraf ingericht Home Assistant i.p.v. zelfstandige netwerkontdekking, provider-503/429 bij lopend werk, dubbele afleverclaims in `delegated_work_delivery.py`, en openstaande full-duplex spraak- en gezinsacceptatie (`F2.1`/`F2.2`). `P2.1` blijft geparkeerd.

---

## De 5 Gecombineerde Afrondingsfasen

### Fase 1 — Menselijke entiteit, brede 1-klik LLM-keuze & nul-techniek kennismaking
*Combineert natuurlijke gesprekshouding (`M3.3`/`E1.10`/`E1.13`), uitbreiding van het providercontract (`E2.5`) en frictieloze onboarding/persoonskoppeling (`O1.1`/`E2.1`/`E2.2`).*

| Onderdeel | Concrete realisatie en eindcontrole |
| --- | --- |
| **1A · Menselijk gesprek & doorpakken** | Ontdoe `pydantic_conversation.py` en `conversation.py` van verstikkend compliance-proza terwijl persoons-, privacy- en receipt-grenzen hard in Python/Postgres blijven. Schaal denkkracht automatisch op bij kritische of meerstaps-vragen (niet blijven hangen op `none`-reasoning) en pak door tot er een echt resultaat of gerichte menselijke vraag ligt. |
| **1B · Brede LLM-catalogus (>10 routes)** | Breid de 3 huidige keuzes onder één uniform adaptercontract uit met 1-klik ondersteuning voor o.a. OpenAI API, OpenRouter, Google Gemini, Anthropic Claude, Mistral, DeepSeek, Groq, xAI/Grok, Qwen/DashScope, eigen OpenAI-compatibele endpoints én automatische detectie van lokale servers (Ollama, LM Studio, llama.cpp op localhost/LAN) zonder verplichte GPU of stille betaalde fallback. |
| **1C · Out-of-the-box kanalen & gezinsleden** | Na *Installeren → Persoonlijkheid geven → LLM klikken* geen handmatig agent-/principal-/UUID-beheer meer: 1-stap Telegram/mobiele webapp-koppeling waarbij de eerste koppeling de eigenaar vastlegt, en "voordeur-kennismaking" of 1-klik uitnodigingslink voor gezinsleden en gasten (Eva vraagt de eigenaar in gewoon gesprek wie er aanklopt en maakt zelf de gescheiden privacygrens aan). |

### Fase 2 — Zelfstandige netwerkontdekking (óók zonder Home Assistant) & veilige inlogkluis
*Combineert bronontdekking (`E1.6`/`I2.1`), camera-/apparaatadoptie (`E2.8`/`E1.12`) en eigen adapterbouw (`E4.15`).*

| Onderdeel | Concrete realisatie en eindcontrole |
| --- | --- |
| **2A · Actieve & passieve LAN-ontdekking** | Eva verkent binnen eigenaarmandaat zelf het lokale netwerk (mDNS/Zeroconf, SSDP/UPnP, ONVIF/WS-Discovery, RTSP-poorten, Tuya/SmartLife/Lidl-camera's, Shelly, Hue, Chromecast/Sonos/AirPlay, Matter én optioneel Home Assistant). Gebruikers zonder Home Assistant met alleen losse netwerkcamera's of slimme apparaten worden volwaardig ondersteund. |
| **2B · Toestemming & veilige wachtwoordvraag via chat** | Vindt Eva een apparaat, dan meldt ze dit in gewone taal en vraagt ze of ze mag koppelen. Is een wachtwoord, pincode of apparaatsleutel nodig, dan biedt ze een 1-tik kluisveld én veilige Telegram-invoer waarbij de gateway het wachtwoord **vóór het taalmodel** onderschept in Eva’s lokale versleutelde kluis en het bericht direct uit de chat verwijdert (`deleteMessage`). Wachtwoorden komen nooit in cloud-prompts, geheugen of logs terecht. |
| **2C · Ontbrekende apparaatkoppeling zelf maken** | Herkent Eva een apparaat waarvoor nog geen kant-en-klare connector actief is, dan gebruikt ze haar bestaande ontwikkelroute (`E4.15`) om veilig een passende driver/skill voor te bereiden, te laten toetsen en na toestemming blijvend als *Household Artifact* (`E1.12`) te beheren. |

### Fase 3 — Kernbetrouwbaarheid, veilige taakaflevering & autonome zelfreparatie (`P0` + `P1-01` + `P2-02`)
*Combineert `P0-01` (`E4.16`), `P0-02` (`E4.18`-nazorg), `P0-03` (`E4.16`/`E4.19`) en `P1-01` (`E1.14`).*

| Onderdeel | Concrete realisatie en eindcontrole |
| --- | --- |
| **3A · Eén onfeilbare taakafleverroute (`P0-02`)** | Consolideer de drie overlappende claimtransacties in `delegated_work_delivery.py`, houd kanaaladapters dun, bewaar exact-once aflevering, STOP, lease-verval en intrekking zonder taken die stil in `ambiguous` achterblijven. |
| **3B · Provider-uitval duurzaam overleven (`P1-01` / `E1.14`)** | Maak taken bij HTTP 429/503 duurzaam wachtend met heldere status en automatische hervatting zonder dubbel extern effect. |
| **3C · Onafhankelijk getoetste zelfreparatie & updates (`P0-01`, `P0-03`, `E4.16`, `E4.19`)** | Scheid validatie van dispatch in `reviewed_component_application.py` met echte onafhankelijke reviewerherkomst, fsync/hashes/rollback, en bewijs in de live installatie dat Eva een echte fout zelfstandig repareert, veilig toepast en de verbetering later daadwerkelijk hergebruikt. |

### Fase 4 — Levend dagelijks initiatief & full-duplex spraak in huis (`P1-02` + `P2-01` + `P2-03`)
*Combineert zuinige aandacht (`P1-02`), dagelijks initiatief/eigenheid (`E1.6–E1.13`, `E4.11`, `AU-008`) en spraakafronding (`C3.5`, `C3.4`, `C4.1`, `C3.6`).*

| Onderdeel | Concrete realisatie en eindcontrole |
| --- | --- |
| **4A · Zelf leven zonder tokenverspilling** | Goedkope veranderingspoort vóór nieuwe modelrondes; Eva signaleert uit zichzelf zinvolle veranderingen in huis, netwerk of lopende projecten, handelt binnen mandaat of blijft bewust stil, en bewaart alleen duurzame kernkennis (`E4.11`). |
| **4B · Natuurlijke full-duplex spraak op de speakers** | Eén gedeelde Eva-stem en kern met behoud van zachte beginwoorden (`C3.5`), natuurlijk spreekritme (`C3.4`), direct onderbreken tijdens spreken op beide fysieke speakers (`C4.1`) en automatisch herstel na verbindingsuitval (`C3.6`). |

### Fase 5 — Vroege gezinswaarneming, 7 dagen praktijkbewijs & eindacceptatie (`P1-05` + `P2-04`)
*Combineert `P1-05`, `F2.1` en `F2.2`.*

| Onderdeel | Concrete realisatie en eindcontrole |
| --- | --- |
| **5A · Parallelle praktijkwaarneming (`P1-05`)** | Start directe waarneming van echt gebruik op de geïnstalleerde kandidaat via bestaande eventlogs zodra de veilige route draait, zonder te wachten op optionele extra's. |
| **5B · Zeven dagen gezinsgebruik & eindoordeel (`F2.1` → `F2.2`)** | Draai zeven aaneengesloten dagen op één stabiele geïnstalleerde release; meet nuttige hulp, gemiste kansen, onderbrekingen, fouten en kosten eerlijk terug, waarna de eigenaar beslist over eindacceptatie (`F2.2`). |

---

## Randvoorwaarden & behouden afspraken

- **Nu niet blokkerend (`P3`):** `E2.6` (WhatsApp met eigen nummer) blijft door eigenaar ingetrokken; `C4.3` (telefoonspraak) en `P2.1` (externe benchmarkvergelijking) blijven geparkeerd en blokkeren Fase 1–5 niet.
- **Geen tweede brein of stille kosten:** Eén kern, geheugen, planner en rechtenmodel; geen tweede permanente schaduwdaemon, geen ongevraagde betaalde fallback en geen verzwakking van kind-/groepsprivacy (`P1-04`) of importgrenzen (`P1-03` / `P1-06`). Concrete bestandsingrepen staan in [VEREENVOUDIGING.md](VEREENVOUDIGING.md); de vorige plantekst blijft exact bewaard in het [integratiearchief](../archive/history/20261105-technical-finalization/before/development/docs/AFRONDINGSPLAN.md) en [opruimarchief](../archive/history/20261105-documentatie-opruiming/AFRONDINGSPLAN.md).
- **Werkwijze:** **Gecombineerde uitkomst → exacte oorzaak/ontwerp → gerichte bouw → positieve én negatieve tests → onafhankelijke review → live installatie + core/worker-profielcontrole → menselijk gebruik → pas dan status bijwerken.**
