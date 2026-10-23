"""File-read boundaries retained by the E4.18 ownership extraction."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import file_inspection as inspection


@pytest.mark.parametrize("content", [b"", b"Eva", b"x" * (2 * 1024 * 1024 + 7)])
def test_hashes_entire_regular_file(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "source.py"
    path.write_bytes(content)
    assert inspection.hash_regular_file(path, "source.py") == (
        len(content), hashlib.sha256(content).hexdigest()
    )


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_nonregular_path_is_rejected_before_open(tmp_path: Path, monkeypatch, kind) -> None:
    path = tmp_path / "source.py"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_text("untouched")
        path.symlink_to(target)
    else:
        os.mkfifo(path)
    def unexpected_open(*args, **kwargs):
        pytest.fail("nonregular input must never be opened")
    monkeypatch.setattr(inspection.os, "open", unexpected_open)
    with pytest.raises(RuntimeError, match="not a regular file: source.py"):
        inspection.hash_regular_file(path, "source.py")


def test_replacement_between_lstat_and_open_is_rejected(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "source.py"
    replacement = tmp_path / "new.py"
    path.write_bytes(b"original")
    replacement.write_bytes(b"different bytes")
    original_open, original_close = os.open, os.close
    closed = []
    def replaced_open(target, flags):
        replacement.replace(path)
        return original_open(target, flags)
    def tracked_close(descriptor):
        closed.append(descriptor)
        return original_close(descriptor)
    monkeypatch.setattr(inspection.os, "open", replaced_open)
    monkeypatch.setattr(inspection.os, "close", tracked_close)
    with pytest.raises(RuntimeError, match="changed before read: source.py"):
        inspection.hash_regular_file(path, "source.py")
    assert len(closed) == 1


def test_change_during_read_is_rejected(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "source.py"
    path.write_bytes(b"original")
    original_read = os.read
    changed = False
    def changed_read(descriptor, length):
        nonlocal changed
        chunk = original_read(descriptor, length)
        if chunk and not changed:
            changed = True
            with path.open("ab") as stream:
                stream.write(b"changed")
        return chunk
    monkeypatch.setattr(inspection.os, "read", changed_read)
    with pytest.raises(RuntimeError, match="changed while hashing: source.py"):
        inspection.hash_regular_file(path, "source.py")


def test_read_failure_closes_descriptor(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "source.py"
    path.write_bytes(b"original")
    original_close = os.close
    closed = []
    def failed_read(*args):
        raise OSError("synthetic read failure")
    def tracked_close(descriptor):
        closed.append(descriptor)
        return original_close(descriptor)
    monkeypatch.setattr(inspection.os, "read", failed_read)
    monkeypatch.setattr(inspection.os, "close", tracked_close)
    with pytest.raises(OSError, match="synthetic read failure"):
        inspection.hash_regular_file(path, "source.py")
    assert len(closed) == 1


def test_registration_does_not_load_container_provenance() -> None:
    program = (
        "import sys; from tools import repository_inventory as i; "
        "from tools.file_inspection import hash_regular_file; "
        "assert i._hash_regular_file is hash_regular_file; "
        "assert 'tools.living_core_source_provenance' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", program], check=True, capture_output=True)


def test_provenance_keeps_the_original_import_symbol() -> None:
    from tools import living_core_source_provenance as provenance
    assert provenance._hash_regular_file is inspection.hash_regular_file


@pytest.mark.parametrize("path", [
    "src/eva/application.py", "deploy/migrations/0001.sql",
    "deploy/schemas/source.json", "deploy/requirements.lock", "deploy/alembic.ini",
])
def test_current_configured_roots_are_accepted(path) -> None:
    from tools import living_core_source_provenance as provenance
    roots = {relative for _, relative in provenance.ROOT_MAPPINGS}
    assert provenance._validated_relative_path(path, roots) == path


@pytest.mark.parametrize("path", [
    "deploy/secrets/key", "deploy/migrations-extra/0001.sql", "src-extra/app.py",
    "deploy/requirements.lock/child", "deploy/alembic.ini/child", "deploy",
    "src/../secrets/key", "/src/app.py",
])
def test_unconfigured_siblings_traversal_and_single_file_children_are_rejected(path) -> None:
    from tools import living_core_source_provenance as provenance
    roots = {relative for _, relative in provenance.ROOT_MAPPINGS}
    with pytest.raises(RuntimeError):
        provenance._validated_relative_path(path, roots)


@pytest.mark.parametrize("missing", [None, *range(5)])
def test_inventory_requires_every_exact_configured_root(missing) -> None:
    from tools import living_core_source_provenance as provenance
    records = [
        {"path": root if root in provenance.SINGLE_FILE_ROOTS else root + "/source.py",
         "size": 0, "sha256": hashlib.sha256(b"").hexdigest()}
        for index, (_, root) in enumerate(provenance.ROOT_MAPPINGS)
        if index != missing
    ]
    records.sort(key=lambda record: record["path"])
    if missing is None:
        assert provenance.validate_inventory(records) == tuple(records)
    else:
        with pytest.raises(RuntimeError, match="does not represent every approved root"):
            provenance.validate_inventory(records)
