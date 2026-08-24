from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from eva.adapters.connectors.local_files import LocalMarkdownBatchAdapter


def test_batch_keeps_four_outputs_and_scopes_one_unwritable_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    for name in ("alpha", "bravo", "charlie", "delta", "echo"):
        (source / name).mkdir(parents=True)
        (destination / name).mkdir(parents=True)
        (source / name / f"{name}.txt").write_text(
            f"inhoud {name}   \n", encoding="utf-8"
        )
    blocked = destination / "charlie"
    blocked.chmod(0o555)
    try:
        adapter = LocalMarkdownBatchAdapter(tmp_path)
        plan = adapter.prepare(
            source_directory="source", destination_directory="destination"
        )

        result = adapter.execute(plan)

        assert len(result.successful) == 4
        assert len(result.failed) == 1
        assert result.retryable_failed == result.failed
        assert result.failed[0].destination_path == "charlie/charlie.md"
        assert result.failed[0].error_code == "destination_unwritable"
        for item in result.successful:
            output = destination / item.destination_path
            assert output.is_file()
            assert hashlib.sha256(output.read_bytes()).hexdigest() == item.output_sha256
        assert not (blocked / "charlie.md").exists()
    finally:
        blocked.chmod(0o755)


def test_batch_retry_reads_existing_outputs_without_overwriting(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    (tmp_path / "destination").mkdir()
    (tmp_path / "source" / "note.txt").write_text("regel\n", encoding="utf-8")
    adapter = LocalMarkdownBatchAdapter(tmp_path)
    plan = adapter.prepare(
        source_directory="source", destination_directory="destination"
    )

    first = adapter.execute(plan)
    second = adapter.execute(plan)

    assert first.files[0].status == "written"
    assert second.files[0].status == "already_verified"
    assert first.files[0].output_sha256 == second.files[0].output_sha256


def test_batch_rejects_a_symlinked_source_tree(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "source").mkdir()
    (tmp_path / "destination").mkdir()
    (tmp_path / "source" / "escape").symlink_to(outside, target_is_directory=True)
    adapter = LocalMarkdownBatchAdapter(tmp_path)

    with pytest.raises(ValueError, match="unsafe"):
        adapter.prepare(
            source_directory="source", destination_directory="destination"
        )
