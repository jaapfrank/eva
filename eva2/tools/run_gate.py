#!/usr/bin/env python3
"""Run one sealed Eva 2 tranche 4A design gate and write immutable evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import os
import platform
import re
import site
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from verify_source_bindings import (
    VERIFICATION_CONTRACT,
    canonical_json_bytes,
    load_json_exact,
    sha256_file,
    verify as verify_source_bindings,
)


RESULT_CONTRACT = "Eva24AGateResult/v1"
SUPPORTED_GATES = (
    "CONTRACT-01",
    "PLATFORM-DESIGN-01",
    "SUPPLY-DESIGN-01",
)
CLAIM_BOUNDARY = {
    "designOnly": True,
    "installerClaim": False,
    "liveClaim": False,
    "migrationClaim": False,
    "productionReadinessClaim": False,
    "runtimeClaim": False,
    "semanticQualityClaim": False,
    "supportClaim": False,
}
EXECUTION_BOUNDARY = {
    "databaseUsed": False,
    "eva1DataUsed": False,
    "networkUsed": False,
    "serviceStarted": False,
    "tranche4BWork": False,
}
LOCK_INPUTS = (
    "eva2/pyproject.toml",
    "eva2/requirements.lock",
    "eva2/release/dependency-roots.in",
    "eva2/release/gate-toolchain.lock.json",
    "eva2/release/gate-toolchain.requirements.lock",
    "eva2/release/gate-toolchain-roots.in",
    "eva2/release/platform-lock.json",
)
_FORBIDDEN_LOCAL_ENVIRONMENTS = {
    ".gate-venv",
    ".nox",
    ".tox",
    ".venv",
    "node_modules",
    "venv",
}


@dataclass(frozen=True)
class CheckSpec:
    identifier: str
    argv: tuple[str, ...]
    required_paths: tuple[str, ...] = ()
    required_modules: tuple[str, ...] = ()


DEFAULT_COMMANDS: dict[str, tuple[CheckSpec, ...]] = {
    "CONTRACT-01": (
        CheckSpec(
            "contract-schema-generation-check",
            ("{python}", "eva2/tools/generate_contract_schemas.py", "--check"),
            ("eva2/tools/generate_contract_schemas.py",),
        ),
        CheckSpec(
            "contract-tests",
            (
                "{python}",
                "-m",
                "pytest",
                "-q",
                "--capture=sys",
                "-p",
                "no:cacheprovider",
                "eva2/tests/contract",
            ),
            ("eva2/tests/contract",),
            ("pytest",),
        ),
    ),
    "PLATFORM-DESIGN-01": (
        CheckSpec(
            "architecture-package-boundary-check",
            ("{python}", "eva2/tools/check_architecture.py"),
            (
                "eva2/tools/check_architecture.py",
                "eva2/design/architecture/package-boundaries-v1.json",
            ),
        ),
        CheckSpec(
            "platform-design-check",
            (
                "{python}",
                "eva2/release/check_design.py",
                "--gate",
                "PLATFORM-DESIGN-01",
            ),
            ("eva2/release/check_design.py",),
        ),
        CheckSpec(
            "architecture-tests",
            (
                "{python}",
                "-m",
                "pytest",
                "-q",
                "--capture=sys",
                "-p",
                "no:cacheprovider",
                "eva2/tests/architecture",
            ),
            ("eva2/tests/architecture",),
            ("pytest",),
        ),
        CheckSpec(
            "platform-tests",
            (
                "{python}",
                "-m",
                "unittest",
                "discover",
                "-s",
                "eva2/tests/platform",
                "-p",
                "test_*.py",
            ),
            ("eva2/tests/platform",),
        ),
    ),
    "SUPPLY-DESIGN-01": (
        CheckSpec(
            "supply-design-check",
            (
                "{python}",
                "eva2/release/check_design.py",
                "--gate",
                "SUPPLY-DESIGN-01",
            ),
            ("eva2/release/check_design.py",),
        ),
        CheckSpec(
            "platform-tests",
            (
                "{python}",
                "-m",
                "unittest",
                "discover",
                "-s",
                "eva2/tests/platform",
                "-p",
                "test_*.py",
            ),
            ("eva2/tests/platform",),
        ),
        CheckSpec(
            "integration-evidence-tests",
            (
                "{python}",
                "-m",
                "unittest",
                "discover",
                "-s",
                "eva2/tests/design",
                "-p",
                "test_*.py",
            ),
            ("eva2/tests/design",),
        ),
    ),
}


class GateError(RuntimeError):
    """A deterministic gate-infrastructure error."""


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _excluded_input(relative_path: str) -> bool:
    parts = relative_path.split("/")
    if relative_path == "eva2/evidence/4a" or relative_path.startswith(
        "eva2/evidence/4a/"
    ):
        return True
    if any(
        part in {".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
        for part in parts
    ):
        return True
    return relative_path.endswith((".pyc", ".pyo")) or parts[-1] in {
        ".coverage",
        ".DS_Store",
    }


def build_input_snapshot(root: Path) -> dict[str, Any]:
    """Hash every 4A input under eva2, excluding only caches and 4A outputs."""

    root = root.resolve(strict=True)
    eva2_root = root / "eva2"
    if not eva2_root.is_dir() or eva2_root.is_symlink():
        raise GateError("eva2 input root is missing or is a symlink")
    files: list[dict[str, str]] = []
    for path in sorted(eva2_root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        relative_parts = set(relative.split("/"))
        if relative_parts & _FORBIDDEN_LOCAL_ENVIRONMENTS or path.name == "pyvenv.cfg":
            raise GateError(
                f"repository-local execution environment is forbidden: {relative}"
            )
        if _excluded_input(relative):
            continue
        if path.is_symlink():
            raise GateError(f"4A input is a symlink: {relative}")
        if path.is_file():
            files.append({"path": relative, "sha256": sha256_file(path)})
    if not files:
        raise GateError("eva2 input snapshot is empty")
    return {
        "algorithm": "sha256",
        "excluded": [
            "eva2/evidence/4a/**",
            "**/.mypy_cache/**",
            "**/.pytest_cache/**",
            "**/.ruff_cache/**",
            "**/__pycache__/**",
            "**/*.pyc",
            "**/*.pyo",
            "**/.coverage",
            "**/.DS_Store",
        ],
        "fileCount": len(files),
        "fingerprint": _canonical_digest(files),
        "files": files,
    }


def execution_environment(root: Path) -> dict[str, Any]:
    """Verify the exact external, hash-locked design-gate environment."""

    locked_inputs: list[dict[str, str]] = []
    for relative in LOCK_INPUTS:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise GateError(
                f"declared lock input is missing or is a symlink: {relative}"
            )
        locked_inputs.append({"path": relative, "sha256": sha256_file(path)})
    lock_path = root / "eva2/release/gate-toolchain.lock.json"
    try:
        toolchain = load_json_exact(lock_path)
    except (OSError, ValueError) as exc:
        raise GateError(f"gate toolchain lock is invalid: {exc}") from exc
    if (
        toolchain.get("contract") != "Eva2GateToolchainLock/v1"
        or toolchain.get("status")
        != "exact_design_gate_environment_target_runtime_unproven"
        or toolchain.get("scope") != "tranche_4a_design_gates_only"
    ):
        raise GateError("gate toolchain lock contract, status, or scope is invalid")

    executable = Path(sys.executable).resolve(strict=True)
    if not executable.is_file():
        raise GateError("Python checker executable is not a regular file")
    interpreter = toolchain.get("interpreter")
    actual_interpreter = {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "cacheTag": sys.implementation.cache_tag,
        "executableSha256": sha256_file(executable),
    }
    if not isinstance(interpreter, dict) or any(
        interpreter.get(field) != value for field, value in actual_interpreter.items()
    ):
        raise GateError("checker interpreter does not match the gate toolchain lock")
    if interpreter.get("targetRuntimeExecutionClaim") is not False:
        raise GateError("gate toolchain must not claim target-runtime execution")

    expected_distributions = toolchain.get("requiredDistributions")
    expected_count = toolchain.get("requiredDistributionCount")
    if (
        not isinstance(expected_distributions, dict)
        or not all(
            isinstance(name, str) and isinstance(version, str)
            for name, version in expected_distributions.items()
        )
        or expected_count != len(expected_distributions)
    ):
        raise GateError("gate toolchain distribution set is invalid")
    expected_normalized = {
        re.sub(r"[-_.]+", "-", name).lower(): version
        for name, version in expected_distributions.items()
    }
    if len(expected_normalized) != len(expected_distributions):
        raise GateError(
            "gate toolchain contains duplicate normalized distribution names"
        )

    pin_start = re.compile(r"(?m)^([A-Za-z0-9_.-]+)==([^ ;\\\n]+)")
    lock_pins: dict[str, str] = {}
    requirement_locks = toolchain.get("requirementLocks")
    if not isinstance(requirement_locks, list) or len(requirement_locks) != 2:
        raise GateError("gate toolchain must bind exactly two requirement locks")
    for entry in requirement_locks:
        if not isinstance(entry, dict):
            raise GateError("gate toolchain requirement lock entry is invalid")
        relative = entry.get("path")
        if not isinstance(relative, str) or relative not in LOCK_INPUTS:
            raise GateError("gate toolchain requirement lock path is invalid")
        path = root / relative
        if sha256_file(path) != entry.get("sha256"):
            raise GateError(f"requirement lock digest mismatch: {relative}")
        text = path.read_text(encoding="utf-8")
        matches = list(pin_start.finditer(text))
        parsed: dict[str, str] = {}
        for index, match in enumerate(matches):
            name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
            version = match.group(2)
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            if not re.search(r"--hash=sha256:[0-9a-f]{64}", text[match.start() : end]):
                raise GateError(f"requirement lock pin lacks a sha256 hash: {name}")
            if name in parsed:
                raise GateError(f"duplicate requirement lock pin: {name}")
            parsed[name] = version
        if entry.get("packageCount") != len(parsed):
            raise GateError(f"requirement lock package count mismatch: {relative}")
        for name, version in parsed.items():
            if name in lock_pins and lock_pins[name] != version:
                raise GateError(f"conflicting requirement lock pin: {name}")
            lock_pins[name] = version
    if lock_pins != expected_normalized:
        raise GateError("requirement lock union does not match requiredDistributions")

    prefix = Path(sys.prefix).resolve(strict=True)
    base_prefix = Path(sys.base_prefix).resolve(strict=True)
    if (
        prefix == base_prefix
        or prefix.is_relative_to(root)
        or site.ENABLE_USER_SITE is not False
    ):
        raise GateError(
            "gate must run in a clean external environment without user site"
        )
    installed: dict[str, str] = {}
    outside_environment: list[str] = []
    for distribution in importlib.metadata.distributions():
        raw_name = distribution.metadata.get("Name")
        if not isinstance(raw_name, str) or not raw_name:
            raise GateError("installed distribution has no canonical name")
        name = re.sub(r"[-_.]+", "-", raw_name).lower()
        if name in installed:
            raise GateError(f"duplicate installed distribution: {name}")
        installed[name] = distribution.version
        try:
            location = Path(distribution.locate_file("")).resolve(strict=True)
        except OSError as exc:
            raise GateError(f"cannot locate installed distribution: {name}") from exc
        if not location.is_relative_to(prefix):
            outside_environment.append(name)
    if outside_environment:
        raise GateError(
            "installed distributions escape the external environment: "
            + ", ".join(sorted(outside_environment))
        )
    if installed != expected_normalized:
        missing = sorted(set(expected_normalized) - set(installed))
        extra = sorted(set(installed) - set(expected_normalized))
        mismatched = sorted(
            name
            for name in set(installed) & set(expected_normalized)
            if installed[name] != expected_normalized[name]
        )
        raise GateError(
            "installed distribution closure mismatch "
            f"(missing={missing}, extra={extra}, versionMismatch={mismatched})"
        )
    policy = toolchain.get("environmentPolicy")
    expected_policy = {
        "freshEnvironmentOutsideRepositoryRequired": True,
        "ambientUserSiteAllowed": False,
        "systemSitePackagesAllowed": False,
        "exactDistributionSetRequired": True,
        "networkAllowedDuringGateExecution": False,
        "dependenciesInstalledByGateRunner": False,
    }
    if policy != expected_policy:
        raise GateError("gate toolchain environment policy is not exact")
    claims = toolchain.get("claims")
    if (
        not isinstance(claims, dict)
        or not claims
        or any(value is not False for value in claims.values())
    ):
        raise GateError("gate toolchain claims must all remain false")

    installed_rows = [
        {"name": name, "version": version}
        for name, version in sorted(installed.items())
    ]
    return {
        "checkerInterpreter": {
            **actual_interpreter,
            "targetRuntimeExecuted": False,
        },
        "declaredLockInputs": locked_inputs,
        "distributionCount": len(installed_rows),
        "distributionFingerprint": _canonical_digest(installed_rows),
        "distributions": installed_rows,
        "dependenciesInstalledByGate": False,
        "dependencyResolutionPerformedByGate": False,
        "externalEnvironmentVerified": True,
        "networkUsedByGate": False,
        "toolchainLockPath": "eva2/release/gate-toolchain.lock.json",
        "toolchainLockSha256": sha256_file(lock_path),
    }


_ELAPSED_PATTERNS = (re.compile(r"(?<= in )\d+(?:\.\d+)?s\b"),)


def _normalize_output(value: str | bytes | None, root: Path) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = value
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(str(root), "<repo>")
    text = text.replace(str(Path(sys.executable).resolve()), "<python>")
    for pattern in _ELAPSED_PATTERNS:
        text = pattern.sub("<elapsed>", text)
    return text


def _display_argv(spec: CheckSpec) -> list[str]:
    return ["<python>" if value == "{python}" else value for value in spec.argv]


def _subprocess_env(root: Path) -> dict[str, str]:
    source_root = str(root / "eva2/src")
    checker_bin = str(Path(sys.executable).parent)
    return {
        "EVA2_4A_OFFLINE": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.pathsep.join((checker_bin, os.defpath)),
        "PIP_NO_INDEX": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": source_root,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TZ": "UTC",
    }


def _run_check(root: Path, spec: CheckSpec, timeout_seconds: int) -> dict[str, Any]:
    display = _display_argv(spec)
    missing_paths = sorted(
        relative for relative in spec.required_paths if not (root / relative).exists()
    )
    missing_modules = sorted(
        module
        for module in spec.required_modules
        if importlib.util.find_spec(module) is None
    )
    if missing_paths or missing_modules:
        return {
            "id": spec.identifier,
            "command": display,
            "status": "not_proven",
            "returnCode": None,
            "missingPaths": missing_paths,
            "missingModules": missing_modules,
            "stdout": "",
            "stdoutSha256": hashlib.sha256(b"").hexdigest(),
            "stderr": "",
            "stderrSha256": hashlib.sha256(b"").hexdigest(),
        }

    argv = [sys.executable if value == "{python}" else value for value in spec.argv]
    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            env=_subprocess_env(root),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        stdout = _normalize_output(getattr(exc, "stdout", None), root)
        stderr = _normalize_output(getattr(exc, "stderr", None), root)
        reason = (
            "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "launch_error"
        )
        return {
            "id": spec.identifier,
            "command": display,
            "status": "not_proven",
            "returnCode": None,
            "reason": reason,
            "stdout": stdout,
            "stdoutSha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
            "stderr": stderr,
            "stderrSha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        }

    stdout = _normalize_output(completed.stdout, root)
    stderr = _normalize_output(completed.stderr, root)
    return {
        "id": spec.identifier,
        "command": display,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returnCode": completed.returncode,
        "stdout": stdout,
        "stdoutSha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr": stderr,
        "stderrSha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
    }


def _aggregate_status(checks: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(check.get("status")) for check in checks}
    if "failed" in statuses:
        return "failed"
    if "not_proven" in statuses:
        return "not_proven"
    return "passed"


def _source_summary(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "contract": receipt.get("contract"),
        "status": receipt.get("status"),
        "expectedCount": receipt.get("expectedCount"),
        "actualCount": receipt.get("actualCount"),
        "manifestPath": receipt.get("manifestPath"),
        "manifestSha256": receipt.get("manifestSha256"),
        "bindingsFingerprint": receipt.get("bindingsFingerprint"),
        "errors": receipt.get("errors", []),
    }


def write_content_addressed(
    evidence_dir: Path, prefix: str, value: Mapping[str, Any]
) -> tuple[Path, str]:
    payload = canonical_json_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / f"{prefix}-sha256-{digest}.json"
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if path.read_bytes() != payload:
            raise GateError(f"content-address collision at {path.name}")
    return path, digest


def evidence_directory(
    root: Path, evidence_dir: Path | None = None, *, create: bool
) -> Path:
    """Resolve the one authorized evidence directory without following symlinks."""

    expected = root / "eva2/evidence/4a"
    candidate = evidence_dir or expected
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.absolute() != expected.absolute():
        raise GateError("4A evidence must stay in eva2/evidence/4a")
    if create:
        candidate.mkdir(parents=True, exist_ok=True)
    current = root
    for part in ("eva2", "evidence", "4a"):
        current = current / part
        if current.is_symlink():
            raise GateError("4A evidence path contains a symlink")
    if not candidate.is_dir():
        raise GateError("4A evidence directory is missing")
    return candidate


SourceVerifier = Callable[[Path], dict[str, Any]]
EnvironmentVerifier = Callable[[Path], dict[str, Any]]


def source_receipt_passed(receipt: Mapping[str, Any]) -> bool:
    expected_count = receipt.get("expectedCount")
    actual_count = receipt.get("actualCount")
    fingerprint = receipt.get("bindingsFingerprint")
    manifest_digest = receipt.get("manifestSha256")
    return (
        receipt.get("contract") == VERIFICATION_CONTRACT
        and receipt.get("status") == "passed"
        and isinstance(expected_count, int)
        and not isinstance(expected_count, bool)
        and expected_count > 0
        and isinstance(actual_count, int)
        and not isinstance(actual_count, bool)
        and actual_count == expected_count
        and receipt.get("errors") == []
        and isinstance(receipt.get("manifestPath"), str)
        and isinstance(fingerprint, str)
        and re.fullmatch(r"[0-9a-f]{64}", fingerprint) is not None
        and isinstance(manifest_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", manifest_digest) is not None
    )


def run_gate(
    root: Path,
    gate: str,
    *,
    commands: Mapping[str, Sequence[CheckSpec]] | None = None,
    source_verifier: SourceVerifier = verify_source_bindings,
    environment_verifier: EnvironmentVerifier = execution_environment,
    evidence_dir: Path | None = None,
    timeout_seconds: int = 120,
) -> tuple[int, Path, dict[str, Any]]:
    """Run one gate.  Injection points are for hermetic unit fixtures only."""

    if gate not in SUPPORTED_GATES:
        raise GateError(f"unsupported gate: {gate}")
    selected_commands = DEFAULT_COMMANDS if commands is None else commands
    if set(selected_commands) != set(SUPPORTED_GATES):
        raise GateError("command map must define exactly the three supported gates")
    if timeout_seconds < 1:
        raise GateError("timeout must be positive")
    root = root.resolve(strict=True)
    output_dir = evidence_directory(root, evidence_dir, create=True)

    try:
        source_before = source_verifier(root)
    except Exception as exc:  # A verifier crash is itself fail-closed evidence.
        source_before = {
            "contract": VERIFICATION_CONTRACT,
            "status": "failed",
            "errors": [f"source verifier error: {type(exc).__name__}"],
        }
    try:
        snapshot_before = build_input_snapshot(root)
        snapshot_error: str | None = None
    except (GateError, OSError) as exc:
        snapshot_before = None
        snapshot_error = str(exc)
    try:
        environment_before = environment_verifier(root)
        environment_error: str | None = None
    except (GateError, OSError) as exc:
        environment_before = None
        environment_error = str(exc)

    checks: list[dict[str, Any]] = []
    if (
        source_receipt_passed(source_before)
        and snapshot_before is not None
        and environment_before is not None
    ):
        for spec in selected_commands[gate]:
            checks.append(_run_check(root, spec, timeout_seconds))
    elif not source_receipt_passed(source_before):
        checks.append(
            {
                "id": "source-binding-precondition",
                "command": [],
                "status": "failed",
                "returnCode": None,
                "reason": "sealed source bindings did not verify",
            }
        )
    elif snapshot_before is None:
        checks.append(
            {
                "id": "input-snapshot-precondition",
                "command": [],
                "status": "not_proven",
                "returnCode": None,
                "reason": snapshot_error,
            }
        )
    else:
        checks.append(
            {
                "id": "execution-environment-precondition",
                "command": [],
                "status": "not_proven",
                "returnCode": None,
                "reason": environment_error,
            }
        )

    try:
        source_after = source_verifier(root)
    except Exception as exc:
        source_after = {
            "contract": VERIFICATION_CONTRACT,
            "status": "failed",
            "errors": [f"source verifier error: {type(exc).__name__}"],
        }
    try:
        snapshot_after = build_input_snapshot(root)
        snapshot_after_error: str | None = None
    except (GateError, OSError) as exc:
        snapshot_after = None
        snapshot_after_error = str(exc)
    try:
        environment_after = environment_verifier(root)
        environment_after_error: str | None = None
    except (GateError, OSError) as exc:
        environment_after = None
        environment_after_error = str(exc)

    source_stable = _source_summary(source_before) == _source_summary(source_after)
    inputs_stable = snapshot_before is not None and snapshot_before == snapshot_after
    environment_stable = (
        environment_before is not None and environment_before == environment_after
    )
    if not source_receipt_passed(source_after) or not source_stable:
        checks.append(
            {
                "id": "source-binding-postcondition",
                "command": [],
                "status": "failed",
                "returnCode": None,
                "reason": "sealed source bindings changed or failed after checks",
            }
        )
    if not inputs_stable:
        checks.append(
            {
                "id": "input-snapshot-postcondition",
                "command": [],
                "status": "failed" if snapshot_before is not None else "not_proven",
                "returnCode": None,
                "reason": snapshot_after_error
                or "4A inputs changed during gate execution",
            }
        )
    if not environment_stable:
        checks.append(
            {
                "id": "execution-environment-postcondition",
                "command": [],
                "status": "failed" if environment_before is not None else "not_proven",
                "returnCode": None,
                "reason": environment_after_error
                or "checker interpreter or declared locks changed during gate execution",
            }
        )

    status = _aggregate_status(checks)
    result: dict[str, Any] = {
        "contract": RESULT_CONTRACT,
        "tranche": "4A",
        "gate": gate,
        "status": status,
        "claimBoundary": CLAIM_BOUNDARY,
        "executionBoundary": EXECUTION_BOUNDARY,
        "executionEnvironment": environment_after,
        "sourceBinding": _source_summary(source_after),
        "inputSnapshot": snapshot_after,
        "integrity": {
            "inputsStableDuringRun": inputs_stable,
            "executionEnvironmentStableDuringRun": environment_stable,
            "sourceBindingsStableDuringRun": source_stable,
        },
        "checks": checks,
    }
    prefix = f"gate-result-{gate.lower()}"
    path, _digest = write_content_addressed(output_dir, prefix, result)
    exit_code = 0 if status == "passed" else (2 if status == "not_proven" else 1)
    return exit_code, path, result


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", required=True, choices=SUPPORTED_GATES)
    parser.add_argument("--root", type=Path, default=_default_root())
    args = parser.parse_args(argv)
    try:
        exit_code, path, result = run_gate(args.root, args.gate)
        root = args.root.resolve()
        try:
            display_path = path.resolve().relative_to(root).as_posix()
        except ValueError:
            display_path = path.name
        receipt = {
            "contract": "Eva24AGateInvocation/v1",
            "evidencePath": display_path,
            "evidenceSha256": sha256_file(path),
            "gate": args.gate,
            "status": result["status"],
        }
    except (GateError, OSError) as exc:
        exit_code = 2
        receipt = {
            "contract": "Eva24AGateInvocation/v1",
            "evidencePath": None,
            "evidenceSha256": None,
            "gate": args.gate,
            "status": "not_proven",
            "error": str(exc),
        }
    sys.stdout.buffer.write(canonical_json_bytes(receipt))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
