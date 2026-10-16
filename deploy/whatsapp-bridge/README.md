# Optional WhatsApp transport

This process connects one owner-authorized WhatsApp account to the existing Eva core.
It is disabled by default. It does not contain an agent, memory, work planner or voice
engine. Enable and scan the private QR in the owner dashboard. Without a configured
contact, the authenticated linked account's phone number becomes the allowed self-chat;
an explicitly configured contact is never replaced. Groups, history imports, status
messages and other contacts are excluded.

Required environment:

- `EVA_WHATSAPP_CONTROL_TOKEN_FILE`: mounted private dashboard bearer file.
- `EVA_WHATSAPP_CORE_URL`: core origin, default `http://core:8080`.
- `EVA_WHATSAPP_DATA_DIRECTORY`: private UID65532 directory, default `/var/lib/eva-whatsapp`.
- `PORT`: control port, default `8080`; keep it on the private service network.

Every control RPC requires the bearer. `GET /status` returns the owner-only connection
status and QR. POST `/enable`, `/disable`, `/logout` take `{}`. POST `/configure` takes
`{contact_phone,audio_enabled}` and disables the connection until explicitly enabled.
Configure requires 7–15 E164 digits without a plus. Disable keeps the session; logout
clears local session keys. QR, tokens, messages and credentials are never logged.

The SQLite volume stores Baileys credentials/key state and transport source/delivery
receipts. Its directory is mode0700 and database mode0600.
The image runs as UID65532:GID65532, matching the existing core secret owner. The image
seeds a fresh data volume with that same owner; an existing volume must retain its
private runtime ownership. The bridge refuses a directory owned by a different UID.

Original remote JID plus provider message ID determines the SHA-256 source ID. Admission and outbound claims
are durable before dispatch. An uncertain call or delivery is not replayed, including
after restart; check the existing Eva task view before repeating a request. Replies
quote the original message key. Core authorization and local settings are rechecked
before sending. Background results use the core's short summary and exact original
source, with authorization before delivery and acknowledgment afterward.

Optional audio uses a bounded download and ffmpeg conversion to the existing core's
PCM transcription route; its existing synthesis route returns encoded audio converted
to Ogg Opus. Source-audio and transcript hashes are retained in transport evidence.
Audio defaults off. No separate speech service is implemented here.

`npm ci --ignore-scripts` installs the locked Baileys7.0.0-rc14 dependencies.
`npm test` uses synthetic sockets/core and a local HTTP fixture, the actual SDK auth
codec and actual local ffmpeg. It does not pair an account or send an external message.
These tests establish transport behavior; real pairing and same-Eva use require
separate installed evidence and cannot be inferred from the tests.
