# CO-004/CO-005 — onafhankelijke bronreview

Ontvangen van `/root/au002_release_review` op 11 september 2026.

**Bron-GO** voor commit `ae832a9840e801cea821ac224bd0cb3e01aed1b5`, basis `52247dc70982159ab2702b60037e58676cd8967c`.

De reviewer vond twee blokkades, beide hersteld en herlezen:
- Een geweigerde geheugenopslag mag niet als ingeplande correctie worden getoond. De duurzame journalfase wordt nu gecontroleerd.
- Een geopende bron moet opnieuw worden geautoriseerd bij verversen, ook als de lijst zelf nog bereikbaar is. De detailinhoud wordt eerst verborgen en opnieuw opgehaald.

De daaropvolgende echte databaseproef vond een derde fout in de bestaande lijst: een afgeronde bewaartaak kan na wissen haar oude fase behouden. De nieuwe `content_erasure`-filter voorkomt het opnieuw decoderen van gewiste inhoud. Ook deze filter is door de reviewer herlezen en goedgekeurd.

De elf beoordeelde bestanden komen exact overeen met de commit. Gezamenlijke bestandsbinding volgens reviewer: `b358924a4a8679afb7cb738364b5ffae5dd17be328dd222295bea41c6d7510ec`.

Reviewer las `memory-tests.log` (13 geslaagd), `evidence-memory-browser.json` (geslaagd, inclusief detail-404 bij lijst-200) en `memory-http-test.log` (volledige HTTP→journal/outbox→retentie→wissen geslaagd met deterministische geheugenprovider). De reviewer voerde geen eigen tests of mutaties uit.

Deze bron-GO omvat geen live Hindsightproef of menselijke acceptatie. CO-008/GE-01 blijft afzonderlijk geaccepteerd.

## Technische oplevering — GO

Dezelfde onafhankelijke reviewer bevestigde daarna **technische GO voor CO-004 en CO-005**, gebonden aan bewijscommit `8e7426e510c009ef3a5f948fb977953809ebeff5` en evidence-SHA256 `c598637e7161b21ebde0e8d398afbbe138ebf064142e45668dfd18218234eb8d`.

Gecontroleerd: live geheugenmethoden exact gelijk aan broncommit `ae832a9840e801cea821ac224bd0cb3e01aed1b5`, overige parent-runtimebytes behouden; image `sha256:bfb0d25abb7f4a1247d376050fdfd7b606e3a3285dcd19b9ee6831d3297b046d`, importhashes en dashboardbestanden consistent. Alle zeven live bewijshashes kloppen. De gecorrigeerde informatie werd onafhankelijk teruggevonden; na beide wisacties verdwenen de proefbronnen en het proefgegeven. Back-uphashes, behoud van Hindsight/spraakdiensten en live UI-leesproef zijn onderbouwd.

Geen resterende concrete blokker binnen de beschreven bronbediening. De expliciete beperkingen in het bewijs blijven gelden. **GE-02/eigenaaracceptatie staat open; CO-008/GE-01 blijft geaccepteerd.** De reviewer voerde geen nieuwe tests of mutaties uit.
