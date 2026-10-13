# Eén veilige openbare browserleesproef

Dit is geen algemene browseragent en geen productie-route. Het bewijst precies
één gebruikersuitkomst: Eva kan een openbare, accountloze pagina lezen zonder
dat de browsercontainer zelf vrij internet krijgt.

De proef gebruikt twee onderhouden afhankelijkheden, niet eigen browser- of
proxycode:

- Microsoft Playwright MCP `0.0.79` als tijdelijke Chromium-hand;
- Canonical `ubuntu/squid` op de gepinde image-digest als enige uitgaande poort.

De browser kent alleen een intern Docker-netwerk zonder gateway. Squid heeft
als enige ook een tweede netwerk, maar laat uitsluitend `example.com:443` toe.
Iedere private bestemming en iedere andere website wordt geweigerd. De browser
mag slechts twee MCP-tools gebruiken: één navigatie naar de vaste URL en één
toegankelijkheidssnapshot. Er zijn geen accounts, cookies, downloads, klikken,
uploads, screenshots, mounts of gepubliceerde poorten.

Voer de herhaalbare proef uit met:

```bash
PYTHONPATH=src:. python3 experiments/playwright-mcp-public-read-v1/smoke_candidate.py
```

Een groene uitkomst bewijst: `https://example.com/` is gelezen, directe
browser-egress naar `1.1.1.1:443` was geblokkeerd, en de proxy gaf `403` voor
`https://www.iana.org/`. Het bewijst nadrukkelijk **niet** dat Eva algemene
webtaken, formulieren, downloads, accounts, meekijken/overnemen of productie-
browserwerk kan uitvoeren.

## Belangrijke onderhoudsuitkomst

De functionele proef is groen, maar de twee gebruikte **huidige** image-digests
zijn op 26 augustus 2026 afgekeurd voor promotie. Een gerichte Trivy-scan vond
in de Squid-proxy drie HIGH-bevindingen met beschikbare fixes; in de
Playwright-sidecar één CRITICAL en 22 HIGH-bevindingen met beschikbare fixes.
Ook de op die dag opgehaalde nieuwste upstream-Playwright-digest had exact
dezelfde telling. Een tag-update alleen is dus geen oplossing.
De strengere herhaalbare releasepoort telt ook bevindingen zonder beschikbare
fix en blokkeerde dezelfde nieuwste digest met 8 CRITICAL en 69 HIGH.
Deze proef mag dus niet als releasebrowser worden hergebruikt. Eerst is een
nieuwe gepinde kandidaat nodig, daarna dezelfde scan én deze functionele proef
opnieuw. De precieze, heropenbare samenvatting staat in
[`browser-dependency-vulnerability-scan-2026-08-26.json`](../../evidence/living-core/browser-dependency-vulnerability-scan-2026-08-26.json).

De Playwright-documentatie beschrijft de ingebouwde `--proxy-server`-optie,
maar zegt ook expliciet dat browser-originfilters geen securitygrens zijn. De
netwerkscheiding en Squid-allowlist zijn daarom de werkelijke grens:
<https://github.com/microsoft/playwright-mcp>.
