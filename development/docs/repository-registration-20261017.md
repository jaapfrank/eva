# Bestandsregistratie — 17 oktober 2026

**E3.1 is afgerond.** Dit is een versiegebonden registratie, geen volledige codebeoordeling of extra gezinsfunctie.

| Geregistreerd | Aantal |
| --- | ---: |
| Paden | 4896 |
| Gevolgd door Git op de vastgelegde bronstand | 4789 |
| Aanwezige projecteigen ongetrackte paden | 107 |
| Eerdere volledige lezing met exact dezelfde hash | 447 |
| Beschermde inhoud: uitsluitend metadata | 80 |
| Expliciete hashuitsluitingen | 18 |
| Uitgesloten dependency-/cache-/privédirectories | 113 |

Bronstand: `b00c407aa0c13cef90decb0f98754816dc49b731` plus expliciet geregistreerde checkoutwijzigingen. Manifest-SHA256: `5f1d3ac9be606c07cc3dcbda1c8ead05f82bb95772e58bd47b48c60f7f9c3339`.

Per pad: hash of concrete uitsluiting, categorie, verklaarde module-eigenaar, versie-/Gitstatus en reviewstatus. Rollen zijn door padbeleid verklaard; consumenten en behoud/vervanging worden pas inhoudelijk beoordeeld in E3.2–E3.4. Er is niets verwijderd. Geheime waarden, privégesprekken en afgeschermde eindvragen zijn niet overgenomen.

De registratie gebruikt het [bestaande manifest](eva-repository-inventory.generated.json). De oude versie blijft exact uit Git herstelbaar via de vastgelegde vorige revisie/hash. Caches en dependency-installaties worden niet doorgelopen. Genegeerde projecteigen documentatie is wel gezocht. Bron- en metadatasymlinks worden niet gevolgd.

De werkelijke installatie:20 service-images geregistreerd; alleen bij kern en achtergrondwerker zijn projectbronhashes gelezen. Op18 gedeelde bronpaden verschillen hun versies. UI-verschillen zijn niet automatisch fouten; de bekende gedeelde provider-/bronbinding krijgt voorrang in E3.2. Bij spraak is alleen imagemetadata geregistreerd; de andere uitvoerder behoudt haar bron en installatie.

Vijf gerichte controles en [onafhankelijke review](../reviews/e31-file-register-20261017.json) zijn geslaagd. Het [uitkomstbestand](../evidence/reassessment/e31-file-register-20261017.json) bewaart ook de exacte delta na de snapshot. De afrondingsstatus in werkbord/WERKBORD wijzigt bewust ná die snapshot; zo wordt een reviewhash niet achteraf herschreven.

Gebruik: `PYTHONPATH=src python3 -m tools.repository_inventory` toont de delta sinds deze snapshot. `--write` maakt bewust een nieuwe versie; `--capture-runtime --write` leest bovendien actuele installatiehashes. Een gewijzigde hash bewijst geen functionele fout. Lees de delta voordat een nieuwe snapshot wordt gemaakt.

**Hierna:** E3.2–E3.4 inhoudelijk beoordelen; daarna alleen bewezen opruim-/refactorbesluiten uitvoeren. Het WhatsApp-nummer is door Jaap uitgesteld. Er is nu geen handeling van de eigenaar nodig.
