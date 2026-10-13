"""Build one local managed-B candidate image from sealed offline inputs.

This is deliberately a candidate-only operation.  It never opens Compose or
any B runtime input, and it gives Docker neither network access nor a tag/push
destination.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Callable

from .base_image import (
    MaterializedBaseImage,
    MaterializedBaseImageLayout,
    materialize_base_image_layout,
    validate_materialized_base_image_layout,
    validate_materialized_base_image,
)
from .build_context import (
    MaterializedBuildContext,
    validate_materialized_build_context,
)
from .private_receipt import write_new_private_json


_IMAGE_PREFIX = "sha256:"
_MAX_IMAGE_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_BUILDX_EXECUTABLE_BYTES = 256 * 1024 * 1024
_RUNTIME_LIBRARY_PATH = "LD_LIBRARY_PATH=/usr/local/lib"
_BUILDER_CONTRACT = "EvaMeasuredDockerBuilder/v3"


@dataclass(frozen=True, slots=True)
class CandidateImage:
    image_id: str
    receipt: dict[str, object]
    artifact_path: Path | None = None


Runner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("managed B Docker command failed") from error


def _image_id(value: str, *, label: str) -> str:
    if (
        not value.startswith(_IMAGE_PREFIX)
        or len(value) != len(_IMAGE_PREFIX) + 64
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise RuntimeError(f"managed B {label} is not a sha256 image ID")
    return value


def _private_parent(path: Path) -> None:
    rendered = os.fspath(path)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or "," in rendered
        or any(ord(character) < 32 for character in rendered)
    ):
        raise RuntimeError(
            "managed B candidate image output parent must be an explicit safe path"
        )
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError(
            "managed B candidate image output parent is inaccessible"
        ) from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        raise RuntimeError("managed B candidate image output parent must be private")


def _inspect(reference: str, runner: Runner) -> dict[str, Any]:
    result = runner(["docker", "image", "inspect", reference], 30)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("managed B local image inspection is invalid") from error
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RuntimeError("managed B local image inspection is invalid")
    return value[0]


def _json_command(command: list[str], runner: Runner, *, label: str) -> dict[str, Any]:
    result = runner(command, 30)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"managed B {label} is invalid") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"managed B {label} is invalid")
    return value


def _identity_sha256(path: Path, *, socket: bool, label: str) -> str:
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as error:
        raise RuntimeError(f"managed B {label} is inaccessible") from error
    expected = stat.S_ISSOCK if socket else stat.S_ISDIR
    if not path.is_absolute() or resolved != path or not expected(metadata.st_mode):
        raise RuntimeError(f"managed B {label} is invalid")
    value = {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": stat.S_IMODE(metadata.st_mode),
        "path": str(path),
        "uid": metadata.st_uid,
    }
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _docker_socket() -> Path:
    endpoint = os.environ.get("DOCKER_HOST", "unix:///run/docker.sock")
    prefix = "unix://"
    if not endpoint.startswith(prefix):
        raise RuntimeError("managed B build requires one local Docker socket")
    path = Path(endpoint[len(prefix) :])
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError("managed B Docker socket path is invalid")
    return path


def _docker_executable_sha256() -> str:
    executable = shutil.which("docker")
    if executable is None:
        raise RuntimeError("managed B Docker executable is unavailable")
    try:
        path = Path(executable).resolve(strict=True)
        metadata = path.lstat()
        raw = path.read_bytes()
    except OSError as error:
        raise RuntimeError("managed B Docker executable is inaccessible") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o022:
        raise RuntimeError("managed B Docker executable is unsafe")
    return hashlib.sha256(raw).hexdigest()


def _buildx_executable_sha256() -> str:
    config_raw = os.environ.get("DOCKER_CONFIG", "")
    expected = os.environ.get("EVA_RR002_BUILDX_SHA256", "")
    if (
        not config_raw
        or len(expected) != 64
        or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise RuntimeError("managed B Buildx authority is unavailable")
    config = Path(config_raw)
    plugin_directory = config / "cli-plugins"
    path = plugin_directory / "docker-buildx"
    try:
        config_resolved = config.resolve(strict=True)
        config_metadata = config.lstat()
        plugin_directory_resolved = plugin_directory.resolve(strict=True)
        plugin_directory_metadata = plugin_directory.lstat()
        resolved = path.resolve(strict=True)
        before = path.lstat()
    except OSError as error:
        raise RuntimeError("managed B Buildx executable is inaccessible") from error
    if (
        not config.is_absolute()
        or ".." in config.parts
        or config_resolved != config
        or plugin_directory_resolved != plugin_directory
        or not stat.S_ISDIR(config_metadata.st_mode)
        or not stat.S_ISDIR(plugin_directory_metadata.st_mode)
        or config_metadata.st_uid != os.geteuid()
        or plugin_directory_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(config_metadata.st_mode) & 0o077
        or stat.S_IMODE(plugin_directory_metadata.st_mode) & 0o077
        or resolved != path
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o077
        or stat.S_IMODE(before.st_mode) & 0o100 == 0
        or not 0 < before.st_size <= _MAX_BUILDX_EXECUTABLE_BYTES
    ):
        raise RuntimeError("managed B Buildx executable is unsafe")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError("managed B Buildx executable changed while read")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RuntimeError("managed B Buildx executable changed while read")
        after = os.fstat(descriptor)
    except OSError as error:
        raise RuntimeError("managed B Buildx executable is inaccessible") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_nlink",
        "st_uid",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if any(
        getattr(before, field) != getattr(opened, field)
        or getattr(opened, field) != getattr(after, field)
        for field in stable_fields
    ):
        raise RuntimeError("managed B Buildx executable changed while read")
    observed = digest.hexdigest()
    if observed != expected:
        raise RuntimeError("managed B Buildx executable differs from authority")
    return observed


def _buildx_version(runner: Runner) -> str:
    result = runner(["docker", "buildx", "version"], 30)
    value = result.stdout.strip()
    if (
        result.returncode != 0
        or not value
        or len(value) > 512
        or "\n" in value
        or "\r" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise RuntimeError("managed B Buildx version is invalid")
    return value


def _builder_measurement(runner: Runner) -> dict[str, object]:
    info = _json_command(
        ["docker", "info", "--format", "{{json .}}"],
        runner,
        label="Docker builder info",
    )
    version = _json_command(
        ["docker", "version", "--format", "{{json .Server}}"],
        runner,
        label="Docker builder version",
    )
    buildx_version = _buildx_version(runner)
    buildx_result = runner(["docker", "buildx", "ls", "--format", "{{json .}}"], 30)
    try:
        builders = [
            json.loads(line)
            for line in buildx_result.stdout.splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        raise RuntimeError("managed B BuildKit builder list is invalid") from error
    selected = [
        value
        for value in builders
        if isinstance(value, dict)
        and value.get("Name") == "default"
        and not value.get("Err")
    ]
    unique_selected = {
        json.dumps(value, separators=(",", ":"), sort_keys=True): value
        for value in selected
    }
    selected = list(unique_selected.values())
    if len(selected) != 1 or selected[0].get("Driver") != "docker":
        raise RuntimeError("managed B default BuildKit builder differs")
    nodes = selected[0].get("Nodes")
    if not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict):
        raise RuntimeError("managed B BuildKit node is invalid")
    node = nodes[0]
    node_ids = node.get("IDs")
    root = info.get("DockerRootDir")
    driver_status = info.get("DriverStatus")
    containerd = info.get("Containerd")
    containerd_namespaces = (
        containerd.get("Namespaces") if isinstance(containerd, dict) else None
    )
    containerd_address = (
        containerd.get("Address") if isinstance(containerd, dict) else None
    )
    containerd_containers_namespace = (
        containerd_namespaces.get("Containers")
        if isinstance(containerd_namespaces, dict)
        else None
    )
    containerd_plugins_namespace = (
        containerd_namespaces.get("Plugins")
        if isinstance(containerd_namespaces, dict)
        else None
    )
    containerd_image_store = (
        isinstance(driver_status, list)
        and ["driver-type", "io.containerd.snapshotter.v1"] in driver_status
    )
    if (
        not isinstance(info.get("ID"), str)
        or not info["ID"]
        or info.get("OSType") != "linux"
        or info.get("Architecture") not in {"x86_64", "amd64"}
        or not isinstance(root, str)
        or not isinstance(version.get("Version"), str)
        or version.get("Os") != "linux"
        or version.get("Arch") != "amd64"
        or node.get("Endpoint") != "default"
        or node.get("Status") != "running"
        or not isinstance(node.get("Version"), str)
        or not isinstance(node_ids, list)
        or len(node_ids) != 1
        or not isinstance(node_ids[0], str)
        or not node_ids[0]
        or containerd_image_store is not True
        or not isinstance(containerd_address, str)
        or not containerd_address
        or not isinstance(containerd_containers_namespace, str)
        or not containerd_containers_namespace
        or not isinstance(containerd_plugins_namespace, str)
        or not containerd_plugins_namespace
        or containerd_containers_namespace == containerd_plugins_namespace
    ):
        raise RuntimeError(
            "managed B containerd image store or Docker exporter capability differs"
        )
    return {
        "backend": "docker-buildx/docker",
        "buildkitNodeId": node_ids[0],
        "buildkitVersion": node["Version"],
        "buildxExecutableSha256": _buildx_executable_sha256(),
        "buildxVersion": buildx_version,
        "containerdContainersNamespace": containerd_containers_namespace,
        "containerdImageStore": "io.containerd.snapshotter.v1",
        "containerdPluginsNamespace": containerd_plugins_namespace,
        "containerdSocketIdentitySha256": _identity_sha256(
            Path(containerd_address), socket=True, label="containerd socket"
        ),
        "daemonId": info["ID"],
        "dockerExecutableSha256": _docker_executable_sha256(),
        "dockerServerVersion": version["Version"],
        "socketIdentitySha256": _identity_sha256(
            _docker_socket(), socket=True, label="Docker socket"
        ),
        "stateRootIdentitySha256": _identity_sha256(
            Path(root), socket=False, label="Docker state root"
        ),
    }


def _validate_builder_binding(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "after",
        "before",
        "contract",
        "runChallenge",
        "singleHost",
    }:
        raise RuntimeError("managed B builder binding differs")
    challenge = value.get("runChallenge")
    before = value.get("before")
    after = value.get("after")
    fields = {
        "backend",
        "buildkitNodeId",
        "buildkitVersion",
        "buildxExecutableSha256",
        "buildxVersion",
        "containerdContainersNamespace",
        "containerdImageStore",
        "containerdPluginsNamespace",
        "containerdSocketIdentitySha256",
        "daemonId",
        "dockerExecutableSha256",
        "dockerServerVersion",
        "socketIdentitySha256",
        "stateRootIdentitySha256",
    }
    if (
        value.get("contract") != _BUILDER_CONTRACT
        or value.get("singleHost") is not True
        or not isinstance(challenge, str)
        or len(challenge) != 64
        or any(character not in "0123456789abcdef" for character in challenge)
        or not isinstance(before, dict)
        or set(before) != fields
        or before != after
    ):
        raise RuntimeError("managed B builder binding differs")
    for key in (
        "backend",
        "buildkitNodeId",
        "buildkitVersion",
        "buildxVersion",
        "containerdContainersNamespace",
        "containerdImageStore",
        "containerdPluginsNamespace",
        "daemonId",
        "dockerServerVersion",
    ):
        if not isinstance(before[key], str) or not before[key]:
            raise RuntimeError("managed B builder identity differs")
    for key in (
        "buildxExecutableSha256",
        "dockerExecutableSha256",
        "containerdSocketIdentitySha256",
        "socketIdentitySha256",
        "stateRootIdentitySha256",
    ):
        field = before[key]
        if (
            not isinstance(field, str)
            or len(field) != 64
            or any(character not in "0123456789abcdef" for character in field)
        ):
            raise RuntimeError("managed B builder identity differs")
    return value


def _read_iid(path: Path) -> str:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError(
            "managed B candidate image ID file is inaccessible"
        ) from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > 80
    ):
        raise RuntimeError("managed B candidate image ID file is unsafe")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (metadata.st_dev, metadata.st_ino, metadata.st_size)
        ):
            raise RuntimeError(
                "managed B candidate image ID file changed before reading"
            )
        raw = os.read(descriptor, 81)
    finally:
        os.close(descriptor)
    try:
        return _image_id(raw.decode("ascii", "strict").strip(), label="candidate")
    except UnicodeDecodeError as error:
        raise RuntimeError("managed B candidate image ID file is not ASCII") from error


def _hash_image_archive(path: Path) -> str:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError(
            "managed B candidate image archive is inaccessible"
        ) from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
        or metadata.st_size <= 0
        or metadata.st_size > _MAX_IMAGE_ARCHIVE_BYTES
    ):
        raise RuntimeError("managed B candidate image archive is unsafe")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (metadata.st_dev, metadata.st_ino, metadata.st_size)
        ):
            raise RuntimeError(
                "managed B candidate image archive changed before hashing"
            )
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        final = os.fstat(descriptor)
        if (final.st_dev, final.st_ino, final.st_size) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
        ):
            raise RuntimeError(
                "managed B candidate image archive changed while hashing"
            )
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _loaded_image_id(result: subprocess.CompletedProcess[str]) -> str:
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    prefix = "Loaded image ID: "
    if len(lines) != 1 or not lines[0].startswith(prefix):
        raise RuntimeError("managed B Docker image load result is invalid")
    return _image_id(lines[0][len(prefix) :], label="loaded candidate")


def _source_identity(receipt: dict[str, object]) -> tuple[str, int]:
    source_commit = receipt.get("candidate_source_commit")
    source_date_epoch = receipt.get("candidate_source_date_epoch")
    if (
        not isinstance(source_commit, str)
        or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit)
        or isinstance(source_date_epoch, bool)
        or not isinstance(source_date_epoch, int)
        or source_date_epoch <= 0
    ):
        raise RuntimeError("managed B candidate source identity is invalid")
    return source_commit, source_date_epoch


def _image_binding(receipt: dict[str, object], role: str) -> tuple[str, str]:
    digest = receipt.get(f"{role}_image_digest")
    archive = receipt.get(f"{role}_image_archive_sha256")
    reference = receipt.get(f"{role}_image_reference")
    if (
        not isinstance(digest, str)
        or len(digest) != 71
        or not digest.startswith(_IMAGE_PREFIX)
        or any(character not in "0123456789abcdef" for character in digest[7:])
        or not isinstance(archive, str)
        or len(archive) != 64
        or any(character not in "0123456789abcdef" for character in archive)
        or not isinstance(reference, str)
        or not reference.endswith(f"@{digest}")
    ):
        raise RuntimeError(f"managed B {role} image binding is invalid")
    return digest, reference


def _require_context_base(
    context: dict[str, object], base: dict[str, object], role: str
) -> None:
    if (
        context.get(f"{role}_image_digest") != base.get("base_image_digest")
        or context.get(f"{role}_image_archive_sha256") != base.get("archive_sha256")
        or context.get(f"{role}_image_repository") != base.get("base_image_repository")
    ):
        raise RuntimeError(
            f"managed B context {role} image differs from the sealed local export"
        )


def _require_layout_base(
    layout: dict[str, object], base: dict[str, object], role: str
) -> None:
    if (
        layout.get("role") != role
        or layout.get("archive_sha256") != base.get("archive_sha256")
        or layout.get("base_image_digest") != base.get("base_image_digest")
        or layout.get("base_image_repository") != base.get("base_image_repository")
        or layout.get("oci_config_digest") != base.get("oci_config_digest")
        or layout.get("oci_selected_manifest_digest")
        != base.get("oci_selected_manifest_digest")
    ):
        raise RuntimeError(f"managed B {role} OCI layout binding differs")


def _remove_layout_root(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(
        (entry for entry in root.rglob("*") if entry.is_dir()),
        key=lambda entry: len(entry.parts),
        reverse=True,
    ):
        path.chmod(0o700)
    root.chmod(0o700)
    shutil.rmtree(root)


def _verify_untagged_candidate(
    image_id: str,
    runner: Runner,
    *,
    source_commit: str,
    source_date_epoch: int,
) -> dict[str, Any]:
    inspection = _inspect(image_id, runner)
    if _image_id(str(inspection.get("Id", "")), label="candidate") != image_id:
        raise RuntimeError("managed B candidate inspection ID differs")
    tags = inspection.get("RepoTags")
    if tags not in (None, []):
        raise RuntimeError("managed B candidate image must not receive a tag")
    size = inspection.get("Size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise RuntimeError("managed B candidate image size is invalid")
    config = inspection.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    environment = config.get("Env") if isinstance(config, dict) else None
    runtime_library_paths = (
        [
            item
            for item in environment
            if isinstance(item, str) and item.startswith("LD_LIBRARY_PATH=")
        ]
        if isinstance(environment, list)
        else []
    )
    if (
        not isinstance(labels, dict)
        or labels.get("io.eva2.source-commit") != source_commit
        or labels.get("io.eva2.source-date-epoch") != str(source_date_epoch)
        or labels.get("io.eva2.source-refresh") != "git-archive-v1"
    ):
        raise RuntimeError("managed B candidate image source identity differs")
    if runtime_library_paths != [_RUNTIME_LIBRARY_PATH]:
        raise RuntimeError("managed B candidate runtime library path differs")
    return inspection


def build_offline_candidate_image(
    context: MaterializedBuildContext,
    build_image: MaterializedBaseImage,
    runtime_libraries_image: MaterializedBaseImage,
    runtime_image: MaterializedBaseImage,
    output_parent: Path,
    *,
    runner: Runner = _run,
) -> CandidateImage:
    """Build and load one untagged image with network disabled throughout RUN steps."""

    context_receipt = validate_materialized_build_context(context)
    build_receipt = validate_materialized_base_image(build_image)
    runtime_libraries_receipt = validate_materialized_base_image(
        runtime_libraries_image
    )
    runtime_receipt = validate_materialized_base_image(runtime_image)
    bases = {
        "build": build_receipt,
        "runtime_libraries": runtime_libraries_receipt,
        "runtime": runtime_receipt,
    }
    for role, receipt in bases.items():
        _require_context_base(context_receipt, receipt, role)
    source_commit, source_date_epoch = _source_identity(context_receipt)
    run_challenge = secrets.token_hex(32)
    builder_before = _builder_measurement(runner)
    _private_parent(output_parent)
    layout_root = Path(
        tempfile.mkdtemp(prefix="eva2-base-layouts-", dir=output_parent)
    )
    layout_root.chmod(0o700)
    temporary_root = Path(
        tempfile.mkdtemp(prefix="eva2-candidate-image-", dir=output_parent)
    )
    temporary_root.chmod(0o700)
    iidfile = temporary_root / "image.iid"
    image_archive = temporary_root / "candidate.tar"
    completed = False
    try:
        materialized = {
            "build": build_image,
            "runtime_libraries": runtime_libraries_image,
            "runtime": runtime_image,
        }
        layouts: dict[str, MaterializedBaseImageLayout] = {}
        for role, base in materialized.items():
            layout = materialize_base_image_layout(base, layout_root, role=role)
            layout_receipt = validate_materialized_base_image_layout(layout)
            _require_layout_base(layout_receipt, bases[role], role)
            layouts[role] = layout
        command = [
            "docker",
            "buildx",
            "build",
            "--builder",
            "default",
            "--network=none",
            "--platform=linux/amd64",
            "--pull=false",
            "--no-cache",
            "--provenance=false",
        ]
        for role in ("build", "runtime_libraries", "runtime"):
            base = bases[role]
            reference = f"{base['base_image_repository']}@{base['base_image_digest']}"
            source = f"oci-layout://{layouts[role].path}@{base['base_image_digest']}"
            command.extend(["--build-context", f"{reference}={source}"])
        command.extend(
            [
                "--output",
                f"type=docker,dest={image_archive},rewrite-timestamp=true",
                "--build-arg",
                f"SOURCE_COMMIT={source_commit}",
                "--build-arg",
                f"SOURCE_DATE_EPOCH={source_date_epoch}",
                "--file",
                str(context.path / "deploy/living-core/Dockerfile"),
                "--iidfile",
                str(iidfile),
                str(context.path),
            ]
        )
        runner(command, 600)
        buildkit_result_digest = _read_iid(iidfile)
        image_archive_sha256 = _hash_image_archive(image_archive)
        image_id = _loaded_image_id(
            runner(
                ["docker", "image", "load", "--input", str(image_archive)],
                300,
            )
        )
        candidate_inspection = _verify_untagged_candidate(
            image_id,
            runner,
            source_commit=source_commit,
            source_date_epoch=source_date_epoch,
        )
        for role, layout in layouts.items():
            current = validate_materialized_base_image_layout(layout)
            _require_layout_base(current, bases[role], role)
        for base in materialized.values():
            validate_materialized_base_image(base)
        builder_after = _builder_measurement(runner)
        if builder_after != builder_before:
            raise RuntimeError("managed B builder identity changed during build")
        completed = True
    finally:
        _remove_layout_root(layout_root)
        if iidfile.exists() or iidfile.is_symlink():
            iidfile.unlink()
        if not completed:
            temporary_root.chmod(0o700)
            if image_archive.exists() or image_archive.is_symlink():
                image_archive.unlink()
            temporary_root.rmdir()
    receipt = {
        "build_image_archive_sha256": build_receipt["archive_sha256"],
        "build_image_digest": build_receipt["base_image_digest"],
        "build_image_reference": (
            f"{build_receipt['base_image_repository']}@{build_receipt['base_image_digest']}"
        ),
        "build_image_layout_tree_sha256": layouts["build"].receipt[
            "layout_tree_sha256"
        ],
        "candidate_image_id": image_id,
        "candidate_image_archive_sha256": image_archive_sha256,
        "candidate_buildkit_result_digest": buildkit_result_digest,
        "candidate_image_config_digest": buildkit_result_digest,
        "candidate_image_size": candidate_inspection["Size"],
        "candidate_image_exporter": "docker",
        "candidate_provenance_attestation_enabled": False,
        "candidate_runtime_library_path": _RUNTIME_LIBRARY_PATH,
        "candidate_source_commit": source_commit,
        "candidate_source_date_epoch": source_date_epoch,
        "candidate_timestamp_rewrite_enabled": True,
        "builder": {
            "after": builder_after,
            "before": builder_before,
            "contract": _BUILDER_CONTRACT,
            "runChallenge": run_challenge,
            "singleHost": True,
        },
        "context_tree_sha256": context_receipt["context_tree_sha256"],
        "contract": "EvaManagedBOfflineCandidateImageReceipt/v3",
        "network_used": False,
        "nonclaims": [
            "The candidate image is untagged and was not pushed, signed, released, or attached to Compose.",
            "No B configuration, container, volume, secret, or live service was opened.",
        ],
        "status": "built_local_candidate",
        "runtime_image_archive_sha256": runtime_receipt["archive_sha256"],
        "runtime_image_digest": runtime_receipt["base_image_digest"],
        "runtime_image_reference": (
            f"{runtime_receipt['base_image_repository']}@{runtime_receipt['base_image_digest']}"
        ),
        "runtime_image_layout_tree_sha256": layouts["runtime"].receipt[
            "layout_tree_sha256"
        ],
        "runtime_libraries_image_archive_sha256": runtime_libraries_receipt[
            "archive_sha256"
        ],
        "runtime_libraries_image_digest": runtime_libraries_receipt[
            "base_image_digest"
        ],
        "runtime_libraries_image_reference": (
            f"{runtime_libraries_receipt['base_image_repository']}@"
            f"{runtime_libraries_receipt['base_image_digest']}"
        ),
        "runtime_libraries_image_layout_tree_sha256": layouts[
            "runtime_libraries"
        ].receipt["layout_tree_sha256"],
    }
    receipt_path = temporary_root / "candidate-image-receipt.json"
    try:
        image_archive.chmod(0o600)
        write_new_private_json(
            receipt_path, receipt, label="managed B candidate image receipt"
        )
    except Exception:
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt_path.unlink()
        if image_archive.exists() or image_archive.is_symlink():
            image_archive.unlink()
        temporary_root.rmdir()
        raise
    return CandidateImage(image_id, receipt, temporary_root)


def validate_offline_candidate_image(
    candidate: CandidateImage, *, runner: Runner = _run
) -> dict[str, object]:
    if not isinstance(candidate, CandidateImage):
        raise RuntimeError("managed B candidate image must be materialized")
    receipt = candidate.receipt
    _validate_builder_binding(receipt.get("builder"))
    if (
        receipt.get("contract") != "EvaManagedBOfflineCandidateImageReceipt/v3"
        or receipt.get("status") != "built_local_candidate"
        or receipt.get("network_used") is not False
        or receipt.get("candidate_image_id") != candidate.image_id
        or receipt.get("candidate_image_exporter") != "docker"
        or receipt.get("candidate_provenance_attestation_enabled") is not False
        or receipt.get("candidate_runtime_library_path") != _RUNTIME_LIBRARY_PATH
        or receipt.get("candidate_timestamp_rewrite_enabled") is not True
    ):
        raise RuntimeError("managed B candidate image receipt differs")
    buildkit_result_digest = _image_id(
        str(receipt.get("candidate_buildkit_result_digest", "")),
        label="BuildKit result",
    )
    if (
        _image_id(str(receipt.get("candidate_image_config_digest", "")), label="config")
        != buildkit_result_digest
    ):
        raise RuntimeError("managed B candidate config digest differs")
    archive_sha256 = receipt.get("candidate_image_archive_sha256")
    if (
        not isinstance(archive_sha256, str)
        or len(archive_sha256) != 64
        or any(character not in "0123456789abcdef" for character in archive_sha256)
    ):
        raise RuntimeError("managed B candidate image archive digest differs")
    for role in ("build", "runtime_libraries", "runtime"):
        _image_binding(receipt, role)
        tree = receipt.get(f"{role}_image_layout_tree_sha256")
        if (
            not isinstance(tree, str)
            or len(tree) != 64
            or any(character not in "0123456789abcdef" for character in tree)
        ):
            raise RuntimeError(f"managed B {role} OCI layout digest differs")
    source_commit, source_date_epoch = _source_identity(receipt)
    inspection = _verify_untagged_candidate(
        candidate.image_id,
        runner,
        source_commit=source_commit,
        source_date_epoch=source_date_epoch,
    )
    if inspection["Size"] != receipt.get("candidate_image_size"):
        raise RuntimeError("managed B candidate image receipt no longer matches")
    return receipt


__all__ = [
    "CandidateImage",
    "build_offline_candidate_image",
    "validate_offline_candidate_image",
]
