"""Validate the full 38-distribution Pydantic AI 2.36 candidate lock."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any


EVA2 = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PRODUCT_LOCK = EVA2 / "requirements-4d.lock"
CANDIDATE_LOCK = HERE / "requirements-4d-candidate.lock"
AUTHORITY = HERE / "wheelhouse-authority.json"
COVERAGE = EVA2 / "release/managed-b-wheel-coverage-4d-amd64-v1.json"
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)(?:\s*\\)?$")
HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})(?:\s*\\)?$")
FORBIDDEN = {"fastmcp", "fastmcp-slim", "httpcore", "httpx", "mcp"}


class CandidateError(RuntimeError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _pins(raw: bytes) -> dict[str, dict[str, Any]]:
    pins: dict[str, dict[str, Any]] = {}
    current: str | None = None
    for source in raw.decode().splitlines():
        line = source.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN.fullmatch(line)
        if match:
            name, version = match.groups()
            name = _name(name)
            if name in pins:
                raise CandidateError(f"duplicate distribution: {name}")
            pins[name] = {"version": version, "hashes": []}
            current = name
            continue
        digest = HASH.fullmatch(line)
        if digest and current:
            pins[current]["hashes"].append(digest.group(1))
            continue
        raise CandidateError("lock has a directive, URL or malformed line")
    if not pins or any(not item["hashes"] for item in pins.values()):
        raise CandidateError("every distribution needs at least one hash")
    return pins


def _expected_candidate(product: bytes) -> bytes:
    replacements = (
        (
            b"pydantic-ai-slim==2.32.1 \\\n    --hash=sha256:0a2565e2dab5f3dab7c6627b749016c4af52a8c7ba4eccaba4a43d8f91bf5e6b \\\n    --hash=sha256:1bf26deaa6ac0ca038b9e23d76cd9683a47b5cd3bc56b666f5900fdc5547cf53",
            b"pydantic-ai-slim==2.36.0 \\\n    --hash=sha256:90957c37c99d3bfbfab4be632b733bf50d563686fbfbf8a2e42b6c91a4fca877",
        ),
        (
            b"pydantic-graph==2.32.1 \\\n    --hash=sha256:29a0cd70914db95b7b1f8a2ab19888f17757784e8b41ac100b894de2eed27993 \\\n    --hash=sha256:881ef6e5115cceedfbc03befa9ec127c32c5d41d6b8b8aa4a950459fb913d848",
            b"pydantic-graph==2.36.0 \\\n    --hash=sha256:c0c7cddb87038298b367036d799b4b605b2a23858869b2b661706df6e1b811f2",
        ),
    )
    result = product
    for old, new in replacements:
        if result.count(old) != 1:
            raise CandidateError("sealed product lock replacement block differs")
        result = result.replace(old, new)
    return result


def _expected_artifacts(
    coverage_raw: bytes, authority: dict[str, Any]
) -> list[dict[str, Any]]:
    coverage = json.loads(coverage_raw)
    artifacts = list(coverage["targets"][0]["artifacts"])
    removed = {item["from"]["filename"] for item in authority["replacements"]}
    artifacts = [item for item in artifacts if item["filename"] not in removed]
    artifacts.extend(item["to"] for item in authority["replacements"])
    return sorted(artifacts, key=lambda item: item["filename"])


def _validate_wheelhouse(path: Path, artifacts: list[dict[str, Any]]) -> None:
    if path.is_symlink() or not path.is_dir():
        raise CandidateError("wheelhouse must be a real directory")
    actual = {item.name: item for item in path.iterdir()}
    expected = {item["filename"]: item for item in artifacts}
    if set(actual) != set(expected):
        raise CandidateError("wheelhouse file set differs")
    for filename, artifact in expected.items():
        wheel = actual[filename]
        info = wheel.stat(follow_symlinks=False)
        if wheel.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CandidateError(f"unsafe wheel: {filename}")
        raw = wheel.read_bytes()
        if len(raw) != artifact["byteLength"] or _sha(raw) != artifact["sha256"]:
            raise CandidateError(f"wheel bytes differ: {filename}")


def inspect_candidate(
    product_raw: bytes,
    candidate_raw: bytes,
    authority_raw: bytes,
    coverage_raw: bytes,
    *,
    wheelhouse: Path | None = None,
) -> dict[str, Any]:
    authority = json.loads(authority_raw)
    if authority_raw not in (_canonical(authority), _canonical(authority) + b"\n"):
        raise CandidateError("authority is not canonical JSON")
    if authority.get("contract") != "Eva2PydanticAgentMergedWheelhouseAuthority/v1":
        raise CandidateError("authority contract differs")
    if authority.get("status") != "experimental_merged_lock_not_product_release":
        raise CandidateError("candidate is not explicitly experimental")
    if _sha(product_raw) != authority["productLock"]["sha256"]:
        raise CandidateError("product lock digest differs")
    if _sha(candidate_raw) != authority["candidateLock"]["sha256"]:
        raise CandidateError("candidate lock digest differs")
    if _sha(coverage_raw) != authority["baselineCoverage"]["sha256"]:
        raise CandidateError("baseline coverage digest differs")
    if candidate_raw != _expected_candidate(product_raw):
        raise CandidateError("candidate differs beyond the two sealed blocks")

    product = _pins(product_raw)
    candidate = _pins(candidate_raw)
    if len(product) != 38 or set(candidate) != set(product):
        raise CandidateError("candidate does not retain the 38 distributions")
    changed = {
        name: (product[name]["version"], candidate[name]["version"])
        for name in product
        if product[name] != candidate[name]
    }
    if changed != {
        "pydantic-ai-slim": ("2.32.1", "2.36.0"),
        "pydantic-graph": ("2.32.1", "2.36.0"),
    }:
        raise CandidateError("candidate package delta differs")
    if set(candidate) & FORBIDDEN:
        raise CandidateError("MCP extra or second HTTP stack entered candidate")
    if any(
        candidate[name] != product[name] for name in ("openai", "httpx2", "httpcore2")
    ):
        raise CandidateError("existing model/HTTP stack changed")

    artifacts = _expected_artifacts(coverage_raw, authority)
    manifest = _canonical(
        sorted((item["filename"], item["sha256"]) for item in artifacts)
    )
    expected = authority["expectedCandidateWheelhouse"]
    if (
        len(artifacts) != expected["artifactCount"]
        or _sha(manifest) != expected["selectedManifestSha256"]
    ):
        raise CandidateError("derived candidate wheel authority differs")
    if sum(item["byteLength"] for item in artifacts) != expected["byteLength"]:
        raise CandidateError("derived candidate wheel byte count differs")
    for artifact in artifacts:
        distribution = next(
            (
                name
                for name in candidate
                if artifact["sha256"] in candidate[name]["hashes"]
            ),
            None,
        )
        if distribution is None:
            raise CandidateError(
                f"wheel not admitted by candidate lock: {artifact['filename']}"
            )
    if wheelhouse is not None:
        _validate_wheelhouse(wheelhouse, artifacts)

    return {
        "contract": "Eva2PydanticAgentMergedLockInspection/v1",
        "status": "passed",
        "packageCount": len(candidate),
        "wheelCount": len(artifacts),
        "changedDistributions": changed,
        "mcpExtraPresent": False,
        "secondHttpStackPresent": False,
        "productLockChanged": False,
        "wheelBytesValidated": wheelhouse is not None,
    }


def main() -> None:
    receipt = inspect_candidate(
        PRODUCT_LOCK.read_bytes(),
        CANDIDATE_LOCK.read_bytes(),
        AUTHORITY.read_bytes(),
        COVERAGE.read_bytes(),
    )
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
