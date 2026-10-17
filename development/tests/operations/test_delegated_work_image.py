from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_worker_requires_explicit_shared_core_release_and_dependency_check():
    text = (ROOT / "deploy/delegated-work/Dockerfile").read_text()
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert lines[:2] == ["ARG CORE_IMAGE", "FROM ${CORE_IMAGE}"]
    assert "verify_delegated_provider_dependencies()" in text
    assert "pip install" not in text
    assert "COPY" not in text
    assert 'USER 65532:65532' in text
    assert '"eva.bootstrap.delegated_work_service", "--check"' in text
    assert 'CMD ["-m", "eva.bootstrap.delegated_work_service"]' in text


def test_source_refresh_cannot_choose_an_obsolete_parent_implicitly():
    text = (ROOT / "deploy/delegated-work/Dockerfile.source-refresh").read_text()
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert lines[:2] == ["ARG PROVEN_WORKER_IMAGE", "FROM ${PROVEN_WORKER_IMAGE}"]
    assert not any(line.split()[0].upper() in {"ADD", "RUN"} for line in lines)
    assert "COPY --chown=65532:65532 src /opt/eva/src" in lines
    assert "USER 65532:65532" in lines
    assert "io.eva2.source-commit=${SOURCE_COMMIT}" in text
    assert "io.eva2.source-date-epoch=${SOURCE_DATE_EPOCH}" in text
