# Managed Living Core B — hostcontract

Status: ontwerp- en preflightbasis. Dit document is **geen** deployinstructie.
De huidige B blijft onaangeraakt totdat een afzonderlijk gereviewde adoptierun
de volledige manifestset en live gates bewijst.

## Eén toekomstige manifestset

De toekomstige Telegram-first B bestaat uit één zelfstandige manifestset:

`schema-installer → model-access-lease + hindsight-access-lease → hindsight → core → durable-goal-worker + telegram-ingress`

Home Assistant is een expliciete core-overlay als fysieke readback is
ingeschakeld. AgentMail, Stripe, proactieve Telegram, browser/gateway en skills
komen niet automatisch mee bij deze eerste adoptie.

Gebruik nooit `stack.compose.yml` of `hindsight.compose.yml` als bron voor de
adoptie: zij bevatten onveilige `secrets:file`-routes en overlappende,
incompatibele Hindsight-definities.

## Verplichte hostcontracts

| Onderdeel | Contract |
|---|---|
| B-envbestand | Absolute reguliere niet-symlink, maximaal 64 KiB, operator-eigendom en zonder group/other-rechten. |
| DSN, Home Assistant-token, Telegram-token en Telegram-bindings | Afzonderlijke reguliere niet-symlink files, UID `65532`, owner-readable, geen group/other-rechten, read-only bind met `create_host_path: false`; de preflight controleert alleen deze metadata, nooit bytes. |
| Codex-authority | Directory `1000:1000`/`0700`; `auth.json` privé en read-only gemount. Eva leest deze bron nooit rechtstreeks. |
| Eva-modellease | Directory `1000:65532`/`02750`; lease één link, `1000:65532`/`0640`. |
| Hindsight-lease | Directory `1000:1000`/`0700`; lease één link, `1000:1000`/`0600`. |
| Volumes | PostgreSQL en workspace hebben exact gepinde namen; de Hindsight-naam is een verplichte, verzegelde B-configuratiewaarde en wordt pas bij adoptie read-only tegen de bestaande data/back-up gecontroleerd. Nooit `down -v`. |

De preflight vereist nu ook de gepinde `EVA_STACK_WORK_VOLUME`,
`EVA_CODEX_AUTH_DIR` en `EVA_HINDSIGHT_ACCESS_DIR`.

## PostgreSQL-bootstrapbasis

PostgreSQL leest `POSTGRES_PASSWORD_FILE` zelf. Dat valt niet onder Eva's
fileloader. `postgres-bootstrap.compose.yml` bindt daarom een vooraf
geprovisioneerd root-owned `0600`-bestand direct en read-only; Docker
`secrets:file` wordt niet gebruikt. De afzonderlijke contractproef voert de
gepinde upstream-image uit met een verse én herstartte tijdelijke volume en
bewijst dat de initiële waarde werkt en een bestaande database niet reset.

Dit is alleen de PostgreSQL-component. Een volledige managed-stackclaim blijft
geblokkeerd totdat de zelfstandige manifestset en een adoptierun zijn
gereviewd en echt uitgevoerd.
