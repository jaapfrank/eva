from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "benchmark" / "agentbench-fc-v1" / "Dockerfile"
REQUIREMENTS = ROOT / "benchmark" / "agentbench-fc-v1" / "requirements.upstream.txt"
RECEIPT = ROOT / "eva2" / "evidence" / "living-core" / "agentbench-fc-environment-2026-08-27.json"


def test_agentbench_fc_container_keeps_the_exact_upstream_requirements_cpu_only() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")

    assert (
        "python@sha256:2d97f6910b16bd338d3060f261f53f144965f755599aab1acda1e13cf1731b1b"
        in source
    )
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in source
    assert "--only-binary=:all:" in source
    assert "pip check" in source
    assert "USER 65532:65532" in source
    assert "COPY AgentBench" not in source


def test_agentbench_fc_container_requirements_match_the_pinned_upstream_bytes() -> None:
    assert hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest() == (
        "2a2ecd2cc0e25ef7155a088604213682d95912c94adfdab9a865ee4b4f0e5da2"
    )


def test_agentbench_fc_environment_receipt_binds_the_built_cpu_image() -> None:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))

    assert receipt["artifact"] == "EvaAgentBenchFcEnvironmentReceipt/v1"
    assert receipt["status"] == "environment_ready_no_tasks_started"
    assert receipt["environment"]["python"] == "3.9.25"
    assert receipt["environment"]["runtimeUser"] == "65532:65532"
    assert receipt["environment"]["image"] == (
        "eva2/agentbench-fc@sha256:"
        "37e7e64b0ecf1f69eb73bdcc13889fd810ef350863570765250719bd6ef85184"
    )
    assert receipt["inputs"]["dockerfile"]["sha256"] == hashlib.sha256(
        DOCKERFILE.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["requirements"]["sha256"] == hashlib.sha256(
        REQUIREMENTS.read_bytes()
    ).hexdigest()
    assert receipt["installedDirectPackages"]["torch"].endswith("+cpu")
