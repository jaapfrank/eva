#!/usr/bin/env python3
"""Build a disposable Telegram-test image from one clean Eva Git commit.

The build deliberately reuses a locally present, digest-pinned Eva image only
when its runtime dependency lock is byte-for-byte the same as the selected
source.  It downloads nothing and is never a managed-B/release image.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import NoReturn, Sequence

from tools.prepare_managed_b_candidate_context import (
    CandidateBuildInputs,
    prepare_candidate_build_inputs,
    validate_candidate_build_inputs,
)


DOCKER_BINARY = "docker"
DOCKERFILE = "deploy/living-core/Dockerfile.telegram-test"
PARENT_IMAGE_TAG = "eva2-living-core:telegram-native-v17-20260824"
PARENT_IMAGE_DIGEST = (
    "sha256:91919f2ef2beac91cc085005e1647f1eb714b0f49f5bd588897d2a35d33f3e5d"
)
EXPECTED_REQUIREMENTS_LOCK_SHA256 = (
    "b05efcbc41cf8dce64177c82e15430c510ef4f12e30242a4e5ea75d803c1f708"
)
IMAGE_TAG_PREFIX = "eva2-telegram-test:git-"
IMAGE_REFERENCE = re.compile(r"eva2-telegram-test@sha256:[0-9a-f]{64}\Z")


def image_tag(source_commit: str) -> str:
    return f"{IMAGE_TAG_PREFIX}{source_commit[:12]}"


def validate_telegram_test_inputs(inputs: CandidateBuildInputs) -> None:
    """Reject a source archive that cannot make the narrow local test image."""

    files = validate_candidate_build_inputs(inputs)
    dockerfile = files.get(DOCKERFILE)
    if dockerfile is None:
        raise RuntimeError("Telegram test source archive omits its Dockerfile")
    if inputs.requirements_lock_sha256 != EXPECTED_REQUIREMENTS_LOCK_SHA256:
        raise RuntimeError("Telegram test dependency lock differs from its parent")
    source, _mode = dockerfile
    text = source.decode("utf-8")
    required = (
        f"FROM {PARENT_IMAGE_TAG}",
        "ARG EVA_TEST_SOURCE_COMMIT",
        'io.eva2.telegram-test-image="v1"',
        'io.eva2.source-commit="${EVA_TEST_SOURCE_COMMIT}"',
        "USER root",
        "COPY --chown=65532:65532 src ./src",
        "COPY --chown=65532:65532 connector_sdk ./connector_sdk",
        "COPY --chown=65532:65532 alembic.ini ./alembic.ini",
        "COPY --chown=65532:65532 migrations ./migrations",
        "COPY --chown=65532:65532 schemas ./schemas",
        "USER 65532:65532",
    )
    if any(value not in text for value in required) or "RUN " in text:
        raise RuntimeError("Telegram test Dockerfile differs from the sealed copy-only contract")
    # Make accidental additions (network downloads, packages or a second stage)
    # fail rather than quietly broadening the disposable test image.
    if text.count("FROM ") != 1 or text.count("COPY ") != 5:
        raise RuntimeError("Telegram test Dockerfile copy contract differs")


def build_telegram_test_image(source_commit: str) -> dict[str, object]:
    """Build with no network from a canonical Git archive and return a receipt."""

    inputs = prepare_candidate_build_inputs(source_commit)
    validate_telegram_test_inputs(inputs)
    tag = image_tag(inputs.source_commit)
    _require_local_parent_image()
    command: Sequence[str] = (
        DOCKER_BINARY,
        "build",
        "--file",
        DOCKERFILE,
        "--network",
        "none",
        "--pull=false",
        "--no-cache",
        "--platform",
        "linux/amd64",
        "--build-arg",
        f"EVA_TEST_SOURCE_COMMIT={inputs.source_commit}",
        "--tag",
        tag,
        "-",
    )
    try:
        subprocess.run(command, check=True, input=inputs.archive_bytes, timeout=300)
        _require_local_parent_image()
        inspected = subprocess.run(
            [DOCKER_BINARY, "image", "inspect", "--format", "{{index .RepoDigests 0}}", tag],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Telegram test image build failed") from error
    if not IMAGE_REFERENCE.fullmatch(inspected):
        raise RuntimeError("Telegram test image digest is invalid")
    return {
        "contract": "EvaTelegramTestImageReceipt/v1",
        "image": inspected,
        "network": "not_used",
        "parent_image": f"{PARENT_IMAGE_TAG}@{PARENT_IMAGE_DIGEST}",
        "requirements_lock_sha256": inputs.requirements_lock_sha256,
        "source_archive_sha256": inputs.source_archive_sha256,
        "source_commit": inputs.source_commit,
        "status": "passed",
        "nonclaims": [
            "This is a disposable local Telegram test image, not a managed-B or release image.",
            "No package was downloaded or installed during this build.",
            "This receipt does not prove a BotFather, Telegram, database, or conversation test.",
        ],
    }


def _require_local_parent_image() -> None:
    """Bind the mutable local name to one immutable parent before each build."""

    try:
        actual = subprocess.run(
            [DOCKER_BINARY, "image", "inspect", "--format", "{{.Id}}", PARENT_IMAGE_TAG],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Telegram test parent image is unavailable") from error
    if actual != PARENT_IMAGE_DIGEST:
        raise RuntimeError("Telegram test parent image digest differs")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_commit")
    return parser


def main() -> int:
    try:
        receipt = build_telegram_test_image(_parser().parse_args().source_commit)
    except RuntimeError as error:
        _die(str(error))
    print(json.dumps(receipt, sort_keys=True))
    return 0


def _die(message: str) -> NoReturn:
    raise SystemExit(f"telegram-test-image: {message}")


if __name__ == "__main__":
    raise SystemExit(main())
