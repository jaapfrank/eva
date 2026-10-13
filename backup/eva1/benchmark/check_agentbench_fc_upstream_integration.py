#!/usr/bin/env python3
"""Attest the missing upstream bridge between AgentBench FC and AgentRL.

AgentBench FC's fixed source tree contains the benchmark tasks, while the
controller and ``agentrl.worker`` entry point live in the separate AgentRL
project.  Its compose file currently selects a mutable controller image and
the task Dockerfiles install a mutable PyPI worker.  This checker makes that
boundary explicit before a development stack is materialised: a run must pin
both source trees and replace those mutable pieces.  It does not install,
start or score anything.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Callable


AGENTBENCH_COMMIT = "d1e4a10db08c87075c78972e48ecc182be03e2d5"
AGENTRL_COMMIT = "6a73409d31ba695d383b978a8ad3ef400d90c054"
_AGENTBENCH_REQUIRED = (
    "extra/docker-compose.yml",
    "src/server/tasks/dbbench/Dockerfile",
    "src/server/tasks/os_interaction/Dockerfile",
)
_AGENTBENCH_HISTORIC_ENTRYPOINTS = (
    "src/start_task.py",
    "src/server/task_controller.py",
    "src/server/task_worker.py",
)
_AGENTRL_REQUIRED = (
    "controller/Dockerfile",
    "worker/pyproject.toml",
    "worker/src/agentrl/worker/__main__.py",
)


@dataclass(frozen=True)
class IntegrationAudit:
    agentbench_commit: str | None
    agentrl_commit: str | None
    errors: tuple[str, ...]
    mutable_upstream_components: tuple[str, ...]
    missing_historic_entrypoints: tuple[str, ...]
    source_sha256: dict[str, str]
    status: str

    def payload(self) -> dict[str, object]:
        return {
            "agentbenchCommit": self.agentbench_commit,
            "agentrlCommit": self.agentrl_commit,
            "artifact": "EvaAgentBenchFcUpstreamIntegrationAudit/v1",
            "errors": list(self.errors),
            "expectedAgentBenchCommit": AGENTBENCH_COMMIT,
            "expectedAgentRLCommit": AGENTRL_COMMIT,
            "missingHistoricEntrypoints": list(self.missing_historic_entrypoints),
            "mutableUpstreamComponents": list(self.mutable_upstream_components),
            "sourceSha256": dict(sorted(self.source_sha256.items())),
            "status": self.status,
        }


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _clean_pinned_commit(
    root: Path,
    expected: str,
    label: str,
    *,
    git_output: Callable[..., str],
    errors: list[str],
) -> str | None:
    try:
        actual = git_output(root, "rev-parse", "HEAD")
        if actual != expected:
            errors.append(f"{label} upstream commit differs")
        if git_output(root, "status", "--porcelain"):
            errors.append(f"{label} upstream checkout is dirty")
        return actual
    except (OSError, subprocess.CalledProcessError):
        errors.append(f"{label} upstream checkout is unavailable")
        return None


def _source_bytes(root: Path, relative_paths: tuple[str, ...], errors: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative_path in relative_paths:
        path = root / relative_path
        try:
            hashes[relative_path] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            errors.append(f"required source path is unavailable: {relative_path}")
    return hashes


def inspect_integration(
    agentbench_root: Path,
    agentrl_root: Path,
    *,
    git_output: Callable[..., str] = _git,
) -> IntegrationAudit:
    """Inspect the two exact upstream trees without changing either of them."""

    errors: list[str] = []
    agentbench_root = Path(agentbench_root)
    agentrl_root = Path(agentrl_root)
    agentbench_commit = _clean_pinned_commit(
        agentbench_root,
        AGENTBENCH_COMMIT,
        "AgentBench",
        git_output=git_output,
        errors=errors,
    )
    agentrl_commit = _clean_pinned_commit(
        agentrl_root,
        AGENTRL_COMMIT,
        "AgentRL",
        git_output=git_output,
        errors=errors,
    )
    hashes = _source_bytes(agentbench_root, _AGENTBENCH_REQUIRED, errors)
    hashes.update(
        {
            f"agentrl/{path}": digest
            for path, digest in _source_bytes(agentrl_root, _AGENTRL_REQUIRED, errors).items()
        }
    )

    missing_entrypoints = tuple(
        path for path in _AGENTBENCH_HISTORIC_ENTRYPOINTS if not (agentbench_root / path).exists()
    )
    if missing_entrypoints != _AGENTBENCH_HISTORIC_ENTRYPOINTS:
        errors.append("AgentBench entrypoint boundary differs from the pinned source")

    mutable_components: list[str] = []
    try:
        compose = (agentbench_root / "extra/docker-compose.yml").read_text(encoding="utf-8")
        if "jingbh/agentrl-controller:latest" in compose:
            mutable_components.append("agentrl_controller_image_latest")
    except OSError:
        pass
    for path in (
        "src/server/tasks/dbbench/Dockerfile",
        "src/server/tasks/os_interaction/Dockerfile",
    ):
        try:
            dockerfile = (agentbench_root / path).read_text(encoding="utf-8")
            if "agentrl-worker" in dockerfile and not re.search(
                r"agentrl-worker\s*==\s*[0-9]", dockerfile
            ):
                mutable_components.append(f"{path}:agentrl_worker_pypi")
        except OSError:
            pass
    expected_mutable = {
        "agentrl_controller_image_latest",
        "src/server/tasks/dbbench/Dockerfile:agentrl_worker_pypi",
        "src/server/tasks/os_interaction/Dockerfile:agentrl_worker_pypi",
    }
    if set(mutable_components) != expected_mutable:
        errors.append("AgentBench mutable AgentRL boundary differs from the pinned source")

    return IntegrationAudit(
        agentbench_commit=agentbench_commit,
        agentrl_commit=agentrl_commit,
        errors=tuple(errors),
        mutable_upstream_components=tuple(mutable_components),
        missing_historic_entrypoints=missing_entrypoints,
        source_sha256=hashes,
        status="requires_pinned_agentrl_sidecars" if not errors else "not_ready",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agentbench-root", required=True, type=Path)
    parser.add_argument("--agentrl-root", required=True, type=Path)
    arguments = parser.parse_args()
    audit = inspect_integration(arguments.agentbench_root, arguments.agentrl_root)
    print(json.dumps(audit.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if audit.status == "requires_pinned_agentrl_sidecars" else 2


if __name__ == "__main__":
    raise SystemExit(main())
