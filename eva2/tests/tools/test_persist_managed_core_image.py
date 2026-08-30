from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

import pytest

from tools.persist_managed_core_image import (
    ManagedCoreImageError,
    persist_managed_core_image,
)


COMMIT = "b" * 40
IMAGE_ID = "sha256:" + "a" * 64
IMAGE = "eva2/generic-core@" + IMAGE_ID


@pytest.fixture
def private_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(dir="/var/tmp") as root:
        yield Path(root)


def _environment(path: Path) -> Path:
    path.write_text(
        "KEEP=value\n"
        "EVA_LIVING_CORE_IMAGE=eva2/old@sha256:" + "c" * 64 + "\n"
        "EVA_DASHBOARD_RELEASE_SHA256=" + "c" * 64 + "\n"
        "EVA_B_CONFIGURATION_SHA256=" + "d" * 64 + "\n"
    )
    path.chmod(0o600)
    return path


def _evidence(path: Path, *, high: int = 0, image_id: str = IMAGE_ID) -> Path:
    path.write_text(
        json.dumps(
            {
                "candidate": {
                    "imageId": image_id,
                    "trivy": {"critical": 0, "high": high, "network": "none"},
                },
                "contract": "EvaManagedCoreImageCandidateEvidence/v1",
                "source": {"commit": COMMIT},
                "status": "passed_candidate_only",
            },
            sort_keys=True,
        )
    )
    return path


def _runner(*, user: str = "65532:65532", commit: str = COMMIT):
    def run(arguments: tuple[str, ...]) -> bytes:
        assert arguments == ("/usr/bin/docker", "image", "inspect", IMAGE)
        return json.dumps(
            [
                {
                    "Config": {
                        "Labels": {
                            "io.eva2.source-commit": commit,
                            "io.eva2.source-refresh": "git-archive-v1",
                        },
                        "User": user,
                    },
                    "Id": IMAGE_ID,
                    "RepoDigests": [IMAGE],
                }
            ]
        ).encode()

    return run


def test_persists_only_the_exact_scan_clean_image_and_reseals_snapshot(
    private_tmp_path: Path,
) -> None:
    environment = _environment(private_tmp_path / "candidate.env")
    evidence = _evidence(private_tmp_path / "evidence.json")
    before = environment.read_bytes()

    receipt = persist_managed_core_image(
        environment, IMAGE, COMMIT, evidence, runner=_runner()
    )

    raw = environment.read_bytes()
    lines = raw.decode().splitlines()
    base = ("\n".join(line for line in lines if not line.startswith("EVA_B_CONFIGURATION_SHA256=")) + "\n").encode()
    configuration_sha256 = hashlib.sha256(base).hexdigest()
    assert f"EVA_LIVING_CORE_IMAGE={IMAGE}" in lines
    assert f"EVA_DASHBOARD_RELEASE_SHA256={IMAGE_ID.removeprefix('sha256:')}" in lines
    assert f"EVA_B_CONFIGURATION_SHA256={configuration_sha256}" in lines
    assert receipt["destinationBeforeSha256"] == hashlib.sha256(before).hexdigest()
    assert receipt["configurationSha256"] == configuration_sha256
    assert environment.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("evidence_high", "evidence_image", "user", "commit", "message"),
    [
        (1, IMAGE_ID, "65532:65532", COMMIT, "scan evidence differs"),
        (0, "sha256:" + "e" * 64, "65532:65532", COMMIT, "scan evidence differs"),
        (0, IMAGE_ID, "0:0", COMMIT, "image identity differs"),
        (0, IMAGE_ID, "65532:65532", "f" * 40, "image identity differs"),
    ],
)
def test_rejects_a_dirty_or_different_candidate(
    private_tmp_path: Path,
    evidence_high: int,
    evidence_image: str,
    user: str,
    commit: str,
    message: str,
) -> None:
    environment = _environment(private_tmp_path / "candidate.env")
    before = environment.read_bytes()
    evidence = _evidence(
        private_tmp_path / "evidence.json", high=evidence_high, image_id=evidence_image
    )
    with pytest.raises(ManagedCoreImageError, match=message):
        persist_managed_core_image(
            environment, IMAGE, COMMIT, evidence, runner=_runner(user=user, commit=commit)
        )
    assert environment.read_bytes() == before


def test_rejects_missing_duplicate_or_non_private_release_keys(
    private_tmp_path: Path,
) -> None:
    evidence = _evidence(private_tmp_path / "evidence.json")
    environment = _environment(private_tmp_path / "candidate.env")
    environment.write_text(environment.read_text() + f"EVA_LIVING_CORE_IMAGE={IMAGE}\n")
    with pytest.raises(ManagedCoreImageError, match="release keys differ"):
        persist_managed_core_image(
            environment, IMAGE, COMMIT, evidence, runner=_runner()
        )

    environment = _environment(private_tmp_path / "candidate.env")
    environment.chmod(0o640)
    with pytest.raises(ManagedCoreImageError, match="not private"):
        persist_managed_core_image(
            environment, IMAGE, COMMIT, evidence, runner=_runner()
        )

    environment.chmod(0o600)
    os.link(environment, private_tmp_path / "linked.env")
    with pytest.raises(ManagedCoreImageError, match="single regular"):
        persist_managed_core_image(
            environment, IMAGE, COMMIT, evidence, runner=_runner()
        )
