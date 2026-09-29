"""Focused composition checks; database helpers are replaced with byte writers."""
import json
import os
from pathlib import Path

import pytest

from tools.installation_source_snapshot import collect_installation_source_snapshot, SourceSnapshotError


@pytest.fixture
def collection(tmp_path):
    tmp_path.chmod(0o700)
    source = tmp_path / "native"
    source.mkdir()
    (source / "empty").mkdir()
    (source / "artifact").write_bytes(b"retained bytes")
    gateway = tmp_path / "gateway.sqlite"
    gateway.write_bytes(b"gateway")
    calls = []

    def guard():
        calls.append("guard")

    def dump(container, database, destination, *, portable):
        assert portable is True
        calls.append(database)
        destination.write_bytes(database.encode())

    def snapshot(source, destination, *, verify_quiescence):
        verify_quiescence()
        destination.write_bytes(source.read_bytes())
        verify_quiescence()

    return dict(destination=tmp_path / "collected", databases={
        "core": {"container": "trusted-core", "database": "eva2"},
        "memory": {"container": "trusted-memory", "database": "hindsight"}},
        gateway_database=gateway, sources={"documents": source}, verify_quiescence=guard,
        dump_writer=dump, gateway_snapshot=snapshot), calls


def test_complete_collection_preserves_empty_directories_and_private_inventory(collection):
    arguments, calls = collection
    receipt = collect_installation_source_snapshot(**arguments)
    destination = arguments["destination"]
    assert receipt == json.loads((destination / "source-snapshot.json").read_bytes())
    assert receipt["entries"]["sources/documents/empty"] == {"kind": "directory"}
    assert receipt["entries"]["sources/documents/artifact"]["size"] == len(b"retained bytes")
    assert "eva2" in calls and "hindsight" in calls and calls.count("guard") >= 8
    assert "trusted-core" not in json.dumps(receipt)
    for path in (destination, *destination.rglob("*")):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    with pytest.raises(FileExistsError):
        collect_installation_source_snapshot(**arguments)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_native_entries_are_refused_and_partial_is_retained(collection, kind):
    arguments, _ = collection
    source = arguments["sources"]["documents"]
    if kind == "symlink":
        (source / "unsafe").symlink_to(source / "artifact")
    elif kind == "hardlink":
        os.link(source / "artifact", source / "unsafe")
    else:
        os.mkfifo(source / "unsafe")
    with pytest.raises(SourceSnapshotError):
        collect_installation_source_snapshot(**arguments)
    assert arguments["destination"].is_dir()
    assert not (arguments["destination"] / "source-snapshot.json").exists()


def test_source_mutation_during_dump_denies_completion(collection):
    arguments, _ = collection
    writer = arguments["dump_writer"]

    def mutate(*args, **kwargs):
        writer(*args, **kwargs)
        (arguments["sources"]["documents"] / "artifact").write_bytes(b"changed")

    arguments["dump_writer"] = mutate
    with pytest.raises(SourceSnapshotError):
        collect_installation_source_snapshot(**arguments)
    assert (arguments["destination"] / "databases/core.dump").exists()
    assert not (arguments["destination"] / "source-snapshot.json").exists()


def test_lost_fence_retains_partial_without_completion_receipt(collection):
    arguments, _ = collection
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise ValueError("fence lost")

    arguments["verify_quiescence"] = guard
    with pytest.raises(SourceSnapshotError):
        collect_installation_source_snapshot(**arguments)
    assert (arguments["destination"] / "databases/core.dump").exists()
    assert not (arguments["destination"] / "source-snapshot.json").exists()
