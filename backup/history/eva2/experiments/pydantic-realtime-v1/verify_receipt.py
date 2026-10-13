"""Fail closed if the Pydantic Realtime dependency receipt drifts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = Path(__file__).resolve().parent
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^ \\]+)")
EXPECTED_ADDED = {
    "certifi": "2026.7.22",
    "charset-normalizer": "3.5.1",
    "jiter": "0.16.0",
    "openai": "3.3.1",
    "regex": "2026.7.19",
    "requests": "2.34.2",
    "sniffio": "1.3.1",
    "tiktoken": "0.14.0",
    "urllib3": "2.7.0",
}
EXPECTED_CHANGED = [
    "pydantic-ai-slim 2.32.1->2.34.0",
    "pydantic-graph 2.32.1->2.34.0",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pins(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = PIN.match(line)
        if match is None:
            continue
        name, version = match.groups()
        name = name.lower().replace("_", "-")
        if name in result:
            raise RuntimeError(f"duplicate distribution: {name}")
        result[name] = version
    return result


def _git_bytes(commit: str, relative: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:eva2/{relative}"],
        cwd=ROOT.parent,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(f"receipt Git binding is unavailable: {relative}")
    return result.stdout


def main() -> None:
    receipt = json.loads((CANDIDATE / "receipt.json").read_text(encoding="utf-8"))
    if receipt["contract"] != "EvaDependencyCandidateReceipt/v1":
        raise RuntimeError("candidate receipt contract differs")
    if receipt["status"] != "tested" or any(
        receipt[name] is not False
        for name in ("productionAuthority", "managedBChanged", "realProviderCalled")
    ):
        raise RuntimeError("candidate receipt makes an invalid promotion claim")
    dependency = receipt["dependency"]
    expected_hashes = {
        ROOT / "requirements.lock": dependency["productionLockSha256"],
        CANDIDATE / "requirements.in": dependency["candidateRootsSha256"],
        CANDIDATE / "requirements.lock": dependency["candidateLockSha256"],
    }
    for path, expected in expected_hashes.items():
        if _sha256(path) != expected:
            raise RuntimeError(f"candidate hash differs: {path.relative_to(ROOT)}")
    production = _pins(ROOT / "requirements.lock")
    candidate = _pins(CANDIDATE / "requirements.lock")
    added = {name: pinned for name, pinned in candidate.items() if name not in production}
    changed = [
        f"{name} {production[name]}->{pinned}"
        for name, pinned in candidate.items()
        if name in production and production[name] != pinned
    ]
    if (
        added != EXPECTED_ADDED
        or changed != EXPECTED_CHANGED
        or set(production) - set(candidate)
        or len(production) != dependency["productionDistributionCount"]
        or len(candidate) != dependency["candidateDistributionCount"]
    ):
        raise RuntimeError("candidate dependency delta differs")
    commit = receipt["implementationGitCommit"]
    for relative, expected in receipt["sourceSha256"].items():
        current = ROOT / relative
        if _sha256(current) != expected:
            raise RuntimeError(f"candidate source differs: {relative}")
        if hashlib.sha256(_git_bytes(commit, relative)).hexdigest() != expected:
            raise RuntimeError(f"candidate Git source differs: {relative}")
    print(
        json.dumps(
            {
                "candidate": receipt["candidateId"],
                "candidateDistributionCount": len(candidate),
                "status": receipt["status"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
