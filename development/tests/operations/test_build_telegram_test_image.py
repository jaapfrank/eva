from __future__ import annotations

import io
import tarfile

import pytest

from tools.build_telegram_test_image import (
    EXPECTED_REQUIREMENTS_LOCK_SHA256,
    PARENT_IMAGE_TAG,
    image_tag,
    validate_telegram_test_inputs,
)
from tools.prepare_managed_b_candidate_context import CandidateBuildInputs


COMMIT = "a" * 40


def _inputs(*, dockerfile: bytes, lock_sha256: str = EXPECTED_REQUIREMENTS_LOCK_SHA256) -> CandidateBuildInputs:
    files = {
        "deploy/living-core/Dockerfile": b"\n".join(
            (
                b"FROM docker.io/library/python@sha256:" + b"1" * 64,
                b"COPY deploy/requirements-4d.lock ./requirements-4d.lock",
                b"COPY wheelhouse ./wheelhouse",
                b"RUN python -m pip install --no-cache-dir --no-index --only-binary=:all: --find-links=./wheelhouse --require-hashes -r deploy/requirements-4d.lock",
                b"COPY src ./src",
                b"COPY connector_sdk ./connector_sdk",
                b"COPY deploy/alembic.ini ./alembic.ini",
                b"COPY migrations ./migrations",
                b"COPY schemas ./schemas",
            )
        ),
        "deploy/living-core/Dockerfile.telegram-test": dockerfile,
        "deploy/requirements-4d.lock": b"lock",
        "src/eva/__init__.py": b"",
        "src/connector_sdk/__init__.py": b"",
        "deploy/alembic.ini": b"[alembic]",
        "deploy/migrations/env.py": b"",
        "deploy/schemas/example.json": b"{}",
    }
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w", format=tarfile.PAX_FORMAT) as bundle:
        for name, contents in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size = len(contents)
            member.mode = 0o644
            member.mtime = 1
            bundle.addfile(member, io.BytesIO(contents))
    value = archive.getvalue()
    # The helper needs a canonical archive for its existing source validator;
    # this unit only exercises the narrow Dockerfile contract afterwards.
    return CandidateBuildInputs(
        archive_bytes=value,
        base_image_digest="sha256:" + "1" * 64,
        build_image_reference="docker.io/library/python@sha256:" + "1" * 64,
        runtime_libraries_image_digest="sha256:" + "2" * 64,
        runtime_libraries_image_reference=(
            "docker.io/library/python@sha256:" + "2" * 64
        ),
        runtime_image_digest="sha256:" + "3" * 64,
        runtime_image_reference=(
            "gcr.io/distroless/cc-debian13@sha256:" + "3" * 64
        ),
        dockerfile_sha256="",
        requirements_lock_sha256=lock_sha256,
        source_archive_sha256="",
        source_commit=COMMIT,
        source_date_epoch=1,
    )


def _dockerfile(*, extra: bytes = b"") -> bytes:
    return b"\n".join(
        (
            f"FROM {PARENT_IMAGE_TAG}".encode(),
            b"ARG EVA_TEST_SOURCE_COMMIT",
            b'LABEL io.eva2.telegram-test-image="v1" ' + b"\\",
            b'      io.eva2.source-commit="${EVA_TEST_SOURCE_COMMIT}"',
            b"USER root",
            b"COPY --chown=65532:65532 src ./src",
            b"COPY --chown=65532:65532 connector_sdk ./connector_sdk",
            b"COPY --chown=65532:65532 deploy/alembic.ini ./alembic.ini",
            b"COPY --chown=65532:65532 migrations ./migrations",
            b"COPY --chown=65532:65532 schemas ./schemas",
            b"COPY --chown=65532:65532 src/tools/__init__.py ./tools/__init__.py",
            b"COPY --chown=65532:65532 src/tools/telegram_test_core.py ./tools/telegram_test_core.py",
            b"COPY --chown=65532:65532 src/tools/telegram_test_ingress.py ./tools/telegram_test_ingress.py",
            b"USER 65532:65532",
            extra,
        )
    )


def test_image_tag_is_deterministically_tied_to_the_source_commit() -> None:
    assert image_tag(COMMIT) == "eva2-telegram-test:git-aaaaaaaaaaaa"


def test_validator_rejects_different_dependency_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tools.build_telegram_test_image.validate_candidate_build_inputs",
        lambda _inputs: {"deploy/living-core/Dockerfile.telegram-test": (_dockerfile(), 0o644)},
    )
    with pytest.raises(RuntimeError, match="dependency lock"):
        validate_telegram_test_inputs(_inputs(dockerfile=_dockerfile(), lock_sha256="0" * 64))


def test_validator_rejects_a_network_or_package_build_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tools.build_telegram_test_image.validate_candidate_build_inputs",
        lambda _inputs: {"deploy/living-core/Dockerfile.telegram-test": (_dockerfile(extra=b"RUN pip install surprise"), 0o644)},
    )
    with pytest.raises(RuntimeError, match="sealed copy-only"):
        validate_telegram_test_inputs(_inputs(dockerfile=_dockerfile()))
