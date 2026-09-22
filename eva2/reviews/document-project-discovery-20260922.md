# Bestaande documentprojecten terugvinden

Bron: 058f407f. Onafhankelijke review: document_maintenance, GO.

De nieuwe read-only actie `list_document_projects` geeft alleen titel, projectcode en opgeslagen status. De bestaande eigenaar-, privé-, project-, bron- en poolcontroles blijven gelden. De actie reserveert geen projectplaats en beweert niet dat bestanden opnieuw zijn gelezen. Het bestaande `get_document_project` blijft daarvoor verantwoordelijk.

Validatie: negen gerichte PostgreSQL-tests voor listing, bestaande documentacties en private eigenaar; vervolgens één listingtest tegen het werkelijk verpakte core-image. Geen modelcalls of externe berichten. Reviewer controleerde dezelfde modulehash 1a79e1903756a8d58123e0670fd7dc0dc8776eefb62d71653fe2303461385545.

De gewone modelkeuze om eerst de lijst op te vragen is hiermee nog geen menselijke gebruiksacceptatie. Installatie en read-only livecontrole worden afzonderlijk geregistreerd in het bewijsbestand.
