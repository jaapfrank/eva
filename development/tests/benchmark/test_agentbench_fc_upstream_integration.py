from __future__ import annotations

from pathlib import Path
import sys


BENCHMARK = Path(__file__).resolve().parents[3] / "development/benchmark"
sys.path.insert(0, str(BENCHMARK))
import check_agentbench_fc_upstream_integration as audit_module  # noqa: E402


def _write(root: Path, relative_path: str, contents: str) -> None:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    agentbench = tmp_path / "AgentBench"
    agentrl = tmp_path / "AgentRL"
    agentbench.mkdir()
    agentrl.mkdir()
    _write(
        agentbench,
        "extra/docker-compose.yml",
        "image: jingbh/agentrl-controller:latest\n",
    )
    for path in (
        "src/server/tasks/dbbench/Dockerfile",
        "src/server/tasks/os_interaction/Dockerfile",
    ):
        _write(agentbench, path, "RUN pip install agentrl-worker\n")
    _write(agentrl, "controller/Dockerfile", "FROM scratch\n")
    _write(agentrl, "worker/pyproject.toml", "[project]\nname = 'agentrl-worker'\n")
    _write(agentrl, "worker/src/agentrl/worker/__main__.py", "pass\n")
    return agentbench, agentrl


def _git(*arguments: object) -> str:
    root = Path(arguments[0]).name
    command = arguments[1:]
    expected = (
        audit_module.AGENTBENCH_COMMIT if root == "AgentBench" else audit_module.AGENTRL_COMMIT
    )
    if command == ("rev-parse", "HEAD"):
        return expected
    assert command == ("status", "--porcelain")
    return ""


def test_reports_the_pinned_upstream_boundary_without_running_it(tmp_path: Path) -> None:
    agentbench, agentrl = _roots(tmp_path)

    audit = audit_module.inspect_integration(agentbench, agentrl, git_output=_git)

    assert audit.status == "requires_pinned_agentrl_sidecars"
    assert audit.errors == ()
    assert audit.missing_historic_entrypoints == (
        "src/start_task.py",
        "src/server/task_controller.py",
        "src/server/task_worker.py",
    )
    assert audit.mutable_upstream_components == (
        "agentrl_controller_image_latest",
        "src/server/tasks/dbbench/Dockerfile:agentrl_worker_pypi",
        "src/server/tasks/os_interaction/Dockerfile:agentrl_worker_pypi",
    )
    assert audit.payload()["artifact"] == "EvaAgentBenchFcUpstreamIntegrationAudit/v1"


def test_rejects_drift_or_an_unexpected_upstream_boundary(tmp_path: Path) -> None:
    agentbench, agentrl = _roots(tmp_path)
    _write(agentbench, "src/start_task.py", "pass\n")

    def drifted_git(*arguments: object) -> str:
        command = arguments[1:]
        if command == ("rev-parse", "HEAD"):
            return "0" * 40
        return "dirty"

    audit = audit_module.inspect_integration(agentbench, agentrl, git_output=drifted_git)

    assert audit.status == "not_ready"
    assert "AgentBench upstream commit differs" in audit.errors
    assert "AgentRL upstream checkout is dirty" in audit.errors
    assert "AgentBench entrypoint boundary differs from the pinned source" in audit.errors
