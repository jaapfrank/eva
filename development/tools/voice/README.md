# Subscription voice transport experiment

This is a bounded development probe, not an enabled EVA channel. It opens an
isolated Codex app-server, uses the existing EVA access lease in memory, requests
WebRTC audio, records a synthetic Dutch reply and closes the session and process.
It does not connect the EVA backend, Home Assistant microphones, or Telegram.

The installed Codex 0.162.1 schema marks `chatgptAuthTokens` **internal-only**.
Current official app-server documentation describes the same mode as experimental
and intended for host apps that own authentication. This documentation/schema
disagreement is recorded; it is not proof that subscription access is prohibited.
The working
session did not report its model. Do not claim parity with ChatGPT voice or deploy
this probe as the household service. `clientManagedHandoffs` does not establish
that automatic Codex backend execution is disabled; that needs separate proof.

Run focused tests without network or credentials:

```sh
python3 -m unittest discover -s development/tools/voice -p 'test_*.py' -v
```

The live probe requires Python with `aiortc` installed, an explicit Codex executable,
and the existing `EvaModelAccessLease/v1` JSON on standard input. Pipe it from the
existing protected lease reader; never paste it into arguments, reports or logs.
Do not create another login. Pass `--audio /private/path/sample.wav` and
`--receipt /private/path/result.json`. The run is bounded to 55 seconds plus
cleanup and uses subscription resources. No microphone is accessed.

The adapter distinguishes the initial RPC acknowledgment from actual voice
startup. Tests cover late startup rejection, missing readiness, event ordering,
credential-safe errors and cancellation cleanup. A decoded audio frame or a
transcript alone is not physical playback or successful interruption.

Evidence: `development/evidence/product/subscription-voice-20261013.json` and the
adjacent `subscription-voice-20261013/` directory. The sample contains generated
speech only. Earlier failed and incomplete probes are preserved under `/tmp/`
for diagnosis; the durable receipts identify the successful adapter run.
