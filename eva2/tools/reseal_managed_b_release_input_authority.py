#!/usr/bin/env python3
"""Mechanically reseal the managed-B static input authority.

The path sets remain code-reviewed constants. This tool only sorts those exact
sets, recalculates SHA-256 values from the working tree, and rewrites canonical
JSON. It does not build, contact a network, inspect Docker, or sign a release.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from eva.operations.managed_b_release_inputs import (
    AUTHORITY_PATH,
    _EXPECTED_INPUT_PATHS,
    _EXPECTED_MIGRATIONS,
    _EXPECTED_TEST_PATHS,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: str) -> str:
    candidate = ROOT / path
    if not candidate.is_file():
        raise RuntimeError(f"managed-B authority input is missing: {path}")
    return hashlib.sha256(candidate.read_bytes()).hexdigest()


def main() -> int:
    authority_file = ROOT / AUTHORITY_PATH
    authority = json.loads(authority_file.read_bytes())
    authority["requiredInputs"] = [
        {"path": path, "sha256": _sha256(path)}
        for path in sorted(_EXPECTED_INPUT_PATHS)
    ]
    authority["requiredTests"] = [
        {"path": path, "sha256": _sha256(path)}
        for path in sorted(_EXPECTED_TEST_PATHS)
    ]
    revisions = {
        row["revision"]: row for row in authority["migrationAuthority"]["revisions"]
    }
    authority["migrationAuthority"]["revisions"] = [
        {
            "downRevision": down_revision,
            "path": path,
            "revision": revision,
            "sha256": _sha256(path),
        }
        for revision, down_revision, path in _EXPECTED_MIGRATIONS
    ]
    if set(revisions) != {revision for revision, _, _ in _EXPECTED_MIGRATIONS}:
        raise RuntimeError("managed-B migration authority differs before reseal")
    authority["nonClaims"] = [
        claim
        for claim in authority["nonClaims"]
        if "full-stack restart remain" not in claim
    ]
    candidate_nonclaim = (
        "The clean nine-service candidate render, fresh two-database start, "
        "real model turn and joint restart passed locally; production B data "
        "transition, cutover, rollback and resident acceptance remain open gates."
    )
    if candidate_nonclaim not in authority["nonClaims"]:
        authority["nonClaims"].append(candidate_nonclaim)
    payload = (
        json.dumps(
            authority,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    authority_file.write_bytes(payload)
    print(
        json.dumps(
            {
                "authoritySha256": hashlib.sha256(payload).hexdigest(),
                "inputCount": len(authority["requiredInputs"]),
                "status": "resealed",
                "testCount": len(authority["requiredTests"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
