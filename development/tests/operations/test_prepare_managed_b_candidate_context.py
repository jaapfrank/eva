from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from tools.prepare_managed_b_candidate_context import (
    EXPECTED_OFFLINE_PIP_INSTALL,
    GIT_BINARY,
    _require_offline_pip_install,
    main,
    prepare_candidate_build_inputs,
)


COMMIT = "a" * 40
RUNTIME_LIBRARIES_REFERENCE = "docker.io/library/python@sha256:" + "2" * 64
RUNTIME_REFERENCE = "gcr.io/distroless/cc-debian13@sha256:" + "3" * 64
EVA2_ROOT = Path(__file__).resolve().parents[3]


def _dockerfile(
    *, build_from: bytes | None = None, install: bytes | None = None
) -> bytes:
    return b"\n".join(
        (
            build_from
            or b"FROM docker.io/library/python@sha256:" + b"1" * 64 + b" AS build",
            b"COPY deploy/requirements-4d.lock ./deploy/requirements-4d.lock",
            b"COPY wheelhouse ./wheelhouse",
            b"COPY pyproject.toml ./pyproject.toml",
            b"COPY src ./src",
                        install or EXPECTED_OFFLINE_PIP_INSTALL.encode(),
            b"COPY deploy/alembic.ini ./deploy/alembic.ini",
            b"COPY deploy/migrations ./deploy/migrations",
            b"COPY deploy/schemas ./deploy/schemas",
            b"FROM " + RUNTIME_LIBRARIES_REFERENCE.encode() + b" AS runtime_libs",
            b"FROM " + RUNTIME_REFERENCE.encode(),
            b"ENV LD_LIBRARY_PATH=/usr/local/lib",
            b"COPY --from=build /usr/local /usr/local",
            b"COPY --from=build /opt/eva /opt/eva",
            b"COPY --from=runtime_libs /usr/lib/x86_64-linux-gnu/libffi.so.8* /usr/lib/x86_64-linux-gnu/",
            b"COPY --from=runtime_libs /usr/lib/x86_64-linux-gnu/libz.so.1* /usr/lib/x86_64-linux-gnu/",
        )
    )


def _archive(
    *,
    omit: str | None = None,
    dockerfile: bytes | None = None,
    include_external_wheelhouse: bool = False,
    mtime: int = 0,
    mode: int = 0o644,
) -> bytes:
    entries = {
        "deploy/living-core/Dockerfile": dockerfile or _dockerfile(),
        "deploy/requirements-4d.lock": b"package==1 --hash=sha256:" + b"2" * 64,
        "pyproject.toml": b"[project]\nname='eva2-living-core'\n",
        "src/eva/__init__.py": b"",
        "src/connector_sdk/__init__.py": b"",
        "deploy/alembic.ini": b"[alembic]",
        "deploy/migrations/env.py": b"",
        "deploy/schemas/example.json": b"{}",
    }
    if omit is not None:
        entries.pop(f"{omit}")
    if include_external_wheelhouse:
        entries["wheelhouse/unsealed.whl"] = b"not-a-sealed-wheel"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as bundle:
        for name, content in sorted(entries.items()):
            member = tarfile.TarInfo("eva2/" + name)
            member.size = len(content)
            member.mode = mode
            member.mtime = mtime
            bundle.addfile(member, io.BytesIO(content))
    return output.getvalue()


def _runner(archive: bytes):
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...]) -> bytes:
        calls.append(command)
        if command == (GIT_BINARY, "rev-parse", "--verify", f"{COMMIT}^{{commit}}"):
            return f"{COMMIT}\n".encode()
        if command == (GIT_BINARY, "show", "-s", "--format=%ct", COMMIT):
            return b"1787480000\n"
        if command == (GIT_BINARY, "archive", "--format=tar", "--prefix=eva2/", COMMIT, "--", "src", "deploy", "pyproject.toml", "development/design", "development/tests", "development/validation", "development/evidence/public", "development/benchmark", "development/archive/history/eva2/experiments/agent-browser-sidecar-v1",
         "development/evidence/living-core", "development/evidence/cockpit", "development/evidence/product", "development/evidence/4c", "development/reviews", "development/archive/history/root/design/fixtures", "development/archive/history/eva2/experiments/distroless-core-v1", "development/archive/history/eva2/experiments/distroless-hindsight-api-v1", "development/archive/history/eva2/experiments/harness-subagents-v1", "development/archive/history/eva2/experiments/managed-b-clean-graph-v1", "development/archive/history/eva2/experiments/playwright-mcp-action-v1", "development/archive/history/eva2/experiments/playwright-mcp-public-read-v1", "development/archive/history/eva2/experiments/playwright-mcp-sidecar-v1"):
            return archive
        raise AssertionError(command)

    return run, calls


