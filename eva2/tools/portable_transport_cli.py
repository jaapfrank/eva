"""Small no-secret-command-line operator route for encrypted portable archives."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from eva.application.portable_archive import PortableArchiveError
from tools.portable_transport import (
    PortableTransportError,
    open_portable_transport,
    seal_portable_archive,
)


_MAX_PASSPHRASE_FILE_BYTES = 4097


def _encode(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eva-portable-transport")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("seal", "open"):
        command = commands.add_parser(name)
        command.add_argument("--source", type=Path, required=True)
        command.add_argument("--destination", type=Path, required=True)
        command.add_argument("--passphrase-file", type=Path, required=True)
    return parser


def _private_passphrase(path: Path) -> str:
    try:
        absolute = path.absolute()
        metadata = absolute.lstat()
    except OSError as exc:
        raise PortableTransportError("passphrase file cannot be inspected") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
        or not 1 <= metadata.st_size <= _MAX_PASSPHRASE_FILE_BYTES
    ):
        raise PortableTransportError(
            "passphrase file must be an owner-only single-link regular file"
        )
    try:
        raw = absolute.read_bytes()
        after = absolute.lstat()
    except OSError as exc:
        raise PortableTransportError("passphrase file cannot be read") from exc
    if (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise PortableTransportError("passphrase file changed while being read")
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if raw.endswith(b"\r") or b"\n" in raw or b"\r" in raw:
        raise PortableTransportError("passphrase file must contain exactly one line")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PortableTransportError("passphrase file is not UTF-8") from exc


def _reader_validator(archive: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    reader = root / "tools" / "read_eva_portable_archive.py"
    result = subprocess.run(
        [sys.executable, "-I", str(reader), str(archive), "--validate-only"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise PortableTransportError("generic reader rejected portable archive")


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        passphrase = _private_passphrase(arguments.passphrase_file)
        if arguments.command == "seal":
            receipt = seal_portable_archive(
                arguments.source,
                arguments.destination,
                passphrase=passphrase,
                validator=_reader_validator,
            )
        else:
            receipt = open_portable_transport(
                arguments.source,
                arguments.destination,
                passphrase=passphrase,
                validator=_reader_validator,
            )
    except (OSError, PortableArchiveError, PortableTransportError, ValueError) as exc:
        print(_encode({"status": "failed", "error": str(exc)}))
        return 2
    print(_encode({"status": "passed", **receipt}))
    return 0


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint
    sys.exit(main())
