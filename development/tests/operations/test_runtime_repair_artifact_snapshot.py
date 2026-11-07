"""Preserve model-authored refusal bytes without reserializing its evidence."""
from types import SimpleNamespace

from eva.operations.runtime_repair_artifacts import RuntimeRepairArtifacts


def test_snapshot_preserves_raw_bytes_and_legacy_parsed_read(tmp_path):
    key = "a" * 64
    directory = tmp_path / key / "repair"
    directory.mkdir(parents=True)
    raw = b' { "status" : "blocked", "reason": "original input absent" }\r\n'
    (directory / "blocked.json").write_bytes(raw)
    reader = RuntimeRepairArtifacts(SimpleNamespace(config=SimpleNamespace(workspace_root=tmp_path)))
    metadata = {"workspace_key": key, "state": "succeeded"}
    parsed, snapshot = reader.read_snapshot(metadata, "blocked.json")
    assert snapshot == raw
    assert parsed == {"status": "blocked", "reason": "original input absent"}
    assert reader.read(metadata, "blocked.json") == parsed
