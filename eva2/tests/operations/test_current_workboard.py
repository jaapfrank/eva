from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from tools.check_current_workboard import (
    TEAM,
    _validate_completion,
    _validate_state_details,
    render_human,
    validate,
)


ROOT = Path(__file__).resolve().parents[3]
EVA2 = ROOT / "eva2"
BOARD = EVA2 / "design/product/execution-workboard-v1.json"
CATALOG = EVA2 / "design/architecture/component-catalog-v1.json"
HUMAN = EVA2 / "WERKBORD.md"


def _board(tmp_path: Path, mutation, *, sync_human: bool = True) -> tuple[Path, Path]:
    value = json.loads(BOARD.read_text(encoding="utf-8"))
    mutation(value)
    board_path = tmp_path / "board.json"
    human_path = tmp_path / "WERKBORD.md"
    board_path.write_text(json.dumps(value), encoding="utf-8")
    human_path.write_text(
        render_human(value) if sync_human else "handmatig afwijkend\n",
        encoding="utf-8",
    )
    return board_path, human_path


def _validate_paths(paths: tuple[Path, Path]) -> dict[str, object]:
    return validate(ROOT, paths[0], CATALOG, paths[1])


def test_current_workboard_is_valid_and_has_only_two_executable_cards() -> None:
    result = validate(ROOT, BOARD, CATALOG, HUMAN)
    assert result == {
        "activeTaskId": "GOV-001",
        "contract": "EvaExecutionWorkboard/v1",
        "executionTaskCount": 2,
        "milestoneCount": 38,
        "status": "valid",
    }


def test_workboard_refuses_two_primary_cards(tmp_path: Path) -> None:
    paths = _board(
        tmp_path,
        lambda value: value["executionTasks"][1].update({"status": "in_progress"}),
    )
    with pytest.raises(RuntimeError, match="exactly one primary"):
        _validate_paths(paths)


def test_workboard_refuses_more_than_one_open_card_after_primary(tmp_path: Path) -> None:
    def mutation(value: dict[str, object]) -> None:
        third = deepcopy(value["executionTasks"][1])
        third.update(
            {
                "id": "RB-002",
                "order": 20,
                "dependsOn": ["RB-001"],
                "title": "te vroeg uitvoerbaar",
            }
        )
        value["executionTasks"].append(third)

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match="only the primary and immediate next"):
        _validate_paths(paths)


def test_workboard_refuses_skipping_the_first_card(tmp_path: Path) -> None:
    def mutation(value: dict[str, object]) -> None:
        value["executionTasks"][0]["status"] = "queued"
        value["executionTasks"][1]["status"] = "in_progress"
        value["activeTaskId"] = "RB-001"

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match="done, then one primary"):
        _validate_paths(paths)


def test_workboard_refuses_future_done_milestone(tmp_path: Path) -> None:
    paths = _board(
        tmp_path,
        lambda value: value["milestones"][0].update({"status": "done"}),
    )
    with pytest.raises(RuntimeError, match="milestone 0 fields differ"):
        _validate_paths(paths)


def test_workboard_refuses_done_without_completion(tmp_path: Path) -> None:
    def mutation(value: dict[str, object]) -> None:
        value["executionTasks"][0]["status"] = "done"
        value["executionTasks"][1]["status"] = "in_progress"
        value["activeTaskId"] = "RB-001"

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match="completion fields differ"):
        _validate_paths(paths)


@pytest.mark.parametrize(
    "command",
    [
        "true",
        "TMPDIR=/tmp true",
        "PYTEST_ADDOPTS=--fixtures pytest -q tests/operations/test_current_workboard.py",
        "PYTEST_ADDOPTS=--markers pytest -q tests/operations/test_current_workboard.py",
        "python3 tools/check_current_workboard.py --render-human",
        "ruff format tools/check_current_workboard.py",
        "python3 tools/check_current_workboard.py --help",
        "pytest --version",
        "pytest -q tests/this-does-not-exist.py",
        "pytest -q tests/operations/test_current_workboard.py | true",
        "pytest -q tests/operations/test_current_workboard.py > /dev/null",
        "pytest --collect-only tests/operations/test_current_workboard.py",
        "pytest --co tests/operations/test_current_workboard.py",
        "pytest -q tests/operations/test_current_workboard.py --fixtures",
        "pytest -q tests/operations/test_current_workboard.py --fixtures-per-test",
        "pytest -q tests/operations/test_current_workboard.py --markers",
        "pytest -q tests/operations/test_current_workboard.py --trace-config",
        "git diff --check",
    ],
)
def test_workboard_refuses_non_verifying_commands(tmp_path: Path, command: str) -> None:
    paths = _board(
        tmp_path,
        lambda value: value["executionTasks"][0].update({"testCommands": [command]}),
    )
    with pytest.raises(RuntimeError, match="test command"):
        _validate_paths(paths)


