# Standalone Eva2 Telegram transport

`standalone.mjs` runs the existing concurrent poller, Eva2 turn/draft adapter and
photo adapter without importing or starting the legacy conversation agent,
autonomous scheduler or perception loop. Replies remain bound to their original
Telegram message. A private SQLite transport journal preserves original updates,
media interpretation, final answers and delivery uncertainty. It contains no
separate agent memory; Eva2 reads its own existing PostgreSQL conversation.
Uncertain model or delivery attempts are retained and are not automatically replayed.

Configure the existing token and trusted principal-binding files, current Eva2 URL,
a private transport database path and the installed third-party dependency manifest.
The photo provider is an explicit optional provider-only module dependency. The
current installation temporarily retains the original provider implementation and
its existing OAuth/usage journal; this does not start Eva1. Removing that retained
provider dependency is separate from stopping the old agent and must preserve the
ordinary photo route. Voice/audio uploads remain unsupported, as before this switch.

Only one poller may own the bot: the installed standalone service conflicts with
`eva.service`. Before stopping that legacy service, detach its lifecycle dependency
from the existing Eva2 ingress proxy while preserving the proxy configuration.
`/eva1` explains that the old agent has stopped and does not dispatch to either agent.

Run `node --test deploy/telegram-bridge/standalone.test.mjs` for the bounded fake-
transport checks. These checks verify reply binding, concurrency, replay, uncertainty,
authority and the photo handoff; they do not prove a new real Telegram conversation.

## Earlier installed adapter wiring

These are the versioned source assets for the existing Bottie Telegram adapter.
Install the three `src/` files at their corresponding host paths. The existing
draft lifecycle wiring is in `telegram-draft-wiring.patch`; when already applied,
retain it. Apply `telegram-default-private-wiring.patch` to the current host.

Set `EVA2_TELEGRAM_DEFAULT_PRIVATE_IDS` in a separate systemd drop-in to the
comma-separated positive Telegram IDs already bound to the owner in Eva2 and
allowed by the bot. Do not publish those IDs or change the existing credentials
or canary URL. Restart the existing `eva.service` once after source checks and
review. Ordinary text in those private chats goes to Eva2; `/eva2` remains usable.
Other accounts, groups, slash commands and media retain their existing routes.
Media and voice are consequently not part of the default-text delivery scope.

Selection is frozen in the original message archive and reused by the handler
and polling driver. Historical archive inference remains unchanged, including
replayed Eva1 messages. The core authenticates the original sender and retrieves
its own conversation; host Eva1 history is never forwarded as Eva2 context.
There is no automatic Eva1 fallback after uncertain Eva2 processing.

Run `node --test development/tests/support/telegram_default_private.test.mjs
development/tests/support/telegram_draft_lane.test.mjs` from the Eva2 checkout. The actual
host composition test needs `EVA_TELEGRAM_HOST_ROOT` pointing to the host checkout:
`node --test development/tests/support/telegram_default_private_ingress.test.mjs`.
`EVA_TELEGRAM_CANDIDATE_ROOT` optionally selects reviewed candidate source for
the five changed host files. Only model/Telegram transport is replaced there;
archive, middleware, polling, drafts and reply binding use the host implementation.

Record installed source/configuration hashes and live health in the delivery
receipt. Those fake-transport checks do not prove a new real Telegram exchange
or the owner's acceptance. Existing host backups and the separate drop-in provide
the return path without changing the Eva1 database or Eva2 memory.

### Huidige privétoegang en contactgrens

De geïnstalleerde standalone Node-transport gebruikt de vertrouwde
`EvaTelegramPrincipalBindings`-lijst. Een andere huisgenoot krijgt een eigen
principal en uitsluitend de bedoelde chattoegang; `/link` in de afzonderlijke
Python-pairingroute koppelt accounts van dezelfde persoon en is hiervoor onjuist.
Een geaccepteerde privéchat bewijst niet dat het model een andere persoon vanuit
een opdracht kan benaderen: daarvoor is een afzonderlijk aangeboden verzendtool
met ontvangerbinding en afleverreceipt nodig.

Geblokkeerde privéaccounts laten nu maximaal 32 metadataregistraties achter in
`telegram_access_requests` van de bestaande transportdatabase: numeriek account,
voornaam, eventuele gebruikersnaam en ontvangsttijd. Berichtinhoud blijft buiten
opslag en model. Bij een volgende aanvraag worden registraties ouder dan een dag
opgeruimd. Dit geeft geen toegang; namen zijn geen identiteitsbewijs. Een beheerder
kan zo het door het gezin aangewezen account afzonderlijk toelaten. Na aanpassen
van het systemd-credentialbestand wordt uitsluitend de idle Telegram-service
herstart; bestaande onzekere afleveringen worden niet opnieuw uitgevoerd.
