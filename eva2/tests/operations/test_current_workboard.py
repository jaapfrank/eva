from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest
import tools.check_current_workboard as workboard_checker

from tools.check_current_workboard import (
    ARTIFACT_RETENTION_RULE,
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


def _primary(value: dict[str, object]) -> dict[str, object]:
    return next(
        task
        for task in value["executionTasks"]
        if task["status"] in {"in_progress", "review", "blocked"}
    )


def _next(value: dict[str, object]) -> dict[str, object]:
    primary_index = value["executionTasks"].index(_primary(value))
    if primary_index + 1 < len(value["executionTasks"]):
        return value["executionTasks"][primary_index + 1]
    milestone = value["milestones"].pop(0)
    task = deepcopy(value["executionTasks"][primary_index])
    task.update(
        {
            "id": milestone["id"],
            "order": milestone["order"],
            "area": milestone["area"],
            "title": milestone["title"],
            "outcome": milestone["outcome"],
            "status": "queued",
            "stateDetails": {
                "baseCommit": None,
                "implementationCommit": None,
                "evidenceCommit": None,
                "reviewEvidencePaths": [],
                "blockerReason": None,
            },
            "dependsOn": [value["executionTasks"][primary_index]["id"]],
        }
    )
    value["executionTasks"].append(task)
    return task


def test_current_workboard_is_valid_with_history_and_one_open_card() -> None:
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    result = validate(ROOT, BOARD, CATALOG, HUMAN)
    assert result == {
        "activeTaskId": _primary(board)["id"],
        "contract": "EvaExecutionWorkboard/v1",
        "executionTaskCount": len(board["executionTasks"]),
        "milestoneCount": len(board["milestones"]),
        "status": "valid",
    }
    assert len([task for task in board["executionTasks"] if task["status"] != "done"]) == 1


def test_eval_engine_adoption_precedes_new_product_work() -> None:
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    by_id = {
        item["id"]: item
        for item in [*board["executionTasks"], *board["milestones"]]
    }

    assert by_id["CP-000"]["title"] == "Open-source evaluatiemotor voorselecteren"
    assert [by_id[item]["order"] for item in ("RB-010", "EV-001", "EV-002", "EV-003", "OS-001")] == [
        100,
        103,
        106,
        109,
        110,
    ]
    assert "productie-image" in " ".join(by_id["CP-000"]["doneWhen"])
    assert "vijfwegmeting" in by_id["EV-002"]["title"].lower()
    assert "sandbox/CLI-agentbruggen" in by_id["EV-001"]["outcome"]
    assert "nulmeting" in by_id["EV-003"]["outcome"]


def test_hermetic_release_recovery_precedes_required_test_execution() -> None:
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    tasks = {item["id"]: item for item in board["executionTasks"]}
    milestones = {item["id"]: item for item in board["milestones"]}

    assert board["activeTaskId"] == "RR-002"
    assert tasks["RR-001"]["status"] == "done"
    assert tasks["RR-001"]["dependsOn"] == ["CP-000"]
    assert tasks["RR-002"]["status"] == "in_progress"
    assert tasks["RR-002"]["dependsOn"] == ["RR-001"]
    assert [
        item
        for item in board["lockedFoundationSequence"]
        if item.startswith("RR-") or item in {"RB-007", "RB-008"}
    ] == ["RR-001", "RR-002", "RR-003", "RR-004", "RB-007", "RB-008"]
    assert [milestones[item]["order"] for item in ("RR-003", "RR-004", "RB-007")] == [
        68,
        69,
        70,
    ]
    assert "Na RR-002" in milestones["RR-003"]["outcome"]
    assert "Na RR-003" in milestones["RR-004"]["outcome"]
    assert "na RR-004" in milestones["RB-007"]["outcome"]
    assert {
        "docs/eva-repository-inventory.generated.json",
        "docs/eva-repository-inventory.generated.md",
        "eva2/design/fixtures/noor-client-ack-key-v1.json",
        "eva2/tests/design/test_component_catalog_repository.py",
    }.issubset(tasks["RR-001"]["changePaths"])
    assert "147 uitvoerbare testmodules" in " ".join(tasks["RR-001"]["evidenceRequired"])
    assert "20 supportinputs" in " ".join(tasks["RR-001"]["evidenceRequired"])
    assert "/home/administrator/project/.eva2-artifacts/rr002-builder-a" in " ".join(
        tasks["RR-002"]["evidenceRequired"]
    )
    assert "/home/administrator/project/.eva2-artifacts/rr002-builder-b" in " ".join(
        tasks["RR-002"]["evidenceRequired"]
    )
    assert set(tasks["RR-002"]["changePaths"]) == {
        "docs/eva-repository-inventory.generated.json",
        "docs/eva-repository-inventory.generated.md",
        "eva2/deploy/living-core/Dockerfile",
        "eva2/release/managed-b-release-input-authority-v1.json",
        "eva2/tests/operations/test_managed_b_candidate_image.py",
        "eva2/tests/operations/test_two_builder_reproducibility.py",
        "eva2/tools/check_two_builder_reproducibility.py",
    }
    assert "zonder handmatige JSON-verbouwing" in " ".join(tasks["RR-002"]["doneWhen"])
    assert board["completions"]["RR-001"] == {
        "baseCommit": "ca6de2b0c0fe7bde1fdf740979ca3959c3e998ae",
        "commit": "daa9022d2ef9f8aefa7e22c1f90d7f6a0c463c6c",
        "decision": "GO",
        "evidenceCommit": "78ad15c93869b663f2f55eaf1dbb15a828abbb77",
        "evidencePaths": ["eva2/reviews/RR-001-hermetic-candidate-source-daa9022.md"],
        "implementedBy": "/root/release_authority",
        "reviewCommit": "766904152c62fb0debef0906321ad3d6fc731874",
        "reviewedBy": "/root/proof_review",
        "testReceiptPaths": [
            "eva2/evidence/product/RR-001-hermetic-candidate-source-2026-09-02.json"
        ],
    }

    for historical_id in ("RB-003", "RB-004", "RB-005", "RB-006"):
        assert tasks[historical_id]["status"] == "done"
        assert historical_id in board["completions"]


@pytest.mark.parametrize(
    ("task_id", "order", "error"),
    [
        ("CO-000", 75, "locked foundation policy differs"),
        ("OS-000", 101, "locked foundation policy differs"),
    ],
)
def test_workboard_refuses_inserting_work_into_locked_foundation(
    tmp_path: Path,
    task_id: str,
    order: int,
    error: str,
) -> None:
    def mutation(value: dict[str, object]) -> None:
        value["milestones"].append(
            {
                "id": task_id,
                "order": order,
                "area": "te vroeg productwerk",
                "title": "fundering omzeilen",
                "outcome": "Deze kaart mag niet tussen de vastgezette basis komen.",
            }
        )
        value["milestones"].sort(key=lambda item: item["order"])
        insertion = next(
            index
            for index, known_id in enumerate(value["lockedFoundationSequence"])
            if next(
                item["order"]
                for item in [*value["executionTasks"], *value["milestones"]]
                if item["id"] == known_id
            )
            > order
        )
        value["lockedFoundationSequence"].insert(insertion, task_id)

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match=error):
        _validate_paths(paths)


