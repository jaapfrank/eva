"""Hash-verified, packaged React-admin assets for the owner dashboard."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Mapping


_ROOT = files("eva.adapters.api").joinpath("dashboard_static")
_MANIFEST_NAME = "eva-dashboard-assets.json"
_ALLOWED_SUFFIXES = {".css", ".html", ".js"}


@dataclass(frozen=True, slots=True)
class DashboardAsset:
    content: bytes
    content_type: str
    sha256: str


def _safe_name(value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeError("dashboard asset manifest contains an invalid path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError("dashboard asset manifest contains an unsafe path")
    if path.suffix not in _ALLOWED_SUFFIXES:
        raise RuntimeError("dashboard asset manifest contains an unsupported asset")
    return path.as_posix()


def _content_type(name: str) -> str:
    if name.endswith(".html"):
        return "text/html; charset=utf-8"
    if name.endswith(".css"):
        return "text/css; charset=utf-8"
    if name.endswith(".js"):
        return "text/javascript; charset=utf-8"
    raise RuntimeError("dashboard asset has an unsupported media type")


def _load() -> tuple[str, Mapping[str, DashboardAsset]]:
    try:
        manifest = json.loads(_ROOT.joinpath(_MANIFEST_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("dashboard asset manifest is unavailable") from error
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {
            "contract", "entry", "files", "package_lock_sha256", "react_admin", "schema_version"
        }
        or manifest.get("contract") != "EvaDashboardAssets/v1"
        or manifest.get("schema_version") != 1
        or manifest.get("react_admin") != "5.15.1"
        or not isinstance(manifest.get("files"), dict)
    ):
        raise RuntimeError("dashboard asset manifest has an invalid contract")
    assets: dict[str, DashboardAsset] = {}
    for raw_name, expected in manifest["files"].items():
        name = _safe_name(raw_name)
        if (
            not isinstance(expected, dict)
            or set(expected) != {"sha256", "size"}
            or not isinstance(expected["sha256"], str)
            or len(expected["sha256"]) != 64
            or not isinstance(expected["size"], int)
            or expected["size"] < 1
        ):
            raise RuntimeError("dashboard asset manifest contains invalid evidence")
        try:
            content = _ROOT.joinpath(*PurePosixPath(name).parts).read_bytes()
        except FileNotFoundError as error:
            raise RuntimeError("dashboard asset is unavailable") from error
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != expected["size"] or digest != expected["sha256"]:
            raise RuntimeError("dashboard asset differs from its manifest")
        assets[name] = DashboardAsset(content, _content_type(name), digest)
    entry = _safe_name(manifest.get("entry"))
    if entry not in assets or set(assets) != {entry, *[name for name in assets if name.startswith("assets/")]}:
        raise RuntimeError("dashboard asset set is invalid")
    return entry, MappingProxyType(assets)


DASHBOARD_ENTRY, DASHBOARD_ASSETS = _load()


__all__ = ["DASHBOARD_ASSETS", "DASHBOARD_ENTRY", "DashboardAsset"]
