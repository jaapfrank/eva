"""Read-only hashing of regular worktree files with descriptor identity checks.

No container access, repository traversal or permission to change source files.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat


def hash_regular_file(path: Path, relative_path: str) -> tuple[int, str]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError(f"Worktree application path is not a regular file: {relative_path}")
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size) != (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
        ):
            raise RuntimeError(f"Worktree application file changed before read: {relative_path}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_ctime_ns,
    ):
        raise RuntimeError(f"Worktree application file changed while hashing: {relative_path}")
    return before.st_size, digest.hexdigest()
