"""Fetch or verify the pinned experimental model; never install runtime packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request


MODEL_KEY = "src/pipecat/audio/vad/data/silero_vad.onnx"


def verify_model(path: Path) -> Path:
    pin = json.loads(Path(__file__).with_name("vad-upstream.json").read_text())["files"][MODEL_KEY]
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8_000_000:
        raise ValueError("model must be a bounded regular file")
    if hashlib.sha256(path.read_bytes()).hexdigest() != pin["sha256"]:
        raise ValueError("model digest differs from pinned upstream")
    return path


def prepare_model(path: Path) -> Path:
    if path.exists() or path.is_symlink():
        return verify_model(path)
    pin = json.loads(Path(__file__).with_name("vad-upstream.json").read_text())["files"][MODEL_KEY]
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="silero-", suffix=".partial", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target, urllib.request.urlopen(pin["url"], timeout=20) as response:
            total = 0
            while chunk := response.read(65536):
                total += len(chunk)
                if total > 8_000_000:
                    raise ValueError("model download exceeds bound")
                target.write(chunk)
        verify_model(temporary)
        # Never replace a file another preparation/user created concurrently.
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
        return verify_model(path)
    finally:
        temporary.unlink(missing_ok=True)


def verified_detector(path: Path):
    from silero_onnx import Silero10msDetector
    return Silero10msDetector(verify_model(path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(prepare_model(args.output))
