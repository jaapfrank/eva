#!/usr/bin/env python3
"""Execute the exact managed-B release tests and emit one closed receipt.

The current authority distinguishes 147 executable test modules from 19 bound
support inputs. Thirteen physically distinct disposable databases include six
PostgreSQL execution slices inside one receipt group. Twelve databases remain
operator inputs; the isolated browser subgate creates and removes its own
thirteenth database and containers. This runner validates their bound
identities and writes no passing receipt unless collection and all seven
isolated groups are green.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import parse_qsl, unquote, urlsplit
import xml.etree.ElementTree as ET

from tools.managed_b_candidate.private_receipt import (
    PrivateReceiptError,
    write_new_private_json,
)
import zipfile

from tools.run_gate import execution_environment
from tools.managed_b_candidate.wheelhouse import validate_wheelhouse
from tools.run_managed_b_browser_release_subgate import (
    BrowserSubgateError,
    CONTRACT as BROWSER_SUBGATE_CONTRACT,
    EXPECTED_NODEIDS as BROWSER_NODEIDS,
    run_browser_subgate,
)


RECEIPT_CONTRACT = "EvaManagedBRequiredTestsExecution/v1"
AUTHORITY_CONTRACT = "EvaManagedBReleaseInputAuthority/v1"
EXPECTED_EXECUTABLE_COUNT = 147
EXPECTED_SUPPORT_COUNT = 19
EXPECTED_GROUP_COUNT = 7
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_PIN = re.compile(r"(?m)^([A-Za-z0-9_.-]+)==([^ ;\\\n]+)")
_PORTABLE_FILES = {
    "portable-effect": "tests/portable/test_portable_restore_effect.py",
    "portable-model": "tests/portable/test_portable_restore_model.py",
    "portable-object": "tests/portable/test_portable_restore_object_boundary.py",
    "portable-read": "tests/portable/test_portable_restore_read.py",
    "portable-full-schema": "tests/portable/test_portable_restore_full_schema.py",
}
_POSTGRES_PURE_FILES = frozenset(
    {
        "tests/postgres/test_4d_migration_admission.py",
        "tests/postgres/test_4d_migrations.py",
        "tests/postgres/test_connection.py",
        "tests/postgres/test_current_catalog.py",
        "tests/postgres/test_foreign_key_catalog.py",
    }
)
_POSTGRES_ACTION_CHAIN_FILES = (
    "tests/postgres/test_action_chain_effect_advances.py",
    "tests/postgres/test_action_chain_effect_authorizer.py",
    "tests/postgres/test_action_chain_effect_controls.py",
    "tests/postgres/test_action_chain_effect_dispatcher.py",
    "tests/postgres/test_action_chain_effect_readback_dispatcher.py",
    "tests/postgres/test_action_chain_effect_readback_executor.py",
    "tests/postgres/test_action_chain_effect_readback_results.py",
    "tests/postgres/test_action_chain_effect_readbacks.py",
    "tests/postgres/test_action_chain_effects.py",
    "tests/postgres/test_action_chain_ledger.py",
    "tests/postgres/test_action_chain_read_advances.py",
    "tests/postgres/test_action_chain_read_authorizer.py",
    "tests/postgres/test_action_chain_read_dispatcher.py",
)
_POSTGRES_BROWSER_FILES = ("tests/postgres/test_playwright_mcp_action_chain.py",)
_POSTGRES_LIVING_FILES = (
    "tests/postgres/test_operational_timeline.py",
    "tests/postgres/test_runtime_resource_usage.py",
)
_POSTGRES_4B_STORE_FILES = (
    "tests/postgres/test_delegated_work_store.py",
    "tests/postgres/test_delegated_work_migration.py",
    "tests/postgres/test_delegated_work_private_context.py",
    "tests/postgres/test_memory_repository.py",
    "tests/postgres/test_people_memory_overview.py",
)
_POSTGRES_4B_CURRENT_FILES = ("tests/postgres/test_postgres_migrations.py",)
_POSTGRES_4B_HISTORICAL_FILES = ("tests/postgres/test_postgres_schema.py",)
_DSN_ENV = {
    "portable-effect": ("EVA2_GATE_PORTABLE_EFFECT_DSN",),
    "portable-model": ("EVA2_GATE_PORTABLE_MODEL_DSN",),
    "portable-object": ("EVA2_GATE_PORTABLE_OBJECT_DSN",),
    "portable-read": ("EVA2_GATE_PORTABLE_READ_DSN",),
    "portable-full-schema": (
        "EVA2_GATE_PORTABLE_FULL_DSN",
        "EVA2_GATE_PORTABLE_SOURCE_DSN",
        "EVA2_GATE_PORTABLE_TARGET_DSN",
    ),
    "postgres-action-chain": ("EVA2_GATE_POSTGRES_ACTION_CHAIN_DSN",),
    "postgres-living": ("EVA2_GATE_POSTGRES_LIVING_DSN",),
    "postgres-4b-store": ("EVA2_GATE_POSTGRES_4B_STORE_DSN",),
    "postgres-4b-current": ("EVA2_GATE_POSTGRES_4B_CURRENT_DSN",),
    "postgres-4b-historical": ("EVA2_GATE_POSTGRES_4B_HISTORICAL_DSN",),
}
_DSN_PREFIX = {
    "postgres-action-chain": "eva2_action_chain_disposable_",
    "postgres-living": "eva2_living_disposable_",
    "postgres-4b-store": "eva2_4b_disposable_",
    "postgres-4b-current": "eva2_4b_disposable_",
    "postgres-4b-historical": "eva2_4b_disposable_",
}


class RequiredTestsError(RuntimeError):
    """A closed release-test admission or execution failure."""


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class TestInputs:
    authority_sha256: str
    executable: tuple[str, ...]
    support: tuple[str, ...]
    snapshot_sha256: str


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _safe_file(path: Path, *, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RequiredTestsError(f"{label} is inaccessible") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        raise RequiredTestsError(f"{label} is not one immutable regular file")
    return path.read_bytes()


def load_test_inputs(eva_root: Path) -> TestInputs:
    authority_path = eva_root / "release/managed-b-release-input-authority-v1.json"
    raw = _safe_file(authority_path, label="release test authority")
    try:
        authority = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequiredTestsError("release test authority is invalid JSON") from exc
    if (
        not isinstance(authority, dict)
        or authority.get("contract") != AUTHORITY_CONTRACT
        or set(authority)
        != {
            "contract",
            "contractRegistry",
            "migrationAuthority",
            "nonClaims",
            "requiredInputs",
            "requiredTests",
            "requiredTestSupportInputs",
            "schemaVersion",
            "status",
        }
    ):
        raise RequiredTestsError("release test authority contract differs")

    snapshots: list[dict[str, str]] = []
    groups: list[tuple[str, tuple[dict[str, object], ...]]] = []
    for key in ("requiredTests", "requiredTestSupportInputs"):
        rows = authority.get(key)
        if not isinstance(rows, list) or not rows:
            raise RequiredTestsError(f"{key} is empty or invalid")
        parsed: list[dict[str, object]] = []
        previous = ""
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
                raise RequiredTestsError(f"{key} row is invalid")
            relative, expected = row.get("path"), row.get("sha256")
            if (
                not isinstance(relative, str)
                or not relative
                or relative <= previous
                or relative.startswith("/")
                or ".." in Path(relative).parts
                or not isinstance(expected, str)
                or _SHA256.fullmatch(expected) is None
            ):
                raise RequiredTestsError(f"{key} path ordering or digest differs")
            actual = _sha256(_safe_file(eva_root / relative, label=relative))
            if actual != expected:
                raise RequiredTestsError(f"pinned test input differs: {relative}")
            snapshots.append({"path": relative, "sha256": actual})
            parsed.append(row)
            previous = relative
        groups.append((key, tuple(parsed)))
    executable = tuple(str(row["path"]) for row in groups[0][1])
    support = tuple(str(row["path"]) for row in groups[1][1])
    if (
        len(executable) != EXPECTED_EXECUTABLE_COUNT
        or len(support) != EXPECTED_SUPPORT_COUNT
        or any(not Path(path).name.startswith("test_") for path in executable)
        or any(Path(path).name.startswith("test_") for path in support)
        or set(executable) & set(support)
        or not {
            "tests/conftest.py",
            "tests/contract/conftest.py",
            "tests/postgres/conftest.py",
        }.issubset(support)
    ):
        raise RequiredTestsError("executable and support test classification differs")
    return TestInputs(
        authority_sha256=_sha256(raw),
        executable=executable,
        support=support,
        snapshot_sha256=_sha256(
            _canonical(sorted(snapshots, key=lambda row: row["path"]))
        ),
    )


def _lock_rows(lock_raw: bytes) -> dict[str, tuple[str, frozenset[str]]]:
    try:
        text = lock_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RequiredTestsError("gate requirement lock is not UTF-8") from exc
    matches = list(_PIN.finditer(text))
    result: dict[str, tuple[str, frozenset[str]]] = {}
    for index, match in enumerate(matches):
        name = _normalize(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        hashes = frozenset(
            re.findall(r"--hash=sha256:([0-9a-f]{64})", text[match.start() : end])
        )
        if name in result or not hashes:
            raise RequiredTestsError("gate requirement lock rows differ")
        result[name] = (match.group(2), hashes)
    if len(result) != 19:
        raise RequiredTestsError("gate requirement lock must contain 19 packages")
    return result


def _wheel_identity(raw: bytes) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            metadata_names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
                and len(PurePosixPath(name).parts) == 2
            ]
            if len(metadata_names) != 1:
                raise RequiredTestsError("gate wheel metadata count differs")
            metadata = archive.read(metadata_names[0]).decode("utf-8")
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise RequiredTestsError("gate wheel is invalid") from exc
    name_match = re.search(r"(?m)^Name: (.+)$", metadata)
    version_match = re.search(r"(?m)^Version: (.+)$", metadata)
    if name_match is None or version_match is None:
        raise RequiredTestsError("gate wheel identity is missing")
    return _normalize(name_match.group(1).strip()), version_match.group(1).strip()


def validate_gate_wheelhouse(eva_root: Path, wheelhouse: Path) -> dict[str, object]:
    if not wheelhouse.is_absolute():
        raise RequiredTestsError("gate wheelhouse path must be absolute")
    try:
        resolved = wheelhouse.resolve(strict=True)
    except OSError as exc:
        raise RequiredTestsError("gate wheelhouse is inaccessible") from exc
    if resolved != wheelhouse or not resolved.is_dir() or resolved.is_symlink():
        raise RequiredTestsError("gate wheelhouse must be one canonical directory")
    lock_path = eva_root / "release/gate-toolchain.requirements.lock"
    lock_raw = _safe_file(lock_path, label="gate requirement lock")
    expected = _lock_rows(lock_raw)
    seen: dict[str, dict[str, object]] = {}
    for path in sorted(resolved.iterdir(), key=lambda item: item.name):
        raw = _safe_file(path, label="gate wheel")
        if path.suffix != ".whl":
            raise RequiredTestsError("gate wheelhouse contains a non-wheel file")
        digest = _sha256(raw)
        name, version = _wheel_identity(raw)
        if name in seen or name not in expected:
            raise RequiredTestsError("gate wheel distribution set differs")
        expected_version, allowed_hashes = expected[name]
        if version != expected_version or digest not in allowed_hashes:
            raise RequiredTestsError("gate wheel version or digest differs")
        seen[name] = {
            "filename": path.name,
            "name": name,
            "sha256": digest,
            "version": version,
        }
    if set(seen) != set(expected):
        raise RequiredTestsError("gate wheelhouse closure differs")
    rows = [seen[name] for name in sorted(seen)]
    return {
        "lockSha256": _sha256(lock_raw),
        "packageCount": len(rows),
        "selectedManifestSha256": _sha256(_canonical(rows)),
    }


def _validate_runtime_wheelhouse(eva_root: Path, wheelhouse: Path) -> dict[str, object]:
    try:
        validated = validate_wheelhouse(
            (eva_root / "release/managed-b-wheelhouse-amd64-v2.json").read_bytes(),
            (
                eva_root / "release/managed-b-wheel-coverage-4d-amd64-v1.json"
            ).read_bytes(),
            wheelhouse,
            (eva_root / "requirements-4d.lock").read_bytes(),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise RequiredTestsError("runtime wheelhouse validation failed") from exc
    return dict(validated)


def _groups(executable: Sequence[str]) -> dict[str, tuple[str, ...]]:
    known = set(executable)
    portable = set(_PORTABLE_FILES.values())
    postgres_tree = {path for path in known if path.startswith("tests/postgres/")}
    postgres = postgres_tree - _POSTGRES_PURE_FILES
    pure = known - portable - postgres
    groups = {"pure": tuple(sorted(pure))}
    groups.update({name: (path,) for name, path in _PORTABLE_FILES.items()})
    groups["postgres"] = tuple(sorted(postgres))
    if (
        len(groups) != EXPECTED_GROUP_COUNT
        or set().union(*(set(paths) for paths in groups.values())) != known
        or sum(len(paths) for paths in groups.values()) != len(known)
        or postgres_tree & _POSTGRES_PURE_FILES != _POSTGRES_PURE_FILES
        or len(postgres) != 23
    ):
        raise RequiredTestsError("release test grouping differs")
    return groups


def _postgres_subgroups(paths: Sequence[str]) -> dict[str, tuple[str, ...]]:
    actual = set(paths)
    slices = (
        _POSTGRES_ACTION_CHAIN_FILES,
        _POSTGRES_BROWSER_FILES,
        _POSTGRES_LIVING_FILES,
        _POSTGRES_4B_STORE_FILES,
        _POSTGRES_4B_CURRENT_FILES,
        _POSTGRES_4B_HISTORICAL_FILES,
    )
    expected = set().union(*(set(group) for group in slices))
    if actual != expected or sum(len(group) for group in slices) != len(expected):
        raise RequiredTestsError("PostgreSQL test subgroup classification differs")
    return {
        "postgres-action-chain": _POSTGRES_ACTION_CHAIN_FILES,
        "postgres-browser-physical": _POSTGRES_BROWSER_FILES,
        "postgres-living": _POSTGRES_LIVING_FILES,
        "postgres-4b-store": _POSTGRES_4B_STORE_FILES,
        "postgres-4b-current": _POSTGRES_4B_CURRENT_FILES,
        "postgres-4b-historical": _POSTGRES_4B_HISTORICAL_FILES,
    }


def _dsn_destination(
    name: str, environment: Mapping[str, str], *, prefix: str
) -> tuple[str, tuple[str, int, str]]:
    value = environment.get(name, "").strip()
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise RequiredTestsError(
            f"{name} must select an explicit disposable database"
        ) from exc
    database = unquote(parsed.path.removeprefix("/"))
    destination_overrides = {
        "database",
        "dbname",
        "host",
        "hostaddr",
        "port",
        "service",
        "servicefile",
        "user",
        "username",
    }
    try:
        query_pairs = parse_qsl(
            parsed.query, keep_blank_values=True, strict_parsing=True
        )
        query_keys = {key.casefold() for key, _value in query_pairs}
        port = parsed.port or 5432
    except ValueError as exc:
        raise RequiredTestsError(
            f"{name} must select an explicit disposable database"
        ) from exc
    socket_path = ""
    if (
        parsed.hostname is None
        and parsed.port is None
        and len(query_pairs) == 1
        and query_pairs[0][0].casefold() == "host"
    ):
        socket_path = query_pairs[0][1]
        socket = PurePosixPath(socket_path)
        if (
            not socket.is_absolute()
            or any(part in {"", ".", ".."} for part in socket.parts)
            or socket.as_posix() != socket_path
        ):
            raise RequiredTestsError(
                f"{name} must select an explicit disposable database"
            )
    # The sealed gate environment contains Psycopg 3 only.  SQLAlchemy treats
    # the unqualified ``postgresql://`` scheme as its legacy psycopg2 dialect,
    # so accepting it would defer a deterministic configuration error until a
    # database-backed test happens to create an engine.  Reject that ambiguity
    # during admission instead.
    if parsed.scheme != "postgresql+psycopg" or (
        parsed.hostname is not None and "," in parsed.hostname
    ):
        raise RequiredTestsError(f"{name} must select an explicit disposable database")
    if (
        (parsed.hostname is None and not socket_path)
        or (parsed.hostname is not None and bool(query_keys & destination_overrides))
        or (socket_path and query_keys != {"host"})
        or not database.startswith(prefix)
        or "/" in database
        or bool(parsed.fragment)
    ):
        raise RequiredTestsError(f"{name} must select an explicit disposable database")
    host = socket_path or parsed.hostname.casefold().rstrip(".")
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    return value, (host, port, database)


def _dsn(name: str, environment: Mapping[str, str], *, prefix: str) -> str:
    return _dsn_destination(name, environment, prefix=prefix)[0]


def _group_environment(
    base: Mapping[str, str], group: str, eva_root: Path
) -> dict[str, str]:
    result = {
        "HOME": base.get("HOME", ""),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.defpath)),
        "PIP_NO_INDEX": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join((str(eva_root / "src"), str(eva_root))),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TZ": "UTC",
    }
    names = _DSN_ENV.get(group, ())
    if group == "portable-full-schema":
        result.update(
            {
                "EVA2_TEST_POSTGRES_DSN": _dsn(
                    names[0], base, prefix="eva2_4b_disposable_"
                ),
                "EVA2_TEST_PORTABLE_SOURCE_DSN": _dsn(
                    names[1], base, prefix="eva2_4b_disposable_"
                ),
                "EVA2_TEST_PORTABLE_TARGET_DSN": _dsn(
                    names[2], base, prefix="eva2_4b_disposable_"
                ),
            }
        )
        if len(set(result[key] for key in result if key.startswith("EVA2_TEST_"))) != 3:
            raise RequiredTestsError("portable full-schema databases must be distinct")
    elif names:
        result["EVA2_TEST_POSTGRES_DSN"] = _dsn(
            names[0], base, prefix=_DSN_PREFIX.get(group, "eva2_4b_disposable_")
        )
    return result


def _validate_database_inputs(base: Mapping[str, str]) -> tuple[str, ...]:
    destinations: list[tuple[str, int, str]] = []
    names: list[str] = []
    for group, environment_names in _DSN_ENV.items():
        prefix = _DSN_PREFIX.get(group, "eva2_4b_disposable_")
        for name in environment_names:
            names.append(name)
            _value, destination = _dsn_destination(name, base, prefix=prefix)
            destinations.append(destination)
    if len(names) != 12 or len(set(destinations)) != len(destinations):
        raise RequiredTestsError("all twelve disposable databases must be distinct")
    return tuple(sorted(names))


def _browser_subgate_receipt(
    *,
    raw: bytes,
    returned: object,
    candidate_image_id: str,
    expected_candidate_source_commit: str,
    expected_gate_site_packages_sha256: str,
    expected_tests_sha256: str,
    expected_tools_sha256: str,
    expected_harness_sha256: str,
) -> tuple[dict[str, object], tuple[str, ...]]:
    try:
        receipt = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequiredTestsError("physical browser subgate receipt is invalid") from exc
    expected_junit = {"errors": 0, "failures": 0, "skipped": 0, "tests": 4}
    if (
        not isinstance(receipt, dict)
        or receipt != returned
        or receipt.get("contract") != BROWSER_SUBGATE_CONTRACT
        or receipt.get("schemaVersion") != 1
        or receipt.get("status") != "passed"
        or receipt.get("cleanup") != {"allNonceResourcesRemoved": True}
        or receipt.get("candidate")
        != {
            "imageId": candidate_image_id,
            "sourceCommit": expected_candidate_source_commit,
        }
    ):
        raise RequiredTestsError("physical browser subgate authority differs")
    execution = receipt.get("execution")
    inputs = receipt.get("inputs")
    isolation = receipt.get("isolation")
    if (
        not isinstance(execution, dict)
        or tuple(execution.get("collectedNodeids", ())) != BROWSER_NODEIDS
        or execution.get("junit") != expected_junit
        or execution.get("externalBrowserEgressBlocked") is not True
        or not isinstance(execution.get("junitSha256"), str)
        or _SHA256.fullmatch(str(execution["junitSha256"])) is None
        or not isinstance(inputs, dict)
        or inputs.get("gateSitePackagesSha256") != expected_gate_site_packages_sha256
        or inputs.get("testsTreeSha256") != expected_tests_sha256
        or inputs.get("toolsTreeSha256") != expected_tools_sha256
        or inputs.get("harnessSha256") != expected_harness_sha256
        or isolation
        != {
            "containerCount": 4,
            "controllerUsesCandidateInternalProductCode": True,
            "gatewaylessInternalNetworkCount": 2,
            "publishedPortCount": 0,
            "repositoryProductSourceMounted": False,
        }
    ):
        raise RequiredTestsError("physical browser subgate result differs")
    return receipt, BROWSER_NODEIDS


def _run(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: int
) -> Completed:
    try:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            umask=0o022,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RequiredTestsError("pytest process could not complete") from exc
    return Completed(result.returncode, result.stdout, result.stderr)


def _junit_counts(path: Path) -> dict[str, int]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RequiredTestsError("pytest JUnit output is missing or invalid") from exc
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise RequiredTestsError("pytest JUnit output contains no suite")
    result = {
        key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    if result["tests"] <= 0 or any(
        result[key] for key in ("failures", "errors", "skipped")
    ):
        raise RequiredTestsError("release test group was not completely green")
    return result


def run_required_tests(
    *,
    repository_root: Path,
    runtime_wheelhouse: Path,
    gate_wheelhouse: Path,
    candidate_image_id: str,
    expected_candidate_source_commit: str,
    gate_site_packages: Path,
    expected_gate_site_packages_sha256: str,
    expected_tests_sha256: str,
    expected_tools_sha256: str,
    expected_browser_harness_sha256: str,
    receipt_path: Path,
    environment: Mapping[str, str] = os.environ,
    environment_verifier: Callable[[Path], dict[str, object]] = execution_environment,
    runtime_validator: Callable[
        [Path, Path], dict[str, object]
    ] = _validate_runtime_wheelhouse,
    gate_validator: Callable[
        [Path, Path], dict[str, object]
    ] = validate_gate_wheelhouse,
    command_runner: Callable[..., Completed] = _run,
    browser_runner: Callable[..., dict[str, object]] = run_browser_subgate,
) -> dict[str, object]:
    if (
        _IMAGE_ID.fullmatch(candidate_image_id) is None
        or _COMMIT.fullmatch(expected_candidate_source_commit) is None
        or not gate_site_packages.is_absolute()
        or any(
            _SHA256.fullmatch(value) is None
            for value in (
                expected_gate_site_packages_sha256,
                expected_tests_sha256,
                expected_tools_sha256,
                expected_browser_harness_sha256,
            )
        )
    ):
        raise RequiredTestsError("physical browser authority argument is invalid")
    repository_root = repository_root.resolve(strict=True)
    eva_root = repository_root / "eva2"
    inputs_before = load_test_inputs(eva_root)
    runtime_receipt = runtime_validator(eva_root, runtime_wheelhouse)
    gate_receipt = gate_validator(eva_root, gate_wheelhouse)
    toolchain = environment_verifier(repository_root)
    database_environment_names = _validate_database_inputs(environment)
    grouped = _groups(inputs_before.executable)
    group_receipts: list[dict[str, object]] = []
    collected_all: set[str] = set()
    receipt_path.parent.resolve(strict=True)
    with tempfile.TemporaryDirectory(
        prefix="eva2-required-tests-", dir=receipt_path.parent
    ) as temporary:
        temporary_root = Path(temporary)
        for group, paths in grouped.items():
            batches = (
                _postgres_subgroups(paths)
                if group == "postgres"
                else {group: tuple(paths)}
            )
            group_nodeids: list[str] = []
            group_modules: set[str] = set()
            subgroup_receipts: list[dict[str, object]] = []
            for subgroup, batch_paths in batches.items():
                if subgroup == "postgres-browser-physical":
                    physical_receipt_path = temporary_root / "browser-subgate.json"
                    try:
                        returned = browser_runner(
                            repository_root=eva_root,
                            candidate_image_id=candidate_image_id,
                            expected_candidate_source_commit=(
                                expected_candidate_source_commit
                            ),
                            gate_site_packages=gate_site_packages,
                            expected_gate_site_packages_sha256=(
                                expected_gate_site_packages_sha256
                            ),
                            expected_tests_sha256=expected_tests_sha256,
                            expected_tools_sha256=expected_tools_sha256,
                            expected_harness_sha256=(expected_browser_harness_sha256),
                            receipt_path=physical_receipt_path,
                        )
                    except BrowserSubgateError as exc:
                        raise RequiredTestsError(
                            "physical browser subgate failed"
                        ) from exc
                    try:
                        physical_raw = physical_receipt_path.read_bytes()
                    except OSError as exc:
                        raise RequiredTestsError(
                            "physical browser subgate wrote no receipt"
                        ) from exc
                    physical, nodeids = _browser_subgate_receipt(
                        raw=physical_raw,
                        returned=returned,
                        candidate_image_id=candidate_image_id,
                        expected_candidate_source_commit=(
                            expected_candidate_source_commit
                        ),
                        expected_gate_site_packages_sha256=(
                            expected_gate_site_packages_sha256
                        ),
                        expected_tests_sha256=expected_tests_sha256,
                        expected_tools_sha256=expected_tools_sha256,
                        expected_harness_sha256=expected_browser_harness_sha256,
                    )
                    modules = {nodeid.split("::", 1)[0] for nodeid in nodeids}
                    if (
                        modules != set(batch_paths)
                        or group_modules & modules
                        or collected_all & set(nodeids)
                    ):
                        raise RequiredTestsError(
                            "physical browser subgate collection differs"
                        )
                    group_modules.update(modules)
                    group_nodeids.extend(nodeids)
                    collected_all.update(nodeids)
                    execution = physical["execution"]
                    assert isinstance(execution, dict)
                    counts = execution["junit"]
                    assert isinstance(counts, dict)
                    subgroup_receipts.append(
                        {
                            "collectedCount": len(nodeids),
                            "collectionSha256": _sha256(_canonical(nodeids)),
                            "executableModuleCount": len(batch_paths),
                            "junitSha256": execution["junitSha256"],
                            "name": subgroup,
                            "physicalReceipt": physical,
                            "physicalReceiptSha256": _sha256(physical_raw),
                            "result": counts,
                            "stderrSha256": execution["stderrSha256"],
                            "stdoutSha256": execution["stdoutSha256"],
                        }
                    )
                    continue
                env = _group_environment(environment, subgroup, eva_root)
                base = [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "--capture=sys",
                    "-p",
                    "no:cacheprovider",
                    "-o",
                    "xfail_strict=true",
                ]
                collection = command_runner(
                    [*base, "--collect-only", *batch_paths],
                    cwd=eva_root,
                    env=env,
                    timeout=600,
                )
                if collection.returncode != 0 or collection.stderr:
                    raise RequiredTestsError(f"{subgroup} collection failed")
                nodeids = tuple(
                    line.strip()
                    for line in collection.stdout.splitlines()
                    if "::" in line and not line.startswith(("=", " "))
                )
                if not nodeids or len(nodeids) != len(set(nodeids)):
                    raise RequiredTestsError(
                        f"{subgroup} collection is empty or duplicated"
                    )
                modules = {nodeid.split("::", 1)[0] for nodeid in nodeids}
                if (
                    modules != set(batch_paths)
                    or group_modules & modules
                    or collected_all & set(nodeids)
                ):
                    raise RequiredTestsError(
                        f"{subgroup} collection differs from authority"
                    )
                group_modules.update(modules)
                group_nodeids.extend(nodeids)
                collected_all.update(nodeids)
                junit = temporary_root / f"{subgroup}.xml"
                execution = command_runner(
                    [
                        *base,
                        f"--basetemp={temporary_root / (subgroup + '-tmp')}",
                        f"--junitxml={junit}",
                        *batch_paths,
                    ],
                    cwd=eva_root,
                    env=env,
                    timeout=3600,
                )
                if execution.returncode != 0:
                    raise RequiredTestsError(f"{subgroup} execution failed")
                counts = _junit_counts(junit)
                if counts["tests"] != len(nodeids):
                    raise RequiredTestsError(
                        f"{subgroup} result count differs from collection"
                    )
                subgroup_receipts.append(
                    {
                        "collectedCount": len(nodeids),
                        "collectionSha256": _sha256(_canonical(nodeids)),
                        "executableModuleCount": len(batch_paths),
                        "junitSha256": _sha256(junit.read_bytes()),
                        "name": subgroup,
                        "result": counts,
                        "stderrSha256": _sha256(execution.stderr.encode()),
                        "stdoutSha256": _sha256(execution.stdout.encode()),
                    }
                )
            if group_modules != set(paths):
                raise RequiredTestsError(f"{group} subgroup collection differs")
            aggregate = {
                key: sum(
                    int(subgroup["result"][key])  # type: ignore[index]
                    for subgroup in subgroup_receipts
                )
                for key in ("tests", "failures", "errors", "skipped")
            }
            group_receipt: dict[str, object] = {
                "collectedCount": len(group_nodeids),
                "collectionSha256": _sha256(_canonical(tuple(group_nodeids))),
                "executableModuleCount": len(paths),
                "group": group,
                "junitSha256": _sha256(
                    _canonical(
                        tuple(subgroup["junitSha256"] for subgroup in subgroup_receipts)
                    )
                ),
                "result": aggregate,
                "stderrSha256": _sha256(
                    _canonical(
                        tuple(
                            subgroup["stderrSha256"] for subgroup in subgroup_receipts
                        )
                    )
                ),
                "stdoutSha256": _sha256(
                    _canonical(
                        tuple(
                            subgroup["stdoutSha256"] for subgroup in subgroup_receipts
                        )
                    )
                ),
            }
            if group == "postgres":
                group_receipt["subgroups"] = subgroup_receipts
            group_receipts.append(group_receipt)
    inputs_after = load_test_inputs(eva_root)
    if inputs_after != inputs_before:
        raise RequiredTestsError("release test inputs changed during execution")
    receipt: dict[str, object] = {
        "authority": {
            "executableModuleCount": len(inputs_before.executable),
            "requiredTestSupportInputCount": len(inputs_before.support),
            "sha256": inputs_before.authority_sha256,
            "snapshotSha256": inputs_before.snapshot_sha256,
        },
        "contract": RECEIPT_CONTRACT,
        "candidate": {
            "imageId": candidate_image_id,
            "scope": ["postgres-browser-physical"],
            "sourceCommit": expected_candidate_source_commit,
        },
        "groups": group_receipts,
        "networkPolicy": {
            "externalNetworkAllowedDuringGateExecution": False,
            "isolationEnforcementClaim": False,
            "loopbackAllowedDuringGateExecution": True,
        },
        "postgres": {
            "databaseEnvironmentVariables": list(database_environment_names),
            "disposableDatabasesRequired": 13,
            "physicalBrowserSubgateDatabaseCount": 1,
            "image": "docker.io/library/postgres@sha256:472efd9a66f2b2f1a5aeb18b28de74332e6ef88c2b93a1a5d812fb6db67a5f60",
            "serverVersionNum": 160013,
        },
        "status": "passed",
        "nonclaims": [
            "Only the postgres-browser-physical subgroup starts and inspects the candidate image; every other subgroup tests the sealed source and wheelhouse inputs.",
            "The sourceCommit field is physically verified by the browser subgroup and is not a claim that every test executes inside the candidate image.",
        ],
        "toolchain": toolchain,
        "wheelhouses": {"gate": gate_receipt, "runtime": runtime_receipt},
    }
    try:
        write_new_private_json(
            receipt_path, receipt, label="managed B required-tests receipt"
        )
    except PrivateReceiptError as error:
        raise RequiredTestsError(str(error)) from error
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--runtime-wheelhouse", type=Path, required=True)
    parser.add_argument("--gate-wheelhouse", type=Path, required=True)
    parser.add_argument("--candidate-image-id", required=True)
    parser.add_argument("--expected-candidate-source-commit", required=True)
    parser.add_argument("--gate-site-packages", type=Path, required=True)
    parser.add_argument("--expected-gate-site-packages-sha256", required=True)
    parser.add_argument("--expected-tests-sha256", required=True)
    parser.add_argument("--expected-tools-sha256", required=True)
    parser.add_argument("--expected-browser-harness-sha256", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        run_required_tests(
            repository_root=arguments.repository_root,
            runtime_wheelhouse=arguments.runtime_wheelhouse,
            gate_wheelhouse=arguments.gate_wheelhouse,
            candidate_image_id=arguments.candidate_image_id,
            expected_candidate_source_commit=(
                arguments.expected_candidate_source_commit
            ),
            gate_site_packages=arguments.gate_site_packages,
            expected_gate_site_packages_sha256=(
                arguments.expected_gate_site_packages_sha256
            ),
            expected_tests_sha256=arguments.expected_tests_sha256,
            expected_tools_sha256=arguments.expected_tools_sha256,
            expected_browser_harness_sha256=(arguments.expected_browser_harness_sha256),
            receipt_path=arguments.receipt,
        )
    except RequiredTestsError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {"status": "passed", "receipt": str(arguments.receipt)}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
