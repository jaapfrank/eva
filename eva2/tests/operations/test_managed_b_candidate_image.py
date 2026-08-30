from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

import pytest

from tools.managed_b_candidate.base_image import MaterializedBaseImage
from tools.managed_b_candidate.build_context import MaterializedBuildContext
from tools.managed_b_candidate.candidate_image import (
    _read_iid,
    build_offline_candidate_image,
    validate_offline_candidate_image,
)


BASE_DIGEST = "sha256:" + "1" * 64
BASE_ID = "sha256:" + "2" * 64
IMAGE_ID = "sha256:" + "3" * 64
ARCHIVE_SHA = "4" * 64
REPO_DIGEST = f"docker.io/library/python@{BASE_DIGEST}"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_image_installs_eva_package_before_removing_pip() -> None:
    dockerfile = (PROJECT_ROOT / "deploy/living-core/Dockerfile").read_text(
        encoding="utf-8"
    )
    install = "python -m pip install --no-cache-dir --no-index --no-deps --no-build-isolation ."
    metadata_check = "metadata.entry_points().select(group='eva2.external_providers')"
    remove_pip = "python -m pip uninstall -y pip wheel"

    assert "COPY pyproject.toml ./pyproject.toml" in dockerfile
    assert install in dockerfile
    assert metadata_check in dockerfile
    assert dockerfile.index(install) < dockerfile.index(metadata_check)
    assert dockerfile.index(metadata_check) < dockerfile.index(remove_pip)


@pytest.fixture
def native_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(prefix="eva2-candidate-image-test-", dir="/tmp") as path:
        yield Path(path)


def _inputs(native_tmp_path: Path) -> tuple[MaterializedBuildContext, MaterializedBaseImage]:
    return (
        MaterializedBuildContext(native_tmp_path / "context", {}),
        MaterializedBaseImage(native_tmp_path / "base.oci.tar", {}),
    )


def _receipts() -> tuple[dict[str, object], dict[str, object]]:
    return (
        {
            "base_image_archive_sha256": ARCHIVE_SHA,
            "base_image_digest": BASE_DIGEST,
            "context_tree_sha256": "5" * 64,
        },
        {
            "archive_sha256": ARCHIVE_SHA,
            "base_image_digest": BASE_DIGEST,
            "local_image_id": BASE_ID,
            "local_repo_digest": REPO_DIGEST,
        },
    )


def _stub_validators(monkeypatch: pytest.MonkeyPatch) -> None:
    context, base = _receipts()
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image.validate_materialized_build_context",
        lambda _context: context,
    )
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image.validate_materialized_base_image",
        lambda _base: base,
    )


def _runner(*, candidate_tags: object = None):
    commands: list[list[str]] = []

    def run(command: list[str], _timeout: int) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            reference = command[3]
            if reference == REPO_DIGEST:
                value = [{"Architecture": "amd64", "Id": BASE_ID, "Os": "linux", "RepoDigests": [REPO_DIGEST]}]
            elif reference == IMAGE_ID:
                value = [{"Id": IMAGE_ID, "RepoTags": candidate_tags, "Size": 123}]
            else:
                raise AssertionError(f"unexpected inspected image: {reference}")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(value), stderr="")
        assert command[:2] == ["docker", "build"]
        iidfile = Path(command[command.index("--iidfile") + 1])
        iidfile.write_text(f"{IMAGE_ID}\n", encoding="ascii")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    return commands, run


def test_builds_an_untagged_networkless_candidate_without_b(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    _stub_validators(monkeypatch)
    context, base = _inputs(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    commands, runner = _runner()

    candidate = build_offline_candidate_image(context, base, output, runner=runner)

    assert candidate.image_id == IMAGE_ID
    assert candidate.receipt["network_used"] is False
    assert validate_offline_candidate_image(candidate, runner=runner)["status"] == "built_local_candidate"
    build = next(command for command in commands if command[:2] == ["docker", "build"])
    assert "--network=none" in build
    assert "--platform=linux/amd64" in build
    assert "--pull=false" in build
    assert "--no-cache" in build
    assert "--load" in build
    assert build[build.index("--file") + 1].endswith("deploy/living-core/Dockerfile")
    assert "--tag" not in build and "--push" not in build
    assert list(output.iterdir()) == []


def test_refuses_when_current_docker_base_differs(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    _stub_validators(monkeypatch)
    context, base = _inputs(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)

    def runner(command: list[str], _timeout: int) -> subprocess.CompletedProcess[str]:
        assert command == ["docker", "image", "inspect", REPO_DIGEST]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([{"Architecture": "amd64", "Id": IMAGE_ID, "Os": "linux", "RepoDigests": [REPO_DIGEST]}]),
            stderr="",
        )

    with pytest.raises(RuntimeError, match="Docker base image differs"):
        build_offline_candidate_image(context, base, output, runner=runner)

    assert list(output.iterdir()) == []


def test_refuses_a_candidate_that_receives_a_tag(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    _stub_validators(monkeypatch)
    context, base = _inputs(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    _commands, runner = _runner(candidate_tags=["unexpected:latest"])

    with pytest.raises(RuntimeError, match="must not receive a tag"):
        build_offline_candidate_image(context, base, output, runner=runner)

    assert list(output.iterdir()) == []


def test_refuses_a_symlinked_candidate_id_file(native_tmp_path: Path) -> None:
    target = native_tmp_path / "target"
    target.write_text(f"{IMAGE_ID}\n", encoding="ascii")
    iidfile = native_tmp_path / "image.iid"
    iidfile.symlink_to(target)

    with pytest.raises(RuntimeError, match="ID file is unsafe"):
        _read_iid(iidfile)
