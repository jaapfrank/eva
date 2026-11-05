# Eva — geprioriteerd afrondingsplan

**Eén bron voor actuele uitvoering en bewezen voortgang:** [execution-workboard-v1.json](../design/product/execution-workboard-v1.json), met `currentWork` en `audit.deliveryPlan`. [WERKBORD.md](WERKBORD.md) is een projectie, geen tweede status. **P0 = eerst, P1 = daarna, P2 = productafronding, P3 = optioneel.** Onderstaande prionummers zijn volgordelabels voor bestaande taken en problemen, géén extra taken of afgegeven acceptaties.

## Uitgangspositie — GitHub, 5 november 2026

- **83/103** kleine taken technisch afgerond; **20 open**: 9 bezig, 10 gepland, 1 geblokkeerd. Slechts **4/24** oorspronkelijke uitkomsten afgesloten. Getelde deeltaken zijn geen percentage productgereedheid.
- **E4.16 staat op `in_progress`**. De eerdere begrensde correcties en compact overzicht zijn **geïnstalleerd en onafhankelijk teruggelezen**. De kandidaat-/werkruimtebinding, volledig autonome foutafhandeling en later hergebruik zijn **niet** bewezen. Geen werk dubbel uitvoeren.
- **E4.18 is technisch afgesloten.** Dat zegt niet dat álle actieve dubbele code verdwenen is. De **110 importgrensschendingen** zijn de **oude meting van 13 oktober**; de actuele waarde is onbekend en moet opnieuw gemeten worden.
- Productproblemen: provider-503 bij echte schoolvraag, 22.633 tokens voor één stilte-afweging, onvolledige spraakonderbreking, nog geen bewezen algemene gezinscontinuïteit. `P2.1` is geparkeerd; `F2.1` en `F2.2` zijn open.
- Dit is **bron-/bewijscontrole op GitHub**, geen nieuwe live-audit van Windows/WSL of bevestiging van de werkelijk draaiende release.

## P0 — code, echte fouten en veilige aflevering

| Prio | Werk / bestaande kaart | Aanpassing en aantoonbare eindcontrole |
| --- | --- | --- |
| **P0-01** | Werkelijke runtime en bestaande fouten, primair **E4.16** | Vergelijk exacte geïnstalleerde core/worker-profielen, actuele claims, providerfouten, receipts en Git-commit. Bind één mislukte gebruikerstaak aan concrete oorzaak. Geen oude pogingen opnieuw dispatchen. |
| **P0-02** | Dubbele aflevercode, **E4.18-nazorg** | Consolideer drie overlappende claimtransacties in `delegated_work_delivery.py`, houd selectors/autoriteitsregels apart, vervang toegang tot private storevelden, maak Telegram alleen kanaaladapter, log veilige foutcodes. **Test concurrentie, lease-verval, intrekking, crash en geen dubbele aflevering.** |
| **P0-03** | Update-/reviewketen, **E4.16/E4.19** | Scheid validatie van dispatch in `reviewed_component_application.py`. Bewaar fsync, lock, hashes, receipts, unknown state, rollback. Verifieer échte onafhankelijke reviewerherkomst; een niet-lege reviewersnaam is onvoldoende. Bewijs een gecontroleerde update en teruglezing. |

**P0 is pas klaar** wanneer echte regressies (geen dubbele effecten, geen verkeerde ontvanger, STOP, persoonsgegevens, herstel naar laatst werkende release) aantoonbaar geslaagd zijn. Niet eerst grote cosmetische splitsingen doorvoeren. [Concrete bestandsingrepen](VEREENVOUDIGING.md).

## P1 — continuïteit, efficiëntie en veilige gezinsproef

