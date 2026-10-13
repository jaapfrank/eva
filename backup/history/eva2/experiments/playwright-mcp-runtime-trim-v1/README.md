# Playwright MCP runtime-trim kandidaat

Deze kandidaat houdt de officiële Microsoft Playwright MCP-server en Chromium
ongewijzigd. Hij verwijdert uitsluitend `npm` en `npx`: dat zijn
build-/pakketbeheertools die de Eva-sidecar niet aanroept, maar die wel de
kritieke npm-`tar`-melding meebrengen.

De kandidaat is geen release en vervangt niets. Promotie vereist allemaal:

1. build met het netwerk uit;
2. dezelfde afgesloten browserlees- en klikproef als de upstream-image;
3. een nieuwe Trivy-scan met een vastgezette advisoriesnapshot;
4. een exact image-digest, SBOM en onafhankelijke review.

Als een van deze punten faalt, blijft de upstream-image de enige bewezen
functionele kandidaat en blijft de browserroute geblokkeerd voor release.

## Uitkomst van de eerste proef — 26 augustus 2026

De kandidaat doorliep dezelfde afgesloten browserleescel: precies één lokale
pagina, geen publiek netwerk of hosttoegang, geen gepubliceerde poorten en een
stop binnen 245 ms. Met één verse Trivy-advisorydatabase verdwenen alle
Node/NPM-bevindingen. De totale score ging van **8 kritiek + 69 hoog** voor de
upstreamimage naar **7 kritiek + 61 hoog** voor deze kandidaat.

Dat is een meetbare verbetering, maar niet voldoende voor Eva: er blijven
zeven kritieke en 61 hoge bevindingen uit de gezamenlijke Debian-/browserbasis
over. De kandidaat is daarom **afgewezen voor promotie**. Dit betekent alleen
dat deze ene verkleining niet veilig genoeg is; de browserdoelstelling loopt
door via een beter onderhouden basis of provider. De volledige, hashgebonden
receipt staat in `evidence/living-core/playwright-mcp-runtime-trim-2026-08-26.json`.
