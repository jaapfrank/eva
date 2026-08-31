from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = ROOT / "tools/check_architecture.py"


def _checker():
    spec = importlib.util.spec_from_file_location(
        "eva2_architecture_checker", CHECKER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _seed_policy_tree(root: Path, policy: dict[str, object]) -> None:
    packages = policy["packages"]
    assert isinstance(packages, dict)
    for raw_spec in packages.values():
        assert isinstance(raw_spec, dict)
        path = root / str(raw_spec["path"])
        path.mkdir(parents=True, exist_ok=True)
        (path / "__init__.py").write_text("", encoding="utf-8")
    isolated = policy["isolatedRoots"]
    assert isinstance(isolated, dict)
    for raw_spec in isolated.values():
        assert isinstance(raw_spec, dict)
        (root / str(raw_spec["path"])).mkdir(parents=True, exist_ok=True)
    boundary_files = policy["boundaryFiles"]
    assert isinstance(boundary_files, list)
    for relative_path in boundary_files:
        path = root / str(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    debt = policy["providerSpecificCoreDebt"]
    assert isinstance(debt, dict)
    ceilings = debt["fileCeilings"]
    assert isinstance(ceilings, dict)
    for relative_path in ceilings:
        path = root / str(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


def test_repository_package_boundaries_are_closed() -> None:
    checker = _checker()
    policy = ROOT / "design/architecture/package-boundaries-v1.json"
    assert checker.check(ROOT, policy) == []


def test_worker_cannot_import_living_core(tmp_path: Path) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    bad_worker = tmp_path / "connectors/mail/worker.py"
    bad_worker.parent.mkdir(parents=True, exist_ok=True)
    bad_worker.write_text("from eva.contracts import EffectIntent\n", encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("isolated_import_boundary", "eva.contracts")
    ]


def test_domain_cannot_import_an_adapter(tmp_path: Path) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    (tmp_path / "src/eva/domain/bad.py").write_text(
        "from eva.adapters.postgres import Repository\n", encoding="utf-8"
    )
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("local_import_boundary", "eva.adapters.postgres")
    ]


def test_tools_are_checked_as_an_outer_architecture_package(tmp_path: Path) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    (tmp_path / "tools/unreviewed.py").write_text("import requests\n", encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("external_import_boundary", "requests")
    ]


def test_relative_import_cannot_cross_a_boundary(tmp_path: Path) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    (tmp_path / "src/eva/domain/bad.py").write_text(
        "from ..adapters import postgres\n", encoding="utf-8"
    )
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("local_import_boundary", "eva.adapters")
    ]


def test_boundary_marker_cannot_become_a_second_composition_root(
    tmp_path: Path,
) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    (tmp_path / "src/eva/__init__.py").write_text("import fastapi\n", encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("boundary_file_import", "fastapi")
    ]


def test_worker_cannot_hide_living_core_import_behind_importlib(tmp_path: Path) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    bad_worker = tmp_path / "connectors/mail/worker.py"
    bad_worker.parent.mkdir(parents=True, exist_ok=True)
    bad_worker.write_text(
        'import importlib\nimportlib.import_module("eva.contracts")\n', encoding="utf-8"
    )
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("isolated_import_boundary", "eva.contracts")
    ]


def test_unlisted_nested_python_package_is_rejected(tmp_path: Path) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    unexpected = tmp_path / "src/eva/adapters/unreviewed"
    unexpected.mkdir(parents=True)
    (unexpected / "__init__.py").write_text("", encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("unexpected_package", "adapters/unreviewed")
    ]


def test_declared_package_requires_a_python_package_marker(tmp_path: Path) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    (tmp_path / "src/eva/domain/__init__.py").unlink()
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("missing_package_marker", "domain")
    ]


def test_loose_python_module_outside_declared_package_is_rejected(
    tmp_path: Path,
) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    loose_module = tmp_path / "src/eva/unreviewed.py"
    loose_module.write_text("import requests\n", encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("unowned_python_file", "src/eva/unreviewed.py")
    ]


def test_unlisted_pep420_namespace_module_is_rejected(tmp_path: Path) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    namespace_module = tmp_path / "src/eva/domain/unreviewed/worker.py"
    namespace_module.parent.mkdir(parents=True)
    namespace_module.write_text("import json\n", encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("undeclared_python_namespace", "src/eva/domain/unreviewed")
    ]


def test_new_provider_specific_core_file_is_rejected(tmp_path: Path) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    (tmp_path / "src/eva/application/new_provider.py").write_text(
        'PROVIDER = "home_assistant"\n', encoding="utf-8"
    )
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("provider_specific_core_debt_new_file", "1 provider-specific identifiers")
    ]


def test_provider_specific_core_debt_can_only_decrease(tmp_path: Path) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    debt = policy["providerSpecificCoreDebt"]
    assert isinstance(debt, dict)
    debt["fileCeilings"] = {"src/eva/application/advanced_tasks.py": 1}
    (tmp_path / "src/eva/application/advanced_tasks.py").write_text(
        'A = "home_assistant"\nB = "localtuya"\n', encoding="utf-8"
    )
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("provider_specific_core_debt_increase", "2>1 provider-specific identifiers")
    ]


def test_provider_specific_core_debt_rejects_a_removed_registered_file(
    tmp_path: Path,
) -> None:
    checker = _checker()
    policy = json.loads(
        (ROOT / "design/architecture/package-boundaries-v1.json").read_text(
            encoding="utf-8"
        )
    )
    _seed_policy_tree(tmp_path, policy)
    debt = policy["providerSpecificCoreDebt"]
    assert isinstance(debt, dict)
    ceilings = debt["fileCeilings"]
    assert isinstance(ceilings, dict)
    stale_path = next(iter(ceilings))
    (tmp_path / stale_path).unlink()
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    violations = checker.check(tmp_path, policy_path)

    assert [(item.code, item.detail) for item in violations] == [
        ("stale_provider_debt_ceiling", stale_path)
    ]