def test_preparation_binds_one_commit_to_only_its_archive_inputs() -> None:
    archive = _archive()
    run, calls = _runner(archive)

    prepared = prepare_candidate_build_inputs(COMMIT, runner=run)

    with tarfile.open(fileobj=io.BytesIO(prepared.archive_bytes), mode="r:") as bundle:
        assert [member.name for member in bundle if member.isfile()] == sorted([
            "deploy/alembic.ini",
            "src/connector_sdk/__init__.py",
            "deploy/living-core/Dockerfile",
            "deploy/migrations/env.py",
            "pyproject.toml",
            "deploy/requirements-4d.lock",
            "deploy/schemas/example.json",
            "src/eva/__init__.py",
        ])
    assert prepared.base_image_digest == "sha256:" + "1" * 64
    assert (
        prepared.build_image_reference == "docker.io/library/python@sha256:" + "1" * 64
    )
    assert prepared.runtime_libraries_image_digest == "sha256:" + "2" * 64
    assert prepared.runtime_libraries_image_reference == RUNTIME_LIBRARIES_REFERENCE
    assert prepared.runtime_image_digest == "sha256:" + "3" * 64
    assert prepared.runtime_image_reference == RUNTIME_REFERENCE
    assert (
        prepared.source_archive_sha256
        == hashlib.sha256(prepared.archive_bytes).hexdigest()
    )
    assert prepared.source_date_epoch == 1787480000
    assert calls == [
        (GIT_BINARY, "rev-parse", "--verify", f"{COMMIT}^{{commit}}"),
        (GIT_BINARY, "show", "-s", "--format=%ct", COMMIT),
        (GIT_BINARY, "archive", "--format=tar", "--prefix=eva2/", COMMIT, "--", "src", "deploy", "pyproject.toml", "development/design", "development/tests", "development/validation", "development/evidence/public", "development/benchmark", "development/archive/history/eva2/experiments/agent-browser-sidecar-v1",
         "development/evidence/living-core", "development/evidence/cockpit", "development/evidence/product", "development/evidence/4c", "development/reviews", "development/archive/history/root/design/fixtures", "development/archive/history/eva2/experiments/distroless-core-v1", "development/archive/history/eva2/experiments/distroless-hindsight-api-v1", "development/archive/history/eva2/experiments/harness-subagents-v1", "development/archive/history/eva2/experiments/managed-b-clean-graph-v1", "development/archive/history/eva2/experiments/playwright-mcp-action-v1", "development/archive/history/eva2/experiments/playwright-mcp-public-read-v1", "development/archive/history/eva2/experiments/playwright-mcp-sidecar-v1"),
    ]
    receipt = prepared.receipt()
    assert receipt["network"] == "not_used"
    assert receipt["image_build"] == "not_started"
    assert receipt["external_inputs_required"] == ["wheelhouse"]
    assert receipt["requirements_lock_path"] == "deploy/requirements-4d.lock"
    assert "secret" not in json.dumps(receipt).casefold()


def test_preparation_refuses_nonimmutable_refs_before_git_is_opened() -> None:
    with pytest.raises(RuntimeError, match="explicit lowercase"):
        prepare_candidate_build_inputs(
            "HEAD", runner=lambda _: (_ for _ in ()).throw(AssertionError())
        )


def test_preparation_normalizes_archive_metadata_from_the_same_commit() -> None:
    first_runner, _ = _runner(_archive(mtime=1))
    second_runner, _ = _runner(_archive(mtime=2))

    first = prepare_candidate_build_inputs(COMMIT, runner=first_runner)
    second = prepare_candidate_build_inputs(COMMIT, runner=second_runner)

    assert first.archive_bytes == second.archive_bytes
    assert first.source_archive_sha256 == second.source_archive_sha256


def test_preparation_normalizes_git_tar_umask_without_making_inputs_writable() -> None:
    run, _ = _runner(_archive(mode=0o664))

    prepared = prepare_candidate_build_inputs(COMMIT, runner=run)

    with tarfile.open(fileobj=io.BytesIO(prepared.archive_bytes), mode="r:") as bundle:
        assert {member.mode for member in bundle if member.isfile()} == {0o644}


def test_preparation_refuses_archive_with_missing_copy_input() -> None:
    run, _ = _runner(_archive(omit="deploy/schemas/example.json"))

    with pytest.raises(RuntimeError, match="omits Dockerfile COPY input: deploy/schemas"):
        prepare_candidate_build_inputs(COMMIT, runner=run)


def test_preparation_refuses_changed_copy_contract() -> None:
    dockerfile = (
        b"FROM python\nCOPY deploy/requirements-4d.lock ./requirements-4d.lock\n"
        b"COPY unknown ./unknown\n"
    )
    run, _ = _runner(_archive(dockerfile=dockerfile))

    with pytest.raises(RuntimeError, match="COPY inputs differ"):
        prepare_candidate_build_inputs(COMMIT, runner=run)


def test_preparation_refuses_online_or_unsealed_package_install() -> None:
    dockerfile = _dockerfile(
        install=b"RUN python -m pip install --require-hashes -r deploy/requirements-4d.lock"
    )
    run, _ = _runner(_archive(dockerfile=dockerfile))

    with pytest.raises(RuntimeError, match="exact offline pip install"):
        prepare_candidate_build_inputs(COMMIT, runner=run)


