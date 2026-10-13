# Onafhankelijke review — conversationele voice en performance/complexiteit v1

Datum: 25 augustus 2026.

## Scope

- `design/product/conversational-voice-v1.md`
- `design/product/performance-and-complexity-v1.md`
- `evidence/product/performance-complexity-pc00-2026-08-25.json`
- De uitsluitend hiermee samenhangende wijzigingen in status-, oude/nieuwe-,
  concurrentie-, architectuur- en eindacceptatiedocumentatie.

Er is geen runtimecode, container, secret, Home Assistant- of Telegramservice
gewijzigd. De reeds open MC03b-werkboomwijzigingen vallen buiten dit checkpoint.

## Reviewverloop

Twee onafhankelijke reviewers gaven eerst NO-GO.

- Voice miste harde Telegram-initdata/session/WebRTC-grenzen, een media-only
  Home Assistant-route, alle barge-in/toolvensters, auditbare audioretentie en
  voldoende p95-samples.
- Performance gebruikte eerst een niet-bindende dirty-worktree-observatie en
  miste een reproduceerbare source-/OCI-baseline, corpusbinding en concrete
  residentdegradatiegrenzen.

Na correctie gaven beide reviewers GO op voice/security. De architectuurreviewer
reproduceerde PC00 daarna onafhankelijk rechtstreeks uit Git-commit
`35e2c0daab4adf96fe8d80c29565288e66109563`:

- 133 productbestanden;
- 51.928 fysieke regels;
- 2.018.005 bronbytes;
- manifest-SHA256
  `58627232b4cf36b9a505fb593350dbd01a17aec6777079f2b39b30585c8962a5`.

De lokale hoofdreview reproduceerde exact dezelfde waarden en valideerde de
JSON-receipt. `git diff --check` is schoon voor de volledige documentscope.

## Uitkomst

**GO** voor het vastleggen van PC00 en de productgates V01-V07/PC01-PC06.

Dit bewijst nog geen werkende voice-mode, Telegram Mini App, Home Assistant-
voicepad, actuele latency, imagebudget of zeven dagen performance. Het bindt de
vereisten, veiligheidsgrenzen, telmethode en toekomstige pass/failcriteria.

## Primaire externe grens

- Telegram Bot API ondersteunt voiceberichten; `phone.requestCall` is alleen
  voor useraccounts. Daarom is de V1-voorkeur een Telegram Mini App, geen
  native bot-call.
- Home Assistant Assist ondersteunt streaming audiopipelines. Eva stopt de
  route vóór HA-intent en gebruikt HA/Wyoming uitsluitend als media-capability.

Bronnen:

- <https://core.telegram.org/bots/api>
- <https://core.telegram.org/method/phone.requestCall>
- <https://core.telegram.org/bots/webapps>
- <https://developers.home-assistant.io/docs/voice/pipelines/>
- <https://www.home-assistant.io/integrations/wyoming/>
