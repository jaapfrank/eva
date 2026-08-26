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

De Playwright-documentatie beschrijft de ingebouwde `--proxy-server`-optie,
maar zegt ook expliciet dat browser-originfilters geen securitygrens zijn. De
netwerkscheiding en Squid-allowlist zijn daarom de werkelijke grens:
<https://github.com/microsoft/playwright-mcp>.
