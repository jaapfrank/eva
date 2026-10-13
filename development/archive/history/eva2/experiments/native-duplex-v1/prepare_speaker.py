"""Prepare a pinned, patched component in a NEW build directory; never flash."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def prepare(upstream: Path, destination: Path, *, websocket_upstream: Path) -> Path:
    manifest = json.loads((HERE / "speaker-upstream.json").read_text())
    # Validate all source bytes before creating any destination files.
    contents = {}
    for name, expected in manifest["files"].items():
        if Path(name).name != name:
            raise ValueError("Manifest filename must be a basename")
        source = upstream / "components" / "va_pipecat" / name
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 256_000:
            raise ValueError(f"Invalid upstream file: {name}")
        contents[name] = source.read_bytes()
        if hashlib.sha256(contents[name]).hexdigest() != expected:
            raise ValueError(f"Upstream hash mismatch: {name}")
    license_data = (HERE / "LICENSE.va_pipecat").read_bytes()
    if hashlib.sha256(license_data).hexdigest() != manifest["licenseSha256"]:
        raise ValueError("Upstream license hash mismatch")
    transport_manifest = json.loads((HERE / "websocket-upstream.json").read_text())
    transport_contents = {}
    for name, expected in transport_manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid transport source path")
        source = websocket_upstream / relative
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 256_000:
            raise ValueError(f"Invalid transport source: {name}")
        transport_contents[name] = source.read_bytes()
        if hashlib.sha256(transport_contents[name]).hexdigest() != expected:
            raise ValueError(f"Transport upstream hash mismatch: {name}")
    if (HERE / "LICENSE.esp_websocket_client").read_bytes() != transport_contents["LICENSE"]:
        raise ValueError("Transport license mismatch")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Destination must be new; existing builds are never overwritten")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".eva-speaker-", dir=destination.parent) as staging:
        stage = Path(staging)
        component = stage / "components" / "va_pipecat"
        component.mkdir(parents=True)
        for name, data in contents.items():
            (component / name).write_bytes(data)
        for name in ["speaker-stop.patch", "speaker-wire.patch", "speaker-auth.patch", "speaker-input.patch"]:
            patch = str(HERE / name)
            for args in [["--check", patch], [patch]]:
                subprocess.run(["git", "apply", *args], cwd=component, check=True, capture_output=True, text=True)
        for name in ["playback_stop_barrier.h", "eva_output_protocol.h", "eva_output_driver.cpp", "eva_input_protocol.h", "eva_microphone_source.h", "eva_input_driver.cpp"]:
            shutil.copyfile(HERE / name, component / name)
        transport = component / "esp_websocket_client"
        for name, data in transport_contents.items():
            target = transport / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        patch = str(HERE / "websocket-no-redirect.patch")
        for args in [["--check", patch], [patch]]:
            subprocess.run(["git", "apply", *args], cwd=transport, check=True, capture_output=True, text=True)
        (stage / "LICENSE").write_bytes(license_data)
        receipt = {
            "upstream": manifest["revision"],
            "patchSha256": hashlib.sha256((HERE / "speaker-stop.patch").read_bytes()).hexdigest(),
            "wirePatchSha256": hashlib.sha256((HERE / "speaker-wire.patch").read_bytes()).hexdigest(),
            "authPatchSha256": hashlib.sha256((HERE / "speaker-auth.patch").read_bytes()).hexdigest(),
            "inputPatchSha256": hashlib.sha256((HERE / "speaker-input.patch").read_bytes()).hexdigest(),
            "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(component.iterdir()) if p.is_file()},
            "transport": {"revision": transport_manifest["revision"],
                          "patchSha256": hashlib.sha256((HERE / "websocket-no-redirect.patch").read_bytes()).hexdigest(),
                          "files": {str(p.relative_to(transport)): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in sorted(transport.rglob("*")) if p.is_file()}},
            "deployable": False,
        }
        (stage / "prepared.json").write_text(json.dumps(receipt, indent=2) + "\n")
        # Exclusive creation also refuses a destination created during staging.
        destination.mkdir()
        for entry in stage.iterdir():
            shutil.move(str(entry), str(destination / entry.name))
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--websocket-upstream", required=True, type=Path)
    args = parser.parse_args()
    print(prepare(args.upstream, args.output, websocket_upstream=args.websocket_upstream))
