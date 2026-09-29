"""Restore verified runtime archives using the existing held database writer."""
from pathlib import Path
import hashlib
import os
import stat

from tools.portable_v2_restore import _restore_archive


def validate_identity_destination(destination: Path, runtime_owner=None):
    """Check host ownership authority before any database insertion occurs."""
    owner = runtime_owner if runtime_owner is not None else {"uid": os.geteuid(), "gid": os.getegid()}
    if (not isinstance(owner, dict) or set(owner) != {"uid", "gid"}
            or any(type(value) is not int or not 0 <= value < 4294967295 for value in owner.values())):
        raise ValueError("explicit numeric identity runtime owner required")
    if os.geteuid() != 0 and (owner["uid"] != os.geteuid() or owner["gid"] != os.getegid()):
        raise ValueError("operator lacks authority to set runtime identity ownership")
    parent = destination.parent
    info = parent.lstat()
    if (not destination.is_absolute() or destination.exists() or destination.is_symlink()
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700 or parent.resolve() != parent.absolute()):
        raise ValueError("identity target must be new in an existing private operator directory")
    if owner["uid"] != os.geteuid() and any(parent.iterdir()):
        raise ValueError("foreign runtime identity requires a dedicated empty directory")
    return owner


def _preserve_identity(raw: bytes, destination: Path, runtime_owner=None) -> str:
    """Materialize an inactive identity without replacing an existing file."""
    owner = validate_identity_destination(destination, runtime_owner)
    parent = destination.parent
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(raw)
            output.flush()
            os.fchown(output.fileno(), owner["uid"], owner["gid"])
            os.fchmod(output.fileno(), 0o600)
            os.fsync(output.fileno())
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchown(parent_fd, owner["uid"], owner["gid"])
            os.fchmod(parent_fd, 0o700)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except BaseException:
        # Keep a partial file visibly inactive; never overwrite or silently retry.
        raise
    return hashlib.sha256(raw).hexdigest()


def restore_runtime_archive(connection, directory: Path, *, identity_trust,
                            identity_destination: Path, authority_reader,
                            expected_runtime_identity, object_store_root: Path,
                            key_resolver=None, identity_runtime_owner=None):
    """Caller owns the closed target transaction; identity is never activated here."""
    from tools.portable_runtime_archive import read_runtime_archive
    archive = read_runtime_archive(directory, identity_trust=identity_trust)
    if not connection.in_transaction():
        raise ValueError("runtime restore requires a caller-owned target transaction")
    validate_identity_destination(identity_destination, identity_runtime_owner)
    with connection.begin_nested():
        result = _restore_archive(connection, archive, directory,
            authority_reader=authority_reader, expected_runtime_identity=expected_runtime_identity,
            object_store_root=object_store_root, key_resolver=key_resolver)
        digest = _preserve_identity(archive["runtime_identity_bytes"], identity_destination, identity_runtime_owner)
    return {**result, "runtime_identity_sha256": digest,
            "runtime_identity_disposition": "preserved_inactive_requires_operator_activation"}
