# DF02a — OpenAI SDK-kandidaat

Status op 26 augustus 2026: `reviewed`, niet in productie.
Implementatiecheckpoint: `8526314fde754ba56cbedde4be352f2496ed4910`.

Deze proef vervangt voor één gewone gespreksroute Eva's zelfgeschreven
Responses-transport door de officiële OpenAI Python-SDK. Eva houdt zelf haar
identiteit, prompt, atomische credentiallease, tooltoestemming, uitvoering,
retrybesluit en eindantwoord. De SDK mag alleen één request versturen en het
getypeerde antwoord teruggeven.

## Waarom deze dependency

- officiële, actief onderhouden upstream: `openai-python` `3.3.1`;
- vaste upstream-tagcommit:
  `753ab5c1a81cd85e8bf0aef4c04c51a2e8dae6cd`;
- gebruikt de publieke `OpenAI(...).responses.create(..., stream=True)`-API;
- geen Pydantic-agentloop, automatische tooluitvoering of verborgen retry;
- de bestaande 38 productiepackages blijven exact gelijk;
- de kandidaat voegt uitsluitend `openai==3.3.1`, `jiter==0.16.0` en
  `sniffio==1.3.1` toe.

`openai[realtime]` valt uitdrukkelijk buiten deze proef: die extra botst met
Eva's huidige `websockets==17.0.1`. Voice krijgt daarom een afzonderlijke
dependencybeslissing.

## Reproduceerbare dependencygrens

- productie-lock SHA-256: `89555d14acd8bceb8786dfa8539af29672899e8259e6a2b5352e27ef9219e8dc`;
- kandidaat-roots SHA-256: `d7ec45355c0ea64a588b06bcefd09ddac69490dea5b06782f6e901c2fa34eba9`;
- kandidaat-lock SHA-256: `66d30604d6b818bad41c61ff003a482badc84401b6b4c5d89796a64f38245f02`;
- generator: CPython 3.12, `pip==24.0`, `pip-tools==7.6.1`;
- installatie: een lege venv accepteerde de volledige lock met
  `pip install --require-hashes` en installeerde precies 41 packages.
- compatibiliteit: een tweede lege venv met uitsluitend de ongewijzigde
  productie-lock importeerde Eva zonder `openai` en koos nog steeds standaard
  `legacy_urllib`.

De lock is kandidaatbewijs. Hij is geen runtime-authority, geen managed-B-input
en geen releaseclaim. Productiepromotie vereist nieuwe amd64- en arm64-wheels,
licentie-inventaris, SBOM, kwetsbaarheidsscan, offline image en rollbackbewijs.
`python3 eva2/experiments/openai-sdk-v1/check_candidate.py` controleert de drie
hashes, de exacte 38→41-delta, de bron- en package-policybytes op dat exacte
Git-checkpoint en deze nonclaims fail-closed.

## Bewezen gedrag

De 18 SDK-tests gebruiken een echte lokale HTTP/SSE-provider en controleren:

- tekst en een toolverzoek met gerapporteerd tokengebruik;
- geen automatische tooluitvoering;
- één atomische token/accountrotatie na 401, maar geen verborgen retry bij
  onveranderde credentials, 429 of 500;
- weigering van redirects, proxyomgevingsvariabelen, debuglogging, niet-function
  tools, dubbele/ongeldige toolcalls en ongeldige eindevents;
- begrensde request- en responsebytes, één totale deadline vanaf de eerste
  modelaanroep en gesaneerde fouten;
- een koude production-composition en herhaalde header-/bodytime-outs zonder
  blijvende SDK-threads of bestandsdescriptorgroei;
- optelling van beide SDK-calls bij een Nederlandse taalcorrectie.

Daarnaast bleven 39 bestaande model- en usage-regressies groen. De gerichte
model-, usage- en PostgreSQL-journalrun gaf 63 groene tests. De algemene
package-boundarycheck blijft door
21 reeds aanwezige werkboomschendingen rood; de nieuwe transportmodule zelf
heeft na de gerichte policy-uitbreiding nul boundaryschendingen.

## Nog niet bewezen

- geen echte OpenAI/Codex-providercall;
- geen bewijs dat de private Codex-route exact dezelfde terminal events levert;
- geen latencyvergelijking met het bestaande transport;
- geen productie-lock, image, managed-B-start, A/B-wijziging of voicegebruik.

De eerste onafhankelijke review vond drie echte blockers: de wandklokdeadline
begon te laat, SDK-exceptions konden via hun cause secrets lekken en remote
plain HTTP was toegestaan. De herreview vond daarna dat de koude SDK-import nog
buiten die deadline viel. Checkpoint `8526314` initialiseert de dependency bij
de production-composition en gebruikt vanaf de eerste modelaanroep één absolute
deadline; het bevat ook koude- en herhaalde-resourceregressies. De
onafhankelijke herreview op exact dit checkpoint is groen en staat in
`../../reviews/openai-sdk-transport-candidate-8526314.md`.
