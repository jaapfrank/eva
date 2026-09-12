# Controleverslag B — AU004-20260912-attempt3

De opgeslagen procedure /proof-state/au004-srt-webvtt-procedure-20260912-attempt3.md is daadwerkelijk volledig gelezen en ongewijzigd toegepast. Geen extra onderzoek of nieuwe procedure. Vooraf vond de bestandszoekactie alleen procedure en A-bestanden: resultaat B, dit verslag en het invalid-doelpad bestonden niet.

## Geldige invoer B
Tekstueel gecontroleerd: twee unieke numerieke nummers (1, 2), twee correcte timingregels, drie niet-lege tekstregels verdeeld als 2 + 1. Geen verboden teksttekens &, < of >, instellingen of uitbreidingen. Alle minuten en seconden binnen 00–59, uren twee cijfers en milliseconden drie cijfers.
Tijden met de procedureformule handmatig beoordeeld:
- Cue 1: begin 3599900 ms, einde 3601050 ms; duur 1150 ms. De uurgrens is geldig.
- Cue 2: begin 3602500 ms, einde 3604000 ms; duur 1500 ms.
Begintijden nemen toe; geen overlap; tussenruimte 1450 ms.

Opgeslagen resultaat: /proof-state/au004-subtitles-B-20260912-attempt3.vtt. WEBVTT-header en lege regel toegevoegd, nummers behouden, alleen de vier timingkomma's vervangen door punten. Spaties rond de pijl en lege scheidingsregel gecontroleerd. Tekst exact behouden:
```
Eerste regel, met komma.
Tweede regel: café.
```
en bij cue 2:
```
Zoë sluit de deur.
```
Geen vertaling, trimmen of Unicode-normalisatie toegepast. De afzonderlijke teruglezing van B bevestigt alle zichtbare regels, interpunctie en Unicode. De opslagcontrole toont tevens het meegegeven afsluitende LF-regeleinde; de afzonderlijke leestool geeft dat laatste regeleinde niet weer. Geen onafhankelijke bytecontrole van UTF-8, BOM of regeleinden uitgevoerd.

## Apart ongeldig testgeval
Begin 9000 ms; einde 8500 ms; verschil -500 ms. Afgewezen volgens de expliciete proceduregrond eind <= begin: de eindtijd ligt 500 ms vóór de begintijd. Geen automatische reparatie. Geen geconverteerd bestand voor dit geval geschreven, ook niet /proof-state/au004-invalid-20260912-attempt3.vtt.

## Methode, fouten en grenzen
Alle controles zijn tekstueel en met handmatige tijdrekening uitgevoerd, niet door code. Geen parser-, browser-, afspeel- of bytevalidatie. Geen conversiefout in B aangetroffen; het ongeldige interval is een invoerfout en is geweigerd. Geldigheid van B is beoordeeld binnen de beperkte opgeslagen procedure, niet met een volledige WebVTT-validator. Dit verslag wordt na opslag afzonderlijk teruggelezen.
