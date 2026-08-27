"""Capture the corrected no-egress local-voice proof without retaining audio.

The older isolation receipt is deliberately not reused: it did not bind the
Whisper tokenizer cache or the small tmpfs mounts that current upstream needs.
Only the three exact disposable containers below are admitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


PIPER_IMAGE = "rhasspy/wyoming-piper@sha256:6b837615b543e38587b735b72abad2dfd7ead50fd10ec1a4d5815241e5539377"
WHISPER_IMAGE = "rhasspy/wyoming-whisper@sha256:731299c56df053dd67978127a44e417c83f0d900fdaad1bd7ab8aad82f84106d"
CONTROLLER_IMAGE = "eva2-v02-wyoming-controller:d2b6397"
NETWORK = "eva2-v02-offline-net-20260827"
TOKENIZER_COMMIT = "169d4a4341b33bc18d8881c4b69c2e104e1cc0af"
TOKENIZER_SHA256 = "27fc476bfe7f17299480be2273fc0608e4d5a99aba2ab5dec5374b4482d1a566"
TMPFS = "/tmp:rw,nosuid,noexec,size=16m"
WHISPER_TMPFS = "/tmp:rw,nosuid,noexec,size=32m"


def _docker_json(*args: str) -> Any:
    completed = subprocess.run(("docker", *args), check=True, text=True, capture_output=True)
    return json.loads(completed.stdout)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _one(name: str) -> dict[str, Any]:
    rows = _docker_json("inspect", name)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError(f"cannot inspect exactly one container: {name}")
    return rows[0]


def _volume_mounts(container: dict[str, Any], expected: dict[str, str]) -> None:
    mounts = container.get("Mounts")
    if not isinstance(mounts, list) or len(mounts) != len(expected):
        raise ValueError("voice sidecar mount count differs")
    actual: dict[str, str] = {}
    for mount in mounts:
        if (
            not isinstance(mount, dict)
            or mount.get("Type") != "volume"
            or mount.get("RW") is not False
            or not isinstance(mount.get("Name"), str)
            or not isinstance(mount.get("Destination"), str)
        ):
            raise ValueError("voice sidecar has a writable or non-volume mount")
        actual[mount["Destination"]] = mount["Name"]
    if actual != expected:
        raise ValueError("voice sidecar volume binding differs")


def _sidecar(
    *,
    name: str,
    image: str,
    volumes: dict[str, str],
    tmpfs: str,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    container = _one(name)
    config = container.get("Config")
    host = container.get("HostConfig")
    networks = container.get("NetworkSettings", {}).get("Networks")
    if not isinstance(config, dict) or not isinstance(host, dict):
        raise ValueError("voice sidecar inspect shape is invalid")
    if config.get("Image") != image or host.get("ReadonlyRootfs") is not True:
        raise ValueError("voice sidecar image or read-only root differs")
    if container.get("State", {}).get("Running") is not True:
        raise ValueError("voice sidecar is not running")
    if set(host.get("CapDrop") or []) != {"ALL"} or host.get("SecurityOpt") != ["no-new-privileges"]:
        raise ValueError("voice sidecar privilege boundary differs")
    if host.get("Tmpfs") != {"/tmp": tmpfs.removeprefix("/tmp:")}:
        raise ValueError("voice sidecar tmpfs boundary differs")
    ports = container.get("NetworkSettings", {}).get("Ports")
    if not isinstance(ports, dict) or any(value is not None for value in ports.values()):
        raise ValueError("voice sidecar exposes a host port")
    if not isinstance(networks, dict) or set(networks) != {NETWORK}:
        raise ValueError("voice sidecar network differs")
    _volume_mounts(container, volumes)
    if env is not None:
        environment = config.get("Env")
        if not isinstance(environment, list) or not all(f"{key}={value}" in environment for key, value in env.items()):
            raise ValueError("Whisper offline environment differs")
    return {
        "image": image,
        "readOnlyRoot": True,
        "allCapabilitiesDropped": True,
        "noNewPrivileges": True,
        "tmpfs": tmpfs,
        "publishedHostPorts": False,
        "hostMounts": False,
        "volumes": {destination: volume for destination, volume in sorted(volumes.items())},
    }


def _controller() -> dict[str, object]:
    container = _one("eva2-v02-offline-controller")
    config = container.get("Config")
    host = container.get("HostConfig")
    if not isinstance(config, dict) or not isinstance(host, dict):
        raise ValueError("controller inspect shape is invalid")
    if config.get("Image") != CONTROLLER_IMAGE or host.get("ReadonlyRootfs") is not True:
        raise ValueError("controller image or root differs")
    if set(host.get("CapDrop") or []) != {"ALL"} or host.get("SecurityOpt") != ["no-new-privileges"]:
        raise ValueError("controller privilege boundary differs")
    if host.get("Tmpfs") != {"/tmp": TMPFS.removeprefix("/tmp:")}:
        raise ValueError("controller tmpfs differs")
    if container.get("State", {}).get("Status") != "exited" or container["State"].get("ExitCode") != 0:
        raise ValueError("controller did not exit successfully")
    mounts = container.get("Mounts")
    if (
        not isinstance(mounts, list)
        or len(mounts) != 1
        or not isinstance(mounts[0], dict)
        or mounts[0].get("Type") != "volume"
        or mounts[0].get("Name") != "eva2-v02-offline-receipt-20260827"
        or mounts[0].get("Destination") != "/receipt"
        or mounts[0].get("RW") is not True
    ):
        raise ValueError("controller receipt volume differs")
    return {
        "image": CONTROLLER_IMAGE,
        "readOnlyRoot": True,
        "allCapabilitiesDropped": True,
        "noNewPrivileges": True,
        "tmpfs": TMPFS,
        "publishedHostPorts": False,
        "hostMounts": False,
        "receiptVolumeOnly": True,
        "exitCode": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pipeline = json.loads(args.pipeline_receipt.read_text(encoding="utf-8"))
    if not isinstance(pipeline, dict) or pipeline.get("candidate") != "V02-wyoming-local-voice-v1":
        raise SystemExit("pipeline receipt is not the expected V02 result")
    network = _docker_json("network", "inspect", NETWORK)
    if not isinstance(network, list) or len(network) != 1 or network[0].get("Internal") is not True:
        raise SystemExit("voice network is not internal")
    result = {
        "candidate": "V02-wyoming-local-offline-isolation-v2",
        "captureScriptSha256": _sha256(Path(__file__)),
        "pipelineReceiptSha256": _sha256(args.pipeline_receipt),
        "pipeline": pipeline,
        "network": {"name": NETWORK, "internal": True, "hostPublishedPorts": False, "internetGateway": False},
        "tokenizer": {"repository": "openai/whisper-tiny", "commit": TOKENIZER_COMMIT, "tokenizerJsonSha256": TOKENIZER_SHA256, "offlineEnvironment": {"HF_HOME": "/cache", "HF_HUB_OFFLINE": "1"}},
        "containers": {
            "piper": _sidecar(name="eva2-v02-offline-piper", image=PIPER_IMAGE, volumes={"/data": "eva2-v02-cache-piper-model-20260826"}, tmpfs=TMPFS),
            "whisper": _sidecar(name="eva2-v02-offline-whisper", image=WHISPER_IMAGE, volumes={"/data": "eva2-v02-offline-whisper-data-20260827", "/cache": "eva2-v02-offline-tokenizer-20260827"}, tmpfs=WHISPER_TMPFS, env={"HF_HOME": "/cache", "HF_HUB_OFFLINE": "1"}),
            "controller": _controller(),
        },
        "nonclaims": [
            "No microphone, speaker, real person, audio recording or hardware path was used.",
            "No speech-provider credential, LLM reply, tool call, persistence or production route was used.",
            "This is not full-duplex voice, microphone barge-in, a latency measurement or a semantic accuracy benchmark.",
        ],
    }
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
