"""Fail closed when either exact MCP dependency comparison drifts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
TYPES = HERE.parent / "mcp-types-v1"
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^ \\]+)")
FULL_DELTA = {
    "attrs": "26.1.0",
    "jsonschema": "4.26.0",
    "jsonschema-specifications": "2025.9.1",
    "mcp": "2.1.1",
    "mcp-types": "2.1.1",
    "pyjwt": "2.13.0",
    "python-multipart": "0.0.32",
    "referencing": "0.37.0",
    "rpds-py": "2026.6.3",
    "sse-starlette": "3.4.8",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _delta(production: dict[str, str], candidate: dict[str, str]) -> dict[str, str]:
    if set(production) - set(candidate):
        raise RuntimeError("candidate removed a production distribution")
    return {
        name: version
        for name, version in candidate.items()
        if production.get(name) != version
    }


def check() -> dict[str, Any]:
    receipt = json.loads((HERE / "receipt.json").read_text(encoding="utf-8"))
    production_lock = ROOT / "requirements.lock"
    full_roots = HERE / "requirements.in"
    full_lock = HERE / "requirements.lock"
    types_roots = TYPES / "requirements.in"
    types_lock = TYPES / "requirements.lock"
    expected = {
        production_lock: "89555d14acd8bceb8786dfa8539af29672899e8259e6a2b5352e27ef9219e8dc",
        full_roots: receipt["fullSdk"]["rootsSha256"],
        full_lock: receipt["fullSdk"]["lockSha256"],
        types_roots: receipt["wireTypesOnly"]["rootsSha256"],
        types_lock: receipt["wireTypesOnly"]["lockSha256"],
    }
    for path, digest in expected.items():
        if _sha256(path) != digest:
            raise RuntimeError(f"MCP bakeoff hash differs: {path.relative_to(ROOT)}")

    production = _pins(production_lock)
    full = _pins(full_lock)
    types = _pins(types_lock)
    if len(production) != 38 or len(full) != 48 or len(types) != 39:
        raise RuntimeError("MCP bakeoff distribution count differs")
    if _delta(production, full) != FULL_DELTA:
        raise RuntimeError("full MCP SDK delta is not exact")
    if _delta(production, types) != {"mcp-types": "2.1.1"} or "mcp" in types:
        raise RuntimeError("MCP wire-types-only delta is not exact")
    if (
        receipt["decision"] != "no_go_for_narrow_v1_client"
        or receipt["productionAuthority"] is not False
        or receipt["managedBChanged"] is not False
        or receipt["liveProviderCalled"] is not False
        or receipt["ephemeralPrototype"]["persisted"] is not False
    ):
        raise RuntimeError("MCP bakeoff nonclaims differ")
    return {
        "candidateId": receipt["candidateId"],
        "decision": receipt["decision"],
        "fullSdkDistributions": len(full),
        "wireTypesOnlyDistributions": len(types),
    }


if __name__ == "__main__":
    print(json.dumps(check(), separators=(",", ":"), sort_keys=True))
