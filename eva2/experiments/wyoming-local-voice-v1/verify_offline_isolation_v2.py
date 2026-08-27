"""Verify the corrected, source-bound no-egress local-voice receipt."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
RECEIPT = ROOT / "offline-isolation-v2-receipt.json"
CAPTURE = ROOT / "capture_offline_isolation_v2.py"
RUNNER = ROOT / "run_pipeline.py"
LOCK = ROOT / "requirements.lock"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
INPUT = "aeb87444df7cbb93a2737b13fc9b6c9f5ca2833ad505aa443b7bc981d1881bfd"
TOKENIZER = "27fc476bfe7f17299480be2273fc0608e4d5a99aba2ab5dec5374b4482d1a566"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pcm(value: object, *, label: str) -> None:
    if not isinstance(value, dict):
        raise SystemExit(f"{label} PCM is absent")
    if value.get("rate") != 22_050 or value.get("width") != 2 or value.get("channels") != 1:
        raise SystemExit(f"{label} PCM format differs")
    if not isinstance(value.get("bytes"), int) or value["bytes"] <= 0:
        raise SystemExit(f"{label} PCM bytes differ")
    if not isinstance(value.get("chunks"), int) or value["chunks"] <= 0:
        raise SystemExit(f"{label} PCM chunks differ")
    if not isinstance(value.get("sha256"), str) or SHA256.fullmatch(value["sha256"]) is None:
        raise SystemExit(f"{label} PCM digest differs")


def main() -> None:
    raw = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if raw.get("candidate") != "V02-wyoming-local-offline-isolation-v2":
        raise SystemExit("offline candidate differs")
    if raw.get("captureScriptSha256") != _sha256(CAPTURE):
        raise SystemExit("capture source binding differs")
    if raw.get("pipelineReceiptSha256") != _sha256(ROOT / "receipt.json"):
        raise SystemExit("pipeline receipt binding differs")
    if raw.get("network") != {"name": "eva2-v02-offline-net-20260827", "internal": True, "hostPublishedPorts": False, "internetGateway": False}:
        raise SystemExit("network boundary differs")
    if raw.get("tokenizer") != {
        "repository": "openai/whisper-tiny",
        "commit": "169d4a4341b33bc18d8881c4b69c2e104e1cc0af",
        "tokenizerJsonSha256": TOKENIZER,
        "offlineEnvironment": {"HF_HOME": "/cache", "HF_HUB_OFFLINE": "1"},
    }:
        raise SystemExit("tokenizer cache binding differs")
    containers = raw.get("containers")
    if not isinstance(containers, dict):
        raise SystemExit("container boundary is absent")
    for name, expected_tmpfs in (("piper", "/tmp:rw,nosuid,noexec,size=16m"), ("whisper", "/tmp:rw,nosuid,noexec,size=32m")):
        sidecar = containers.get(name)
        if not isinstance(sidecar, dict) or any(sidecar.get(key) is not value for key, value in {"readOnlyRoot": True, "allCapabilitiesDropped": True, "noNewPrivileges": True, "publishedHostPorts": False, "hostMounts": False}.items()) or sidecar.get("tmpfs") != expected_tmpfs:
            raise SystemExit(f"{name} isolation differs")
    controller = containers.get("controller")
    if not isinstance(controller, dict) or any(controller.get(key) is not value for key, value in {"readOnlyRoot": True, "allCapabilitiesDropped": True, "noNewPrivileges": True, "publishedHostPorts": False, "hostMounts": False, "receiptVolumeOnly": True}.items()) or controller.get("exitCode") != 0:
        raise SystemExit("controller isolation differs")
    pipeline = raw.get("pipeline")
    if not isinstance(pipeline, dict) or pipeline.get("candidate") != "V02-wyoming-local-voice-v1":
        raise SystemExit("pipeline is absent")
    if pipeline.get("controller") != {"requirementsSha256": _sha256(LOCK), "runnerSha256": _sha256(RUNNER), "wyoming": "1.10.0", "pydantic": "2.12.5"}:
        raise SystemExit("pipeline source binding differs")
    input_data = pipeline.get("input")
    if not isinstance(input_data, dict) or input_data.get("phraseSha256") != INPUT:
        raise SystemExit("input binding differs")
    _pcm(input_data.get("pcm"), label="input")
    reply = pipeline.get("reply")
    if not isinstance(reply, dict) or reply.get("delivery") != {"interrupted": True, "receivedAudioStop": False}:
        raise SystemExit("local playback interruption differs")
    _pcm(reply.get("pcm"), label="reply")
    if not isinstance(reply.get("pcm"), dict) or reply["pcm"]["chunks"] > 3:
        raise SystemExit("interrupted playback exceeded three chunks")
    print("V02 corrected local voice offline receipt is source-bound valid.")


if __name__ == "__main__":
    main()
