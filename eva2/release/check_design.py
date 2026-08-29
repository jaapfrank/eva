#!/usr/bin/env python3
"""Deterministic, read-only tranche 4A platform and supply design gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any


HEX64 = re.compile(r"^[0-9a-f]{64}$")
OCI_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
PIN = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([^ ;\\]+)")

PROCESS_IDS = {
    "living_core",
    "model_adapter",
    "postgresql",
    "connector_supervisor",
    "credential_broker",
    "connector_worker",
    "egress_proxy",
    "object_store",
    "telemetry_collector",
    "eva_cli",
}

OCI_COMPONENT_IDS = frozenset(
    {
        "living_core",
        "model_adapter",
        "connector_supervisor",
        "credential_broker",
        "connector_worker_base",
        "postgresql",
        "egress_proxy",
        "object_store",
        "telemetry_collector",
    }
)

DIRECT_PINS = {
    "alembic": "1.19.1",
    "cryptography": "46.0.5",
    "fastapi": "0.141.1",
    "psycopg": "3.3.4",
    "pydantic": "2.12.5",
    "pydantic-ai-slim": "2.32.1",
    "pynacl": "1.6.2",
    "setuptools": "84.0.0",
    "sqlalchemy": "2.0.52",
    "uvicorn": "0.41.0",
    "websockets": "17.0.1",
}

TOOLCHAIN_DIRECT_PINS = {
    "build": "1.5.0",
    "click": "8.4.2",
    "iniconfig": "2.3.0",
    "jsonschema": "4.25.1",
    "packaging": "26.3",
    "pip": "26.0.1",
    "pip-tools": "7.6.1",
    "pluggy": "1.6.0",
    "pygments": "2.17.2",
    "pyproject-hooks": "1.2.0",
    "pytest": "8.4.2",
    "setuptools": "84.0.0",
    "wheel": "0.48.0",
}

TOOLCHAIN_PINS = TOOLCHAIN_DIRECT_PINS | {
    "attrs": "26.1.0",
    "jsonschema-specifications": "2025.9.1",
    "referencing": "0.37.0",
    "rpds-py": "2026.6.3",
    "typing-extensions": "4.16.0",
}

LOCKED_PINS = {
    "alembic": "1.19.1",
    "annotated-doc": "0.0.5",
    "annotated-types": "0.8.0",
    "anyio": "4.14.2",
    "cffi": "2.1.1",
    "click": "8.4.2",
    "cryptography": "46.0.5",
    "fastapi": "0.141.1",
    "genai-prices": "0.1.4",
    "greenlet": "3.5.5",
    "griffelib": "2.2.0",
    "h11": "0.16.0",
    "httpcore2": "2.12.0",
    "httpx2": "2.12.0",
    "idna": "3.19",
    "logfire-api": "4.41.0",
    "mako": "1.4.1",
    "markupsafe": "3.0.3",
    "opentelemetry-api": "1.44.0",
    "psycopg": "3.3.4",
    "psycopg-binary": "3.3.4",
    "pycparser": "3.0",
    "pydantic": "2.12.5",
    "pydantic-ai-slim": "2.32.1",
    "pydantic-core": "2.41.5",
    "pydantic-graph": "2.32.1",
    "pynacl": "1.6.2",
    "setuptools": "84.0.0",
    "sqlalchemy": "2.0.52",
    "starlette": "1.6.0",
    "truststore": "0.10.4",
    "typing-extensions": "4.16.0",
    "typing-inspection": "0.4.4",
    "uvicorn": "0.41.0",
    "websockets": "17.0.1",
}

SOURCE_BINDINGS = {
    "architecture_decision": (
        "docs/adr-0001-eva2-living-core.md",
        "73b53e392b8e3b65d1241bff46bb92144960a2620dbd45f3c9d3526d9f07910d",
    ),
    "transition_disposition": (
        "docs/eva1-to-eva2-transition-manifest-2026-08-20.md",
        "7ea53e81e6fb3af0304cf88119abc9bf864e1a9dbf1445bfaed6dbd18d3f8d85",
    ),
    "transition_evidence": (
        "transition/evidence/eva1-to-eva2-transition-manifest-c6fff926d7f6b2f199081a91dac6cfcaf4e5c2c750159d6bf2dc068bae670c1c.json",
        "c6fff926d7f6b2f199081a91dac6cfcaf4e5c2c750159d6bf2dc068bae670c1c",
    ),
    "step3_master_build_map": (
        "docs/eva2-build-map-2026-08-20.md",
        "188731302df263427ad499102e0d2be9ca11bf5321d81959d492d4604243fd3b",
    ),
    "step3_core_contracts_map": (
        "docs/eva2-build-map-core-contracts.md",
        "877a0d25b637ef0f3bbfe3286ab40b5c40d7a0e50dbc3bf4af14504426bf0d63",
    ),
    "step3_platform_map": (
        "docs/eva2-build-map-platform.md",
        "244b2f2e397d4ce11e245d573b266dee443f77b85d0bd456d7e4b9cd66fa98f1",
    ),
    "step3_presence_evidence_map": (
        "docs/eva2-build-map-presence-evidence.md",
        "69d391ba4e3b3363c4d1d3cb6471c1510b68e974f5d8e61088356b6020328ed8",
    ),
    "first_vertical_contract": (
        "design/evidence/eva2-first-vertical-contract-2c9ffb7a2ae6fd9b34cc4c29f73e6b3b2212f6a96232090ddbdbcfb02ad5b9a1.json",
        "2c9ffb7a2ae6fd9b34cc4c29f73e6b3b2212f6a96232090ddbdbcfb02ad5b9a1",
    ),
    "public_client_ack_fixture": (
        "design/fixtures/noor-client-ack-key-v1.json",
        "330476faf5f2317d0edfaa991cc7dd344711353188d6c504f9629a780d5f4e93",
    ),
    "step3_4a_authorization_freeze": (
        "design/evidence/eva2-step3-design-freeze-cac255b606224a65c9c14906dca4f2b1257a688edbc45c631688529bf9adcb33.json",
        "cac255b606224a65c9c14906dca4f2b1257a688edbc45c631688529bf9adcb33",
    ),
}

BASE_DIGESTS = {
    "python_base": {
        "index": "sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134",
        "amd64": "sha256:356b0d18f9385f4bdcc673af60e1e64c9d1504952e4ec36ee32044c722a6bc4e",
        "arm64": "sha256:fa161ca9d626b475d504c439b943e295fbca9e2560b1be14654ade60e7d8d45a",
    },
    "postgresql_base": {
        "index": "sha256:472efd9a66f2b2f1a5aeb18b28de74332e6ef88c2b93a1a5d812fb6db67a5f60",
        "amd64": "sha256:57c55aefb81a937d21171b7c2266264b5552cb1a4f90459bbdfc417a36883a4d",
        "arm64": "sha256:494df59df434a3a83b698c01d0ae28d486d68e9675877d3341287f054a653be2",
    },
    "debian_slim_base": {
        "index": "sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241",
        "amd64": "sha256:362e64223cc0da95422b3b13c045186fc0a81250e765d31c025fbddf257f6143",
        "arm64": "sha256:817e6cf99d6fc127ff4ffe8580049b60deba0adfbbb2bd65ddc3ef8fbb7aade0",
    },
}


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("JSON root is not an object")
    return value


def issue(errors: list[dict[str, str]], code: str, path: Path, detail: str) -> None:
    errors.append({"code": code, "path": path.as_posix(), "detail": detail})


def safe_load(
    root: Path, relative: str, errors: list[dict[str, str]], checked: set[str]
) -> dict[str, Any]:
    path = root / relative
    checked.add(relative)
    try:
        return load_json(path)
    except (OSError, ValueError, UnicodeError) as exc:
        issue(errors, "invalid_json", Path(relative), str(exc))
        return {}


def parse_lock(text: str) -> tuple[dict[str, str], dict[str, set[str]], list[str]]:
    pins: dict[str, str] = {}
    hashes: dict[str, set[str]] = {}
    problems: list[str] = []
    starts = list(re.finditer(r"(?m)^([A-Za-z0-9_.-]+)==([^ ;\\\n]+)", text))
    for index, match in enumerate(starts):
        name = normalize_name(match.group(1))
        version = match.group(2)
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        block = text[match.start() : end]
        block_hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", block))
        if name in pins:
            problems.append(f"duplicate pin {name}")
        pins[name] = version
        hashes[name] = block_hashes
        if not block_hashes:
            problems.append(f"missing sha256 hash for {name}")
    if not starts:
        problems.append("lock has no exact pins")
    if re.search(
        r"(?mi)^\s*(?:-e\s|git\+|https?://|file:|--index-url|--extra-index-url)", text
    ):
        problems.append("lock contains a mutable or location dependency")
    if re.search(r"(?:/tmp/|/home/|[A-Za-z]:\\\\)", text):
        problems.append("lock contains a host-local absolute path")
    return pins, hashes, problems


def parse_root_pins(text: str) -> tuple[dict[str, str], list[str]]:
    pins: dict[str, str] = {}
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN.fullmatch(line)
        if not match:
            problems.append(f"line {number} is not an exact pin")
            continue
        name, version = normalize_name(match.group(1)), match.group(2)
        if name in pins:
            problems.append(f"duplicate root pin {name}")
        pins[name] = version
    return pins, problems


def _claims_are_false(data: dict[str, Any]) -> bool:
    claims = data.get("claims")
    return (
        isinstance(claims, dict)
        and bool(claims)
        and all(value is False for value in claims.values())
    )


def platform_gate(root: Path) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    checked: set[str] = set()
    eva = root / "eva2"

    platform = safe_load(root, "eva2/release/platform-lock.json", errors, checked)
    if (
        platform.get("contract") != "PlatformLock/v1"
        or platform.get("schema_version") != 1
    ):
        issue(
            errors,
            "platform_contract",
            Path("eva2/release/platform-lock.json"),
            "must be PlatformLock/v1 schema_version 1",
        )
    expected_versions = {
        "python_version": "3.12.14",
        "postgres_version": "16.13",
        "dbos_version": "2.30.0",
        "pydantic_version": "2.12.5",
        "pydantic_ai_slim_version": "2.32.1",
    }
    for field, expected in expected_versions.items():
        if platform.get(field) != expected:
            issue(
                errors,
                "platform_version",
                Path("eva2/release/platform-lock.json"),
                f"{field} must equal {expected}",
            )
    if (
        platform.get("claim_state") != "selected_for_validation"
        or platform.get("mutable_image_tags_allowed") is not False
    ):
        issue(
            errors,
            "platform_claim_state",
            Path("eva2/release/platform-lock.json"),
            "4A must be selected_for_validation with mutable tags forbidden",
        )
    expected_tier1 = {
        ("Ubuntu Server 24.04.4 LTS", "amd64", "containerized", "Docker Engine 29.7.2"),
        ("Ubuntu Server 24.04.4 LTS", "arm64", "containerized", "Docker Engine 29.7.2"),
    }
    tier1 = platform.get("tier1_targets")
    actual_tier1: set[tuple[Any, ...]] = set()
    if isinstance(tier1, list):
        for target in tier1:
            if isinstance(target, dict):
                actual_tier1.add(
                    tuple(
                        target.get(key)
                        for key in (
                            "operating_system",
                            "architecture",
                            "execution_mode",
                            "container_engine",
                        )
                    )
                )
                if (
                    target.get("target_class"),
                    target.get("validation_state"),
                    target.get("support_status"),
                    target.get("validation_receipt_sha256"),
                ) != ("tier1_release_gate", "unvalidated", "unsupported", None):
                    issue(
                        errors,
                        "tier1_claim_inflation",
                        Path("eva2/release/platform-lock.json"),
                        "Tier 1 design target has support or receipt claim",
                    )
    if actual_tier1 != expected_tier1:
        issue(
            errors,
            "tier1_matrix",
            Path("eva2/release/platform-lock.json"),
            "exact Ubuntu 24.04.4 amd64/arm64 and Docker 29.7.2 targets required",
        )
    expected_compatibility = {
        ("macOS 15", "arm64", "containerized"),
        ("Windows 11 24H2", "x86_64", "containerized_not_native"),
        ("Ubuntu Server 24.04.4 LTS", "amd64_or_arm64", "rootless_podman"),
    }
    compatibility = platform.get("compatibility_targets")
    actual_compatibility: set[tuple[Any, ...]] = set()
    if isinstance(compatibility, list):
        for target in compatibility:
            if isinstance(target, dict):
                actual_compatibility.add(
                    tuple(
                        target.get(key)
                        for key in (
                            "operating_system",
                            "architecture",
                            "execution_mode",
                        )
                    )
                )
                if (
                    target.get("target_class"),
                    target.get("validation_state"),
                    target.get("support_status"),
                    target.get("validation_receipt_sha256"),
                ) != ("compatibility_target", "unvalidated", "unsupported", None):
                    issue(
                        errors,
                        "compatibility_claim_inflation",
                        Path("eva2/release/platform-lock.json"),
                        "compatibility target has validation or support claim",
                    )
    if actual_compatibility != expected_compatibility:
        issue(
            errors,
            "compatibility_matrix",
            Path("eva2/release/platform-lock.json"),
            "exact sealed no-support compatibility targets required",
        )
    images = platform.get("oci_images")
    expected_image_slots = {
        (component, architecture)
        for component in OCI_COMPONENT_IDS
        for architecture in ("amd64", "arm64")
    }
    actual_image_slots: list[tuple[str, str]] = []
    image_rows_valid = isinstance(images, list)
    if not isinstance(images, list):
        issue(
            errors,
            "platform_oci_slots",
            Path("eva2/release/platform-lock.json"),
            "oci_images must be a list",
        )
    else:
        for image in images:
            if not isinstance(image, dict):
                issue(
                    errors,
                    "platform_oci_slot",
                    Path("eva2/release/platform-lock.json"),
                    "OCI slot must be an object",
                )
                image_rows_valid = False
                continue
            component = image.get("component")
            architecture = image.get("architecture")
            if isinstance(component, str) and isinstance(architecture, str):
                actual_image_slots.append((component, architecture))
            else:
                image_rows_valid = False
            if (
                image.get("validation_state") != "selected_for_validation"
                or image.get("runnable") is not False
                or any(
                    image.get(field) is not None
                    for field in ("image_repository", "image_digest", "sbom_sha256")
                )
            ):
                issue(
                    errors,
                    "platform_oci_claim_inflation",
                    Path("eva2/release/platform-lock.json"),
                    "selected OCI slots must remain null and non-runnable",
                )
    if (
        not image_rows_valid
        or len(actual_image_slots) != len(expected_image_slots)
        or set(actual_image_slots) != expected_image_slots
    ):
        issue(
            errors,
            "platform_oci_matrix",
            Path("eva2/release/platform-lock.json"),
            "every selected component requires amd64 and arm64 placeholders",
        )

    checked.add("eva2/pyproject.toml")
    try:
        pyproject = tomllib.loads((eva / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        issue(errors, "pyproject", Path("eva2/pyproject.toml"), str(exc))
        pyproject = {}
    project = pyproject.get("project", {}) if isinstance(pyproject, dict) else {}
    if (
        project.get("requires-python") != "==3.12.14"
        or pyproject.get("tool", {}).get("eva2", {}).get("target-python") != "3.12.14"
    ):
        issue(
            errors,
            "python_target",
            Path("eva2/pyproject.toml"),
            "package and design target must pin CPython 3.12.14",
        )

    design = safe_load(
        root, "eva2/design/platform/platform-design-v1.json", errors, checked
    )
    processes = design.get("processes")
    process_ids = (
        {item.get("id") for item in processes if isinstance(item, dict)}
        if isinstance(processes, list)
        else set()
    )
    authorities = (
        [
            item.get("id")
            for item in processes
            if isinstance(item, dict) and item.get("decisionAuthority") is True
        ]
        if isinstance(processes, list)
        else []
    )
    if (
        design.get("contract") != "Eva2PlatformDesign/v1"
        or design.get("claimScope") != "design_only"
        or process_ids != PROCESS_IDS
    ):
        issue(
            errors,
            "platform_design",
            Path("eva2/design/platform/platform-design-v1.json"),
            "closed process map and design_only claim required",
        )
    if (
        authorities != ["living_core"]
        or design.get("trustedDecisionCore") != "living_core"
    ):
        issue(
            errors,
            "second_trusted_core",
            Path("eva2/design/platform/platform-design-v1.json"),
            "living_core must be the only decision authority",
        )
    embedded = design.get("embeddedLibraries")
    if not isinstance(embedded, list) or not any(
        item.get("id") == "dbos"
        and item.get("version") == "2.30.0"
        and item.get("canonicalEvaDataAllowed") is False
        for item in embedded
        if isinstance(item, dict)
    ):
        issue(
            errors,
            "dbos_boundary",
            Path("eva2/design/platform/platform-design-v1.json"),
            "DBOS must be embedded 2.30.0 without canonical data authority",
        )
    if not _claims_are_false(design):
        issue(
            errors,
            "platform_runtime_claim",
            Path("eva2/design/platform/platform-design-v1.json"),
            "all runtime/process/support claims must remain false",
        )

    boundary = safe_load(
        root, "eva2/design/architecture/package-boundaries-v1.json", errors, checked
    )
    packages = boundary.get("packages")
    required_packages = {
        "contracts",
        "domain",
        "application",
        "adapters.postgres",
        "adapters.dbos",
        "adapters.model",
        "adapters.connectors",
        "adapters.credentials",
        "adapters.objects",
        "adapters.api",
        "bootstrap",
        "operations",
    }
    if (
        boundary.get("contract") != "Eva2PackageBoundaries/v1"
        or boundary.get("trustedCore") != "src/eva"
        or not isinstance(packages, dict)
        or not required_packages.issubset(packages)
    ):
        issue(
            errors,
            "package_boundary_map",
            Path("eva2/design/architecture/package-boundaries-v1.json"),
            "closed src/eva package/import policy is missing",
        )
    elif any(
        prefix.startswith("eva.adapters")
        for prefix in packages["domain"].get("allowedLocalImports", [])
    ):
        issue(
            errors,
            "domain_import_arrow",
            Path("eva2/design/architecture/package-boundaries-v1.json"),
            "domain may not import adapters",
        )

    budgets = safe_load(
        root, "eva2/design/platform/process-budgets-v1.json", errors, checked
    )
    process_budgets = budgets.get("processBudgets")
    if (
        budgets.get("contract") != "Eva2ProcessBudgets/v1"
        or not isinstance(process_budgets, dict)
        or set(process_budgets) != PROCESS_IDS
    ):
        issue(
            errors,
            "process_budget_map",
            Path("eva2/design/platform/process-budgets-v1.json"),
            "every process requires exactly one numeric budget",
        )
    else:
        required_numeric = {
            "maximumMemoryMiB",
            "maximumCpuCores",
            "maximumPids",
            "maximumTemporaryStorageMiB",
            "maximumShutdownGraceMs",
        }
        for process_id, budget in process_budgets.items():
            if (
                not isinstance(budget, dict)
                or set(budget) != required_numeric
                or any(
                    not isinstance(budget.get(field), int) or budget[field] <= 0
                    for field in required_numeric
                )
            ):
                issue(
                    errors,
                    "unbounded_process",
                    Path("eva2/design/platform/process-budgets-v1.json"),
                    f"{process_id} lacks a positive numeric ceiling",
                )
    worker = (
        process_budgets.get("connector_worker", {})
        if isinstance(process_budgets, dict)
        else {}
    )
    if worker != {
        "maximumMemoryMiB": 256,
        "maximumCpuCores": 1,
        "maximumPids": 32,
        "maximumTemporaryStorageMiB": 64,
        "maximumShutdownGraceMs": 5000,
    }:
        issue(
            errors,
            "worker_process_budget",
            Path("eva2/design/platform/process-budgets-v1.json"),
            "worker process ceilings differ from selected design",
        )
    invocation_expected = {
        "maximumWallClockMs": 35000,
        "maximumProtocolFrameBytes": 1048576,
        "maximumProtocolFramesPerInvocation": 2,
        "maximumStdoutBytes": 2101248,
        "maximumStderrBytes": 65536,
        "maximumDocumentBytes": 65536,
        "initializeCountsAsExternalCall": False,
        "initializeMayPerformExternalIo": False,
        "initializeCountsAgainstResourceAndFrameBudgets": True,
    }
    if budgets.get("connectorWorkerInvocation") != invocation_expected:
        issue(
            errors,
            "worker_invocation_budget",
            Path("eva2/design/platform/process-budgets-v1.json"),
            "worker invocation limits must match the sealed vertical",
        )

    trust = safe_load(
        root, "eva2/design/platform/trust-boundaries-v1.json", errors, checked
    )
    resources = trust.get("resources")
    resource_map = (
        {item.get("id"): item for item in resources if isinstance(item, dict)}
        if isinstance(resources, list)
        else {}
    )
    required_resources = {
        "model_provider_credential_fd",
        "model_provider_egress_socket",
        "worker_secret_fd",
        "worker_egress_socket",
        "container_engine_lifecycle_socket",
        "cli_temporary_migrator_database_grant",
        "cli_temporary_object_store_grant",
        "cli_temporary_backup_keywrap_fd",
        "worker_runtime_readonly_mount",
    }
    if (
        trust.get("contract") != "Eva2TrustBoundaries/v1"
        or set(trust.get("processes", [])) != PROCESS_IDS
        or trust.get("consumerPolicy")
        != "default_deny_every_process_not_listed_as_owner_or_consumer"
    ):
        issue(
            errors,
            "trust_boundary_map",
            Path("eva2/design/platform/trust-boundaries-v1.json"),
            "closed default-deny process/resource map required",
        )
    if not required_resources.issubset(resource_map):
        issue(
            errors,
            "missing_trust_resource",
            Path("eva2/design/platform/trust-boundaries-v1.json"),
            f"missing {sorted(required_resources - set(resource_map))}",
        )
    for resource_id, resource in resource_map.items():
        owner = resource.get("owner")
        consumers = resource.get("consumers")
        if owner not in PROCESS_IDS and not (
            isinstance(owner, str) and owner.startswith("external:")
        ):
            issue(
                errors,
                "resource_owner",
                Path("eva2/design/platform/trust-boundaries-v1.json"),
                f"{resource_id} has no valid owner",
            )
        if not isinstance(consumers, list) or any(
            value not in PROCESS_IDS
            and not (isinstance(value, str) and value.startswith("external:"))
            for value in consumers
        ):
            issue(
                errors,
                "resource_consumer",
                Path("eva2/design/platform/trust-boundaries-v1.json"),
                f"{resource_id} has invalid consumers",
            )
    worker_allowed = set(trust.get("connectorWorkerAllowedResources", []))
    actual_worker = {
        resource_id
        for resource_id, resource in resource_map.items()
        if resource.get("owner") == "connector_worker"
        or "connector_worker" in resource.get("consumers", [])
    }
    if worker_allowed != actual_worker or any(
        resource_map.get(resource_id, {}).get("kind")
        in {"database_grant", "secret_mount"}
        for resource_id in worker_allowed
    ):
        issue(
            errors,
            "worker_resource_boundary",
            Path("eva2/design/platform/trust-boundaries-v1.json"),
            "worker allowlist must be exhaustive and contain no database or persistent secret mount",
        )
    if resource_map.get("model_provider_credential_fd", {}).get("consumers") != [
        "model_adapter"
    ] or resource_map.get("model_provider_egress_socket", {}).get("consumers") != [
        "model_adapter"
    ]:
        issue(
            errors,
            "model_secret_egress_boundary",
            Path("eva2/design/platform/trust-boundaries-v1.json"),
            "model adapter needs its own broker FD and proxy path",
        )
    engine_consumers = set(
        resource_map.get("container_engine_lifecycle_socket", {}).get("consumers", [])
    )
    if engine_consumers != {"connector_supervisor", "eva_cli"}:
        issue(
            errors,
            "container_engine_boundary",
            Path("eva2/design/platform/trust-boundaries-v1.json"),
            "only supervisor and CLI may use lifecycle socket",
        )
    cli = trust.get("cliAccess")
    if not isinstance(cli, dict) or any(
        cli.get(field) is not False
        for field in (
            "ordinaryRuntimeDatabaseAccess",
            "ordinaryRuntimeObjectStoreAccess",
            "ordinaryRuntimeContainerEngineAccess",
            "persistentRuntimeCredentialAllowed",
        )
    ):
        issue(
            errors,
            "cli_temporary_boundary",
            Path("eva2/design/platform/trust-boundaries-v1.json"),
            "CLI access must be temporary and default denied",
        )

    status = "passed" if not errors else "failed"
    return {
        "contract": "Eva2DesignGateResult/v1",
        "gate": "PLATFORM-DESIGN-01",
        "status": status,
        "claimScope": "design_only",
        "runtimeEvidence": False,
        "installerClaim": False,
        "imageExecutionClaim": False,
        "platformSupportClaim": False,
        "checkedFiles": sorted(checked),
        "errors": sorted(
            errors, key=lambda item: (item["path"], item["code"], item["detail"])
        ),
    }


def supply_gate(root: Path) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    checked: set[str] = set()
    eva = root / "eva2"
    lock_path = eva / "requirements-4d.lock"
    roots_path = eva / "release/dependency-roots.in"
    pyproject_path = eva / "pyproject.toml"
    for relative in (
        "eva2/requirements-4d.lock",
        "eva2/release/dependency-roots.in",
        "eva2/pyproject.toml",
    ):
        checked.add(relative)
    try:
        lock_text = lock_path.read_text(encoding="utf-8")
        pins, _, lock_problems = parse_lock(lock_text)
        for problem in lock_problems:
            issue(errors, "dependency_lock", Path("eva2/requirements.lock"), problem)
        if pins != LOCKED_PINS:
            issue(
                errors,
                "dependency_pin_set",
                Path("eva2/requirements-4d.lock"),
                "exact 35-package direct/transitive set differs",
            )
    except OSError as exc:
        issue(errors, "dependency_lock", Path("eva2/requirements-4d.lock"), str(exc))
        lock_text, pins = "", {}
    try:
        root_pins, root_problems = parse_root_pins(
            roots_path.read_text(encoding="utf-8")
        )
        for problem in root_problems:
            issue(
                errors,
                "dependency_roots",
                Path("eva2/release/dependency-roots.in"),
                problem,
            )
        if root_pins != DIRECT_PINS:
            issue(
                errors,
                "dependency_root_set",
                Path("eva2/release/dependency-roots.in"),
                "direct/build pin set differs",
            )
    except OSError as exc:
        issue(
            errors,
            "dependency_roots",
            Path("eva2/release/dependency-roots.in"),
            str(exc),
        )
        root_pins = {}
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        project_dependencies = pyproject["project"]["dependencies"]
        project_pins: dict[str, str] = {}
        for dependency in project_dependencies:
            match = PIN.fullmatch(dependency)
            if not match:
                raise ValueError(f"unlocked project dependency {dependency!r}")
            project_pins[normalize_name(match.group(1))] = match.group(2)
        build_pins: dict[str, str] = {}
        for dependency in pyproject["build-system"]["requires"]:
            match = PIN.fullmatch(dependency)
            if not match:
                raise ValueError(f"unlocked build dependency {dependency!r}")
            build_pins[normalize_name(match.group(1))] = match.group(2)
        if (
            project_pins | build_pins != DIRECT_PINS
            or pyproject["project"].get("requires-python") != "==3.12.14"
        ):
            issue(
                errors,
                "pyproject_pin_set",
                Path("eva2/pyproject.toml"),
                "pyproject pins do not equal dependency roots or Python 3.12.14",
            )
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        issue(errors, "pyproject", Path("eva2/pyproject.toml"), str(exc))

    gate_lock_path = eva / "release/gate-toolchain.requirements.lock"
    gate_roots_path = eva / "release/gate-toolchain-roots.in"
    checked.update(
        {
            "eva2/release/gate-toolchain.requirements.lock",
            "eva2/release/gate-toolchain-roots.in",
        }
    )
    try:
        gate_lock_text = gate_lock_path.read_text(encoding="utf-8")
        gate_pins, _, gate_problems = parse_lock(gate_lock_text)
        for problem in gate_problems:
            issue(
                errors,
                "gate_toolchain_lock",
                Path("eva2/release/gate-toolchain.requirements.lock"),
                problem,
            )
        if gate_pins != TOOLCHAIN_PINS:
            issue(
                errors,
                "gate_toolchain_pin_set",
                Path("eva2/release/gate-toolchain.requirements.lock"),
                "exact 18-package gate/build/schema-consumer closure differs",
            )
    except OSError as exc:
        issue(
            errors,
            "gate_toolchain_lock",
            Path("eva2/release/gate-toolchain.requirements.lock"),
            str(exc),
        )
        gate_lock_text, gate_pins = "", {}
    try:
        gate_root_pins, gate_root_problems = parse_root_pins(
            gate_roots_path.read_text(encoding="utf-8")
        )
        for problem in gate_root_problems:
            issue(
                errors,
                "gate_toolchain_roots",
                Path("eva2/release/gate-toolchain-roots.in"),
                problem,
            )
        if gate_root_pins != TOOLCHAIN_DIRECT_PINS:
            issue(
                errors,
                "gate_toolchain_root_set",
                Path("eva2/release/gate-toolchain-roots.in"),
                "gate roots differ from their closed hash lock",
            )
    except OSError as exc:
        issue(
            errors,
            "gate_toolchain_roots",
            Path("eva2/release/gate-toolchain-roots.in"),
            str(exc),
        )

    toolchain = safe_load(
        root, "eva2/release/gate-toolchain.lock.json", errors, checked
    )
    expected_union = LOCKED_PINS | TOOLCHAIN_PINS
    declared_locks = toolchain.get("requirementLocks")
    expected_lock_rows = (
        [
            {
                "role": "application_and_contract_runtime",
                "path": "eva2/requirements-4d.lock",
                "sha256": sha256_file(lock_path),
                "packageCount": 35,
            },
            {
                "role": "design_gate_build_frontend_and_independent_schema_consumer",
                "path": "eva2/release/gate-toolchain.requirements.lock",
                "sha256": sha256_file(gate_lock_path),
                "packageCount": 18,
            },
        ]
        if lock_path.is_file() and gate_lock_path.is_file()
        else []
    )
    interpreter = toolchain.get("interpreter", {})
    policy = toolchain.get("environmentPolicy", {})
    if (
        toolchain.get("contract") != "Eva2GateToolchainLock/v1"
        or toolchain.get("scope") != "current_runtime_design_gates_only"
        or declared_locks != expected_lock_rows
        or toolchain.get("requiredDistributionCount") != len(expected_union)
        or toolchain.get("requiredDistributions") != expected_union
        or interpreter
        != {
            "implementation": "CPython",
            "version": "3.12.3",
            "cacheTag": "cpython-312",
            "executableSha256": "1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118",
            "targetRuntimeVersion": "3.12.14",
            "targetRuntimeExecutionClaim": False,
        }
        or policy.get("freshEnvironmentOutsideRepositoryRequired") is not True
        or policy.get("ambientUserSiteAllowed") is not False
        or policy.get("systemSitePackagesAllowed") is not False
        or policy.get("exactDistributionSetRequired") is not True
        or policy.get("networkAllowedDuringGateExecution") is not False
        or not _claims_are_false(toolchain)
    ):
        issue(
            errors,
            "gate_toolchain_contract",
            Path("eva2/release/gate-toolchain.lock.json"),
            f"exact external {len(expected_union)}-distribution design-gate environment is not bound",
        )

    tool_licenses = safe_load(
        root, "eva2/release/gate-toolchain-license-inventory.json", errors, checked
    )
    tool_rows = tool_licenses.get("packages")
    tool_license_pins: dict[str, str] = {}
    if isinstance(tool_rows, list):
        for package in tool_rows:
            if not isinstance(package, dict):
                continue
            name, version = package.get("name"), package.get("version")
            if isinstance(name, str) and isinstance(version, str):
                tool_license_pins[normalize_name(name)] = version
            if (
                not package.get("licenseExpression")
                or package.get("reviewStatus") != "metadata_recorded_not_legal_opinion"
            ):
                issue(
                    errors,
                    "gate_toolchain_license_entry",
                    Path("eva2/release/gate-toolchain-license-inventory.json"),
                    f"missing reviewed metadata for {name}",
                )
    if (
        tool_licenses.get("contract") != "Eva2GateToolchainLicenseInventory/v1"
        or tool_licenses.get("packageCount") != 18
        or tool_license_pins != TOOLCHAIN_PINS
        or tool_licenses.get("claims", {}).get("legalApprovalComplete") is not False
    ):
        issue(
            errors,
            "gate_toolchain_license_inventory",
            Path("eva2/release/gate-toolchain-license-inventory.json"),
            "inventory must cover all 18 gate/build/schema-consumer packages without legal-release claim",
        )

    inventory = safe_load(root, "eva2/release/license-inventory.json", errors, checked)
    packages = inventory.get("packages")
    inventory_pins: dict[str, str] = {}
    if isinstance(packages, list):
        for package in packages:
            if not isinstance(package, dict):
                continue
            name, version = package.get("name"), package.get("version")
            if isinstance(name, str) and isinstance(version, str):
                inventory_pins[normalize_name(name)] = version
            if (
                not package.get("licenseExpression")
                or package.get("reviewStatus") != "metadata_recorded_not_legal_opinion"
            ):
                issue(
                    errors,
                    "license_entry",
                    Path("eva2/release/license-inventory.json"),
                    f"missing reviewed metadata for {name}",
                )
            expected_source = f"https://pypi.org/pypi/{name}/{version}/json"
            if package.get("metadataSource") != expected_source:
                issue(
                    errors,
                    "license_source",
                    Path("eva2/release/license-inventory.json"),
                    f"unexpected primary metadata source for {name}",
                )
    if (
        inventory.get("contract") != "Eva2PythonLicenseInventory/v1"
        or inventory.get("packageCount") != 35
        or inventory_pins != LOCKED_PINS
        or inventory.get("claims", {}).get("legalApprovalComplete") is not False
    ):
        issue(
            errors,
            "license_inventory",
            Path("eva2/release/license-inventory.json"),
            "inventory must cover all 35 locked packages without legal-release claim",
        )

    bindings = safe_load(
        root, "eva2/release/design-source-bindings.json", errors, checked
    )
    binding_rows = bindings.get("bindings")
    actual_bindings: dict[str, tuple[Any, Any]] = {}
    if isinstance(binding_rows, list):
        for row in binding_rows:
            if isinstance(row, dict) and set(row) == {"id", "path", "sha256"}:
                actual_bindings[str(row["id"])] = (row["path"], row["sha256"])
    if (
        bindings.get("contract") != "Eva2DesignSourceBindings/v1"
        or bindings.get("pathPolicy") != "repository_relative_only"
        or bindings.get("secretsAllowed") is not False
        or bindings.get("legacyInputsAllowed") is not False
        or bindings.get("bindingCount") != 10
        or actual_bindings != SOURCE_BINDINGS
    ):
        issue(
            errors,
            "source_bindings",
            Path("eva2/release/design-source-bindings.json"),
            "exact sealed ten-row binding set required",
        )
    for identifier, (relative, expected_sha) in SOURCE_BINDINGS.items():
        checked.add(relative)
        target = (root / relative).resolve()
        try:
            if (
                not target.is_relative_to(root.resolve())
                or sha256_file(target) != expected_sha
            ):
                issue(
                    errors,
                    "source_binding_hash",
                    Path(relative),
                    f"{identifier} does not match sealed SHA-256",
                )
        except OSError as exc:
            issue(errors, "source_binding_hash", Path(relative), str(exc))

    platform_lock = safe_load(root, "eva2/release/platform-lock.json", errors, checked)
    platform_images = platform_lock.get("oci_images")
    platform_image_slots: list[tuple[str, str]] = []
    platform_image_rows_valid = isinstance(platform_images, list)
    if isinstance(platform_images, list):
        for image in platform_images:
            if not isinstance(image, dict):
                platform_image_rows_valid = False
                continue
            component = image.get("component")
            architecture = image.get("architecture")
            if not isinstance(component, str) or not isinstance(architecture, str):
                platform_image_rows_valid = False
                continue
            platform_image_slots.append((component, architecture))
    expected_platform_image_slots = {
        (component, architecture)
        for component in OCI_COMPONENT_IDS
        for architecture in ("amd64", "arm64")
    }
    if (
        not platform_image_rows_valid
        or len(platform_image_slots) != len(expected_platform_image_slots)
        or set(platform_image_slots) != expected_platform_image_slots
    ):
        issue(
            errors,
            "oci_platform_slot_set",
            Path("eva2/release/platform-lock.json"),
            "PlatformLock must contain each final-image component exactly once per architecture",
        )
    platform_component_ids = frozenset(
        component for component, _ in platform_image_slots
    )

    oci = safe_load(root, "eva2/release/oci-image-slots.json", errors, checked)
    bases = oci.get("baseSlots")
    base_map = (
        {item.get("id"): item for item in bases if isinstance(item, dict)}
        if isinstance(bases, list)
        else {}
    )
    if (
        oci.get("contract") != "Eva2OciImageSlots/v1"
        or oci.get("runnable") is not False
        or oci.get("mutableTagsAllowedAsAuthority") is not False
        or oci.get("authorityRequiresDigest") is not True
        or set(base_map) != set(BASE_DIGESTS)
    ):
        issue(
            errors,
            "oci_base_lock",
            Path("eva2/release/oci-image-slots.json"),
            "exact three digest-authoritative, non-runnable base slots required",
        )
    for base_id, expected in BASE_DIGESTS.items():
        base = base_map.get(base_id, {})
        if (
            base.get("indexDigest") != expected["index"]
            or base.get("authorityReference")
            != f"{base.get('repository')}@{expected['index']}"
        ):
            issue(
                errors,
                "oci_index_digest",
                Path("eva2/release/oci-image-slots.json"),
                f"{base_id} index authority differs",
            )
        platforms = base.get("platformManifests", {})
        if not isinstance(platforms, dict) or set(platforms) != {
            "linux/amd64",
            "linux/arm64",
        }:
            issue(
                errors,
                "oci_platform_matrix",
                Path("eva2/release/oci-image-slots.json"),
                f"{base_id} lacks exact Linux manifests",
            )
        else:
            for arch in ("amd64", "arm64"):
                record = platforms.get(f"linux/{arch}", {})
                if record.get("manifestDigest") != expected[
                    arch
                ] or not OCI_DIGEST.fullmatch(str(record.get("configDigest", ""))):
                    issue(
                        errors,
                        "oci_child_digest",
                        Path("eva2/release/oci-image-slots.json"),
                        f"{base_id} {arch} digest differs",
                    )
        source = base.get("officialImagesSource", {})
        entry = source.get("entry") if isinstance(source, dict) else None
        if (
            not isinstance(entry, str)
            or hashlib.sha256(entry.encode("utf-8")).hexdigest()
            != source.get("entrySha256")
            or not source.get("url", "").startswith(
                "https://raw.githubusercontent.com/docker-library/official-images/"
            )
        ):
            issue(
                errors,
                "oci_provenance",
                Path("eva2/release/oci-image-slots.json"),
                f"{base_id} official-images preimage is unbound",
            )
        license_data = base.get("license")
        if (
            not isinstance(license_data, dict)
            or not license_data.get("model")
            or not str(license_data.get("primarySource", "")).startswith("https://")
            or license_data.get("legalClearanceComplete") is not False
        ):
            issue(
                errors,
                "oci_license",
                Path("eva2/release/oci-image-slots.json"),
                f"{base_id} license provenance/nonclaim missing",
            )
    assignments = oci.get("componentBaseAssignments")
    assignments_valid = (
        isinstance(assignments, dict)
        and frozenset(assignments) == OCI_COMPONENT_IDS
        and all(
            isinstance(value, str) and value in BASE_DIGESTS
            for value in assignments.values()
        )
    )
    if not assignments_valid:
        issue(
            errors,
            "oci_base_assignment",
            Path("eva2/release/oci-image-slots.json"),
            "every component needs one selected base",
        )
    assignment_ids = (
        frozenset(assignments) if isinstance(assignments, dict) else frozenset()
    )
    final_slots = oci.get("finalImageSlots")
    final_slot_ids: list[str] = []
    final_slot_shape_valid = isinstance(final_slots, list)
    final_slot_nonclaims_valid = isinstance(final_slots, list)
    if isinstance(final_slots, list):
        for slot in final_slots:
            if not isinstance(slot, dict) or set(slot) != {"id", "digest", "status"}:
                final_slot_shape_valid = False
                final_slot_nonclaims_valid = False
                continue
            slot_id = slot.get("id")
            if isinstance(slot_id, str):
                final_slot_ids.append(slot_id)
            else:
                final_slot_shape_valid = False
            if (
                slot.get("digest") is not None
                or slot.get("status") != "unresolved_4a_not_runnable"
            ):
                final_slot_nonclaims_valid = False
    if not final_slot_nonclaims_valid:
        issue(
            errors,
            "oci_final_nonclaim",
            Path("eva2/release/oci-image-slots.json"),
            "final image slots must be explicit unresolved placeholders",
        )
    final_slot_id_set = frozenset(final_slot_ids)
    if (
        not final_slot_shape_valid
        or len(final_slot_ids) != len(final_slot_id_set)
        or final_slot_id_set != OCI_COMPONENT_IDS
        or final_slot_id_set != assignment_ids
        or final_slot_id_set != platform_component_ids
    ):
        issue(
            errors,
            "oci_final_slot_set",
            Path("eva2/release/oci-image-slots.json"),
            "final slot IDs must be unique and exactly match assignments and PlatformLock components",
        )
    claims = oci.get("claims", {})
    if claims.get("baseManifestMetadataSelected") is not True or any(
        claims.get(field) is not False
        for field in (
            "baseBytesPulledOrExecuted",
            "finalImagesBuilt",
            "imageSignaturesExist",
            "installerExists",
            "runtimeSupportProven",
        )
    ):
        issue(
            errors,
            "oci_claim_inflation",
            Path("eva2/release/oci-image-slots.json"),
            "base metadata may be selected but no image/runtime claim is allowed",
        )

    wheel_path = "eva2/release/managed-b-wheel-coverage-4d-amd64-v1.json"
    wheels = safe_load(root, wheel_path, errors, checked)
    if (
        wheels.get("contract") != "EvaManagedBAlignedWheelCoverage/v1"
        or wheels.get("requirementsLockSha256")
        != hashlib.sha256(lock_text.encode("utf-8")).hexdigest()
        or wheels.get("onlyBinary") is not True
        or wheels.get("status")
        != "derived_from_hash_pinned_baseline_minus_unused_dbos_transitives_plus_pynacl_no_install_or_execution"
    ):
        issue(
            errors,
            "wheel_coverage",
            Path(wheel_path),
            "wheel receipt must bind the current hash lock",
        )
    targets = wheels.get("targets")
    target_map = (
        {item.get("architecture"): item for item in targets if isinstance(item, dict)}
        if isinstance(targets, list)
        else {}
    )
    for arch in ("amd64",):
        target = target_map.get(arch, {})
        artifacts = target.get("artifacts")
        artifact_pins: dict[str, str] = {}
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                filename = artifact.get("filename")
                digest = artifact.get("sha256")
                if (
                    not isinstance(filename, str)
                    or "/" in filename
                    or "\\" in filename
                    or not HEX64.fullmatch(str(digest))
                    or digest not in lock_text
                ):
                    issue(
                        errors,
                        "wheel_artifact",
                        Path(wheel_path),
                        f"invalid {arch} wheel evidence",
                    )
                    continue
                parts = filename.removesuffix(".whl").split("-")
                if len(parts) >= 2:
                    artifact_pins[normalize_name(parts[0])] = parts[1]
        if (
            target.get("os") != "Ubuntu Server 24.04.4 LTS"
            or target.get("pythonVersion") != "3.12.14"
            or target.get("resolvedWheelCount") != 35
            or target.get("allArtifactsMatchedRequirementsLockHashes") is not True
            or artifact_pins != LOCKED_PINS
        ):
            issue(
                errors,
                "wheel_target",
                Path(wheel_path),
                f"{arch} does not resolve all 35 locked wheels",
            )
    if any(
        wheels.get("claims", {}).get(field) is True
        for field in (
            "wheelsInstalled",
            "targetRuntimeStarted",
            "nativeArchitectureExecutionProven",
            "installerExists",
        )
    ):
        issue(
            errors,
            "wheel_claim_inflation",
            Path(wheel_path),
            "wheel metadata is not execution evidence",
        )

    schema_paths = {
        "eva2/release/formats/eva-sbom-binding-v1.schema.json",
        "eva2/release/formats/eva-signature-envelope-v1.schema.json",
        "eva2/release/formats/eva-trust-root-v1.schema.json",
        "eva2/release/formats/eva-revocation-list-v1.schema.json",
        "eva2/release/formats/eva-build-inputs-v1.schema.json",
        "eva2/release/formats/eva-build-provenance-v1.schema.json",
        "eva2/release/formats/eva-release-manifest-v1.schema.json",
    }
    schemas: dict[str, dict[str, Any]] = {}
    for relative in sorted(schema_paths):
        schema = safe_load(root, relative, errors, checked)
        schemas[relative] = schema
        if (
            schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema"
            or schema.get("type") != "object"
            or schema.get("additionalProperties") is not False
            or not schema.get("required")
        ):
            issue(
                errors,
                "release_format",
                Path(relative),
                "closed draft-2020-12 object schema required",
            )

    sbom = safe_load(root, "eva2/release/sbom-policy.json", errors, checked)
    signature = safe_load(root, "eva2/release/signature-policy.json", errors, checked)
    trust = safe_load(root, "eva2/release/trust-root-slot.json", errors, checked)
    revocation = safe_load(root, "eva2/release/revocation-policy.json", errors, checked)
    build = safe_load(root, "eva2/release/build-policy.json", errors, checked)
    if sbom.get("authoritativeFormat") != "SPDX-2.3-json" or not _claims_are_false(
        sbom
    ):
        issue(
            errors,
            "sbom_policy",
            Path("eva2/release/sbom-policy.json"),
            "SPDX format and artifact nonclaims required",
        )
    expected_scope_policies = [
        {"scope": "release", "minimumValidSignatures": 1, "keyRole": "release_signer"},
        {"scope": "image", "minimumValidSignatures": 1, "keyRole": "release_signer"},
        {
            "scope": "connector_artifact",
            "minimumValidSignatures": 1,
            "keyRole": "connector_artifact_signer",
        },
        {
            "scope": "connector_promotion",
            "minimumValidSignatures": 1,
            "keyRole": "connector_promotion_signer",
        },
        {
            "scope": "revocation_list",
            "minimumValidSignatures": 1,
            "keyRole": "offline_root",
        },
    ]
    expected_multisignature_policy = {
        "minimumThreshold": 1,
        "additionalValidSignaturesAllowed": True,
        "thresholdAuthority": "signed_trust_root",
    }
    if (
        signature.get("algorithm") != "Ed25519"
        or signature.get("scopePolicies") != expected_scope_policies
        or signature.get("multisignaturePolicy") != expected_multisignature_policy
        or not _claims_are_false(signature)
    ):
        issue(
            errors,
            "signature_policy",
            Path("eva2/release/signature-policy.json"),
            "closed Ed25519 policy without signature claim required",
        )
    if (
        trust.get("status") != "unresolved_4a_not_usable"
        or trust.get("publicKeys") != []
        or trust.get("privateKeyMaterialAllowed") is not False
        or trust.get("minimumOfflineRootThreshold") != 1
        or trust.get("multisignaturePolicy")
        != "optional_threshold_selected_by_root_ceremony"
        or trust.get("usable") is not False
    ):
        issue(
            errors,
            "trust_root_slot",
            Path("eva2/release/trust-root-slot.json"),
            "4A trust root must contain no key material and be unusable",
        )
    expected_stale_list_policy = {
        "blocks": ["new_install", "update", "activation"],
        "allowsLastKnownGood": [
            "installed_image_restart",
            "rollback_to_previously_verified_installed_image",
        ],
        "knownRevocationAlwaysBlocks": True,
        "networkRefreshRequiredForAllowedOperation": False,
    }
    if (
        revocation.get("failClosed") is not True
        or revocation.get("failClosedFor")
        != ["invalid_signature", "known_revoked_subject", "sequence_rollback"]
        or revocation.get("minimumValidRootSignatures") != 1
        or revocation.get("multisignaturePolicy")
        != "optional_threshold_declared_by_signed_trust_root"
        or revocation.get("freshnessRequiredFor")
        != ["new_install", "update", "activation"]
        or revocation.get("staleListPolicy") != expected_stale_list_policy
        or "checksBefore" in revocation
        or revocation.get("catalogCannotOverrideRevocation") is not True
        or revocation.get("minimumSupportedReleaseEnforcedFor")
        != ["new_install", "update", "activation"]
        or "minimumSupportedReleaseEnforced" in revocation
        or not _claims_are_false(revocation)
    ):
        issue(
            errors,
            "revocation_policy",
            Path("eva2/release/revocation-policy.json"),
            "stale lists must block only fresh activation paths while known revocations remain fail-closed",
        )
    trust_schema = schemas.get("eva2/release/formats/eva-trust-root-v1.schema.json", {})
    trust_properties = trust_schema.get("properties", {})
    trust_keys = (
        trust_properties.get("keys", {}) if isinstance(trust_properties, dict) else {}
    )
    trust_threshold = (
        trust_properties.get("threshold", {})
        if isinstance(trust_properties, dict)
        else {}
    )
    revocation_schema = schemas.get(
        "eva2/release/formats/eva-revocation-list-v1.schema.json", {}
    )
    revocation_properties = revocation_schema.get("properties", {})
    revocation_signatures = (
        revocation_properties.get("signatures", {})
        if isinstance(revocation_properties, dict)
        else {}
    )
    if trust_threshold.get("minimum") != 1 or trust_keys.get("minItems") != 1:
        issue(
            errors,
            "trust_root_schema_threshold",
            Path("eva2/release/formats/eva-trust-root-v1.schema.json"),
            "trust-root schema must permit a one-key threshold while allowing multiple keys",
        )
    if revocation_signatures.get("minItems") != 1:
        issue(
            errors,
            "revocation_schema_threshold",
            Path("eva2/release/formats/eva-revocation-list-v1.schema.json"),
            "revocation-list schema must permit one valid root signature",
        )
    if (
        build.get("sourceTreeMustBeClean") is not True
        or build.get("compileAndPackage", {}).get("generalNetworkAllowed") is not False
        or build.get("compileAndPackage", {}).get("productionCredentialsAllowed")
        is not False
        or not _claims_are_false(build)
    ):
        issue(
            errors,
            "build_policy",
            Path("eva2/release/build-policy.json"),
            "closed reproducible build inputs and nonclaims required",
        )

    status = "passed" if not errors else "failed"
    return {
        "contract": "Eva2DesignGateResult/v1",
        "gate": "SUPPLY-DESIGN-01",
        "status": status,
        "claimScope": "design_only",
        "baseManifestMetadataSelected": True,
        "baseBytesExecuted": False,
        "finalImagesBuilt": False,
        "installerClaim": False,
        "runtimeSupportClaim": False,
        "checkedFiles": sorted(checked),
        "errors": sorted(
            errors, key=lambda item: (item["path"], item["code"], item["detail"])
        ),
    }


def run_gate(gate: str, root: Path) -> dict[str, Any]:
    if gate == "PLATFORM-DESIGN-01":
        return platform_gate(root)
    if gate == "SUPPLY-DESIGN-01":
        return supply_gate(root)
    platform = platform_gate(root)
    supply = supply_gate(root)
    return {
        "contract": "Eva2DesignGateSetResult/v1",
        "gate": "ALL",
        "status": "passed"
        if platform["status"] == supply["status"] == "passed"
        else "failed",
        "results": [platform, supply],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gate",
        choices=["PLATFORM-DESIGN-01", "SUPPLY-DESIGN-01", "ALL"],
        required=True,
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    args = parser.parse_args()
    result = run_gate(args.gate, args.root.resolve())
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
