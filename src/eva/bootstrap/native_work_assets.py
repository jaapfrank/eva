"""Host-owned native input manifests, composed outside the queue adapter.

The manifest is private deployment configuration, never model-authored paths.
Core and worker must mount the same inputs at the same absolute paths. Static manifests are read-only. Task-bound delivery publishes the verified
source envelope before admission; it never infers filesystem access from prose.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
import re

from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal


_SETTING = "EVA_NATIVE_WORK_INPUTS_FILE"
_ROOT_SETTING = "EVA_NATIVE_WORK_INPUTS_ROOT"


def native_input_binding(spec):
    return {
        "source": spec.source.model_dump(mode="json"),
        "source_ingress_sha256": spec.source_ingress_sha256,
        "intent_id": spec.origin.intent_id,
        "execution_id": spec.execution_id,
        "grant_sha256": canonical_sha256(spec.grant.model_dump(mode="json")),
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("native input manifest repeats a field")
        result[key] = value
    return result


def _absolute_directory(value):
    if not isinstance(value, str):
        raise ValueError("native input root must be a string")
    path = Path(value)
    if (not path.is_absolute() or path == Path("/")
            or path.resolve(strict=True) != path or not path.is_dir()):
        raise ValueError("native input root must be an exact existing directory")
    return path


class HostNativeWorkAssets:
    def __init__(self, manifest_file):
        self.manifest_file = manifest_file

    def __call__(self, spec):
        # Re-read private host configuration at admission AND call-start. The
        # credentials reader enforces owner, mode, size and non-symlink checks.
        raw = read_runtime_secret_file(self.manifest_file, setting_name=_SETTING)
        manifest = json.loads(raw, object_pairs_hook=_unique_object)
        if (not isinstance(manifest, dict)
                or set(manifest) != {"contract", "allowed_roots", "tasks"}
                or manifest["contract"] != "EvaNativeWorkInputs/v1"
                or not isinstance(manifest["allowed_roots"], list)
                or not 1 <= len(manifest["allowed_roots"]) <= 8
                or not isinstance(manifest["tasks"], dict)):
            raise ValueError("invalid native input manifest")
        roots = tuple(_absolute_directory(value) for value in manifest["allowed_roots"])
        binding = native_input_binding(spec)
        entry = manifest["tasks"].get(canonical_sha256(binding))
        if (not isinstance(entry, dict) or set(entry) != {"binding", "files"}
                or entry["binding"] != binding
                or not isinstance(entry["files"], list) or len(entry["files"]) > 32):
            raise ValueError("native task has no exact host input declaration")
        assets = []
        for item in entry["files"]:
            if (not isinstance(item, dict) or set(item) != {"path", "sha256"}
                    or not isinstance(item["path"], str)
                    or not isinstance(item["sha256"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None):
                raise ValueError("invalid native input asset")
            path = Path(item["path"])
            if (not path.is_absolute() or path.resolve(strict=True) != path
                    or not any(path.is_relative_to(root) for root in roots)
                    or any(path == existing for existing, _ in assets)):
                raise ValueError("native input asset is outside its host roots or repeated")
            assets.append((path, item["sha256"]))
        # An empty tuple is valid ONLY for an explicit bound files=[] entry.
        return tuple(assets)


def _private_directory(path):
    if not path.is_absolute() or path == Path("/") or path.resolve(strict=True) != path:
        raise ValueError("native input directory must be exact and absolute")
    metadata = path.stat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077):
        raise ValueError("native input directory must be private to the runtime owner")
    return path


def _publish_immutable(path, value):
    """Publish complete bytes once; retries can only read back identical bytes."""
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(raw) > 64 * 1024:
        raise ValueError("native input snapshot exceeds the private file bound")
    descriptor, temporary = tempfile.mkstemp(prefix=".native-input-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            existing = read_runtime_secret_file(str(path), setting_name=_ROOT_SETTING)
            if existing.encode() != raw.rstrip(b"\n"):
                raise ValueError("native input snapshot already owns different bytes")
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


class TaskBoundNativeWorkAssets:
    """One shared source snapshot per immutable execution/source/grant binding.

    Source input is real host-verified ingress, not a fabricated empty input list.
    Ambient workspaces remain forbidden by the existing grant. Other files are
    still accessed through their existing independently guarded host tools.
    """

    def __init__(self, root, *, producer=False):
        self.root = _private_directory(Path(root))
        self.producer = producer

    def _directory(self, spec):
        return self.root / canonical_sha256(native_input_binding(spec))

    def prepare(self, spec, turn):
        if not self.producer:
            raise RuntimeError("native worker may read inputs but cannot produce them")
        from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
        if type(spec) is not DelegatedInitiativeDigitalWorkSpec:
            raise ValueError("native input delivery requires a typed native task")
        ingress = PostgresConversationJournal._ingress_data(turn)
        source = {key: getattr(turn, key) for key in (
            "tenant_id", "installation_id", "principal_id", "conversation_id", "message_id")}
        source["audience"] = {"kind": turn.audience.kind, "principal_id": turn.audience.principal_id}
        if (turn.audience.kind != "private_person"
                or source != spec.source.model_dump(mode="json")
                or canonical_sha256(ingress) != spec.source_ingress_sha256
                or turn.text != spec.task):
            raise ValueError("native input delivery differs from its verified original source")
        _private_directory(self.root)
        directory = self._directory(spec)
        directory.mkdir(mode=0o700, exist_ok=True)
        _private_directory(directory)
        lock = os.open(directory / ".publish.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            metadata = os.fstat(lock)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) & 0o077):
                raise ValueError("native input publication lock must be private")
            fcntl.flock(lock, fcntl.LOCK_EX)
            binding = native_input_binding(spec)
            payload = {"contract": "EvaNativeTaskInput/v1", "binding": binding,
                       "task": spec.task, "source_ingress": ingress}
            source_file = directory / "source.json"
            _publish_immutable(source_file, payload)
            digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
            manifest = {"contract": "EvaNativeWorkInputs/v1", "allowed_roots": [str(directory)],
                        "tasks": {canonical_sha256(binding): {"binding": binding,
                                  "files": [{"path": str(source_file), "sha256": digest}]}}}
            _publish_immutable(directory / "manifest.json", manifest)
        finally:
            os.close(lock)
        return self(spec)

    def __call__(self, spec):
        _private_directory(self.root)
        directory = _private_directory(self._directory(spec))
        assets = HostNativeWorkAssets(str(directory / "manifest.json"))(spec)
        source_file = directory / "source.json"
        raw = read_runtime_secret_file(str(source_file), setting_name=_ROOT_SETTING)
        payload = json.loads(raw, object_pairs_hook=_unique_object)
        binding = native_input_binding(spec)
        if (not isinstance(payload, dict)
                or set(payload) != {"contract", "binding", "task", "source_ingress"}
                or payload["contract"] != "EvaNativeTaskInput/v1"
                or payload["binding"] != binding or payload["task"] != spec.task
                or canonical_sha256(payload["source_ingress"]) != spec.source_ingress_sha256
                or len(assets) != 1 or assets[0][0] != source_file
                or hashlib.sha256(source_file.read_bytes()).hexdigest() != assets[0][1]):
            raise ValueError("native input snapshot is not the admitted task source")
        return assets


def prepare_native_work_assets(service, spec, turn):
    resolver = service.store._native_required_assets
    prepare = getattr(resolver, "prepare", None)
    if callable(prepare):
        prepare(spec, turn)


def configured_native_work_assets(*, producer=False):
    path = os.environ.get(_SETTING, "").strip()
    root = os.environ.get(_ROOT_SETTING, "").strip()
    if path and root:
        raise ValueError("configure either a static native manifest or task-bound native inputs")
    if root:
        return TaskBoundNativeWorkAssets(root, producer=producer)
    return HostNativeWorkAssets(path) if path else None
