from __future__ import annotations

import importlib.util
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
            },
            {
                "path": "eva2/tests/application/test_a.py",
                "gitState": "tracked",
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


def _run(
    tmp_path: Path,
    catalog: dict[str, object],
    inventory: dict[str, object],
):
    catalog_path = tmp_path / "catalog.json"
    inventory_path = tmp_path / "inventory.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    return _checker().check(tmp_path, catalog_path, inventory_path)


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
