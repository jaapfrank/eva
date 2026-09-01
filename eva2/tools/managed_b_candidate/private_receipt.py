"""Durably publish one new owner-private canonical JSON receipt.

The final pathname is never exposed with partial contents.  A fully fsynced
temporary inode is hard-linked into place without replacement, followed by
directory fsyncs for both publication and temporary-name removal.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat


_write = os.write
_geteuid = os.geteuid


class PrivateReceiptError(RuntimeError):
    """A private receipt destination or durable write is unsafe."""


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def write_new_private_json(path: Path, value: object, *, label: str) -> bytes:
    """Write one canonical receipt without overwrite or partial final state."""

    if (
        not path.is_absolute()
        or ".." in path.parts
        or path.name in {"", ".", ".."}
        or "/" in path.name
    ):
        raise PrivateReceiptError(f"{label} path must be explicit and absolute")
    parent = path.parent
    try:
        if parent.resolve(strict=True) != parent:
            raise PrivateReceiptError(f"{label} parent must not use links")
        directory = os.open(
            parent,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except (OSError, RuntimeError) as error:
        if isinstance(error, PrivateReceiptError):
            raise
        raise PrivateReceiptError(f"{label} parent is inaccessible") from error
    temporary_name = f".{path.name}.{secrets.token_hex(16)}.tmp"
    temporary_created = False
    published = False
    raw = canonical_json(value)
    try:
        metadata = os.fstat(directory)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != _geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise PrivateReceiptError(f"{label} parent must be private and owned")
        try:
            os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise PrivateReceiptError(f"{label} must be one new file")
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory,
        )
        temporary_created = True
        try:
            view = memoryview(raw)
            while view:
                written = _write(descriptor, view)
                if written <= 0:
                    raise PrivateReceiptError(f"{label} write did not progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(
            temporary_name,
            path.name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
            follow_symlinks=False,
        )
        published = True
        os.fsync(directory)
        os.unlink(temporary_name, dir_fd=directory)
        temporary_created = False
        os.fsync(directory)
    except PrivateReceiptError:
        raise
    except OSError as error:
        raise PrivateReceiptError(f"{label} could not be persisted") from error
    finally:
        if temporary_created:
            try:
                os.unlink(temporary_name, dir_fd=directory)
                os.fsync(directory)
            except OSError:
                pass
        os.close(directory)
    if not published:
        raise PrivateReceiptError(f"{label} was not published")
    return raw


__all__ = [
    "PrivateReceiptError",
    "canonical_json",
    "write_new_private_json",
]
