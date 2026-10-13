# Gecontroleerd WebVTT-resultaat

Bron: https://www.w3.org/TR/webvtt1/ — officiële documentatie daadwerkelijk geraadpleegd via onderzoekstool op 28 september 2026; relevante secties 4.1, 4.2.2 en 6.4.
Uitgevoerde werkwijze: /proof-state/webvtt-subset-20260928.py
SHA256: a196aaaed4cb571023c9e92ed0192d502373c07c32f1e75c03b0e880fb47d6d1
Uitvoering met exact de twee aangeleverde name/content-objecten: exitcode 0, geen stderr, uitvoer niet afgekapt. Inhoud hieronder vergeleken met de invoer: drie cues, tijden gelijkwaardig genormaliseerd, duur berekend als einde minus begin (2500−250=2250; 4500−3000=1500; 62250−60000=2250). Tekstregels behouden; &amp; gedecodeerd, b-tags en volledig NOTE-blok verwijderd. Bestand zonder header afzonderlijk afgewezen. Dit is inhoudelijke controle van deze voorbeelden, geen volledige conformiteitstest.

## Cue-lijst en afzonderlijk ongeldig bestand
```json
{
  "format": "eva-webvtt-subset-v1",
  "source": "https://www.w3.org/TR/webvtt1/",
  "files": [
    {
      "name": "eerste-opname.vtt",
      "status": "ok",
      "cues": [
        {"id":"intro","start":"00:00:00.250","end":"00:00:02.500","start_ms":250,"end_ms":2500,"duration_ms":2250,"text":"Welkom & hallo.\nDit is regel twee."},
        {"id":null,"start":"00:00:03.000","end":"00:00:04.500","start_ms":3000,"end_ms":4500,"duration_ms":1500,"text":"Rustig beginnen."},
        {"id":"slot","start":"00:01:00.000","end":"00:01:02.250","start_ms":60000,"end_ms":62250,"duration_ms":2250,"text":"Tot de volgende keer."}
      ],
      "transcript":"Welkom & hallo.\nDit is regel twee.\n\nRustig beginnen.\n\nTot de volgende keer."
    },
    {"name":"ongeldig.vtt","status":"invalid","reason":"Verplichte WEBVTT-header ontbreekt of is ongeldig"}
  ]
}
```

## Leesbaar transcript — eerste-opname.vtt
```text
Welkom & hallo.
Dit is regel twee.

Rustig beginnen.

Tot de volgende keer.
```

## Hergebruik en beperkingen
De Python-werkwijze is opgeslagen en geregistreerd tot 28 september 2027. Volgende opname: resolve_reusable_procedure met procedure-reference:f9160be162c0cccbd82ccc1e6df77791dcb3733138aa77ef894998a3d0942016, daarna run_reusable_procedure met JSON-lijst van name/content-objecten via input_json. Controleer stdout inhoudelijk en bewaar nieuwe resultaten op een eigen pad. Geen nieuwe code, installatie of betaalde middelen nodig.
Conservatieve subset, geen volledige WebVTT-conformiteit of rendering. Alleen b/i/u-opmaak; STYLE, REGION, cue settings, complexe markup en lege cues niet ondersteund. JSON-tekst wordt verwerkt, niet de oorspronkelijke UTF-8-bytes. Geen volledige controle van identifiers. De entiteitherkenning is beperkt: ook geldige entiteiten direct gevolgd door tekst of leestekens kunnen als niet ondersteund worden afgewezen. De aangeleverde &amp; gevolgd door een spatie is correct verwerkt. Uitvoering van andere gevallen is niet bewezen door deze test. Niet-ondersteunde bestanden krijgen afzonderlijke status unsupported, headerfouten status invalid.
