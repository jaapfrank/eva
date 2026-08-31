#!/usr/bin/env python3
"""Mechanically reseal the managed-B static input authority.

The path sets remain code-reviewed constants. This tool only sorts those exact
sets, recalculates SHA-256 values from the working tree, and rewrites canonical
JSON. It does not build, contact a network, inspect Docker, or sign a release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools.managed_b_candidate.release_inputs import (
    AUTHORITY_PATH,
    _COCKPIT_EVIDENCE_PATH,
    _EXPECTED_INPUT_PATHS_SHA256,
    _EXPECTED_MIGRATIONS,
    _EXPECTED_TEST_PATHS_SHA256,
    _NONCLAIMS,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: str) -> str:
    candidate = ROOT / path
    if not candidate.is_file():
        raise RuntimeError(f"managed-B authority input is missing: {path}")
    return hashlib.sha256(candidate.read_bytes()).hexdigest()


def _path_set_sha256(paths: tuple[str, ...]) -> str:
    payload = (
        json.dumps(paths, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--admit-migration",
        help="explicitly acknowledge one newly reviewed expected migration",
    )
    parser.add_argument(
        "--admit-input",
        action="append",
        default=[],
        help="explicitly add one reviewed static release input path",
    )
    parser.add_argument(
        "--replace-input",
        help="explicitly replace OLD_PATH=NEW_PATH in the reviewed input set",
    )
    parser.add_argument(
        "--replace-test",
        help="explicitly replace OLD_PATH=NEW_PATH in the reviewed test set",
    )
    parser.add_argument(
        "--admit-test",
        action="append",
        default=[],
        help="explicitly add one reviewed static release test path",
    )
    parser.add_argument(
        "--admit-cockpit-source",
        help="explicitly add CLAIM_ID=PATH to one existing cockpit claim",
    )
    parser.add_argument(
        "--replace-cockpit-source",
        help=(
            "explicitly replace CLAIM_ID=OLD_PATH=NEW_PATH in one existing "
            "cockpit claim"
        ),
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    authority_file = ROOT / AUTHORITY_PATH
    authority = json.loads(authority_file.read_bytes())
    for field, replacement in (
        ("requiredInputs", arguments.replace_input),
        ("requiredTests", arguments.replace_test),
    ):
        if replacement is None:
            continue
        old_path, separator, new_path = replacement.partition("=")
        rows = authority[field]
        known = {row["path"] for row in rows}
        if (
            not separator
            or old_path not in known
            or new_path in known
            or not new_path
            or new_path.startswith("/")
            or ".." in Path(new_path).parts
        ):
            raise RuntimeError("replacement managed-B authority path is invalid")
        matching = [row for row in rows if row["path"] == old_path]
        if len(matching) != 1:
            raise RuntimeError("replacement managed-B authority path is ambiguous")
        matching[0]["path"] = new_path
        matching[0]["sha256"] = _sha256(new_path)
        rows.sort(key=lambda row: row["path"])
    for field, admitted in (
        ("requiredInputs", arguments.admit_input),
        ("requiredTests", arguments.admit_test),
    ):
        rows = authority[field]
        known = {row["path"] for row in rows}
        for path in admitted:
            if path in known or not path or path.startswith("/") or ".." in Path(path).parts:
                raise RuntimeError("admitted managed-B authority path is invalid")
            rows.append({"path": path, "sha256": _sha256(path)})
            known.add(path)
        rows.sort(key=lambda row: row["path"])
    input_paths = tuple(row["path"] for row in authority["requiredInputs"])
    test_paths = tuple(row["path"] for row in authority["requiredTests"])
    if _path_set_sha256(input_paths) != _EXPECTED_INPUT_PATHS_SHA256:
        raise RuntimeError("managed-B required input path set differs")
    if _path_set_sha256(test_paths) != _EXPECTED_TEST_PATHS_SHA256:
        raise RuntimeError("managed-B required test path set differs")
    cockpit_file = ROOT / _COCKPIT_EVIDENCE_PATH
    cockpit = json.loads(cockpit_file.read_bytes())
    if arguments.replace_cockpit_source is not None:
        parts = arguments.replace_cockpit_source.split("=", 2)
        if len(parts) != 3:
            raise RuntimeError("replacement cockpit source is invalid")
        claim_id, old_path, new_path = parts
        if (
            not claim_id
            or not old_path
            or new_path not in input_paths
            or old_path == new_path
        ):
            raise RuntimeError("replacement cockpit source is invalid")
        matching = [
            entry for entry in cockpit["entries"] if entry.get("claimId") == claim_id
        ]
        if len(matching) != 1:
            raise RuntimeError("replacement cockpit claim is invalid")
        sources = matching[0]["sources"]
        old_sources = [source for source in sources if source.get("path") == old_path]
        if len(old_sources) != 1 or new_path in {
            source.get("path") for source in sources
        }:
            raise RuntimeError("replacement cockpit source is invalid")
        old_sources[0]["path"] = new_path
        old_sources[0]["sha256"] = _sha256(new_path)
        sources.sort(key=lambda source: source["path"])
    if arguments.admit_cockpit_source is not None:
        claim_id, separator, source_path = arguments.admit_cockpit_source.partition("=")
        if not separator or source_path not in input_paths:
            raise RuntimeError("admitted cockpit source is invalid")
        matching = [
            entry for entry in cockpit["entries"] if entry.get("claimId") == claim_id
        ]
        if len(matching) != 1:
            raise RuntimeError("admitted cockpit claim is invalid")
        sources = matching[0]["sources"]
        if source_path not in {source["path"] for source in sources}:
            sources.append({"path": source_path, "sha256": _sha256(source_path)})
        sources.sort(key=lambda source: source["path"])
    for entry in cockpit["entries"]:
        for source in entry["sources"]:
            source["sha256"] = _sha256(source["path"])
    cockpit_payload = (
        json.dumps(
            cockpit,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    cockpit_file.write_bytes(cockpit_payload)
    authority["requiredInputs"] = [
        {"path": path, "sha256": _sha256(path)}
        for path in input_paths
    ]
    authority["requiredTests"] = [
        {"path": path, "sha256": _sha256(path)}
        for path in test_paths
    ]
    revisions = {
        row["revision"]: row for row in authority["migrationAuthority"]["revisions"]
    }
    expected_revisions = {revision for revision, _, _ in _EXPECTED_MIGRATIONS}
    current_revisions = set(revisions)
    missing_revisions = expected_revisions - current_revisions
    extra_revisions = current_revisions - expected_revisions
    admitted = set() if arguments.admit_migration is None else {arguments.admit_migration}
    if extra_revisions or missing_revisions != admitted:
        raise RuntimeError("managed-B migration authority differs before reseal")
    authority["migrationAuthority"]["revisions"] = [
        {
            "downRevision": down_revision,
            "path": path,
            "revision": revision,
            "sha256": _sha256(path),
        }
        for revision, down_revision, path in _EXPECTED_MIGRATIONS
    ]
    authority["migrationAuthority"]["head"] = _EXPECTED_MIGRATIONS[-1][0]
    manifest = json.loads((ROOT / "schemas/contracts/manifest.json").read_bytes())
    authority["contractRegistry"]["currentContractCount"] = manifest["contractCount"]
    authority["contractRegistry"]["currentManifestSha256"] = _sha256(
        "schemas/contracts/manifest.json"
    )
    authority["contractRegistry"]["currentCorpusSha256"] = _sha256(
        "schemas/contracts/contract-corpus-v1.json"
    )
    authority["contractRegistry"]["currentProfileSha256"] = _sha256(
        "schemas/contracts/canonical-json-profile-v1.json"
    )
    authority["nonClaims"] = list(_NONCLAIMS)
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
