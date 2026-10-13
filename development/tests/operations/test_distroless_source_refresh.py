from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "experiments/distroless-core-v1/Dockerfile.source-refresh"


def test_source_refresh_only_replaces_reviewed_git_archive_inputs() -> None:
    lines = tuple(
        line.strip()
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert not any(
        line.split(maxsplit=1)[0].upper() in {"ADD", "RUN"} for line in lines
    )
    assert {
        "COPY --chown=65532:65532 src /opt/eva/src",
        "COPY --chown=65532:65532 connector_sdk /opt/eva/connector_sdk",
        "COPY --chown=65532:65532 deploy/alembic.ini /opt/eva/alembic.ini",
        "COPY --chown=65532:65532 migrations /opt/eva/migrations",
        "COPY --chown=65532:65532 schemas /opt/eva/schemas",
    } <= set(lines)
    assert "FROM ${PROVEN_CORE_IMAGE} AS proven_core" in lines
    assert "FROM ${RUNTIME_LIBS_IMAGE} AS runtime_libs" in lines
    assert "FROM ${FINAL_BASE_IMAGE}" in lines
    assert "COPY --from=proven_core /usr/local /usr/local" in lines
    assert "COPY --from=proven_core /opt/eva /opt/eva" in lines
    assert any("libffi.so.8" in line for line in lines)
    assert any("libz.so.1" in line for line in lines)
    assert "LD_LIBRARY_PATH=/usr/local/lib \\" in lines
    text = "\n".join(lines).casefold()
    for forbidden in ("curl", "wget", "apt", "apk", "pip", "npm", "git clone"):
        assert forbidden not in text


def test_source_refresh_binds_exact_base_commit_and_epoch_labels() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG SOURCE_COMMIT" in text
    assert "ARG SOURCE_DATE_EPOCH" in text
    assert "io.eva2.source-commit=${SOURCE_COMMIT}" in text
    assert "io.eva2.source-date-epoch=${SOURCE_DATE_EPOCH}" in text
    assert 'io.eva2.source-refresh="git-archive-v1"' in text
    assert "gcr.io/distroless/cc-debian13@sha256:" in text
    assert "python@sha256:" in text
