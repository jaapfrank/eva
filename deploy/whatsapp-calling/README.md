# WhatsApp transport with incoming audio calls

One pinned Hypermeow client supplies text and incoming calls for the existing Eva
core. Meowcaller handles the WhatsApp media protocol; the existing Eva voice service
handles speech. No second Eva, work store or speech engine is created. This transport
replaces the unused Baileys process; do not run both clients for this connection.

This is an unofficial interoperability implementation, not Meta's Business Calling
API. Meowcaller is pinned to `c48c3e2a243c672c942cbf0c941d14161720d540`, with its exact
Hypermeow dependency and Go module checksums. Its API/codecs compile and can be tested
without an account. That does not prove a real handset call or permission to operate
an account. Pairing and real incoming duplex audio remain separate acceptance evidence.

Configure the owner's E164 digits before enabling. Pair a **different Eva WhatsApp
account** using the private owner QR. The account's phone is never copied into the
owner-contact setting. Calls from the same account, unknown/unproven callers, groups
and video calls are rejected. One call may run at a time. Voice notes are unsupported
and receive an explicit text response. No outgoing call feature is exposed.
The exact authenticated SDK call offer can prove the caller's alternate phone number
for that same call ID and peer. This metadata is bounded, short lived and consumed
once; it never becomes a second identity store. Absent that evidence, only the SDK's
existing PN/LID mapping is used. An unproven caller is rejected without a number guess.

Environment:

- `EVA_WHATSAPP_CONTROL_TOKEN_FILE`: existing private owner bearer, default
  `/run/secrets/dashboard_access_token`.
- `EVA_WHATSAPP_CORE_URL`: existing Eva core origin, default `http://core:8080`.
- `EVA_WHATSAPP_CALL_MEDIA_URL`: private authenticated PCM WebSocket, default
  `ws://host.docker.internal:44731/call-audio`.
- `EVA_WHATSAPP_DATA_DIRECTORY`: UID65532 mode0700 directory, default
  `/var/lib/eva-whatsapp`. Auth and transport receipts use separate SQLite databases
  within that private directory. Files are mode0600. Preserve the volume.
- `PORT`: private control HTTP port, default8080.

Control RPCs retain `EvaWhatsApp/v1`: bearer-authenticated `GET /status` and POST
`/configure {contact_phone,audio_enabled}`, `/enable {}`, `/disable {}`, `/logout {}`.
Configure disables until explicit enable. `audio_enabled` allows live incoming calls;
it does not enable voice notes. Status also exposes `account_phone`, `call_state`,
`media_ready` and `features:{live_calls:true,voice_messages:false,text:true}`.
`media_ready` comes from the authenticated helper `/ready`, never its liveness alone.
Disable preserves auth; logout clears local auth. QR/session/message contents and raw
SDK errors are not logged. Keep all RPCs and media on the private service network.
An expired, failed or closed pairing channel clears the QR and disables the attempt;
the owner can explicitly connect again. A completed live connection is not cleared by
a delayed callback from an earlier QR attempt.

The call WebSocket uses the same bearer plus `X-Eva-Contact` and `X-Eva-Call-ID`.
Before answering, require JSON:
`{"type":"ready","sample_rate":16000,"channels":1,"encoding":"pcm_s16le"}`.
Then both directions carry binary signed16 little-endian mono16kHz PCM. A binary
message must contain1–9600 samples; queues are bounded. Incoming SDK audio is960
float samples per60ms, converted to signed16. Returned PCM feeds the SDK's real
`PCMStream`. Authority loss, local disable, disconnect, remote end or media failure
closes the bridge and hangs up; no call is redialed automatically.

Text uses the ordinary core respond route and current canonical owner. Original JID
plus provider ID determines the SHA-256 ingress ID. Durable admission precedes core
dispatch; durable outbound claim precedes the single quoted SDK send. Lost receipts
remain uncertain and are not replayed. Background returns use the existing original
source, short summary, current authorization and acknowledgment. A lost core ack after
a confirmed send retries only the idempotent ack. Full results remain on request.

Build with the pinned Dockerfile. `go test ./...` uses synthetic local HTTP/WebSocket
fixtures and real SDK PCM interfaces; no account pairing, WhatsApp message or model
call is made. Run upstream pinned codec/media unit tests separately for their own
evidence. The distroless image runs as65532 and seeds fresh volume ownership correctly.
