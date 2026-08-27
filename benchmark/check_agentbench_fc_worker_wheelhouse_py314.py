#!/usr/bin/env python3
"""Seal and verify the Python 3.14 AgentRL-worker wheelhouse.

The resolver lock admits upstream distribution bytes.  Two wheels are built
locally from separately pinned source bytes: ``etcd3`` (which upstream ships
only as an sdist) and the AgentRL worker itself.  They are admitted only by
their exact filename and digest, never by a loose package name.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any


HERE = Path(__file__).resolve().parent
LOCK = HERE / "agentbench-fc-v1" / "agentrl-worker-requirements-py314.lock"
INPUT = HERE / "agentbench-fc-v1" / "agentrl-worker-requirements.in"
CONTRACT = "EvaAgentBenchFcWorkerWheelhouse/v2"
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
AGENTRL_COMMIT = "6a73409d31ba695d383b978a8ad3ef400d90c054"
BUILDER_IMAGE = (
    "cgr.dev/chainguard/python@sha256:"
    "aa8fd2447b8b52922db57deb3894b622c3229387aaaec5934d64b85dbff6eb17"
)
LOCAL_BUILT_WHEELS: dict[str, dict[str, object]] = {
    "etcd3-0.12.0-py2.py3-none-any.whl": {
        "sha256": "d58157ddd4a73da64d5909ff8bd44984b1f67c1e5c775d0ec52c776038b22692",
        "source": {
            "filename": "etcd3-0.12.0.tar.gz",
            "sha256": "89a704cb389bf0a010a1fa050ce19342d23bf6371ebda1c21cfe8ff3ed488726",
        },
    },
    "agentrl_worker-0.4.1.dev8+g6a73409-py3-none-any.whl": {
        "sha256": "b33305e245e79b869a774e12fdaf1e9a6d3c8a98371bf8e1c60a9719b2a9ba5d",
        "source": {
            "agentrlGitArchiveSha256": (
                "76a50942687e5646ad401adbb579e70fafb0761175e61019cc2c72cd0b7ce2ee"
            ),
        },
    },
}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _lock_hashes(lock: bytes) -> set[str]:
    hashes = {
        line.split("--hash=sha256:", 1)[1].strip().split()[0]
        for line in lock.decode("utf-8", "strict").splitlines()
        if "--hash=sha256:" in line
    }
    if not hashes or any(len(value) != 64 for value in hashes):
        raise RuntimeError("worker lock has no valid package hashes")
    return hashes


def _wheel_artifacts(
    wheelhouse: Path, lock_hashes: set[str], *, require_read_only: bool
) -> list[dict[str, object]]:
    try:
        root = wheelhouse.lstat()
        entries = list(os.scandir(wheelhouse))
    except FileNotFoundError as error:
        raise RuntimeError("worker wheelhouse is absent") from error
    if not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode):
        raise RuntimeError("worker wheelhouse is not a safe directory")
    if require_read_only and root.st_mode & 0o222:
        raise RuntimeError("worker wheelhouse is not read-only")

    artifacts: list[dict[str, object]] = []
    found_local: set[str] = set()
    for entry in sorted(entries, key=lambda item: item.name):
        path = Path(entry.path)
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or not entry.name.endswith(".whl")
            or not 0 < metadata.st_size <= MAX_ARTIFACT_BYTES
        ):
            raise RuntimeError(f"unsafe worker wheelhouse entry: {entry.name}")
        if require_read_only and metadata.st_mode & 0o222:
            raise RuntimeError(f"worker wheelhouse artifact is not read-only: {entry.name}")
        digest = _sha256(path.read_bytes())
        local = LOCAL_BUILT_WHEELS.get(entry.name)
        if local is not None:
            if digest != local["sha256"]:
                raise RuntimeError(f"locally built wheel differs from pinned bytes: {entry.name}")
            found_local.add(entry.name)
        elif digest not in lock_hashes:
            raise RuntimeError(f"worker wheel is not admitted by the exact lock: {entry.name}")
        artifacts.append({"byteLength": metadata.st_size, "filename": entry.name, "sha256": digest})
    if not artifacts:
        raise RuntimeError("worker wheelhouse is empty")
    if found_local != set(LOCAL_BUILT_WHEELS):
        raise RuntimeError("worker wheelhouse misses a required locally built wheel")
    return artifacts


def _manifest(wheelhouse: Path, *, require_read_only: bool = False) -> dict[str, object]:
    lock = LOCK.read_bytes()
    return {
        "agentrlCommit": AGENTRL_COMMIT,
        "artifacts": _wheel_artifacts(
            wheelhouse, _lock_hashes(lock), require_read_only=require_read_only
        ),
        "builderImage": BUILDER_IMAGE,
        "contract": CONTRACT,
        "localBuildArtifacts": [
            {"filename": filename, **metadata}
            for filename, metadata in sorted(LOCAL_BUILT_WHEELS.items())
        ],
        "requirementsIn": {
            "path": "benchmark/agentbench-fc-v1/agentrl-worker-requirements.in",
            "sha256": _sha256(INPUT.read_bytes()),
        },
        "requirementsLock": {
            "path": "benchmark/agentbench-fc-v1/agentrl-worker-requirements-py314.lock",
            "sha256": _sha256(lock),
        },
        "status": "sealed_development_wheelhouse",
        "target": {
            "architecture": "amd64",
            "os": "wolfi",
            "pythonAbi": "cp314",
            "pythonVersion": "3.14.7",
        },
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("worker wheelhouse manifest is not valid UTF-8 JSON") from error
    if not isinstance(value, dict) or raw != _canonical(value) + b"\n":
        raise RuntimeError("worker wheelhouse manifest is not canonical")
    return value


def validate(
    wheelhouse: Path, manifest_path: Path, *, require_read_only: bool = False
) -> dict[str, object]:
    expected = _manifest(wheelhouse, require_read_only=require_read_only)
    actual = _read_manifest(manifest_path)
    if actual != expected:
        raise RuntimeError("worker wheelhouse manifest differs from local inputs")
    return expected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", type=Path, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--write-manifest", type=Path)
    target.add_argument("--manifest", type=Path)
    parser.add_argument("--require-read-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.write_manifest is not None:
        arguments.write_manifest.write_bytes(
            _canonical(_manifest(arguments.wheelhouse, require_read_only=arguments.require_read_only))
            + b"\n"
        )
        return
    assert arguments.manifest is not None
    print(
        json.dumps(
            validate(
                arguments.wheelhouse,
                arguments.manifest,
                require_read_only=arguments.require_read_only,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
