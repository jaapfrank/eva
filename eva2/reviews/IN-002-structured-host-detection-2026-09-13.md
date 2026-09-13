# IN-002: gestructureerde hostdetectie

De kandidaat leest ID, VERSION_ID, VERSION en VARIANT_ID uit dezelfde begrensde, reguliere os-releasebron. PRETTY_NAME blijft uitsluitend een zichtbare naam. De geselecteerde Ubuntu-puntversie moet bij VERSION_ID passen; dubbelzinnige sleutelwaarden worden geweigerd. De bestaande reguliere fallback voor /etc/os-release blijft behouden.

Een passende Ubuntu-versie/architectuur laat nu de echte Docker-versie afzonderlijk vergelijken. Hostacceptatie vereist daarnaast expliciet VARIANT_ID=server. De authentieke gast heeft geen VARIANT_ID en blijft dus geblokkeerd met de concrete melding dat serverbewijs ontbreekt. De onafhankelijke release_support-controle blijft ongewijzigd dicht. Geen servervariant wordt afgeleid uit een label, cloudimagennaam of stil gewijzigde lock.

Veertig gerichte preflight/API-tests slagen, inclusief vastgelegde gastmetadata, verkeerde OS/versie/variant, ontbrekende metadata, misleidend label en duplicaatsleutels. Een afzonderlijke echte gaststart met uitsluitend deze kandidaatfile over het bestaande operatorpackage geeft Python=passed, engine=passed, host=blocked wegens ontbrekend serverbewijs en release_support=blocked. Bootstrap204, herstelpagina beschikbaar, prepare409, geen database- of autoriteitsgebruik. De gast is daarna uitgezet. Bronhash en daadwerkelijk gebruikte kandidaatbytes zijn gelijk.

Dit is een correctie van detectie en foutuitleg; nog geen volledige IN-002-oplevering. Betrouwbaar servervariantbewijs en de bestaande volledige releasevalidatie blijven nodig. Onafhankelijke review staat open. Exacte bron en heropenbaar bewijs staan in ../evidence/product/IN-002-structured-host-detection-2026-09-13.json.
