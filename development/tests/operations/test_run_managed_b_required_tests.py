from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

from tools import run_managed_b_required_tests as runner


ROOT = Path(__file__).resolve().parents[3]
NESTED_ROOT = ROOT.parent / "eva2"
REPOSITORY_ROOT = (
    ROOT.parent
    if NESTED_ROOT.is_dir() and NESTED_ROOT.resolve() == ROOT.resolve()
    else ROOT
)
_CANDIDATE = "sha256:" + "a" * 64
_COMMIT = "b" * 40
_GATE_HASH = "c" * 64
_TESTS_HASH = "d" * 64
_TOOLS_HASH = "e" * 64
_HARNESS_HASH = "f" * 64


def _browser_arguments(path: Path) -> dict[str, object]:
    return {
        "candidate_image_id": _CANDIDATE,
        "expected_candidate_source_commit": _COMMIT,
        "gate_site_packages": path,
        "expected_gate_site_packages_sha256": _GATE_HASH,
        "expected_tests_sha256": _TESTS_HASH,
        "expected_tools_sha256": _TOOLS_HASH,
        "expected_browser_harness_sha256": _HARNESS_HASH,
    }


def _browser_runner(**arguments: object) -> dict[str, object]:
    receipt = {
        "candidate": {"imageId": _CANDIDATE, "sourceCommit": _COMMIT},
        "cleanup": {"allNonceResourcesRemoved": True},
        "contract": runner.BROWSER_SUBGATE_CONTRACT,
        "execution": {
            "collectedNodeids": list(runner.BROWSER_NODEIDS),
            "externalBrowserEgressBlocked": True,
            "junit": {"errors": 0, "failures": 0, "skipped": 0, "tests": 4},
            "junitSha256": "1" * 64,
            "stderrSha256": "2" * 64,
            "stdoutSha256": "3" * 64,
        },
        "inputs": {
            "gateSitePackagesSha256": _GATE_HASH,
            "harnessSha256": _HARNESS_HASH,
            "testsTreeSha256": _TESTS_HASH,
            "toolsTreeSha256": _TOOLS_HASH,
        },
        "isolation": {
            "containerCount": 4,
            "controllerUsesCandidateInternalProductCode": True,
            "gatewaylessInternalNetworkCount": 2,
            "publishedPortCount": 0,
            "repositoryProductSourceMounted": False,
        },
        "schemaVersion": 1,
        "status": "passed",
    }
    receipt_path = arguments["receipt_path"]
    assert isinstance(receipt_path, Path)
    receipt_path.write_text(
        json.dumps(receipt, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    values = {
        "EVA2_GATE_PORTABLE_EFFECT_DSN": "postgresql+psycopg://test/eva2_4b_disposable_effect",
        "EVA2_GATE_PORTABLE_MODEL_DSN": "postgresql+psycopg://test/eva2_4b_disposable_model",
        "EVA2_GATE_PORTABLE_OBJECT_DSN": "postgresql+psycopg://test/eva2_4b_disposable_object",
        "EVA2_GATE_PORTABLE_READ_DSN": "postgresql+psycopg://test/eva2_4b_disposable_read",
        "EVA2_GATE_PORTABLE_FULL_DSN": "postgresql+psycopg://test/eva2_4b_disposable_full",
        "EVA2_GATE_PORTABLE_SOURCE_DSN": "postgresql+psycopg://test/eva2_4b_disposable_source",
        "EVA2_GATE_PORTABLE_TARGET_DSN": "postgresql+psycopg://test/eva2_4b_disposable_target",
        "EVA2_GATE_POSTGRES_ACTION_CHAIN_DSN": (
            "postgresql+psycopg://test/eva2_action_chain_disposable_gate"
        ),
        "EVA2_GATE_POSTGRES_LIVING_DSN": (
            "postgresql+psycopg://test/eva2_living_disposable_gate"
        ),
        "EVA2_GATE_POSTGRES_4B_STORE_DSN": (
            "postgresql+psycopg://test/eva2_4b_disposable_store"
        ),
        "EVA2_GATE_POSTGRES_4B_CURRENT_DSN": (
            "postgresql+psycopg://test/eva2_4b_disposable_current"
        ),
        "EVA2_GATE_POSTGRES_4B_HISTORICAL_DSN": (
            "postgresql+psycopg://test/eva2_4b_disposable_historical"
        ),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return dict(values)


def test_authority_distinguishes_executable_tests_from_bound_support() -> None:
    inputs = runner.load_test_inputs(ROOT)
    authority = json.loads(
        (ROOT / "deploy/release/managed-b-release-input-authority-v1.json").read_bytes()
    )
    required_inputs = {row["path"] for row in authority["requiredInputs"]}

    assert len(inputs.executable) == 162
    assert "development/tests/application/test_free_will.py" in inputs.executable
    assert "development/tests/postgres/test_sensory_admission.py" in inputs.executable
    assert "development/tests/operations/test_two_builder_reproducibility.py" in inputs.executable
    assert len(inputs.support) == 20
    assert all(Path(path).name.startswith("test_") for path in inputs.executable)
    assert (
        "development/tests/experiments/test_managed_b_local_candidate_image.py" in inputs.executable
    )
    assert not any(Path(path).name.startswith("test_") for path in inputs.support)
    assert {
        "development/tests/conftest.py",
        "development/tests/contract/conftest.py",
        "development/tests/postgres/conftest.py",
    }.issubset(inputs.support)
    assert {
        "development/design/vertical/noor-4d-concurrency-crash-schedules-v1.json",
        "experiments/playwright-mcp-action-v1/fixture_server.py",
        "development/tests/fixtures/browser/playwright_mcp_worker.py",
        "development/tests/fixtures/browser/worker-profile.json",
        "development/tests/operations/test_run_managed_b_browser_release_subgate.py",
        "development/tests/operations/test_run_managed_b_required_tests.py",
        "src/tools/run_managed_b_browser_release_subgate.py",
        "src/tools/run_managed_b_required_tests.py",
    }.issubset(required_inputs)


def test_runner_accepts_flat_candidate_source_root(
    private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flat_root = private_tmp_path / "candidate"
    (flat_root / "deploy/release").mkdir(parents=True)
    (flat_root / "deploy/release/managed-b-release-input-authority-v1.json").write_text(
        "{}", encoding="utf-8"
    )
    monkeypatch.setattr(runner, "load_test_inputs", lambda root: root)
    captured: dict[str, Path] = {}

    def runtime_validator(root: Path, _wheelhouse: Path) -> dict[str, object]:
        captured["root"] = root
        raise RuntimeError("stop after source-root resolution")

    with pytest.raises(RuntimeError, match="source-root resolution"):
        runner.run_required_tests(
            repository_root=flat_root,
            runtime_wheelhouse=private_tmp_path,
            gate_wheelhouse=private_tmp_path,
            **_browser_arguments(private_tmp_path),
            receipt_path=private_tmp_path / "receipt.json",
            environment=_environment(monkeypatch),
            runtime_validator=runtime_validator,
        )

    assert captured["root"] == flat_root


def test_missing_physical_gate_wheelhouse_fails_closed(tmp_path: Path) -> None:
    absent = (tmp_path / "absent").absolute()
    with pytest.raises(runner.RequiredTestsError, match="inaccessible"):
        runner.validate_gate_wheelhouse(ROOT, absent)


def test_cli_requires_all_external_browser_authorities() -> None:
    with pytest.raises(SystemExit):
        runner._parser().parse_args(
            [
                "--repository-root",
                str(REPOSITORY_ROOT),
                "--runtime-wheelhouse",
                "/runtime",
                "--gate-wheelhouse",
                "/gate",
                "--receipt",
                "/receipt.json",
            ]
        )


def test_real_command_runner_enforces_a_stable_non_group_writable_umask(
    private_tmp_path: Path,
) -> None:
    result = runner._run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "Path('created').mkdir(); "
                "print(oct(Path('created').stat().st_mode & 0o777))"
            ),
        ],
        cwd=private_tmp_path,
        env=os.environ,
        timeout=30,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.strip() == "0o755"


def test_gate_lock_binds_yaml_and_native_ha_http_test_dependencies() -> None:
    rows = runner._lock_rows(
        (ROOT / "deploy/release/gate-toolchain.requirements.lock").read_bytes()
    )

    assert len(rows) == 27
    assert rows["aiohttp"][0] == "3.14.3"
    assert {"aiohappyeyeballs", "aiosignal", "frozenlist", "multidict", "propcache", "yarl"} <= rows.keys()
    assert rows["pyyaml"] == (
        "6.0.3",
        frozenset({"ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc"}),
    )
    assert (
        "pyyaml=="
        not in (ROOT / "deploy/requirements-4d.lock").read_text(encoding="utf-8").lower()
    )


def test_wheel_identity_ignores_vendored_nested_metadata() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        archive.writestr(
            "pip-26.0.1.dist-info/METADATA", "Name: pip\nVersion: 26.0.1\n"
        )
        archive.writestr(
            "pip/_vendor/example-1.0.dist-info/METADATA",
            "Name: example\nVersion: 1.0\n",
        )

    assert runner._wheel_identity(buffer.getvalue()) == ("pip", "26.0.1")


def test_disposable_database_name_cannot_be_spoofed_in_query() -> None:
    with pytest.raises(runner.RequiredTestsError, match="explicit disposable"):
        runner._dsn(
            "EVA2_GATE_POSTGRES_DSN",
            {
                "EVA2_GATE_POSTGRES_DSN": (
                    "postgresql://test/production?note=eva2_4b_disposable_postgres"
                )
            },
            prefix="eva2_4b_disposable_",
        )


def test_local_psycopg_socket_is_an_explicit_database_destination() -> None:
    value, destination = runner._dsn_destination(
        "EVA2_GATE_POSTGRES_DSN",
        {
            "EVA2_GATE_POSTGRES_DSN": (
                "postgresql+psycopg://gate@/eva2_4b_disposable_gate"
                "?host=%2Ftmp%2Feva2-gate-socket"
            )
        },
        prefix="eva2_4b_disposable_",
    )

    assert value.endswith("?host=%2Ftmp%2Feva2-gate-socket")
    assert destination == ("/tmp/eva2-gate-socket", 5432, "eva2_4b_disposable_gate")


def test_unqualified_postgresql_scheme_is_rejected_before_execution() -> None:
    with pytest.raises(runner.RequiredTestsError, match="explicit disposable"):
        runner._dsn_destination(
            "EVA2_GATE_POSTGRES_DSN",
            {
                "EVA2_GATE_POSTGRES_DSN": (
                    "postgresql://gate@/eva2_4b_disposable_gate"
                    "?host=%2Ftmp%2Feva2-gate-socket"
                )
            },
            prefix="eva2_4b_disposable_",
        )


@pytest.mark.parametrize(
    "suffix",
    (
        "?host=other-postgres",
        "?dbname=other_database",
        "?service=other-cluster",
        "#other-destination",
    ),
)
def test_database_destination_override_is_rejected(suffix: str) -> None:
    with pytest.raises(runner.RequiredTestsError, match="explicit disposable"):
        runner._dsn_destination(
            "EVA2_GATE_POSTGRES_4B_CURRENT_DSN",
            {
                "EVA2_GATE_POSTGRES_4B_CURRENT_DSN": (
                    "postgresql://test/eva2_4b_disposable_gate" + suffix
                )
            },
            prefix="eva2_4b_disposable_",
        )


def test_postgres_modules_have_six_exact_nonoverlapping_subgroups() -> None:
    groups = runner._groups(runner.load_test_inputs(ROOT).executable)
    paths = groups["postgres"]

    subgroups = runner._postgres_subgroups(paths)

    assert {name: len(modules) for name, modules in subgroups.items()} == {
        "postgres-action-chain": 13,
        "postgres-browser-physical": 1,
        "postgres-living": 2,
        "postgres-4b-store": 5,
        "postgres-4b-current": 2,
        "postgres-4b-historical": 1,
    }
    assert subgroups["postgres-4b-store"] == (
        "development/tests/postgres/test_delegated_work_store.py",
        "development/tests/postgres/test_delegated_work_migration.py",
        "development/tests/postgres/test_delegated_work_private_context.py",
        "development/tests/postgres/test_memory_repository.py",
        "development/tests/postgres/test_people_memory_overview.py",
    )
    assert subgroups["postgres-4b-current"] == (
        "development/tests/postgres/test_postgres_migrations.py",
        "development/tests/postgres/test_sensory_admission.py",
    )
    assert set().union(*(set(modules) for modules in subgroups.values())) == set(paths)
    assert runner._POSTGRES_PURE_FILES.issubset(groups["pure"])
    with pytest.raises(runner.RequiredTestsError, match="classification differs"):
        runner._postgres_subgroups(paths[:-1])


def test_all_twelve_physical_databases_must_be_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _environment(monkeypatch)
    environment["EVA2_GATE_POSTGRES_4B_HISTORICAL_DSN"] = (
        "postgresql+psycopg://another-user@TEST.:5432/"
        "eva2_4b_disposable_effect?sslmode=disable"
    )

    with pytest.raises(runner.RequiredTestsError, match="twelve.*distinct"):
        runner._validate_database_inputs(environment)


def test_runner_executes_seven_closed_groups_and_redacts_dsns(
    private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _environment(monkeypatch)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def command(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int
    ) -> runner.Completed:
        del cwd, timeout
        calls.append((list(argv), dict(env)))
        paths = [item for item in argv if item.startswith(("development/tests/", "experiments/"))]
        if "--collect-only" in argv:
            return runner.Completed(
                0, "".join(f"{path}::test_bound\n" for path in paths), ""
            )
        junit_arg = next(item for item in argv if item.startswith("--junitxml="))
        junit = Path(junit_arg.split("=", 1)[1])
        suite = ET.Element(
            "testsuite",
            tests=str(len(paths)),
            failures="0",
            errors="0",
            skipped="0",
        )
        ET.ElementTree(suite).write(junit, encoding="utf-8", xml_declaration=True)
        return runner.Completed(0, "green\n", "")

    receipt_path = private_tmp_path / "receipt.json"
    receipt = runner.run_required_tests(
        repository_root=REPOSITORY_ROOT,
        runtime_wheelhouse=private_tmp_path,
        gate_wheelhouse=private_tmp_path,
        **_browser_arguments(private_tmp_path),
        receipt_path=receipt_path,
        environment=environment,
        environment_verifier=lambda _root: {"distributionCount": 54},
        runtime_validator=lambda _root, _wheels: {"packageCount": 38},
        gate_validator=lambda _root, _wheels: {"packageCount": 19},
        command_runner=command,
        browser_runner=_browser_runner,
    )

    assert receipt["status"] == "passed"
    assert receipt["candidate"] == {
        "imageId": _CANDIDATE,
        "scope": ["postgres-browser-physical"],
        "sourceCommit": _COMMIT,
    }
    assert "Only the postgres-browser-physical subgroup" in receipt["nonclaims"][0]
    assert receipt["networkPolicy"] == {
        "externalNetworkAllowedDuringGateExecution": False,
        "isolationEnforcementClaim": False,
        "loopbackAllowedDuringGateExecution": True,
    }
    assert len(receipt["groups"]) == 7
    assert len(calls) == 22
    assert sum(group["executableModuleCount"] for group in receipt["groups"]) == 162
    postgres = next(
        group for group in receipt["groups"] if group["group"] == "postgres"
    )
    assert [subgroup["name"] for subgroup in postgres["subgroups"]] == [
        "postgres-action-chain",
        "postgres-browser-physical",
        "postgres-living",
        "postgres-4b-store",
        "postgres-4b-current",
        "postgres-4b-historical",
    ]
    assert postgres["executableModuleCount"] == 24
    physical = next(
        subgroup
        for subgroup in postgres["subgroups"]
        if subgroup["name"] == "postgres-browser-physical"
    )
    assert physical["physicalReceipt"]["candidate"] == {
        "imageId": receipt["candidate"]["imageId"],
        "sourceCommit": receipt["candidate"]["sourceCommit"],
    }
    assert physical["physicalReceipt"]["cleanup"] == {"allNonceResourcesRemoved": True}
    assert receipt["postgres"]["disposableDatabasesRequired"] == 13
    assert receipt["postgres"]["physicalBrowserSubgateDatabaseCount"] == 1
    assert set(receipt["postgres"]["databaseEnvironmentVariables"]) == set(environment)
    postgres_environments = [
        env
        for argv, env in calls
        if "EVA2_TEST_POSTGRES_DSN" in env
        and any(path.startswith("development/tests/postgres/") for path in argv)
    ]
    assert {
        env["EVA2_TEST_POSTGRES_DSN"].split("/", 3)[-1] for env in postgres_environments
    } == {
        "eva2_action_chain_disposable_gate",
        "eva2_living_disposable_gate",
        "eva2_4b_disposable_store",
        "eva2_4b_disposable_current",
        "eva2_4b_disposable_historical",
    }
    assert all(
        not any(path in argv for path in runner.load_test_inputs(ROOT).support)
        for argv, _env in calls
    )
    serialized = receipt_path.read_text(encoding="utf-8")
    assert "postgresql://" not in serialized
    assert not any(value in serialized for value in environment.values())


def test_runner_writes_no_receipt_when_a_group_skips(
    private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _environment(monkeypatch)

    def command(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int
    ) -> runner.Completed:
        del cwd, env, timeout
        paths = [item for item in argv if item.startswith(("development/tests/", "experiments/"))]
        if "--collect-only" in argv:
            return runner.Completed(
                0, "".join(f"{path}::test_bound\n" for path in paths), ""
            )
        junit = Path(
            next(item for item in argv if item.startswith("--junitxml=")).split("=", 1)[
                1
            ]
        )
        suite = ET.Element(
            "testsuite",
            tests=str(len(paths)),
            failures="0",
            errors="0",
            skipped="1",
        )
        ET.ElementTree(suite).write(junit, encoding="utf-8", xml_declaration=True)
        return runner.Completed(0, "", "")

    receipt_path = private_tmp_path / "must-not-exist.json"
    with pytest.raises(runner.RequiredTestsError, match="not completely green"):
        runner.run_required_tests(
            repository_root=REPOSITORY_ROOT,
            runtime_wheelhouse=private_tmp_path,
            gate_wheelhouse=private_tmp_path,
            **_browser_arguments(private_tmp_path),
            receipt_path=receipt_path,
            environment=environment,
            environment_verifier=lambda _root: {},
            runtime_validator=lambda _root, _wheels: {},
            gate_validator=lambda _root, _wheels: {},
            command_runner=command,
            browser_runner=_browser_runner,
        )
    assert not receipt_path.exists()


def test_runner_rejects_result_count_that_differs_from_collection(
    private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _environment(monkeypatch)

    def command(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int
    ) -> runner.Completed:
        del cwd, env, timeout
        paths = [item for item in argv if item.startswith(("development/tests/", "experiments/"))]
        if "--collect-only" in argv:
            return runner.Completed(
                0, "".join(f"{path}::test_bound\n" for path in paths), ""
            )
        junit = Path(
            next(item for item in argv if item.startswith("--junitxml=")).split("=", 1)[
                1
            ]
        )
        suite = ET.Element(
            "testsuite",
            tests=str(len(paths) + 1),
            failures="0",
            errors="0",
            skipped="0",
        )
        ET.ElementTree(suite).write(junit, encoding="utf-8", xml_declaration=True)
        return runner.Completed(0, "", "")

    receipt_path = private_tmp_path / "count-mismatch-must-not-exist.json"
    with pytest.raises(runner.RequiredTestsError, match="result count differs"):
        runner.run_required_tests(
            repository_root=REPOSITORY_ROOT,
            runtime_wheelhouse=private_tmp_path,
            gate_wheelhouse=private_tmp_path,
            **_browser_arguments(private_tmp_path),
            receipt_path=receipt_path,
            environment=environment,
            environment_verifier=lambda _root: {},
            runtime_validator=lambda _root, _wheels: {},
            gate_validator=lambda _root, _wheels: {},
            command_runner=command,
            browser_runner=_browser_runner,
        )
    assert not receipt_path.exists()


def test_physical_browser_receipt_cannot_claim_cleanup_without_proof(
    tmp_path: Path,
) -> None:
    receipt = _browser_runner(receipt_path=tmp_path / "browser.json")
    receipt["cleanup"] = {"allNonceResourcesRemoved": False}
    raw = (json.dumps(receipt, separators=(",", ":"), sort_keys=True) + "\n").encode()

    with pytest.raises(
        runner.RequiredTestsError, match="physical browser subgate authority differs"
    ):
        runner._browser_subgate_receipt(
            raw=raw,
            returned=receipt,
            candidate_image_id=_CANDIDATE,
            expected_candidate_source_commit=_COMMIT,
            expected_gate_site_packages_sha256=_GATE_HASH,
            expected_tests_sha256=_TESTS_HASH,
            expected_tools_sha256=_TOOLS_HASH,
            expected_harness_sha256=_HARNESS_HASH,
        )
