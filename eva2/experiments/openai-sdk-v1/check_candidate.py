"""Fail closed when the DF02a candidate drifts from its receipt."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = Path(__file__).resolve().parent
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^ \\]+)")
EXPECTED_DELTA = {
    "jiter": "0.16.0",
    "openai": "3.3.1",
    "sniffio": "1.3.1",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_bytes(commit: str, relative: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:eva2/{relative}"],
        cwd=ROOT.parent,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(f"candidate Git binding is unavailable: {relative}")
    return result.stdout


def _pins(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = PIN.match(line)
        if match is None:
            continue
        name, version = match.groups()
        normalized = name.lower().replace("_", "-")
        if normalized in result:
            raise RuntimeError(f"duplicate distribution: {normalized}")
        result[normalized] = version
    return result


def check() -> dict[str, Any]:
    receipt = json.loads((CANDIDATE / "receipt.json").read_text(encoding="utf-8"))
    production_lock = ROOT / "requirements.lock"
    candidate_roots = CANDIDATE / "requirements.in"
    candidate_lock = CANDIDATE / "requirements.lock"
    dependency = receipt["dependency"]
    expected_hashes = {
        production_lock: dependency["productionLockSha256"],
        candidate_roots: dependency["candidateRootsSha256"],
        candidate_lock: dependency["candidateLockSha256"],
    }
    for path, expected in expected_hashes.items():
        if _sha256(path) != expected:
            raise RuntimeError(f"candidate hash mismatch: {path.relative_to(ROOT)}")

    production = _pins(production_lock)
    candidate = _pins(candidate_lock)
    changed = {
        name: version
        for name, version in candidate.items()
        if production.get(name) != version
    }
    removed = sorted(set(production) - set(candidate))
    if changed != EXPECTED_DELTA or removed:
        raise RuntimeError("candidate dependency delta is not exactly closed")
    if len(production) != 38 or len(candidate) != 41:
        raise RuntimeError("candidate distribution count is invalid")

    for relative, expected in receipt["sourceSha256"].items():
        if _sha256(ROOT / relative) != expected:
            raise RuntimeError(f"candidate source hash mismatch: {relative}")
        if hashlib.sha256(
            _git_bytes(receipt["implementationGitCommit"], relative)
        ).hexdigest() != expected:
            raise RuntimeError(f"candidate Git source mismatch: {relative}")
    policy = "design/architecture/package-boundaries-v1.json"
    if hashlib.sha256(
        _git_bytes(receipt["implementationGitCommit"], policy)
    ).hexdigest() != receipt["packagePolicySha256"]:
        raise RuntimeError("candidate package policy Git binding differs")
    if (
        receipt["productionAuthority"] is not False
        or receipt["managedBChanged"] is not False
        or receipt["realProviderCalled"] is not False
    ):
        raise RuntimeError("candidate nonclaims are invalid")
    return {
        "candidateId": receipt["candidateId"],
        "candidateDistributionCount": len(candidate),
        "newDistributions": [f"{name}=={version}" for name, version in changed.items()],
        "productionAuthority": False,
        "status": receipt["status"],
    }


if __name__ == "__main__":
    print(json.dumps(check(), separators=(",", ":"), sort_keys=True))
