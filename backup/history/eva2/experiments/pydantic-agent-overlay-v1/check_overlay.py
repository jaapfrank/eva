"""Fail-closed checker for the isolated Pydantic AI 2.37 overlay.

The overlay is deliberately not a standalone runtime lock.  It replaces three
already-present distributions in the frozen historical 2.36 baseline
and is installed with ``--no-deps`` from exactly three wheel bytes.  This module
does not install anything and does not import product code.
"""

from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any
import zipfile


EXPERIMENT_ROOT = Path(__file__).resolve().parent
BASE_LOCK = EXPERIMENT_ROOT / "requirements-4d-baseline-2.36.lock"
OVERLAY_LOCK = EXPERIMENT_ROOT / "requirements-overlay.lock"
AUTHORITY = EXPERIMENT_ROOT / "wheelhouse-authority.json"

PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)(?:\s*\\)?$")
HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})(?:\s*\\)?$")
FORBIDDEN_DIRECTIVE = re.compile(
    r"^\s*(?:-r|--requirement|-c|--constraint|-i|--index-url|"
    r"--extra-index-url|--find-links|--trusted-host)\b"
)
EXPECTED_OVERLAY = {
    "genai-prices": "0.1.5",
    "pydantic-ai-slim": "2.37.0",
    "pydantic-graph": "2.37.0",
}
EXPECTED_BASE_REPLACED = {
    "genai-prices": "0.1.4",
    "pydantic-ai-slim": "2.36.0",
    "pydantic-graph": "2.36.0",
}
FORBIDDEN_MCP_DISTRIBUTIONS = {"fastmcp", "fastmcp-slim", "mcp"}
FORBIDDEN_SECOND_HTTP_STACK = {"httpcore", "httpx"}
EXPECTED_MERGED_DEPENDENCIES = {
    "anyio": "4.14.2",
    "genai-prices": "0.1.5",
    "griffelib": "2.2.0",
    "httpcore2": "2.12.0",
    "httpx2": "2.12.0",
    "logfire-api": "4.41.0",
    "openai": "3.6.0",
    "opentelemetry-api": "1.44.0",
    "pydantic": "2.12.5",
    "typing-inspection": "0.4.4",
}
EXPECTED_ARTIFACTS = [
    {
        "byteLength": 116330,
        "distribution": "genai-prices",
        "filename": "genai_prices-0.1.5-py3-none-any.whl",
        "sha256": "5215706950decd833ce3527a9e1e745ad0dbdd35ce1b33ac1f7167699640b82a",
        "version": "0.1.5",
    },
    {
        "byteLength": 1552945,
        "distribution": "pydantic-ai-slim",
        "filename": "pydantic_ai_slim-2.37.0-py3-none-any.whl",
        "sha256": "439973ce2dae1e64631b05b803743fe44b3b1791514b16488c005b1ca4f2b3e7",
        "version": "2.37.0",
    },
    {
        "byteLength": 52701,
        "distribution": "pydantic-graph",
        "filename": "pydantic_graph-2.37.0-py3-none-any.whl",
        "sha256": "7b07237264b92f57aa06b1686315005e320099106534fc20da4081873f68c31f",
        "version": "2.37.0",
    },
]
EXPECTED_INSTALL_PROTOCOL = {
    "cleanBaselineRequired": True,
    "commandShape": [
        "python",
        "-m",
        "pip",
        "install",
        "--no-index",
        "--only-binary=:all:",
        "--no-deps",
        "--require-hashes",
        "--find-links=<sealed-wheelhouse>",
        "-r",
        "requirements-overlay.lock",
    ],
    "networkAllowed": False,
    "postInstallChecks": [
        "pip check",
        "genai-prices==0.1.5",
        "pydantic-ai-slim==2.37.0",
        "pydantic-graph==2.37.0",
        "openai==3.6.0",
        "httpx2==2.12.0",
        "httpcore2==2.12.0",
        "mcp/fastmcp/httpx/httpcore absent",
    ],
}
EXPECTED_CORE_REQUIRES_DIST = {
    "genai-prices": ["httpx2>=2.0", "pydantic>=2.10"],
    "pydantic-ai-slim": [
        "anyio>=4.7.0",
        "exceptiongroup>=1.2.2; python_version < '3.11'",
        "genai-prices>=0.1.5",
        "griffelib>=2.0",
        "httpx2>=2.7",
        "opentelemetry-api>=1.28.0",
        "pydantic-graph==2.37.0",
        "pydantic>=2.12",
        "typing-inspection>=0.4.0",
    ],
    "pydantic-graph": [
        "anyio>=4.7.0",
        "logfire-api>=3.14.1",
        "pydantic>=2.12",
        "typing-inspection>=0.4.0",
    ],
}
EXPECTED_NON_CLAIMS = [
    "This authority does not change or replace eva2/requirements-4d.lock.",
    "This authority does not promote Pydantic AI's mcp or openai extras.",
    "This authority does not prove provider, tool, streaming, crash, latency or A/B parity.",
    "The three wheel bytes are selected experimental inputs, not a live release.",
]
EXPECTED_REJECTED_CANDIDATE = {
    "packageCount": 69,
    "path": "eva2/experiments/pydantic-mcp-discovery-v1/client-requirements.lock",
    "reason": (
        "The standalone MCP/OpenAI-extra candidate is not a merged product lock "
        "and adds MCP plus a second legacy httpx/httpcore stack."
    ),
    "sha256": "b72c670c4c119232c5c3c82396e8db39275b605b793530659c7fc396f316a151",
}


