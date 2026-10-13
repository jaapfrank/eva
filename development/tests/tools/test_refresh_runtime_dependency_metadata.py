from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

import pytest

from tools import refresh_runtime_dependency_metadata as refresh_tool


_PACKAGES = {
    "genai-prices": "0.1.5",
    "openai": "3.6.0",
    "pydantic-ai-slim": "2.37.0",
    "pydantic-graph": "2.37.0",
    "setuptools": "84.0.0",
}


@pytest.fixture
def native_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="eva2-dependency-refresh-test-", dir="/tmp"
    ) as path:
        yield Path(path)


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _wheel(path: Path, *, package: str, version: str) -> str:
    distribution = package.replace("-", "_")
    filename = f"{distribution}-{version}-py3-none-any.whl"
    target = path / filename
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(
            f"{distribution}-{version}.dist-info/METADATA",
            f"Name: {package}\nVersion: {version}\nLicense-Expression: MIT\n",
        )
    target.chmod(0o444)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical(value))


def _fixture(tmp_path: Path) -> tuple[Path, Path, tuple[Path, ...]]:
    root = tmp_path / "source"
    eva = root
    release = eva / "deploy/release"
    release.mkdir(parents=True)
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir(mode=0o700)
    hashes = {
        package: _wheel(wheelhouse, package=package, version=version)
        for package, version in _PACKAGES.items()
    }
    (eva / "deploy/requirements-4d.lock").write_text(
        "".join(
            f"{package}=={version} \\\n+    --hash=sha256:{hashes[package]}\n"
            for package, version in _PACKAGES.items()
        ),
        encoding="utf-8",
    )
    (release / "dependency-roots.in").write_text(
        "".join(f"{package}=={version}\n" for package, version in _PACKAGES.items()),
        encoding="utf-8",
    )
    (release / "gate-toolchain.requirements.lock").write_text(
        "pytest==8.4.2\n", encoding="utf-8"
    )
    (eva / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")

    binding = release / "4d-dependency-source-binding.json"
    coverage = release / "managed-b-wheel-coverage-4d-amd64-v1.json"
    manifest = release / "managed-b-wheelhouse-amd64-v2.json"
    inventory = release / "license-inventory.json"
    toolchain = release / "gate-toolchain.lock.json"
    _write_json(
        binding,
        {
            "dependencies": {},
            "licenseAndSbomRequirements": {"status": "old"},
            "pyprojectAdoption": {
                "directRuntimeAdded": [],
                "directRuntimeUpdated": [
                    "pydantic-ai-slim==2.32.1 -> pydantic-ai-slim==2.36.0"
                ],
            },
            "standaloneLock": {},
            "wheelhouseInstallProtocol": {},
        },
    )
    _write_json(
        coverage,
        {
            "targets": [
                {"artifacts": [], "lockedPackageCount": 0, "resolvedWheelCount": 0}
            ]
        },
    )
    _write_json(manifest, {})
    _write_json(inventory, {"metadataAsOf": "2026-08-31", "packages": []})
    _write_json(
        toolchain,
        {
            "generationRecord": {"applicationLock": {"frontend": "pip 24.0"}},
            "requiredDistributionCount": 2,
            "requiredDistributions": {"legacy-gate-only": "1.0", "pytest": "8.4.2"},
            "requirementLocks": [
                {
                    "packageCount": 0,
                    "role": "application_and_contract_runtime",
                    "sha256": "old-runtime",
                },
                {
                    "packageCount": 0,
                    "role": (
                        "design_gate_build_frontend_and_independent_schema_consumer"
                    ),
                    "sha256": "old-gate",
                },
            ],
        },
    )
    return root, wheelhouse, (binding, coverage, manifest, inventory, toolchain)


def test_refresh_stages_and_commits_the_complete_metadata_set(
    native_tmp_path: Path,
) -> None:
    root, wheelhouse, outputs = _fixture(native_tmp_path)

    receipt = refresh_tool.refresh(root=root, wheelhouse=wheelhouse)

    assert receipt["wheelCount"] == len(_PACKAGES)
    assert json.loads(outputs[3].read_bytes())["metadataAsOf"] == "2026-09-01"
    toolchain = json.loads(outputs[4].read_bytes())
    assert toolchain["generationRecord"]["applicationLock"]["frontend"] == "pip 24.0"
    assert toolchain["requiredDistributions"]["pydantic-ai-slim"] == "2.37.0"
    rows = {row["role"]: row for row in toolchain["requirementLocks"]}
    assert rows["application_and_contract_runtime"]["packageCount"] == len(_PACKAGES)
    assert rows["design_gate_build_frontend_and_independent_schema_consumer"] == {
        "packageCount": 1,
        "role": "design_gate_build_frontend_and_independent_schema_consumer",
        "sha256": hashlib.sha256(
            (root / "deploy/release/gate-toolchain.requirements.lock").read_bytes()
        ).hexdigest(),
    }
    assert not list(outputs[0].parent.glob(f"*{refresh_tool._OUTPUT_SUFFIX}"))


@pytest.mark.parametrize(
    "mutation",
    (
        "public_root",
        "root_symlink",
        "extra",
        "directory",
        "symlink",
        "hardlink",
        "writable",
    ),
)
def test_refresh_rejects_unsafe_or_non_exact_wheelhouses(
    native_tmp_path: Path, mutation: str
) -> None:
    root, wheelhouse, outputs = _fixture(native_tmp_path)
    originals = {path: path.read_bytes() for path in outputs}
    wheels = sorted(wheelhouse.iterdir())
    if mutation == "public_root":
        wheelhouse.chmod(0o755)
    elif mutation == "root_symlink":
        real = wheelhouse.with_name("real-wheelhouse")
        wheelhouse.rename(real)
        wheelhouse.symlink_to(real, target_is_directory=True)
    elif mutation == "extra":
        (wheelhouse / "README.txt").write_text("not a wheel", encoding="utf-8")
    elif mutation == "directory":
        (wheelhouse / "nested.whl").mkdir()
    elif mutation == "symlink":
        wheels[0].unlink()
        wheels[0].symlink_to(wheels[1].name)
    elif mutation == "hardlink":
        wheels[0].unlink()
        wheels[0].hardlink_to(wheels[1])
    else:
        wheels[0].chmod(0o644)

    with pytest.raises(RuntimeError):
        refresh_tool.refresh(root=root, wheelhouse=wheelhouse)

    assert {path: path.read_bytes() for path in outputs} == originals


def test_refresh_detects_a_wheel_changed_while_reading(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, wheelhouse, outputs = _fixture(native_tmp_path)
    originals = {path: path.read_bytes() for path in outputs}
    victim = sorted(wheelhouse.iterdir())[0]
    original_read = refresh_tool.os.read
    changed = False

    def mutating_read(descriptor: int, amount: int) -> bytes:
        nonlocal changed
        block = original_read(descriptor, amount)
        target = Path(f"/proc/self/fd/{descriptor}").resolve()
        if block and not changed and target == victim:
            changed = True
            victim.chmod(0o400)
        return block

    monkeypatch.setattr(refresh_tool.os, "read", mutating_read)
    with pytest.raises(RuntimeError, match="changed while it was read"):
        refresh_tool.refresh(root=root, wheelhouse=wheelhouse)

    assert changed is True
    assert {path: path.read_bytes() for path in outputs} == originals


def test_refresh_rolls_back_every_output_when_one_atomic_replace_fails(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, wheelhouse, outputs = _fixture(native_tmp_path)
    originals = {path: path.read_bytes() for path in outputs}
    original_replace = refresh_tool.os.replace
    failed = False
    first_replace = True

    def failing_replace(
        source: os.PathLike[str], destination: os.PathLike[str]
    ) -> None:
        nonlocal failed, first_replace
        if first_replace:
            first_replace = False
            assert (
                len(list(outputs[0].parent.glob(f"*{refresh_tool._OUTPUT_SUFFIX}")))
                == 10
            )
        if Path(destination) == outputs[2] and not failed:
            failed = True
            raise OSError("synthetic replace failure")
        original_replace(source, destination)

    monkeypatch.setattr(refresh_tool.os, "replace", failing_replace)
    with pytest.raises(RuntimeError, match="rolled back"):
        refresh_tool.refresh(root=root, wheelhouse=wheelhouse)

    assert failed is True
    assert {path: path.read_bytes() for path in outputs} == originals
    assert not list(outputs[0].parent.glob(f"*{refresh_tool._OUTPUT_SUFFIX}"))


def test_refresh_rejects_a_changed_application_lock_frontend_without_writes(
    native_tmp_path: Path,
) -> None:
    root, wheelhouse, outputs = _fixture(native_tmp_path)
    toolchain = json.loads(outputs[4].read_bytes())
    toolchain["generationRecord"]["applicationLock"]["frontend"] = "pip 26.0.1"
    _write_json(outputs[4], toolchain)
    originals = {path: path.read_bytes() for path in outputs}

    with pytest.raises(RuntimeError, match="application lock frontend"):
        refresh_tool.refresh(root=root, wheelhouse=wheelhouse)

    assert {path: path.read_bytes() for path in outputs} == originals


@pytest.mark.parametrize(
    "relative",
    (
        "deploy/requirements-4d.lock",
        "deploy/release/dependency-roots.in",
        "pyproject.toml",
        "deploy/release/gate-toolchain.requirements.lock",
    ),
)
def test_refresh_rejects_symlinked_source_inputs_without_writes(
    native_tmp_path: Path, relative: str
) -> None:
    root, wheelhouse, outputs = _fixture(native_tmp_path)
    source = root / relative
    retained = source.with_name(source.name + ".retained")
    source.rename(retained)
    source.symlink_to(retained.name)
    originals = {path: path.read_bytes() for path in outputs}

    with pytest.raises(RuntimeError, match="safe owned regular file"):
        refresh_tool.refresh(root=root, wheelhouse=wheelhouse)

    assert {path: path.read_bytes() for path in outputs} == originals


@pytest.mark.parametrize("directory", ("root", "eva", "release"))
def test_refresh_rejects_writable_or_indirect_source_directories(
    native_tmp_path: Path, directory: str
) -> None:
    root, wheelhouse, outputs = _fixture(native_tmp_path)
    selected = {
        "root": root,
        "eva": root,
        "release": root / "deploy/release",
    }[directory]
    selected.chmod(0o777)
    originals = {path: path.read_bytes() for path in outputs}

    with pytest.raises(RuntimeError, match="owned non-writable directory"):
        refresh_tool.refresh(root=root, wheelhouse=wheelhouse)

    assert {path: path.read_bytes() for path in outputs} == originals


def test_refresh_rejects_a_symlinked_source_root(native_tmp_path: Path) -> None:
    root, wheelhouse, outputs = _fixture(native_tmp_path)
    alias = native_tmp_path / "source-alias"
    alias.symlink_to(root, target_is_directory=True)
    originals = {path: path.read_bytes() for path in outputs}

    with pytest.raises(RuntimeError, match="canonical absolute path"):
        refresh_tool.refresh(root=alias, wheelhouse=wheelhouse)

    assert {path: path.read_bytes() for path in outputs} == originals
