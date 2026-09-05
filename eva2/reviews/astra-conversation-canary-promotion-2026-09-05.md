# Astra gesprek — beperkte promotiebeoordeling

Datum: 5 september 2026. Reviewer: `/root/identity_pair_review`.
Door `/root` vastgelegd uit de onafhankelijke beoordeling; geen zelfgoedkeuring.

**GO voor voorbereiding van inactief slot 3 en de bestaande quiescente
4→3-omschakeling van de gesprekcanary. Geen volledige productacceptatie.**

Beoordeeld: de bestaande build/import/scan van bron `dc675503`, image `455be8…`,
de selectie van 147 modules/1.750 tests, Astra met een verse stack, herstel van
gekopieerde databases en de bestaande Telegramclient met gekopieerde privébinding.
De laatste proef bewijst eindantwoord en herstart, niet live tekststukjes.

De nieuwe mechanische toelatingsimport is geen nieuwe scan of herlabelde
RB005/RB006-receipt. Zij verwijst naar de oorspronkelijke bouw-/scanbestanden,
het exacte archief/configdigest en 28 gecontroleerde archiefblobs.
De bestaande image-inspectie en scan-validator accepteren die import.

- Toelatingsimport SHA-256: `258fb21ebb87b54858d581083052643fbf9c996d43a28c111479c63497e79de7`.
- Oude slot3-env SHA-256: `bee83f3783f2a2be312ce503846f81ecc7cc69965ca0aa2888f2dfde791f7181`.
- Geplande slot3-env SHA-256: `15a63c06ed171f811b826af7c2f0ca62f779cb67074257ca4948a0ca6ec038b9`.
- Private bestanden: `conversation-rollout-20260905/{candidate-admission,target-plan}.json`
  onder het reeds geregistreerde CO-002-artifactarchief.

Voorwaarden vóór schrijven: private root-owned `0600`-back-up van de oude
slot3-env; exacte vóór/na-hashes; alleen de genoemde image-/dashboard-/configvelden,
expliciet Astra en uitgeschakelde doeltoelating veranderen. Identiteit, budget,
koppeling en secrets blijven gelijk; geen worker of Telegrampoller starten.

Omschakeling gebruikt uitsluitend de bestaande stop→datakopie→migratie→start→
controle→routerwissel. Slot4 blijft intact als terugweg. Controleer daarna
model, image, instellingen, readiness en een begrensde canarybeurt. Bij een
afwijking kopieert de bestaande omgekeerde route eerst de nieuwe gegevens terug.

Open grenzen: de scan gebruikt de gepinde database van 31 augustus; geen nieuwe
tweebouwerproef, volledige streaming, canonieke episodetoelating, menselijke
acceptatie of concurrentiewinst. De volledige doelstelling blijft open.

Na de geslaagde live omschakeling en herstartproef gaf dezelfde reviewer GO
voor één 3→4→3-terugrolcontrole. Alleen de geïnstalleerde journaldecoder leest
het nieuwe opgeslagen antwoord, met read-only databasetransacties en zonder
model-/providerobject. De bronkopieën en kandidaatconfiguratie blijven privé
behouden totdat beide richtingen en dezelfde antwoordhash zijn gecontroleerd.
