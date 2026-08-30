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
_CORE_IMAGE_KEY = "EVA_LIVING_CORE_IMAGE"
_DASHBOARD_RELEASE_KEY = "EVA_DASHBOARD_RELEASE_SHA256"
_DELEGATED_KEYS = (
    "EVA_DELEGATED_WORK_IMAGE",
    "EVA_DELEGATED_WORK_OWNER_PRINCIPAL_ID",
    "EVA_DELEGATED_WORK_OWNER_BINDING_ID",
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
        _DELEGATED_KEYS[0]: worker_image,
        _DELEGATED_KEYS[1]: owner_principal_id,
        _DELEGATED_KEYS[2]: owner_binding_id,
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
    if counts[_CORE_IMAGE_KEY] != 1 or counts[_DASHBOARD_RELEASE_KEY] != 1:
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


def persist_delegated_work_activation(
    environment_path: Path,
    *,
    core_image: str,
    worker_image: str,
    owner_principal_id: str,
    owner_binding_id: str,
    runner: Runner = _run,
) -> dict[str, object]:
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
    after = _updated_environment(
        before,
        core_image=core_image,
        worker_image=worker_image,
        owner_principal_id=owner_principal_id,
        owner_binding_id=owner_binding_id,
    )
    _atomic_write(environment_path, after, metadata)
    return {
        "afterSha256": _sha256(after),
        "beforeSha256": _sha256(before),
        "contract": "EvaManagedBDelegatedWorkActivationReceipt/v1",
        "imageSourceCommit": core_source,
        "ownerAuthorityFieldCount": 2,
        "status": "persisted",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--core-image", required=True)
    parser.add_argument("--worker-image", required=True)
    parser.add_argument("--owner-principal-id", required=True)
    parser.add_argument("--owner-binding-id", required=True)
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
        )
    except DelegatedWorkActivationError as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
