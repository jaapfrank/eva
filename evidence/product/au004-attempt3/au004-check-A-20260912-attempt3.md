# Controleverslag AU004-20260912-attempt3

## Bewijs en methode
De zoekactie naar *attempt3* onder /proof-state gaf vóór schrijven geen treffers. Procedure en uitvoer zijn nieuw opgeslagen en afzonderlijk volledig teruggelezen. De onderzoeksdienst rapporteerde het lezen van de primaire W3C-bron: https://www.w3.org/TR/2026/CRD-webvtt1-20260520/ . De bronclaims zijn via dat onderzoeksresultaat gebruikt; geen onafhankelijke tweede broncontrole uitgevoerd.

Alle onderstaande inhoudelijke controles zijn tekstueel en met beredeneerde rekenstappen uitgevoerd, niet door code. Geen parser, browser, uitvoerbare test of byte-/UTF-8-validator gebruikt.

## Werkelijke controles van invoer A en teruggelezen uitvoer
- Twee cues, unieke identifiers 1 en 2 en ieder één tekstregel: behouden.
- Header WEBVTT en lege regel aanwezig; lege regel tussen cues aanwezig.
- Timing 1: 1250 naar 3000 ms; duur 1750 ms > 0.
- Timing 2: 4100 naar 6900 ms; duur 2800 ms > 0.
- Begintijden stijgen; tussenruimte 1100 ms, geen overlap.
- Alle minuten en seconden binnen 00–59; elk millisecondeveld heeft drie cijfers.
- De vier timingkomma's zijn punten geworden. 'Hoi, Eva.' behoudt zijn komma en punt. 'Café om de hoek.' behoudt accent, spaties en punt. Geen tekstregels gewijzigd of vertaald.
- Geen markup, speciale tekstsyntaxis of cue-instellingen aanwezig.
- Bij opslaan is een afsluitende LF meegegeven en door de opslagcontrole als overeenkomstige inhoud bevestigd. De afzonderlijke tekstlezing toont geen afsluitende LF; die weergave alleen bewijst geen ontbrekend regeleinde. Geen bytecontrole uitgevoerd.
- Teruggelezen procedure bevat bron, scope, afwijzingsgronden, stappen en controlelijst; lokale beperking voor speciale tekens is onderscheiden van algemene WebVTT-regels.

## Zelfbedachte controlevector B: minuutgrens en twee tekstregels
Invoer:
```srt
7
00:00:59,999 --> 00:01:00,001
Ja, café.
Tweede regel.
```
Verwachte uitvoer, inclusief afsluitend regeleinde:
```vtt
WEBVTT

7
00:00:59.999 --> 00:01:00.001
Ja, café.
Tweede regel.
```
Tekstueel toegepast en vergeleken: 59999 naar 60001 ms, dus duur 2 ms. Nummer 7 blijft staan; twee tekstregels en de tekstkomma blijven gelijk. Alleen de twee timingkomma's wijzigen. Deze vector voldoet aan de beperkte procedure; niet als afzonderlijk bestand of met code getest.

Negatieve variant: vervang de eindtijd door 00:00:59,999. Begin en eind zijn dan gelijk, dus tekstueel afgewezen wegens eind <= begin; niet automatisch gerepareerd.

## Fouten en beperkingen
Geen inhoudelijke conversiefout aangetroffen in A of positieve vector B. De negatieve variant is bewust ongeldig. Geen toolfouten gemeld tijdens deze poging tot dit verslag. Dit is geen bewijs van volledige WebVTT-conformiteit of afspeelbaarheid. UTF-8-bytes en afspeelgedrag zijn niet apart onderzocht. Alleen een lokale procedurekandidaat, geen installatie of bevoegdheidswijziging.
