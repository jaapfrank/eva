"""Immutable AgentSkills bundles carried by Eva's existing artifact references.

This module only validates bytes and provenance metadata. It performs no I/O,
installs nothing, executes no package code and grants no tool authority.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit

import yaml

from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256

MAX_PACKAGE_FILES = 128
MAX_PACKAGE_BYTES = 262144
MAX_SKILL_INSTRUCTIONS_BYTES = 16384
PACKAGE_CONTRACT = "EvaAgentSkillPackage/v1"


class SkillPackageRejected(ValueError):
    """An untrusted package cannot be represented without losing its identity."""


def package_path(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 240
            or any(ord(c) < 32 or c in '<>"|?*' for c in value) or "\\" in value or ":" in value):
        raise SkillPackageRejected("invalid package-relative path")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or str(path) != value or any(p in {".", ".."} for p in path.parts):
        raise SkillPackageRejected("package path escapes or is not canonical")
    reserved = {"con", "prn", "aux", "nul", *("com" + str(i) for i in range(1, 10)),
                *("lpt" + str(i) for i in range(1, 10))}
    if any(part.endswith((".", " ")) or part.split(".", 1)[0].casefold() in reserved
           for part in path.parts):
        raise SkillPackageRejected("package path is not portable across supported hosts")
    return value


def _metadata(raw: bytes) -> dict:
    if len(raw) > MAX_SKILL_INSTRUCTIONS_BYTES:
        raise SkillPackageRejected("skill instructions exceed the existing text projection bound")
    try:
        text = raw.decode("utf-8", errors="strict")
        lines = text.splitlines()
        if not lines or lines[0] != "---":
            raise ValueError("missing frontmatter")
        end = lines.index("---", 1)
        frontmatter = "\n".join(lines[1:end])
        depth = 0
        for event in yaml.parse(frontmatter):
            if isinstance(event, yaml.events.AliasEvent):
                raise ValueError("aliased metadata")
            if isinstance(event, yaml.events.CollectionStartEvent):
                depth += 1
                if depth > 64:
                    raise ValueError("metadata nesting exceeds the existing JSON depth bound")
            elif isinstance(event, yaml.events.CollectionEndEvent):
                depth -= 1
        # Reject ambiguous duplicate metadata rather than silently choosing a name.
        class UniqueKeys(yaml.SafeLoader):
            pass
        def mapping(loader, node):
            pairs = loader.construct_pairs(node)
            result = {}
            for key, value in pairs:
                if not isinstance(key, str) or key in result:
                    raise ValueError("duplicate or non-string metadata key")
                result[key] = value
            return result
        UniqueKeys.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        value = yaml.load(frontmatter, Loader=UniqueKeys)
        if not isinstance(value, dict):
            raise ValueError("metadata must be a mapping")
        name, description = value.get("name"), value.get("description")
        if (not isinstance(name, str) or len(name) > 64
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                or not isinstance(description, str) or not description.strip()
                or len(description) > 1024):
            raise ValueError("invalid skill name or description")
        canonical_json_bytes(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, yaml.YAMLError) as error:
        raise SkillPackageRejected("invalid AgentSkills frontmatter") from error


@dataclass(frozen=True, slots=True)
class SkillResource:
    path: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True, slots=True)
class AgentSkillPackage:
    repository_url: str
    revision: str
    subdirectory: str
    license_id: str
    license_path: str
    files: tuple[SkillResource, ...]

    def __post_init__(self):
        try:
            parsed = urlsplit(self.repository_url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or len(self.repository_url) > 2048):
                raise ValueError("unbound repository URL")
            if not isinstance(self.revision, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.revision):
                raise ValueError("repository revision must be immutable")
            if not isinstance(self.subdirectory, str):
                raise ValueError("package subdirectory must be text")
            if self.subdirectory:
                package_path(self.subdirectory)
            if not isinstance(self.license_id, str) or not 1 <= len(self.license_id) <= 128:
                raise ValueError("license identifier missing")
            package_path(self.license_path)
            if not isinstance(self.files, tuple) or not 1 <= len(self.files) <= MAX_PACKAGE_FILES:
                raise ValueError("package file count exceeds bound")
            seen, total = set(), 0
            for resource in self.files:
                if not isinstance(resource, SkillResource) or not isinstance(resource.content, bytes):
                    raise ValueError("package resources must be exact file bytes")
                key = package_path(resource.path).casefold()
                if key in seen:
                    raise ValueError("duplicate/case-ambiguous package resource")
                seen.add(key);total += len(resource.content)
                if total > MAX_PACKAGE_BYTES:
                    raise ValueError("package content exceeds bound")
            for name in seen:
                parts = name.split("/")
                if any("/".join(parts[:i]) in seen for i in range(1, len(parts))):
                    raise ValueError("package file conflicts with a required directory")
            self.resource(self.license_path).decode("utf-8", errors="strict")
            if not self.resource(self.license_path).strip():
                raise ValueError("license notice empty")
            _metadata(self.resource("SKILL.md"))
        except (ValueError, TypeError, UnicodeError, KeyError, AttributeError) as error:
            raise SkillPackageRejected("invalid skill package") from error

    def resource(self, path: str) -> bytes:
        package_path(path)
        for item in self.files:
            if item.path == path:
                return item.content
        raise SkillPackageRejected("package resource unavailable")

    @property
    def metadata(self) -> dict:
        return _metadata(self.resource("SKILL.md"))

    def manifest(self) -> dict:
        return {"contract": PACKAGE_CONTRACT,
            "origin": {"repository_url": self.repository_url, "revision": self.revision,
                "subdirectory": self.subdirectory, "license_id": self.license_id,
                "license_path": self.license_path},
            "files": [{"path": item.path, "bytes": len(item.content), "sha256": item.sha256}
                for item in sorted(self.files, key=lambda x:x.path)]}

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.manifest())

    def to_wire(self) -> dict:
        return {**self.manifest(), "package_sha256": self.sha256,
            "files": [{"path": item.path, "content_base64": base64.b64encode(item.content).decode("ascii"),
                "sha256": item.sha256} for item in sorted(self.files, key=lambda x:x.path)]}

    @classmethod
    def from_wire(cls, value: dict) -> AgentSkillPackage:
        try:
            if (not isinstance(value, dict) or set(value) != {"contract", "origin", "files", "package_sha256"}
                    or value["contract"] != PACKAGE_CONTRACT or not isinstance(value["files"], list)
                    or not 1 <= len(value["files"]) <= MAX_PACKAGE_FILES
                    or len(canonical_json_bytes(value)) > MAX_PACKAGE_BYTES * 2):
                raise ValueError("package envelope differs")
            files = []
            for item in value["files"]:
                if not isinstance(item, dict) or set(item) != {"path", "content_base64", "sha256"}:
                    raise ValueError("package resource fields differ")
                resource = SkillResource(item["path"], base64.b64decode(item["content_base64"], validate=True))
                if resource.sha256 != item["sha256"]:
                    raise ValueError("resource digest mismatch")
                files.append(resource)
            package = cls(**value["origin"], files=tuple(files))
            if package.sha256 != value["package_sha256"]:
                raise ValueError("package digest mismatch")
            return package
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise SkillPackageRejected("invalid skill package envelope") from error


def resource_projection(package: AgentSkillPackage, resource_path: str, *, offset: int = 0,
                        maximum_bytes: int = 16384) -> dict:
    """Read exact text as bounded untrusted data, not executable instructions."""
    if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= 16384:
        raise SkillPackageRejected("invalid resource byte bound")
    raw = package.resource(resource_path)
    if type(offset) is not int or offset < 0:
        raise SkillPackageRejected("invalid resource character offset")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise SkillPackageRejected("resource is binary; text projection unavailable") from error
    if offset > len(text):
        raise SkillPackageRejected("resource offset exceeds content")
    selected = text[offset:offset + maximum_bytes]
    # Preserve UTF-8 characters while retaining the existing byte bound.
    selected = selected.encode("utf-8")[:maximum_bytes].decode("utf-8", errors="ignore")
    end = offset + len(selected)
    return {"resource_path": resource_path, "resource_sha256": hashlib.sha256(raw).hexdigest(),
        "untrusted_content": selected, "offset": offset,
        "next_offset": end if end < len(text) else None, "total_bytes": len(raw),
        "projection": "UTF8_text_character_offsets", "complete": end == len(text) and offset == 0}
