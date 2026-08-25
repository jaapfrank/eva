#!/usr/bin/env python3
"""Create a sealed, dirty-worktree-free input receipt for a B image candidate.

This is deliberately a preparation step, not a Docker build or B deployment.
The later builder must consume the exact returned archive bytes with networking
disabled.  No Docker, Compose, environment file, container, or secret is
opened here.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
GIT_BINARY = "/usr/bin/git"
CONTEXT_TREE = "eva2"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
BASE_IMAGE_PATTERN = re.compile(
    r"FROM\s+docker\.io/library/python@(?P<digest>sha256:[0-9a-f]{64})"
)
EXPECTED_COPY_SOURCES = frozenset(
    {
        "requirements.lock",
        "src",
        "connector_sdk",
        "alembic.ini",
        "migrations",
        "schemas",
    }
)
EXTERNAL_COPY_SOURCES = frozenset({"wheelhouse"})
EXPECTED_OFFLINE_PIP_INSTALL = (
    "RUN python -m pip install --no-cache-dir --no-index --only-binary=:all: "
    "--find-links=./wheelhouse --require-hashes -r requirements.lock"
)


@dataclass(frozen=True, slots=True)
class CandidateBuildInputs:
    """One immutable source context, ready for a later offline builder."""

    archive_bytes: bytes
    base_image_digest: str
    dockerfile_sha256: str
    requirements_lock_sha256: str
    source_archive_sha256: str
    source_commit: str
    source_date_epoch: int

    def receipt(self) -> dict[str, object]:
        return {
            "contract": "EvaManagedBCandidateBuildInputsReceipt/v1",
            "base_image_digest": self.base_image_digest,
            "dockerfile_path": "deploy/living-core/Dockerfile",
            "dockerfile_sha256": self.dockerfile_sha256,
            "external_inputs_required": ["wheelhouse"],
            "image_build": "not_started",
            "network": "not_used",
            "nonclaims": [
                "No Docker image was built, inspected, tagged, pushed, signed, or released.",
                "No Compose graph, B configuration, container, volume, or live service was opened.",
                "The required wheelhouse must be separately sealed before a later offline build.",
                "This receipt does not prove hermetic or reproducible image construction.",
            ],
            "requirements_lock_sha256": self.requirements_lock_sha256,
            "source_archive_sha256": self.source_archive_sha256,
            "source_commit": self.source_commit,
            "source_date_epoch": self.source_date_epoch,
            "status": "passed",
        }


def validate_candidate_build_inputs(
    inputs: CandidateBuildInputs,
) -> dict[str, tuple[bytes, int]]:
    """Revalidate the immutable source archive before a later builder uses it."""

    if _sha256(inputs.archive_bytes) != inputs.source_archive_sha256:
        raise RuntimeError("candidate source archive digest differs")
    if inputs.source_date_epoch <= 0:
        raise RuntimeError("candidate source archive epoch is invalid")
    files = _archive_files(inputs.archive_bytes, require_context_root=False)
    if _canonical_archive(files, source_date_epoch=inputs.source_date_epoch) != inputs.archive_bytes:
        raise RuntimeError("candidate source archive is not canonical")
    dockerfile_entry = files.get("deploy/living-core/Dockerfile")
    requirements_lock_entry = files.get("requirements.lock")
    if dockerfile_entry is None or requirements_lock_entry is None:
        raise RuntimeError("candidate archive omits its Dockerfile or requirements lock")
    dockerfile, requirements_lock = dockerfile_entry[0], requirements_lock_entry[0]
    if _sha256(dockerfile) != inputs.dockerfile_sha256:
        raise RuntimeError("candidate Dockerfile digest differs")
    if _sha256(requirements_lock) != inputs.requirements_lock_sha256:
        raise RuntimeError("candidate requirements lock digest differs")
    if _base_image_digest(dockerfile) != inputs.base_image_digest:
        raise RuntimeError("candidate base image digest differs")
    _require_copy_inputs(files, _copy_sources(dockerfile))
    _require_offline_pip_install(dockerfile)
    return files


Runner = Callable[[Sequence[str]], bytes]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _run(command: Sequence[str]) -> bytes:
    try:
        result = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            cwd=ROOT.parent,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("candidate source preparation failed") from error
    return result.stdout


def _member_name(member: tarfile.TarInfo) -> str:
    path = PurePosixPath(member.name)
    if (
        not member.name
        or "\x00" in member.name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError("candidate archive contains an unsafe path")
    return path.as_posix()


def _archive_files(
    archive: bytes, *, require_context_root: bool = True
) -> dict[str, tuple[bytes, int]]:
    if not archive or len(archive) > MAX_ARCHIVE_BYTES:
        raise RuntimeError("candidate archive is empty or exceeds its size limit")
    files: dict[str, tuple[bytes, int]] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            for member in bundle:
                name = _member_name(member)
                if require_context_root:
                    if name == CONTEXT_TREE:
                        if not member.isdir():
                            raise RuntimeError("candidate archive has an invalid context root")
                        continue
                    prefix = f"{CONTEXT_TREE}/"
                    if not name.startswith(prefix):
                        raise RuntimeError("candidate archive escapes its sealed context root")
                    name = name.removeprefix(prefix)
                if member.isdir():
                    if not require_context_root:
                        raise RuntimeError("canonical candidate archive has an unexpected directory")
                    continue
                if not member.isfile() or member.issym() or member.islnk():
                    raise RuntimeError("candidate archive contains a non-regular input")
                if name in files:
                    raise RuntimeError("candidate archive repeats an input path")
                handle = bundle.extractfile(member)
                if handle is None:
                    raise RuntimeError("candidate archive input is unreadable")
                content = handle.read(MAX_ARCHIVE_BYTES + 1)
                if len(content) > MAX_ARCHIVE_BYTES:
                    raise RuntimeError("candidate archive input exceeds its size limit")
                canonical_mode = 0o755 if member.mode & 0o111 else 0o644
                files[name] = (content, canonical_mode)
    except (tarfile.TarError, OSError) as error:
        raise RuntimeError("candidate archive is invalid") from error
    return files


def _canonical_archive(
    files: dict[str, tuple[bytes, int]],
    *,
    source_date_epoch: int,
) -> bytes:
    output = io.BytesIO()
    try:
        with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as bundle:
            for name, (content, mode) in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.gid = 0
                member.gname = ""
                member.mtime = source_date_epoch
                member.mode = mode
                member.size = len(content)
                member.uid = 0
                member.uname = ""
                bundle.addfile(member, io.BytesIO(content))
    except (tarfile.TarError, ValueError) as error:
        raise RuntimeError("candidate archive could not be normalized") from error
    return output.getvalue()


def _copy_sources(dockerfile: bytes) -> frozenset[str]:
    try:
        lines = dockerfile.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise RuntimeError("candidate Dockerfile must be UTF-8") from error
    sources: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith("COPY "):
            continue
        parts = stripped.split()
        if len(parts) != 3 or parts[1].startswith("--"):
            raise RuntimeError("candidate Dockerfile has an unsupported COPY instruction")
        source, destination = parts[1:]
        if not destination.startswith("./"):
            raise RuntimeError("candidate Dockerfile COPY destination is unsupported")
        if source.startswith("/") or ".." in PurePosixPath(source).parts:
            raise RuntimeError("candidate Dockerfile COPY source is unsafe")
        sources.add(source)
    if sources != EXPECTED_COPY_SOURCES | EXTERNAL_COPY_SOURCES:
        raise RuntimeError("candidate Dockerfile COPY inputs differ from the sealed set")
    return frozenset(sources)


def _require_offline_pip_install(dockerfile: bytes) -> None:
    try:
        lines = dockerfile.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise RuntimeError("candidate Dockerfile must be UTF-8") from error
    commands = [line.strip() for line in lines if line.strip().startswith("RUN ")]
    if commands != [EXPECTED_OFFLINE_PIP_INSTALL]:
        raise RuntimeError("candidate Dockerfile must have the exact offline pip install")


def _base_image_digest(dockerfile: bytes) -> str:
    try:
        lines = dockerfile.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise RuntimeError("candidate Dockerfile must be UTF-8") from error
    matches = [BASE_IMAGE_PATTERN.fullmatch(line.strip()) for line in lines]
    digests = [match.group("digest") for match in matches if match is not None]
    if len(digests) != 1:
        raise RuntimeError("candidate Dockerfile must pin exactly one Python base digest")
    return digests[0]


def _require_copy_inputs(
    files: dict[str, tuple[bytes, int]], sources: frozenset[str]
) -> None:
    for source in sources - EXTERNAL_COPY_SOURCES:
        if source in files:
            continue
        prefix = f"{source}/"
        if not any(path.startswith(prefix) for path in files):
            raise RuntimeError(f"candidate archive omits Dockerfile COPY input: {source}")
    for source in EXTERNAL_COPY_SOURCES:
        if source in files or any(path.startswith(f"{source}/") for path in files):
            raise RuntimeError(f"candidate archive must not contain external input: {source}")


def prepare_candidate_build_inputs(
    commit: str,
    *,
    runner: Runner = _run,
) -> CandidateBuildInputs:
    """Archive exactly one immutable eva2 commit, never the worktree."""

    if not COMMIT_PATTERN.fullmatch(commit):
        raise RuntimeError("candidate source must be an explicit lowercase 40-character commit")
    resolved = runner((GIT_BINARY, "rev-parse", "--verify", f"{commit}^{{commit}}"))
    resolved_commit = resolved.decode("ascii", "strict").strip()
    if resolved_commit != commit:
        raise RuntimeError("candidate source commit did not resolve exactly")
    timestamp = runner((GIT_BINARY, "show", "-s", "--format=%ct", commit))
    try:
        source_date_epoch = int(timestamp.decode("ascii", "strict").strip())
    except ValueError as error:
        raise RuntimeError("candidate source commit has an invalid timestamp") from error
    if source_date_epoch <= 0:
        raise RuntimeError("candidate source commit timestamp must be positive")
    raw_archive = runner(
        (GIT_BINARY, "archive", "--format=tar", commit, "--", CONTEXT_TREE)
    )
    files = _archive_files(raw_archive)
    dockerfile_entry = files.get("deploy/living-core/Dockerfile")
    requirements_lock_entry = files.get("requirements.lock")
    dockerfile = dockerfile_entry[0] if dockerfile_entry is not None else None
    requirements_lock = (
        requirements_lock_entry[0] if requirements_lock_entry is not None else None
    )
    if dockerfile is None or requirements_lock is None:
        raise RuntimeError("candidate archive omits its Dockerfile or requirements lock")
    _require_copy_inputs(files, _copy_sources(dockerfile))
    _require_offline_pip_install(dockerfile)
    archive = _canonical_archive(files, source_date_epoch=source_date_epoch)
    prepared = CandidateBuildInputs(
        archive_bytes=archive,
        base_image_digest=_base_image_digest(dockerfile),
        dockerfile_sha256=_sha256(dockerfile),
        requirements_lock_sha256=_sha256(requirements_lock),
        source_archive_sha256=_sha256(archive),
        source_commit=commit,
        source_date_epoch=source_date_epoch,
    )
    validate_candidate_build_inputs(prepared)
    return prepared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a sealed Git-archive context for a later offline managed-B image build."
    )
    parser.add_argument("commit")
    arguments = parser.parse_args(argv)
    try:
        receipt = prepare_candidate_build_inputs(arguments.commit).receipt()
    except RuntimeError as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
