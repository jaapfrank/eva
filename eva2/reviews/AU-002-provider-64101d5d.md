# AU-002 — onafhankelijke providerreview

Reviewer: bestaande projecteigenaartaak `01a07e5b-3ef4-7ef3-8a68-3197a780694e`.
Bron: reviewbericht aan uitvoeringstaak `01a08d52-31b1-7091-be45-812c0b6e6c9f`, 11 september 2026.
Besluit: **GO voor de beperkte providerreparatie**, geen volledige productacceptatie.

Kandidaatcommit `64101d5d7dcbbf75f49bb130f9d769aa9c32cdb6`, basis `e267e568`.
Providerbestand SHA256 `e0837229a16fdf169f30bfafa85ea2ff6571d78c390502a3217c5e5ce11b39f8`.

De reviewer las de twee-pad-diff, gedeelde provider en lease, toolvrije runner,
annulering/resultaatregistratie en eigenaar-start/hervatten. Geen blokkerende
bronbevinding. Kern en worker moeten dezelfde providerbytes krijgen wegens de
gewijzigde model-, worker- en programhashes. Hervatten maakt een expliciete nieuwe
generatie en behoudt de oude call en het oude verbruik. Oude profielen blijven
ongewijzigd. Stop blijft resultaatregistratie blokkeren, inclusief de race bij
record_success. Geen nieuwe tools, authenticatieroute, rechten of retrybeleid.

De reviewer controleerde de officiële modelmigratie en API-equivalente tarieven:
https://learn.chatgpt.com/docs/models en
https://developers.openai.com/api/docs/models/gpt-5.6-luna . De bedragen zijn geen
meting van abonnementskosten. De uitvoerlengte-instructie is geen harde providerstop;
de bestaande uitvoergrens wordt ook na de providerrespons gecontroleerd.

Alle elf workerbronhashes uit het imagereceipt zijn onafhankelijk met de kandidaat
vergeleken. De reviewer heeft de 14+6 tests en de echte providerproef niet opnieuw
uitgevoerd; deze zijn door de uitvoeringstaak gerapporteerd zonder bewaarde raw logs.
Ook de Docker-lagen zijn door de uitvoeringstaak gecontroleerd, niet door de reviewer.
Installatie, echte chatlevenscyclus en menselijke acceptatie blijven aparte bewijzen.