def test_workboard_refuses_repurposing_a_locked_foundation_card(tmp_path: Path) -> None:
    def mutation(value: dict[str, object]) -> None:
        item = next(
            milestone
            for milestone in value["milestones"]
            if milestone["id"] == "EV-001"
        )
        item.update(
            {
                "area": "Voiceproduct",
                "title": "Full-duplex voice bouwen",
                "outcome": "Deze productkaart mag een evaluatiekaart niet vermommen.",
            }
        )

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match="locked foundation contract differs"):
        _validate_paths(paths)


def test_workboard_refuses_repurposing_locked_execution_scope(tmp_path: Path) -> None:
    def mutation(value: dict[str, object]) -> None:
        task = _primary(value)
        task.update(
            {
                "componentIds": ["api-and-runtime-composition"],
                "inventoryOwners": ["eva-runtime"],
                "changePaths": ["eva2/src/eva/adapters/api/app.py"],
                "evidenceRequired": ["Een nieuwe runtimefunctie lijkt te werken."],
                "doneWhen": [
                    "De productfunctie is toegevoegd.",
                    "De kaart houdt haar oude naam.",
                ],
            }
        )

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match="locked foundation contract differs"):
        _validate_paths(paths)


def test_open_card_refuses_retired_benchmark_validator(tmp_path: Path) -> None:
    def mutation(value: dict[str, object]) -> None:
        _next(value)["testCommands"].append(
            "pytest -q tests/benchmark/test_comparable_agent_run_validator.py"
        )

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match="references a retired benchmark format"):
        _validate_paths(paths)