def test_repository_dockerfile_removes_build_only_inputs_before_runtime_copy() -> None:
    dockerfile = (EVA2_ROOT / "deploy/living-core/Dockerfile").read_bytes()

    _require_offline_pip_install(dockerfile)

    assert EXPECTED_OFFLINE_PIP_INSTALL.endswith(
        "&& rm -rf ./build ./wheelhouse ./*.egg-info ./src/*.egg-info"
    )


def test_preparation_refuses_runtime_without_the_pinned_python_library_path() -> None:
    dockerfile = _dockerfile().replace(b"ENV LD_LIBRARY_PATH=/usr/local/lib\n", b"")
    run, _ = _runner(_archive(dockerfile=dockerfile))

    with pytest.raises(RuntimeError, match="runtime library path differs"):
        prepare_candidate_build_inputs(COMMIT, runner=run)


def test_preparation_refuses_library_path_only_in_the_build_stage() -> None:
    dockerfile = _dockerfile().replace(b"ENV LD_LIBRARY_PATH=/usr/local/lib\n", b"")
    dockerfile = dockerfile.replace(
        b" AS build\n", b" AS build\nENV LD_LIBRARY_PATH=/usr/local/lib\n", 1
    )
    run, _ = _runner(_archive(dockerfile=dockerfile))

    with pytest.raises(RuntimeError, match="runtime library path differs"):
        prepare_candidate_build_inputs(COMMIT, runner=run)


@pytest.mark.parametrize(
    "replacement",
    (
        b"# LD_LIBRARY_PATH=/usr/local/lib\n",
        b"ENV LD_LIBRARY_PATH=/tmp\n",
        (b"ENV LD_LIBRARY_PATH=/usr/local/lib\nENV LD_LIBRARY_PATH=/tmp\n"),
    ),
)
def test_preparation_refuses_comment_wrong_or_duplicate_runtime_library_path(
    replacement: bytes,
) -> None:
    dockerfile = _dockerfile().replace(
        b"ENV LD_LIBRARY_PATH=/usr/local/lib\n", replacement
    )
    run, _ = _runner(_archive(dockerfile=dockerfile))

    with pytest.raises(RuntimeError, match="runtime library path differs"):
        prepare_candidate_build_inputs(COMMIT, runner=run)


def test_preparation_refuses_the_historical_requirements_lock_as_build_input() -> None:
    dockerfile = b"\n".join(
        (
            b"FROM docker.io/library/python@sha256:" + b"1" * 64,
            b"COPY deploy/requirements.lock ./requirements.lock",
            b"COPY wheelhouse ./wheelhouse",
            b"RUN python -m pip install --no-cache-dir --no-index --only-binary=:all: --find-links=./wheelhouse --require-hashes -r deploy/requirements.lock",
            b"COPY src ./src",
                        b"COPY deploy/alembic.ini ./deploy/alembic.ini",
            b"COPY deploy/migrations ./deploy/migrations",
            b"COPY deploy/schemas ./deploy/schemas",
        )
    )
    run, _ = _runner(_archive(dockerfile=dockerfile))

    with pytest.raises(RuntimeError, match="COPY inputs differ"):
        prepare_candidate_build_inputs(COMMIT, runner=run)


def test_preparation_refuses_a_wheelhouse_from_the_source_archive() -> None:
    run, _ = _runner(_archive(include_external_wheelhouse=True))

    with pytest.raises(RuntimeError, match="must not contain external input"):
        prepare_candidate_build_inputs(COMMIT, runner=run)


def test_preparation_refuses_an_unpinned_or_ambiguous_base_image() -> None:
    dockerfile = _dockerfile(build_from=b"FROM python:3.12 AS build")
    run, _ = _runner(_archive(dockerfile=dockerfile))

    with pytest.raises(RuntimeError, match="build image differs"):
        prepare_candidate_build_inputs(COMMIT, runner=run)


def test_preparation_refuses_an_unpinned_runtime_image() -> None:
    dockerfile = _dockerfile().replace(
        b"FROM " + RUNTIME_REFERENCE.encode(),
        b"FROM gcr.io/distroless/cc-debian13:latest",
    )
    run, _ = _runner(_archive(dockerfile=dockerfile))

    with pytest.raises(RuntimeError, match="runtime image differs"):
        prepare_candidate_build_inputs(COMMIT, runner=run)


def test_preparation_refuses_an_extra_from_stage() -> None:
    dockerfile = _dockerfile() + b"\nFROM scratch AS hidden"
    run, _ = _runner(_archive(dockerfile=dockerfile))

    with pytest.raises(RuntimeError, match="exactly three pinned image stages"):
        prepare_candidate_build_inputs(COMMIT, runner=run)


def test_cli_prints_only_the_receipt_or_a_sanitized_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = _archive()
    run, _ = _runner(archive)
    monkeypatch.setattr(
        "tools.prepare_managed_b_candidate_context.prepare_candidate_build_inputs",
        lambda commit: prepare_candidate_build_inputs(commit, runner=run),
    )

    assert main([COMMIT]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"

    assert main(["HEAD"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "candidate source must be an explicit lowercase 40-character commit",
        "status": "failed",
    }
