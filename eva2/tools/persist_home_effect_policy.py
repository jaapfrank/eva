#!/usr/bin/env python3
"""Persist one healthy Eva2 container's bounded house-effect policy."""

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


DOCKER = "/usr/bin/docker"
_CONTAINER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_EFFECT = re.compile(r"^(?:light|switch)\.[a-z0-9_]+:turn_(?:on|off)$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_REFERENCE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_MANAGED_ENVIRONMENT = re.compile(
    r"^/var/lib/eva2/managed-b-slot[34]/candidate\.env$"
)
_POLICY_KEYS = (
    "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST",
    "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST",
)
_CORE_IMAGE_KEY = "EVA_LIVING_CORE_IMAGE"
_DASHBOARD_RELEASE_KEY = "EVA_DASHBOARD_RELEASE_SHA256"
_MAX_ENVIRONMENT_BYTES = 64 * 1024


class HomeEffectPolicyError(RuntimeError):
    """The policy source or private destination stopped closed."""


Runner = Callable[[tuple[str, ...]], bytes]


def _run(arguments: tuple[str, ...]) -> bytes:
    result = subprocess.run(arguments, capture_output=True, check=False)
    if result.returncode != 0:
        raise HomeEffectPolicyError("Docker inspection failed")
    return result.stdout


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _private_environment(path: Path) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise HomeEffectPolicyError("managed environment is inaccessible") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
        ):
            raise HomeEffectPolicyError("managed environment is not private")
        raw = os.read(descriptor, _MAX_ENVIRONMENT_BYTES + 1)
        if len(raw) > _MAX_ENVIRONMENT_BYTES:
            raise HomeEffectPolicyError("managed environment is too large")
        return raw, metadata
    finally:
        os.close(descriptor)


def _policy(container: str, runner: Runner) -> tuple[str, str, str]:
    if _CONTAINER.fullmatch(container) is None:
        raise HomeEffectPolicyError("container name is invalid")
    try:
        inspected = json.loads(runner((DOCKER, "inspect", container)))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HomeEffectPolicyError("Docker inspection is invalid") from error
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise HomeEffectPolicyError("Docker inspection differs")
    row = inspected[0]
    try:
        image = row["Image"]
        state = row["State"]
        environment = row["Config"]["Env"]
    except (KeyError, TypeError) as error:
        raise HomeEffectPolicyError("container metadata differs") from error
    if (
        _DIGEST.fullmatch(image or "") is None
        or state.get("Status") != "running"
        or state.get("Health", {}).get("Status") != "healthy"
        or not isinstance(environment, list)
    ):
        raise HomeEffectPolicyError("policy source container is not healthy and exact")
    values: dict[str, str] = {}
    for item in environment:
        if not isinstance(item, str) or "=" not in item:
            raise HomeEffectPolicyError("container environment is invalid")
        key, value = item.split("=", 1)
        if key in values:
            raise HomeEffectPolicyError("container environment repeats a key")
        values[key] = value
    allowlist = values.get(_POLICY_KEYS[0], "")
    principals = values.get(_POLICY_KEYS[1], "")
    effects = allowlist.split(",") if allowlist else []
    principal_hashes = principals.split(",") if principals else []
    if (
        not effects
        or effects != sorted(set(effects))
        or any(_EFFECT.fullmatch(item) is None for item in effects)
        or not principal_hashes
        or principal_hashes != sorted(set(principal_hashes))
        or any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in principal_hashes)
    ):
        raise HomeEffectPolicyError("container house-effect policy is invalid")
    return image, allowlist, principals


def _source_image_reference(image: str, runner: Runner) -> str:
    try:
        inspected = json.loads(runner((DOCKER, "image", "inspect", image)))
        references = inspected[0]["RepoDigests"]
    except (IndexError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HomeEffectPolicyError("source image reference is unavailable") from error
    matching = sorted(
        {
            reference
            for reference in references
            if isinstance(reference, str)
            and _IMAGE_REFERENCE.fullmatch(reference) is not None
            and reference.rsplit("@", 1)[1] == image
        }
    )
    if len(matching) != 1:
        raise HomeEffectPolicyError("source image reference is not unique and exact")
    return matching[0]


def _updated_environment(
    raw: bytes,
    allowlist: str,
    principals: str,
    *,
    image_reference: str | None = None,
) -> bytes:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise HomeEffectPolicyError("managed environment is not UTF-8") from error
    replacements = dict(zip(_POLICY_KEYS, (allowlist, principals), strict=True))
    if image_reference is not None:
        replacements[_CORE_IMAGE_KEY] = image_reference
        replacements[_DASHBOARD_RELEASE_KEY] = image_reference.rsplit("sha256:", 1)[-1]
    counts = {key: 0 for key in replacements}
    result: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in replacements:
            counts[key] += 1
            result.append(f"{key}={replacements[key]}")
        else:
            result.append(line)
    if counts[_POLICY_KEYS[0]] != 1 or counts[_POLICY_KEYS[1]] not in {0, 1}:
        raise HomeEffectPolicyError("managed environment policy keys differ")
    if image_reference is not None and any(counts[key] != 1 for key in (_CORE_IMAGE_KEY, _DASHBOARD_RELEASE_KEY)):
        raise HomeEffectPolicyError("managed environment core image key differs")
    if counts[_POLICY_KEYS[1]] == 0:
        result.append(f"{_POLICY_KEYS[1]}={principals}")
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


def persist_home_effect_policy(
    environment_path: Path,
    source_container: str,
    *,
    promote_source_image: bool = False,
    runner: Runner = _run,
) -> dict[str, object]:
    before, metadata = _private_environment(environment_path)
    image, allowlist, principals = _policy(source_container, runner)
    image_reference = (
        _source_image_reference(image, runner) if promote_source_image else None
    )
    after = _updated_environment(
        before, allowlist, principals, image_reference=image_reference
    )
    _atomic_write(environment_path, after, metadata)
    return {
        "allowlistSha256": _sha256(allowlist.encode()),
        "contract": "EvaManagedBHomeEffectPolicyPersistenceReceipt/v1",
        "destinationAfterSha256": _sha256(after),
        "destinationBeforeSha256": _sha256(before),
        "effectRuleCount": len(allowlist.split(",")),
        "principalHashCount": len(principals.split(",")),
        "sourceImage": image,
        "sourceImagePromoted": image_reference is not None,
        "status": "persisted",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--source-container", required=True)
    parser.add_argument("--promote-source-image", action="store_true")
    arguments = parser.parse_args(argv)
    if _MANAGED_ENVIRONMENT.fullmatch(str(arguments.environment)) is None:
        print(
            json.dumps(
                {"error": "managed environment path is not admitted", "status": "failed"},
                sort_keys=True,
            )
        )
        return 2
    try:
        receipt = persist_home_effect_policy(
            arguments.environment,
            arguments.source_container,
            promote_source_image=arguments.promote_source_image,
        )
    except HomeEffectPolicyError as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