def test_workboard_refuses_two_primary_cards(tmp_path: Path) -> None:
    paths = _board(tmp_path, lambda value: _next(value).update({"status": "in_progress"}))
    with pytest.raises(RuntimeError, match="exactly one primary"):
        _validate_paths(paths)


def test_workboard_refuses_more_than_one_open_card_after_primary(tmp_path: Path) -> None:
    def mutation(value: dict[str, object]) -> None:
        next_task = _next(value)
        third = deepcopy(next_task)
        third.update(
            {
                "id": "ZZ-999",
                "order": next_task["order"] + 1,
                "dependsOn": [next_task["id"]],
                "title": "te vroeg uitvoerbaar",
            }
        )
        value["executionTasks"].append(third)

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match="only the primary and immediate next"):
        _validate_paths(paths)


def test_workboard_refuses_skipping_the_first_card(tmp_path: Path) -> None:
    def mutation(value: dict[str, object]) -> None:
        primary = _primary(value)
        next_task = _next(value)
        primary["status"] = "queued"
        next_task["status"] = "in_progress"
        value["activeTaskId"] = next_task["id"]

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
        primary = _primary(value)
        next_task = _next(value)
        primary["status"] = "done"
        next_task["status"] = "in_progress"
        value["activeTaskId"] = next_task["id"]

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
        lambda value: _primary(value).update({"testCommands": [command]}),
    )
    with pytest.raises(RuntimeError, match="test command"):
        _validate_paths(paths)


def test_workboard_refuses_directory_as_change_path(tmp_path: Path) -> None:
    paths = _board(
        tmp_path,
        lambda value: _primary(value).update({"changePaths": ["eva2/tools"]}),
    )
    with pytest.raises(RuntimeError, match="exact regular repository file"):
        _validate_paths(paths)


def test_workboard_refuses_unrelated_inventory_owner(tmp_path: Path) -> None:
    paths = _board(
        tmp_path,
        lambda value: _next(value).update(
            {
                "changePaths": ["eva2/pyproject.toml"],
                "inventoryOwners": ["deployment"],
            }
        ),
    )
    with pytest.raises(RuntimeError, match="inventory ownership differs"):
        _validate_paths(paths)


def test_open_card_cannot_escape_the_canonical_eva2_authoring_root(
    tmp_path: Path,
) -> None:
    def mutation(value: dict[str, object]) -> None:
        task = _primary(value)
        task["changePaths"] = [
            "docs/repository-inventory-handwritten-copy.json",
            "eva2/tests/operations/test_current_workboard.py",
        ]
        task["inventoryOwners"] = ["architecture", "verification"]
        task["componentIds"] = ["operations-and-release-tests"]

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match="canonical Eva2 authoring root"):
        _validate_paths(paths)


