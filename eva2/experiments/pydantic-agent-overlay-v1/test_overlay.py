from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


EXPERIMENT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "pydantic_agent_overlay_checker", EXPERIMENT / "check_overlay.py"
)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


def _inputs() -> tuple[bytes, bytes, bytes]:
    return (
        checker.BASE_LOCK.read_bytes(),
        checker.OVERLAY_LOCK.read_bytes(),
        checker.AUTHORITY.read_bytes(),
    )


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _requirement(name: str, version: str, digest: str) -> str:
    return f"{name}=={version} {chr(92)}\n    --hash=sha256:{digest}"


def test_committed_overlay_is_exactly_two_replacements_over_live_lock() -> None:
    receipt = checker.inspect_overlay(*_inputs())
    assert receipt == {
        "contract": "Eva2PydanticAgentOverlayInspection/v1",
        "status": "passed",
        "basePackageCount": 38,
        "overlayWheelCount": 2,
        "mergedPackageCount": 38,
        "changedDistributions": {
            "pydantic-ai-slim": {"from": "2.32.1", "to": "2.36.0"},
            "pydantic-graph": {"from": "2.32.1", "to": "2.36.0"},
        },
        "mcpExtraPresent": False,
        "secondHttpStackPresent": False,
        "productLockChanged": False,
        "wheelBytesValidated": False,
    }


@pytest.mark.parametrize(
    "extra",
    (
        _requirement("mcp", "1.29.1", "1" * 64),
        _requirement("httpx", "0.28.1", "2" * 64),
        _requirement("fastmcp-slim", "3.4.7", "3" * 64),
    ),
)
def test_overlay_refuses_mcp_and_second_http_stack(extra: str) -> None:
    base, overlay, authority_raw = _inputs()
    candidate = overlay + b"\n" + extra.encode()
    authority = json.loads(authority_raw)
    authority["overlayLock"]["sha256"] = hashlib.sha256(candidate).hexdigest()
    with pytest.raises(checker.OverlayError, match="exactly the two approved"):
        checker.inspect_overlay(base, candidate, _canonical(authority))


def test_overlay_refuses_the_69_package_candidate_lock() -> None:
    base, overlay, authority_raw = _inputs()
    authority = json.loads(authority_raw)
    rejected = (
        EXPERIMENT.parent / "pydantic-mcp-discovery-v1/client-requirements.lock"
    ).read_bytes()
    rejected_binding = authority["explicitlyRejectedCandidate"]
    assert hashlib.sha256(rejected).hexdigest() == rejected_binding["sha256"]
    assert len(checker._parse_lock(rejected, overlay=False)) == 69
    assert hashlib.sha256(overlay).hexdigest() != rejected_binding["sha256"]
    with pytest.raises(checker.OverlayError):
        checker.inspect_overlay(base, rejected, authority_raw)


def test_overlay_refuses_product_baseline_drift() -> None:
    base, overlay, authority = _inputs()
    changed = base.replace(b"openai==3.6.0", b"openai==3.6.1", 1)
    with pytest.raises(checker.OverlayError, match="base requirements lock digest"):
        checker.inspect_overlay(changed, overlay, authority)


def test_overlay_refuses_authority_or_lock_drift() -> None:
    base, overlay, authority = _inputs()
    value = json.loads(authority)
    changed = copy.deepcopy(value)
    changed["mergedResult"]["packageCount"] = 69
    with pytest.raises(checker.OverlayError, match="distribution set"):
        checker.inspect_overlay(base, overlay, _canonical(changed))
    with pytest.raises(checker.OverlayError, match="overlay requirements lock digest"):
        checker.inspect_overlay(base, overlay + b"\n", authority)


def test_exact_two_wheelhouse_bytes_are_optional_but_fail_closed(
    tmp_path: Path,
) -> None:
    base, _, authority_raw = _inputs()
    authority = json.loads(authority_raw)
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    # Fixture bytes get a fixture-local authority. Production authority remains
    # bound to the two real PyPI wheel digests and lengths.
    for artifact in authority["wheelhouse"]["artifacts"]:
        raw = artifact["filename"].encode()
        artifact["byteLength"] = len(raw)
        artifact["sha256"] = hashlib.sha256(raw).hexdigest()
        (wheelhouse / artifact["filename"]).write_bytes(raw)
    fixture_overlay = "\n".join(
        _requirement(
            artifact["distribution"],
            artifact["version"],
            artifact["sha256"],
        )
        for artifact in authority["wheelhouse"]["artifacts"]
    ).encode()
    authority["overlayLock"]["sha256"] = hashlib.sha256(fixture_overlay).hexdigest()
    receipt = checker.inspect_overlay(
        base,
        fixture_overlay,
        _canonical(authority),
        wheelhouse=wheelhouse,
    )
    assert receipt["wheelBytesValidated"] is True
    (wheelhouse / authority["wheelhouse"]["artifacts"][0]["filename"]).write_bytes(
        b"tampered"
    )
    with pytest.raises(checker.OverlayError, match="wheel (size|digest) differs"):
        checker.inspect_overlay(
            base,
            fixture_overlay,
            _canonical(authority),
            wheelhouse=wheelhouse,
        )
