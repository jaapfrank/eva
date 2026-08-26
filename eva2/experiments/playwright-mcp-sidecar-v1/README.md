# Eva's eerste afgeschermde browsertaak

Bijgewerkt: 26 augustus 2026.

## Wat nu werkelijk werkt

Eva kan de officiële Microsoft Playwright MCP-browser `0.0.79` gebruiken om
één vooraf goedgekeurde pagina te openen. Daarna leest zij via een tweede,
afzonderlijke browserwaarneming terug welke URL, titel en tekst werkelijk in de
browser stonden.

Dit is niet nagebouwd met een eigen Chromiumlaag. De browser, accessibility-
snapshot en MCP-server komen uit de exact vastgezette Microsoft-image. Eva
voegt alleen haar eigen grenzen toe: de enige toegestane URL, precies twee
toegestane browserhandelingen, een tijd- en bytebudget en de beslissing of het
bewijs exact klopt.

## Wat de echte proef deed

De herhaalbare proef start drie tijdelijke containers op één gatewayloos
Docker-netwerk:

1. een kleine, vaste testpagina;
2. de officiële Playwright MCP-browser;
3. een kleine, secretvrije controleclient die Eva's echte adapter uitvoert.

De browsercontainer:

- draait als gebruiker `node`, niet als root;
- heeft een alleen-lezen hoofdbestandssysteem;
- heeft geen Linux-capabilities en krijgt `no-new-privileges`;
- gebruikt een vluchtig profiel, Chromium-sandbox en vaste CPU-, geheugen- en
  procesgrenzen;
- krijgt geen secrets, Eva-database, Hindsight, gebruikersbestanden of
  workspace;
- heeft geen gepubliceerde poort, netwerkgateway of route naar host en publiek
  internet;
- kan alleen de testserver op dat interne netwerk bereiken.

De controleclient staat bewust in hetzelfde gatewayloze testvak, omdat de host
de browsercontainer evenmin mag bereiken. De proef controleert zowel de lege
gateway als een mislukte hostverbinding. Daarmee kan een toevallig op de
Docker-host luisterende database, API of secretservice niet via het bridgeadres
worden benaderd.

Eva riep uitsluitend `browser_navigate` en `browser_snapshot` aan. Zij zag de
verwachte URL, titel en twee bewijszinnen. Ook stond er expres een kwaadaardig
klinkende webinstructie op de pagina. Die bleef gewone onbetrouwbare tekst en
veroorzaakte geen extra browseractie. De volledige container werd daarna binnen
de gemeten stopgrens verwijderd.

## Eerlijke grens

Hiermee is **de eerste afgeschermde browserleestaak getest**. Dit bewijst nog
niet dat Eva willekeurige websites kan gebruiken. Klikken, downloads,
formulieren, accounts, blijvende profielen, live overname, echte Telegram,
productie-B en crashherstel midden in een externe browseractie blijven open.

De volgende browseroplevering mag pas één van die uitkomsten toevoegen bovenop
deze vaste dependency en isolatie. Er komt geen tweede browserframework naast.

## Herhalen

Na het eenmalig ophalen van de exacte images:

```bash
python3 experiments/playwright-mcp-sidecar-v1/check_candidate.py
python3 experiments/playwright-mcp-sidecar-v1/smoke_candidate.py
```

De smoke gebruikt uitsluitend `--pull never`; tijdens de run wordt niets
gedownload of geïnstalleerd. Alle tijdelijke containers en het tijdelijke
netwerk worden ook na een fout opgeruimd.
