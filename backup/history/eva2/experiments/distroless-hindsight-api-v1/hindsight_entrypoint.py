"""Start Hindsight with a database DSN read from one private file."""

from __future__ import annotations

import os
from pathlib import Path
import stat
from urllib.parse import urlsplit


FILE_ENV = "HINDSIGHT_API_DATABASE_URL_FILE"
VALUE_ENV = "HINDSIGHT_API_DATABASE_URL"
MAX_BYTES = 4096
HINDSIGHT = "/app/api/.venv/bin/hindsight-api"


def _database_url_from_private_file() -> str:
    if os.environ.get(VALUE_ENV):
        raise RuntimeError("inline Hindsight database URL is forbidden")
    raw_path = os.environ.get(FILE_ENV, "")
    path = Path(raw_path)
    if not raw_path or not path.is_absolute():
        raise RuntimeError("Hindsight database URL file must be absolute")
    named = os.lstat(path)
    if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode):
        raise RuntimeError("Hindsight database URL file must be a regular file")
    if named.st_uid != os.geteuid() or stat.S_IMODE(named.st_mode) not in {0o400, 0o600}:
        raise RuntimeError("Hindsight database URL file ownership or mode differs")
    if named.st_nlink != 1 or not 0 < named.st_size <= MAX_BYTES:
        raise RuntimeError("Hindsight database URL file link count or size differs")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_mode, opened.st_size) != (
            named.st_dev,
            named.st_ino,
            named.st_uid,
            named.st_mode,
            named.st_size,
        ):
            raise RuntimeError("Hindsight database URL file changed during open")
        payload = os.read(descriptor, MAX_BYTES + 1)
        if os.read(descriptor, 1):
            raise RuntimeError("Hindsight database URL file is too large")
    finally:
        os.close(descriptor)
    try:
        value = payload.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise RuntimeError("Hindsight database URL file is not UTF-8") from error
    if "\n" in value or "\r" in value:
        raise RuntimeError("Hindsight database URL file must contain one line")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"postgresql", "postgres"}
        or not parsed.hostname
        or not parsed.path.removeprefix("/")
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("Hindsight database URL is invalid")
    return value


def main() -> None:
    os.environ[VALUE_ENV] = _database_url_from_private_file()
    os.execv(HINDSIGHT, [HINDSIGHT])


if __name__ == "__main__":
    main()
