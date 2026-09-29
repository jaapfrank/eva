"""Restore verified runtime archives using the existing held database writer."""
from pathlib import Path
import hashlib
import os
import stat

from tools.portable_v2_restore import _restore_archive


def _preserve_identity(raw: bytes, destination: Path) -> str:
    """Materialize an inactive identity without replacing an existing file."""
    parent = destination.parent
    info = parent.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or parent.resolve() != parent.absolute()):
        raise ValueError("identity target must be an existing private directory without links")
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        # Keep a partial file visibly inactive; never overwrite or silently retry.
        raise
    return hashlib.sha256(raw).hexdigest()


def restore_runtime_archive(connection, directory: Path, *, identity_trust,
                            identity_destination: Path, authority_reader,
                            expected_runtime_identity, object_store_root: Path,
                            key_resolver=None):
    """Caller owns the closed target transaction; identity is never activated here."""
    from tools.portable_runtime_archive import read_runtime_archive
    archive = read_runtime_archive(directory, identity_trust=identity_trust)
    if not connection.in_transaction():
        raise ValueError("runtime restore requires a caller-owned target transaction")
    with connection.begin_nested():
        result = _restore_archive(connection, archive, directory,
            authority_reader=authority_reader, expected_runtime_identity=expected_runtime_identity,
            object_store_root=object_store_root, key_resolver=key_resolver)
        digest = _preserve_identity(archive["runtime_identity_bytes"], identity_destination)
    return {**result, "runtime_identity_sha256": digest,
            "runtime_identity_disposition": "preserved_inactive_requires_operator_activation"}
