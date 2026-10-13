#!/usr/bin/env python3
"""Materialize the exact React-admin build as sealed Python package data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "experiments" / "react-admin-dashboard-v1" / "dist"
DESTINATION = ROOT / "src" / "eva" / "adapters" / "api" / "dashboard_static"
VITE_MANIFEST = "asset-manifest.json"
SEALED_MANIFEST = "eva-dashboard-assets.json"
ALLOWED_SUFFIXES = {".css", ".html", ".js"}


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError("dashboard build contains an unsafe path")
    return path.as_posix()


def _source_files() -> dict[str, bytes]:
    manifest_path = SOURCE / VITE_MANIFEST
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise RuntimeError("dashboard build manifest is missing")
    try:
        vite = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("dashboard build manifest is invalid") from error
    entry = vite.get("index.html") if isinstance(vite, dict) else None
    if not isinstance(entry, dict) or entry.get("isEntry") is not True:
        raise RuntimeError("dashboard build has no unique application entry")
    names = {"index.html", _safe_relative(str(entry.get("file", "")))}
    css = entry.get("css")
    if not isinstance(css, list) or len(css) != 1:
        raise RuntimeError("dashboard build must have exactly one stylesheet")
    names.add(_safe_relative(str(css[0])))
    files: dict[str, bytes] = {}
    for name in sorted(names):
        path = SOURCE / name
        if path.is_symlink() or not path.is_file() or path.suffix not in ALLOWED_SUFFIXES:
            raise RuntimeError("dashboard build contains an unsupported asset")
        content = path.read_bytes()
        if not content:
            raise RuntimeError("dashboard build contains an empty asset")
        files[name] = content
    return files


def _sealed_manifest(files: dict[str, bytes]) -> bytes:
    package_lock = (
        ROOT / "experiments" / "react-admin-dashboard-v1" / "package-lock.json"
    ).read_bytes()
    payload = {
        "contract": "EvaDashboardAssets/v1",
        "entry": "index.html",
        "files": {
            name: {"sha256": _sha256(content), "size": len(content)}
            for name, content in sorted(files.items())
        },
        "package_lock_sha256": _sha256(package_lock),
        "react_admin": "5.15.1",
        "schema_version": 1,
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _expected() -> dict[str, bytes]:
    files = _source_files()
    return {**files, SEALED_MANIFEST: _sealed_manifest(files)}


def _actual() -> dict[str, bytes]:
    if not DESTINATION.exists():
        return {}
    if DESTINATION.is_symlink() or not DESTINATION.is_dir():
        raise RuntimeError("dashboard package asset path is unsafe")
    files: dict[str, bytes] = {}
    for path in sorted(DESTINATION.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("dashboard package assets contain a link")
        if path.is_file():
            files[path.relative_to(DESTINATION).as_posix()] = path.read_bytes()
    return files


def materialize() -> None:
    expected = _expected()
    if DESTINATION.exists():
        _actual()
        shutil.rmtree(DESTINATION)
    for name, content in expected.items():
        target = DESTINATION / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def check() -> None:
    expected = _expected()
    actual = _actual()
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(name for name in set(actual) & set(expected) if actual[name] != expected[name])
        raise RuntimeError(
            f"dashboard package assets differ: missing={missing}, extra={extra}, changed={changed}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--materialize", action="store_true")
    mode.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    materialize() if arguments.materialize else check()
    print(json.dumps({"contract": "EvaDashboardAssetBuildReceipt/v1", "status": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
