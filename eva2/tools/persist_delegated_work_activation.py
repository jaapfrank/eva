#!/usr/bin/env python3
"""Atomically persist one exact managed-slot delegated-work activation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Callable
from uuid import UUID


DOCKER = "/usr/bin/docker"
_MANAGED_ENVIRONMENT = re.compile(
    r"^/var/lib/eva2/managed-b-slot[34]/candidate\.env$"
)
_IMAGE_REFERENCE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_MAX_ENVIRONMENT_BYTES = 64 * 1024
_MAX_TELEGRAM_USER_ID = 9_007_199_254_740_991
_CORE_IMAGE_KEY = "EVA_LIVING_CORE_IMAGE"
_DASHBOARD_RELEASE_KEY = "EVA_DASHBOARD_RELEASE_SHA256"
_MANIFEST_KEY = "EVA_B_MANAGED_MANIFEST_SHA256"
_MANIFEST_PATH = (
    Path(__file__).resolve().parents[1] / "deploy/living-core/managed-b.compose.yml"
)
_DELEGATED_KEYS = (
    "EVA_DELEGATED_WORK_IMAGE",
    "EVA_DELEGATED_WORK_OWNER_PRINCIPAL_ID",
    "EVA_DELEGATED_WORK_OWNER_BINDING_ID",
    "EVA_DELEGATED_WORK_ENABLED",
)


class DelegatedWorkActivationError(RuntimeError):
    """The exact images or private managed environment stopped closed."""


Runner = Callable[[tuple[str, ...]], bytes]


def _run(arguments: tuple[str, ...]) -> bytes:
    result = subprocess.run(arguments, capture_output=True, check=False)
    if result.returncode != 0:
        raise DelegatedWorkActivationError("Docker image inspection failed")
    return result.stdout


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _private_environment(path: Path) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise DelegatedWorkActivationError(
            "managed environment is inaccessible"
        ) from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
        ):
            raise DelegatedWorkActivationError("managed environment is not private")
        raw = os.read(descriptor, _MAX_ENVIRONMENT_BYTES + 1)
        if len(raw) > _MAX_ENVIRONMENT_BYTES:
            raise DelegatedWorkActivationError("managed environment is too large")
        return raw, metadata
    finally:
        os.close(descriptor)


def _private_bindings(path: Path) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise DelegatedWorkActivationError(
            "Telegram bindings are inaccessible"
        ) from error
    try:
        metadata = os.fstat(descriptor)
        expected_owner = 65532 if os.geteuid() == 0 else os.geteuid()
        expected_group = 65532 if os.geteuid() == 0 else os.getegid()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != expected_owner
            or metadata.st_gid != expected_group
        ):
            raise DelegatedWorkActivationError("Telegram bindings are not private")
        raw = os.read(descriptor, _MAX_ENVIRONMENT_BYTES + 1)
        if len(raw) > _MAX_ENVIRONMENT_BYTES:
            raise DelegatedWorkActivationError("Telegram bindings are too large")
        return raw, metadata
    finally:
        os.close(descriptor)


def _image_source(image: str, *, runner: Runner) -> str:
    if _IMAGE_REFERENCE.fullmatch(image) is None:
        raise DelegatedWorkActivationError("managed image is not exact")
    try:
        inspected = json.loads(runner((DOCKER, "image", "inspect", image)))
        row = inspected[0]
        image_id = row["Id"]
        references = row["RepoDigests"]
        source_commit = row["Config"]["Labels"]["io.eva2.source-commit"]
        user = row["Config"]["User"]
    except (
        IndexError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise DelegatedWorkActivationError("managed image metadata differs") from error
    digest = image.rsplit("@", 1)[1]
    if (
        image not in references
        or image_id != digest
        or not re.fullmatch(r"[0-9a-f]{40}", source_commit or "")
        or user not in {"65532", "65532:65532"}
    ):
        raise DelegatedWorkActivationError("managed image authority differs")
    return source_commit


def _updated_environment(
    raw: bytes,
    *,
    core_image: str,
    worker_image: str,
    owner_principal_id: str,
    owner_binding_id: str,
) -> bytes:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise DelegatedWorkActivationError("managed environment is not UTF-8") from error
    replacements = {
        _CORE_IMAGE_KEY: core_image,
        _DASHBOARD_RELEASE_KEY: core_image.rsplit("sha256:", 1)[-1],
        _MANIFEST_KEY: _sha256(_MANIFEST_PATH.read_bytes()),
        _DELEGATED_KEYS[0]: worker_image,
        _DELEGATED_KEYS[1]: owner_principal_id,
        _DELEGATED_KEYS[2]: owner_binding_id,
        _DELEGATED_KEYS[3]: "true",
    }
    counts = {key: 0 for key in replacements}
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key and key in seen:
            raise DelegatedWorkActivationError("managed environment repeats a key")
        if key:
            seen.add(key)
        if key in replacements:
            counts[key] += 1
            result.append(f"{key}={replacements[key]}")
        else:
            result.append(line)
    if (
        counts[_CORE_IMAGE_KEY] != 1
        or counts[_DASHBOARD_RELEASE_KEY] != 1
        or counts[_MANIFEST_KEY] != 1
    ):
        raise DelegatedWorkActivationError("managed environment core keys differ")
    for key in _DELEGATED_KEYS:
        if counts[key] == 0:
            result.append(f"{key}={replacements[key]}")
    return ("\n".join(result) + "\n").encode("utf-8")


def _atomic_write(path: Path, raw: bytes, metadata: os.stat_result) -> None:
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary_name = temporary.name
            os.fchmod(temporary.fileno(), 0o600)
            os.fchown(temporary.fileno(), metadata.st_uid, metadata.st_gid)
            temporary.write(raw)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        os.fsync(directory)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        os.close(directory)


def _replaceable_owner_binding(
    raw: bytes, *, owner_principal_id: str, owner_binding_id: str
) -> bool:
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if (
        not isinstance(document, dict)
        or set(document) != {"bindings", "contract"}
        or document.get("contract") != "EvaTelegramPrincipalBindings/v2"
        or not isinstance(document.get("bindings"), list)
        or len(document["bindings"]) != 1
    ):
        return False
    binding = document["bindings"][0]
    if (
        not isinstance(binding, dict)
        or set(binding)
        != {
            "allowed_chat_ids",
            "principal_binding_id",
            "principal_id",
            "telegram_user_id",
        }
        or binding.get("principal_id") != owner_principal_id
        or binding.get("principal_binding_id") != owner_binding_id
        or isinstance(binding.get("telegram_user_id"), bool)
        or not isinstance(binding.get("telegram_user_id"), int)
    ):
        return False
    telegram_user_id = binding["telegram_user_id"]
    return (
        1 <= telegram_user_id <= _MAX_TELEGRAM_USER_ID
        and binding.get("allowed_chat_ids") == [str(telegram_user_id)]
    )


def _owner_binding_payload(
    *, telegram_user_id: int, owner_principal_id: str, owner_binding_id: str
) -> bytes:
    return (
        json.dumps(
            {
                "bindings": [
                    {
                        "allowed_chat_ids": [str(telegram_user_id)],
                        "principal_binding_id": owner_binding_id,
                        "principal_id": owner_principal_id,
                        "telegram_user_id": telegram_user_id,
                    }
                ],
                "contract": "EvaTelegramPrincipalBindings/v2",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def rebind_owner_telegram_account(
    environment_path: Path, *, telegram_user_id: int
) -> dict[str, object]:
    """Replace only the private Telegram account for the same sealed owner."""

    if (
        isinstance(telegram_user_id, bool)
        or not isinstance(telegram_user_id, int)
        or not 1 <= telegram_user_id <= _MAX_TELEGRAM_USER_ID
    ):
        raise DelegatedWorkActivationError("delegated Telegram identity is invalid")
    environment, _ = _private_environment(environment_path)
    values: dict[str, str] = {}
    try:
        lines = environment.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise DelegatedWorkActivationError("managed environment is not UTF-8") from error
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or not key:
            continue
        if key in values:
            raise DelegatedWorkActivationError("managed environment repeats a key")
        values[key] = value
    try:
        owner_principal_id = str(UUID(values["EVA_RUNTIME_PRINCIPAL_ID"]))
        owner_binding_id = str(UUID(values["EVA_DASHBOARD_PRINCIPAL_BINDING_ID"]))
    except (KeyError, ValueError) as error:
        raise DelegatedWorkActivationError(
            "managed owner authority is invalid"
        ) from error
    bindings_path = environment_path.parent / "secrets/telegram-bindings"
    before, metadata = _private_bindings(bindings_path)
    if not _replaceable_owner_binding(
        before,
        owner_principal_id=owner_principal_id,
        owner_binding_id=owner_binding_id,
    ):
        raise DelegatedWorkActivationError("Telegram bindings carry other authority")
    after = _owner_binding_payload(
        telegram_user_id=telegram_user_id,
        owner_principal_id=owner_principal_id,
        owner_binding_id=owner_binding_id,
    )
    _atomic_write(bindings_path, after, metadata)
    return {
        "beforeSha256": _sha256(before),
        "afterSha256": _sha256(after),
        "contract": "EvaManagedBOwnerTelegramRebindingReceipt/v1",
        "ownerAuthorityChanged": False,
        "status": "persisted",
        "telegramBindingCount": 1,
    }


def persist_delegated_work_activation(
    environment_path: Path,
    *,
    core_image: str,
    worker_image: str,
    owner_principal_id: str,
    owner_binding_id: str,
    telegram_user_id: int | None = None,
    replace_existing_owner_binding: bool = False,
    runner: Runner = _run,
) -> dict[str, object]:
    if telegram_user_id is not None and (
        isinstance(telegram_user_id, bool)
        or not isinstance(telegram_user_id, int)
        or not 1 <= telegram_user_id <= _MAX_TELEGRAM_USER_ID
    ):
        raise DelegatedWorkActivationError("delegated Telegram identity is invalid")
    try:
        owner_principal_id = str(UUID(owner_principal_id))
        owner_binding_id = str(UUID(owner_binding_id))
    except ValueError as error:
        raise DelegatedWorkActivationError("delegated owner authority is invalid") from error
    core_source = _image_source(core_image, runner=runner)
    worker_source = _image_source(worker_image, runner=runner)
    if core_source != worker_source:
        raise DelegatedWorkActivationError("managed images use different source commits")
    before, metadata = _private_environment(environment_path)
    if telegram_user_id is None:
        values = dict(line.split("=", 1) for line in before.decode("utf-8").splitlines()
                      if "=" in line and not line.startswith("#"))
        if (replace_existing_owner_binding
                or values.get("EVA_RUNTIME_PRINCIPAL_ID") != owner_principal_id
                or values.get("EVA_DASHBOARD_PRINCIPAL_BINDING_ID") != owner_binding_id):
            raise DelegatedWorkActivationError("managed dashboard owner authority differs")
    bindings_path = environment_path.parent / "secrets/telegram-bindings"
    bindings_before, bindings_metadata = _private_bindings(bindings_path)
    after = _updated_environment(
        before,
        core_image=core_image,
        worker_image=worker_image,
        owner_principal_id=owner_principal_id,
        owner_binding_id=owner_binding_id,
    )
    bindings_after = bindings_before
    replaceable = False
    if telegram_user_id is not None:
        bindings_after = _owner_binding_payload(
            telegram_user_id=telegram_user_id,
            owner_principal_id=owner_principal_id,
            owner_binding_id=owner_binding_id,
        )
        replaceable = replace_existing_owner_binding and _replaceable_owner_binding(
            bindings_before,
            owner_principal_id=owner_principal_id,
            owner_binding_id=owner_binding_id,
        )
        if bindings_before.strip() not in {b"{}", bindings_after.strip()} and not replaceable:
            raise DelegatedWorkActivationError("Telegram bindings already carry authority")
        _atomic_write(bindings_path, bindings_after, bindings_metadata)
    _atomic_write(environment_path, after, metadata)
    return {
        "afterSha256": _sha256(after),
        "beforeSha256": _sha256(before),
        "bindingsAfterSha256": _sha256(bindings_after),
        "bindingsBeforeSha256": _sha256(bindings_before),
        "contract": "EvaManagedBDelegatedWorkActivationReceipt/v1",
        "imageSourceCommit": core_source,
        "ownerAuthorityFieldCount": 2,
        "ownerBindingReplaced": replaceable,
        "status": "persisted",
        "telegramBindingCount": len(json.loads(bindings_after).get("bindings", [])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--core-image", required=True)
    parser.add_argument("--worker-image", required=True)
    parser.add_argument("--owner-principal-id", required=True)
    parser.add_argument("--owner-binding-id", required=True)
    parser.add_argument("--telegram-user-id", type=int,
                        help="Only for explicit Telegram binding setup; omitted preserves existing bindings")
    parser.add_argument("--replace-existing-owner-binding", action="store_true")
    arguments = parser.parse_args(argv)
    if _MANAGED_ENVIRONMENT.fullmatch(str(arguments.environment)) is None:
        print(json.dumps({"error": "environment is not admitted", "status": "failed"}))
        return 2
    try:
        receipt = persist_delegated_work_activation(
            arguments.environment,
            core_image=arguments.core_image,
            worker_image=arguments.worker_image,
            owner_principal_id=arguments.owner_principal_id,
            owner_binding_id=arguments.owner_binding_id,
            telegram_user_id=arguments.telegram_user_id,
            replace_existing_owner_binding=arguments.replace_existing_owner_binding,
        )
    except DelegatedWorkActivationError as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
