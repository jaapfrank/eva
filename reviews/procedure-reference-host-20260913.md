# AU004: expliciet bewaarde werkwijzen later terugvinden

Bronkandidaat, nog niet live. Dit voegt drie opt-in gesprekstools toe aan de bestaande providerregistratie: designate_reusable_procedure, list_reusable_procedures en resolve_reusable_procedure. De bestaande bronopslag blijft leidend; er is geen tweede index of persoonlijk Hindsight-feit.

## Gebruikersproef na review en gecontroleerde activering

1. Vraag Eva een korte, nuttige werkwijze als document op te slaan en die expliciet als herbruikbaar te markeren. Bijvoorbeeld: “Bewaar mijn werkwijze voor een documentreview: eerst bewijs controleren, dan conclusies beoordelen. Markeer deze als herbruikbaar tot [datum].” De markering gebruikt document_path, exact het pad van de zojuist opgeslagen werkwijze. Eva hoeft geen intern aanroepnummer te kennen: de host koppelt het pad aan de unieke echte geverifieerde save in dezelfde gesprekbeurt. Onbekende of dubbelzinnige paden worden geweigerd.
2. Begin een nieuw gesprek en vraag: “Welke bewaarde werkwijze kan helpen bij het beoordelen van mijn volgende document?” Eva kan de eigen kandidaten bekijken en de gekozen verwijzing opnieuw controleren. Laat haar de gevonden documentlocatie noemen; een kandidaatlijst alleen is nog geen gecontroleerde verwijzing.
3. Vraag daarna iets waarvoor de werkwijze niet relevant is. Eva hoeft die werkwijze daarvoor niet toe te passen. Dit oordeel moet nog met het echte model worden bewezen; de huidige offlineproef claimt geen semantische selectie.
4. Wijzig of verwijder de bron en herhaal de vraag. Een verouderde verwijzing mag niet als actueel terugkomen. Intrekken van het mandaat moet toegang blokkeren.

Deze gebruikersproef wordt pas gevraagd wanneer een gereviewde versie daadwerkelijk beschikbaar is. Er is nu niets voor Jaap te testen.

## Controle en grenzen

De geïntegreerde proef gebruikt echte PostgreSQL, originele conversation ingress en de bestaande geverifieerde MCP-toolroute via een lokale HTTP-fixture. De fixture bewaart documenten alleen in testgeheugen. Een latere resolve voert geen nieuwe save uit. Pagination, andere eigenaar, gewijzigde inhoud, dubbele call-ID, werkelijk gewiste ingress en gestopt mandaat zijn gecontroleerd. Gewiste bronnen worden uit kandidaten gefilterd; fouten in de huidige autorisatie blijven blokkeren.

De technische 25 controles slagen. De architectuurpoort heeft nog precies dezelfde 23 overtredingen als basis 51be1235; dat is geen vrijgave van de hele repository. Details en bronhashes staan in het gelijknamige JSON-bewijs. Geen modelcalls, livewijzigingen, tweede zoekindex, algemene vaardigheidsclaim of verzonnen acceptatie.
