# Managed Living Core B — hostcontract

Status: ontwerp- en preflightbasis. Dit document is **geen** deployinstructie.
De huidige B blijft onaangeraakt totdat een afzonderlijk gereviewde adoptierun
de volledige manifestset en live gates bewijst.

## Eén toekomstige manifestset

De toekomstige Telegram-first B bestaat uit één zelfstandige manifestset:

`postgres + hindsight-postgres → schema-installer + model-access-lease + hindsight-access-lease → hindsight → core → durable-goal-worker + telegram-ingress`

De operationele waarheid en Hindsight hebben dus ieder een eigen PostgreSQL-
database en volume. Hindsight gebruikt geen ingebouwde database meer.
De embeddingprovider blijft bij adoptie `local`: het bestaande geheugen bevat
384-dimensionale vectoren en mag niet stil worden verwijderd of met een ander
model opnieuw worden geïnterpreteerd. De twee lokale modelcaches zijn onderdeel
van de gepinde image; Hugging Face en Transformers staan voor die caches
expliciet offline en sturen geen telemetrie. De minimale image gebruikt
expliciet UTC en hoeft daardoor geen hosttijdzonebestand te raden.
Hindsight blokkeert zijn lokale opstart niet op de ingebouwde, mogelijk vijf
minuten durende externe LLM-proef. De provider wordt na readiness met een
aparte begrensde geheugenhandeling gecontroleerd; een externe storing houdt zo
de lokale geheugen-API en bestaande herinneringen niet onbereikbaar.

Home Assistant is een expliciete core-overlay als fysieke readback is
ingeschakeld. AgentMail, Stripe, proactieve Telegram, browser/gateway en skills
komen niet automatisch mee bij deze eerste adoptie.

De providerconfiguratie van de duurzame-doelenworker activeert de gesprekstool
niet automatisch. Alleen wanneer dezelfde echte provider en ontvangers apart
als `EVA_DURABLE_GOAL_ADMISSION_PROVIDER` en
`EVA_DURABLE_GOAL_ADMISSION_RECIPIENTS` zijn vrijgegeven, mag de kern zo'n
afspraak aannemen. Een voorbereide proefprovider blijft daardoor onzichtbaar
voor gebruikersgesprekken.

Gebruik nooit `stack.compose.yml` of `hindsight.compose.yml` als bron voor de
adoptie: zij bevatten onveilige `secrets:file`-routes en overlappende,
incompatibele Hindsight-definities.

## Verplichte hostcontracts

| Onderdeel | Contract |
|---|---|
| B-envbestand | Absolute reguliere niet-symlink, maximaal 64 KiB, operator-eigendom en zonder group/other-rechten. |
| Eva-DSN, Home Assistant-token, dashboardtoegangstoken, Telegram-token, Telegram-bindings en Telegram-koppelsecret | Afzonderlijke reguliere niet-symlink files, UID `65532`, owner-readable, geen group/other-rechten, read-only bind met `create_host_path: false`; de preflight controleert alleen deze metadata, nooit bytes. |
| Hindsight-DSN | Reguliere niet-symlink file, UID `1000`, owner-readable, geen group/other-rechten. Alleen `HINDSIGHT_API_DATABASE_URL_FILE` staat in Docker-configuratie; een inline database-URL is verboden. |
| PostgreSQL-wachtwoorden | Twee afzonderlijke root-owned `0600`-files: één voor Eva en één voor Hindsight. |
| Codex-authority | Directory `1000:1000`/`0700`; `auth.json` privé en read-only gemount. Eva leest deze bron nooit rechtstreeks. |
| Eva-modellease | Directory `1000:65532`/`02750`; lease één link, `1000:65532`/`0640`. |
| Hindsight-lease | Directory `1000:1000`/`0700`; lease één link, `1000:1000`/`0600`. |
| Volumes | Eva-PostgreSQL, Hindsight-PostgreSQL en workspace hebben exact gepinde namen. Een echte adoptie controleert eerst de bestaande data en back-up. Nooit `down -v` bij productie. |
| Images | Core, beide PostgreSQL-diensten en Hindsight gebruiken uitsluitend lokaal aanwezige `repository@sha256`-verwijzingen met `pull_policy: never`. |

De preflight vereist ook de gepinde volumes, alle drie image-digests,
`EVA_CODEX_AUTH_DIR` en `EVA_HINDSIGHT_ACCESS_DIR`.

## Verzegenen vóór renderen

`EVA_B_MANAGED_MANIFEST_SHA256` is de exacte SHA-256 van
`managed-b.compose.yml`. De renderer weigert een template die daarvan afwijkt.
De private B-configuratie bevat **niet** zijn eigen hash: die zou
self-referential zijn. De renderer berekent één hash van de exacte
configuratie-snapshot en geeft die alleen aan het Compose-proces mee als
`EVA_B_CONFIGURATION_SHA256`.

Een render gebruikt uitsluitend de gepinde Docker-binary, configdirectory,
context, projectnaam, een nieuw private `--env-file` uit de al gevalideerde
in-memory snapshot en dit vaste manifest. Hij opent het oorspronkelijke
B-envbestand dus niet opnieuw. Hij voert
alleen `docker compose ... config --format json` uit, start of vervangt niets,
en weigert onder andere een afwijkende servicegraph, ontbrekende labels,
onopgeloste interpolatie, `env_file` en Docker-secrets. De receipt bevat alleen
de drie hashes, Compose-versie, project en servicenamen — nooit configuratie- of
secretwaarden.

## PostgreSQL-bootstrapbasis

PostgreSQL leest `POSTGRES_PASSWORD_FILE` zelf. Dat valt niet onder Eva's
fileloader. `postgres-bootstrap.compose.yml` bindt daarom een vooraf
geprovisioneerd root-owned `0600`-bestand direct en read-only; Docker
`secrets:file` wordt niet gebruikt. De afzonderlijke contractproef voert de
gepinde upstream-image uit met een verse én herstartte tijdelijke volume en
bewijst dat de initiële waarde werkt en een bestaande database niet reset.

De tijdelijke kandidaatproef heeft de negen-dienstengrafiek gerenderd, beide
databases gestart, het schema geïnstalleerd, een echte Codex-beurt uitgevoerd
en database, Hindsight en core samen herstart. Dit bewijst de kandidaatgrafiek,
niet de productieadoptie. Een gecontroleerde data-overdracht, A/B-omschakeling
en terugrol op de echte B blijven afzonderlijke gates.
