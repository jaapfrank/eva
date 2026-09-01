from __future__ import annotations

import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import pytest

from tools import run_managed_b_required_tests as runner


ROOT = Path(__file__).resolve().parents[2]
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
        "EVA2_GATE_PORTABLE_EFFECT_DSN": "postgresql://test/eva2_4b_disposable_effect",
        "EVA2_GATE_PORTABLE_MODEL_DSN": "postgresql://test/eva2_4b_disposable_model",
        "EVA2_GATE_PORTABLE_OBJECT_DSN": "postgresql://test/eva2_4b_disposable_object",
        "EVA2_GATE_PORTABLE_READ_DSN": "postgresql://test/eva2_4b_disposable_read",
        "EVA2_GATE_PORTABLE_FULL_DSN": "postgresql://test/eva2_4b_disposable_full",
        "EVA2_GATE_PORTABLE_SOURCE_DSN": "postgresql://test/eva2_4b_disposable_source",
        "EVA2_GATE_PORTABLE_TARGET_DSN": "postgresql://test/eva2_4b_disposable_target",
        "EVA2_GATE_POSTGRES_ACTION_CHAIN_DSN": (
            "postgresql://test/eva2_action_chain_disposable_gate"
        ),
        "EVA2_GATE_POSTGRES_LIVING_DSN": (
            "postgresql://test/eva2_living_disposable_gate"
        ),
        "EVA2_GATE_POSTGRES_4B_STORE_DSN": (
            "postgresql://test/eva2_4b_disposable_store"
        ),
        "EVA2_GATE_POSTGRES_4B_CURRENT_DSN": (
            "postgresql://test/eva2_4b_disposable_current"
        ),
        "EVA2_GATE_POSTGRES_4B_HISTORICAL_DSN": (
            "postgresql://test/eva2_4b_disposable_historical"
        ),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return dict(values)


def test_authority_distinguishes_executable_tests_from_bound_support() -> None:
    inputs = runner.load_test_inputs(ROOT)
    authority = json.loads(
        (ROOT / "release/managed-b-release-input-authority-v1.json").read_bytes()
    )
    required_inputs = {row["path"] for row in authority["requiredInputs"]}

    assert len(inputs.executable) == 146
    assert len(inputs.support) == 19
    assert all(Path(path).name.startswith("test_") for path in inputs.executable)
    assert (
        "tests/experiments/test_managed_b_local_candidate_image.py" in inputs.executable
    )
    assert not any(Path(path).name.startswith("test_") for path in inputs.support)
    assert {
        "tests/conftest.py",
        "tests/contract/conftest.py",
        "tests/postgres/conftest.py",
    }.issubset(inputs.support)
    assert {
        "design/vertical/noor-4d-concurrency-crash-schedules-v1.json",
        "experiments/playwright-mcp-action-v1/fixture_server.py",
        "tests/fixtures/browser/playwright_mcp_worker.py",
        "tests/fixtures/browser/worker-profile.json",
        "tests/operations/test_run_managed_b_browser_release_subgate.py",
        "tests/operations/test_run_managed_b_required_tests.py",
        "tools/run_managed_b_browser_release_subgate.py",
        "tools/run_managed_b_required_tests.py",
    }.issubset(required_inputs)


def test_missing_physical_gate_wheelhouse_fails_closed(tmp_path: Path) -> None:
    absent = (tmp_path / "absent").absolute()
    with pytest.raises(runner.RequiredTestsError, match="inaccessible"):
        runner.validate_gate_wheelhouse(ROOT, absent)


def test_cli_requires_all_external_browser_authorities() -> None:
    with pytest.raises(SystemExit):
        runner._parser().parse_args(
            [
                "--repository-root",
                str(ROOT.parent),
                "--runtime-wheelhouse",
                "/runtime",
                "--gate-wheelhouse",
                "/gate",
                "--receipt",
                "/receipt.json",
            ]
        )


def test_gate_lock_is_exact_and_adds_pyyaml_only_to_gate_toolchain() -> None:
    rows = runner._lock_rows(
        (ROOT / "release/gate-toolchain.requirements.lock").read_bytes()
    )

    assert len(rows) == 19
    assert rows["pyyaml"] == (
        "6.0.3",
        frozenset({"ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc"}),
    )
    assert (
        "pyyaml=="
        not in (ROOT / "requirements-4d.lock").read_text(encoding="utf-8").lower()
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
        "postgres-4b-current": 1,
        "postgres-4b-historical": 1,
    }
    assert subgroups["postgres-4b-store"] == (
        "tests/postgres/test_delegated_work_store.py",
        "tests/postgres/test_delegated_work_migration.py",
        "tests/postgres/test_delegated_work_private_context.py",
        "tests/postgres/test_memory_repository.py",
        "tests/postgres/test_people_memory_overview.py",
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
        paths = [item for item in argv if item.startswith(("tests/", "experiments/"))]
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
        repository_root=ROOT.parent,
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
    assert receipt["networkPolicy"] == {
        "externalNetworkAllowedDuringGateExecution": False,
        "isolationEnforcementClaim": False,
        "loopbackAllowedDuringGateExecution": True,
    }
    assert len(receipt["groups"]) == 7
    assert len(calls) == 22
    assert sum(group["executableModuleCount"] for group in receipt["groups"]) == 146
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
    assert postgres["executableModuleCount"] == 23
    assert receipt["postgres"]["disposableDatabasesRequired"] == 13
    assert receipt["postgres"]["physicalBrowserSubgateDatabaseCount"] == 1
    assert set(receipt["postgres"]["databaseEnvironmentVariables"]) == set(environment)
    postgres_environments = [
        env
        for argv, env in calls
        if "EVA2_TEST_POSTGRES_DSN" in env
        and any(path.startswith("tests/postgres/") for path in argv)
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
        paths = [item for item in argv if item.startswith(("tests/", "experiments/"))]
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
            repository_root=ROOT.parent,
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
        paths = [item for item in argv if item.startswith(("tests/", "experiments/"))]
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
            repository_root=ROOT.parent,
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
