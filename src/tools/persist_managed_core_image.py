#!/usr/bin/env python3
"""Atomically admit one scan-clean core image into a retained managed slot."""

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
_ENVIRONMENT = re.compile(r"^/var/lib/eva2/managed-b-slot[34]/candidate\.env$")
_IMAGE_REFERENCE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MAX_BYTES = 64 * 1024
_IMAGE_KEY = "EVA_LIVING_CORE_IMAGE"
_DASHBOARD_KEY = "EVA_DASHBOARD_RELEASE_SHA256"
_CONFIGURATION_KEY = "EVA_B_CONFIGURATION_SHA256"


class ManagedCoreImageError(RuntimeError):
    """The candidate or retained configuration stopped closed."""


Runner = Callable[[tuple[str, ...]], bytes]


def _run(arguments: tuple[str, ...]) -> bytes:
    completed = subprocess.run(arguments, capture_output=True, check=False)
    if completed.returncode != 0:
        raise ManagedCoreImageError("Docker image inspection failed")
    return completed.stdout


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_regular(path: Path, *, private: bool) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ManagedCoreImageError("required file is inaccessible") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ManagedCoreImageError("required file is not a single regular file")
        if private and (
            stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
        ):
            raise ManagedCoreImageError("managed environment is not private")
        raw = os.read(descriptor, _MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ManagedCoreImageError("required file is too large")
        return raw, metadata
    finally:
        os.close(descriptor)


def _candidate(
    image_reference: str,
    expected_source_commit: str,
    runner: Runner,
) -> str:
    if _IMAGE_REFERENCE.fullmatch(image_reference) is None:
        raise ManagedCoreImageError("candidate image must use an exact digest")
    if _COMMIT.fullmatch(expected_source_commit) is None:
        raise ManagedCoreImageError("candidate source commit is invalid")
    try:
        rows = json.loads(runner((DOCKER, "image", "inspect", image_reference)))
        row = rows[0]
        image_id = row["Id"]
        references = row["RepoDigests"]
        config = row["Config"]
        labels = config["Labels"]
    except (IndexError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManagedCoreImageError("candidate image inspection is invalid") from error
    digest = image_reference.rsplit("@", 1)[1]
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or image_id != digest
        or image_reference not in references
        or config.get("User") != "65532:65532"
        or labels.get("io.eva2.source-commit") != expected_source_commit
        or labels.get("io.eva2.source-refresh") != "git-archive-v1"
    ):
        raise ManagedCoreImageError("candidate image identity differs")
    return image_id


def _scan_evidence(
    path: Path,
    *,
    image_id: str,
    source_commit: str,
) -> tuple[str, bytes]:
    raw, _ = _read_regular(path, private=False)
    try:
        evidence = json.loads(raw)
        candidate = evidence["candidate"]
        scan = candidate["trivy"]
        source = evidence["source"]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManagedCoreImageError("candidate scan evidence is invalid") from error
    if (
        evidence.get("contract") != "EvaManagedCoreImageCandidateEvidence/v1"
        or evidence.get("status") != "passed_candidate_only"
        or candidate.get("imageId") != image_id
        or source.get("commit") != source_commit
        or scan.get("critical") != 0
        or scan.get("high") != 0
        or scan.get("network") != "none"
    ):
        raise ManagedCoreImageError("candidate scan evidence differs")
    return _sha256(raw), raw


def _updated_environment(raw: bytes, image_reference: str) -> tuple[bytes, str]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ManagedCoreImageError("managed environment is not UTF-8") from error
    digest = image_reference.rsplit("sha256:", 1)[1]
    replacements = {_IMAGE_KEY: image_reference, _DASHBOARD_KEY: digest}
    counts = {_IMAGE_KEY: 0, _DASHBOARD_KEY: 0, _CONFIGURATION_KEY: 0}
    base_lines: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key == _CONFIGURATION_KEY:
            counts[key] += 1
            continue
        if key in replacements:
            counts[key] += 1
            line = f"{key}={replacements[key]}"
        base_lines.append(line)
    if any(counts[key] != 1 for key in counts):
        raise ManagedCoreImageError("managed environment release keys differ")
    base = ("\n".join(base_lines) + "\n").encode("utf-8")
    configuration_sha256 = _sha256(base)
    return base + f"{_CONFIGURATION_KEY}={configuration_sha256}\n".encode(), configuration_sha256


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


def persist_managed_core_image(
    environment_path: Path,
    image_reference: str,
    expected_source_commit: str,
    scan_evidence_path: Path,
    *,
    runner: Runner = _run,
) -> dict[str, object]:
    before, metadata = _read_regular(environment_path, private=True)
    image_id = _candidate(image_reference, expected_source_commit, runner)
    evidence_sha256, _ = _scan_evidence(
        scan_evidence_path,
        image_id=image_id,
        source_commit=expected_source_commit,
    )
    after, configuration_sha256 = _updated_environment(before, image_reference)
    _atomic_write(environment_path, after, metadata)
    return {
        "candidateImage": image_reference,
        "candidateSourceCommit": expected_source_commit,
        "configurationSha256": configuration_sha256,
        "contract": "EvaManagedCoreImagePersistenceReceipt/v1",
        "destinationAfterSha256": _sha256(after),
        "destinationBeforeSha256": _sha256(before),
        "scanEvidenceSha256": evidence_sha256,
        "status": "persisted",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--scan-evidence", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if _ENVIRONMENT.fullmatch(str(arguments.environment)) is None:
        print(json.dumps({"error": "managed environment path is not admitted", "status": "failed"}, sort_keys=True))
        return 2
    try:
        receipt = persist_managed_core_image(
            arguments.environment,
            arguments.image,
            arguments.source_commit,
            arguments.scan_evidence,
        )
    except ManagedCoreImageError as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
