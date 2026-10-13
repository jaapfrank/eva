from __future__ import annotations

import hashlib
import json
import os
import errno
from pathlib import Path

import pytest

from tools.living_core_source_provenance import (
    CommandResult,
    EXPECTED_IMAGE_REF,
    NONCLAIMS,
    ROOT_MAPPINGS,
    TOOL_VERSION,
    _receipt_digest,
    collect_container_snapshot,
    compare_inventories,
    inventory_worktree,
    parse_selected_metadata,
    validate_receipt,
    validate_inventory,
)


CONTAINER_ID = "a" * 64
IMAGE_ID = "sha256:" + ("b" * 64)


def _metadata(*, container_id: str = CONTAINER_ID, image_ref: str = EXPECTED_IMAGE_REF) -> str:
    return json.dumps(
        [container_id, IMAGE_ID, image_ref, "/eva2-living-core-b", "running", 0]
    )


def _record(path: str, content: bytes) -> dict[str, object]:
    return {"path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _complete_container_inventory() -> list[dict[str, object]]:
    return sorted(
        (
            _record(
                relative if "." in Path(relative).name else f"{relative}/fixture.txt",
                relative.encode(),
            )
            for _, relative in ROOT_MAPPINGS
        ),
        key=lambda record: str(record["path"]),
    )


def _complete_fixture(root: Path) -> None:
    for _, relative in ROOT_MAPPINGS:
        path = root / relative
        if "." in Path(relative).name:
            path.write_bytes(relative.encode())
        else:
            path.mkdir(parents=True)
            (path / "fixture.txt").write_bytes(relative.encode())


def test_inventory_worktree_and_compare_include_worktree_only(tmp_path: Path) -> None:
    _complete_fixture(tmp_path)
    local = inventory_worktree(tmp_path)
    deployed = tuple(record for record in local if record["path"] != "src/fixture.txt")

    comparison = compare_inventories(deployed, local)

    by_path = {record["path"]: record for record in comparison}
    assert by_path["src/fixture.txt"]["state"] == "worktree_only"
    assert by_path["requirements.lock"]["state"] == "identical"


def test_inventory_worktree_fails_closed_on_symlink(tmp_path: Path) -> None:
    _complete_fixture(tmp_path)
    (tmp_path / "src" / "link").symlink_to(tmp_path / "requirements.lock")

    with pytest.raises(RuntimeError, match="symlink"):
        inventory_worktree(tmp_path)


def test_inventory_worktree_fails_closed_on_nonregular_file(tmp_path: Path) -> None:
    _complete_fixture(tmp_path)
    try:
        os.mkfifo(tmp_path / "src" / "pipe")
    except OSError as error:
        if error.errno == errno.EOPNOTSUPP:
            pytest.skip("the mounted temporary-filesystem does not support fifos")
        raise

    with pytest.raises(RuntimeError, match="nonregular"):
        inventory_worktree(tmp_path)


def test_validate_inventory_rejects_traversal_duplicate_and_noncanonical_order() -> None:
    valid = _record("src/one.py", b"one")
    with pytest.raises(RuntimeError, match="traversal"):
        validate_inventory([{**valid, "path": "src/../requirements.lock"}])
    with pytest.raises(RuntimeError, match="duplicate"):
        validate_inventory([valid, valid])
    with pytest.raises(RuntimeError, match="deterministic"):
        validate_inventory([_record("src/z.py", b"z"), _record("src/a.py", b"a")])
    with pytest.raises(RuntimeError, match="single-file root"):
        validate_inventory([{**valid, "path": "requirements.lock/nested"}])


@pytest.mark.parametrize(
    "malformed",
    (
        "not-json",
        "{}",
        '[{"path":"src/a.py","size":1,"sha256":"bad"}]',
        '[{"path":"outside/a.py","size":1,"sha256":"' + ("a" * 64) + '"}]',
    ),
)
def test_validate_inventory_rejects_malformed_output(malformed: str) -> None:
    with pytest.raises(RuntimeError):
        validate_inventory(malformed)


def test_parse_selected_metadata_rejects_image_or_container_mismatch() -> None:
    with pytest.raises(RuntimeError, match="image reference"):
        parse_selected_metadata(_metadata(image_ref="eva2-living-core:wrong"))
    wrong_name = json.dumps(
        [CONTAINER_ID, IMAGE_ID, EXPECTED_IMAGE_REF, "/eva2-living-core-a", "running", 0]
    )
    with pytest.raises(RuntimeError, match="different container"):
        parse_selected_metadata(wrong_name)


def test_collect_snapshot_uses_selected_inspect_and_injected_inventory() -> None:
    expected_inventory = _complete_container_inventory()
    inventory = json.dumps(expected_inventory, separators=(",", ":"))
    calls: list[tuple[str, ...]] = []
    outputs = [_metadata(), inventory, _metadata()]

    def runner(command: tuple[str, ...]) -> CommandResult:
        calls.append(command)
        return CommandResult(0, outputs.pop(0), "")

    subject, records = collect_container_snapshot(runner=runner)

    assert subject["image_ref"] == EXPECTED_IMAGE_REF
    assert records == tuple(expected_inventory)
    assert len(calls) == 3
    assert all(command[0] == "/usr/bin/docker" for command in calls)
    assert calls[0][1:4] == ("inspect", "--type", "container")
    assert calls[1][1:3] == ("exec", "eva2-living-core-b")
    assert calls[2][1:4] == ("inspect", "--type", "container")
    rendered = " ".join(" ".join(command) for command in calls)
    assert "Config.Env" not in rendered
    assert "Mounts" not in rendered


def test_collect_snapshot_fails_on_command_failure_or_metadata_race() -> None:
    def failed_runner(command: tuple[str, ...]) -> CommandResult:
        del command
        return CommandResult(1, "", "denied")

    with pytest.raises(RuntimeError, match="failed with exit 1"):
        collect_container_snapshot(runner=failed_runner)

    inventory = json.dumps(_complete_container_inventory())
    outputs = [_metadata(), inventory, _metadata(container_id="c" * 64)]

    def racing_runner(command: tuple[str, ...]) -> CommandResult:
        del command
        return CommandResult(0, outputs.pop(0), "")

    with pytest.raises(RuntimeError, match="changed during"):
        collect_container_snapshot(runner=racing_runner)


def test_receipt_validator_is_deterministic_and_detects_tampering() -> None:
    digest = "d" * 64
    receipt: dict[str, object] = {
        "schema_version": 1,
        "captured_at": "2026-08-24T12:35:00+02:00",
        "subject": {
            "container_id": CONTAINER_ID,
            "image_id": IMAGE_ID,
            "image_ref": EXPECTED_IMAGE_REF,
            "name": "eva2-living-core-b",
            "status": "running",
            "restart_count": 0,
        },
        "git_head": "e" * 40,
        "tool": {
            "path": "tools/living_core_source_provenance.py",
            "version": TOOL_VERSION,
            "sha256": "f" * 64,
        },
        "roots": [
            {"container": container, "worktree": f"{worktree}"}
            for container, worktree in ROOT_MAPPINGS
        ],
        "files": [
            {
                "path": "src/eva.py",
                "state": "identical",
                "container": {"size": 3, "sha256": digest},
                "worktree": {"size": 3, "sha256": digest},
            }
        ],
        "counts": {
            "container_only": 0,
            "different": 0,
            "identical": 1,
            "worktree_only": 0,
            "total": 1,
        },
        "nonclaims": list(NONCLAIMS),
    }
    receipt["receipt_sha256"] = _receipt_digest(receipt)

    validate_receipt(receipt)
    receipt["counts"] = {**receipt["counts"], "identical": 0}  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="counts"):
        validate_receipt(receipt)
