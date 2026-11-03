"""Immutable task-owned package inputs for the existing host application boundary.

This is not an installer or an approval mechanism. An archive from a programming
workspace is untrusted, including its manifest and tests. Only a separate host
review may authorize the exact snapshot for application.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile

from eva.contracts.canonical_json import canonical_sha256

MAX_ARCHIVE = 8 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024
MAX_FILES = 256


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def key(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("package identity must be an exact SHA-256")
    return value


def relative_path(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 240
            or "\\" in value or any(ord(c) < 32 for c in value)
            or value.startswith("/") or str(PurePosixPath(value)) != value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("package path must be a canonical relative POSIX path")
    return value


def _object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("package JSON repeats a field")
        result[name] = value
    return result


def json_object(raw):
    value = json.loads(raw, object_pairs_hook=_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite package JSON")))
    if not isinstance(value, dict):
        raise ValueError("package metadata must be an object")
    return value


def archive_files(raw):
    """Read bounded regular bytes without extracting paths or following links."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_ARCHIVE:
        raise ValueError("package archive exceeds bound")
    files, total, members = {}, 0, 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r|gz") as archive:
        for member in archive:
            members += 1
            if members > MAX_FILES * 2:
                raise ValueError("package has too many archive entries")
            name = relative_path(member.name.rstrip("/") if member.isdir() else member.name)
            if member.isdir():
                continue
            if (not member.isfile() or member.linkname or member.pax_headers
                    or member.mode & 0o7000 or name in files
                    or len(files) >= MAX_FILES or not 0 <= member.size <= MAX_EXPANDED):
                raise ValueError("package requires unique plain regular files")
            total += member.size
            if total > MAX_EXPANDED:
                raise ValueError("expanded package exceeds bound")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("package member has no bytes")
            content = stream.read(member.size + 1)
            if len(content) != member.size:
                raise ValueError("package member size differs")
            files[name] = content
    if not files:
        raise ValueError("package is empty")
    return files


