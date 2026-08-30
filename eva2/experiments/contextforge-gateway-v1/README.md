# Algemene MCP-gatewaykandidaat

Externe diensten zijn tools, geen Eva-onderdelen. Deze proef onderzoekt daarom
één onderhouden MCP-gateway die toolservers kan registreren, begrenzen en via
één standaardprotocol kan aanbieden. Eva houdt alleen haar unieke grens voor
persoon, toestemming, risico, duurzaam bewijs en controle achteraf.

De actuele eerste kandidaat is IBM ContextForge `v1.0.8`. De exact gepinde
amd64-image is Apache-2.0, draait als gebruiker `10001:10001` en is lokaal met
de gepinde Trivy-poort op nul hoge en nul kritieke bevindingen gescand. Een
afgesloten proef startte op een alleen-lezen bestandssysteem; `/health` gaf 200
en `/tools` en `/admin` gaven zonder token beide 401.

De upstreamimage accepteert zijn JWT-, encryptie- en eerste beheersleutel niet
rechtstreeks als file-secret. `start-contextforge.sh` is daarom uitsluitend
deploymentlijm: hij weigert links, een verkeerde eigenaar, andere rechten dan
`0400`, regeleinden en onbegrensde bestanden, laadt de drie waarden in het
proces en start daarna ongewijzigd de upstreamentrypoint. Hij implementeert
geen MCP, registry, authenticatie, tooluitvoering of dashboard.

De Compose-kandidaat is standaard uitgeschakeld, publiceert geen poort, kan
niet naar internet, draait niet als root, heeft geen extra capabilities en
weigert starten zonder drie expliciete private bronbestanden. Hij is nog niet
aan Eva of een echte toolprovider gekoppeld.

Docker MCP Gateway `v0.43.3` blijft de reservekandidaat. Zijn releasebinary is
bytegelijk aan GitHubs gepubliceerde SHA-256 en biedt sterke container- en
toollimieten, maar de release is nog als prerelease gemarkeerd en de huidige
offline scan kon de Go-modules uit de losse binary niet betrouwbaar
inventariseren. Daarom krijgt hij nu geen productievoorkeur.

ContextForge krijgt evenmin algemene autoriteit over Eva-personen. De open
upstream-RBAC-kwestie `#6015` blokkeert een direct multi-usergatewaydashboard.
De eerste integratieproef is eigenaar-only; Eva controleert iedere
persoonsbevoegdheid zelf vóór een gatewaycall.

Dit is een kandidaatbesluit, geen live promotie. De volgende gate koppelt één
synthetische MCP-toolserver via deze gateway aan Eva's bestaande generieke
toolcontract en bewijst catalogus, call, readback, herstart en verwijdering van
overlappende eigen plug-inlijm.
