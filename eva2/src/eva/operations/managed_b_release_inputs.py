"""Validate one version-matched managed-B runtime-input authority.

The source archive remains the authority for bytes.  This module only proves
that the selected commit contains the exact runtime registry, current
dependency projection, migration chain, build inputs and test files named by
the release authority.  It does not build, deploy, inspect secrets or open B.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

from eva.operations.managed_b_wheelhouse import inspect_runtime_dependency_alignment
from tools.prepare_managed_b_candidate_context import (
    prepare_candidate_build_inputs,
    validate_candidate_build_inputs,
)


AUTHORITY_PATH = "release/managed-b-release-input-authority-v1.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_AUTHORITY_BYTES = 64 * 1024
_MANIFEST_PATH = "schemas/contracts/manifest.json"
_CORPUS_PATH = "schemas/contracts/contract-corpus-v1.json"
_PROFILE_PATH = "schemas/contracts/canonical-json-profile-v1.json"
_FOUR_D_FREEZE_PATH = "design/vertical/noor-4d-schema-export-freeze-v1.json"
_COCKPIT_EVIDENCE_PATH = "release/eva-cockpit-evidence-index-v1.json"
_COCKPIT_PUBLIC_EVIDENCE_PATH = "evidence/public/eva-cockpit-sanitized-evidence-v1.json"
_PORTABLE_V1_CONTRACT_ROOT = "schemas/portable/v1/contracts"
_PORTABLE_V1_MANIFEST_PATH = f"{_PORTABLE_V1_CONTRACT_ROOT}/manifest.json"
_PORTABLE_V1_PROFILE_PATH = (
    f"{_PORTABLE_V1_CONTRACT_ROOT}/canonical-json-profile-v1.json"
)
_EXPECTED_INPUT_PATHS_SHA256 = (
    "d534f49a3dd18ecfdb22e7ae6da96d906550a0df79fe2708d90461f56bd14c1c"
)
_EXPECTED_TEST_PATHS_SHA256 = (
    "a91e91fae1f53be748f4f7912c3005698183cc04a145924d1581373cf78e8a82"
)
_NONCLAIMS = (
    "No image, container, Compose graph, database, volume, network, secret, A service, or B service was opened.",
    "This static authority does not prove a build, deploy, release, B adoption, live Telegram, live Home Assistant, another capability provider, or production readiness.",
    "The pinned test files are inputs only; this authority does not prove collection, execution, results, skips, xfails, environment, toolchain, or complete regression coverage.",
    "The active amd64 dependency inputs are version-matched to the 35-package requirements-4d.lock compatibility path; the stock 38-wheel authority remains historical and is not a fallback build input.",
    "The exact offline installer command, post-install attestations, physical 35-wheel wheelhouse, and runtime import remain separate open gates.",
    "The historical 4D authority remains a 64-contract compatibility baseline; this authority does not reseal it as the current 71-contract registry.",
    "The immutable 0/0-scanned core now serves the explicit /eva2 canary after a green forward/rollback/forward switch; full nine-service managed-B cutover, reboot and resident acceptance remain open gates.",
)
_COCKPIT_EVIDENCE_NONCLAIMS = (
    "The cockpit index is a release-bound evidence projection, not a new live run.",
    "A source receipt proves only the claim and nonclaims written in that receipt.",
    "Selected and open cockpit items are deliberately absent from this evidence index.",
)
_SANITIZED_COCKPIT_NONCLAIMS = (
    "This projection excludes raw dialogue, prompts, replies, names, identifiers, entity metadata, memory content, secrets, and provider payloads.",
    "Source receipt hashes allow private re-opening by the owner; raw receipts are not release inputs.",
    "Historical live results do not prove the final image.",
    "This projection is not a new live execution.",
)
_SANITIZED_COCKPIT_CLAIMS = {
    "capability/home-skill": (
        "live",
        (
            "automation_config_readback_verified",
            "end_image_rerun_open",
            "historical_b_live_execution",
            "real_history_read",
            "real_light_action_with_independent_readback",
            "real_state_read",
        ),
    ),
    "capability/learning": (
        "tested",
        (
            "one_repository_improvement_committed",
            "repeatable_skill_cycle_not_proven",
        ),
    ),
    "capability/living-core": (
        "live",
        (
            "15_unique_cases_passed_live_b",
            "end_image_rerun_open",
            "historical_b_live_execution",
        ),
    ),
    "capability/personal-memory": (
        "live",
        (
            "end_image_rerun_open",
            "memory_isolation_passed_live_b",
            "person_project_memory_passed_live_b",
        ),
    ),
    "dependency/hindsight": (
        "live",
        (
            "candidate_b_ready",
            "real_model_turn_completed",
            "telegram_inbound_gate_open",
        ),
    ),
}
_SANITIZED_CORE_CASES = (
    "C01",
    "C02",
    "C03",
    "C04",
    "M02",
    "M03",
    "M04",
    "P02",
    "R02",
    "R03",
    "T02",
    "T04",
    "X02",
    "X03",
    "X04",
)
_COCKPIT_EVIDENCE_STATES = {
    "capability/browser": "tested",
    "capability/child-engine": "tested",
    "capability/cockpit": "tested",
    "capability/home-skill": "live",
    "capability/installer": "tested",
    "capability/learning": "tested",
    "capability/living-core": "live",
    "capability/personal-memory": "live",
    "capability/runtime-subagents": "tested",
    "capability/safe-actions": "tested",
    "dependency/dbos": "tested",
    "dependency/agent-browser": "tested",
    "dependency/hindsight": "live",
    "dependency/pydantic-ai": "tested",
    "dependency/pydantic-ai-harness": "tested",
    "dependency/microsoft-playwright-mcp": "tested",
}
_EXPECTED_MIGRATIONS = (
    ("4b_0000_core_namespace", None, "migrations/versions/4b_0000_core_namespace.py"),
    (
        "4b_0001_tenant_scope",
        "4b_0000_core_namespace",
        "migrations/versions/4b_0001_tenant_scope.py",
    ),
    (
        "4b_0002_episode_atomicity",
        "4b_0001_tenant_scope",
        "migrations/versions/4b_0002_episode_atomicity.py",
    ),
    (
        "4d_0003_core_continuity",
        "4b_0002_episode_atomicity",
        "migrations/versions/4d_0003_core_continuity.py",
    ),
    (
        "4d_0004_effect_authority",
        "4d_0003_core_continuity",
        "migrations/versions/4d_0004_effect_authority.py",
    ),
    (
        "4d_0005_evidence_objects",
        "4d_0004_effect_authority",
        "migrations/versions/4d_0005_evidence_objects.py",
    ),
    (
        "4d_0006_memory",
        "4d_0005_evidence_objects",
        "migrations/versions/4d_0006_memory.py",
    ),
    (
        "4d_0007_projection_telemetry",
        "4d_0006_memory",
        "migrations/versions/4d_0007_projection_telemetry.py",
    ),
    (
        "4e_0001_operational_timeline",
        "4d_0007_projection_telemetry",
        "migrations/versions/4e_0001_operational_timeline.py",
    ),
    (
        "4e_0002_delegated_work",
        "4e_0001_operational_timeline",
        "migrations/versions/4e_0002_delegated_work.py",
    ),
    (
        "4e_0003_delegated_work_runs",
        "4e_0002_delegated_work",
        "migrations/versions/4e_0003_delegated_work_runs.py",
    ),
    (
        "4e_0004_delegated_work_stop",
        "4e_0003_delegated_work_runs",
        "migrations/versions/4e_0004_delegated_work_stop.py",
    ),
    (
        "4e_0005_conversation_erasure",
        "4e_0004_delegated_work_stop",
        "migrations/versions/4e_0005_conversation_erasure.py",
    ),
    (
        "4e_0006_memory_outbox_erasure",
        "4e_0005_conversation_erasure",
        "migrations/versions/4e_0006_memory_outbox_erasure.py",
    ),
)


ArchiveFiles = Mapping[str, tuple[bytes, int]]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"managed B {label} is not canonical JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"managed B {label} must be an object")
    canonical = (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
    )
    if raw != canonical:
        raise RuntimeError(f"managed B {label} is not canonical JSON")
    return value


def _json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    """Parse an exact hash-pinned legacy JSON document without reformatting it."""

    try:
        value = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"managed B {label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"managed B {label} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise RuntimeError(f"managed B {label} fields differ")


def _path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise RuntimeError(f"managed B {label} path is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError(f"managed B {label} path is invalid")
    return path.as_posix()


def _digest(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RuntimeError(f"managed B {label} digest is invalid")
    return value


def _file(files: ArchiveFiles, path: str, *, label: str) -> bytes:
    entry = files.get(path)
    if entry is None:
        raise RuntimeError(f"managed B {label} is absent from the source archive")
    raw, mode = entry
    if mode & 0o022:
        raise RuntimeError(f"managed B {label} is group/world writable")
    return raw


def _pin_rows(
    files: ArchiveFiles,
    value: object,
    *,
    expected_paths_sha256: str,
    label: str,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"managed B {label} pins are invalid")
    rows: list[tuple[str, str]] = []
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise RuntimeError(f"managed B {label} pin is invalid")
        _exact_keys(row, {"path", "sha256"}, label=f"{label} pin")
        path = _path(row["path"], label=f"{label} pin {index}")
        digest = _digest(row["sha256"], label=f"{label} pin {index}")
        if _sha256(_file(files, path, label=path)) != digest:
            raise RuntimeError(f"managed B pinned input differs: {path}")
        rows.append((path, digest))
    if rows != sorted(rows) or len({path for path, _ in rows}) != len(rows):
        raise RuntimeError(f"managed B {label} pins must be unique and sorted")
    paths = tuple(path for path, _ in rows)
    paths_sha256 = _sha256(
        json.dumps(
            paths, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
    )
    if paths_sha256 != expected_paths_sha256:
        raise RuntimeError(f"managed B {label} path set differs")
    return tuple(rows)


def _projected_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
    )


def _validate_sanitized_cockpit_evidence(files: ArchiveFiles) -> tuple[int, str]:
    """Validate the closed public projection without opening private receipts."""

    raw = _file(
        files,
        _COCKPIT_PUBLIC_EVIDENCE_PATH,
        label="sanitized cockpit evidence",
    )
    value = _json(raw, label="sanitized cockpit evidence")
    _exact_keys(
        value,
        {
            "claims",
            "containsRawData",
            "contract",
            "coreCases",
            "dataClassification",
            "generatedOn",
            "nonClaims",
            "schemaVersion",
        },
        label="sanitized cockpit evidence",
    )
    if (
        value.get("contract") != "EvaCockpitSanitizedEvidence/v1"
        or value.get("schemaVersion") != 1
        or value.get("dataClassification") != "sanitized_projection"
        or value.get("containsRawData") is not False
        or value.get("generatedOn") != "2026-08-26"
        or value.get("nonClaims") != list(_SANITIZED_COCKPIT_NONCLAIMS)
    ):
        raise RuntimeError("managed B sanitized cockpit evidence header differs")

    claims = value.get("claims")
    if not isinstance(claims, list) or len(claims) != len(_SANITIZED_COCKPIT_CLAIMS):
        raise RuntimeError("managed B sanitized cockpit claims are invalid")
    actual_claims: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            raise RuntimeError("managed B sanitized cockpit claim is invalid")
        _exact_keys(
            claim,
            {"claimId", "facts", "sourceReceiptSha256", "state"},
            label="sanitized cockpit claim",
        )
        claim_id = claim.get("claimId")
        facts = claim.get("facts")
        digests = claim.get("sourceReceiptSha256")
        if (
            not isinstance(claim_id, str)
            or claim_id not in _SANITIZED_COCKPIT_CLAIMS
            or claim.get("state") != _SANITIZED_COCKPIT_CLAIMS[claim_id][0]
            or facts != list(_SANITIZED_COCKPIT_CLAIMS[claim_id][1])
            or not isinstance(digests, list)
            or not digests
            or any(
                not isinstance(digest, str) or not _SHA256.fullmatch(digest)
                for digest in digests
            )
            or digests != sorted(set(digests))
        ):
            raise RuntimeError("managed B sanitized cockpit claim is invalid")
        actual_claims.append(claim_id)
    if actual_claims != sorted(_SANITIZED_COCKPIT_CLAIMS):
        raise RuntimeError(
            "managed B sanitized cockpit claims must be unique and sorted"
        )

    cases = value.get("coreCases")
    if not isinstance(cases, list) or len(cases) != len(_SANITIZED_CORE_CASES):
        raise RuntimeError("managed B sanitized core cases are invalid")
    actual_cases: list[str] = []
    for case in cases:
        if not isinstance(case, dict):
            raise RuntimeError("managed B sanitized core case is invalid")
        _exact_keys(
            case,
            {"caseId", "criticalFailureCount", "sourceReceiptSha256", "status"},
            label="sanitized core case",
        )
        case_id = case.get("caseId")
        digests = case.get("sourceReceiptSha256")
        if (
            not isinstance(case_id, str)
            or case_id not in _SANITIZED_CORE_CASES
            or case.get("status") != "passed_live_b"
            or case.get("criticalFailureCount") != 0
            or not isinstance(digests, list)
            or not digests
            or any(
                not isinstance(digest, str) or not _SHA256.fullmatch(digest)
                for digest in digests
            )
            or digests != sorted(set(digests))
        ):
            raise RuntimeError("managed B sanitized core case is invalid")
        actual_cases.append(case_id)
    if tuple(actual_cases) != _SANITIZED_CORE_CASES:
        raise RuntimeError("managed B sanitized core cases must be unique and sorted")
    return len(actual_cases), _sha256(raw)


def _validate_cockpit_evidence(files: ArchiveFiles) -> dict[str, object]:
    """Bind every approved cockpit label to exact, release-pinned archive bytes."""

    raw = _file(files, _COCKPIT_EVIDENCE_PATH, label="cockpit evidence index")
    value = _json(raw, label="cockpit evidence index")
    _exact_keys(
        value,
        {"contract", "entries", "nonClaims", "schemaVersion", "status", "updatedOn"},
        label="cockpit evidence index",
    )
    entries = value.get("entries")
    if (
        value.get("contract") != "EvaCockpitEvidenceIndex/v1"
        or value.get("schemaVersion") != 1
        or value.get("status") != "release_bound_projection"
        or value.get("updatedOn") != "2026-08-29"
        or value.get("nonClaims") != list(_COCKPIT_EVIDENCE_NONCLAIMS)
        or not isinstance(entries, list)
        or len(entries) != len(_COCKPIT_EVIDENCE_STATES)
    ):
        raise RuntimeError("managed B cockpit evidence index is invalid")
    actual_claims: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("managed B cockpit evidence entry is invalid")
        _exact_keys(
            entry,
            {"claimId", "claimState", "claimSummary", "sources"},
            label="cockpit evidence entry",
        )
        claim_id = entry.get("claimId")
        summary = entry.get("claimSummary")
        rows = entry.get("sources")
        if (
            not isinstance(claim_id, str)
            or claim_id not in _COCKPIT_EVIDENCE_STATES
            or entry.get("claimState") != _COCKPIT_EVIDENCE_STATES[claim_id]
            or not isinstance(summary, str)
            or not 1 <= len(summary) <= 2_000
            or not isinstance(rows, list)
            or not rows
        ):
            raise RuntimeError("managed B cockpit evidence entry is invalid")
        actual_claims.append(claim_id)
        actual_sources: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("managed B cockpit evidence source is invalid")
            _exact_keys(row, {"path", "sha256"}, label="cockpit evidence source")
            path = _path(row.get("path"), label="cockpit evidence source")
            if _sha256(_file(files, path, label=path)) != _digest(
                row.get("sha256"), label="cockpit evidence source"
            ):
                raise RuntimeError("managed B cockpit evidence source differs")
            actual_sources.append(path)
        if len(actual_sources) != len(set(actual_sources)):
            raise RuntimeError("managed B cockpit evidence source set is not unique")
    if actual_claims != sorted(_COCKPIT_EVIDENCE_STATES):
        raise RuntimeError(
            "managed B cockpit evidence claims must be unique and sorted"
        )

    core_live_cases, sanitized_sha256 = _validate_sanitized_cockpit_evidence(files)
    return {
        "cockpit_evidence_claim_count": len(actual_claims),
        "cockpit_evidence_sha256": _sha256(raw),
        "cockpit_sanitized_evidence_sha256": sanitized_sha256,
        "core_live_case_count": core_live_cases,
    }


def _validate_registry(
    files: ArchiveFiles, authority: Mapping[str, Any]
) -> dict[str, object]:
    _exact_keys(
        authority,
        {
            "currentContractCount",
            "currentCorpusSha256",
            "currentManifestSha256",
            "currentProfileSha256",
            "historical4d",
        },
        label="contract registry authority",
    )
    manifest_raw = _file(files, _MANIFEST_PATH, label="contract manifest")
    corpus_raw = _file(files, _CORPUS_PATH, label="contract corpus")
    profile_raw = _file(files, _PROFILE_PATH, label="canonical profile")
    expected_manifest = _digest(
        authority["currentManifestSha256"], label="current manifest"
    )
    expected_corpus = _digest(authority["currentCorpusSha256"], label="current corpus")
    expected_profile = _digest(
        authority["currentProfileSha256"], label="current profile"
    )
    if (
        _sha256(manifest_raw) != expected_manifest
        or _sha256(corpus_raw) != expected_corpus
        or _sha256(profile_raw) != expected_profile
    ):
        raise RuntimeError("managed B current contract authority differs")
    manifest = _json(manifest_raw, label="contract manifest")
    corpus = _json(corpus_raw, label="contract corpus")
    count = authority["currentContractCount"]
    schemas = manifest.get("schemas")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        or manifest.get("manifest") != "EvaContractSchemaManifest/v1"
        or manifest.get("contractCount") != count
        or manifest.get("corpusSha256") != expected_corpus
        or manifest.get("profileSha256") != expected_profile
        or not isinstance(schemas, dict)
        or len(schemas) != count
    ):
        raise RuntimeError("managed B current contract manifest differs")
    for contract, schema_digest in schemas.items():
        if not isinstance(contract, str):
            raise RuntimeError("managed B contract manifest name is invalid")
        digest = _digest(schema_digest, label=f"schema {contract}")
        path = f"schemas/contracts/{contract.replace('/', '-')}.schema.json"
        schema_raw = _file(files, path, label=path)
        schema = _json(schema_raw, label=path)
        if (
            _sha256(schema_raw) != digest
            or schema.get("properties", {}).get("contract", {}).get("const") != contract
        ):
            raise RuntimeError(f"managed B contract schema differs: {contract}")
    expected_schema_paths = {
        f"schemas/contracts/{contract.replace('/', '-')}.schema.json"
        for contract in schemas
    }
    actual_schema_paths = {
        path
        for path in files
        if path.startswith("schemas/contracts/") and path.endswith(".schema.json")
    }
    if actual_schema_paths != expected_schema_paths:
        raise RuntimeError("managed B contract schema file set differs")

    historical = authority["historical4d"]
    if not isinstance(historical, dict):
        raise RuntimeError("managed B historical 4D authority is invalid")
    _exact_keys(
        historical,
        {
            "allowedCurrentAdditions",
            "contractCount",
            "corpusSha256",
            "freezeArtifactSha256",
            "manifestSha256",
        },
        label="historical 4D authority",
    )
    additions = historical["allowedCurrentAdditions"]
    if not isinstance(additions, dict) or not additions:
        raise RuntimeError("managed B historical 4D additions are invalid")
    if any(not isinstance(case_id, str) for case_id in additions.values()):
        raise RuntimeError("managed B historical 4D additions are invalid")
    historical_count = historical["contractCount"]
    if (
        isinstance(historical_count, bool)
        or not isinstance(historical_count, int)
        or historical_count + len(additions) != count
        or set(additions) - set(schemas)
    ):
        raise RuntimeError("managed B historical 4D contract count differs")
    historical_manifest_sha = _digest(
        historical["manifestSha256"], label="historical 4D manifest"
    )
    historical_corpus_sha = _digest(
        historical["corpusSha256"], label="historical 4D corpus"
    )
    projected_manifest = copy.deepcopy(manifest)
    projected_schemas = projected_manifest["schemas"]
    for contract in additions:
        projected_schemas.pop(contract)
        projected_manifest.get("hostConformanceRules", {}).pop(contract, None)
    projected_manifest["contractCount"] = historical_count
    projected_manifest["corpusSha256"] = historical_corpus_sha
    if _sha256(_projected_json(projected_manifest)) != historical_manifest_sha:
        raise RuntimeError("managed B historical 4D manifest projection differs")

    cases = corpus.get("cases")
    if not isinstance(cases, list):
        raise RuntimeError("managed B contract corpus cases are invalid")
    case_by_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("caseId"), str):
            raise RuntimeError("managed B contract corpus case is invalid")
        case_id = case["caseId"]
        if case_id in case_by_id:
            raise RuntimeError("managed B contract corpus repeats a case")
        case_by_id[case_id] = case
    removed_cases: set[str] = set()
    for contract, case_id in additions.items():
        case = case_by_id.get(case_id)
        if case is None or case.get("contract") != contract:
            raise RuntimeError("managed B historical 4D corpus projection differs")
    for case_id, case in case_by_id.items():
        if case.get("encoding") != "utf8" or not isinstance(case.get("wireUtf8"), str):
            continue
        try:
            payload = json.loads(case["wireUtf8"])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("contract") in additions:
            removed_cases.add(case_id)
    projected_corpus = copy.deepcopy(corpus)
    projected_corpus["cases"] = [
        case
        for case in projected_corpus["cases"]
        if case["caseId"] not in removed_cases
    ]
    if _sha256(_projected_json(projected_corpus)) != historical_corpus_sha:
        raise RuntimeError("managed B historical 4D corpus projection differs")

    freeze_raw = _file(files, _FOUR_D_FREEZE_PATH, label="4D freeze artifact")
    if _sha256(freeze_raw) != _digest(
        historical["freezeArtifactSha256"], label="4D freeze artifact"
    ):
        raise RuntimeError("managed B historical 4D freeze artifact differs")
    freeze = _json_object(freeze_raw, label="4D freeze artifact")
    binding = freeze.get("authorityBindings", {}).get("contracts", {})
    if (
        binding.get("manifestPath") != f"eva2/{_MANIFEST_PATH}"
        or binding.get("manifestSha256") != historical_manifest_sha
        or binding.get("corpusSha256") != historical_corpus_sha
        or binding.get("contractCount") != historical_count
    ):
        raise RuntimeError("managed B historical 4D freeze binding differs")
    return {
        "current_contract_count": count,
        "current_corpus_sha256": expected_corpus,
        "current_manifest_sha256": expected_manifest,
        "historical_4d_contract_count": historical_count,
        "historical_4d_corpus_sha256": historical_corpus_sha,
        "historical_4d_manifest_sha256": historical_manifest_sha,
    }


def _validate_portable_v1_contract_bundle(
    files: ArchiveFiles, authority: Mapping[str, Any]
) -> dict[str, object]:
    """Bind the immutable 64-contract reader/exporter authority for archive v1."""

    historical = authority.get("historical4d")
    if not isinstance(historical, dict):
        raise RuntimeError("managed B portable v1 authority is invalid")
    expected_manifest_sha = _digest(
        historical.get("manifestSha256"), label="portable v1 manifest"
    )
    expected_corpus_sha = _digest(
        historical.get("corpusSha256"), label="portable v1 corpus"
    )
    expected_profile_sha = _digest(
        authority.get("currentProfileSha256"), label="portable v1 profile"
    )
    expected_count = historical.get("contractCount")
    if (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count <= 0
    ):
        raise RuntimeError("managed B portable v1 contract count is invalid")

    manifest_raw = _file(
        files, _PORTABLE_V1_MANIFEST_PATH, label="portable v1 manifest"
    )
    profile_raw = _file(files, _PORTABLE_V1_PROFILE_PATH, label="portable v1 profile")
    if (
        _sha256(manifest_raw) != expected_manifest_sha
        or _sha256(profile_raw) != expected_profile_sha
    ):
        raise RuntimeError("managed B portable v1 contract authority differs")
    manifest = _json(manifest_raw, label="portable v1 contract manifest")
    schemas = manifest.get("schemas")
    if (
        manifest.get("manifest") != "EvaContractSchemaManifest/v1"
        or manifest.get("contractCount") != expected_count
        or manifest.get("corpusSha256") != expected_corpus_sha
        or manifest.get("profileSha256") != expected_profile_sha
        or not isinstance(schemas, dict)
        or len(schemas) != expected_count
    ):
        raise RuntimeError("managed B portable v1 contract manifest differs")

    expected_paths = {
        _PORTABLE_V1_MANIFEST_PATH,
        _PORTABLE_V1_PROFILE_PATH,
    }
    for contract, schema_digest in schemas.items():
        if not isinstance(contract, str):
            raise RuntimeError("managed B portable v1 contract name is invalid")
        digest = _digest(schema_digest, label=f"portable v1 schema {contract}")
        name = f"{contract.replace('/', '-')}.schema.json"
        path = f"{_PORTABLE_V1_CONTRACT_ROOT}/{name}"
        current_path = f"schemas/contracts/{name}"
        schema_raw = _file(files, path, label=path)
        schema = _json(schema_raw, label=path)
        if (
            _sha256(schema_raw) != digest
            or schema_raw != _file(files, current_path, label=current_path)
            or schema.get("properties", {}).get("contract", {}).get("const") != contract
        ):
            raise RuntimeError(
                f"managed B portable v1 contract schema differs: {contract}"
            )
        expected_paths.add(path)
    actual_paths = {
        path for path in files if path.startswith(f"{_PORTABLE_V1_CONTRACT_ROOT}/")
    }
    if actual_paths != expected_paths:
        raise RuntimeError("managed B portable v1 contract file set differs")
    return {
        "portable_v1_contract_count": expected_count,
        "portable_v1_manifest_sha256": expected_manifest_sha,
    }


def _assignment(module: ast.Module, name: str) -> object:
    found: list[object] = []
    for node in module.body:
        value: ast.expr | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            value = node.value
        if value is not None:
            try:
                found.append(ast.literal_eval(value))
            except (ValueError, TypeError) as error:
                raise RuntimeError(
                    "managed B migration metadata is not literal"
                ) from error
    if len(found) != 1:
        raise RuntimeError(f"managed B migration {name} is not unique")
    return found[0]


def _validate_migrations(files: ArchiveFiles, value: object) -> str:
    if not isinstance(value, dict):
        raise RuntimeError("managed B migration authority is invalid")
    _exact_keys(value, {"head", "revisions"}, label="migration authority")
    head = value["head"]
    revisions = value["revisions"]
    if (
        head != _EXPECTED_MIGRATIONS[-1][0]
        or not isinstance(revisions, list)
        or len(revisions) != len(_EXPECTED_MIGRATIONS)
    ):
        raise RuntimeError("managed B migration authority is invalid")
    actual_migration_paths = {
        path
        for path in files
        if path.startswith("migrations/versions/")
        and path.endswith(".py")
        and not path.endswith("/__init__.py")
    }
    if actual_migration_paths != {row[2] for row in _EXPECTED_MIGRATIONS}:
        raise RuntimeError("managed B migration file set differs")
    previous: str | None = None
    seen: set[str] = set()
    for index, row in enumerate(revisions):
        if not isinstance(row, dict):
            raise RuntimeError("managed B migration row is invalid")
        _exact_keys(
            row, {"downRevision", "path", "revision", "sha256"}, label="migration row"
        )
        path = _path(row["path"], label=f"migration {index}")
        revision = row["revision"]
        down_revision = row["downRevision"]
        expected_revision, expected_down, expected_path = _EXPECTED_MIGRATIONS[index]
        if (
            revision != expected_revision
            or down_revision != expected_down
            or path != expected_path
            or revision in seen
            or down_revision != previous
            or _sha256(_file(files, path, label=path))
            != _digest(row["sha256"], label=f"migration {revision}")
        ):
            raise RuntimeError("managed B migration chain differs")
        try:
            module = ast.parse(_file(files, path, label=path), filename=path)
        except SyntaxError as error:
            raise RuntimeError("managed B migration source is invalid") from error
        if (
            _assignment(module, "revision") != revision
            or _assignment(module, "down_revision") != down_revision
        ):
            raise RuntimeError("managed B migration metadata differs")
        seen.add(revision)
        previous = revision
    if previous != head:
        raise RuntimeError("managed B migration head differs")
    return head


def _validate_static_archive_inputs(
    files: ArchiveFiles, authority_raw: bytes
) -> dict[str, object]:
    """Validate static inputs without making a source-archive claim."""

    archived_authority = _file(files, AUTHORITY_PATH, label="release authority")
    if archived_authority != authority_raw or len(authority_raw) > _MAX_AUTHORITY_BYTES:
        raise RuntimeError("managed B release authority differs from source archive")
    authority = _json(authority_raw, label="release authority")
    _exact_keys(
        authority,
        {
            "contract",
            "contractRegistry",
            "migrationAuthority",
            "nonClaims",
            "requiredInputs",
            "requiredTests",
            "schemaVersion",
            "status",
        },
        label="release authority",
    )
    if (
        authority.get("contract") != "EvaManagedBReleaseInputAuthority/v1"
        or authority.get("schemaVersion") != 1
        or authority.get("status") != "version_matched_runtime_dependency_static_inputs"
        or authority.get("nonClaims") != list(_NONCLAIMS)
    ):
        raise RuntimeError("managed B release authority header differs")
    inputs = _pin_rows(
        files,
        authority["requiredInputs"],
        expected_paths_sha256=_EXPECTED_INPUT_PATHS_SHA256,
        label="release input",
    )
    tests = _pin_rows(
        files,
        authority["requiredTests"],
        expected_paths_sha256=_EXPECTED_TEST_PATHS_SHA256,
        label="release test",
    )
    alignment = inspect_runtime_dependency_alignment(
        _file(
            files,
            "release/managed-b-wheelhouse-amd64-v2.json",
            label="active wheelhouse manifest",
        ),
        _file(
            files,
            "release/managed-b-wheel-coverage-4d-amd64-v1.json",
            label="aligned wheel coverage",
        ),
        _file(files, "requirements-4d.lock", label="4D requirements lock"),
        _file(
            files,
            "release/managed-b-wheelhouse-amd64-v1.json",
            label="historical wheelhouse manifest",
        ),
        _file(
            files,
            "release/python-wheel-coverage.json",
            label="historical wheel coverage",
        ),
        _file(files, "requirements.lock", label="historical requirements lock"),
        _file(
            files,
            "release/4d-dependency-source-binding.json",
            label="4D dependency binding",
        ),
    )
    registry = authority["contractRegistry"]
    if not isinstance(registry, dict):
        raise RuntimeError("managed B contract registry authority is invalid")
    registry_receipt = _validate_registry(files, registry)
    portable_v1_receipt = _validate_portable_v1_contract_bundle(files, registry)
    migration_head = _validate_migrations(files, authority["migrationAuthority"])
    cockpit_evidence = _validate_cockpit_evidence(files)
    return {
        "authority_sha256": _sha256(authority_raw),
        "contract": "EvaManagedBReleaseInputReceipt/v1",
        "dependency_alignment": alignment,
        **registry_receipt,
        **portable_v1_receipt,
        **cockpit_evidence,
        "migration_head": migration_head,
        "network_used": False,
        "nonclaims": list(_NONCLAIMS),
        "required_input_count": len(inputs),
        "required_test_count": len(tests),
        "status": "version_matched_dependency_inputs_bound",
    }


def prepare_managed_b_release_inputs(commit: str) -> dict[str, object]:
    """Resolve one real Git commit and validate only its archived inputs."""

    candidate = prepare_candidate_build_inputs(commit)
    files = validate_candidate_build_inputs(candidate)
    authority_raw = _file(files, AUTHORITY_PATH, label="release authority")
    receipt = _validate_static_archive_inputs(files, authority_raw)
    return {
        **receipt,
        "source_archive_sha256": candidate.source_archive_sha256,
        "source_commit": candidate.source_commit,
        "status": "version_matched_runtime_dependency_inputs_bound_not_built_or_deployed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit")
    arguments = parser.parse_args(argv)
    try:
        receipt = prepare_managed_b_release_inputs(arguments.commit)
    except RuntimeError as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


__all__ = [
    "AUTHORITY_PATH",
    "prepare_managed_b_release_inputs",
]


if __name__ == "__main__":
    raise SystemExit(main())
