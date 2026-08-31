from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
CHECKER_PATH = ROOT / "eva2/tools/check_component_catalog.py"
SCOPE_PREFIXES = [
    "eva2/src/eva/",
    "eva2/connector_sdk/",
    "eva2/connectors/",
    "eva2/deploy/",
    "eva2/migrations/",
    "eva2/tests/",
]
FIXTURE_TOOL_PATH = "eva2/tools/fixture_evidence_tool.py"
FIXTURE_TEST_PATH = "eva2/tests/application/test_a.py"
FIXTURE_TOOL_SOURCE = b"def check():\n    return True\n"
FIXTURE_APP_SOURCE = b"VALUE = 1\n"
FIXTURE_TEST_SOURCE = b"def test_a():\n    assert True\n"


def _checker():
    spec = importlib.util.spec_from_file_location(
        "eva2_component_catalog_checker", CHECKER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _component(
    component_id: str,
    owner_paths: list[str],
    *,
    disposition: str,
) -> dict[str, object]:
    return {
        "id": component_id,
        "name": f"Component {component_id}",
        "purpose": "Heeft één afgebakende verantwoordelijkheid.",
        "necessity": "Is nodig voor deze fixturegrens.",
        "disposition": disposition,
        "lifecycleStatus": "implemented",
        "ownerPaths": owner_paths,
        "forbiddenScope": ["Mag zijn componentgrens niet overschrijden."],
        "upstream": {
            "status": "not_applicable",
            "name": None,
            "version": None,
            "url": None,
            "reviewedOn": "2026-08-31",
        },
        "replacement": {
            "status": "retained",
            "target": None,
            "reason": "Geen vervanging in deze fixture.",
        },
        "verification": {
            "testPaths": ["eva2/tests/application/test_a.py"],
            "evidencePaths": [],
        },
        "blockers": [],
    }


def _fixture_documents() -> tuple[dict[str, object], dict[str, object]]:
    digest = "a" * 64
    inventory: dict[str, object] = {
        "schema": "EvaRepositoryInventory/v1",
        "sourceDigest": digest,
        "files": [
            {
                "path": "eva2/src/eva/application/a.py",
                "gitState": "tracked",
                "present": True,
                "contentSha256": hashlib.sha256(FIXTURE_APP_SOURCE).hexdigest(),
            },
            {
                "path": "eva2/tests/application/test_a.py",
                "gitState": "tracked",
                "present": True,
                "contentSha256": hashlib.sha256(FIXTURE_TEST_SOURCE).hexdigest(),
            },
            {
                "path": "eva2/src/eva/application/scratch.py",
                "gitState": "untracked",
            },
        ],
    }
    catalog: dict[str, object] = {
        "contract": "EvaComponentCatalog/v1",
        "schemaVersion": 1,
        "status": "implemented",
        "inventory": {
            "contract": "EvaRepositoryInventory/v1",
            "path": "docs/eva-repository-inventory.generated.json",
        },
        "managedToolRegistry": {
            "contract": "EvaManagedEvidenceToolRegistry/v1",
            "path": "eva2/design/architecture/evidence-tool-registry-v1.json",
            "requiredTools": [FIXTURE_TOOL_PATH],
        },
        "scopePrefixes": SCOPE_PREFIXES,
        "components": [
            _component(
                "application-core",
                ["eva2/src/eva/application/**"],
                disposition="unique_core",
            ),
            _component(
                "application-tests",
                ["eva2/tests/application/**"],
                disposition="test",
            ),
        ],
    }
    return catalog, inventory


def _fixture_tool_registry() -> dict[str, object]:
    return {
        "contract": "EvaManagedEvidenceToolRegistry/v1",
        "schemaVersion": 1,
        "status": "implemented",
        "tools": [
            {
                "path": FIXTURE_TOOL_PATH,
                "owner": "verification",
                "purpose": "Controleert de tijdelijke testfixture.",
                "riskClass": "repository_read_only",
                "sideEffects": ["Leest uitsluitend tijdelijke fixturedata."],
                "lifecycleStatus": "managed",
                "invocationPolicy": "ci_or_read_only_operator",
                "sourceSha256": hashlib.sha256(FIXTURE_TOOL_SOURCE).hexdigest(),
                "verification": {
                    "testPaths": [FIXTURE_TEST_PATH],
                    "evidencePaths": [],
                },
            }
        ],
    }


def _run(
    tmp_path: Path,
    catalog: dict[str, object],
    inventory: dict[str, object],
    tool_registry: dict[str, object] | None = None,
):
    catalog_path = tmp_path / "catalog.json"
    inventory_path = tmp_path / "inventory.json"
    tool_registry_path = tmp_path / "tool-registry.json"
    tool_path = tmp_path / FIXTURE_TOOL_PATH
    app_path = tmp_path / "eva2/src/eva/application/a.py"
    test_path = tmp_path / FIXTURE_TEST_PATH
    tool_path.parent.mkdir(parents=True, exist_ok=True)
    app_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.parent.mkdir(parents=True, exist_ok=True)
    tool_path.write_bytes(FIXTURE_TOOL_SOURCE)
    app_path.write_bytes(FIXTURE_APP_SOURCE)
    test_path.write_bytes(FIXTURE_TEST_SOURCE)
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    tool_registry_path.write_text(
        json.dumps(tool_registry or _fixture_tool_registry()), encoding="utf-8"
    )
    return _checker().check(
        tmp_path,
        catalog_path,
        inventory_path,
        tool_registry_path,
    )


def _codes(violations) -> set[str]:
    return {item.code for item in violations}


def test_repository_catalog_has_one_owner_for_every_tracked_scoped_path() -> None:
    checker = _checker()

    assert checker.check(ROOT) == []


def test_valid_closed_fixture_passes_and_untracked_path_is_not_required(
    tmp_path: Path,
) -> None:
    catalog, inventory = _fixture_documents()

    assert _run(tmp_path, catalog, inventory) == []


def test_inventory_cannot_keep_stale_tracked_bytes(tmp_path: Path) -> None:
    catalog, inventory = _fixture_documents()
    files = inventory["files"]
    assert isinstance(files, list)
    app = files[0]
    assert isinstance(app, dict)
    app["contentSha256"] = "b" * 64

    violations = _run(tmp_path, catalog, inventory)

    assert "inventory_current_drift" in _codes(violations)


def test_missing_primary_owner_is_rejected(tmp_path: Path) -> None:
    catalog, inventory = _fixture_documents()
    catalog["components"] = [catalog["components"][1]]  # type: ignore[index]

    violations = _run(tmp_path, catalog, inventory)

    assert "owner_missing" in _codes(violations)


def test_ambiguous_primary_owner_is_rejected(tmp_path: Path) -> None:
    catalog, inventory = _fixture_documents()
    components = catalog["components"]
    assert isinstance(components, list)
    second = components[1]
    assert isinstance(second, dict)
    second["ownerPaths"].append("eva2/src/eva/application/**")  # type: ignore[union-attr]

    violations = _run(tmp_path, catalog, inventory)

    assert "owner_ambiguous" in _codes(violations)


def test_dead_owner_path_is_rejected(tmp_path: Path) -> None:
    catalog, inventory = _fixture_documents()
    components = catalog["components"]
    assert isinstance(components, list)
    first = components[0]
    assert isinstance(first, dict)
    first["ownerPaths"].append("eva2/src/eva/domain/**")  # type: ignore[union-attr]

    violations = _run(tmp_path, catalog, inventory)

    assert "owner_path_dead" in _codes(violations)


def test_unknown_lifecycle_status_is_rejected(tmp_path: Path) -> None:
    catalog, inventory = _fixture_documents()
    components = catalog["components"]
    assert isinstance(components, list)
    first = components[0]
    assert isinstance(first, dict)
    first["lifecycleStatus"] = "probably_done"

    violations = _run(tmp_path, catalog, inventory)

    assert "component_status" in _codes(violations)


def test_unknown_component_field_is_rejected(tmp_path: Path) -> None:
    catalog, inventory = _fixture_documents()
    components = catalog["components"]
    assert isinstance(components, list)
    first = components[0]
    assert isinstance(first, dict)
    first["silentCatchAll"] = True

    violations = _run(tmp_path, catalog, inventory)

    assert "component_fields" in _codes(violations)


def test_incomplete_pending_replacement_is_rejected(tmp_path: Path) -> None:
    catalog, inventory = _fixture_documents()
    components = catalog["components"]
    assert isinstance(components, list)
    first = components[0]
    assert isinstance(first, dict)
    first["disposition"] = "replacement_pending"
    replacement = first["replacement"]
    assert isinstance(replacement, dict)
    replacement["status"] = "replacement_pending"
    replacement["target"] = None

    violations = _run(tmp_path, catalog, inventory)

    assert "component_replacement_incomplete" in _codes(violations)


def test_inventory_contract_mismatch_is_rejected(tmp_path: Path) -> None:
    catalog, inventory = _fixture_documents()
    binding = catalog["inventory"]
    assert isinstance(binding, dict)
    binding["contract"] = "EvaRepositoryInventory/v0"

    violations = _run(tmp_path, catalog, inventory)

    assert "inventory_binding_contract" in _codes(violations)


def test_external_migration_tool_must_remain_quarantined(tmp_path: Path) -> None:
    catalog, inventory = _fixture_documents()
    registry = _fixture_tool_registry()
    tool = registry["tools"][0]  # type: ignore[index]
    assert isinstance(tool, dict)
    tool["riskClass"] = "external_migration_write"
    tool["invocationPolicy"] = "quarantined_explicit_operator_only"
    tool["lifecycleStatus"] = "managed"

    violations = _run(tmp_path, catalog, inventory, registry)

    assert "managed_tool_quarantine" in _codes(violations)


def test_required_managed_tool_cannot_disappear_from_registry(tmp_path: Path) -> None:
    catalog, inventory = _fixture_documents()
    registry = _fixture_tool_registry()
    registry["tools"] = []

    violations = _run(tmp_path, catalog, inventory, registry)

    assert "managed_tools" in _codes(violations)


def test_managed_tool_source_hash_is_sealed(tmp_path: Path) -> None:
    catalog, inventory = _fixture_documents()
    registry = _fixture_tool_registry()
    tool = registry["tools"][0]  # type: ignore[index]
    assert isinstance(tool, dict)
    tool["sourceSha256"] = "0" * 64

    violations = _run(tmp_path, catalog, inventory, registry)

    assert "managed_tool_hash" in _codes(violations)
