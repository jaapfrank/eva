from __future__ import annotations

import os
from pathlib import Path

import pytest

from eva.bootstrap.secrets import (
    MAX_RUNTIME_SECRET_BYTES,
    environment_secret,
    read_runtime_secret_file,
    read_shared_runtime_file_bytes,
)


VALUE_NAME = "EVA_TEST_SECRET"
FILE_NAME = "EVA_TEST_SECRET_FILE"


def _private_secret(path: Path, content: bytes = b"secret-value\n") -> Path:
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def test_file_secret_accepts_absolute_private_runtime_owned_file(
    private_tmp_path: Path,
) -> None:
    secret = _private_secret(private_tmp_path / "secret")

    assert environment_secret(
        VALUE_NAME,
        FILE_NAME,
        environment={FILE_NAME: str(secret)},
    ) == "secret-value"


def test_file_secret_preserves_environment_file_exclusivity(
    private_tmp_path: Path,
) -> None:
    secret = _private_secret(private_tmp_path / "secret")

    with pytest.raises(RuntimeError, match="set only one"):
        environment_secret(
            VALUE_NAME,
            FILE_NAME,
            environment={VALUE_NAME: "inline", FILE_NAME: str(secret)},
        )


def test_file_secret_rejects_relative_path(private_tmp_path: Path) -> None:
    secret = _private_secret(private_tmp_path / "secret")

    with pytest.raises(RuntimeError, match="absolute path"):
        environment_secret(
            VALUE_NAME,
            FILE_NAME,
            environment={FILE_NAME: secret.name},
        )


def test_file_secret_rejects_symlink_and_non_regular_file(
    private_tmp_path: Path,
) -> None:
    secret = _private_secret(private_tmp_path / "secret")
    link = private_tmp_path / "secret-link"
    link.symlink_to(secret)

    for invalid_path in (link, private_tmp_path):
        with pytest.raises(RuntimeError, match="regular non-symlink"):
            environment_secret(
                VALUE_NAME,
                FILE_NAME,
                environment={FILE_NAME: str(invalid_path)},
            )


def test_file_secret_rejects_group_or_other_permissions(
    private_tmp_path: Path,
) -> None:
    secret = _private_secret(private_tmp_path / "secret")

    for invalid_mode in (0o640, 0o604):
        secret.chmod(invalid_mode)
        with pytest.raises(RuntimeError, match="group or other permissions"):
            environment_secret(
                VALUE_NAME,
                FILE_NAME,
                environment={FILE_NAME: str(secret)},
            )


def test_file_secret_rejects_execute_only_owner_mode(
    private_tmp_path: Path,
) -> None:
    secret = _private_secret(private_tmp_path / "secret")
    secret.chmod(0o100)

    with pytest.raises(RuntimeError, match="owner read permission"):
        environment_secret(
            VALUE_NAME,
            FILE_NAME,
            environment={FILE_NAME: str(secret)},
        )


def test_file_secret_rejects_owner_other_than_runtime_effective_uid(
    private_tmp_path: Path,
) -> None:
    secret = _private_secret(private_tmp_path / "secret")

    with pytest.raises(RuntimeError, match="runtime effective UID"):
        environment_secret(
            VALUE_NAME,
            FILE_NAME,
            environment={FILE_NAME: str(secret)},
            effective_uid=os.geteuid() + 1,
        )


def test_file_secret_rejects_empty_oversized_and_non_utf8_files(
    private_tmp_path: Path,
) -> None:
    cases = (
        (b" \n", "empty"),
        (b"x" * (MAX_RUNTIME_SECRET_BYTES + 1), "too large"),
        (b"\xff", "UTF-8"),
    )

    for index, (content, expected_error) in enumerate(cases):
        secret = _private_secret(private_tmp_path / f"secret-{index}", content)
        with pytest.raises(RuntimeError, match=expected_error):
            environment_secret(
                VALUE_NAME,
                FILE_NAME,
                environment={FILE_NAME: str(secret)},
            )


def test_file_secret_accepts_exact_size_limit(private_tmp_path: Path) -> None:
    secret = _private_secret(
        private_tmp_path / "secret",
        b"x" * MAX_RUNTIME_SECRET_BYTES,
    )

    assert len(
        environment_secret(
            VALUE_NAME,
            FILE_NAME,
            environment={FILE_NAME: str(secret)},
        )
    ) == MAX_RUNTIME_SECRET_BYTES


def test_fifo_replacement_is_opened_nonblocking_and_rejected(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    regular = _private_secret(private_tmp_path / "regular")
    fifo = private_tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    regular_metadata = os.lstat(regular)
    real_lstat = os.lstat
    observed_flags: list[int] = []
    real_open = os.open

    def swapped_lstat(
        path: os.PathLike[str] | str,
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        if not args and not kwargs and Path(path) == fifo:
            return regular_metadata
        return real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

    def observed_open(
        path: os.PathLike[str] | str,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        if not args and not kwargs and Path(path) == fifo:
            observed_flags.append(flags)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    with monkeypatch.context() as patch:
        patch.setattr(os, "lstat", swapped_lstat)
        patch.setattr(os, "open", observed_open)
        with pytest.raises(RuntimeError, match="regular non-symlink"):
            read_runtime_secret_file(str(fifo), setting_name=FILE_NAME)
    assert observed_flags and observed_flags[0] & os.O_NONBLOCK


def test_file_secret_rejects_metadata_change_during_read(
    monkeypatch: pytest.MonkeyPatch,
    private_tmp_path: Path,
) -> None:
    secret = _private_secret(private_tmp_path / "secret")
    real_read = os.read
    changed = False

    def changing_read(descriptor: int, maximum: int) -> bytes:
        nonlocal changed
        chunk = real_read(descriptor, maximum)
        if chunk and not changed:
            changed = True
            secret.chmod(0o400)
        return chunk

    monkeypatch.setattr(os, "read", changing_read)

    with pytest.raises(RuntimeError, match="changed while it was read"):
        read_runtime_secret_file(str(secret), setting_name=FILE_NAME)


def test_shared_lease_requires_exact_owner_group_mode_and_single_link(
    private_tmp_path: Path,
) -> None:
    lease = private_tmp_path / "lease"
    lease.write_bytes(b'{"contract":"EvaModelAccessLease/v1"}')
    lease.chmod(0o640)
    kwargs = {
        "setting_name": "model lease",
        "owner_uid": os.geteuid(),
        "reader_gid": os.getegid(),
        "required_mode": 0o640,
    }

    assert read_shared_runtime_file_bytes(str(lease), **kwargs).startswith(b"{")

    lease.chmod(0o600)
    with pytest.raises(RuntimeError, match="permissions differ"):
        read_shared_runtime_file_bytes(str(lease), **kwargs)

    lease.chmod(0o640)
    hardlink = private_tmp_path / "lease-hardlink"
    os.link(lease, hardlink)
    with pytest.raises(RuntimeError, match="exactly one hard link"):
        read_shared_runtime_file_bytes(str(lease), **kwargs)
