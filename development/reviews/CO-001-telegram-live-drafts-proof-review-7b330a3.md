# CO-001 — onafhankelijke proofreview

- Taak: `CO-001`
- Basis: `ff9ff89aa0f102543d217138a5f96fe91c2d6c4a`
- Implementatie: `7b330a3c7ba4e00fa97b5fcd18d3e127ffcf4df7`
- Bewijs: `aae9229c29c8fef6f3576ed6ef4b4a242b1a32f9`
- Besluit: **GO**

De implementatie voldoet aan de begrensde kaart. Alleen een toegelaten
privégesprek krijgt een tijdelijke `sendMessageDraft`; groepen en lokale
commando's slaan die route over. Alleen echte modeldeltas uit een toolvrije
fase worden gepubliceerd. De Telegramgrens van 4096 tekens wordt uitsluitend
in de connector toegepast, terwijl de kern haar volledige canonieke antwoord
behoudt. De draft-ID is deterministisch, niet nul en blijft binnen een signed
31-bit bereik.

Drafts zijn best-effort en schrijven geen duurzame toestand. Een mislukte
eerste draft valt vóór modelstart terug naar precies één gewone core-call;
een latere draftfout laat de al lopende stream afronden. Alleen de bestaande,
geclaimde finale `sendMessage` wordt duurzaam bevestigd. Een reeds voorbereid
of herhaald ingress-item start daarom geen tweede model- of draftpad.

Zelf herhaald:

- De vijf door het werkbord vereiste pytestbestanden: `54 passed`.
- Dezelfde set plus de modeladaptertests: `77 passed`.
- De exacte Ruff-selectie uit het werkbord: groen.
- `python3 tools/check_component_catalog.py`: `ok`, nul overtredingen.
- Beide repository-inventorytests: groen (`11` en `1`).
- `git diff --check 7b330a3^ 7b330a3`: groen.

De openbare receipt bindt de juiste basis en implementatie en maakt haar grens
eerlijk: zij gebruikte alleen lokale contractproviders, deed nul externe
Telegram- of modelcalls, bewijst geen live uitrol en claimt geen stopknop of
algemene concurrentiewinst. Een echte Telegramcanary en eventuele
stop-generatie-UX zijn vervolgwerk, geen voorwaarde van CO-001.
