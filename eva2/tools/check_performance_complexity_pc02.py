#!/usr/bin/env python3
"""Fail closed when Eva's largest runtime files grow or escape ownership."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any


CONTRACT = "EvaPerformanceComplexityPc02Receipt/v1"
AUTHORITY_CONTRACT = "EvaPerformanceComplexityPc02Authority/v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_HOT_KEYS = {
    "baselineSha256",
    "componentOwner",
    "contract",
    "maximumPhysicalLines",
    "path",
}


class Pc02Error(RuntimeError):
    """The closed authority or measured source tree is invalid."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise Pc02Error(f"PC02 {label} is invalid")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or value != candidate.as_posix():
        raise Pc02Error(f"PC02 {label} is unsafe")
    return value


def _lines(raw: bytes, *, path: str) -> int:
    try:
        return len(raw.decode("utf-8").splitlines())
    except UnicodeDecodeError as error:
        raise Pc02Error(f"PC02 source is not UTF-8: {path}") from error


def _load_authority(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = json.loads(raw)
    required = {
        "contract",
        "extensions",
        "generatedExactPaths",
        "hotPaths",
        "maximumUnclassifiedPhysicalLines",
        "productRoots",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise Pc02Error("PC02 authority fields differ")
    if value["contract"] != AUTHORITY_CONTRACT:
        raise Pc02Error("PC02 authority contract differs")
    extensions = value["extensions"]
    if extensions != sorted(set(extensions)) or not all(
        isinstance(item, str) and item.startswith(".") for item in extensions
    ):
        raise Pc02Error("PC02 extensions are invalid")
    roots = tuple(_path(item, label="product root") for item in value["productRoots"])
    if tuple(sorted(set(roots))) != roots:
        raise Pc02Error("PC02 product roots must be unique and sorted")
    generated = tuple(
        _path(item, label="generated path") for item in value["generatedExactPaths"]
    )
    if tuple(sorted(set(generated))) != generated:
        raise Pc02Error("PC02 generated paths must be unique and sorted")
    maximum = value["maximumUnclassifiedPhysicalLines"]
    if not isinstance(maximum, int) or maximum < 100:
        raise Pc02Error("PC02 unclassified maximum is invalid")
    hot_paths = value["hotPaths"]
    if not isinstance(hot_paths, list) or not hot_paths:
        raise Pc02Error("PC02 hot-path authority is empty")
    previous = ""
    for row in hot_paths:
        if not isinstance(row, dict) or set(row) != _HOT_KEYS:
            raise Pc02Error("PC02 hot-path fields differ")
        current = _path(row["path"], label="hot path")
        if current <= previous:
            raise Pc02Error("PC02 hot paths must be unique and sorted")
        previous = current
        if not _DIGEST.fullmatch(str(row["baselineSha256"])):
            raise Pc02Error("PC02 baseline digest is invalid")
        if not isinstance(row["maximumPhysicalLines"], int) or row["maximumPhysicalLines"] <= maximum:
            raise Pc02Error("PC02 hot-path maximum is invalid")
        for label in ("componentOwner", "contract"):
            if not isinstance(row[label], str) or not row[label].strip():
                raise Pc02Error(f"PC02 {label} is invalid")
    return value, _sha256(raw)


def check(repo: Path, project: Path, authority_path: Path) -> dict[str, object]:
    authority, authority_sha256 = _load_authority(authority_path)
    extensions = tuple(authority["extensions"])
    generated = set(authority["generatedExactPaths"])
    observed: dict[str, tuple[int, str]] = {}
    for root_name in authority["productRoots"]:
        root = project / root_name
        if not root.is_dir():
            raise Pc02Error(f"PC02 product root is unavailable: {root_name}")
        for candidate in sorted(path for path in root.rglob("*") if path.is_file()):
            relative = candidate.relative_to(project).as_posix()
            if relative in generated or candidate.suffix not in extensions:
                continue
            raw = candidate.read_bytes()
            observed[relative] = (_lines(raw, path=relative), _sha256(raw))
    hot_authority = {row["path"]: row for row in authority["hotPaths"]}
    violations: list[str] = []
    hot_receipts: list[dict[str, object]] = []
    for path, row in hot_authority.items():
        if path not in observed:
            violations.append(f"registered_hot_path_missing:{path}")
            continue
        physical_lines, current_sha256 = observed[path]
        if physical_lines > row["maximumPhysicalLines"]:
            violations.append(f"registered_hot_path_grew:{path}")
        hot_receipts.append(
            {
                **row,
                "currentSha256": current_sha256,
                "physicalLines": physical_lines,
            }
        )
    unclassified = sorted(
        path
        for path, (physical_lines, _) in observed.items()
        if path not in hot_authority
        and physical_lines > authority["maximumUnclassifiedPhysicalLines"]
    )
    violations.extend(f"unclassified_hot_path:{path}" for path in unclassified)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    return {
        "authoritySha256": authority_sha256,
        "contract": CONTRACT,
        "gitCommit": commit,
        "hotPaths": hot_receipts,
        "maximumUnclassifiedPhysicalLines": authority["maximumUnclassifiedPhysicalLines"],
        "observedProductFiles": len(observed),
        "status": "passed" if not violations else "failed",
        "unclassifiedHotPaths": unclassified,
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        receipt = check(arguments.repo, arguments.project, arguments.authority)
    except (OSError, ValueError, Pc02Error, subprocess.CalledProcessError) as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