| Prio | Werk / bestaande kaart | Aanpassing en bewijs |
| --- | --- | --- |
| **P1-01** | Provider-503, **E1.14/E4.16** | Maak een bestaande taak duurzaam wachtend, toon bruikbare fout en vervolg; herstel bij uitval/herstart zonder dubbel extern effect. Optionele tweede provider alleen na **expliciete toestemming, kosten- en privacycontrole**; geen stille betaalde fallback. |
| **P1-02** | Token- en tijdverspilling, aandacht/E1.6 | Meet tokens **per uitkomst**, niet per poging. Gebruik een goedkope `niets-relevants-veranderd`-poort vóór nieuwe modelafweging, behoud geplande aandacht/urgentie en legitimeer een nieuwe modelronde met vers bronbewijs. Vergelijk echt token- en foutverbruik vóór/na. |
| **P1-03** | Importgrenzen, actuele architectuur | Voer `src/tools/check_architecture.py` opnieuw uit op werkelijke huidige bronstand. Sorteer bevindingen op risico en dynamisch gebruik; los actieve grensschendingen in de aangeraakte hotspots op. **Niet** de oude 110 als actuele telling gebruiken. |
| **P1-04** | Adversarial gedrag en kinderen, vóór brede gezinsuitrol | Bestaande mandaatgrenzen tegen prompt-injectie behouden; voeg gerichte misbruikcases toe voor geïmporteerde tekst, bevoegdheidsverhoging, groepsgesprek, persoonsverwisseling, intrekking en gedeelde speakers. Leg kindbeleid vast (toestemming ouder, privédata, camera-/spraakidentiteit, zichtbare stop/wisbediening). |
| **P1-05** | **F2.1** voorbereiden en direct waarnemen | Start de gewone gezinswaarnemingen **parallel** zodra de veilig bestaande route bruikbaar is. Noteer nuttige, gemiste en storende initiatieven en werkelijke onderbrekingen; gebruik eerst bestaande eventlogs. Een nieuwe permanente schaduwmodus is **niet nodig**. |
| **P1-06** | Grote kern-/adapterbestanden gericht beoordelen | `pydantic_conversation.py` (~4.971 regels), `conversation_actions.py` (~3.581) en `sensory_events.py` (~2.456) dragen veel verantwoordelijkheden. Meet imports, echte foutpaden, testdekking en wijzigingsrisico; splits alleen aantoonbaar overlappende of foutgevoelige verantwoordelijkheden. Geen generieke totale herbouw. |

## P2 — het daadwerkelijke product afmaken

| Prio | Werk / bestaande kaart | Eindresultaat |
| --- | --- | --- |
| **P2-01** | **E1.6–E1.14**, eigen initiatief | Eén werkelijke bronverandering → zelfstandig passend initiatief/stilte → blijvende verantwoordelijkheid en later vervolg. Continuïteit bij modeluitval is eerst P1-01; bouw geen tweede scheduler. |
| **P2-02** | **E4.15/E4.16/E4.19**, zelf verbeteren | Eva ontdekt een echte behoefte of fout, vergelijkt bestaand hulpmiddel, realiseert een veilige uitbreiding, krijgt onafhankelijke beoordeling, installeert en gebruikt haar **later opnieuw**. Behoud de bestaande camera-/begroetingsacceptatie als E4.15-voorbeeld, niet als aparte kern. |
| **P2-03** | **C3.5, C3.1, C3.4, C4.1, C3.6**, spraak | Behoud één gedeelde stem/kern. Controleer zachte beginwoorden, antwoordlatentie, natuurlijk ritme, onderbreken tijdens spreken en reconnect op **beide fysieke speakers**. Een interne PCM- of API-ack is geen menselijke luisteracceptatie. |
| **P2-04** | **F2.1 → F2.2**, eindgebruik | Rond de afgesproken **zeven dagen** op één geïnstalleerde kandidaat af; registreer frustratie, hulp, missers, resultaten, fouten, kosten en feedback. De eigenaar beoordeelt pas daarna expliciet of Eva nuttig en prettig genoeg is. |

## P3 — nu niet bouwen

- **E2.6 WhatsApp**, **C4.3 telefoonspraak**, extra apparaten of nieuwe platforms zijn optioneel. Laat ze geen P0–P2 blokkeren.
- Een altijd-aan ambient klein model, nieuwe kennisgraaf, tweede uitvoeringsframework, nieuwe queue, volledige microVM-architectuur of omvangrijke autonomie-UI is geen bewezen noodzaak. Eerst de bestaande grenzen testen.
- Een uitgebreid autonomielevel per actie, een `Waarom?`-knop en variantenarchief zijn zinvol **na** stabiele uitvoering, tenzij een concrete veiligheidstest ze eerder noodzakelijk maakt.
- **P2.1 externe eindvergelijking** blijft geparkeerd; geen claim dat Eva beter is dan OpenClaw/Hermes.

## Werkwijze en statusregels

**Bestaande uitkomst → exacte fout → gerichte codeaanpassing → positieve én negatieve tests → echt onafhankelijke beoordeling → geïnstalleerde readback → menselijk gebruik → uitsluitend dan status bijwerken.**

Houd bewijs, foutgeschiedenis en oorspronkelijke criteria in het JSON-werkbord; verzin geen nieuwe hoofdkaart of nieuwe bron van waarheid. De oude gedetailleerde planning blijft exact in het [archief](../archive/history/20261105-documentatie-opruiming/AFRONDINGSPLAN.md). Volgorde veranderen verandert geen afgesloten taak of acceptatie. Het afsluiten van P0/P1 mag de gezinsproef niet onnodig vertragen.
