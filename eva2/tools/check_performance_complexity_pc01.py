#!/usr/bin/env python3
"""Fail-closed PC01 source, dependency and OCI inventory gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Any


CONTRACT = "EvaPerformanceComplexityPc01Authority/v1"
RECEIPT_CONTRACT = "EvaPerformanceComplexityPc01Receipt/v1"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DEPENDENCY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


class Pc01Error(RuntimeError):
    """The PC01 authority or local measurement cannot be trusted."""


def _run(arguments: list[str], *, cwd: Path) -> bytes:
    result = subprocess.run(arguments, cwd=cwd, capture_output=True, check=False)
    if result.returncode != 0:
        raise Pc01Error(f"command failed: {arguments[0]}")
    return result.stdout


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_authority(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Pc01Error("PC01 authority is invalid JSON") from exc
    if not isinstance(value, dict) or value.get("contract") != CONTRACT:
        raise Pc01Error("PC01 authority contract differs")
    if set(value) != {
        "contract",
        "dependencies",
        "preVoiceBrowserCoreImageBaseline",
        "source",
    }:
        raise Pc01Error("PC01 authority fields differ")
    return value, _sha256(raw)


def _string_list(value: object, *, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or value != sorted(set(value))
    ):
        raise Pc01Error(f"{label} must be a unique sorted string list")
    return value


def _git_paths(repo: Path, project: Path) -> list[str]:
    prefix = project.relative_to(repo).as_posix()
    raw = _run(
        ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD", "--", prefix], cwd=repo
    )
    result: list[str] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        path = item.decode("utf-8")
        if not path.startswith(prefix + "/"):
            raise Pc01Error("tracked PC01 path escaped the project")
        result.append(path[len(prefix) + 1 :])
    return sorted(result)


def _git_blob(repo: Path, project: Path, relative: str) -> bytes:
    prefix = project.relative_to(repo).as_posix()
    return _run(["git", "show", f"HEAD:{prefix}/{relative}"], cwd=repo)


def _manifest(rows: list[tuple[str, bytes]]) -> dict[str, object]:
    entries: list[bytes] = []
    line_count = 0
    byte_count = 0
    for path, raw in sorted(rows):
        lines = len(raw.splitlines())
        line_count += lines
        byte_count += len(raw)
        entries.append(f"{path}\t{len(raw)}\t{lines}\t{_sha256(raw)}\n".encode("utf-8"))
    return {
        "bytes": byte_count,
        "fileCount": len(rows),
        "manifestSha256": _sha256(b"".join(entries)),
        "physicalLines": line_count,
    }


def _measure_sources(
    repo: Path, project: Path, source: dict[str, Any]
) -> tuple[dict[str, object], list[str]]:
    required = {
        "classifiedNonProductRoots",
        "extensions",
        "generatedExactPaths",
        "livingCoreRoot",
        "productRoots",
    }
    if set(source) != required:
        raise Pc01Error("PC01 source authority fields differ")
    extensions = _string_list(source["extensions"], label="extensions")
    product_roots = _string_list(source["productRoots"], label="product roots")
    non_product_roots = _string_list(
        source["classifiedNonProductRoots"], label="non-product roots"
    )
    generated = _string_list(
        source["generatedExactPaths"], label="generated exact paths"
    )
    if set(product_roots) & set(non_product_roots):
        raise Pc01Error("PC01 source roots overlap")
    if source["livingCoreRoot"] not in product_roots:
        raise Pc01Error("PC01 living core root is not a product root")
    tracked = _git_paths(repo, project)
    source_paths = [path for path in tracked if Path(path).suffix in extensions]
    known_roots = product_roots + non_product_roots
    unclassified = [
        path
        for path in source_paths
        if not any(path == root or path.startswith(root + "/") for root in known_roots)
    ]
    missing_generated = [path for path in generated if path not in source_paths]
    if missing_generated:
        raise Pc01Error("a sealed generated source path is missing")
    product_rows = [
        (path, _git_blob(repo, project, path))
        for path in source_paths
        if path not in generated
        and any(path.startswith(root + "/") for root in product_roots)
    ]
    living_rows = [
        row
        for row in product_rows
        if row[0].startswith(str(source["livingCoreRoot"]) + "/")
    ]
    generated_rows = [(path, _git_blob(repo, project, path)) for path in generated]
    classified: dict[str, object] = {}
    for root in non_product_roots:
        rows = [
            (path, _git_blob(repo, project, path))
            for path in source_paths
            if path.startswith(root + "/")
        ]
        classified[root] = _manifest(rows)
    measurement = {
        "classifiedNonProduct": classified,
        "generated": _manifest(generated_rows),
        "livingCore": _manifest(living_rows),
        "product": _manifest(product_rows),
        "unclassifiedSourcePaths": unclassified,
    }
    violations: list[str] = []
    if unclassified:
        violations.append("unclassified_source_root")
    return measurement, violations


def _dependency_name(requirement: str) -> str:
    match = _DEPENDENCY_NAME.match(requirement)
    if match is None:
        raise Pc01Error("a direct runtime dependency is invalid")
    return match.group(0).lower().replace("_", "-")


def _measure_dependencies(
    repo: Path, project: Path, authority: dict[str, Any]
) -> tuple[dict[str, object], list[str]]:
    if set(authority) != {"expected", "source"}:
        raise Pc01Error("PC01 dependency authority fields differ")
    expected = _string_list(authority["expected"], label="expected dependencies")
    source = authority["source"]
    if source != "pyproject.toml":
        raise Pc01Error("PC01 dependency source differs")
    raw = _git_blob(repo, project, source)
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
        requirements = parsed["project"]["dependencies"]
    except (UnicodeDecodeError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise Pc01Error("PC01 dependency source is invalid") from exc
    if not isinstance(requirements, list) or any(
        not isinstance(item, str) for item in requirements
    ):
        raise Pc01Error("PC01 direct dependencies differ")
    names = sorted(_dependency_name(item) for item in requirements)
    violations: list[str] = []
    if names != expected:
        violations.append("direct_runtime_dependency_set_differs")
    return {
        "count": len(names),
        "names": names,
        "sourceSha256": _sha256(raw),
    }, violations


def _measure_image(
    project: Path, authority: dict[str, Any]
) -> tuple[dict[str, object], list[str]]:
    required = {
        "activeContainer",
        "compressedLayerBytes",
        "configDigest",
        "layerCount",
        "manifestDigest",
        "platform",
        "sourceCommit",
        "sourceEvidence",
        "sourceEvidenceSha256",
    }
    if set(authority) != required:
        raise Pc01Error("PC01 image authority fields differ")
    for key in ("manifestDigest", "configDigest"):
        if not isinstance(authority[key], str) or not _DIGEST.fullmatch(authority[key]):
            raise Pc01Error("PC01 image digest is invalid")
    evidence_path = project / authority["sourceEvidence"]
    evidence_raw = evidence_path.read_bytes()
    if _sha256(evidence_raw) != authority["sourceEvidenceSha256"]:
        raise Pc01Error("PC01 source evidence differs")
    evidence = json.loads(evidence_raw)
    if (
        evidence.get("build", {}).get("sourceCommit") != authority["sourceCommit"]
        or evidence.get("image", {}).get("id") != authority["manifestDigest"]
    ):
        raise Pc01Error("PC01 image provenance differs")
    container_raw = subprocess.run(
        ["docker", "inspect", authority["activeContainer"]],
        capture_output=True,
        check=False,
    )
    if container_raw.returncode != 0:
        raise Pc01Error("PC01 active core container is unavailable")
    container = json.loads(container_raw.stdout)[0]

    def content(digest: str) -> dict[str, Any]:
        result = subprocess.run(
            ["ctr", "-n", "moby", "content", "get", digest],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise Pc01Error("PC01 OCI manifest is unavailable")
        return json.loads(result.stdout)

    manifest = content(authority["manifestDigest"])
    if manifest.get("mediaType") == "application/vnd.oci.image.index.v1+json":
        operating_system, architecture = authority["platform"].split("/", 1)
        candidates = [
            descriptor
            for descriptor in manifest.get("manifests", [])
            if descriptor.get("platform", {}).get("os") == operating_system
            and descriptor.get("platform", {}).get("architecture") == architecture
        ]
        if len(candidates) != 1 or not _DIGEST.fullmatch(
            candidates[0].get("digest", "")
        ):
            raise Pc01Error("PC01 OCI platform manifest differs")
        manifest = content(candidates[0]["digest"])
    compressed = sum(layer["size"] for layer in manifest["layers"])
    observed = {
        "activeContainer": authority["activeContainer"],
        "activeContainerRunning": container.get("State", {}).get("Running") is True,
        "activeImageDigest": container.get("Image"),
        "compressedLayerBytes": compressed,
        "configDigest": manifest.get("config", {}).get("digest"),
        "layerCount": len(manifest.get("layers", [])),
        "manifestDigest": authority["manifestDigest"],
        "platform": authority["platform"],
        "sourceCommit": authority["sourceCommit"],
    }
    violations: list[str] = []
    if not observed["activeContainerRunning"]:
        violations.append("active_core_container_not_running")
    if observed["activeImageDigest"] != authority["manifestDigest"]:
        violations.append("active_core_image_differs")
    if observed["configDigest"] != authority["configDigest"]:
        violations.append("core_image_config_digest_differs")
    if observed["compressedLayerBytes"] != authority["compressedLayerBytes"]:
        violations.append("core_image_compressed_bytes_differ")
    if observed["layerCount"] != authority["layerCount"]:
        violations.append("core_image_layer_count_differs")
    return observed, violations


def check(repo: Path, project: Path, authority_path: Path) -> dict[str, object]:
    authority, authority_sha256 = _load_authority(authority_path)
    source, source_violations = _measure_sources(repo, project, authority["source"])
    dependencies, dependency_violations = _measure_dependencies(
        repo, project, authority["dependencies"]
    )
    image, image_violations = _measure_image(
        project, authority["preVoiceBrowserCoreImageBaseline"]
    )
    commit = _run(["git", "rev-parse", "HEAD"], cwd=repo).decode("ascii").strip()
    violations = source_violations + dependency_violations + image_violations
    return {
        "authoritySha256": authority_sha256,
        "contract": RECEIPT_CONTRACT,
        "dependencies": dependencies,
        "gitCommit": commit,
        "imageBaseline": image,
        "source": source,
        "status": "passed" if not violations else "failed",
        "violations": violations,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        receipt = check(
            args.repo.resolve(), args.project.resolve(), args.authority.resolve()
        )
    except (OSError, Pc01Error, json.JSONDecodeError) as exc:
        print(f"performance-complexity-pc01: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    return 0 if receipt["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
