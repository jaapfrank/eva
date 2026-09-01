#!/usr/bin/env python3
"""Build the exact filesystem MCP proof image from a private offline artifactset."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
AUTHORITY_PATH = HERE / "artifact-authority.json"
SOURCE_FILES = (
    "Dockerfile",
    "package-lock.json",
    "package.json",
    "start-filesystem-bridge.sh",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _run(*command: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _authority() -> dict[str, object]:
    value = json.loads(AUTHORITY_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("contract") != (
        "EvaFilesystemMcpBuildArtifactAuthority/v1"
    ):
        raise RuntimeError("filesystem MCP artifact authority is invalid")
    return value


def _validate_artifacts(root: Path, authority: dict[str, object]) -> str:
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise RuntimeError("artifact root must be an explicit regular directory")
    manifest = root / "manifest.sha256"
    manifest_authority = authority.get("privateArtifactManifest")
    if not isinstance(manifest_authority, dict):
        raise RuntimeError("private artifact manifest authority is invalid")
    expected_manifest_sha256 = manifest_authority.get("sha256")
    expected_count = manifest_authority.get("fileCount")
    if (
        not isinstance(expected_manifest_sha256, str)
        or _sha256(manifest) != expected_manifest_sha256
    ):
        raise RuntimeError("private artifact manifest hash differs")
    lines = manifest.read_text(encoding="utf-8").splitlines()
    if not isinstance(expected_count, int) or len(lines) != expected_count:
        raise RuntimeError("private artifact manifest count differs")
    expected_paths: set[str] = set()
    for line in lines:
        digest, separator, relative = line.partition("  ")
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or separator != "  "
            or relative.startswith("/")
            or ".." in Path(relative).parts
            or relative in expected_paths
        ):
            raise RuntimeError("private artifact manifest row is invalid")
        target = root / relative
        if target.is_symlink() or not target.is_file() or _sha256(target) != digest:
            raise RuntimeError("private artifact bytes differ")
        if stat.S_IMODE(target.stat().st_mode) != 0o400:
            raise RuntimeError("private artifact mode differs")
        expected_paths.add(relative)
    observed_paths = {
        path.relative_to(root).as_posix()
        for parent in (root / "npm-cache", root / "rpm")
        for path in parent.rglob("*")
        if path.is_file()
    }
    if observed_paths != expected_paths:
        raise RuntimeError("private artifact file set differs")
    expected_rpms = authority.get("rpmPackages")
    if not isinstance(expected_rpms, list) or sorted(expected_rpms) != sorted(
        path.name for path in (root / "rpm").iterdir()
    ):
        raise RuntimeError("private RPM set differs")
    return expected_manifest_sha256


def _source_commit() -> str:
    commit = _run("git", "rev-parse", "HEAD", cwd=HERE)
    if len(commit) != 40:
        raise RuntimeError("source commit is invalid")
    changed = _run(
        "git",
        "status",
        "--porcelain=v1",
        "--",
        *(str(HERE / name) for name in (*SOURCE_FILES, "artifact-authority.json")),
        cwd=HERE,
    )
    if changed:
        raise RuntimeError("filesystem MCP image inputs are not committed")
    return commit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--tag", default="eva2/filesystem-mcp-mutation:candidate")
    arguments = parser.parse_args()
    authority = _authority()
    manifest_sha256 = _validate_artifacts(arguments.artifact_root, authority)
    source_commit = _source_commit()
    with tempfile.TemporaryDirectory(prefix="eva2-filesystem-mcp-build-") as raw:
        context = Path(raw)
        for name in SOURCE_FILES:
            shutil.copyfile(HERE / name, context / name)
        shutil.copytree(arguments.artifact_root / "rpm", context / "rpm")
        shutil.copytree(
            arguments.artifact_root / "npm-cache", context / "npm-cache"
        )
        _run(
            "docker",
            "build",
            "--network",
            "none",
            "--no-cache",
            "--pull=false",
            "--build-arg",
            f"EVA_SOURCE_COMMIT={source_commit}",
            "--build-arg",
            f"EVA_ARTIFACT_MANIFEST_SHA256={manifest_sha256}",
            "--tag",
            arguments.tag,
            ".",
            cwd=context,
        )
    image_id = _run("docker", "image", "inspect", arguments.tag, "--format", "{{.Id}}")
    print(
        json.dumps(
            {
                "artifactManifestSha256": manifest_sha256,
                "contract": "EvaFilesystemMcpOfflineImageBuild/v1",
                "imageId": image_id,
                "networkUsed": False,
                "sourceCommit": source_commit,
                "tag": arguments.tag,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
