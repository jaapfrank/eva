from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "merged_lock_checker", HERE / "check_candidate.py"
)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


def _inputs() -> tuple[bytes, bytes, bytes, bytes]:
    return (
        checker.PRODUCT_LOCK.read_bytes(),
        checker.CANDIDATE_LOCK.read_bytes(),
        checker.AUTHORITY.read_bytes(),
        checker.COVERAGE.read_bytes(),
    )


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _authority_for(candidate: bytes, authority_raw: bytes) -> bytes:
    authority = json.loads(authority_raw)
    authority["candidateLock"]["sha256"] = hashlib.sha256(candidate).hexdigest()
    return _canonical(authority)


def test_committed_full_candidate_retains_exact_38_distribution_set() -> None:
    assert checker.PRODUCT_LOCK.name == "requirements-4d-baseline-2.32.lock"
    assert hashlib.sha256(checker.PRODUCT_LOCK.read_bytes()).hexdigest() == (
        "ce92ff9385c0b708ea0596b4cbfdfe5473379a744d1eb3dc6c1080959d17334f"
    )
    assert checker.COVERAGE.name == "managed-b-wheel-coverage-2.32-amd64.json"
    assert hashlib.sha256(checker.COVERAGE.read_bytes()).hexdigest() == (
        "0eceb7bce89a847680cccf8c0a12a82fc6a3cbe3701f1dc0d1ea09c413ac382c"
    )
    receipt = checker.inspect_candidate(*_inputs())
    assert receipt == {
        "contract": "Eva2PydanticAgentMergedLockInspection/v1",
        "status": "passed",
        "packageCount": 38,
        "wheelCount": 38,
        "changedDistributions": {
            "pydantic-ai-slim": ("2.32.1", "2.36.0"),
            "pydantic-graph": ("2.32.1", "2.36.0"),
        },
        "mcpExtraPresent": False,
        "secondHttpStackPresent": False,
        "productLockChanged": False,
        "wheelBytesValidated": False,
    }


def test_candidate_refuses_any_third_version_or_hash_change() -> None:
    product, candidate, authority, coverage = _inputs()
    changed = candidate.replace(b"click==8.4.2", b"click==8.5.0", 1)
    with pytest.raises(checker.CandidateError, match="beyond the two sealed blocks"):
        checker.inspect_candidate(
            product, changed, _authority_for(changed, authority), coverage
        )
    changed = candidate.replace(
        b"e6f9f66136c816745b9d65817da91d61d957fb16e02e4dcd0552553c5a197b76",
        b"0" * 64,
        1,
    )
    with pytest.raises(checker.CandidateError, match="beyond the two sealed blocks"):
        checker.inspect_candidate(
            product, changed, _authority_for(changed, authority), coverage
        )


@pytest.mark.parametrize("name", ("mcp", "fastmcp-slim", "httpx", "httpcore"))
def test_candidate_refuses_mcp_extra_and_second_http_stack(name: str) -> None:
    product, candidate, authority, coverage = _inputs()
    extra = f"\n{name}==1.0.0 {chr(92)}\n    --hash=sha256:{'1' * 64}\n".encode()
    changed = candidate + extra
    with pytest.raises(checker.CandidateError, match="beyond the two sealed blocks"):
        checker.inspect_candidate(
            product, changed, _authority_for(changed, authority), coverage
        )


def test_candidate_refuses_previous_69_package_lock() -> None:
    product, _, authority, coverage = _inputs()
    rejected = (
        HERE.parent / "pydantic-mcp-discovery-v1/client-requirements.lock"
    ).read_bytes()
    with pytest.raises(checker.CandidateError):
        checker.inspect_candidate(
            product, rejected, _authority_for(rejected, authority), coverage
        )


def test_candidate_refuses_product_or_coverage_drift() -> None:
    product, candidate, authority, coverage = _inputs()
    with pytest.raises(checker.CandidateError, match="historical baseline authority"):
        checker.inspect_candidate(product + b"\n", candidate, authority, coverage)
    with pytest.raises(checker.CandidateError, match="historical coverage authority"):
        checker.inspect_candidate(product, candidate, authority, coverage + b"\n")


@pytest.mark.parametrize(
    ("binding", "path"),
    (
        ("productLock", "eva2/requirements-4d.lock"),
        (
            "baselineCoverage",
            "eva2/release/managed-b-wheel-coverage-4d-amd64-v1.json",
        ),
    ),
)
def test_candidate_refuses_rebinding_to_active_inputs(binding: str, path: str) -> None:
    product, candidate, authority_raw, coverage = _inputs()
    authority = json.loads(authority_raw)
    authority[binding]["path"] = path
    with pytest.raises(checker.CandidateError, match="historical .* authority"):
        checker.inspect_candidate(product, candidate, _canonical(authority), coverage)


def test_wheelhouse_validator_refuses_missing_extra_and_changed_bytes(
    tmp_path: Path,
) -> None:
    artifacts = [
        {
            "filename": "alpha-1-py3-none-any.whl",
            "byteLength": 5,
            "sha256": hashlib.sha256(b"alpha").hexdigest(),
        },
        {
            "filename": "beta-1-py3-none-any.whl",
            "byteLength": 4,
            "sha256": hashlib.sha256(b"beta").hexdigest(),
        },
    ]
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / artifacts[0]["filename"]).write_bytes(b"alpha")
    (wheelhouse / artifacts[1]["filename"]).write_bytes(b"beta")
    checker._validate_wheelhouse(wheelhouse, artifacts)
    (wheelhouse / artifacts[1]["filename"]).write_bytes(b"wrong")
    with pytest.raises(checker.CandidateError, match="wheel bytes differ"):
        checker._validate_wheelhouse(wheelhouse, artifacts)
    (wheelhouse / artifacts[1]["filename"]).write_bytes(b"beta")
    (wheelhouse / "extra.whl").write_bytes(b"extra")
    with pytest.raises(checker.CandidateError, match="file set"):
        checker._validate_wheelhouse(wheelhouse, artifacts)
