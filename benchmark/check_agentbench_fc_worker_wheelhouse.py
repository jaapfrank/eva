#!/usr/bin/env python3
"""Seal and verify the disposable AgentBench AgentRL-worker wheelhouse.

This tool only reads local bytes.  It never downloads, installs, imports, or
executes a package.  The generated manifest deliberately belongs to the
AgentBench development candidate, not to Eva's production wheelhouse.
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
LOCK = HERE / "agentbench-fc-v1" / "agentrl-worker-requirements.lock"
INPUT = HERE / "agentbench-fc-v1" / "agentrl-worker-requirements.in"
CONTRACT = "EvaAgentBenchFcWorkerWheelhouse/v1"
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
AGENTRL_COMMIT = "6a73409d31ba695d383b978a8ad3ef400d90c054"


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
        if digest not in lock_hashes:
            raise RuntimeError(f"worker wheel is not admitted by the exact lock: {entry.name}")
        artifacts.append(
            {"byteLength": metadata.st_size, "filename": entry.name, "sha256": digest}
        )
    if not artifacts:
        raise RuntimeError("worker wheelhouse is empty")
    return artifacts


def _manifest(wheelhouse: Path, *, require_read_only: bool = False) -> dict[str, object]:
    lock = LOCK.read_bytes()
    artifacts = _wheel_artifacts(
        wheelhouse, _lock_hashes(lock), require_read_only=require_read_only
    )
    return {
        "agentrlCommit": AGENTRL_COMMIT,
        "artifacts": artifacts,
        "contract": CONTRACT,
        "requirementsIn": {
            "path": "benchmark/agentbench-fc-v1/agentrl-worker-requirements.in",
            "sha256": _sha256(INPUT.read_bytes()),
        },
        "requirementsLock": {
            "path": "benchmark/agentbench-fc-v1/agentrl-worker-requirements.lock",
            "sha256": _sha256(lock),
        },
        "status": "sealed_development_wheelhouse",
        "target": {
            "architecture": "amd64",
            "os": "debian-bookworm",
            "pythonAbi": "cp310",
            "pythonVersion": "3.10.18",
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
    parser.add_argument(
        "--require-read-only",
        action="store_true",
        help="refuse a writable materialized wheelhouse",
    )
    arguments = parser.parse_args()

    if arguments.write_manifest is not None:
        arguments.write_manifest.write_bytes(
            _canonical(_manifest(arguments.wheelhouse, require_read_only=arguments.require_read_only))
            + b"\n"
        )
        return
    assert arguments.manifest is not None
    checked = validate(
        arguments.wheelhouse, arguments.manifest, require_read_only=arguments.require_read_only
    )
    print(json.dumps(checked, sort_keys=True))


if __name__ == "__main__":
    main()
