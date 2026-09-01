from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

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


def test_committed_overlay_is_exactly_three_replacements_over_frozen_236() -> None:
    assert checker.BASE_LOCK.name == "requirements-4d-baseline-2.36.lock"
    assert hashlib.sha256(checker.BASE_LOCK.read_bytes()).hexdigest() == (
        "204fefe5052a2f457b8a12aece51ca675520bb250bf97771a69a7e22e14c81c5"
    )
    receipt = checker.inspect_overlay(*_inputs())
    assert receipt == {
        "contract": "Eva2PydanticAgentOverlayInspection/v1",
        "status": "passed",
        "basePackageCount": 38,
        "overlayWheelCount": 3,
        "mergedPackageCount": 38,
        "changedDistributions": {
            "genai-prices": {"from": "0.1.4", "to": "0.1.5"},
            "pydantic-ai-slim": {"from": "2.36.0", "to": "2.37.0"},
            "pydantic-graph": {"from": "2.36.0", "to": "2.37.0"},
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
    with pytest.raises(checker.OverlayError, match="exactly the three approved"):
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
    with pytest.raises(checker.OverlayError, match="merged result authority"):
        checker.inspect_overlay(base, overlay, _canonical(changed))
    with pytest.raises(checker.OverlayError, match="overlay requirements lock digest"):
        checker.inspect_overlay(base, overlay + b"\n", authority)


@pytest.mark.parametrize(
    "case",
    (
        "install_with_deps",
        "external_index",
        "wrong_wheel_count",
        "wrong_filename",
        "wrong_size",
        "invented_distribution",
        "extra_top_level_key",
    ),
)
def test_authority_safety_and_claim_fields_are_all_fail_closed(case: str) -> None:
    base, overlay, authority_raw = _inputs()
    authority = json.loads(authority_raw)
    if case == "install_with_deps":
        authority["overlayLock"]["installWithNoDeps"] = False
    elif case == "external_index":
        authority["installProtocol"]["commandShape"] = [
            "python",
            "-m",
            "pip",
            "install",
            "--index-url=https://example.invalid/simple",
        ]
    elif case == "wrong_wheel_count":
        authority["wheelhouse"]["expectedWheelCount"] = 99
    elif case == "wrong_filename":
        authority["wheelhouse"]["artifacts"][0]["filename"] = "other.whl"
    elif case == "wrong_size":
        authority["wheelhouse"]["artifacts"][0]["byteLength"] += 1
    elif case == "invented_distribution":
        authority["mergedResult"]["newDistributions"] = ["evil"]
    elif case == "extra_top_level_key":
        authority["unvalidated"] = True
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(case)
    with pytest.raises(checker.OverlayError):
        checker.inspect_overlay(base, overlay, _canonical(authority))


def test_physical_three_wheelhouse_bytes_are_fail_closed(
    tmp_path: Path,
) -> None:
    _, _, authority_raw = _inputs()
    authority = json.loads(authority_raw)
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    # Fixture wheels exercise the physical validator without weakening the
    # committed authority's binding to the three real PyPI wheel bytes.
    for artifact in authority["wheelhouse"]["artifacts"]:
        wheel_path = wheelhouse / artifact["filename"]
        dist_info = artifact["filename"].split("-py3-none-any.whl", 1)[0]
        metadata = [
            "Metadata-Version: 2.4",
            f"Name: {artifact['distribution']}",
            f"Version: {artifact['version']}",
        ]
        metadata.extend(
            f"Requires-Dist: {requirement}"
            for requirement in checker.EXPECTED_CORE_REQUIRES_DIST[
                artifact["distribution"]
            ]
        )
        with zipfile.ZipFile(wheel_path, "w") as archive:
            archive.writestr(
                f"{dist_info}.dist-info/METADATA", "\n".join(metadata) + "\n"
            )
        raw = wheel_path.read_bytes()
        artifact["byteLength"] = len(raw)
        artifact["sha256"] = hashlib.sha256(raw).hexdigest()
    checker._validate_wheelhouse(wheelhouse, authority["wheelhouse"]["artifacts"])
    (wheelhouse / authority["wheelhouse"]["artifacts"][0]["filename"]).write_bytes(
        b"tampered"
    )
    with pytest.raises(checker.OverlayError, match="wheel (size|digest) differs"):
        checker._validate_wheelhouse(wheelhouse, authority["wheelhouse"]["artifacts"])


def test_cli_requires_a_physical_wheelhouse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["check_overlay.py"])
    with pytest.raises(SystemExit) as error:
        checker.main()
    assert error.value.code == 2
