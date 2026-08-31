"""Fail-closed checker for the isolated Pydantic AI 2.36 overlay.

The overlay is deliberately not a standalone runtime lock.  It replaces two
already-present distributions in the sealed ``requirements-4d.lock`` baseline
and is installed with ``--no-deps`` from exactly two wheel bytes.  This module
does not install anything and does not import product code.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any


EVA2_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = Path(__file__).resolve().parent
BASE_LOCK = EVA2_ROOT / "requirements-4d.lock"
OVERLAY_LOCK = EXPERIMENT_ROOT / "requirements-overlay.lock"
AUTHORITY = EXPERIMENT_ROOT / "wheelhouse-authority.json"

PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)(?:\s*\\)?$")
HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})(?:\s*\\)?$")
FORBIDDEN_DIRECTIVE = re.compile(
    r"^\s*(?:-r|--requirement|-c|--constraint|-i|--index-url|"
    r"--extra-index-url|--find-links|--trusted-host)\b"
)
EXPECTED_OVERLAY = {
    "pydantic-ai-slim": "2.36.0",
    "pydantic-graph": "2.36.0",
}
EXPECTED_BASE_REPLACED = {
    "pydantic-ai-slim": "2.32.1",
    "pydantic-graph": "2.32.1",
}
FORBIDDEN_MCP_DISTRIBUTIONS = {"fastmcp", "fastmcp-slim", "mcp"}
FORBIDDEN_SECOND_HTTP_STACK = {"httpcore", "httpx"}


class OverlayError(RuntimeError):
    """The candidate is not the exact sealed two-package overlay."""


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _parse_lock(raw: bytes, *, overlay: bool) -> dict[str, dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OverlayError("requirements lock is not UTF-8") from exc
    pins: dict[str, dict[str, Any]] = {}
    current: str | None = None
    for number, source_line in enumerate(text.splitlines(), start=1):
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        if FORBIDDEN_DIRECTIVE.match(line):
            raise OverlayError(f"requirements directive forbidden at line {number}")
        if "://" in line or " @ " in line:
            raise OverlayError(f"URL requirement forbidden at line {number}")
        pin = PIN.match(line)
        if pin is not None:
            raw_name, version = pin.groups()
            if "[" in raw_name or "]" in raw_name:
                raise OverlayError(f"dependency extras forbidden at line {number}")
            name = _normalise(raw_name)
            if name in pins:
                raise OverlayError(f"duplicate requirement: {name}")
            pins[name] = {"version": version, "hashes": []}
            current = name
            continue
        digest = HASH.fullmatch(line)
        if digest is not None and current is not None:
            pins[current]["hashes"].append(digest.group(1))
            continue
        # pip-compile provenance comments are already skipped. Any other
        # executable-looking line makes the lock ambiguous.
        if line.startswith("--hash="):
            raise OverlayError(f"orphan or malformed hash at line {number}")
        if overlay:
            raise OverlayError(f"unexpected overlay lock content at line {number}")
    if overlay:
        for name, value in pins.items():
            hashes = value["hashes"]
            if len(hashes) != 1:
                raise OverlayError(f"overlay requirement {name} needs one wheel hash")
    return pins


def _load_authority(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OverlayError("authority is not valid JSON") from exc
    if not isinstance(value, dict):
        raise OverlayError("authority must be a JSON object")
    canonical = _canonical_json(value)
    if raw not in (canonical, canonical + b"\n"):
        raise OverlayError("authority is not canonical JSON")
    return value


def _validate_wheelhouse(path: Path, artifacts: list[dict[str, Any]]) -> None:
    if path.is_symlink() or not path.is_dir():
        raise OverlayError("wheelhouse must be a real directory")
    expected = {item["filename"]: item for item in artifacts}
    actual = {item.name: item for item in path.iterdir()}
    if set(actual) != set(expected):
        raise OverlayError("wheelhouse file set differs from authority")
    for name, artifact in expected.items():
        wheel = actual[name]
        wheel_stat = wheel.stat(follow_symlinks=False)
        if wheel.is_symlink() or not stat.S_ISREG(wheel_stat.st_mode):
            raise OverlayError(f"unsafe wheelhouse entry: {name}")
        if wheel_stat.st_nlink != 1:
            raise OverlayError(f"wheelhouse hardlink forbidden: {name}")
        raw = wheel.read_bytes()
        if len(raw) != artifact["byteLength"]:
            raise OverlayError(f"wheel size differs: {name}")
        if _sha256(raw) != artifact["sha256"]:
            raise OverlayError(f"wheel digest differs: {name}")


def inspect_overlay(
    base_raw: bytes,
    overlay_raw: bytes,
    authority_raw: bytes,
    *,
    wheelhouse: Path | None = None,
) -> dict[str, Any]:
    base = _parse_lock(base_raw, overlay=False)
    overlay = _parse_lock(overlay_raw, overlay=True)
    authority = _load_authority(authority_raw)

    if authority.get("contract") != "Eva2PydanticAgentOverlayAuthority/v1":
        raise OverlayError("unexpected overlay authority contract")
    if authority.get("status") != "experimental_not_product_lock":
        raise OverlayError("overlay authority status is not experimental")
    if authority["baseLock"]["sha256"] != _sha256(base_raw):
        raise OverlayError("base requirements lock digest differs")
    if authority["overlayLock"]["sha256"] != _sha256(overlay_raw):
        raise OverlayError("overlay requirements lock digest differs")
    if authority["baseLock"]["packageCount"] != len(base):
        raise OverlayError("base package count differs")

    versions = {name: value["version"] for name, value in overlay.items()}
    if versions != EXPECTED_OVERLAY:
        raise OverlayError("overlay is not exactly the two approved upgrades")
    for name, version in EXPECTED_BASE_REPLACED.items():
        if base.get(name, {}).get("version") != version:
            raise OverlayError(f"baseline pin differs for {name}")

    final = dict(base)
    final.update(overlay)
    if (
        set(final) != set(base)
        or len(final) != authority["mergedResult"]["packageCount"]
    ):
        raise OverlayError("overlay changes the runtime distribution set")
    changed = {
        name: {"from": base[name]["version"], "to": final[name]["version"]}
        for name in final
        if base[name]["version"] != final[name]["version"]
    }
    if changed != authority["mergedResult"]["changedDistributions"]:
        raise OverlayError("merged version delta differs from authority")

    final_names = set(final)
    if final_names & FORBIDDEN_MCP_DISTRIBUTIONS:
        raise OverlayError("MCP distribution entered the minimal overlay")
    if final_names & FORBIDDEN_SECOND_HTTP_STACK:
        raise OverlayError("a second HTTP stack entered the minimal overlay")
    for name in ("httpx2", "httpcore2", "openai"):
        if final.get(name, {}).get("version") != base.get(name, {}).get("version"):
            raise OverlayError(f"existing transport pin changed: {name}")

    required_base = authority["baseSatisfiedCoreDependencies"]
    for name, expected in required_base.items():
        if final.get(name, {}).get("version") != expected:
            raise OverlayError(f"audited core dependency differs: {name}")

    artifacts = authority["wheelhouse"]["artifacts"]
    lock_hashes = {name: value["hashes"][0] for name, value in overlay.items()}
    artifact_hashes = {
        _normalise(item["distribution"]): item["sha256"] for item in artifacts
    }
    if artifact_hashes != lock_hashes or len(artifacts) != 2:
        raise OverlayError("wheel authority and overlay hashes differ")
    if wheelhouse is not None:
        _validate_wheelhouse(wheelhouse, artifacts)

    forbidden_lock = authority["explicitlyRejectedCandidate"]
    if forbidden_lock["sha256"] == _sha256(overlay_raw):
        raise OverlayError("the rejected 69-package candidate was substituted")

    return {
        "contract": "Eva2PydanticAgentOverlayInspection/v1",
        "status": "passed",
        "basePackageCount": len(base),
        "overlayWheelCount": len(overlay),
        "mergedPackageCount": len(final),
        "changedDistributions": changed,
        "mcpExtraPresent": False,
        "secondHttpStackPresent": False,
        "productLockChanged": False,
        "wheelBytesValidated": wheelhouse is not None,
    }


def main() -> None:
    receipt = inspect_overlay(
        BASE_LOCK.read_bytes(),
        OVERLAY_LOCK.read_bytes(),
        AUTHORITY.read_bytes(),
    )
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
