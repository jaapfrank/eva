from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "development/benchmark" / "run_gaia_development.py"
SPEC = importlib.util.spec_from_file_location("gaia_development", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gaia = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gaia
SPEC.loader.exec_module(gaia)


def _metadata(path: Path) -> None:
    path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "task_id": "development-001",
                        "Question": "Which value is safe to return?",
                        "Final answer": "private-gold-must-not-reach-agent",
                        "file_path": "attachment.txt",
                    }
                ),
                json.dumps(
                    {
                        "task_id": "development-002",
                        "Question": "Return the other value.",
                        "Final answer": "another-private-gold",
                        "file_path": None,
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )


def _agent_command() -> list[str]:
    return [
        sys.executable,
        "-c",
        (
            "import json,sys; task=json.load(sys.stdin); "
            "assert set(task)=={'attachment_path','question','task_id'}; "
            "assert 'Final answer' not in task and 'private-gold' not in str(task); "
            "print(json.dumps({'model_answer':'development-answer-'+task['task_id']}))"
        ),
    ]


def test_development_adapter_emits_official_answer_shape_without_scoring() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        tmp_path = Path(directory)
        metadata = tmp_path / "metadata.jsonl"
        attachments = tmp_path / "attachments"
        attachments.mkdir()
        (attachments / "attachment.txt").write_text(
            "local test attachment", encoding="utf-8"
        )
        _metadata(metadata)
        output = tmp_path / "run"

        receipt = gaia.run(
            metadata_path=metadata,
            attachment_root=attachments,
            output_directory=output,
            maximum_tasks=2,
            timeout_seconds=10,
            command=_agent_command(),
        )

        answers = [
            json.loads(line) for line in (output / "answers.jsonl").read_text().splitlines()
        ]
        assert answers == [
            {"model_answer": "development-answer-development-001", "task_id": "development-001"},
            {"model_answer": "development-answer-development-002", "task_id": "development-002"},
        ]
        assert receipt["contract"] == "EvaGaiaDevelopmentRun/v1"
        assert receipt["officialScore"] is None
        assert receipt["taskCount"] == 2
        assert "private-gold" not in (output / "answers.jsonl").read_text()
        assert "private-gold" not in (output / "adapter-trace.jsonl").read_text()
        assert (output / "answers.jsonl").stat().st_mode & 0o777 == 0o600
        assert output.stat().st_mode & 0o777 == 0o700


def test_development_adapter_fails_closed_on_unsafe_attachment_or_bad_agent_output(
    tmp_path: Path,
) -> None:
    metadata = tmp_path / "metadata.jsonl"
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    _metadata(metadata)
    with pytest.raises(gaia.RunError, match="attachment is unavailable"):
        gaia.run(
            metadata_path=metadata,
            attachment_root=attachments,
            output_directory=tmp_path / "missing-attachment",
            maximum_tasks=2,
            timeout_seconds=10,
            command=_agent_command(),
        )
    (attachments / "attachment.txt").write_text("available", encoding="utf-8")
    with pytest.raises(gaia.RunError, match="exactly model_answer"):
        gaia.run(
            metadata_path=metadata,
            attachment_root=attachments,
            output_directory=tmp_path / "bad-agent",
            maximum_tasks=1,
            timeout_seconds=10,
            command=[sys.executable, "-c", "print('{}')"],
        )
    assert not (tmp_path / "bad-agent").exists()


def test_parquet_reader_selects_only_safe_columns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.parquet"
    metadata.write_bytes(b"not-read-by-fake-arrow")
    attachments = tmp_path / "attachments"
    attachments.mkdir()

    selected_columns: list[list[str]] = []

    class FakeTable:
        def to_pylist(self) -> list[dict[str, object]]:
            return [
                {
                    "task_id": "parquet-development-001",
                    "Question": "What is safe to return?",
                    "file_path": None,
                }
            ]

    class FakeParquet:
        @staticmethod
        def read_table(path: Path, *, columns: list[str], memory_map: bool) -> FakeTable:
            assert path == metadata
            assert memory_map is True
            selected_columns.append(columns)
            return FakeTable()

    fake_pyarrow = ModuleType("pyarrow")
    fake_pyarrow.parquet = FakeParquet
    monkeypatch.setitem(sys.modules, "pyarrow", fake_pyarrow)

    receipt = gaia.run(
        metadata_path=metadata,
        attachment_root=attachments,
        output_directory=tmp_path / "parquet-run",
        maximum_tasks=1,
        timeout_seconds=10,
        command=_agent_command(),
    )

    assert selected_columns == [["task_id", "Question", "file_path"]]
    assert receipt["officialScore"] is None
    assert "Final answer" not in (tmp_path / "parquet-run" / "answers.jsonl").read_text()


def test_cli_requires_explicit_budget_and_writes_a_receipt(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.jsonl"
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    (attachments / "attachment.txt").write_text("available", encoding="utf-8")
    _metadata(metadata)
    output = tmp_path / "cli-run"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--metadata-jsonl",
            str(metadata),
            "--attachments-root",
            str(attachments),
            "--output-directory",
            str(output),
            "--max-tasks",
            "1",
            "--timeout-seconds",
            "10",
            "--agent-command",
            *_agent_command(),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout)["officialScore"] is None
    assert (output / "receipt.json").is_file()
