# C3.1 installed audio corrections — 1 October 2026

Outcome: remove the observed interrupted speech input and unconfirmed native playback from the existing Woonkamer continuous route. Physical microphone conversation and subjective intelligibility remain open.

## Independent review

/root/open_questions_triage reviewed root-authored shared empty-STT handling and bounded Python output: GO after the two-second realtime prefetch correction. The earlier unpaced window was not installed. Empty speech is recoverable only after admitted STT start; authority and complete segment consumption remain required.

The same agent authored the firmware EOF changes. Root independently reviewed those changes: GO. EOF appends eight zero input frames required by the pinned esp-audio-libs 3.2.1 sixteen-tap 24-to-48 kHz FIR contract, retries only unwritten padding, requests graceful speaker finish, and still requires real played-frame callbacks plus the existing physical stop barrier. Padding changes no accepted answer or priming counts. STOP, connection retirement and later generations invalidate old work.

## Focused and actual-route evidence

- Actual packaged phone media/connector/stream suite: 71 passed, 6.56 s. Local older connector data contract retained; no unrelated connector migration.
- Wire/LiveKit focused tests: 34 passed, 4.19 s. Ordered cumulative receipts, four-packet bound, finite realtime prefetch, STOP during pending receipts/pacing and existing playback barriers.
- Final pure C++ protocol test with warnings-as-errors and UBSan:passed. Actual prepared driver/drain host bounds passed in 1.79 s, including partial tail writes, STOP inside tail play, stale callback and later generation; Python-to-actual-driver wire verification also passed.
- Installed HA STT:two actual empty recognitions on the same continuing stream, no transcript or exception.
- Actual Woonkamer: 3 s PCM, 72000 frames completed in 3.428 s; feed 2.391 s versus reference ACK wait total 8.658 s. Existing HA TTS 108864 frames completed. Physical wire STOP 0.203 s then next 4800-frame answer completed.

Only two speaker output source files changed in the existing pinned wrapper. Original input, device credentials, CA, provider, config hash and native audio settings retained. Firmware OTA SHA256 b13db634acfec61c4d4772b063e7180f68a3350fd650a66027de30854efd557c authenticated after upload, compile 2026-10-01 18:28:29 +0200. Current speaker image sha256:b8001798c65f79e44b5a659ea7295103a633f63f03efc39106114bcc8208894f; phone sha256:2e9092a057dbcaa089bc91b00f27c9abd7feff429257d54f7a90a642917e69f6. Compose changes only those images. Earlier firmware, images and private settings retained; no rollback performed.

## Limits and disposition

GO for these installed corrections. Output-only checks and explicit wire STOP do not prove physical microphone barge-in, audible quality or full C3/C4. Faster PCM transport does not prove a faster model answer. Earlier failed measurements remain recorded; the EOF-only candidate still had an 8-frame deficit, which justified the source-derived tail correction and second targeted upload. No broad test or installation loop.

No Eva model call or house action. Three actual STT and two actual TTS verification requests; development and provider credit costs unknown. Owner acceptance and existing product counts unchanged. See evidence/product/c3-audio-stutter-fix-20261001.json.
