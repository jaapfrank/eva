from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "experiments/distroless-core-v1/Dockerfile.source-refresh"


def test_source_refresh_only_replaces_reviewed_git_archive_inputs() -> None:
    lines = tuple(
        line.strip()
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert lines[:2] == ("ARG PROVEN_CORE_IMAGE", "FROM ${PROVEN_CORE_IMAGE}")
    assert not any(
        line.split(maxsplit=1)[0].upper() in {"ADD", "RUN"} for line in lines
    )
    assert {
        "COPY --chown=65532:65532 src /opt/eva/src",
        "COPY --chown=65532:65532 connector_sdk /opt/eva/connector_sdk",
        "COPY --chown=65532:65532 alembic.ini /opt/eva/alembic.ini",
        "COPY --chown=65532:65532 migrations /opt/eva/migrations",
        "COPY --chown=65532:65532 schemas /opt/eva/schemas",
    } <= set(lines)
    text = "\n".join(lines).casefold()
    for forbidden in ("curl", "wget", "apt", "apk", "pip", "npm", "git clone"):
        assert forbidden not in text


def test_source_refresh_binds_exact_base_commit_and_epoch_labels() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG SOURCE_COMMIT" in text
    assert "ARG SOURCE_DATE_EPOCH" in text
    assert "io.eva2.source-commit=${SOURCE_COMMIT}" in text
    assert "io.eva2.source-date-epoch=${SOURCE_DATE_EPOCH}" in text
    assert "io.eva2.source-refresh=\"git-archive-v1\"" in text
