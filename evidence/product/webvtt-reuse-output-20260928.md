# Resultaat andere-opname.vtt

Uitgevoerd met de ongewijzigde opgeslagen WebVTT v2: /proof-state/webvtt-v2-20260928.py, SHA-256 e809df5057f132283b164ba752abecf696ceb30287f0795b989f609c8a56e0ff. De gevonden v2 is de expliciete verbeterde opvolger van v1; de procedurezoekactie leverde geen nieuwere kandidaat op.

## Daadwerkelijke stdout

```json
{
  "format": "eva-webvtt-subset-v2",
  "source": "https://www.w3.org/TR/webvtt1/",
  "files": [
    {
      "name": "andere-opname.vtt",
      "status": "ok",
      "cues": [
        {
          "id": "nieuwe-cue",
          "start": "00:00:58.500",
          "end": "00:01:00.000",
          "start_ms": 58500,
          "end_ms": 60000,
          "duration_ms": 1500,
          "text": "De deur <blijft> dicht.",
          "segments": [
            {
              "speaker": null,
              "text": "De deur <blijft> dicht."
            }
          ],
          "transcript": "De deur <blijft> dicht."
        },
        {
          "id": null,
          "start": "01:00:00.000",
          "end": "01:00:01.001",
          "start_ms": 3600000,
          "end_ms": 3601001,
          "duration_ms": 1001,
          "text": "Café klaar.\nTot zo.",
          "segments": [
            {
              "speaker": "Eva",
              "text": "Café klaar.\nTot zo."
            }
          ],
          "transcript": "Eva: Café klaar.\nTot zo."
        }
      ],
      "plain_transcript": "De deur <blijft> dicht.\n\nCafé klaar.\nTot zo.",
      "transcript": "De deur <blijft> dicht.\n\nEva: Café klaar.\nTot zo."
    }
  ]
}
```

## Leesbaar transcript zonder sprekerlabels

```text
De deur <blijft> dicht.

Café klaar.
Tot zo.
```

## Controle

De daadwerkelijke stdout is inhoudelijk vergeleken met de aangeleverde invoer: twee cues; NOTE met beide notitieregels ontbreekt; &lt;blijft&gt; is correct als letterlijke tekst <blijft> behouden; voice-opmaak staat niet in text of plain_transcript; Café en de regelovergang vóór Tot zo. zijn behouden. Eerste tijdsinterval: 60000 − 58500 = 1500 gehele milliseconden. Tweede interval: 3601001 − 3600000 = 1001 gehele milliseconden. Begin- en eindtijden stemmen overeen met de invoer. Het sprekerloze transcript hierboven is gelijk aan plain_transcript. De oorspronkelijke stdout bevat daarnaast ook de door de procedure standaard geleverde sprekermetadata en transcriptvariant met label; deze zijn niet gewijzigd.

Uitvoering: exitcode 0, geen stderr, uitvoer niet afgekapt. Dit is controle van deze concrete opname binnen de conservatieve WebVTT-subset, geen claim van volledige WebVTT-conformiteit. Geen code gewijzigd en geen nieuw onderzoek gedaan.
