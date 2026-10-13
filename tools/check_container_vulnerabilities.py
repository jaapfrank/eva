#!/usr/bin/env python3
"""Run one pinned Trivy scan against one exact, locally available OCI image.

This is deliberately a release *gate*, not a replacement vulnerability scanner.
Trivy keeps ownership of the advisory database and scanning rules; this small
wrapper only makes Eva reject a candidate that has HIGH or CRITICAL findings.
The target is exported locally and Trivy scans that archive with no network.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any


TRIVY_IMAGE = (
    "aquasec/trivy@sha256:"
    "62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"
)
_DIGEST_IMAGE = re.compile(r"^[a-z0-9./:_-]+@sha256:[0-9a-f]{64}$")


def _exact_image(value: str) -> str:
    image = value.strip()
    if not _DIGEST_IMAGE.fullmatch(image):
        raise ValueError("image must use an exact sha256 digest")
    return image


def finding_counts(payload: object) -> dict[str, int]:
    """Return only HIGH/CRITICAL counts from one Trivy JSON result."""

    if not isinstance(payload, dict):
        raise ValueError("Trivy result is invalid")
    results = payload.get("Results")
    if not isinstance(results, list):
        raise ValueError("Trivy results are invalid")
    counts = {"HIGH": 0, "CRITICAL": 0}
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("Trivy result item is invalid")
        vulnerabilities = result.get("Vulnerabilities", [])
        if vulnerabilities is None:
            continue
        if not isinstance(vulnerabilities, list):
            raise ValueError("Trivy vulnerabilities are invalid")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                raise ValueError("Trivy vulnerability is invalid")
            severity = vulnerability.get("Severity")
            if not isinstance(severity, str):
                raise ValueError("Trivy vulnerability severity is invalid")
            if severity in counts:
                counts[severity] += 1
    return counts


def trivy_command(*, image: str, archive: Path, cache_dir: Path) -> list[str]:
    """Construct the no-network scanner command; all paths stay local."""

    return [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=2g",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--memory",
        "3g",
        "--cpus",
        "1",
        "--env",
        "TRIVY_CACHE_DIR=/cache",
        "--mount",
        f"type=bind,src={cache_dir},dst=/cache",
        "--mount",
        f"type=bind,src={archive.parent},dst=/archive,readonly",
        "--entrypoint",
        "trivy",
        TRIVY_IMAGE,
        "--cache-dir",
        "/cache",
        "image",
        "--skip-db-update",
        "--input",
        f"/archive/{archive.name}",
        "--scanners",
        "vuln",
        "--severity",
        "HIGH,CRITICAL",
        "--format",
        "json",
    ]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def scan(*, image: str, cache_dir: Path) -> dict[str, int]:
    """Export a local pinned image, scan it offline, then return blocker counts."""

    target = _exact_image(image)
    if not cache_dir.is_dir():
        raise ValueError("Trivy cache directory is unavailable")
    with tempfile.TemporaryDirectory(prefix="eva2-trivy-scan-") as temporary:
        archive = Path(temporary) / "image.tar"
        os.chmod(temporary, 0o755)
        exported = _run(["docker", "save", target, "--output", str(archive)])
        if exported.returncode != 0:
            raise RuntimeError("target image is not locally available")
        archive.chmod(0o644)
        completed = _run(trivy_command(image=target, archive=archive, cache_dir=cache_dir))
        if completed.returncode != 0:
            raise RuntimeError("Trivy scan did not complete")
    try:
        payload: Any = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Trivy did not return JSON") from error
    return finding_counts(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="local image pinned by sha256 digest")
    parser.add_argument(
        "--trivy-cache-dir",
        required=True,
        type=Path,
        help="preloaded local Trivy DB cache; it is never updated by this command",
    )
    args = parser.parse_args()
    counts = scan(image=args.image, cache_dir=args.trivy_cache_dir)
    print(json.dumps({"image": _exact_image(args.image), "findings": counts}, sort_keys=True))
    if counts["HIGH"] or counts["CRITICAL"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
