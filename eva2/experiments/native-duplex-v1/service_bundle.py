"""Prepare and verify one offline native-speaker bundle; never activate it.

Source is an exact Git commit. Wheels/model are hash pinned; distro Python,
stdlib, FFmpeg and linked libraries are explicit host inputs checked at start.
An OS update requires a newly prepared/tested bundle, not silent dependency drift.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import sysconfig
import tarfile
import tempfile


SOURCE_PATHS = (
    "eva2/src/eva", "eva2/experiments/native-duplex-v1",
    "eva2/deploy/home-assistant/custom_components/eva2_voice/client.py",
    "eva2/deploy/home-assistant/eva2-native-speaker.service",
)
HERE = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_map(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("bundle must not contain symlinks")
        if path.is_file() and path != root / "bundle.json":
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def host_runtime(bundle: Path | None = None) -> dict:
    if sys.platform != "linux" or sys.version_info[:2] != (3, 12) or platform.machine() != "x86_64":
        raise ValueError("speaker bundle requires Linux CPython 3.12 x86_64")
    python = Path(sys.executable).resolve()
    ffmpeg = Path("/usr/bin/ffmpeg").resolve()
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    files = {python, ffmpeg}
    for path in stdlib.rglob("*"):
        if (path.is_file() and path.suffix in {".py", ".so"}
                and not any(part in {"site-packages", "dist-packages", "__pycache__"}
                            for part in path.relative_to(stdlib).parts)):
            files.add(path.resolve())
    # ldd recursively resolves DT_NEEDED dependencies, including FFmpeg's
    # codecs and Python extension dependencies. Do not execute unknown inputs.
    targets = {python, ffmpeg} | {p for p in files if p.suffix == ".so"}
    if bundle is not None:
        targets.update(p for p in (bundle / "dependencies").rglob("*")
                       if p.is_file() and (p.suffix == ".so" or ".so." in p.name))
    for binary in sorted(targets):
        result = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, check=True)
        if "not found" in result.stdout:
            raise ValueError("host audio/Python library is missing")
        for item in re.findall(r"(?:=>\s+)?(/[^\s()]+)", result.stdout):
            library = Path(item).resolve()
            if bundle is None or not library.is_relative_to(bundle):
                files.add(library)
    return {"python": str(python), "pythonVersion": platform.python_version(),
        "architecture": platform.machine(), "ffmpeg": str(ffmpeg),
        "files": {str(p): digest(p) for p in sorted(files)}}


def verify(root: Path, *, require_root: bool = True) -> dict:
    root = root.resolve(strict=True)
    manifest = root / "bundle.json"
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 2_000_000:
        raise ValueError("invalid speaker bundle manifest")
    data = json.loads(manifest.read_bytes())
    if (data.get("contract") != "EvaNativeSpeakerBundle/v1"
            or not re.fullmatch(r"[0-9a-f]{40}", data.get("sourceCommit", ""))
            or data.get("files") != file_map(root)):
        raise ValueError("speaker bundle content differs from manifest")
    if require_root:
        for path in (root, manifest, *root.rglob("*")):
            info = path.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("speaker bundle must be root-owned and immutable to the service")
    runtime = data["hostRuntime"]
    if runtime["python"] != str(Path(sys.executable).resolve()) or runtime["pythonVersion"] != platform.python_version():
        raise ValueError("speaker Python runtime differs from prepared bundle")
    for path, expected in runtime["files"].items():
        target = Path(path)
        if not target.is_absolute() or target.is_symlink() or digest(target) != expected:
            raise ValueError("speaker host runtime changed; prepare and test a new bundle")
        info = target.stat()
        if require_root and (info.st_uid != 0 or info.st_mode & 0o022):
            raise ValueError("speaker host runtime must be root-managed")
    return data


def prepare(*, repository: Path, source_commit: str, wheelhouse: Path,
            model: Path, output: Path, pip_python: Path) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("exact source commit required")
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise ValueError("bundle output must be a new absolute directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = Path(tempfile.mkdtemp(prefix=".speaker-preparing-", dir=output.parent))
    # Never overwrite a prior bundle. A failed preparation retains its clearly
    # named candidate directory for diagnosis; it cannot be activated.
    raw = subprocess.check_output(["git", "-C", str(repository), "archive", source_commit, "--", *SOURCE_PATHS])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if (path.is_absolute() or ".." in path.parts or not (member.isdir() or member.isfile())):
                raise ValueError("unsupported speaker source archive entry")
        archive.extractall(pending, filter="data")
    code = pending / "eva2/experiments/native-duplex-v1"
    pin = json.loads((code / "vad-upstream.json").read_bytes())["files"]["src/pipecat/audio/vad/data/silero_vad.onnx"]
    if model.is_symlink() or model.stat().st_size > 8_000_000 or digest(model) != pin["sha256"]:
        raise ValueError("speaker model differs from pinned upstream")
    (pending / "silero_vad.onnx").write_bytes(model.read_bytes())
    command = [str(pip_python), "-m", "pip", "install", "--no-index", "--require-hashes",
        "--only-binary=:all:", "--no-compile", "--find-links", str(wheelhouse),
        "--target", str(pending / "dependencies"), "-r", str(code / "service-requirements.lock")]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        # Dependency logs contain only offline wheel paths, never service config.
        (pending / "preparation-failure.log").write_text(result.stdout + result.stderr)
        raise ValueError("offline speaker dependency installation failed")
    data = {"contract": "EvaNativeSpeakerBundle/v1", "sourceCommit": source_commit,
        "hostRuntime": host_runtime(pending), "files": file_map(pending)}
    (pending / "bundle.json").write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
    verify(pending, require_root=False)
    for path in pending.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o444)
    pending.chmod(0o755)
    # Both names share a filesystem; publish only after the complete manifest.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(pending), -100, os.fsencode(output), 1):
        raise OSError(ctypes.get_errno(), "speaker bundle destination could not be published")
    return {"bundle": str(output), "sourceCommit": source_commit,
            "manifestSha256": digest(output / "bundle.json"), "files": len(data["files"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repository", "wheelhouse", "model", "output", "pip-python"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    try:
        print(json.dumps(prepare(**vars(parser.parse_args()))))
    except Exception:
        print('{"status":"speaker-bundle-preparation-failed"}', file=sys.stderr)
        raise SystemExit(1) from None
