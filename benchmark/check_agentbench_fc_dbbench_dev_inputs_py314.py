#!/usr/bin/env python3
"""Validate the immutable inputs for the unscored AgentBench DBBench dev worker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile
from typing import Any


HERE = Path(__file__).resolve().parent
CONTRACT = "EvaAgentBenchFcDbbenchDevWorkerInputs/v1"
AGENTBENCH_COMMIT = "d1e4a10db08c87075c78972e48ecc182be03e2d5"
BUILDER_IMAGE = (
    "cgr.dev/chainguard/python@sha256:"
    "aa8fd2447b8b52922db57deb3894b622c3229387aaaec5934d64b85dbff6eb17"
)
RUNTIME_IMAGE = (
    "cgr.dev/chainguard/python@sha256:"
    "ee37f5e4fb445732409626797dccb6f2a6337872def2bab48729ee61b335fa77"
)
PARENT_IMAGE_ID = "sha256:9619b34b82e36e8b8f9149069a8748663895ae2c3c9e653186c56a385361f3a5"
MYSQL_SOURCE_SHA256 = "8b16d51447e3603f18478fb5a19b333bfb73fb58f872eb055a105635f53d2345"
MYSQL_SOURCE = "mysql-connector-python-9.3.0.tar.gz"
MYSQL_WHEEL = "mysql_connector_python-9.3.0-cp314-cp314-linux_x86_64.whl"
MYSQL_WHEEL_SHA256 = "c2d94632c614d6fd17309abf692475ba42fa88675e7937903bafe672923f472d"
SOURCE_ARCHIVE = "agentbench-dbbench-dev-d1e4a10.tar.gz"
SOURCE_ARCHIVE_SHA256 = "7d78625f9b178941e207f928716a93cafafe6ad483cbe0d3717a1dcef7464c5d"
EXPECTED_SOURCE_PATHS = {
    "configs/tasks/dbbench.yaml",
    "data/dbbench/dev.jsonl",
    "src/server/tasks/dbbench/Dockerfile",
    "src/server/tasks/dbbench/__init__.py",
    "src/server/tasks/dbbench/environment.py",
    "src/server/tasks/dbbench/interaction.py",
    "src/server/tasks/dbbench/requirements.txt",
    "src/server/tasks/dbbench/result_processor.py",
    "src/server/tasks/dbbench/task.py",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _artifact(path: Path, expected_sha256: str, *, require_read_only: bool) -> dict[str, object]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"unsafe DBBench input: {path.name}")
    if require_read_only and metadata.st_mode & 0o222:
        raise RuntimeError(f"DBBench input is not read-only: {path.name}")
    digest = _sha256(path)
    if digest != expected_sha256:
        raise RuntimeError(f"DBBench input has an unexpected digest: {path.name}")
    return {"byteLength": metadata.st_size, "filename": path.name, "sha256": digest}


def _source_paths(archive: Path) -> list[str]:
    with tarfile.open(archive, "r:gz") as value:
        members = value.getmembers()
        if any(member.issym() or member.islnk() or (not member.isfile() and not member.isdir()) for member in members):
            raise RuntimeError("DBBench source archive contains an unsafe entry")
        names = [member.name for member in members if member.isfile()]
    if set(names) != EXPECTED_SOURCE_PATHS:
        raise RuntimeError("DBBench source archive does not contain the exact upstream dev-task files")
    return sorted(names)


def build_manifest(inputs_dir: Path, *, require_read_only: bool = False) -> dict[str, object]:
    root = inputs_dir.lstat()
    if not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode):
        raise RuntimeError("DBBench inputs directory is unsafe")
    if require_read_only and root.st_mode & 0o222:
        raise RuntimeError("DBBench inputs directory is not read-only")
    expected_names = {MYSQL_SOURCE, MYSQL_WHEEL, SOURCE_ARCHIVE}
    actual_names = {entry.name for entry in os.scandir(inputs_dir)}
    if actual_names != expected_names:
        raise RuntimeError("DBBench inputs are incomplete or contain an extra file")
    archive = inputs_dir / SOURCE_ARCHIVE
    return {
        "agentbenchCommit": AGENTBENCH_COMMIT,
        "artifacts": [
            _artifact(archive, SOURCE_ARCHIVE_SHA256, require_read_only=require_read_only),
            _artifact(inputs_dir / MYSQL_SOURCE, MYSQL_SOURCE_SHA256, require_read_only=require_read_only),
            _artifact(inputs_dir / MYSQL_WHEEL, MYSQL_WHEEL_SHA256, require_read_only=require_read_only),
        ],
        "builderImage": BUILDER_IMAGE,
        "contract": CONTRACT,
        "mysqlConnectorSource": {
            "filename": MYSQL_SOURCE,
            "sha256": MYSQL_SOURCE_SHA256,
            "twoNetworklessBuildsMatch": True,
        },
        "parentAgentRlWorkerImageId": PARENT_IMAGE_ID,
        "runtimeImage": RUNTIME_IMAGE,
        "sourceArchivePaths": _source_paths(archive),
        "status": "sealed_unscored_dbbench_dev_worker_inputs",
        "target": {"architecture": "amd64", "pythonAbi": "cp314", "pythonVersion": "3.14.7"},
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("DBBench input manifest is not valid UTF-8 JSON") from error
    if not isinstance(value, dict) or raw != _canonical(value) + b"\n":
        raise RuntimeError("DBBench input manifest is not canonical")
    return value


def validate(inputs_dir: Path, manifest_path: Path, *, require_read_only: bool = False) -> dict[str, object]:
    expected = build_manifest(inputs_dir, require_read_only=require_read_only)
    if _read_manifest(manifest_path) != expected:
        raise RuntimeError("DBBench input manifest differs from local inputs")
    return expected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs-dir", type=Path, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--write-manifest", type=Path)
    target.add_argument("--manifest", type=Path)
    parser.add_argument("--require-read-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.write_manifest is not None:
        arguments.write_manifest.write_bytes(_canonical(build_manifest(arguments.inputs_dir, require_read_only=arguments.require_read_only)) + b"\n")
        return
    assert arguments.manifest is not None
    validate(arguments.inputs_dir, arguments.manifest, require_read_only=arguments.require_read_only)


if __name__ == "__main__":
    main()
