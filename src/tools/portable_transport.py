"""Encrypted whole-archive transport for validated Eva portable exports.

This module deliberately does *not* restore an archive into PostgreSQL.  It
moves a already-validated portable archive between machines as one encrypted
file, then recreates the exact archive directory only after authentication and
an independent archive validator succeed.  Database import remains a separate
operator/product gate.

The cryptographic primitives come from Eva's pinned ``cryptography`` package:
scrypt for a passphrase-derived transport key and streaming AES-256-GCM for
authenticated encryption.  No secret, key or passphrase is written to the
transport header or a receipt.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import struct
import tarfile
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from eva.application.portable_archive import PortableArchiveError, canonical_json_bytes


MAGIC = b"EVAPT001"
HEADER_LENGTH_BYTES = 4
TAG_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32
SALT_BYTES = 32
CHUNK_BYTES = 1024 * 1024
MAX_HEADER_BYTES = 16_384
MAX_ARCHIVE_FILES = 100_000
MAX_ARCHIVE_BYTES = 1_073_741_824
_SCRYPT_N = 32_768
_SCRYPT_R = 8
_SCRYPT_P = 1
_SHA256_RE = frozenset("0123456789abcdef")


class PortableTransportError(PortableArchiveError):
    """An encrypted transport is malformed, unauthenticated or unsafe."""


def _require_regular(path: Path, *, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PortableTransportError(f"{label} cannot be inspected") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise PortableTransportError(f"{label} must be a single-link regular file")
    return metadata


def _safe_relative_path(path: str) -> tuple[str, ...]:
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or any(ord(character) < 0x20 for character in path)
    ):
        raise PortableTransportError("transport member path is unsafe")
    parts = tuple(path.split("/"))
    if any(not part or part in {".", ".."} for part in parts):
        raise PortableTransportError("transport member path has an unsafe segment")
    return parts


def _source_files(root: Path) -> Iterator[tuple[str, Path, os.stat_result]]:
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise PortableTransportError("portable archive cannot be inspected") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink():
        raise PortableTransportError("portable archive must be a real directory")

    seen_files = 0
    total_bytes = 0

    def visit(directory: Path, prefix: tuple[str, ...]) -> Iterator[
        tuple[str, Path, os.stat_result]
    ]:
        nonlocal seen_files, total_bytes
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name.encode("utf-8"))
        except OSError as exc:
            raise PortableTransportError("portable archive directory cannot be read") from exc
        for entry in entries:
            if entry.name in {".", ".."} or "/" in entry.name or "\\" in entry.name:
                raise PortableTransportError("portable archive contains an unsafe path")
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise PortableTransportError("portable archive member cannot be inspected") from exc
            relative_parts = (*prefix, entry.name)
            relative = "/".join(relative_parts)
            _safe_relative_path(relative)
            candidate = Path(entry.path)
            if stat.S_ISDIR(metadata.st_mode):
                yield from visit(candidate, relative_parts)
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise PortableTransportError(
                    "portable archive may contain only single-link regular files"
                )
            seen_files += 1
            total_bytes += metadata.st_size
            if seen_files > MAX_ARCHIVE_FILES or total_bytes > MAX_ARCHIVE_BYTES:
                raise PortableTransportError("portable archive exceeds transport limits")
            yield relative, candidate, metadata

    yield from visit(root, ())


def _write_plaintext_tar(source: Path, destination: Path) -> tuple[int, str]:
    try:
        with destination.open("xb") as raw:
            with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for relative, path, metadata in _source_files(source):
                    member = tarfile.TarInfo(name=relative)
                    member.size = metadata.st_size
                    member.mode = 0o600
                    member.mtime = 0
                    member.uid = 0
                    member.gid = 0
                    member.uname = ""
                    member.gname = ""
                    try:
                        with path.open("rb") as input_handle:
                            before = os.fstat(input_handle.fileno())
                            if (
                                before.st_dev,
                                before.st_ino,
                                before.st_size,
                                before.st_mtime_ns,
                            ) != (
                                metadata.st_dev,
                                metadata.st_ino,
                                metadata.st_size,
                                metadata.st_mtime_ns,
                            ):
                                raise PortableTransportError(
                                    "portable archive member changed while being packed"
                                )
                            tar.addfile(member, input_handle)
                            after = os.fstat(input_handle.fileno())
                    except OSError as exc:
                        raise PortableTransportError(
                            "portable archive member cannot be packed"
                        ) from exc
                    if (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                    ) != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    ):
                        raise PortableTransportError(
                            "portable archive member changed while being packed"
                        )
            raw.flush()
            os.fsync(raw.fileno())
    except (OSError, tarfile.TarError) as exc:
        raise PortableTransportError("portable archive could not be packed") from exc
    metadata = _require_regular(destination, label="packed portable archive")
    if metadata.st_size > MAX_ARCHIVE_BYTES + (MAX_ARCHIVE_FILES * 1024) + 10_240:
        raise PortableTransportError("packed portable archive exceeds transport limits")
    digest = hashlib.sha256()
    with destination.open("rb") as packed:
        while chunk := packed.read(CHUNK_BYTES):
            digest.update(chunk)
    after = _require_regular(destination, label="packed portable archive")
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
        raise PortableTransportError("packed portable archive changed while being hashed")
    return metadata.st_size, digest.hexdigest()


def _passphrase_bytes(passphrase: str) -> bytes:
    if not isinstance(passphrase, str):
        raise TypeError("transport passphrase must be text")
    if unicodedata.normalize("NFC", passphrase) != passphrase:
        raise PortableTransportError("transport passphrase must already be NFC")
    encoded = passphrase.encode("utf-8")
    if not 12 <= len(encoded) <= 4096:
        raise PortableTransportError("transport passphrase must contain 12..4096 UTF-8 bytes")
    return encoded


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if len(salt) != SALT_BYTES:
        raise PortableTransportError("transport salt length is invalid")
    try:
        return hashlib.scrypt(
            _passphrase_bytes(passphrase),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=KEY_BYTES,
            maxmem=128 * 1024 * 1024,
        )
    except ValueError as exc:
        raise PortableTransportError("transport key derivation failed") from exc


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64_exact(value: object, *, size: int, label: str) -> bytes:
    if not isinstance(value, str):
        raise PortableTransportError(f"transport {label} is not text")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise PortableTransportError(f"transport {label} is invalid base64") from exc
    if len(raw) != size or _b64(raw) != value:
        raise PortableTransportError(f"transport {label} has an invalid length")
    return raw


def _header(*, salt: bytes, nonce: bytes, plaintext_bytes: int, plaintext_sha256: str) -> bytes:
    if plaintext_bytes < 1024 or plaintext_bytes > MAX_ARCHIVE_BYTES + (MAX_ARCHIVE_FILES * 1024) + 10_240:
        raise PortableTransportError("packed portable archive length is invalid")
    if len(plaintext_sha256) != 64 or any(character not in _SHA256_RE for character in plaintext_sha256):
        raise PortableTransportError("packed portable archive digest is invalid")
    return canonical_json_bytes(
        {
            "cipher": {"algorithm": "AES-256-GCM", "nonce_b64": _b64(nonce), "tag_bytes": TAG_BYTES},
            "contract": "EvaPortableTransport/v1",
            "kdf": {
                "algorithm": "scrypt",
                "dklen": KEY_BYTES,
                "n": _SCRYPT_N,
                "p": _SCRYPT_P,
                "r": _SCRYPT_R,
                "salt_b64": _b64(salt),
            },
            "plaintext": {
                "byte_length": plaintext_bytes,
                "format": "EvaPortableArchiveTar/v1",
                "sha256": plaintext_sha256,
            },
        }
    )


def _parse_header(raw: bytes) -> tuple[bytes, bytes, int, str]:
    if not 1 <= len(raw) <= MAX_HEADER_BYTES:
        raise PortableTransportError("transport header length is invalid")
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortableTransportError("transport header is not valid JSON") from exc
    if canonical_json_bytes(value) != raw or not isinstance(value, dict):
        raise PortableTransportError("transport header is not canonical")
    if set(value) != {"cipher", "contract", "kdf", "plaintext"} or value["contract"] != "EvaPortableTransport/v1":
        raise PortableTransportError("transport header contract is invalid")
    cipher = value["cipher"]
    kdf = value["kdf"]
    plaintext = value["plaintext"]
    if not isinstance(cipher, dict) or not isinstance(kdf, dict) or not isinstance(plaintext, dict):
        raise PortableTransportError("transport header sections are invalid")
    if cipher != {"algorithm": "AES-256-GCM", "nonce_b64": cipher.get("nonce_b64"), "tag_bytes": TAG_BYTES}:
        raise PortableTransportError("transport cipher parameters are invalid")
    if kdf != {
        "algorithm": "scrypt",
        "dklen": KEY_BYTES,
        "n": _SCRYPT_N,
        "p": _SCRYPT_P,
        "r": _SCRYPT_R,
        "salt_b64": kdf.get("salt_b64"),
    }:
        raise PortableTransportError("transport key derivation parameters are invalid")
    if set(plaintext) != {"byte_length", "format", "sha256"} or plaintext.get("format") != "EvaPortableArchiveTar/v1":
        raise PortableTransportError("transport plaintext parameters are invalid")
    byte_length = plaintext.get("byte_length")
    digest = plaintext.get("sha256")
    if (
        isinstance(byte_length, bool)
        or not isinstance(byte_length, int)
        or not 1024 <= byte_length <= MAX_ARCHIVE_BYTES + (MAX_ARCHIVE_FILES * 1024) + 10_240
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in _SHA256_RE for character in digest)
    ):
        raise PortableTransportError("transport plaintext metadata is invalid")
    return (
        _b64_exact(kdf["salt_b64"], size=SALT_BYTES, label="salt"),
        _b64_exact(cipher["nonce_b64"], size=NONCE_BYTES, label="nonce"),
        byte_length,
        digest,
    )


@contextmanager
def _private_staging(destination: Path) -> Iterator[Path]:
    destination = destination.absolute()
    parent = destination.parent
    if not parent.is_dir() or parent.is_symlink():
        raise PortableTransportError("transport destination parent is unsafe")
    staging = parent / f".{destination.name}.transport-staging-{uuid4()}"
    try:
        staging.mkdir(mode=0o700)
    except OSError as exc:
        raise PortableTransportError("transport staging directory cannot be created") from exc
    try:
        yield staging
    finally:
        if staging.exists() and staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)


@contextmanager
def _reserved_empty_directory(destination: Path) -> Iterator[None]:
    """Reserve an absent restore target so rename cannot replace another archive."""

    try:
        destination.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise PortableTransportError("restored archive destination already exists") from exc
    except OSError as exc:
        raise PortableTransportError("restored archive destination cannot be reserved") from exc
    try:
        yield
    finally:
        try:
            metadata = destination.lstat()
            if stat.S_ISDIR(metadata.st_mode) and not destination.is_symlink():
                destination.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            # A successful publication replaces this reservation with a non-empty
            # archive.  Never delete anything we did not create as empty.
            pass


def _sha256_file(path: Path, *, label: str) -> str:
    metadata = _require_regular(path, label=label)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise PortableTransportError(f"{label} cannot be read") from exc
    after = _require_regular(path, label=label)
    if (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise PortableTransportError(f"{label} changed while being hashed")
    return digest.hexdigest()


def _encrypt_file(*, source: Path, destination: Path, key: bytes, nonce: bytes, aad: bytes) -> None:
    source_metadata = _require_regular(source, label="packed portable archive")
    try:
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(aad)
        with source.open("rb") as input_handle, destination.open("xb") as output_handle:
            while chunk := input_handle.read(CHUNK_BYTES):
                output_handle.write(encryptor.update(chunk))
            output_handle.write(encryptor.finalize())
            output_handle.write(encryptor.tag)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        after = _require_regular(source, label="packed portable archive")
    except OSError as exc:
        raise PortableTransportError("transport ciphertext could not be written") from exc
    if (source_metadata.st_dev, source_metadata.st_ino, source_metadata.st_size, source_metadata.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise PortableTransportError("packed portable archive changed while being encrypted")


def seal_portable_archive(
    source: Path,
    destination: Path,
    *,
    passphrase: str,
    validator: Callable[[Path], None],
) -> dict[str, str | int]:
    """Validate and encrypt one portable archive directory as one transport file."""

    source = source.absolute()
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise PortableTransportError("transport destination already exists")
    validator(source)
    with _private_staging(destination) as staging:
        packed = staging / "portable.tar"
        plaintext_bytes, plaintext_sha256 = _write_plaintext_tar(source, packed)
        salt = os.urandom(SALT_BYTES)
        nonce = os.urandom(NONCE_BYTES)
        header = _header(
            salt=salt,
            nonce=nonce,
            plaintext_bytes=plaintext_bytes,
            plaintext_sha256=plaintext_sha256,
        )
        if len(header) > MAX_HEADER_BYTES:
            raise PortableTransportError("transport header exceeds limit")
        staged_transport = staging / "transport.eva2pt"
        with staged_transport.open("xb") as output:
            output.write(MAGIC)
            output.write(struct.pack(">I", len(header)))
            output.write(header)
        _encrypt_file(
            source=packed,
            destination=staging / "ciphertext",
            key=_derive_key(passphrase, salt),
            nonce=nonce,
            aad=header,
        )
        with staged_transport.open("ab") as output, (staging / "ciphertext").open("rb") as ciphertext:
            while chunk := ciphertext.read(CHUNK_BYTES):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        transport_sha256 = _sha256_file(staged_transport, label="transport output")
        try:
            os.link(staged_transport, destination, follow_symlinks=False)
        except FileExistsError as exc:
            raise PortableTransportError("transport destination appeared during sealing") from exc
        except OSError as exc:
            raise PortableTransportError("transport output cannot be published atomically") from exc
    return {
        "contract": "EvaPortableTransport/v1",
        "plaintext_sha256": plaintext_sha256,
        "plaintext_byte_length": plaintext_bytes,
        "transport_sha256": transport_sha256,
    }


def _decrypt_to_file(
    source: Path,
    destination: Path,
    *,
    passphrase: str,
) -> tuple[bytes, int, str]:
    metadata = _require_regular(source, label="transport source")
    try:
        with source.open("rb") as input_handle:
            if input_handle.read(len(MAGIC)) != MAGIC:
                raise PortableTransportError("transport magic is invalid")
            length_raw = input_handle.read(HEADER_LENGTH_BYTES)
            if len(length_raw) != HEADER_LENGTH_BYTES:
                raise PortableTransportError("transport header is truncated")
            header_length = struct.unpack(">I", length_raw)[0]
            header = input_handle.read(header_length)
            salt, nonce, plaintext_bytes, plaintext_sha256 = _parse_header(header)
            ciphertext_start = len(MAGIC) + HEADER_LENGTH_BYTES + header_length
            ciphertext_bytes = metadata.st_size - ciphertext_start - TAG_BYTES
            if ciphertext_bytes != plaintext_bytes:
                raise PortableTransportError("transport ciphertext length differs from header")
            input_handle.seek(metadata.st_size - TAG_BYTES)
            tag = input_handle.read(TAG_BYTES)
            decryptor = Cipher(algorithms.AES(_derive_key(passphrase, salt)), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            input_handle.seek(ciphertext_start)
            digest = hashlib.sha256()
            written = 0
            with destination.open("xb") as output_handle:
                remaining = ciphertext_bytes
                while remaining:
                    chunk = input_handle.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise PortableTransportError("transport ciphertext is truncated")
                    plaintext = decryptor.update(chunk)
                    digest.update(plaintext)
                    written += len(plaintext)
                    output_handle.write(plaintext)
                    remaining -= len(chunk)
                final = decryptor.finalize()
                digest.update(final)
                written += len(final)
                output_handle.write(final)
                output_handle.flush()
                os.fsync(output_handle.fileno())
        after = _require_regular(source, label="transport source")
    except InvalidTag as exc:
        raise PortableTransportError("transport cannot be authenticated with this passphrase") from exc
    except OSError as exc:
        raise PortableTransportError("transport source cannot be read") from exc
    if (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise PortableTransportError("transport source changed while being read")
    if written != plaintext_bytes or digest.hexdigest() != plaintext_sha256:
        raise PortableTransportError("transport plaintext does not match authenticated header")
    return header, plaintext_bytes, plaintext_sha256


def _unpack_plaintext_tar(source: Path, destination: Path) -> None:
    seen: set[str] = set()
    total_bytes = 0
    try:
        with tarfile.open(source, mode="r:") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_ARCHIVE_FILES:
                raise PortableTransportError("transport tar member count is invalid")
            for member in members:
                parts = _safe_relative_path(member.name)
                relative = "/".join(parts)
                if relative in seen or not member.isreg() or member.size < 0:
                    raise PortableTransportError("transport tar member is invalid")
                seen.add(relative)
                total_bytes += member.size
                if total_bytes > MAX_ARCHIVE_BYTES:
                    raise PortableTransportError("transport tar exceeds byte limit")
                source_handle = archive.extractfile(member)
                if source_handle is None:
                    raise PortableTransportError("transport tar member cannot be extracted")
                target = destination.joinpath(*parts)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with source_handle, target.open("xb") as output:
                    while chunk := source_handle.read(CHUNK_BYTES):
                        output.write(chunk)
                os.chmod(target, 0o600)
    except (OSError, tarfile.TarError) as exc:
        raise PortableTransportError("transport tar cannot be unpacked") from exc


def open_portable_transport(
    source: Path,
    destination: Path,
    *,
    passphrase: str,
    validator: Callable[[Path], None],
) -> dict[str, str | int]:
    """Authenticate, reconstruct and validate a portable archive directory atomically."""

    source = source.absolute()
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise PortableTransportError("restored archive destination already exists")
    with _reserved_empty_directory(destination):
        with _private_staging(destination) as staging:
            packed = staging / "portable.tar"
            _, plaintext_bytes, plaintext_sha256 = _decrypt_to_file(
                source, packed, passphrase=passphrase
            )
            restored = staging / "restored"
            restored.mkdir(mode=0o700)
            _unpack_plaintext_tar(packed, restored)
            validator(restored)
            try:
                if any(destination.iterdir()) or destination.is_symlink():
                    raise PortableTransportError(
                        "restored archive destination changed during restoration"
                    )
                os.replace(restored, destination)
            except OSError as exc:
                raise PortableTransportError(
                    "restored archive cannot be published atomically"
                ) from exc
    return {
        "contract": "EvaPortableTransport/v1",
        "plaintext_sha256": plaintext_sha256,
        "plaintext_byte_length": plaintext_bytes,
        "transport_sha256": _sha256_file(source, label="transport source"),
    }


__all__ = [
    "PortableTransportError",
    "open_portable_transport",
    "seal_portable_archive",
]
