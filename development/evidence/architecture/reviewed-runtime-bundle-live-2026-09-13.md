# Gezamenlijke reparaties live

Sinds 13 september 2026 om 00:50 Nederlandse tijd draaien core en delegated-work-worker op image `2a5cc362`. Dit bundelt de gedeelde schemavalidatie, actuele hostgebonden mandaatlezing, terminale registratie van directe modelpogingen inclusief gemeten foutverbruik, en de guard die taalherstel niet opnieuw start nadat het bestaande inputbudget is opgebruikt.

De bron en image zijn onafhankelijk goedgekeurd. Voor de laatste zeven regels slaagden 209 gecombineerde checks; na die goedgekeurde delta slaagden 96 gerichte budget-, poging- en onderzoekschecks. Deze aantallen overlappen. Geen extra modelproef of budgetverhoging.

Vóór installatie waren er geen actieve eigenaarwerkers, geen recente actieve gesprekcheckpoints en geen spraakruimte. Alleen de twee afgesproken services zijn vernieuwd, vanaf de geverifieerde parent c992. Beide zijn gezond zonder onverwachte herstarts; de zeven gewijzigde bronhashes kloppen in beide containers. Core en voice geven HTTP 200/ready. Zeven beschermde containers, candidate.env en de achtergrondwerkconfiguratie bleven gelijk. De quiescencecontrole was een momentopname, geen globale requestlock.

Op canonical commit 1d92625e zijn zes van de zeven bronbestanden bytegelijk aan de image. runtime.py wijkt alleen af doordat de niet live household-router daar wel wordt geïnstalleerd; de gewijzigde mandaatconstructor is AST-gelijk. De releasebaseline wordt niet naar canonical gemerged.

Terugweg: herstel de vastgelegde override en registry uit de lokale activationmap en vernieuw alleen core/worker naar c992. Geen databaserestore. De schrijf-foutpaden zijn offline getest; een productierollback was niet nodig.

Technisch geïnstalleerd en gecontroleerd. Dit is geen algemene architectuurafronding, nieuwe gebruikersacceptatie of bewijs van algemene modelkwaliteit. De centrale beheerder registreert de bestaande bronfixes en deze live ontvangst in het werkbord.
