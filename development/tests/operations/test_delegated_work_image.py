from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "deploy/delegated-work/Dockerfile"
SOURCE_REFRESH_DOCKERFILE = ROOT / "deploy/delegated-work/Dockerfile.source-refresh"


def test_delegated_worker_image_is_narrow_offline_and_non_root() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert (
        "FROM docker.io/library/python@sha256:"
        "a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134"
    ) in text
    assert (
        "FROM cgr.dev/chainguard/python@sha256:"
        "ee37f5e4fb445732409626797dccb6f2a6337872def2bab48729ee61b335fa77"
    ) in text
    assert "COPY deploy/requirements-delegated-work.lock" in text
    assert "--no-index --only-binary=:all:" in text
    assert "--require-hashes -r deploy/requirements-delegated-work.lock" in text
    assert "COPY src ./src" in text
    assert "COPY --from=builder /usr/local /usr/local" in text
    assert "COPY --from=builder /usr/lib/x86_64-linux-gnu/libssl.so.3" in text
    assert "USER 65532:65532" in text
    assert (
        'CMD ["/usr/local/bin/python", "-m", "eva.bootstrap.delegated_work_service", "--check"]'
        in text
    )
    assert 'ENTRYPOINT ["/usr/local/bin/python"]' in text
    assert 'CMD ["-m", "eva.bootstrap.delegated_work_service"]' in text

    for forbidden in (
        "deploy/requirements-4d.lock",
        "deploy/requirements.lock",
        "connector_sdk",
        "migrations",
        "schemas",
        "uvicorn",
        "EXPOSE",
    ):
        assert forbidden not in text


def test_delegated_worker_source_refresh_only_replaces_sealed_source() -> None:
    lines = tuple(
        line.strip()
        for line in SOURCE_REFRESH_DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert not any(
        line.split(maxsplit=1)[0].upper() in {"ADD", "RUN"} for line in lines
    )
    assert (
        "ARG PROVEN_WORKER_IMAGE=eva2/delegated-work-clean@sha256:"
        "170f9e6b0fdf01aba499c8820c581167b586c4e3e1c70609e85fc47f6c46ef36"
    ) in lines
    assert "FROM ${PROVEN_WORKER_IMAGE}" in lines
    assert "COPY --chown=65532:65532 src /opt/eva/src" in lines
    assert "USER 65532:65532" in lines
    text = "\n".join(lines)
    assert "ARG SOURCE_COMMIT" in text
    assert "ARG SOURCE_DATE_EPOCH" in text
    assert "io.eva2.source-commit=${SOURCE_COMMIT}" in text
    assert "io.eva2.source-date-epoch=${SOURCE_DATE_EPOCH}" in text
    assert 'io.eva2.source-refresh="git-archive-v1"' in text
