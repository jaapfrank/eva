from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

import pytest

from tools.check_current_workboard import ARTIFACT_RETENTION_RULE, render_human, validate


ROOT = Path(__file__).resolve().parents[3]
EVA2 = ROOT / "eva2"
BOARD = EVA2 / "design/product/execution-workboard-v1.json"
CATALOG = EVA2 / "design/architecture/component-catalog-v1.json"
HUMAN = EVA2 / "WERKBORD.md"


def copy_board(tmp_path: Path, change, *, sync_human: bool = True) -> tuple[Path, Path]:
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    change(board)
    board_path = tmp_path / "board.json"
    human_path = tmp_path / "WERKBORD.md"
    board_path.write_text(json.dumps(board), encoding="utf-8")
    human_path.write_text(
        render_human(board) if sync_human else "handmatig afwijkend\n",
        encoding="utf-8",
    )
    return board_path, human_path


def test_board_has_one_small_active_card_and_direct_next() -> None:
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    result = validate(ROOT, BOARD, CATALOG, HUMAN)
    assert result["activeTaskId"] == board["activeTaskId"]
    open_cards = [card for card in board["executionTasks"] if card["status"] != "done"]
    assert len(open_cards) in {1, 2}
    assert open_cards[0]["id"] == board["activeTaskId"]
    if len(open_cards) == 2:
        assert open_cards[1]["status"] == "queued"
        assert open_cards[1]["dependsOn"] == [open_cards[0]["id"]]
    assert board["audit"]["releaseRecovery"]["evidence"].endswith(
        "RR-002-RR-004-release-recovery-2026-09-04.json"
    )
    assert board["audit"]["releaseRecovery"]["review"].endswith(
        "RR-002-RR-004-release-recovery-c1d4c8a.md"
    )


def test_product_cadence_and_release_phase_are_explicit() -> None:
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    rules = " ".join(board["rules"])
    assert "nulmeting" in rules and "maintained upstream" in rules
    assert ARTIFACT_RETENTION_RULE in board["rules"]
    milestones = {card["id"]: card for card in board["milestones"]}
    execution = {card["id"]: card for card in board["executionTasks"]}
    assert execution["CO-001"]["order"] == 110
    assert "sendMessageDraft" in " ".join(execution["CO-001"]["evidenceRequired"])
    assert not any(card_id.startswith("OS-") for card_id in milestones)
    assert "werkende slot blijft actief" in rules
    assert "kleine ontwikkelwijzigingen krijgen geen A/B-cyclus" in rules
    assert [milestones[f"RB-00{i}"]["order"] for i in range(7, 10)] + [milestones["RB-010"]["order"]] == [400, 410, 420, 430]
    assert "reserveslot" in milestones["RB-010"]["outcome"]
    assert "route" in milestones["RB-010"]["outcome"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda board: board.update({"activeTaskId": "EV-001"}), "exactly one primary"),
        (lambda board: board["executionTasks"][0].update({"changePaths": ["/tmp/unsafe.py"]}), "safe repository path"),
        (lambda board: board["executionTasks"][-2].update({"testCommands": ["true"]}), "unsupported test command"),
        (
            lambda board: board["executionTasks"][-1]["testCommands"].append(
                "pytest -q tests/benchmark/test_comparable_agent_run_validator.py"
            ),
            "retired benchmark",
        ),
        (lambda board: board["rules"].remove(ARTIFACT_RETENTION_RULE), "rules differ"),
        (
            lambda board: board["completions"]["RR-001"].update(
                {"reviewedBy": board["completions"]["RR-001"]["implementedBy"]}
            ),
            "independent reviewer",
        ),
        (lambda board: board["audit"].pop("releaseRecovery"), "audit"),
    ],
)
def test_checker_keeps_core_invariants(tmp_path: Path, change, message: str) -> None:
    paths = copy_board(tmp_path, change)
    with pytest.raises(RuntimeError, match=message):
        validate(ROOT, paths[0], CATALOG, paths[1])


def test_completed_work_requires_an_independent_reviewer() -> None:
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    done = next(card for card in board["executionTasks"] if card["id"] == "RR-001")
    assert done["reviewerRole"] != done["ownerRole"]


def test_checklist_preserves_evidence_and_open_product_outcomes() -> None:
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    rendered = render_human(board)
    assert "PROJECTDOEL.md" in rendered
    for card in board["executionTasks"]:
        mark = "x" if card["status"] == "done" else " "
        assert f"- [{mark}] `{card['id']}`" in rendered
    for card in board["milestones"]:
        assert f"- [ ] `{card['id']}`" in rendered
    assert "- [ ] `CO-002`" in rendered
    assert board["activeTaskId"] in {
        card["id"] for card in board["executionTasks"] if card["status"] != "done"
    }
    assert "- [ ] `CO-003`" in rendered
    assert {"UP-001", "AU-004", "AU-005", "AU-006", "EV-003"} <= {
        card["id"] for card in board["executionTasks"] + board["milestones"]
    }
    for completion in board["completions"].values():
        for path in completion["testReceiptPaths"]:
            assert path.removeprefix("eva2/") in rendered


def test_human_board_cannot_drift_from_machine_source(tmp_path: Path) -> None:
    paths = copy_board(tmp_path, lambda _board: None, sync_human=False)
    with pytest.raises(RuntimeError, match="human workboard differs"):
        validate(ROOT, paths[0], CATALOG, paths[1])


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "reordered"])
def test_dashboard_roadmap_cannot_be_a_second_work_queue(tmp_path: Path, mutation: str) -> None:
    def change(board):
        ids = board["audit"]["projectDashboard"]["roadmap"][0]["taskIds"]
        if mutation == "missing":
            ids.pop()
        elif mutation == "duplicate":
            ids.append(ids[0])
        elif mutation == "unknown":
            ids.append("MISSING-001")
        else:
            done = {card["id"] for card in board["executionTasks"] if card["status"] == "done"}
            first, second = [index for index, card_id in enumerate(ids) if card_id not in done][:2]
            ids[first], ids[second] = ids[second], ids[first]

    paths = copy_board(tmp_path, change)
    with pytest.raises(RuntimeError, match="roadmap"):
        validate(ROOT, paths[0], CATALOG, paths[1])