def test_workboard_refuses_directory_as_change_path(tmp_path: Path) -> None:
    paths = _board(
        tmp_path,
        lambda value: value["executionTasks"][0].update({"changePaths": ["eva2/tools"]}),
    )
    with pytest.raises(RuntimeError, match="exact regular repository file"):
        _validate_paths(paths)


def test_workboard_refuses_unrelated_inventory_owner(tmp_path: Path) -> None:
    paths = _board(
        tmp_path,
        lambda value: value["executionTasks"][1].update(
            {"changePaths": ["eva2/PROJECT_STATUS.md"]}
        ),
    )
    with pytest.raises(RuntimeError, match="inventory ownership differs"):
        _validate_paths(paths)


def test_workboard_refuses_wrong_component_owner(tmp_path: Path) -> None:
    paths = _board(
        tmp_path,
        lambda value: value["executionTasks"][0].update(
            {"componentIds": ["core-semantics"]}
        ),
    )
    with pytest.raises(RuntimeError, match="componentIds must match exact"):
        _validate_paths(paths)


@pytest.mark.parametrize(
    "field,extra,message",
    [
        ("inventoryOwners", "nobody", "inventoryOwners must be exact"),
        ("componentIds", "core-semantics", "componentIds must match exact"),
    ],
)
def test_workboard_refuses_surplus_ownership_claims(
    tmp_path: Path, field: str, extra: str, message: str
) -> None:
    def mutation(value: dict[str, object]) -> None:
        value["executionTasks"][0][field].append(extra)

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match=message):
        _validate_paths(paths)


def test_workboard_refuses_review_without_bound_implementation(tmp_path: Path) -> None:
    paths = _board(
        tmp_path,
        lambda value: value["executionTasks"][0].update({"status": "review"}),
    )
    with pytest.raises(RuntimeError, match="baseCommit|implementationCommit"):
        _validate_paths(paths)


def test_workboard_refuses_blocked_without_concrete_reason(tmp_path: Path) -> None:
    paths = _board(
        tmp_path,
        lambda value: value["executionTasks"][0].update({"status": "blocked"}),
    )
    with pytest.raises(RuntimeError, match="concrete blocker reason"):
        _validate_paths(paths)


@pytest.mark.parametrize(
    "mutation,message",
    [
        (
            lambda value: value["executionTasks"][0].update({"ownerRole": "chief"}),
            "unknown team role",
        ),
        (
            lambda value: value["executionTasks"][0].update(
                {"reviewerRole": value["executionTasks"][0]["ownerRole"]}
            ),
            "review its own work",
        ),
        (
            lambda value: value["team"].update({"architecturelead": "/root/pretender"}),
            "team identities differ",
        ),
    ],
)
def test_workboard_refuses_unknown_or_self_review_roles(
    tmp_path: Path, mutation, message: str
) -> None:
    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match=message):
        _validate_paths(paths)


@pytest.mark.parametrize(
    "mutation,message",
    [
        (lambda value: value.update({"lastUpdated": "morgen"}), "ISO date"),
        (
            lambda value: value.update({"rules": [*value["rules"], "sla dit over"]}),
            "contract or rules differ",
        ),
    ],
)
def test_workboard_refuses_invalid_date_or_rules(
    tmp_path: Path, mutation, message: str
) -> None:
    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match=message):
        _validate_paths(paths)


def test_workboard_refuses_out_of_order_execution_cards(tmp_path: Path) -> None:
    def mutation(value: dict[str, object]) -> None:
        tasks = deepcopy(value["executionTasks"])
        tasks[0], tasks[1] = tasks[1], tasks[0]
        value["executionTasks"] = tasks

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match="done, then one primary|identity, order or status"):
        _validate_paths(paths)


def test_workboard_refuses_human_board_drift(tmp_path: Path) -> None:
    paths = _board(tmp_path, lambda value: None, sync_human=False)
    with pytest.raises(RuntimeError, match="human workboard differs"):
        _validate_paths(paths)


