#!/usr/bin/env python3
"""Prove which worktree app files are present in the frozen Living Core B.

The Docker boundary is deliberately narrow: this tool invokes exactly
``/usr/bin/docker inspect`` for selected non-secret fields and ``docker exec``
to calculate file sizes and SHA-256 digests inside the container.  It never
requests environment variables, mounts, file contents, or hashes outside the
six application roots copied by the Living Core Dockerfile.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any


TOOL_VERSION = "1.0.0"
DOCKER_BINARY = "/usr/bin/docker"
EXPECTED_CONTAINER_NAME = "eva2-living-core-b"
EXPECTED_IMAGE_REF = "eva2-living-core:cross-channel-v53-20260824"
MAX_INVENTORY_BYTES = 16 * 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

# container root -> path relative to the eva2 worktree root
ROOT_MAPPINGS: tuple[tuple[str, str], ...] = (
    ("/opt/eva/src", "src"),
    ("/opt/eva/connector_sdk", "connector_sdk"),
    ("/opt/eva/migrations", "migrations"),
    ("/opt/eva/schemas", "schemas"),
    ("/opt/eva/requirements.lock", "requirements.lock"),
    ("/opt/eva/alembic.ini", "alembic.ini"),
)
SINGLE_FILE_ROOTS = frozenset({"requirements.lock", "alembic.ini"})

NONCLAIMS: tuple[str, ...] = (
    "This receipt does not prove that the image can be rebuilt reproducibly.",
    "This receipt does not prove runtime behavior, test coverage, or release acceptance.",
    "This receipt does not inspect or attest environment variables, secrets, or volume metadata.",
    "This receipt does not attest installed Python packages or base-image operating-system files.",
    "Worktree-only files are differences, not evidence that they were intended for this image.",
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str]], CommandResult]


def _default_runner(command: Sequence[str]) -> CommandResult:
    completed = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=120,
        env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def verify_docker_binary(path: str = DOCKER_BINARY) -> None:
    if path != DOCKER_BINARY:
        raise RuntimeError(f"Docker binary must be exactly {DOCKER_BINARY}")
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise RuntimeError("Docker binary is inaccessible") from error
    mode = metadata.st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise RuntimeError("Docker binary must be a regular non-symlink file")
    if metadata.st_uid != 0:
        raise RuntimeError("Docker binary must be root-owned")
    if not mode & stat.S_IXUSR:
        raise RuntimeError("Docker binary must be owner-executable")
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError("Docker binary must not be group/other-writable")


def _run_checked(runner: Runner, command: Sequence[str], *, purpose: str) -> str:
    result = runner(tuple(command))
    if result.returncode != 0:
        detail = result.stderr.strip()[:300]
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"{purpose} failed with exit {result.returncode}{suffix}")
    if len(result.stdout.encode("utf-8")) > MAX_INVENTORY_BYTES:
        raise RuntimeError(f"{purpose} output exceeds the safe size limit")
    return result.stdout


def _inspect_command(container_name: str) -> tuple[str, ...]:
    selected = (
        "[{{json .Id}},{{json .Image}},{{json .Config.Image}},"
        "{{json .Name}},{{json .State.Status}},{{json .RestartCount}}]"
    )
    return (
        DOCKER_BINARY,
        "inspect",
        "--type",
        "container",
        "--format",
        selected,
        container_name,
    )


def parse_selected_metadata(
    raw: str,
    *,
    expected_container_name: str = EXPECTED_CONTAINER_NAME,
    expected_image_ref: str = EXPECTED_IMAGE_REF,
) -> dict[str, Any]:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("Docker returned malformed selected metadata") from error
    if not isinstance(decoded, list) or len(decoded) != 6:
        raise RuntimeError("Docker selected metadata must contain exactly six fields")
    container_id, image_id, image_ref, name, status, restart_count = decoded
    if not isinstance(container_id, str) or not re.fullmatch(
        r"[0-9a-f]{64}", container_id
    ):
        raise RuntimeError("Docker returned an invalid container ID")
    if not isinstance(image_id, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", image_id
    ):
        raise RuntimeError("Docker returned an invalid image ID")
    if image_ref != expected_image_ref:
        raise RuntimeError("Living Core B image reference does not match frozen v53")
    if name != f"/{expected_container_name}":
        raise RuntimeError("Docker returned metadata for a different container")
    if status != "running":
        raise RuntimeError("Living Core B must be running while provenance is captured")
    if isinstance(restart_count, bool) or not isinstance(restart_count, int):
        raise RuntimeError("Docker returned an invalid restart count")
    if restart_count < 0:
        raise RuntimeError("Docker returned a negative restart count")
    return {
        "container_id": container_id,
        "image_id": image_id,
        "image_ref": image_ref,
        "name": expected_container_name,
        "status": status,
        "restart_count": restart_count,
    }


def _container_inventory_program() -> str:
    mappings = json.dumps(ROOT_MAPPINGS, separators=(",", ":"))
    return f'''import hashlib,json,os,stat,sys
roots={mappings}
records=[]
seen=set()
def digest_file(path):
    value=hashlib.sha256()
    with open(path,"rb",buffering=0) as handle:
        while True:
            chunk=handle.read(1024*1024)
            if not chunk: break
            value.update(chunk)
    return value.hexdigest()
def add_file(path,key):
    before=os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError("nonregular application path: "+key)
    digest=digest_file(path)
    after=os.lstat(path)
    if (before.st_dev,before.st_ino,before.st_mode,before.st_size,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_mode,after.st_size,after.st_ctime_ns):
        raise RuntimeError("application file changed while hashing: "+key)
    if key in seen: raise RuntimeError("duplicate application path: "+key)
    seen.add(key)
    records.append({{"path":key,"size":before.st_size,"sha256":digest}})
def walk(path,key):
    metadata=os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("symlink application path: "+key)
    if stat.S_ISREG(metadata.st_mode):
        add_file(path,key); return
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError("nonregular application path: "+key)
    with os.scandir(path) as entries:
        ordered=sorted(entries,key=lambda entry:entry.name)
    for entry in ordered:
        if entry.name in (".","..") or "/" in entry.name or "\\x00" in entry.name:
            raise RuntimeError("invalid application path component")
        child_key=key+"/"+entry.name if key else entry.name
        walk(entry.path,child_key)
for container_root,worktree_root in roots:
    walk(container_root,worktree_root)
records.sort(key=lambda record:record["path"])
sys.stdout.write(json.dumps(records,sort_keys=True,separators=(",",":")))
'''


def _inventory_command(container_name: str) -> tuple[str, ...]:
    return (
        DOCKER_BINARY,
        "exec",
        container_name,
        "python",
        "-I",
        "-c",
        _container_inventory_program(),
    )


def _validated_relative_path(value: object, allowed_roots: set[str]) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeError("Inventory record has an invalid path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError("Inventory record path traversal is forbidden")
    root = path.parts[0]
    if root not in allowed_roots:
        raise RuntimeError("Inventory record is outside the approved roots")
    if root in SINGLE_FILE_ROOTS and len(path.parts) != 1:
        raise RuntimeError("Inventory record descends below an approved single-file root")
    return path.as_posix()


def validate_inventory(
    raw: str | bytes | Sequence[Mapping[str, object]],
    *,
    mappings: Sequence[tuple[str, str]] = ROOT_MAPPINGS,
) -> tuple[dict[str, object], ...]:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RuntimeError("Container inventory must be UTF-8") from error
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_INVENTORY_BYTES:
            raise RuntimeError("Container inventory exceeds the safe size limit")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError("Container returned malformed inventory JSON") from error
    else:
        decoded = raw
    if not isinstance(decoded, (list, tuple)):
        raise RuntimeError("Container inventory must be a list")
    allowed_roots = {worktree for _, worktree in mappings}
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for value in decoded:
        if not isinstance(value, Mapping) or set(value) != {"path", "size", "sha256"}:
            raise RuntimeError("Container inventory record has an invalid schema")
        path = _validated_relative_path(value["path"], allowed_roots)
        size = value["size"]
        digest = value["sha256"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise RuntimeError("Container inventory record has an invalid size")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise RuntimeError("Container inventory record has an invalid SHA-256")
        if path in seen:
            raise RuntimeError("Container inventory contains a duplicate path")
        seen.add(path)
        records.append({"path": path, "size": size, "sha256": digest})
    if not records:
        raise RuntimeError("Container inventory is unexpectedly empty")
    if [record["path"] for record in records] != sorted(seen):
        raise RuntimeError("Container inventory is not in deterministic path order")
    represented_roots = {PurePosixPath(str(record["path"])).parts[0] for record in records}
    if represented_roots != allowed_roots:
        raise RuntimeError("Container inventory does not represent every approved root")
    return tuple(records)


def _hash_regular_file(path: Path, relative_path: str) -> tuple[int, str]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError(f"Worktree application path is not a regular file: {relative_path}")
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size) != (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
        ):
            raise RuntimeError(f"Worktree application file changed before read: {relative_path}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_ctime_ns,
    ):
        raise RuntimeError(f"Worktree application file changed while hashing: {relative_path}")
    return before.st_size, digest.hexdigest()


def inventory_worktree(
    eva2_root: Path,
    *,
    mappings: Sequence[tuple[str, str]] = ROOT_MAPPINGS,
) -> tuple[dict[str, object], ...]:
    root_metadata = eva2_root.lstat()
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise RuntimeError("Eva2 worktree root must be a regular non-symlink directory")
    records: list[dict[str, object]] = []
    seen: set[str] = set()

    def walk(path: Path, key: str) -> None:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError(f"Worktree application path is a symlink: {key}")
        if stat.S_ISREG(metadata.st_mode):
            if key in seen:
                raise RuntimeError(f"Worktree inventory contains a duplicate path: {key}")
            seen.add(key)
            size, digest = _hash_regular_file(path, key)
            records.append({"path": key, "size": size, "sha256": digest})
            return
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError(f"Worktree application path is nonregular: {key}")
        with os.scandir(path) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            if entry.name in {".", ".."} or "/" in entry.name or "\x00" in entry.name:
                raise RuntimeError("Worktree contains an invalid path component")
            child_key = f"{key}/{entry.name}" if key else entry.name
            walk(Path(entry.path), child_key)

    for _, relative_root in mappings:
        mapped = eva2_root / relative_root
        try:
            mapped.relative_to(eva2_root)
        except ValueError as error:
            raise RuntimeError("Worktree root mapping escapes eva2 root") from error
        walk(mapped, relative_root)
    records.sort(key=lambda record: str(record["path"]))
    return tuple(records)


def compare_inventories(
    container_records: Sequence[Mapping[str, object]],
    worktree_records: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    def index(records: Sequence[Mapping[str, object]], side: str) -> dict[str, Mapping[str, object]]:
        result: dict[str, Mapping[str, object]] = {}
        for record in records:
            path = record.get("path")
            if not isinstance(path, str) or path in result:
                raise RuntimeError(f"{side} inventory contains an invalid or duplicate path")
            result[path] = record
        return result

    container = index(container_records, "Container")
    worktree = index(worktree_records, "Worktree")
    comparisons: list[dict[str, object]] = []
    for path in sorted(container.keys() | worktree.keys()):
        deployed = container.get(path)
        local = worktree.get(path)
        if deployed is None:
            state = "worktree_only"
        elif local is None:
            state = "container_only"
        elif (
            deployed.get("size") == local.get("size")
            and deployed.get("sha256") == local.get("sha256")
        ):
            state = "identical"
        else:
            state = "different"
        comparisons.append(
            {
                "path": path,
                "state": state,
                "container": (
                    None
                    if deployed is None
                    else {"size": deployed["size"], "sha256": deployed["sha256"]}
                ),
                "worktree": (
                    None
                    if local is None
                    else {"size": local["size"], "sha256": local["sha256"]}
                ),
            }
        )
    return tuple(comparisons)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _receipt_digest(receipt_without_digest: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(receipt_without_digest)).hexdigest()


def validate_receipt(receipt: Mapping[str, object]) -> None:
    expected_keys = {
        "schema_version",
        "captured_at",
        "subject",
        "git_head",
        "tool",
        "roots",
        "files",
        "counts",
        "nonclaims",
        "receipt_sha256",
    }
    if set(receipt) != expected_keys or receipt.get("schema_version") != 1:
        raise RuntimeError("Receipt has an invalid top-level schema")
    captured_at = receipt.get("captured_at")
    if not isinstance(captured_at, str):
        raise RuntimeError("Receipt capture time is invalid")
    try:
        parsed_time = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("Receipt capture time is invalid") from error
    if parsed_time.tzinfo is None:
        raise RuntimeError("Receipt capture time must include a timezone")
    git_head = receipt.get("git_head")
    if not isinstance(git_head, str) or not re.fullmatch(r"[0-9a-f]{40}", git_head):
        raise RuntimeError("Receipt Git HEAD is invalid")
    tool = receipt.get("tool")
    if not isinstance(tool, Mapping) or set(tool) != {"path", "version", "sha256"}:
        raise RuntimeError("Receipt tool binding is invalid")
    if tool.get("version") != TOOL_VERSION or not isinstance(tool.get("path"), str):
        raise RuntimeError("Receipt tool version or path is invalid")
    if not isinstance(tool.get("sha256"), str) or not SHA256_PATTERN.fullmatch(
        str(tool["sha256"])
    ):
        raise RuntimeError("Receipt tool SHA-256 is invalid")
    subject = receipt.get("subject")
    if not isinstance(subject, Mapping) or set(subject) != {
        "container_id",
        "image_id",
        "image_ref",
        "name",
        "status",
        "restart_count",
    }:
        raise RuntimeError("Receipt subject binding is invalid")
    # Reuse the strict selected-metadata validator without Docker access.
    subject_array = json.dumps(
        [
            subject.get("container_id"),
            subject.get("image_id"),
            subject.get("image_ref"),
            f"/{subject.get('name')}",
            subject.get("status"),
            subject.get("restart_count"),
        ]
    )
    parse_selected_metadata(subject_array)
    roots = receipt.get("roots")
    expected_roots = [
        {"container": container, "worktree": f"{worktree}"}
        for container, worktree in ROOT_MAPPINGS
    ]
    if roots != expected_roots:
        raise RuntimeError("Receipt root mapping is invalid")
    files = receipt.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("Receipt file comparisons are missing")
    seen: set[str] = set()
    allowed_states = {"identical", "different", "container_only", "worktree_only"}
    calculated = {state: 0 for state in allowed_states}
    for comparison in files:
        if not isinstance(comparison, Mapping) or set(comparison) != {
            "path",
            "state",
            "container",
            "worktree",
        }:
            raise RuntimeError("Receipt file comparison has an invalid schema")
        path = _validated_relative_path(
            comparison["path"], {worktree for _, worktree in ROOT_MAPPINGS}
        )
        if path in seen:
            raise RuntimeError("Receipt repeats a file path")
        seen.add(path)
        state = comparison["state"]
        if state not in allowed_states:
            raise RuntimeError("Receipt file comparison has an invalid state")
        calculated[str(state)] += 1
        for side in ("container", "worktree"):
            value = comparison[side]
            if value is None:
                continue
            if not isinstance(value, Mapping) or set(value) != {"size", "sha256"}:
                raise RuntimeError("Receipt file digest binding is invalid")
            if isinstance(value["size"], bool) or not isinstance(value["size"], int) or value["size"] < 0:
                raise RuntimeError("Receipt file size is invalid")
            if not isinstance(value["sha256"], str) or not SHA256_PATTERN.fullmatch(value["sha256"]):
                raise RuntimeError("Receipt file SHA-256 is invalid")
        if state == "container_only" and comparison["worktree"] is not None:
            raise RuntimeError("Container-only comparison unexpectedly has worktree data")
        if state == "worktree_only" and comparison["container"] is not None:
            raise RuntimeError("Worktree-only comparison unexpectedly has container data")
        if state in {"identical", "different"} and (
            comparison["container"] is None or comparison["worktree"] is None
        ):
            raise RuntimeError("Two-sided comparison is missing one side")
        if state == "identical" and comparison["container"] != comparison["worktree"]:
            raise RuntimeError("Identical comparison has unequal digests")
        if state == "different" and comparison["container"] == comparison["worktree"]:
            raise RuntimeError("Different comparison has equal digests")
    if [comparison["path"] for comparison in files] != sorted(seen):
        raise RuntimeError("Receipt files are not in deterministic order")
    counts = receipt.get("counts")
    expected_counts = {**calculated, "total": len(files)}
    if counts != expected_counts:
        raise RuntimeError("Receipt counts do not match file comparisons")
    if receipt.get("nonclaims") != list(NONCLAIMS):
        raise RuntimeError("Receipt explicit nonclaims are incomplete")
    actual_digest = receipt.get("receipt_sha256")
    if not isinstance(actual_digest, str) or not SHA256_PATTERN.fullmatch(actual_digest):
        raise RuntimeError("Receipt self-digest is invalid")
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256")
    if actual_digest != _receipt_digest(unsigned):
        raise RuntimeError("Receipt self-digest does not match its canonical content")


def _git_head(repository_root: Path, runner: Runner) -> str:
    output = _run_checked(
        runner,
        ("/usr/bin/git", "-C", str(repository_root), "rev-parse", "HEAD"),
        purpose="Git HEAD lookup",
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", output):
        raise RuntimeError("Git returned an invalid HEAD")
    return output


def collect_container_snapshot(
    *,
    runner: Runner = _default_runner,
    container_name: str = EXPECTED_CONTAINER_NAME,
    expected_image_ref: str = EXPECTED_IMAGE_REF,
) -> tuple[dict[str, Any], tuple[dict[str, object], ...]]:
    if container_name != EXPECTED_CONTAINER_NAME:
        raise RuntimeError("Only the exact approved Living Core B container is allowed")
    before = parse_selected_metadata(
        _run_checked(runner, _inspect_command(container_name), purpose="Initial Docker inspect"),
        expected_container_name=container_name,
        expected_image_ref=expected_image_ref,
    )
    inventory = validate_inventory(
        _run_checked(runner, _inventory_command(container_name), purpose="Container inventory")
    )
    after = parse_selected_metadata(
        _run_checked(runner, _inspect_command(container_name), purpose="Final Docker inspect"),
        expected_container_name=container_name,
        expected_image_ref=expected_image_ref,
    )
    if before != after:
        raise RuntimeError("Container or image metadata changed during provenance capture")
    return before, inventory


def build_receipt(
    *,
    repository_root: Path,
    eva2_root: Path,
    captured_at: str,
    runner: Runner = _default_runner,
) -> dict[str, object]:
    verify_docker_binary()
    subject, container_records = collect_container_snapshot(runner=runner)
    worktree_records = inventory_worktree(eva2_root)
    files = list(compare_inventories(container_records, worktree_records))
    counts = {state: 0 for state in ("container_only", "different", "identical", "worktree_only")}
    for comparison in files:
        counts[str(comparison["state"])] += 1
    counts["total"] = len(files)
    tool_path = Path(__file__).resolve()
    tool_hash = hashlib.sha256(tool_path.read_bytes()).hexdigest()
    receipt: dict[str, object] = {
        "schema_version": 1,
        "captured_at": captured_at,
        "subject": subject,
        "git_head": _git_head(repository_root, runner),
        "tool": {
            "path": "tools/living_core_source_provenance.py",
            "version": TOOL_VERSION,
            "sha256": tool_hash,
        },
        "roots": [
            {"container": container, "worktree": f"{worktree}"}
            for container, worktree in ROOT_MAPPINGS
        ],
        "files": files,
        "counts": counts,
        "nonclaims": list(NONCLAIMS),
    }
    receipt["receipt_sha256"] = _receipt_digest(receipt)
    validate_receipt(receipt)
    return receipt


def _write_new_receipt(path: Path, receipt: Mapping[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError("Receipt output already exists; overwrite is forbidden")
    parent = path.parent
    parent_metadata = parent.lstat()
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
        raise RuntimeError("Receipt parent must be a regular non-symlink directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o644)
    try:
        payload = json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        remaining = memoryview(payload.encode("utf-8"))
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise RuntimeError("Receipt write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--eva2-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--captured-at",
        default=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    arguments = parser.parse_args(argv)
    receipt = build_receipt(
        repository_root=arguments.repository_root.resolve(),
        eva2_root=arguments.eva2_root.resolve(),
        captured_at=arguments.captured_at,
    )
    _write_new_receipt(arguments.output, receipt)
    validate_receipt(json.loads(arguments.output.read_text(encoding="utf-8")))
    print(json.dumps({"output": str(arguments.output), "counts": receipt["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"provenance capture refused: {error}", file=sys.stderr)
        raise SystemExit(2) from error
