"""Capture the Docker facts behind one offline local-Wyoming proof.

This host-side checker is intentionally narrow: it accepts only the three
named disposable containers, refuses a non-internal network, host bind mounts,
published ports or writable model volumes, and records no audio or transcript.
It is not part of Eva's product runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


PIPER_IMAGE = (
    "rhasspy/wyoming-piper@sha256:"
    "6b837615b543e38587b735b72abad2dfd7ead50fd10ec1a4d5815241e5539377"
)
WHISPER_IMAGE = (
    "rhasspy/wyoming-whisper@sha256:"
    "731299c56df053dd67978127a44e417c83f0d900fdaad1bd7ab8aad82f84106d"
)
CONTROLLER_IMAGE = "eva2-v02-wyoming-controller:81f18f4"
SOURCE_COMMIT = "81f18f4341e7b5356065bf884b454968a955c2e5"


def _docker_json(*args: str) -> Any:
    completed = subprocess.run(
        ("docker", *args), check=True, text=True, capture_output=True
    )
    return json.loads(completed.stdout)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _one_container(name: str) -> dict[str, Any]:
    result = _docker_json("inspect", name)
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise ValueError(f"cannot inspect exactly one container: {name}")
    return result[0]


def _sidecar(
    *, container: dict[str, Any], image: str, volume: str, destination: str
) -> dict[str, object]:
    name = container.get("Name")
    config = container.get("Config")
    host = container.get("HostConfig")
    networks = container.get("NetworkSettings", {}).get("Networks")
    if not isinstance(name, str) or not isinstance(config, dict) or not isinstance(host, dict):
        raise ValueError("sidecar inspect shape is invalid")
    if config.get("Image") != image or host.get("ReadonlyRootfs") is not True:
        raise ValueError(f"sidecar image or read-only root mismatch: {name}")
    if container.get("State", {}).get("Running") is not True:
        raise ValueError(f"sidecar is not running: {name}")
    ports = container.get("NetworkSettings", {}).get("Ports")
    if not isinstance(ports, dict) or any(value is not None for value in ports.values()):
        raise ValueError(f"sidecar exposes a host port: {name}")
    mounts = container.get("Mounts")
    if not isinstance(mounts, list) or mounts != [
        {
            "Type": "volume",
            "Name": volume,
            "Source": f"/var/lib/docker/volumes/{volume}/_data",
            "Destination": destination,
            "Driver": "local",
            "Mode": "z",
            "RW": False,
            "Propagation": "",
        }
    ]:
        raise ValueError(f"sidecar mounts are not the one read-only model volume: {name}")
    if not isinstance(networks, dict) or len(networks) != 1:
        raise ValueError(f"sidecar network binding is invalid: {name}")
    return {
        "image": image,
        "readOnlyRoot": True,
        "publishedHostPorts": False,
        "hostMounts": False,
        "modelVolume": volume,
        "modelVolumeReadOnly": True,
    }


def _controller(container: dict[str, Any]) -> dict[str, object]:
    config = container.get("Config")
    host = container.get("HostConfig")
    mounts = container.get("Mounts")
    ports = container.get("NetworkSettings", {}).get("Ports")
    if not isinstance(config, dict) or not isinstance(host, dict) or not isinstance(mounts, list):
        raise ValueError("controller inspect shape is invalid")
    if config.get("Image") != CONTROLLER_IMAGE or host.get("ReadonlyRootfs") is not True:
        raise ValueError("controller image or root filesystem changed")
    if set(host.get("CapDrop") or []) != {"ALL"}:
        raise ValueError("controller must drop every Linux capability")
    if host.get("SecurityOpt") != ["no-new-privileges"]:
        raise ValueError("controller must use no-new-privileges")
    if not isinstance(ports, dict) or ports:
        raise ValueError("controller exposes a host port")
    if len(mounts) != 1 or mounts[0].get("Type") != "volume" or mounts[0].get("Destination") != "/receipt":
        raise ValueError("controller must have exactly one named receipt volume")
    if mounts[0].get("RW") is not True or mounts[0].get("Name") != "eva2-v02-offline-receipt-20260826":
        raise ValueError("controller receipt volume changed")
    if host.get("Tmpfs") != {"/tmp": "rw,nosuid,noexec,size=16m"}:
        raise ValueError("controller tmpfs changed")
    state = container.get("State", {})
    if state.get("Status") != "exited" or state.get("ExitCode") != 0:
        raise ValueError("controller did not exit successfully")
    return {
        "image": CONTROLLER_IMAGE,
        "sourceCommit": SOURCE_COMMIT,
        "readOnlyRoot": True,
        "allCapabilitiesDropped": True,
        "noNewPrivileges": True,
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
    pipeline_excerpt = {
        "candidate": pipeline.get("candidate"),
        "controller": pipeline.get("controller"),
        "input": pipeline.get("input"),
        "reply": pipeline.get("reply"),
        "sidecarsExpected": pipeline.get("sidecarsExpected"),
        "voiceAdmission": pipeline.get("voiceAdmission"),
    }
    if any(value is None for value in pipeline_excerpt.values()):
        raise SystemExit("pipeline receipt is missing a required binding")
    network = _docker_json("network", "inspect", "eva2-v02-offline-net-20260826")
    if not isinstance(network, list) or len(network) != 1 or network[0].get("Internal") is not True:
        raise SystemExit("voice network is not an internal Docker network")
    result = {
        "candidate": "V02-wyoming-local-offline-isolation-v1",
        "captureScriptSha256": _sha256(Path(__file__)),
        "pipelineReceiptSha256": _sha256(args.pipeline_receipt),
        "pipeline": pipeline_excerpt,
        "network": {
            "name": "eva2-v02-offline-net-20260826",
            "internal": True,
            "hostPublishedPorts": False,
            "internetGateway": False,
        },
        "containers": {
            "piper": _sidecar(
                container=_one_container("eva2-v02-offline-piper"),
                image=PIPER_IMAGE,
                volume="eva2-v02-offline-piper-20260826",
                destination="/data",
            ),
            "whisper": _sidecar(
                container=_one_container("eva2-v02-offline-whisper"),
                image=WHISPER_IMAGE,
                volume="eva2-v02-offline-whisper-20260826",
                destination="/data",
            ),
            "controller": _controller(_one_container("eva2-v02-offline-controller")),
        },
        "nonclaims": [
            "No microphone, speaker, real person, audio recording or hardware path was used.",
            "No speech-provider credential, LLM reply, tool call, persistence or production route was used.",
            "This is not full-duplex voice, a barge-in proof, a latency measurement or a semantic accuracy benchmark.",
        ],
    }
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