def test_board_keeps_outcomes_visible_and_technical_details_collapsed() -> None:
    board = json.loads(BOARD.read_text(encoding="utf-8"))
    rendered = render_human(board)
    visible = re.sub(r"<details>.*?</details>", "", rendered, flags=re.DOTALL)
    primary = next(card for card in board["executionTasks"] if card["id"] == board["activeTaskId"])
    assert rendered.count("<details>") == rendered.count("</details>") == 3
    assert "<details open" not in rendered
    assert primary["outcome"] in visible
    for condition in primary["doneWhen"]:
        assert condition in visible
    for path in primary["changePaths"]:
        assert f"`{path}`" in rendered and f"`{path}`" not in visible
    for command in primary["testCommands"]:
        assert f"`{command}`" in rendered and f"`{command}`" not in visible
    for milestone in board["milestones"]:
        assert f"- [ ] `{milestone['id']}`" in visible
    assert "- [ ] `CO-002`" in visible
    assert "- [ ] `CO-003`" in visible


def test_only_completed_bound_deletions_use_historical_inventory(tmp_path: Path) -> None:
    from tools.check_current_workboard import _task_inventory_entry

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Workboard fixture")
    git("config", "user.email", "workboard@example.invalid")
    raw = "eva2/old-asset.js"
    old = tmp_path / raw
    old.parent.mkdir()
    old.write_text("old asset\n")
    kept = tmp_path / "eva2/kept.js"
    kept.write_text("kept asset\n")
    entry = {"path": raw, "present": True, "decision": "keep", "owner": "eva-runtime"}
    inventory = tmp_path / "docs/eva-repository-inventory.generated.json"
    inventory.parent.mkdir()
    inventory.write_text(json.dumps({"files": [entry]}))
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    old.unlink()
    git("add", "-u")
    git("commit", "-qm", "replace asset")
    implementation = git("rev-parse", "HEAD")
    task = {"id": "VB-003", "status": "done", "stateDetails": {
        "baseCommit": base, "implementationCommit": implementation,
    }}
    assert _task_inventory_entry(tmp_path, raw, task, {}) == entry
    for status in ("in_progress", "queued"):
        with pytest.raises(RuntimeError, match="does not exist"):
            _task_inventory_entry(tmp_path, raw, {**task, "status": status}, {})
    # A local deletion absent from the implementation is not historical proof.
    kept.unlink()
    with pytest.raises(RuntimeError, match="not a verified implementation deletion"):
        _task_inventory_entry(tmp_path, "eva2/kept.js", task, {})


@pytest.mark.parametrize("case", ["linear", "reverted_outside", "merge", "unrelated"])
def test_implementation_series_preserves_exact_scope(tmp_path: Path, case: str) -> None:
    from tools.check_current_workboard import _validate_implementation_scope

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Review fixture")
    git("config", "user.email", "review@example.invalid")
    file = tmp_path / "feature.py"
    file.write_text("before\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    file.write_text("first\n")
    git("commit", "-qam", "first implementation")
    if case == "reverted_outside":
        outside = tmp_path / "outside.txt"
        outside.write_text("not in scope\n")
        git("add", ".")
        git("commit", "-qm", "outside scope")
        outside.unlink()
        git("commit", "-qam", "revert outside")
    elif case == "merge":
        git("checkout", "-qb", "side", base)
        (tmp_path / "side.txt").write_text("side\n")
        git("add", ".")
        git("commit", "-qm", "side")
        git("checkout", "-")
        git("merge", "--no-ff", "-qm", "merge", "side")
    elif case == "unrelated":
        git("checkout", "--orphan", "unrelated")
        git("commit", "-qm", "unrelated root")
    file.write_text("final\n")
    git("commit", "-qam", "final implementation")
    final = git("rev-parse", "HEAD")
    if case == "linear":
        assert _validate_implementation_scope(tmp_path, "CO-008", base, final, ["feature.py"]) == (base, final)
    else:
        with pytest.raises(RuntimeError, match="range"):
            _validate_implementation_scope(tmp_path, "CO-008", base, final, ["feature.py"])


def test_later_proof_commit_stays_bound_and_proof_only(tmp_path: Path) -> None:
    from tools.check_current_workboard import _validate_bound_proof_commit

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Review fixture")
    git("config", "user.email", "review@example.invalid")
    (tmp_path / "feature.py").write_text("implemented\n")
    git("add", ".")
    git("commit", "-qm", "implementation")
    implementation = git("rev-parse", "HEAD")
    (tmp_path / "other.py").write_text("separate work\n")
    git("add", ".")
    git("commit", "-qm", "other task")
    (tmp_path / "review.md").write_text("review of " + implementation)
    git("add", ".")
    git("commit", "-qm", "independent review")
    proof = git("rev-parse", "HEAD")
    assert _validate_bound_proof_commit(tmp_path, "CO-008", implementation, proof, ["review.md"], "reviewCommit") == proof
    with pytest.raises(RuntimeError, match="exact proof paths"):
        _validate_bound_proof_commit(tmp_path, "CO-008", implementation, proof, ["other.py"], "reviewCommit")
    with pytest.raises(RuntimeError, match="bound predecessor"):
        _validate_bound_proof_commit(tmp_path, "CO-008", proof, implementation, ["feature.py"], "reviewCommit")
