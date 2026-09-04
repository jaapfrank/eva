from __future__ import annotations

import json
from pathlib import Path

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
    assert validate(ROOT, BOARD, CATALOG, HUMAN)["activeTaskId"] == "EV-001"
    open_cards = [card for card in board["executionTasks"] if card["status"] != "done"]
    assert [card["id"] for card in open_cards] == ["EV-001", "EV-002"]
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
    assert [milestones[f"RB-00{i}"]["order"] for i in range(7, 10)] + [milestones["RB-010"]["order"]] == [400, 410, 420, 430]
    assert "reserveslot" in milestones["RB-010"]["outcome"]
    assert "route" in milestones["RB-010"]["outcome"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda board: board.update({"activeTaskId": "EV-002"}), "exactly one primary"),
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


def test_human_board_cannot_drift_from_machine_source(tmp_path: Path) -> None:
    paths = copy_board(tmp_path, lambda _board: None, sync_human=False)
    with pytest.raises(RuntimeError, match="human workboard differs"):
        validate(ROOT, paths[0], CATALOG, paths[1])