def test_workboard_refuses_completion_by_the_implementer(tmp_path: Path) -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    base = subprocess.run(
        ["git", "rev-parse", "HEAD^"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    def mutation(value: dict[str, object]) -> None:
        value["executionTasks"][0]["status"] = "done"
        value["executionTasks"][1]["status"] = "in_progress"
        value["activeTaskId"] = "RB-001"
        value["completions"]["GOV-001"] = {
            "baseCommit": base,
            "commit": head,
            "decision": "GO",
            "evidenceCommit": head,
            "evidencePaths": [
                "eva2/evidence/product/current-four-way-c01-development-2026-08-30.json"
            ],
            "implementedBy": "/root",
            "reviewCommit": head,
            "reviewedBy": "/root",
            "testReceiptPaths": [
                "eva2/reviews/managed-b-host-only-preflight-0ed424f.md"
            ],
        }

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match="independent reviewer differs"):
        _validate_paths(paths)


def test_workboard_refuses_completion_from_an_unrelated_commit_diff(
    tmp_path: Path,
) -> None:
    commits = subprocess.run(
        ["git", "rev-list", "--max-count=3", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    review_commit, implementation_commit, base_commit = commits

    def mutation(value: dict[str, object]) -> None:
        value["executionTasks"][0]["status"] = "done"
        value["executionTasks"][0]["stateDetails"] = {
            "baseCommit": base_commit,
            "implementationCommit": implementation_commit,
            "evidenceCommit": review_commit,
            "reviewEvidencePaths": [
                "eva2/reviews/managed-b-host-only-preflight-0ed424f.md"
            ],
            "blockerReason": None,
        }
        value["executionTasks"][1]["status"] = "in_progress"
        value["activeTaskId"] = "RB-001"
        value["completions"]["GOV-001"] = {
            "baseCommit": base_commit,
            "commit": implementation_commit,
            "decision": "GO",
            "evidenceCommit": review_commit,
            "evidencePaths": [
                "eva2/reviews/managed-b-host-only-preflight-0ed424f.md"
            ],
            "implementedBy": "/root",
            "reviewCommit": review_commit,
            "reviewedBy": "/root/workboard_review",
            "testReceiptPaths": [
                "eva2/evidence/product/current-four-way-c01-development-2026-08-30.json"
            ],
        }

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match="exact changePaths"):
        _validate_paths(paths)


def test_completion_chain_is_reachable_with_real_git_commits(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Workboard test")
    git("config", "user.email", "workboard@example.invalid")
    implementation = repository / "eva2/app.py"
    implementation.parent.mkdir()
    implementation.write_text("value = 1\n", encoding="utf-8")
    git("add", "eva2/app.py")
    git("commit", "-m", "base")
    base_commit = git("rev-parse", "HEAD")

    implementation.write_text("value = 2\n", encoding="utf-8")
    git("add", "eva2/app.py")
    git("commit", "-m", "implementation")
    implementation_commit = git("rev-parse", "HEAD")

    receipt = repository / "eva2/evidence/product/GOV-001-tests.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps({"taskId": "GOV-001", "implementationCommit": implementation_commit}),
        encoding="utf-8",
    )
    git("add", "eva2/evidence/product/GOV-001-tests.json")
    git("commit", "-m", "evidence")
    evidence_commit = git("rev-parse", "HEAD")

    review = repository / "eva2/reviews/GOV-001-review.md"
    review.parent.mkdir(parents=True)
    review.write_text(
        f"GOV-001 independent GO for {implementation_commit}\n",
        encoding="utf-8",
    )
    git("add", "eva2/reviews/GOV-001-review.md")
    git("commit", "-m", "review")
    review_commit = git("rev-parse", "HEAD")

    task = {
        "id": "GOV-001",
        "ownerRole": "architecturelead",
        "reviewerRole": "workboardreviewer",
        "changePaths": ["eva2/app.py"],
    }
    completion = {
        "baseCommit": base_commit,
        "commit": implementation_commit,
        "decision": "GO",
        "evidenceCommit": evidence_commit,
        "evidencePaths": ["eva2/reviews/GOV-001-review.md"],
        "implementedBy": "/root",
        "reviewCommit": review_commit,
        "reviewedBy": "/root/workboard_review",
        "testReceiptPaths": ["eva2/evidence/product/GOV-001-tests.json"],
    }
    _validate_state_details(
        repository,
        task,
        "review",
        {
            "baseCommit": base_commit,
            "implementationCommit": implementation_commit,
            "evidenceCommit": evidence_commit,
            "reviewEvidencePaths": ["eva2/evidence/product/GOV-001-tests.json"],
            "blockerReason": None,
        },
    )
    _validate_completion(repository, task, completion, TEAM)
