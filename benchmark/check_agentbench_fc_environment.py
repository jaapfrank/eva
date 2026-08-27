#!/usr/bin/env python3
"""Fail-closed preflight for the pinned AgentBench FC development environment.

It neither installs packages nor starts Docker.  Its only job is to make the
required upstream checkout, Python interpreter and imported packages explicit
before an operator chooses to materialize the heavy task environment.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from importlib.metadata import PackageNotFoundError, version as package_version
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable


UPSTREAM_COMMIT = "d1e4a10db08c87075c78972e48ecc182be03e2d5"
REQUIREMENTS_SHA256 = "2a2ecd2cc0e25ef7155a088604213682d95912c94adfdab9a865ee4b4f0e5da2"
REQUIRED_DISTRIBUTIONS = (
    "numpy",
    "pydantic",
    "requests",
    "tqdm",
    "PyYAML",
    "jsonlines",
    "aiohttp",
    "uvicorn",
    "fastapi",
    "urllib3",
    "mysql-connector-python",
    "docker",
    "SPARQLWrapper",
    "networkx",
    "anthropic",
    "fschat",
    "accelerate",
    "transformers",
)


@dataclass(frozen=True, slots=True)
class AgentBenchPreflight:
    actual_commit: str | None
    errors: tuple[str, ...]
    missing_distributions: tuple[str, ...]
    requirements_sha256: str | None
    status: str

    def payload(self) -> dict[str, object]:
        return {
            "actualCommit": self.actual_commit,
            "contract": "EvaAgentBenchFcPreflight/v1",
            "errors": list(self.errors),
            "expectedCommit": UPSTREAM_COMMIT,
            "expectedPython": "3.9",
            "expectedRequirementsSha256": REQUIREMENTS_SHA256,
            "missingDistributions": list(self.missing_distributions),
            "requirementsSha256": self.requirements_sha256,
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


def inspect_environment(
    upstream_root: Path,
    *,
    python_version: tuple[int, int] | None = None,
    git_output: Callable[..., str] = _git,
    distribution_version: Callable[[str], str] = package_version,
) -> AgentBenchPreflight:
    """Check the pinned runner prerequisites without changing the environment."""

    errors: list[str] = []
    actual_commit: str | None = None
    requirements_sha256: str | None = None
    if python_version is None:
        python_version = sys.version_info[:2]
    if python_version != (3, 9):
        errors.append("AgentBench FC requires an isolated Python 3.9 interpreter")

    root = Path(upstream_root)
    requirements = root / "requirements.txt"
    try:
        actual_commit = git_output(root, "rev-parse", "HEAD")
        if actual_commit != UPSTREAM_COMMIT:
            errors.append("AgentBench upstream commit differs")
        if git_output(root, "status", "--porcelain"):
            errors.append("AgentBench upstream checkout is dirty")
    except (OSError, subprocess.CalledProcessError):
        errors.append("AgentBench upstream checkout is unavailable")

    try:
        requirements_sha256 = hashlib.sha256(requirements.read_bytes()).hexdigest()
        if requirements_sha256 != REQUIREMENTS_SHA256:
            errors.append("AgentBench requirements file differs")
    except OSError:
        errors.append("AgentBench requirements file is unavailable")

    missing: list[str] = []
    for distribution in REQUIRED_DISTRIBUTIONS:
        try:
            distribution_version(distribution)
        except PackageNotFoundError:
            missing.append(distribution)
    if missing:
        errors.append("AgentBench Python distributions are missing")
    return AgentBenchPreflight(
        actual_commit=actual_commit,
        errors=tuple(errors),
        missing_distributions=tuple(missing),
        requirements_sha256=requirements_sha256,
        status="ready_for_manual_setup" if not errors else "not_ready",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-root", type=Path, required=True)
    arguments = parser.parse_args()
    receipt = inspect_environment(arguments.upstream_root)
    print(json.dumps(receipt.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if receipt.status == "ready_for_manual_setup" else 2


if __name__ == "__main__":
    raise SystemExit(main())
