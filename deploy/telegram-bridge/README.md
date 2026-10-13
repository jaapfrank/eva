# Installed Telegram bridge source

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

Run `node --test tests/support/telegram_default_private.test.mjs
tests/support/telegram_draft_lane.test.mjs` from the Eva2 checkout. The actual
host composition test needs `EVA_TELEGRAM_HOST_ROOT` pointing to the host checkout:
`node --test tests/support/telegram_default_private_ingress.test.mjs`.
`EVA_TELEGRAM_CANDIDATE_ROOT` optionally selects reviewed candidate source for
the five changed host files. Only model/Telegram transport is replaced there;
archive, middleware, polling, drafts and reply binding use the host implementation.

Record installed source/configuration hashes and live health in the delivery
receipt. Those fake-transport checks do not prove a new real Telegram exchange
or the owner's acceptance. Existing host backups and the separate drop-in provide
the return path without changing the Eva1 database or Eva2 memory.
