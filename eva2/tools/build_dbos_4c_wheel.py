#!/usr/bin/env python3
"""Build the hash-guarded DBOS 2.30.0+eva.1 wheel for Eva tranche 4C."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PATCH_ROOT = ROOT / "vendor" / "dbos-2.30.0-eva1"
MANIFEST_PATH = PATCH_ROOT / "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_hash(path: Path, expected: str, label: str) -> None:
    actual = _sha256(path)
    if actual != expected:
        raise SystemExit(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")


def _record_digest(path: Path) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest())
    return "sha256=" + encoded.rstrip(b"=").decode("ascii")


def _replace_metadata_version(metadata: Path, old: str, new: str) -> None:
    original = metadata.read_text(encoding="utf-8")
    needle = f"Version: {old}\n"
    if original.count(needle) != 1:
        raise SystemExit(f"expected exactly one {needle.strip()!r} in {metadata}")
    metadata.write_text(original.replace(needle, f"Version: {new}\n"), encoding="utf-8")


def _write_record(root: Path, dist_info: Path) -> None:
    record = dist_info / "RECORD"
    rows: list[str] = []
    for path in sorted(
        item for item in root.rglob("*") if item.is_file() and item != record
    ):
        relative = path.relative_to(root).as_posix()
        rows.append(f"{relative},{_record_digest(path)},{path.stat().st_size}")
    rows.append(f"{record.relative_to(root).as_posix()},,")
    record.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")


def _write_deterministic_wheel(root: Path, output: Path) -> None:
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as wheel:
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 & 0xFFFF) << 16
            wheel.writestr(
                info,
                path.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )


def build(
    upstream_wheel: Path,
    output_directory: Path,
    *,
    patch_root: Path = PATCH_ROOT,
) -> Path:
    manifest_path = patch_root / "manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    upstream = manifest["upstream_wheel"]
    patch_spec = manifest["patch"]
    patch_path = patch_root / patch_spec["filename"]

    if upstream_wheel.name != upstream["filename"]:
        raise SystemExit(f"unexpected upstream filename: {upstream_wheel.name}")
    if upstream_wheel.stat().st_size != upstream["size_bytes"]:
        raise SystemExit("upstream wheel size mismatch")
    _require_hash(upstream_wheel, upstream["sha256"], "upstream wheel")
    _require_hash(patch_path, patch_spec["sha256"], "patch")

    with tempfile.TemporaryDirectory(prefix="eva2-dbos-4c-") as temporary:
        unpacked = Path(temporary) / "unpacked"
        unpacked.mkdir()
        with zipfile.ZipFile(upstream_wheel) as wheel:
            wheel.extractall(unpacked)

        member = patch_spec["members"][0]
        source_path = unpacked / member["path"]
        _require_hash(source_path, member["upstream_sha256"], "upstream source member")
        subprocess.run(
            ["patch", "--batch", "--forward", "-p1", "-i", os.fspath(patch_path)],
            cwd=unpacked,
            check=True,
        )
        _require_hash(source_path, member["patched_sha256"], "patched source member")

        old_dist_info = unpacked / f"dbos-{manifest['upstream_version']}.dist-info"
        new_dist_info = unpacked / f"dbos-{manifest['patched_version']}.dist-info"
        old_dist_info.rename(new_dist_info)
        _replace_metadata_version(
            new_dist_info / "METADATA",
            manifest["upstream_version"],
            manifest["patched_version"],
        )
        _write_record(unpacked, new_dist_info)

        output_directory.mkdir(parents=True, exist_ok=True)
        output_spec = manifest["output_wheel"]
        output = output_directory / output_spec["filename"]
        temporary_output = output.with_suffix(".whl.tmp")
        _write_deterministic_wheel(unpacked, temporary_output)
        _require_hash(temporary_output, output_spec["sha256"], "output wheel")
        temporary_output.replace(output)
        return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("upstream_wheel", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument(
        "--patch-root",
        type=Path,
        default=PATCH_ROOT,
        help="directory containing a pinned DBOS patch manifest (default: 4C fork)",
    )
    arguments = parser.parse_args()
    print(
        build(
            arguments.upstream_wheel.resolve(),
            arguments.output_directory.resolve(),
            patch_root=arguments.patch_root.resolve(),
        )
    )


if __name__ == "__main__":
    main()