def validate_descriptor(value, files):
    """A runtime-neutral envelope; application policy is independently host-owned."""
    required = {"contract", "name", "version", "files", "entrypoint", "tests",
                "config_schema", "permissions", "tool_names", "transport"}
    if (not required <= set(value) or set(value) - required - {'activation'} or value["contract"] != "EvaOwnedPackage/v1"
            or not isinstance(value["name"], str)
            or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value["name"]) is None
            or not isinstance(value["version"], str)
            or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?", value["version"]) is None):
        raise ValueError("owned package descriptor differs")
    pins = value["files"]
    if (not isinstance(pins, dict) or set(pins) != set(files)
            or any(key(digest) != sha(files[relative_path(path)]) for path, digest in pins.items())):
        raise ValueError("owned package file set or bytes differ")
    for field in ("entrypoint", "config_schema"):
        if relative_path(value[field]) not in files:
            raise ValueError("package entrypoint or configuration schema is missing")
    if not value["entrypoint"].endswith(".py"):
        raise ValueError("initial package host supports isolated Python entrypoints")
    if (not isinstance(value["tests"], list) or not 1 <= len(value["tests"]) <= 16
            or len(set(value["tests"])) != len(value["tests"])
            or any(relative_path(path) not in files or not path.endswith(".py")
                   for path in value["tests"])):
        raise ValueError("package test inputs are missing or invalid")
    if len(files[value['config_schema']]) > 65536:
        raise ValueError('package configuration schema exceeds bound')
    schema = json_object(files[value["config_schema"]])
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError("package configuration must declare its exact object shape")
    properties = schema.get('properties', {})
    if (not isinstance(properties, dict) or len(properties) > 64
            or any(not isinstance(name, str) or re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', name) is None
                   or not isinstance(spec, dict) or spec.get('type') != 'string'
                   for name, spec in properties.items())):
        raise ValueError('package configuration requires bounded named string environment settings')
    permissions = value["permissions"]
    if (not isinstance(permissions, dict)
            or set(permissions) != {"network_hosts", "secret_names", "persistent_data"}
            or type(permissions["persistent_data"]) is not bool):
        raise ValueError("package permission declaration differs")
    for field in ("network_hosts", "secret_names"):
        values = permissions[field]
        if not isinstance(values, list) or len(values) > 16 or len(set(values)) != len(values):
            raise ValueError("package permission list differs")
        pattern = r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?" if field == "network_hosts" else r"[A-Z][A-Z0-9_]{0,63}"
        if any(not isinstance(item, str) or re.fullmatch(pattern, item) is None for item in values):
            raise ValueError("package permission name differs")
    if set(properties) & set(permissions['secret_names']):
        raise ValueError('package secrets must stay outside non-secret configuration')
    names = value["tool_names"]
    if (not isinstance(names, list) or not 1 <= len(names) <= 32 or len(set(names)) != len(names)
            or any(not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,95}", name) is None for name in names)):
        raise ValueError("package must declare its exact tool catalog")
    transport = value["transport"]
    if (not isinstance(transport, dict) or set(transport) != {"kind", "port", "path", "bearer_env"}
            or transport["kind"] != "http-json-rpc" or type(transport["port"]) is not int
            or not 1024 <= transport["port"] <= 65535
            or not isinstance(transport["path"], str)
            or re.fullmatch(r"/[a-z][a-z0-9_/-]{0,127}", transport["path"]) is None
            or transport["bearer_env"] not in permissions["secret_names"]):
        raise ValueError("package requires an explicit authenticated local HTTP transport")
    activation = value.get('activation')
    if activation is not None:
        if (not isinstance(activation, dict) or set(activation) != {'start_path', 'callback_path', 'redirect_env'}
                or any(not isinstance(activation[k], str) or re.fullmatch(r'/[a-z][a-z0-9_/-]{0,127}', activation[k]) is None
                       for k in ('start_path', 'callback_path'))
                or activation['redirect_env'] not in schema.get('properties', {})):
            raise ValueError('package activation must declare exact host-controlled routes')
    return value


def read_workspace_file(root, workspace_key, path, *, maximum=MAX_ARCHIVE):
    """Open through directory descriptors; workspace symlinks never reach the host."""
    root = Path(root)
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise ValueError("trusted programming workspace root required")
    parts = [key(workspace_key), *relative_path(path).split("/")]
    descriptors = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= maximum:
            raise ValueError("bounded regular package file required")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(maximum + 1)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(raw) != before.st_size or any(getattr(before, f) != getattr(after, f) for f in fields):
            raise ValueError("package changed during snapshot")
        return raw
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def snapshot_candidate(root, metadata, expected_sha256):
    """Only the admitted task selects a workspace; the model supplies no host path."""
    request = json_object(read_workspace_file(root, metadata["workspace_key"],
                                             "package/candidate.json", maximum=65536))
    if set(request) != {"contract", "archive", "archive_sha256", "descriptor"} or request["contract"] != "EvaOwnedPackageCandidate/v1":
        raise ValueError("package handoff declaration differs")
    raw = read_workspace_file(root, metadata["workspace_key"], request["archive"])
    if sha(raw) != key(request["archive_sha256"]) or sha(raw) != key(expected_sha256):
        raise ValueError("package handoff does not name the exact archive")
    files = archive_files(raw)
    descriptor = validate_descriptor(request["descriptor"], files)
    origin = {name: metadata[name] for name in ("task_id", "execution_id", "scope_sha256",
                                               "source_ingress_sha256", "spec_sha256", "workspace_key", "owner_binding_sha256")}
    package = {"contract": "EvaOwnedPackageSnapshot/v1", "origin": origin,
               "archive_sha256": sha(raw), "descriptor": descriptor}
    package["package_sha256"] = canonical_sha256(package)
    return package, raw, files
