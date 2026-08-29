"""Test support for an ephemeral, networkless managed-B candidate smoke."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from typing import Callable

from tools.managed_b_candidate.candidate_image import CandidateImage, validate_offline_candidate_image


_PROGRAM = (
    "from eva.bootstrap.runtime import create_runtime_app; "
    "assert callable(create_runtime_app); print('runtime-import-ok')"
)


@dataclass(frozen=True, slots=True)
class RuntimeSmoke:
    receipt: dict[str, object]


Runner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)


def run_candidate_runtime_smoke(
    candidate: CandidateImage, *, runner: Runner = _run
) -> RuntimeSmoke:
    """Run one automatically removed container with no network, mounts or secrets."""

    validate_offline_candidate_image(candidate, runner=runner)
    command = [
        "docker", "run", "--pull=never", "--rm", "--network=none", "--read-only",
        "--user=65532:65532", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--entrypoint", "python", candidate.image_id, "-c", _PROGRAM,
    ]
    result = runner(command, 30)
    if result.stdout.strip() != "runtime-import-ok" or result.stderr:
        raise RuntimeError("managed B candidate runtime smoke differs")
    return RuntimeSmoke({
        "candidate_image_id": candidate.image_id,
        "contract": "EvaManagedBCandidateRuntimeSmokeReceipt/v1",
        "network_used": False,
        "status": "runtime_imported_nonroot",
        "nonclaims": [
            "The temporary container used no mounts, secrets, B configuration, or B runtime input.",
            "The container was automatically removed; no B service was started or replaced.",
        ],
    })


__all__ = ["RuntimeSmoke", "run_candidate_runtime_smoke"]
