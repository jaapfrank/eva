#!/usr/bin/env python3
"""Fail-closed static package-boundary check for Eva 2 tranche 4A."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    code: str
    detail: str


def _load_json_exact(path: Path) -> dict[str, Any]:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate
    )
    if not isinstance(value, dict):
        raise ValueError("policy root must be an object")
    return value


def _top_level(module: str) -> str:
    return module.split(".", 1)[0]


def _matches(module: str, prefixes: list[str]) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes
    )


def _provider_specific_debt_violations(
    root: Path, policy_path: Path, raw_policy: object
) -> list[Violation]:
    if raw_policy is None:
        return []
    if not isinstance(raw_policy, dict):
        return [
            Violation(
                str(policy_path),
                1,
                "policy_provider_debt",
                "providerSpecificCoreDebt must be an object",
            )
        ]

    patterns = raw_policy.get("patterns")
    scoped_paths = raw_policy.get("scopedPaths")
    ceilings = raw_policy.get("fileCeilings")
    if (
        not isinstance(patterns, list)
        or not patterns
        or not all(isinstance(value, str) and value for value in patterns)
        or not isinstance(scoped_paths, list)
        or not scoped_paths
        or not all(isinstance(value, str) and value for value in scoped_paths)
        or not isinstance(ceilings, dict)
        or not all(
            isinstance(path, str)
            and isinstance(limit, int)
            and not isinstance(limit, bool)
            and limit >= 0
            for path, limit in ceilings.items()
        )
    ):
        return [
            Violation(
                str(policy_path),
                1,
                "policy_provider_debt",
                "invalid patterns, scopedPaths, or fileCeilings",
            )
        ]

    matcher = re.compile(
        "|".join(re.escape(value) for value in sorted(patterns, key=len, reverse=True)),
        re.IGNORECASE,
    )
    violations: list[Violation] = []
    for relative_file in sorted(ceilings):
        ceiling_path = root / relative_file
        if (
            not ceiling_path.is_file()
            or ceiling_path.is_symlink()
            or ceiling_path.suffix != ".py"
        ):
            violations.append(
                Violation(
                    str(ceiling_path),
                    1,
                    "stale_provider_debt_ceiling",
                    relative_file,
                )
            )
    checked: set[Path] = set()
    for relative_scope in scoped_paths:
        scope = root / relative_scope
        if not scope.is_dir():
            violations.append(
                Violation(str(scope), 1, "missing_provider_debt_scope", relative_scope)
            )
            continue
        for py_file in sorted(scope.rglob("*.py")):
            if py_file in checked:
                continue
            checked.add(py_file)
            count = len(matcher.findall(py_file.read_text(encoding="utf-8")))
            if count == 0:
                continue
            relative_file = py_file.relative_to(root).as_posix()
            ceiling = ceilings.get(relative_file)
            if ceiling is None:
                violations.append(
                    Violation(
                        str(py_file),
                        1,
                        "provider_specific_core_debt_new_file",
                        f"{count} provider-specific identifiers",
                    )
                )
            elif count > ceiling:
                violations.append(
                    Violation(
                        str(py_file),
                        1,
                        "provider_specific_core_debt_increase",
                        f"{count}>{ceiling} provider-specific identifiers",
                    )
                )
    return violations


def _imports(path: Path, module_name: str) -> list[tuple[int, str]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                current_package = (
                    module_name
                    if path.name == "__init__.py"
                    else module_name.rpartition(".")[0]
                )
                package_parts = current_package.split(".") if current_package else []
                ascents = node.level - 1
                if ascents > len(package_parts):
                    found.append((node.lineno, "<invalid-relative-import>"))
                    continue
                target_parts = package_parts[: len(package_parts) - ascents]
                if node.module:
                    target_parts.extend(node.module.split("."))
                target = ".".join(target_parts)
                found.append((node.lineno, target or "<invalid-relative-import>"))
            elif node.module:
                found.append((node.lineno, node.module))
        elif isinstance(node, ast.Call):
            direct = isinstance(node.func, ast.Name) and node.func.id == "__import__"
            via_importlib = (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.func.attr == "import_module"
            )
            if direct or via_importlib:
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    found.append((node.lineno, node.args[0].value))
                else:
                    found.append((node.lineno, "<dynamic-import>"))
    return found


def _module_name(root: Path, path: Path) -> str:
    src_root = root / "src"
    if path.is_relative_to(src_root):
        relative = path.relative_to(src_root)
    else:
        relative = path.relative_to(root)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = Path(parts[-1]).stem
    return ".".join(parts)


def _scoped_python_files(root: Path, policy: dict[str, Any]) -> set[Path]:
    """Return the exact Python files whose imports or source debt are checked."""

    scoped: set[Path] = set()
    for section in ("packages", "isolatedRoots"):
        raw_specs = policy.get(section, {})
        if not isinstance(raw_specs, dict):
            continue
        for raw_spec in raw_specs.values():
            if not isinstance(raw_spec, dict):
                continue
            directory = root / str(raw_spec.get("path", ""))
            if directory.is_dir():
                scoped.update(directory.rglob("*.py"))
    boundary_files = policy.get("boundaryFiles", [])
    if isinstance(boundary_files, list):
        for relative_path in boundary_files:
            if not isinstance(relative_path, str):
                continue
            candidate = root / relative_path
            if candidate.is_file() and candidate.suffix == ".py":
                scoped.add(candidate)
    provider_debt = policy.get("providerSpecificCoreDebt", {})
    if isinstance(provider_debt, dict):
        scoped_paths = provider_debt.get("scopedPaths", [])
        if isinstance(scoped_paths, list):
            for relative_path in scoped_paths:
                if not isinstance(relative_path, str):
                    continue
                directory = root / relative_path
                if directory.is_dir():
                    scoped.update(directory.rglob("*.py"))
    return scoped


def check(root: Path, policy_path: Path) -> list[Violation]:
    policy = _load_json_exact(policy_path)
    violations: list[Violation] = []
    stdlib = set(sys.stdlib_module_names)

    if policy.get("contract") != "Eva2PackageBoundaries/v1":
        return [
            Violation(
                str(policy_path), 1, "policy_contract", "unexpected policy contract"
            )
        ]

    packages = policy.get("packages")
    if not isinstance(packages, dict) or not packages:
        return [
            Violation(
                str(policy_path), 1, "policy_packages", "packages must be non-empty"
            )
        ]

    forbidden = policy.get("forbiddenImportPrefixes", [])
    if not isinstance(forbidden, list) or not all(
        isinstance(v, str) for v in forbidden
    ):
        return [
            Violation(
                str(policy_path), 1, "policy_forbidden", "invalid forbidden prefixes"
            )
        ]

    violations.extend(
        _provider_specific_debt_violations(
            root, policy_path, policy.get("providerSpecificCoreDebt")
        )
    )

    eva_root = root / "src/eva"
    declared_package_paths = {
        root / str(raw_spec.get("path", ""))
        for raw_spec in packages.values()
        if isinstance(raw_spec, dict)
    }
    unexpected_package_paths: set[Path] = set()
    if eva_root.is_dir() and policy.get("unexpectedPackagesAllowed") is False:
        expected_package_paths = {eva_root}
        for package_path in declared_package_paths:
            expected_package_paths.add(package_path)
            parent = package_path.parent
            while parent != eva_root and parent.is_relative_to(eva_root):
                expected_package_paths.add(parent)
                parent = parent.parent
        actual_package_paths = {path.parent for path in eva_root.rglob("__init__.py")}
        unexpected_package_paths = actual_package_paths - expected_package_paths
        for unexpected in sorted(unexpected_package_paths):
            violations.append(
                Violation(
                    str(unexpected),
                    1,
                    "unexpected_package",
                    unexpected.relative_to(eva_root).as_posix(),
                )
            )

    boundary_files = policy.get("boundaryFiles", [])
    if not isinstance(boundary_files, list) or not all(
        isinstance(v, str) for v in boundary_files
    ):
        violations.append(
            Violation(str(policy_path), 1, "policy_boundary_files", "invalid files")
        )
    else:
        for relative_path in boundary_files:
            boundary_path = root / relative_path
            if not boundary_path.is_file():
                violations.append(
                    Violation(
                        str(boundary_path), 1, "missing_boundary_file", relative_path
                    )
                )
                continue
            for line, module in _imports(
                boundary_path, _module_name(root, boundary_path)
            ):
                violations.append(
                    Violation(str(boundary_path), line, "boundary_file_import", module)
                )

    if (
        eva_root.is_dir()
        and policy.get("unexpectedPackagesAllowed") is False
        and isinstance(boundary_files, list)
        and all(isinstance(value, str) for value in boundary_files)
    ):
        boundary_paths = {root / relative_path for relative_path in boundary_files}
        for py_file in sorted(eva_root.rglob("*.py")):
            if py_file in boundary_paths:
                continue
            owners = [
                package_path
                for package_path in declared_package_paths
                if py_file.is_relative_to(package_path)
            ]
            if len(owners) == 1:
                owner = owners[0]
                namespace = py_file.parent
                while namespace != owner:
                    if not (namespace / "__init__.py").is_file():
                        violations.append(
                            Violation(
                                str(namespace),
                                1,
                                "undeclared_python_namespace",
                                namespace.relative_to(root).as_posix(),
                            )
                        )
                        break
                    namespace = namespace.parent
                continue
            if not owners and any(
                py_file.is_relative_to(path) for path in unexpected_package_paths
            ):
                continue
            code = "unowned_python_file" if not owners else "ambiguous_python_owner"
            violations.append(
                Violation(
                    str(py_file),
                    1,
                    code,
                    py_file.relative_to(root).as_posix(),
                )
            )

    for name, raw_spec in packages.items():
        if not isinstance(raw_spec, dict):
            violations.append(
                Violation(str(policy_path), 1, "policy_package", str(name))
            )
            continue
        package_path = root / str(raw_spec.get("path", ""))
        if not package_path.is_dir():
            violations.append(
                Violation(
                    str(package_path), 1, "missing_package", f"required package {name}"
                )
            )
            continue
        package_marker = package_path / "__init__.py"
        if not package_marker.is_file() or package_marker.is_symlink():
            violations.append(
                Violation(
                    str(package_marker),
                    1,
                    "missing_package_marker",
                    str(name),
                )
            )
        allowed_local = raw_spec.get("allowedLocalImports", [])
        allowed_external = raw_spec.get("allowedExternalImports", [])
        if not isinstance(allowed_local, list) or not isinstance(
            allowed_external, list
        ):
            violations.append(
                Violation(str(policy_path), 1, "policy_imports", str(name))
            )
            continue
        for py_file in sorted(package_path.rglob("*.py")):
            try:
                imports = _imports(py_file, _module_name(root, py_file))
            except (SyntaxError, UnicodeError) as exc:
                violations.append(
                    Violation(
                        str(py_file), getattr(exc, "lineno", 1) or 1, "parse", str(exc)
                    )
                )
                continue
            for line, module in imports:
                if _matches(module, forbidden):
                    violations.append(
                        Violation(str(py_file), line, "legacy_import", module)
                    )
                elif module == "eva" or module.startswith("eva."):
                    if not _matches(module, allowed_local):
                        violations.append(
                            Violation(
                                str(py_file), line, "local_import_boundary", module
                            )
                        )
                elif _top_level(module) not in stdlib and not _matches(
                    module, allowed_external
                ):
                    violations.append(
                        Violation(
                            str(py_file), line, "external_import_boundary", module
                        )
                    )

    isolated_roots = policy.get("isolatedRoots", {})
    if not isinstance(isolated_roots, dict):
        violations.append(
            Violation(str(policy_path), 1, "policy_isolated", "invalid roots")
        )
    else:
        for name, raw_spec in isolated_roots.items():
            if not isinstance(raw_spec, dict):
                violations.append(
                    Violation(str(policy_path), 1, "policy_isolated", str(name))
                )
                continue
            isolated_path = root / str(raw_spec.get("path", ""))
            if not isolated_path.is_dir():
                violations.append(
                    Violation(str(isolated_path), 1, "missing_isolated_root", str(name))
                )
                continue
            forbidden_imports = raw_spec.get("forbiddenImports", [])
            for py_file in sorted(isolated_path.rglob("*.py")):
                for line, module in _imports(py_file, _module_name(root, py_file)):
                    if _matches(module, forbidden_imports) or _matches(
                        module, forbidden
                    ):
                        violations.append(
                            Violation(
                                str(py_file), line, "isolated_import_boundary", module
                            )
                        )

    return sorted(
        violations, key=lambda item: (item.path, item.line, item.code, item.detail)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--policy", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    policy = (
        args.policy or root / "development/design/architecture/package-boundaries-v1.json"
    ).resolve()
    try:
        raw_policy = _load_json_exact(policy)
        violations = check(root, policy)
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "contract": "Eva2ArchitectureGateResult/v1",
                    "ok": False,
                    "error": str(exc),
                }
            )
        )
        return 2
    result = {
        "contract": "Eva2ArchitectureGateResult/v1",
        "ok": not violations,
        "policy": str(policy.relative_to(root)),
        "pythonFilesChecked": len(_scoped_python_files(root, raw_policy)),
        "pythonFilesDiscovered": sum(1 for _ in root.rglob("*.py")),
        "violations": [violation.__dict__ for violation in violations],
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
