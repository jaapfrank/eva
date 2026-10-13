#!/usr/bin/env python3
"""Build one untagged managed-B candidate from existing sealed local inputs.

This operator sequences the existing offline candidate APIs and persists only
private verification artifacts. It does not download, tag, scan, configure
Compose, or touch a live service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Mapping

from tools.managed_b_candidate.base_image import materialize_pinned_base_image_archive
from tools.managed_b_candidate.build_context import materialize_build_context
from tools.managed_b_candidate.candidate_image import build_offline_candidate_image
from tools.managed_b_candidate.private_receipt import (
    PrivateReceiptError,
    write_new_private_json,
)
from tools.managed_b_candidate.wheelhouse import (
    ACTIVE_COVERAGE_PATH,
    inspect_wheelhouse_manifest,
    materialize_wheelhouse,
)
from tools.prepare_managed_b_candidate_context import (
    RUNTIME_REQUIREMENTS_LOCK,
    prepare_candidate_build_inputs,
    validate_candidate_build_inputs,
)


WHEELHOUSE_MANIFEST_PATH = "deploy/release/managed-b-wheelhouse-amd64-v2.json"
EXPECTED_WHEEL_COUNT = 38


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _private_output_parent(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError("managed B offline output parent must be an absolute path")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError("managed B offline output parent is inaccessible") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise RuntimeError("managed B offline output parent must be private")


def _absolute_artifact_root(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError(
            "managed B wheel artifact root must be an explicit absolute path"
        )


def _archived_bytes(
    files: dict[str, tuple[bytes, int]], path: str, *, label: str
) -> bytes:
    value = files.get(path)
    if value is None:
        raise RuntimeError(f"managed B candidate archive omits {label}")
    return value[0]


def _base_archive_inputs(
    value: Mapping[str, tuple[Path, str]],
) -> dict[str, tuple[Path, str]]:
    roles = {"build", "runtime_libraries", "runtime"}
    if set(value) != roles:
        raise RuntimeError("managed B requires exactly three role-bound base archives")
    validated: dict[str, tuple[Path, str]] = {}
    for role in sorted(roles):
        item = value[role]
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], Path)
            or not isinstance(item[1], str)
        ):
            raise RuntimeError(f"managed B {role} base archive authority is invalid")
        validated[role] = item
    return validated


def build_managed_b_offline_candidate(
    source_commit: str,
    wheel_artifact_root: Path,
    output_parent: Path,
    *,
    base_image_archives: Mapping[str, tuple[Path, str]],
) -> dict[str, object]:
    """Materialize and build exactly one local, untagged candidate image."""

    _absolute_artifact_root(wheel_artifact_root)
    _private_output_parent(output_parent)
    archives = _base_archive_inputs(base_image_archives)
    candidate = prepare_candidate_build_inputs(source_commit)
    files = validate_candidate_build_inputs(candidate)
    manifest_raw = _archived_bytes(
        files, WHEELHOUSE_MANIFEST_PATH, label="the active wheelhouse manifest"
    )
    coverage_raw = _archived_bytes(
        files, ACTIVE_COVERAGE_PATH, label="the active wheel coverage"
    )
    requirements_lock_raw = _archived_bytes(
        files, RUNTIME_REQUIREMENTS_LOCK, label="the runtime requirements lock"
    )
    manifest_receipt = inspect_wheelhouse_manifest(
        manifest_raw, coverage_raw, requirements_lock_raw
    )
    if manifest_receipt.get("expected_wheel_count") != EXPECTED_WHEEL_COUNT:
        raise RuntimeError("managed B candidate must use the exact 38-wheel manifest")

    wheelhouse = materialize_wheelhouse(
        manifest_raw,
        coverage_raw,
        wheel_artifact_root,
        output_parent,
        requirements_lock_raw,
    )
    build_image = materialize_pinned_base_image_archive(
        archives["build"][0],
        archives["build"][1],
        candidate.base_image_digest,
        output_parent,
        repository=candidate.build_image_reference.rsplit("@", 1)[0],
    )
    runtime_libraries_image = materialize_pinned_base_image_archive(
        archives["runtime_libraries"][0],
        archives["runtime_libraries"][1],
        candidate.runtime_libraries_image_digest,
        output_parent,
        repository=candidate.runtime_libraries_image_reference.rsplit("@", 1)[0],
    )
    runtime_image = materialize_pinned_base_image_archive(
        archives["runtime"][0],
        archives["runtime"][1],
        candidate.runtime_image_digest,
        output_parent,
        repository=candidate.runtime_image_reference.rsplit("@", 1)[0],
    )
    context = materialize_build_context(
        candidate,
        manifest_raw,
        coverage_raw,
        wheelhouse,
        build_image,
        runtime_libraries_image,
        runtime_image,
        output_parent,
    )
    image = build_offline_candidate_image(
        context,
        build_image,
        runtime_libraries_image,
        runtime_image,
        output_parent,
    )
    if image.artifact_path is None:
        raise RuntimeError("managed B candidate artifacts were not persisted")

    return {
        "candidate": {
            "image_id": image.image_id,
            "receipt": image.receipt,
        },
        "contract": "EvaManagedBOfflineCandidateBuildReceipt/v2",
        "inputs": {
            "output_parent": str(output_parent),
            "wheel_artifact_root": str(wheel_artifact_root),
            "wheel_coverage_path": ACTIVE_COVERAGE_PATH,
            "wheel_coverage_sha256": _sha256(coverage_raw),
            "wheelhouse_manifest_path": WHEELHOUSE_MANIFEST_PATH,
            "wheelhouse_manifest_sha256": _sha256(manifest_raw),
            "base_image_archives": {
                role: {"path": str(path), "sha256": digest}
                for role, (path, digest) in sorted(archives.items())
            },
        },
        "materialized": {
            "build_image_archive_path": str(build_image.path),
            "runtime_libraries_image_archive_path": str(runtime_libraries_image.path),
            "runtime_image_archive_path": str(runtime_image.path),
            "build_context_path": str(context.path),
            "wheelhouse_path": str(wheelhouse.path),
            "candidate_artifact_path": str(image.artifact_path),
        },
        "network_used": False,
        "nonclaims": [
            "No wheel or base image was downloaded.",
            "The candidate archive and receipts were persisted privately for verification; the image was not tagged, scanned, signed, pushed, attached to Compose, or deployed.",
            "No live configuration, container, volume, database, secret, route, or service was opened or changed.",
        ],
        "source": {
            "archive_sha256": candidate.source_archive_sha256,
            "commit": candidate.source_commit,
            "date_epoch": candidate.source_date_epoch,
            "dockerfile_sha256": candidate.dockerfile_sha256,
            "requirements_lock_path": RUNTIME_REQUIREMENTS_LOCK,
            "requirements_lock_sha256": candidate.requirements_lock_sha256,
        },
        "stages": {
            "build_image": {
                "archive_sha256": build_image.receipt["archive_sha256"],
                "digest": build_image.receipt["base_image_digest"],
                "config_digest": build_image.receipt["oci_config_digest"],
                "layout_tree_sha256": image.receipt[
                    "build_image_layout_tree_sha256"
                ],
            },
            "runtime_image": {
                "archive_sha256": runtime_image.receipt["archive_sha256"],
                "digest": runtime_image.receipt["base_image_digest"],
                "config_digest": runtime_image.receipt["oci_config_digest"],
                "layout_tree_sha256": image.receipt[
                    "runtime_image_layout_tree_sha256"
                ],
            },
            "runtime_libraries_image": {
                "archive_sha256": runtime_libraries_image.receipt["archive_sha256"],
                "digest": runtime_libraries_image.receipt["base_image_digest"],
                "config_digest": runtime_libraries_image.receipt[
                    "oci_config_digest"
                ],
                "layout_tree_sha256": image.receipt[
                    "runtime_libraries_image_layout_tree_sha256"
                ],
            },
            "build_context": {
                "tree_sha256": context.receipt["context_tree_sha256"],
            },
            "wheelhouse": {
                "count": wheelhouse.receipt["expected_wheel_count"],
                "tree_sha256": wheelhouse.receipt["wheelhouse_tree_sha256"],
            },
        },
        "status": "built_local_untagged_candidate",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_commit")
    parser.add_argument("--wheel-artifact-root", required=True, type=Path)
    parser.add_argument("--output-parent", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    for role in ("build", "runtime-libraries", "runtime"):
        parser.add_argument(f"--{role}-image-archive", required=True, type=Path)
        parser.add_argument(f"--{role}-image-archive-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = build_managed_b_offline_candidate(
            arguments.source_commit,
            arguments.wheel_artifact_root,
            arguments.output_parent,
            base_image_archives={
                "build": (
                    arguments.build_image_archive,
                    arguments.build_image_archive_sha256,
                ),
                "runtime_libraries": (
                    arguments.runtime_libraries_image_archive,
                    arguments.runtime_libraries_image_archive_sha256,
                ),
                "runtime": (
                    arguments.runtime_image_archive,
                    arguments.runtime_image_archive_sha256,
                ),
            },
        )
    except RuntimeError as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    try:
        write_new_private_json(
            arguments.receipt, receipt, label="managed B build receipt"
        )
    except (PrivateReceiptError, OSError) as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
