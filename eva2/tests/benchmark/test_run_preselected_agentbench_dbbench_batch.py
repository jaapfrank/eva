from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


BENCHMARK = Path(__file__).resolve().parents[3] / "benchmark"
sys.path.insert(0, str(BENCHMARK))
import run_preselected_agentbench_dbbench_batch as batch  # noqa: E402


def _selection(path: Path, *, indices: list[int] | None = None) -> Path:
    document = {
        "contract": batch.SELECTION_CONTRACT,
        "selectedBeforeAnyCase32Through39ResultWasKnown": True,
        "selection": {
            "caseIndices": indices if indices is not None else list(range(32, 40)),
            "selectionRule": "fixed test selection",
        },
        "candidate": {
            "clientSha256": batch._sha256(BENCHMARK / "eva_agentbench_client.py"),
            "runnerSha256": batch._sha256(
                BENCHMARK / "run_agentbench_dbbench_development.py"
            ),
        },
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _write_case(path: Path, index: int) -> dict[str, object]:
    artifact: dict[str, object] = {
        "caseIndex": index,
        "modelTurnsUsed": 2,
        "result": {"finish": True, "status": "completed"},
    }
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return artifact


def test_runs_only_the_eight_preselected_cases_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fake_run(*, controller: str, case_index: int, output: Path) -> dict[str, object]:
        assert controller == "http://127.0.0.1:5000"
        calls.append(case_index)
        return _write_case(output, case_index)

    monkeypatch.setattr(batch, "run_one_case", fake_run)
    output = tmp_path / "output"
    result = batch.run_batch(
        controller="http://127.0.0.1:5000",
        selection_path=_selection(tmp_path / "selection.json"),
        output_dir=output,
    )

    assert calls == list(range(32, 40))
    assert result["completedCases"] == 8
    assert result["failedCases"] == 0
    assert result["retriedCases"] == 0
    assert (output / "batch-summary.json").is_file()


def test_rejects_a_changed_case_selection_before_running(tmp_path: Path) -> None:
    with pytest.raises(batch.BatchError, match="sealed case range"):
        batch.run_batch(
            controller="http://127.0.0.1:5000",
            selection_path=_selection(tmp_path / "selection.json", indices=[32]),
            output_dir=tmp_path / "output",
        )
    assert not (tmp_path / "output").exists()


def test_rejects_a_changed_candidate_hash(tmp_path: Path) -> None:
    selection = _selection(tmp_path / "selection.json")
    document = json.loads(selection.read_text(encoding="utf-8"))
    document["candidate"]["clientSha256"] = "0" * 64
    selection.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(batch.BatchError, match="source differs"):
        batch.run_batch(
            controller="http://127.0.0.1:5000",
            selection_path=selection,
            output_dir=tmp_path / "output",
        )


def test_refuses_an_existing_output_directory(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(batch.BatchError, match="already exists"):
        batch.run_batch(
            controller="http://127.0.0.1:5000",
            selection_path=_selection(tmp_path / "selection.json"),
            output_dir=output,
        )


def test_sealed_failure_is_not_retried_or_copied_into_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fake_run(*, controller: str, case_index: int, output: Path) -> dict[str, object]:
        del controller
        calls.append(case_index)
        if case_index == 34:
            output.write_text('{"failure":"fixed_category"}\n', encoding="utf-8")
            raise batch.DbBenchDevelopmentError("raw private model text")
        return _write_case(output, case_index)

    monkeypatch.setattr(batch, "run_one_case", fake_run)
    output = tmp_path / "output"
    result = batch.run_batch(
        controller="http://127.0.0.1:5000",
        selection_path=_selection(tmp_path / "selection.json"),
        output_dir=output,
    )

    assert calls.count(34) == 1
    assert result["failedCases"] == 1
    summary = (output / "batch-summary.json").read_text(encoding="utf-8")
    assert "raw private model text" not in summary


def test_unsealed_failure_stops_before_the_next_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fake_run(*, controller: str, case_index: int, output: Path) -> dict[str, object]:
        del controller, output
        calls.append(case_index)
        raise batch.DbBenchDevelopmentError("provider unavailable")

    monkeypatch.setattr(batch, "run_one_case", fake_run)
    with pytest.raises(batch.BatchError, match="failed without a sealed artifact"):
        batch.run_batch(
            controller="http://127.0.0.1:5000",
            selection_path=_selection(tmp_path / "selection.json"),
            output_dir=tmp_path / "output",
        )
    assert calls == [32]
