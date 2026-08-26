"""Verify that a V02 local-Wyoming receipt still binds the tested boundary.

The PCM hashes intentionally vary across Piper runs, so this verifier checks
their shape and size rather than pretending they are a stable golden audio
fixture.  It never starts Docker or contacts a voice endpoint.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
RECEIPT = ROOT / "receipt.json"
RUNNER = ROOT / "run_pipeline.py"
LOCK = ROOT / "requirements.lock"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
INPUT_PHRASE_SHA256 = "aeb87444df7cbb93a2737b13fc9b6c9f5ca2833ad505aa443b7bc981d1881bfd"
REPLY_PHRASE_SHA256 = "82a7c6e0cade94072c48184837393691c2838111ddaff64e7f6e778a30b33d22"
PIPER_IMAGE = (
    "rhasspy/wyoming-piper@sha256:"
    "6b837615b543e38587b735b72abad2dfd7ead50fd10ec1a4d5815241e5539377"
)
WHISPER_IMAGE = (
    "rhasspy/wyoming-whisper@sha256:"
    "731299c56df053dd67978127a44e417c83f0d900fdaad1bd7ab8aad82f84106d"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pcm(raw: object, *, name: str) -> None:
    if not isinstance(raw, dict):
        raise SystemExit(f"{name} PCM is not an object")
    if raw.get("rate") != 22_050 or raw.get("width") != 2 or raw.get("channels") != 1:
        raise SystemExit(f"{name} PCM format changed")
    if not isinstance(raw.get("bytes"), int) or raw["bytes"] <= 0:
        raise SystemExit(f"{name} PCM byte count is invalid")
    if not isinstance(raw.get("sha256"), str) or SHA256.fullmatch(raw["sha256"]) is None:
        raise SystemExit(f"{name} PCM hash is invalid")


def main() -> None:
    raw = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if raw.get("candidate") != "V02-wyoming-local-voice-v1":
        raise SystemExit("unexpected voice receipt candidate")
    controller = raw.get("controller")
    if not isinstance(controller, dict):
        raise SystemExit("missing controller binding")
    if controller != {
        "requirementsSha256": _sha256(LOCK),
        "runnerSha256": _sha256(RUNNER),
        "wyoming": "1.10.0",
        "pydantic": "2.12.5",
    }:
        raise SystemExit("controller package or source binding changed")
    if raw.get("sidecarsExpected") != {
        "piperImage": PIPER_IMAGE,
        "piperUpstream": "OHF-Voice/wyoming-piper@v2.4.2",
        "whisperImage": WHISPER_IMAGE,
        "whisperUpstream": "OHF-Voice/wyoming-faster-whisper@v3.6.0",
        "whisperModel": "base-int8",
    }:
        raise SystemExit("sidecar binding changed")
    input_data = raw.get("input")
    if not isinstance(input_data, dict) or input_data.get("voice") != "nl_NL-pim-medium":
        raise SystemExit("Piper voice binding changed")
    if input_data.get("phraseSha256") != INPUT_PHRASE_SHA256:
        raise SystemExit("sealed input phrase binding changed")
    _pcm(input_data.get("pcm"), name="input")
    admission = raw.get("voiceAdmission")
    if admission != {
        "contract": "VoiceTurn/v1",
        "schemaVersion": 1,
        "transcriptState": "final",
        "audience": "private",
        "language": "nl",
        "finalTranscriptSha256": INPUT_PHRASE_SHA256,
    }:
        raise SystemExit("Eva VoiceTurn admission binding changed")
    reply = raw.get("reply")
    if not isinstance(reply, dict) or reply.get("phraseSha256") != REPLY_PHRASE_SHA256:
        raise SystemExit("sealed reply phrase binding changed")
    _pcm(reply.get("pcm"), name="reply")
    print("V02 local Wyoming receipt is structurally and source-bound valid.")


if __name__ == "__main__":
    main()
