"""Retain/load exact local runtime images through existing document payloads.

Only image availability is restored here. Container creation, authority, database
restore and release remain with the existing held installation orchestration.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from tools.managed_b_candidate.base_image import _archive_entries, _member_bytes, _required_blob

_ROLES = {"core", "delegated-work-worker"}
_MAX_RUNTIME_LAYERS=512  # Current core271 + bounded maintenance overlays.
_MAX_RUNTIME_DESCRIPTORS=1024

def _image(value):
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("immutable runtime image ID required")
    return value

def _hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024*1024):
            value.update(block)
    return value.hexdigest()

def validate_runtime_image_archive(path, expected_image, *, expected_sha256=None):
    path = Path(path)
    expected_image = _image(expected_image)
    before = path.lstat()
    if not path.is_file() or path.is_symlink() or before.st_nlink != 1 or before.st_uid != os.geteuid() or before.st_mode & 0o022:
        raise ValueError("owned immutable runtime image archive required")
    archive_hash = _hash(path)
    if expected_sha256 is not None and archive_hash != expected_sha256:
        raise ValueError("runtime image archive hash changed")
    entries, archive = _archive_entries(path)
    visited, selected = set(), []
    try:
        layout = json.loads(_member_bytes(archive, entries["oci-layout"], label="layout"))
        if layout != {"imageLayoutVersion": "1.0.0"}:
            raise ValueError("runtime OCI layout differs")
        index = json.loads(_member_bytes(archive, entries["index.json"], label="index"))
        def visit(descriptor, depth):
            if depth > 4 or len(visited) > _MAX_RUNTIME_DESCRIPTORS:
                raise ValueError("runtime image graph too large")
            member = _required_blob(entries, archive, descriptor, label="runtime image")
            visited.add(member.name)
            kind = descriptor.get("mediaType")
            if kind in {"application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json"}:
                value = json.loads(_member_bytes(archive, member, label="runtime index"))
                for child in value["manifests"]:
                    visit(child, depth+1)
            elif kind in {"application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"}:
                manifest = json.loads(_member_bytes(archive, member, label="runtime manifest"))
                config = manifest["config"]
                blob = _required_blob(entries, archive, config, label="runtime config")
                visited.add(blob.name)
                actual = json.loads(_member_bytes(archive, blob, label="runtime config"))
                if actual.get("os") != "linux" or actual.get("architecture") != "amd64":
                    raise ValueError("unexpected runtime platform or image ID")
                layers = manifest["layers"]
                if len(layers) > _MAX_RUNTIME_LAYERS or len(actual.get("rootfs", {}).get("diff_ids", [])) != len(layers):
                    raise ValueError("runtime root filesystem differs")
                for layer in layers:
                    blob = _required_blob(entries, archive, layer, label="runtime layer")
                    visited.add(blob.name)
                selected.append({"manifest":descriptor["digest"],"config":config["digest"]})
            else:
                raise ValueError("unsupported runtime image descriptor")
        if index.get("schemaVersion") != 2 or len(index.get("manifests", [])) != 1:
            raise ValueError("one exact runtime archive root required")
        visit(index["manifests"][0], 0)
        if len(selected)!=1 or expected_image not in {index['manifests'][0]['digest'],selected[0]['config']}:
            # containerd uses manifest ID; classic Docker uses config ID.
            # Both must be hash-verified and bound to the ONE selected image.
            raise ValueError("runtime archive image ID is not the one selected image")
        blobs = {name for name in entries if name.startswith("blobs/sha256/")}
        if blobs != visited:
            raise ValueError("runtime image archive contains unbound blobs")
        if "manifest.json" in entries:
            legacy = json.loads(_member_bytes(archive, entries["manifest.json"], label="Docker manifest"))
            if len(legacy) != 1 or legacy[0].get("Config") != "blobs/sha256/"+selected[0]["config"][7:]:
                raise ValueError("Docker image manifest differs")
    finally:
        archive.close()
    after = path.lstat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns) or _hash(path) != archive_hash:
        raise ValueError("runtime archive changed during validation")
    return {"image": expected_image, "sha256": archive_hash, "size": after.st_size}

def retain_runtime_image_pair(directory, *, before, after, run=subprocess.run):
    if set(before) != _ROLES or set(after) != _ROLES:
        raise ValueError("exact current core/worker image pair required")
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.geteuid() or directory.stat().st_mode & 0o077:
        raise ValueError("private runtime image artifact directory required")
    artifacts = {}
    for image in sorted(set(before.values()) | set(after.values())):
        image = _image(image)
        path = directory / (image[7:]+".oci.tar")
        if not path.exists():
            result = run(["docker", "image", "save", "--output", str(path), image], capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError("runtime image retention failed; inspect retained output")
            path.chmod(0o400)
        artifacts[image] = {"path": str(path), **validate_runtime_image_archive(path,image),
            "package_path": "documents/runtime-images/"+path.name, "role": "document_artifact"}
    return {"contract": "EvaRetainedRuntimeImages/v1", "before": before, "after": after, "artifacts": artifacts}

def load_held_runtime_images(receipt, *, resolve, guard, run=subprocess.run):
    """Trusted restore-only wrapper; never starts a service or releases a target."""
    if receipt.get("contract") != "EvaRetainedRuntimeImages/v1" or set(receipt["before"]) != _ROLES or set(receipt["after"]) != _ROLES:
        raise ValueError("exact retained runtime image contract required")
    expected = set(receipt["before"].values()) | set(receipt["after"].values())
    if set(receipt["artifacts"]) != expected:
        raise ValueError("last-good runtime images not retained")
    validated = []
    for image in sorted(expected):
        item = receipt["artifacts"][image]
        path = Path(resolve(item["package_path"]))
        proof = validate_runtime_image_archive(path, image, expected_sha256=item["sha256"])
        if proof["size"] != item["size"]:
            raise ValueError("runtime archive size differs")
        validated.append((image,path,item))
    imported = []
    for image,path,item in validated:
        guard()
        validate_runtime_image_archive(path,image,expected_sha256=item["sha256"])
        result = run(["docker", "image", "load", "--input", str(path)], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError("runtime image load outcome uncertain; inspect without starting")
        inspection = run(["docker", "image", "inspect", image], capture_output=True, text=True)
        rows = json.loads(inspection.stdout) if inspection.returncode == 0 else []
        if len(rows) != 1 or rows[0]["Id"] != image:
            raise ValueError("loaded runtime image ID differs")
        guard()
        imported.append(image)
    return {"contract": "EvaHeldRuntimeImagesRestored/v1", "images": imported,
            "activated": False, "services_started": False}
