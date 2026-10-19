"""Bind a supervised voice probe to the runtime it actually used (stdlib only).

Call snapshot() INSIDE the service at recording start and close, after relevant
modules have loaded. No module is imported to manufacture loaded-source proof.
The coordinator brackets these snapshots with capture_containers() on the host,
then calls compare(). Missing/changed evidence invalidates the run.

Only digests, deployed source paths and a fixed safe settings vocabulary leave
the process. Configuration/fixture paths, credentials, device identifiers, raw
environment and fixture contents are never returned. Keep even these manifests
private until reviewed. Identity is not acoustic, timing or owner-acceptance proof.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import marshal
import os
from pathlib import Path
import re
import subprocess
import sys
from types import FunctionType
from uuid import UUID


CONTRACT = "EvaVoiceRuntimeIdentity/v1"
CONTAINERS_CONTRACT = "EvaVoiceContainerIdentity/v1"
NATIVE_MODULES = (
    "__main__", "speaker_endpoint", "speaker_livekit_session", "speaker_livekit_audio",
    "speaker_input", "speaker_wire", "eva.voice_service.codex_media",
    "eva.voice_service.codex_speaker_audio", "eva.voice_service.codex_session",
    "eva.voice_service.literal_speech", "eva.voice_service.media",
    "eva.adapters.connectors.home_assistant_voice",
)
_SAFE_VALUES = {
    "tts_engine": {"cloud", "tts.cloud", "tts.home_assistant_cloud"},
    "tts_language": {"nl", "nl-NL", "en", "en-US", "en-GB"},
    "tts_voice": {"FennaNeural", "Juniper", "juniper"},
}
_SAFE_FLAGS = {"wake_greeting", "render_only", "microphone_enabled"}
_LABEL = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _json_default(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (Path, UUID)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError("Unsupported identity configuration value")


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         default=_json_default, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"sha256": digest.hexdigest(), "bytes": size}


def _files(paths, kind, errors):
    result = {}
    if not isinstance(paths, Mapping) or not paths:
        errors.append(kind + ":missing")
        return result
    for label, path in paths.items():
        if not isinstance(label, str) or not _LABEL.fullmatch(label):
            errors.append(kind + ":invalid_label")
            continue
        try:
            result[label] = _file(path)
        except (OSError, TypeError, ValueError):
            errors.append(kind + ":unreadable")
    return result


def _code_digest(module):
    """Hash resident function code too: on-disk bytes alone can be stale."""
    digest = hashlib.sha256()

    def add(name, value):
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        if isinstance(value, FunctionType):
            digest.update(name.encode())
            digest.update(marshal.dumps(value.__code__))
        elif isinstance(value, property):
            for suffix, function in (("get", value.fget), ("set", value.fset),
                                     ("del", value.fdel)):
                add(name + "." + suffix, function)

    for name, value in sorted(vars(module).items()):
        add(name, value)
        if isinstance(value, type) and value.__module__ == module.__name__:
            for member, function in sorted(vars(value).items()):
                add(name + "." + member, function)
    return digest.hexdigest()


def capture_loaded_sources(module_names: Sequence[str]):
    """Return loaded origin/file/code digests and sanitized failure codes.

    Loaded-code digests describe resident functions, not every mutable global.
    snapshot() binds the explicitly supplied runtime configuration separately.
    """
    sources, errors = {}, []
    if not module_names or isinstance(module_names, (str, bytes)):
        return {"sources": {}, "errors": ["sources:missing"]}
    for name in module_names:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][\w.]{0,199}", name):
            errors.append("sources:invalid_name")
            continue
        module = sys.modules.get(name)
        if module is None:
            errors.append("sources:not_loaded:" + name)
            continue
        try:
            path = Path(module.__file__).resolve(strict=True)
            if path.suffix != ".py":
                raise ValueError("A Python source file is required")
            # Actual precedence remains visible for deployed code; private test
            # paths never escape into the manifest.
            public_path = str(path) if any(path.is_relative_to(root) for root in
                (Path("/opt/eva"), Path("/opt/eva-voice-deps"), Path("/app"))) else None
            sources[name] = {**_file(path), "origin": public_path,
                "origin_sha256": _digest(str(path)),
                "loaded_code_sha256": _code_digest(module)}
        except (AttributeError, OSError, TypeError, ValueError):
            errors.append("sources:unreadable:" + name)
    return {"sources": sources, "errors": errors}


def _safe_settings(config):
    result = {}
    if dataclasses.is_dataclass(config) and not isinstance(config, type):
        config = dataclasses.asdict(config)
    if not isinstance(config, Mapping):
        return result
    for key, allowed in _SAFE_VALUES.items():
        value = config.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    for key in _SAFE_FLAGS:
        if type(config.get(key)) is bool:
            result[key] = config[key]
    return result


def snapshot(*, runtime_config, config_files, fixtures,
             module_names=NATIVE_MODULES, resolved_settings=None):
    """Capture the running process, not a newly imported subprocess replica.

    runtime_config must be the actual loaded config object. config_files binds
    its disk inputs separately. fixtures can name a predeclared scenario file
    for a physical trial; it need not contain recorded household speech.
    resolved_settings optionally pins values from the constructed audio objects.
    """
    loaded = capture_loaded_sources(module_names)
    errors = list(loaded["errors"])
    identity = {"sources": loaded["sources"],
        "config_files": _files(config_files, "config_files", errors),
        "fixtures": _files(fixtures, "fixtures", errors),
        "settings": _safe_settings(runtime_config)}
    try:
        if runtime_config is None:
            raise ValueError("Loaded configuration is required")
        identity["runtime_config_sha256"] = _digest(runtime_config)
        identity["resolved_settings_sha256"] = _digest(resolved_settings)
        identity["settings"].update(_safe_settings(resolved_settings))
        # Field 22 follows the final ')' because the process name may contain
        # spaces/parentheses. A new process in the same container must not pass.
        stat = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
        identity["process"] = {"pid": os.getpid(), "start_ticks": int(stat[19]),
                               "python": list(sys.version_info[:3])}
    except (OSError, TypeError, ValueError, IndexError):
        errors.append("process_or_configuration:unavailable")
    return {"contract": CONTRACT, "captured_at": datetime.now(timezone.utc).isoformat(),
            "valid": not errors, "identity": identity, "errors": errors}


def container_configuration_digest(raw):
    """Bind every configuration field, ignoring only Docker's mount-list order."""
    mounts = raw["Mounts"]
    if not isinstance(mounts, list) or not all(isinstance(item, dict) for item in mounts):
        raise ValueError("Invalid container mounts")
    return _digest({"Config": raw["Config"], "HostConfig": raw["HostConfig"],
        "Mounts": sorted(mounts, key=lambda item: json.dumps(
            item, sort_keys=True, separators=(",", ":"), allow_nan=False))})


