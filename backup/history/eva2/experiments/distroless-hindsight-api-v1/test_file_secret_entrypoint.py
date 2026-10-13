from __future__ import annotations

import os
from pathlib import Path
import tempfile
from collections.abc import Iterator

import pytest

from hindsight_entrypoint import _database_url_from_private_file


@pytest.fixture
def private_tmp_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="eva2-hindsight-secret-", dir="/var/tmp") as directory:
        path = Path(directory)
        path.chmod(0o700)
        yield path


def _private_file(tmp_path: Path, value: bytes) -> Path:
    path = tmp_path / "database-dsn"
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def test_reads_one_private_absolute_database_url(private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _private_file(private_tmp_path, b"postgresql://eva:encoded@postgres:5432/hindsight\n")
    monkeypatch.setenv("HINDSIGHT_API_DATABASE_URL_FILE", str(path))
    monkeypatch.delenv("HINDSIGHT_API_DATABASE_URL", raising=False)

    assert _database_url_from_private_file() == (
        "postgresql://eva:encoded@postgres:5432/hindsight"
    )


@pytest.mark.parametrize(
    "value",
    [
        b"not-a-url",
        b"https://postgres/hindsight",
        b"postgresql://postgres:5432/",
        b"postgresql://postgres:5432/hindsight?sslmode=disable",
        b"postgresql://postgres:5432/hindsight#fragment",
        b"postgresql://postgres:5432/hindsight\nsecond-line",
    ],
)
def test_rejects_invalid_database_urls(private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: bytes) -> None:
    path = _private_file(private_tmp_path, value)
    monkeypatch.setenv("HINDSIGHT_API_DATABASE_URL_FILE", str(path))
    monkeypatch.delenv("HINDSIGHT_API_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError):
        _database_url_from_private_file()


def test_rejects_inline_url_even_with_a_valid_file(private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _private_file(private_tmp_path, b"postgresql://eva:encoded@postgres:5432/hindsight")
    monkeypatch.setenv("HINDSIGHT_API_DATABASE_URL_FILE", str(path))
    monkeypatch.setenv("HINDSIGHT_API_DATABASE_URL", "postgresql://inline/forbidden")

    with pytest.raises(RuntimeError, match="inline"):
        _database_url_from_private_file()


def test_rejects_symlink_hardlink_and_group_readable_file(private_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _private_file(private_tmp_path, b"postgresql://eva:encoded@postgres:5432/hindsight")
    symlink = private_tmp_path / "dsn-link"
    symlink.symlink_to(target)
    monkeypatch.delenv("HINDSIGHT_API_DATABASE_URL", raising=False)

    monkeypatch.setenv("HINDSIGHT_API_DATABASE_URL_FILE", str(symlink))
    with pytest.raises(RuntimeError):
        _database_url_from_private_file()

    hardlink = private_tmp_path / "dsn-hardlink"
    os.link(target, hardlink)
    monkeypatch.setenv("HINDSIGHT_API_DATABASE_URL_FILE", str(target))
    with pytest.raises(RuntimeError):
        _database_url_from_private_file()

    hardlink.unlink()
    target.chmod(0o640)
    with pytest.raises(RuntimeError):
        _database_url_from_private_file()
