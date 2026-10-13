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
weigert starten zonder drie expliciete private bronbestanden.

De volgende geïsoleerde proef koppelde de exact gepinde upstream
`cfex-mcp-fast-time-server` als synthetische provider. ContextForge ontdekte
zijn catalogus en Eva bond naam, invoerschema, toestand en zichtbaarheid aan
één vaste hash. Eva voerde een echo-opdracht uit en las de uitkomst via een
tweede gatewaycall onafhankelijk terug. PostgreSQL 16.13 bewaarde de
`verified` receipt; een nieuw Eva-object gaf na herstart dezelfde receipt terug
met nul nieuwe providercalls. De proef gebruikte alleen een intern
Docker-netwerk; een tijdelijke loopbackpoort was uitsluitend voor diagnose.

Docker MCP Gateway `v0.43.3` blijft de reservekandidaat. Zijn releasebinary is
bytegelijk aan GitHubs gepubliceerde SHA-256 en biedt sterke container- en
toollimieten, maar de release is nog als prerelease gemarkeerd en de huidige
offline scan kon de Go-modules uit de losse binary niet betrouwbaar
inventariseren. Daarom krijgt hij nu geen productievoorkeur.

ContextForge krijgt evenmin algemene autoriteit over Eva-personen. De open
upstream-RBAC-kwestie `#6015` blokkeert een direct multi-usergatewaydashboard.
De eerste integratieproef is eigenaar-only; Eva controleert iedere
persoonsbevoegdheid zelf vóór een gatewaycall.

Dit is nog geen live promotie. De algemene gatewaygrens is nu wel echt bewezen;
de volgende gate zet een echte niet-huisgebonden provider achter dezelfde route
in de gebruikersflow. Pas daarna kan de tijdelijke Python-entry-pointlijm
worden verwijderd. Home Assistant is hierbij slechts één mogelijke toolserver,
geen onderdeel van Eva's kern.
