#!/usr/bin/env python3
"""Verify exact sealed Go 1.25 inputs for the AgentRL controller candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any


CONTRACT = "EvaAgentBenchFcControllerBuildInputs/v3"
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
AGENTRL_COMMIT = "6a73409d31ba695d383b978a8ad3ef400d90c054"
SOURCE_ARCHIVE_SHA256 = "76a50942687e5646ad401adbb579e70fafb0761175e61019cc2c72cd0b7ce2ee"
SECURITY_PATCH_SHA256 = "30004f90adf4536b4425df48229af91a764c1d65b5f70b019c8b2d0f23aaa51b"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _artifacts(inputs_dir: Path, *, require_read_only: bool) -> list[dict[str, object]]:
    try:
        root = inputs_dir.lstat()
        entries = list(os.scandir(inputs_dir))
    except FileNotFoundError as error:
        raise RuntimeError("controller build-input directory is absent") from error
    if not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode):
        raise RuntimeError("controller build-input directory is not safe")
    if require_read_only and root.st_mode & 0o222:
        raise RuntimeError("controller build-input directory is not read-only")
    artifacts: list[dict[str, object]] = []
    for entry in sorted(entries, key=lambda item: item.name):
        path = Path(entry.path)
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or not 0 < metadata.st_size <= MAX_ARTIFACT_BYTES
        ):
            raise RuntimeError(f"unsafe controller build input: {entry.name}")
        if require_read_only and metadata.st_mode & 0o222:
            raise RuntimeError(f"controller build input is not read-only: {entry.name}")
        artifacts.append(
            {
                "byteLength": metadata.st_size,
                "filename": entry.name,
                "sha256": _sha256(path.read_bytes()),
            }
        )
    expected_names = {
        "gomodcache.tar",
        "protoc-31.1-linux-x86_64.zip",
        "protoc-gen-go",
        "protoc-gen-go-grpc",
    }
    if {artifact["filename"] for artifact in artifacts} != expected_names:
        raise RuntimeError("controller build inputs are incomplete or contain an extra artifact")
    return artifacts


def build_manifest(inputs_dir: Path, *, require_read_only: bool = False) -> dict[str, object]:
    return {
        "agentrlCommit": AGENTRL_COMMIT,
        "artifacts": _artifacts(inputs_dir, require_read_only=require_read_only),
        "contract": CONTRACT,
        "securityPatch": {"sha256": SECURITY_PATCH_SHA256},
        "sourceArchive": {"sha256": SOURCE_ARCHIVE_SHA256},
        "status": "sealed_development_build_inputs",
        "target": {
            "architecture": "amd64",
            "goModuleVersion": "1.25.0",
            "goToolchainVersion": "1.25.14",
            "os": "debian-bookworm",
            "protobufVersion": "31.1",
        },
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("controller build-input manifest is not valid UTF-8 JSON") from error
    if not isinstance(value, dict) or raw != _canonical(value) + b"\n":
        raise RuntimeError("controller build-input manifest is not canonical")
    return value


def validate(
    inputs_dir: Path, manifest_path: Path, *, require_read_only: bool = False
) -> dict[str, object]:
    expected = build_manifest(inputs_dir, require_read_only=require_read_only)
    if _read_manifest(manifest_path) != expected:
        raise RuntimeError("controller build-input manifest differs from local inputs")
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
        arguments.write_manifest.write_bytes(
            _canonical(
                build_manifest(arguments.inputs_dir, require_read_only=arguments.require_read_only)
            )
            + b"\n"
        )
        return
    assert arguments.manifest is not None
    print(
        json.dumps(
            validate(
                arguments.inputs_dir,
                arguments.manifest,
                require_read_only=arguments.require_read_only,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
