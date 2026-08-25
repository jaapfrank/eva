# Onafhankelijke review — dependencykandidaten `2f67277`

Datum: 25 augustus 2026

Reviewer: Curie the 2nd, onafhankelijke verificatie- en releaselead

Beoordeeld checkpoint: `2f672771f72254cb5d8d35263b67eb85653b4446`

## Conclusie

**GO voor dit documentatie- en selectiecheckpoint.** De vier documenten maken
duidelijk onderscheid tussen een bestaande smalle `GO`, een nog afgeschermd te
beproeven kandidaat en een bewuste `NO-GO V1`. Zij claimen niet dat een nieuwe
runtime-, UI-, voice-, browser-, scanner- of skilldependency al is
geïnstalleerd, goedgekeurd of live gebruikt.

## Scope

De review omvatte uitsluitend de vier paden van commit `2f67277`:

- `OPEN_SOURCE_EN_DEPENDENCYKEUZES.md`;
- `PROJECTEN_EN_PLANNING.md`;
- `PROJECT_STATUS.md`;
- `design/product/conversational-voice-v1.md`.

Gecontroleerd zijn versie- en licentieclaims, onderhouds- en
securityvoorbehouden, de grens tussen Eva-eigendom en hergebruik, de
projectstatussen, de voicecorrectie en de interne samenhang. Er is in deze
review geen dependency geïnstalleerd en geen productgedrag getest.

## Vastgestelde selectiegrens

- Hindsight `0.9.1` krijgt alleen `GO` voor Eva's reeds gebruikte smalle
  L2-geheugenrol. PostgreSQL blijft de canonieke autoriteit.
- Renovate krijgt alleen `GO` als voorstelbot met `automerge=false`; het mag
  geen release goedkeuren of rechtstreeks naar de base branch schrijven.
- Onder meer Pydantic AI, Harness, DBOS, de officiële MCP Python SDK, Home
  Assistant MCP Server, Playwright MCP, Hindsight Control Plane, LiveKit,
  OpenClaw, React-Admin, PyInstaller, Agent Skills en Trivy blijven `PROEF`.
- Browser Use en een tweede workflowplatform zijn terecht `NO-GO V1` zolang
  een concreet gat in de smallere voorkeursroute niet is bewezen.

De huidige proefpins zijn als inputs, niet als productgoedkeuring beschreven:
MCP Python SDK `2.1.1`, Playwright MCP `0.0.79`, Harness `0.24.0`, React Admin
`5.15.1`, PyInstaller `6.20.0` en Agent Skills commit `69ef37e…`.

## Primaire-bron- en claimcontrole

- Pydantic AI Harness `0.24.0` accepteert de huidige Pydantic AI-lijn;
  `0.25.0` vereist `pydantic-ai-slim>=2.33.0` en is daarom niet stil gekozen.
- De voiceproef is gecorrigeerd naar Pydantic AI `2.32.2`: die versie biedt de
  coöperatieve realtime run-/sessie-annulering. Hoorbare playbackstop binnen
  250 ms blijft terecht een afzonderlijke Eva-proef en wordt niet aan de
  dependency toegeschreven.
- PyInstaller is exact benoemd als
  `GPL-2.0-or-later WITH Bootloader-exception`. De Agent Skills-code en
  -specificatie zijn Apache-2.0; de afzonderlijke documentlicentie wordt niet
  met de codelicentie verward.
- De Pydantic-telemetryadvisory, de open Home Assistant MCP-schema-issue, de
  OpenClaw-scopeadvisory en de Trivy-supply-chaincompromittering worden als
  onderhouds- en selectiegrenzen genoemd, niet als marketingbewijs.
- De gemeten bronomvangen voor conversation, MCP, Home Assistant en API zijn
  als vervangkansen beschreven. Zij zijn geen beloofde verwijdercijfers.

De controle gebruikte de upstream releases, repositories, advisories en
licentiebronnen die onder `Primaire bronnen` in
`OPEN_SOURCE_EN_DEPENDENCYKEUZES.md` zijn vastgelegd.

## Eigendomsgrens en nonclaims

De dependency-first grens blijft logisch: generieke agent-, protocol-,
workflow-, browser-, audio-, UI- en scanbouwstenen mogen vervangbaar zijn. Eva
blijft eigenaar van identiteit, audience, consent, mandaat, budgetbesluit,
taakbetekenis, call-start, ambigu resultaat, onafhankelijke readback,
persoonsgebonden geheugenregels en het eindantwoord.

Dit checkpoint bewijst niet dat:

- een proefkandidaat compatibel, veilig, sneller of goedkoper is;
- Pydantic AI al Eva's productieagentroute vormt;
- Home Assistant al via MCP pariteit heeft met Eva's directe adapter;
- voice, browser, subagents, dashboard, installatie of updates af zijn;
- een proefpin of transitieve dependency voor een release is toegestaan.

## Eindoordeel

Commit `2f67277` mag als aangescherpt dependencyselectiecontract worden
gebruikt. Werkelijke toelating vereist per kandidaat nog een exacte pin,
afgeschermde verticale proef, privacy- en foutproeven, SBOM/scan,
upgrade/rollback en een nieuw onafhankelijk stop/go-besluit.