def capture_containers(containers: Mapping[str, str]):
    """Host-only Docker reads. Use neutral role aliases, not device identities."""
    items, errors = {}, []
    if not containers:
        errors.append("containers:missing")
    for alias, name in containers.items():
        if not isinstance(alias, str) or not _LABEL.fullmatch(alias):
            errors.append("containers:invalid_label")
            continue
        try:
            response = subprocess.run(["docker", "inspect", "--type", "container", name],
                capture_output=True, check=True, timeout=15)
            raw = json.loads(response.stdout)[0]
            state = raw["State"]
            image, identifier, started = raw["Image"], raw["Id"], state["StartedAt"]
            if (not _SHA.fullmatch(identifier) or not image.startswith("sha256:")
                    or not _SHA.fullmatch(image[7:]) or not started):
                raise ValueError("Incomplete container identity")
            items[alias] = {"image": image, "container_sha256": _digest(identifier),
                "started_at": started, "restart_count": raw["RestartCount"],
                "running": state["Running"],
                "configuration_sha256": container_configuration_digest(raw)}
            if state["Running"] is not True:
                errors.append("containers:not_running:" + alias)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
            # Docker's error/inspect output may contain environment or paths.
            errors.append("containers:unavailable:" + alias)
    return {"contract": CONTAINERS_CONTRACT, "valid": not errors,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "identity": items, "errors": errors}


def _complete(value, contract):
    def timestamp_valid(raw):
        try:
            return isinstance(raw, str) and datetime.fromisoformat(raw).tzinfo is not None
        except ValueError:
            return False

    if (not isinstance(value, dict) or value.get("contract") != contract
            or value.get("valid") is not True or value.get("errors") != []
            or not timestamp_valid(value.get("captured_at"))
            or not isinstance(value.get("identity"), dict) or not value["identity"]):
        return False
    identity = value["identity"]
    if contract == CONTRACT:
        def files_valid(files, source=False):
            return (isinstance(files, dict) and bool(files) and all(
                isinstance(item, dict) and type(item.get("bytes")) is int
                and item["bytes"] >= 0 and all(isinstance(item.get(key), str)
                    and _SHA.fullmatch(item[key]) for key in
                    (("sha256", "origin_sha256", "loaded_code_sha256") if source
                     else ("sha256",))) for item in files.values()))

        process = identity.get("process")
        return (isinstance(process, dict) and type(process.get("pid")) is int
                and process["pid"] > 0 and type(process.get("start_ticks")) is int
                and process["start_ticks"] >= 0
                and isinstance(process.get("python"), list) and len(process["python"]) == 3
                and all(type(part) is int for part in process["python"])
                and files_valid(identity.get("sources"), source=True)
                and files_valid(identity.get("config_files"))
                and files_valid(identity.get("fixtures"))
                and isinstance(identity.get("settings"), dict)
                and _safe_settings(identity["settings"]) == identity["settings"]
                and all(isinstance(identity.get(key), str) and _SHA.fullmatch(identity[key])
                        for key in ("runtime_config_sha256", "resolved_settings_sha256")))
    return all(isinstance(item, dict) and item.get("running") is True
               and isinstance(item.get("image"), str) and item["image"].startswith("sha256:")
               and _SHA.fullmatch(item["image"][7:])
               and timestamp_valid(item.get("started_at"))
               and all(isinstance(item.get(key), str) and _SHA.fullmatch(item[key])
                       for key in ("container_sha256", "configuration_sha256"))
               and type(item.get("restart_count")) is int and item["restart_count"] >= 0
               for item in identity.values())


def compare(before, after, *, containers_before, containers_after):
    """Fail closed; report only differing field names, never before/after values."""
    issues = []
    pairs = (("process", before, after, CONTRACT),
             ("containers", containers_before, containers_after, CONTAINERS_CONTRACT))
    for label, first, last, contract in pairs:
        if not _complete(first, contract) or not _complete(last, contract):
            issues.append(label + ":missing_or_invalid")
            continue
        for key in sorted(first["identity"].keys() | last["identity"].keys()):
            if first["identity"].get(key) != last["identity"].get(key):
                # Keys may be supplied by a malformed manifest. Do not echo them.
                issues.append(label + ":identity_changed")
                break
    return {"valid": not issues, "status": "bound" if not issues else "invalid",
            "issues": issues, "proves": "runtime_identity_only"}
