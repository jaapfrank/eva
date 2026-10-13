# Playwright MCP system-refresh kandidaat

Dit is geen eigen browserplatform. De kandidaat gebruikt de actuele officiële
Microsoft Playwright MCP-release `0.0.79` en past alleen twee gewone
onderhoudsstappen toe:

1. beschikbare Debian-beveiligingsupdates tijdens de kandidaatbuild;
2. verwijderen van de niet gebruikte `npm`/`npx`-pakketbeheerder.

Dit is een vergelijking, geen promotie. De build heeft tijdelijk netwerk nodig
voor Debian-updates, maar de uitkomst wordt lokaal op exact image-ID, package-
inventaris en scannerresultaat vastgelegd. Daarna moeten dezelfde afgesloten
browsercel, SBOM en onafhankelijke review groen zijn. Een HIGH of CRITICAL
bevinding houdt de kandidaat buiten Eva's releasepad.

## Eerste uitkomst — 26 augustus 2026

De kandidaat doorliep dezelfde afgesloten browserleescel als de upstreamimage:
één lokale pagina, geen hosttoegang, geen publiek netwerk en stop binnen 283
ms. De verse, offline gebruikte Trivy-snapshot gaf **7 CRITICAL + 47 HIGH**.
Dat is beter dan zowel upstream (**8 + 69**) als de kandidaat die alleen npm
weghaalt (**7 + 61**), maar Eva's releasegrens is nul hoge of kritieke
bevindingen. Daarom is deze kandidaat ook afgewezen voor promotie.

Het resultaat en de drie daadwerkelijk geüpdatete Debian-pakketten staan in
`evidence/living-core/playwright-mcp-system-refresh-2026-08-26.json`.
