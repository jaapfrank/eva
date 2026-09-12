# Kleine afhankelijkheidsreparatie op de live bron

De PostgreSQL-werkadapter ontvangt dezelfde bestaande service, maar typeert die via een application-protocol. Alleen de import en annotatie veranderen; autorisatie, uitvoering en stopreconciliatie blijven gelijk. Dit past de onderhouden Python Protocol-bouwsteen toe zonder nieuwe dependency of operationele rechten.

Canonical referentie f775654 bevat ook dependency-injectie voor een household-router die niet in de huidige image staat. Die router en de gedeelde voice-stream worden niet in deze release opgenomen. De afzonderlijke canonical wijziging blijft bij de coördinator. Deze releaseworktree heeft een imagegebonden baseline en mag niet als geheel in de gedeelde bron worden gemerged.

Alle 258 bronbestanden van de baseline komen bytegelijk overeen met image e1bb281c. De kandidaat wijzigt één adapterbestand en voegt één protocol toe (259 bestanden). De 61 gerichte tests slagen, inclusief werkreceipts, stoppen en de negatieve architectuurtest. De volledige architectuurpoort blijft rood: 25 bestaande bevindingen worden 24; alleen de bedoelde bootstrap-import verdwijnt, zonder nieuwe bevindingen. Geen algemene architectuur-GO, prestatieclaim of modelproef.

Status: bron en image onafhankelijk goedgekeurd; live op 12 september 2026 om 21:44 UTC als c992b0c3. Alleen core en delegated-work-worker zijn vervangen. Beide zijn gezond, de geïnstalleerde bronhashes kloppen en core/voice readiness is groen. Zeven beschermde services en candidate.env zijn onveranderd. e1bb281c blijft de geteste terugweg; er zijn geen modelproeven uitgevoerd. De centrale beheerder verwerkt bronstatus en bewijs in het bestaande werkbord. Dit is een beperkte afhankelijkheidsreparatie, geen volledige architectuurafronding of eigenaaracceptatie.
