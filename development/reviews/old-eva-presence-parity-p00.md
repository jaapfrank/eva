# Onafhankelijke review — oude-Eva-presence P00

Datum: 25 augustus 2026.

## Afbakening

- Alleen-lezen vergelijking van Eva A en Eva2 voor spontane huisaanwezigheid.
- Geen runtime-, Home Assistant-, speaker-, deurbel-, A- of B-wijziging.
- Gebruikerswaarneming, bron-/fixturebewijs en onveranderlijke live-receipts
  blijven drie afzonderlijke bewijssoorten.

## Bevindingen

- Eva A bevat de concrete private deurbelroute, exacte deurbelcamera- en
  speakerbinding, TTS-preflight, modelkeuze `silent`/Telegram/intercom,
  voice-ACK-herstel, autonomy-wake, nachtgrens, cooldown en dagbudget.
- De eigenaar ervaart spontane deurbel-/speakeruitvoer live. In de repository
  is geen onveranderlijke oude hardware-receipt gevonden die de fysieke
  afspeeluitkomst exact aan één build bindt; die ontbrekende receipt is niet
  alsnog geclaimd.
- Eva2 is aantoonbaar sterker in persoonsisolatie, geheugen, crashherstel en
  event/readbackbewijs, maar heeft nog geen toegelaten speaker- of intercomroute.
- De bestaande minimale presence hoort daarom bij V1-behoud. Veel apparaten en
  brede voice/media blijven terecht een latere producttrack.
- P01-P05 bouwen klein op: bestaand expressiecontract uitbreiden, één speaker,
  één deurbel, residentpolicy en daarna zeven dagen op de bevroren eindimage.

## Besluit

**GO.** De vergelijking en overgangsgate zijn eerlijk begrensd, sluiten aan op
de bestaande Eva2-orchestratie en bevatten geen product- of hardware-liveclaim
die niet door het bijbehorende bewijs wordt gedragen.