class OverlayError(RuntimeError):
    """The candidate is not the exact sealed three-package overlay."""


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
        try:
            with zipfile.ZipFile(wheel) as archive:
                metadata_names = [
                    entry
                    for entry in archive.namelist()
                    if entry.endswith(".dist-info/METADATA")
                ]
                if len(metadata_names) != 1:
                    raise OverlayError(f"wheel METADATA count differs: {name}")
                metadata = BytesParser().parsebytes(
                    archive.read(metadata_names[0]), headersonly=True
                )
        except zipfile.BadZipFile as exc:
            raise OverlayError(f"wheel is not a valid ZIP archive: {name}") from exc
        if _normalise(str(metadata["Name"])) != _normalise(artifact["distribution"]):
            raise OverlayError(f"wheel METADATA name differs: {name}")
        if metadata["Version"] != artifact["version"]:
            raise OverlayError(f"wheel METADATA version differs: {name}")
        core_requirements = [
            requirement
            for requirement in metadata.get_all("Requires-Dist", [])
            if "extra ==" not in requirement
        ]
        if core_requirements != EXPECTED_CORE_REQUIRES_DIST[artifact["distribution"]]:
            raise OverlayError(f"wheel core requirements differ: {name}")


def _validate_authority_shape(
    authority: dict[str, Any],
    *,
    base_raw: bytes,
    overlay_raw: bytes,
    changed: dict[str, dict[str, str]],
    merged_package_count: int,
) -> None:
    expected_keys = {
        "baseLock",
        "contract",
        "decisionDate",
        "explicitlyRejectedCandidate",
        "installProtocol",
        "mergedResult",
        "mergedSatisfiedCoreDependencies",
        "nonClaims",
        "overlayLock",
        "status",
        "wheelhouse",
    }
    if set(authority) != expected_keys:
        raise OverlayError("authority top-level schema differs")
    if (
        _sha256(base_raw)
        != "204fefe5052a2f457b8a12aece51ca675520bb250bf97771a69a7e22e14c81c5"
    ):
        raise OverlayError("base requirements lock digest differs")
    if (
        _sha256(overlay_raw)
        != "ed4b36d9c217a0c8c2f41d95be4fae300414c71a1e2a3f8b740fe7a1db62a027"
    ):
        raise OverlayError("overlay requirements lock digest differs")
    if authority["baseLock"] != {
        "packageCount": 38,
        "path": (
            "eva2/experiments/pydantic-agent-overlay-v1/"
            "requirements-4d-baseline-2.36.lock"
        ),
        "sha256": _sha256(base_raw),
    }:
        raise OverlayError("base lock authority differs")
    if authority["overlayLock"] != {
        "installWithNoDeps": True,
        "path": "eva2/experiments/pydantic-agent-overlay-v1/requirements-overlay.lock",
        "sha256": _sha256(overlay_raw),
    }:
        raise OverlayError("overlay lock authority differs")
    if authority["decisionDate"] != "2026-09-01":
        raise OverlayError("decision date differs")
    if authority["installProtocol"] != EXPECTED_INSTALL_PROTOCOL:
        raise OverlayError("install protocol differs")
    if authority["explicitlyRejectedCandidate"] != EXPECTED_REJECTED_CANDIDATE:
        raise OverlayError("rejected candidate binding differs")
    if authority["mergedSatisfiedCoreDependencies"] != EXPECTED_MERGED_DEPENDENCIES:
        raise OverlayError("merged dependency authority differs")
    if authority["mergedResult"] != {
        "changedDistributions": changed,
        "newDistributions": [],
        "packageCount": merged_package_count,
        "removedDistributions": [],
        "unchangedTransport": {
            "httpcore2": "2.12.0",
            "httpx2": "2.12.0",
            "openai": "3.6.0",
        },
    }:
        raise OverlayError("merged result authority differs")
    if authority["nonClaims"] != EXPECTED_NON_CLAIMS:
        raise OverlayError("non-claims differ")
    if authority["wheelhouse"] != {
        "artifacts": EXPECTED_ARTIFACTS,
        "expectedWheelCount": 3,
        "onlyBinary": True,
        "source": "PyPI release wheel bytes",
        "target": {"implementation": "CPython", "pythonVersion": "3.12.14"},
    }:
        raise OverlayError("wheelhouse authority differs")


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
    versions = {name: value["version"] for name, value in overlay.items()}
    if versions != EXPECTED_OVERLAY:
        raise OverlayError("overlay is not exactly the three approved upgrades")
    for name, version in EXPECTED_BASE_REPLACED.items():
        if base.get(name, {}).get("version") != version:
            raise OverlayError(f"baseline pin differs for {name}")

    final = dict(base)
    final.update(overlay)
    if set(final) != set(base) or len(final) != len(base):
        raise OverlayError("overlay changes the runtime distribution set")
    changed = {
        name: {"from": base[name]["version"], "to": final[name]["version"]}
        for name in final
        if base[name]["version"] != final[name]["version"]
    }
    _validate_authority_shape(
        authority,
        base_raw=base_raw,
        overlay_raw=overlay_raw,
        changed=changed,
        merged_package_count=len(final),
    )

    final_names = set(final)
    if final_names & FORBIDDEN_MCP_DISTRIBUTIONS:
        raise OverlayError("MCP distribution entered the minimal overlay")
    if final_names & FORBIDDEN_SECOND_HTTP_STACK:
        raise OverlayError("a second HTTP stack entered the minimal overlay")
    for name in ("httpx2", "httpcore2", "openai"):
        if final.get(name, {}).get("version") != base.get(name, {}).get("version"):
            raise OverlayError(f"existing transport pin changed: {name}")

    required_merged = authority["mergedSatisfiedCoreDependencies"]
    for name, expected in required_merged.items():
        if final.get(name, {}).get("version") != expected:
            raise OverlayError(f"audited core dependency differs: {name}")

    artifacts = authority["wheelhouse"]["artifacts"]
    lock_hashes = {name: value["hashes"][0] for name, value in overlay.items()}
    artifact_hashes = {
        _normalise(item["distribution"]): item["sha256"] for item in artifacts
    }
    if artifact_hashes != lock_hashes or len(artifacts) != 3:
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", type=Path, required=True)
    args = parser.parse_args()
    receipt = inspect_overlay(
        BASE_LOCK.read_bytes(),
        OVERLAY_LOCK.read_bytes(),
        AUTHORITY.read_bytes(),
        wheelhouse=args.wheelhouse,
    )
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
