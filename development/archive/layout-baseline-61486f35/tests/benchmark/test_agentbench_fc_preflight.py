from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from pathlib import Path
import sys


BENCHMARK = Path(__file__).resolve().parents[2] / "benchmark"
sys.path.insert(0, str(BENCHMARK))
import check_agentbench_fc_environment as preflight  # noqa: E402


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "AgentBench"
    root.mkdir()
    return root


def _expected_requirements() -> bytes:
    return b"\n".join(
        [
            b"numpy~=1.23.5",
            b"pydantic~=1.10.12",
            b"requests~=2.28.1",
            b"tqdm~=4.65.0",
            b"pyyaml~=6.0",
            b"jsonlines~=3.1.0",
            b"aiohttp~=3.8.4",
            b"uvicorn~=0.22.0",
            b"fastapi~=0.101.1",
            b"urllib3~=1.26.15",
            b"mysql-connector-python==8.0.33",
            b"docker==6.1.2",
            b"SPARQLWrapper",
            b"networkx~=2.8.4",
            b"anthropic~=0.4.1",
            b"fschat~=0.2.31",
            b"accelerate~=0.23.0",
            b"transformers~=4.34.0",
        ]
    )


def _git(*arguments: object) -> str:
    command = arguments[1:]
    if command == ("rev-parse", "HEAD"):
        return preflight.UPSTREAM_COMMIT
    assert command == ("status", "--porcelain")
    return ""


def _installed(_distribution: str) -> str:
    return "1.0"


def test_accepts_exact_clean_python39_environment(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "requirements.txt").write_bytes(_expected_requirements())
    receipt = preflight.inspect_environment(
        root,
        python_version=(3, 9),
        git_output=_git,
        distribution_version=_installed,
    )

    assert receipt.status == "ready_for_manual_setup"
    assert receipt.errors == ()
    assert receipt.requirements_sha256 == preflight.REQUIREMENTS_SHA256
    assert receipt.payload()["expectedPython"] == "3.9"


def test_rejects_python_commit_requirements_and_missing_distribution_drift(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    (root / "requirements.txt").write_text("changed\n", encoding="utf-8")

    def wrong_git(*arguments: object) -> str:
        command = arguments[1:]
        if command == ("rev-parse", "HEAD"):
            return "0" * 40
        return "dirty"

    def missing(distribution: str) -> str:
        if distribution == "numpy":
            raise PackageNotFoundError
        return "1.0"

    receipt = preflight.inspect_environment(
        root,
        python_version=(3, 12),
        git_output=wrong_git,
        distribution_version=missing,
    )

    assert receipt.status == "not_ready"
    assert receipt.missing_distributions == ("numpy",)
    assert receipt.errors == (
        "AgentBench FC requires an isolated Python 3.9 interpreter",
        "AgentBench upstream commit differs",
        "AgentBench upstream checkout is dirty",
        "AgentBench requirements file differs",
        "AgentBench Python distributions are missing",
    )


def test_rejects_an_unavailable_upstream_checkout(tmp_path: Path) -> None:
    def unavailable(*_arguments: object) -> str:
        raise OSError("git unavailable")

    receipt = preflight.inspect_environment(
        tmp_path / "absent",
        python_version=(3, 9),
        git_output=unavailable,
        distribution_version=_installed,
    )

    assert receipt.status == "not_ready"
    assert receipt.actual_commit is None
    assert "AgentBench upstream checkout is unavailable" in receipt.errors
    assert "AgentBench requirements file is unavailable" in receipt.errors
