"""Fail-closed local keyring for Eva's encrypted object store.

The portable archive never contains this file.  A restore can only use object
bytes after the receiving installation has separately provisioned the exact
key that authenticates their immutable envelope.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from eva.application.objects import ObjectBinding


_KEY_HEX = re.compile(r"^[0-9a-f]{64}$")
_MAX_KEYRING_BYTES = 64 * 1024


class ObjectKeyringError(RuntimeError):
    """A local object-key authority is unavailable or malformed."""


def _read_private_keyring_file(path: Path, *, effective_uid: int | None) -> bytes:
    """Read one private keyring without following a replaced filesystem path."""

    if not path.is_absolute():
        raise ObjectKeyringError("object keyring file must be an absolute path")
    owner_uid = os.geteuid() if effective_uid is None else effective_uid

    def validate(metadata: os.stat_result) -> None:
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != owner_uid
            or not stat.S_IMODE(metadata.st_mode) & stat.S_IRUSR
            or stat.S_IMODE(metadata.st_mode) & (stat.S_IRWXG | stat.S_IRWXO)
            or metadata.st_size > _MAX_KEYRING_BYTES
        ):
            raise ObjectKeyringError("object keyring file is unavailable")

    try:
        named = os.lstat(path)
        validate(named)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except (OSError, ObjectKeyringError) as error:
        if isinstance(error, ObjectKeyringError):
            raise
        raise ObjectKeyringError("object keyring file is unavailable") from error
    try:
        opened = os.fstat(descriptor)
        validate(opened)
        if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
            raise ObjectKeyringError("object keyring file changed during preflight")
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_uid,
            opened.st_mode,
            opened.st_size,
            opened.st_ctime_ns,
        )
        chunks: list[bytes] = []
        remaining = _MAX_KEYRING_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        result = b"".join(chunks)
        if len(result) > _MAX_KEYRING_BYTES:
            raise ObjectKeyringError("object keyring file is unavailable")
        after = os.fstat(descriptor)
        if identity != (
            after.st_dev,
            after.st_ino,
            after.st_uid,
            after.st_mode,
            after.st_size,
            after.st_ctime_ns,
        ):
            raise ObjectKeyringError("object keyring file changed while it was read")
        return result
    finally:
        os.close(descriptor)


def _canonical_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ObjectKeyringError(f"object key {field} is invalid")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ObjectKeyringError(f"object key {field} is invalid") from error
    if str(parsed) != value:
        raise ObjectKeyringError(f"object key {field} is invalid")
    return value


def _load_exact_json(raw: bytes) -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ObjectKeyringError("object keyring has duplicate JSON keys")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ObjectKeyringError("object keyring is not valid UTF-8 JSON") from error
    if not isinstance(value, dict) or set(value) != {"contract", "keys"}:
        raise ObjectKeyringError("object keyring shape differs from its contract")
    if value["contract"] != "EvaObjectKeyring/v1":
        raise ObjectKeyringError("object keyring contract differs")
    return value


@dataclass(frozen=True, slots=True)
class _KeyRecord:
    tenant_id: str
    installation_id: str
    key_ref: str
    key: bytes


class FileObjectKeyResolver:
    """Resolve exactly one installation-scoped 32-byte object key from a file."""

    def __init__(self, keyring_file: Path, *, effective_uid: int | None = None) -> None:
        if not isinstance(keyring_file, Path):
            raise TypeError("object keyring file must be a pathlib.Path")
        raw = _read_private_keyring_file(keyring_file, effective_uid=effective_uid)
        value = _load_exact_json(raw)
        raw_records = value["keys"]
        if not isinstance(raw_records, list) or not raw_records:
            raise ObjectKeyringError("object keyring must contain at least one key")
        records: list[_KeyRecord] = []
        selectors: set[tuple[str, str, str]] = set()
        for item in raw_records:
            if not isinstance(item, dict) or set(item) != {
                "installation_id",
                "key_material_hex",
                "key_ref",
                "tenant_id",
            }:
                raise ObjectKeyringError("object key record shape differs from its contract")
            tenant_id = _canonical_uuid(item["tenant_id"], field="tenant_id")
            installation_id = _canonical_uuid(
                item["installation_id"], field="installation_id"
            )
            key_ref = _canonical_uuid(item["key_ref"], field="key_ref")
            material = item["key_material_hex"]
            if not isinstance(material, str) or _KEY_HEX.fullmatch(material) is None:
                raise ObjectKeyringError("object key material is invalid")
            selector = (tenant_id, installation_id, key_ref)
            if selector in selectors:
                raise ObjectKeyringError("object key selectors must be unique")
            selectors.add(selector)
            records.append(
                _KeyRecord(
                    tenant_id=tenant_id,
                    installation_id=installation_id,
                    key_ref=key_ref,
                    key=bytes.fromhex(material),
                )
            )
        self._records = tuple(records)

    def __call__(self, binding: ObjectBinding) -> bytes:
        if not isinstance(binding, ObjectBinding):
            raise TypeError("object key resolver requires an ObjectBinding")
        matches = [
            item
            for item in self._records
            if (item.tenant_id, item.installation_id, item.key_ref)
            == (binding.tenant_id, binding.installation_id, binding.key_ref)
        ]
        if len(matches) != 1:
            raise ObjectKeyringError("no exact object key is available")
        return matches[0].key


__all__ = ["FileObjectKeyResolver", "ObjectKeyringError"]
