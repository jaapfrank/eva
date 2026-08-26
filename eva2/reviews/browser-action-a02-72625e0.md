# Onafhankelijke review — eerste duurzame browseractie

Datum: 26 augustus 2026

Broncheckpoint: `72625e05483a7ce0c97952697c174968feb1dcbb`

Oordeel: **GO voor één lokaal geteste browseractie; NO-GO voor product, B of live gebruik**

## Wat onafhankelijk opnieuw is uitgevoerd

De reviewer maakte een schone Git-archive van het exacte checkpoint en draaide
de kandidaat buiten de vuile werkboom opnieuw. Alleen de tijdelijke archiveroot
kreeg mode `0755`, zodat test-UID `65532` de bindmount kon doorlopen.

- 37 van 37 connectorgevallen groen;
- Ruff groen;
- 8 van 8 bronhashes gelijk aan de receipt;
- 3 van 3 echte scenario's groen met een disposable PostgreSQL en de gepinde
  Microsoft Playwright MCP-/Chromium-image;
- nul achterblijvende eigen containers of netwerken.

## Wat de drie scenario's bewijzen

1. De normale route klikte exact één vaste knop, las de servertoestand via een
   afzonderlijke route terug en bereikte pas daarna `STATE_VERIFIED`.
2. Een provider-subprocess dat vóór effect-I/O niet-nul eindigde veroorzaakte
   nul POST-verzoeken. PostgreSQL bewaarde `ambiguous`; hervatten riep de
   provider niet opnieuw aan.
3. Een provider-subprocess dat ná effect-I/O niet-nul eindigde veroorzaakte
   precies één POST en één toestandsovergang. PostgreSQL bewaarde `ambiguous`;
   hervatten klikte niet opnieuw.

## Harde bewijsgrens

Dit is één vaste, credentialvrije lokale browseractie. Het is geen bewijs voor
publieke websites, vrije formulieren, accounts, downloads, gebruikersovername,
Telegram, managed B of algemeen computergebruik. De foutproeven zijn echte
niet-nul eindigende subprocessvensters, geen SIGKILL van controller of browser.
Automatische reconciliatie, crash-na-readback en product-secretisolatie blijven
open. De passwordloze test-DSN bestaat uitsluitend in de disposable
testcontroller.

## Heropenbaar bewijs

- `evidence/living-core/browser-a02-local-action-2026-08-26.json`
- `experiments/playwright-mcp-action-v1/smoke_candidate.py`
- `tests/connectors/test_playwright_mcp_action.py`
- `tests/postgres/test_playwright_mcp_action_chain.py`
