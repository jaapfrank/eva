# I2.2 — onafhankelijke eindreview, 5 oktober 2026

**GO voor technische afronding van uitsluitend I2.2 binnen de oorspronkelijke eindvoorwaarde.** Geen blokkerende bevinding in de bewezen waarnemingsautomatisering. De correctiejob blijft `ambiguous/internal_error`; herstel van ongeldige modeltoolinvoer, I2.1 en het gehele onderdeel I2 blijven open.

Reviewer: `/root/agentzero_cm_review`. Bron- en bewijsreview; geen eigen modelcalls, HA-acties, installaties of productwijzigingen. Alleen dit reviewbestand is geschreven. Root beheert eindbewijs en werkbord. Het compacte repo-eindbewijs in `eva2/evidence/product/i2-observation-automation-installed-20261005.json` is op samenhang met de gelezen traces en receipts gecontroleerd.

De oorspronkelijke `remainingTasks[id=I2.2].doneWhen` luidt: “Eva stelt de gevraagde waarneming en terugmelding zelf in via bestaande algemene hulpmiddelen en passende automatisering, zonder vraag-/apparaatspecifieke kernregel; de werkelijke terugmelding blijft binnen het bestaande mandaat.” Die concrete vaardigheid is aangetoond. Een foutloze afronding van iedere ontwikkelpoging is geen aanvullende eindvoorwaarde; de mislukte poging mag ook niet als geslaagd worden geregistreerd.

Bewijs staat onder `/home/administrator/project/.eva2-artifacts/i2-observation-automation-20261005/`:

- `initial-manifest.json`, `automation-readback.json` en `initial-trigger-evidence.json`: Eva koos zelf de fictieve vochtbron, schreef de configuratie en controleerde de actieve installatie. De echte overgang 55→24 gaf een geslaagde callback en dezelfde lokale notice; callback na 22,676 seconden. Een JSON-serialisatiefout in de onafhankelijke observer werd via de bestaande HA-trace hersteld, zonder taak- of effectherhaling.
- `corrected-automation-readback.json` en `work-11ff62a6-770e-5c8a-8a50-bebfaee3060c.json`: Eva schreef zelf de twee gewijzigde configuraties en las beide volledig terug. De ontvanger is vooraf actief; de bronrun kiest vóór `ask` een gesprekscode. Ontvangst koppelt gesprek, satellite en actieve bronrun. De bronrun wacht op een blijvende afhandelmarkering, zodat ontvangst vóór het antwoord op `ask` niet afhankelijk is van een later ingestelde eventlistener. Diezelfde markering onderdrukt dubbele lokale meldingen.
- `corrected-trigger-result.json` en `corrected-traces.json`: de echte overgang 55→22 leverde na 21,804 seconden een geslaagde callback en identieke lokale notice. Brontrace `6f9012d523290791b19fa26c4ce7572d` en ontvangertrace `3cbb18550dfeeb415f5565925cc0b499` hebben beide `script_execution: finished`; de bronwait is voltooid en beide automations hebben `current: 0`. De ontvangertrace verbindt exact gesprek, satellite en work-id aan het notice-event. De gecorrigeerde tracekeuze gebruikt de nieuwste run; herstellen van de eerdere selectie van een oude trace was geen nieuwe uitvoering.
- `work-e7eb17c4-9711-5d27-af40-e7386bedd7ec.json`: opgeslagen status `succeeded`; één geverifieerde GET van uitsluitend `sensor.eva2_i2_49dc2be9883d_a` gaf HTTP 200 en waarde 22. Werkresultaat, callback en notice hebben dezelfde inhoud. Resultaathash: `bd5aecb866511ca070fe69520c432135091a9d39926577ee6eaca5c76d02379a`. Gedeeltelijke werkmeting: twee voltooide modelresponses, één toolcall, 9.252 tokens; kosten onbekend.
- `cleanup.json`: de twee eigen proefautomatiseringen en twee fictieve sensoren zijn verwijderd; geen resterende eigen proefautomatisering. De algemene geïnstalleerde mogelijkheid blijft beschikbaar.

De timingrace is in de configuratie gericht opgelost. De tweede echte proef bevestigt de werking daarvan; daarin kwam de callback na de ontvangstbevestiging van `ask`. Er is geen afzonderlijke geforceerde vroeg-eventproef of algemene foutloosheidsclaim. De proef bleef bij fictieve bronwaarden, algemene HA-hulpmiddelen en een lokaal event; geen fysieke bediening, spraakuitvoer of echte camera-/agendapayload.

De drie timeoutcorrecties zijn eerder gebundeld beoordeeld. Gewone spraak behoudt 32 seconden. Alleen duurzaam expliciet als automation toegelaten aanvragen krijgen geen spraakdeadline; ontbrekende metadata blijft gesloten. Stop, disconnect, actuele autoriteit en eenmalige atomaire claim blijven behouden. De HA-client wijzigt uitsluitend de aanvraagtimeout en behoudt de verbindingsgrenzen van de gedeelde sessie.

`installation.json` bindt de installatie aan dezelfde beoordeelde SHA-256-hashes:

| Bestand | SHA-256 |
| --- | --- |
| `src/eva/adapters/api/native_voice.py` | `e7c8c3e25aff9a7d2aa624e4b42d7bb85dbfd846a3aeb817bb15efecdcaf5369` |
| `src/eva/adapters/postgres/voice_requests.py` | `adc7c1352a680102e0f72cb699195882b35cc8e28c0d3894e529cebd42361bf0` |
| `deploy/home-assistant/custom_components/eva2_voice/client.py` | `dad73bd53cd7ec1bdb024303d254ebe09e9b63a3744800c5921baa3c67fc272d` |

Ook beide beoordeelde documentatiehashes stemmen overeen. Het installatiebewijs registreert gecontroleerde runtimebron, HA-bestanden en pakketcontrole van 1.225 bestanden/8 images. Root rapporteert 88 geslaagde gerichte tests: 26 API, 5 op echte geïsoleerde PostgreSQL en 57 HA. De reviewer las de testwijzigingen maar voerde de tests niet zelf uit.

`correction-finalization-diagnosis.json` behoudt de correctiejob als `ambiguous/internal_error`. De opgeslagen fingerprint matcht `ConversationConflict:Home Assistant request body must be JSON`. Broncontrole bevestigt validatie in `home_assistant_api.py` vóór toelating van die nieuwe externe call en de foutmapping in `delegated_digital_runner.py`. Die geweigerde call introduceerde geen onzeker extern effect en maakt eerdere opgeslagen wijzigingen niet ongedaan. De exacte ongeldige argumenttekst is niet behouden; een definitief correctierapport is niet gecommit. Herstel is als concrete vervolgbevinding bij bestaande taak T2.1 vastgelegd en blijft expliciet open, zonder herhaling van uitgevoerde writes of versoepeling van autoriteit.

Ook de eerder vastgelegde toolvrije-admissiongap, de afzonderlijke bronvoorwaarden van I2.1, het gehele I2 en menselijke/eindacceptatie vallen buiten dit GO. Geen nieuwe taak-ID, extra producttelling of bredere voltooiingsclaim.
