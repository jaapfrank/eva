from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy/delegated-work/Dockerfile"


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
    assert "COPY requirements-delegated-work.lock" in text
    assert "--no-index --only-binary=:all:" in text
    assert "--require-hashes -r requirements-delegated-work.lock" in text
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
        "requirements-4d.lock",
        "requirements.lock",
        "connector_sdk",
        "migrations",
        "schemas",
        "uvicorn",
        "EXPOSE",
    ):
        assert forbidden not in text
