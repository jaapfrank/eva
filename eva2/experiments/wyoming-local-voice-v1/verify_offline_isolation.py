"""Recheck the committed facts of the no-egress local voice proof.

This verifier does not start Docker.  It checks the receipt's source bindings,
the exact internal-network constraints and the final private VoiceTurn shape.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
RECEIPT = ROOT / "offline-isolation-receipt.json"
RUNNER = ROOT / "run_pipeline.py"
LOCK = ROOT / "requirements.lock"
CAPTURE = ROOT / "capture_offline_isolation.py"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
INPUT_PHRASE_SHA256 = "aeb87444df7cbb93a2737b13fc9b6c9f5ca2833ad505aa443b7bc981d1881bfd"
REPLY_PHRASE_SHA256 = "82a7c6e0cade94072c48184837393691c2838111ddaff64e7f6e778a30b33d22"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pcm(raw: object, *, name: str) -> None:
    if not isinstance(raw, dict) or raw.get("rate") != 22_050:
        raise SystemExit(f"{name} PCM rate is invalid")
    if raw.get("width") != 2 or raw.get("channels") != 1:
        raise SystemExit(f"{name} PCM format is invalid")
    if not isinstance(raw.get("bytes"), int) or raw["bytes"] <= 0:
        raise SystemExit(f"{name} PCM byte count is invalid")
    if not isinstance(raw.get("sha256"), str) or SHA256.fullmatch(raw["sha256"]) is None:
        raise SystemExit(f"{name} PCM hash is invalid")


def main() -> None:
    raw = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if raw.get("candidate") != "V02-wyoming-local-offline-isolation-v1":
        raise SystemExit("unexpected offline voice candidate")
    if raw.get("captureScriptSha256") != _sha256(CAPTURE):
        raise SystemExit("offline receipt does not bind the capture checker")
    if raw.get("network") != {
        "name": "eva2-v02-offline-net-20260826",
        "internal": True,
        "hostPublishedPorts": False,
        "internetGateway": False,
    }:
        raise SystemExit("offline network boundary changed")
    containers = raw.get("containers")
    if not isinstance(containers, dict):
        raise SystemExit("offline container record is invalid")
    for name in ("piper", "whisper"):
        sidecar = containers.get(name)
        if not isinstance(sidecar, dict):
            raise SystemExit(f"missing {name} sidecar")
        if sidecar.get("readOnlyRoot") is not True or sidecar.get("modelVolumeReadOnly") is not True:
            raise SystemExit(f"{name} is not read-only")
        if sidecar.get("hostMounts") is not False or sidecar.get("publishedHostPorts") is not False:
            raise SystemExit(f"{name} exposes host data or a host port")
    if containers.get("controller") != {
        "image": "eva2-v02-wyoming-controller:81f18f4",
        "sourceCommit": "81f18f4341e7b5356065bf884b454968a955c2e5",
        "readOnlyRoot": True,
        "allCapabilitiesDropped": True,
        "noNewPrivileges": True,
        "publishedHostPorts": False,
        "hostMounts": False,
        "receiptVolumeOnly": True,
        "exitCode": 0,
    }:
        raise SystemExit("offline controller boundary changed")
    pipeline = raw.get("pipeline")
    if not isinstance(pipeline, dict) or pipeline.get("candidate") != "V02-wyoming-local-voice-v1":
        raise SystemExit("missing local voice pipeline")
    if pipeline.get("controller") != {
        "requirementsSha256": _sha256(LOCK),
        "runnerSha256": _sha256(RUNNER),
        "wyoming": "1.10.0",
        "pydantic": "2.12.5",
    }:
        raise SystemExit("local voice controller binding changed")
    input_data = pipeline.get("input")
    if not isinstance(input_data, dict) or input_data.get("voice") != "nl_NL-pim-medium":
        raise SystemExit("Piper voice binding changed")
    if input_data.get("phraseSha256") != INPUT_PHRASE_SHA256:
        raise SystemExit("input phrase binding changed")
    _pcm(input_data.get("pcm"), name="input")
    reply = pipeline.get("reply")
    if not isinstance(reply, dict) or reply.get("phraseSha256") != REPLY_PHRASE_SHA256:
        raise SystemExit("reply phrase binding changed")
    _pcm(reply.get("pcm"), name="reply")
    if pipeline.get("voiceAdmission") != {
        "contract": "VoiceTurn/v1",
        "schemaVersion": 1,
        "transcriptState": "final",
        "audience": "private",
        "language": "nl",
        "finalTranscriptSha256": INPUT_PHRASE_SHA256,
    }:
        raise SystemExit("final private VoiceTurn binding changed")
    print("V02 local voice offline-isolation receipt is source-bound valid.")


if __name__ == "__main__":
    main()