def test_inventory_metadata_allows_changed_bytes_but_not_scope_attacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    target = _primary(board)["changePaths"][0]
    inventory_path = ROOT / board["inventoryPath"]
    inventory_reader = workboard_checker._inventory
    entries, inventory = inventory_reader(ROOT, inventory_path)

    changed_bytes = deepcopy(entries)
    changed_bytes[target]["hashMode"] = "exact-bytes"
    changed_bytes[target]["contentSha256"] = "0" * 64
    monkeypatch.setattr(
        workboard_checker,
        "_inventory",
        lambda _root, _path: (changed_bytes, inventory),
    )
    assert _validate_paths((BOARD, HUMAN))["status"] == "valid"

    missing_entry = deepcopy(changed_bytes)
    missing_entry.pop(target)
    monkeypatch.setattr(
        workboard_checker,
        "_inventory",
        lambda _root, _path: (missing_entry, inventory),
    )
    with pytest.raises(RuntimeError, match="inventory ownership differs"):
        _validate_paths((BOARD, HUMAN))

    wrong_owner = deepcopy(changed_bytes)
    wrong_owner[target]["owner"] = "outside-eva2"
    monkeypatch.setattr(
        workboard_checker,
        "_inventory",
        lambda _root, _path: (wrong_owner, inventory),
    )
    with pytest.raises(RuntimeError, match="inventory ownership differs"):
        _validate_paths((BOARD, HUMAN))

    monkeypatch.setattr(workboard_checker, "_inventory", inventory_reader)
    paths = _board(
        tmp_path,
        lambda value: _primary(value).update(
            {"changePaths": ["eva2/does-not-exist.md"]}
        ),
    )
    with pytest.raises(RuntimeError, match="does not exist"):
        _validate_paths(paths)


def test_workboard_refuses_wrong_component_owner(tmp_path: Path) -> None:
    paths = _board(
        tmp_path,
        lambda value: _primary(value).update({"componentIds": ["core-semantics"]}),
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
        _primary(value)[field].append(extra)

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match=message):
        _validate_paths(paths)


def test_workboard_refuses_review_without_bound_implementation(tmp_path: Path) -> None:
    def mutation(value: dict[str, object]) -> None:
        task = _primary(value)
        task["status"] = "review"
        task["stateDetails"] = {
            "baseCommit": None,
            "blockerReason": None,
            "evidenceCommit": None,
            "implementationCommit": None,
            "reviewEvidencePaths": [],
        }

    paths = _board(
        tmp_path,
        mutation,
    )
    with pytest.raises(RuntimeError, match="baseCommit|implementationCommit"):
        _validate_paths(paths)


def test_workboard_refuses_blocked_without_concrete_reason(tmp_path: Path) -> None:
    def mutation(value: dict[str, object]) -> None:
        task = _primary(value)
        task["status"] = "blocked"
        task["stateDetails"] = {
            "baseCommit": None,
            "blockerReason": None,
            "evidenceCommit": None,
            "implementationCommit": None,
            "reviewEvidencePaths": [],
        }

    paths = _board(
        tmp_path,
        mutation,
    )
    with pytest.raises(RuntimeError, match="concrete blocker reason"):
        _validate_paths(paths)


@pytest.mark.parametrize(
    "mutation,message",
    [
        (
            lambda value: _primary(value).update({"ownerRole": "chief"}),
            "unknown team role",
        ),
        (
            lambda value: _primary(value).update(
                {"reviewerRole": _primary(value)["ownerRole"]}
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


@pytest.mark.parametrize(
    "replacement",
    [None, ".eva2-artifacts vereist een kaart met een retentiepad."],
)
def test_workboard_refuses_removed_or_weakened_artifact_retention_rule(
    tmp_path: Path,
    replacement: str | None,
) -> None:
    def mutation(value: dict[str, object]) -> None:
        rules = value["rules"]
        index = rules.index(ARTIFACT_RETENTION_RULE)
        if replacement is None:
            rules.pop(index)
        else:
            rules[index] = replacement

    paths = _board(tmp_path, mutation)
    with pytest.raises(RuntimeError, match="contract or rules differ"):
        _validate_paths(paths)


def test_workboard_refuses_out_of_order_execution_cards(tmp_path: Path) -> None:
    def mutation(value: dict[str, object]) -> None:
        tasks = deepcopy(value["executionTasks"])
        tasks[0], tasks[1] = tasks[1], tasks[0]
        value["executionTasks"] = tasks

    paths = _board(tmp_path, mutation)
    with pytest.raises(
        RuntimeError,
        match="done, then one primary|identity, order or status|missing or forward dependency",
    ):
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
        value["executionTasks"][0]["stateDetails"] = {
            "baseCommit": base_commit,
            "implementationCommit": implementation_commit,
            "evidenceCommit": review_commit,
            "reviewEvidencePaths": [
                "eva2/reviews/managed-b-host-only-preflight-0ed424f.md"
            ],
            "blockerReason": None,
        }
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
