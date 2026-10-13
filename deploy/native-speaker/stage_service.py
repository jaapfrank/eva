"""Create a new private, inactive service stage; never run Compose or edit the core."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

BASE_IMAGE = "sha256:1aab254f21a58cf97844679f2b0a0f82ea6507989badf3229796fb471193dd51"
IMAGE = "sha256:1e3445797bcdc1afe350370c14d91f07ccb1bc818ea9fa1a8d1af737d9ec1aba"
DEVICES = {
    "assist_satellite.home_assistant_voice_0a16e0_assist_satellite": "9e877ca00f57b1b75e0c2e3318abc704",
    "assist_satellite.home_assistant_voice_0a5035_assist_satellite": "f075b8ba2b711d74fff7d4756c620d64",
}


def stage(package, target, core_state):
    os.umask(0o077)
    if os.geteuid() != 0 or target.exists() or not target.resolve().is_relative_to(Path("/var/lib/eva2/native-speaker/candidates")):
        raise ValueError("Use root and a new native-speaker candidate directory")
    manifest = json.loads((package / "manifest.json").read_text())
    raw = (package / "config.json").read_bytes()
    if manifest["image"] != BASE_IMAGE or hashlib.sha256(raw).hexdigest() != manifest["config_sha256"]:
        raise ValueError("Private package differs from its manifest")
    config = json.loads(raw)
    devices = {g["binding"]["satellite_id"]: g["binding"]["device_id"] for g in config["grants"]}
    if len(config["grants"]) != 2 or devices != DEVICES:
        raise ValueError("Expected exactly the two reviewed device grants")
    if config["host"] != "10.0.20.46" or config["port"] != 44715 or config["eva_url"] != "http://127.0.0.1:44710":
        raise ValueError("Listener or core route differs from reviewed installation")
    if config["eva_token"] != (core_state / "household-voice/token").read_text().strip():
        raise ValueError("Household token changed; review current package binding")
    if config["ha_token"] != (core_state / "secrets/home-assistant-token").read_text().strip():
        raise ValueError("HA token changed; review current package binding")
    config.pop("ha_token")
    config.pop("eva_token")
    target.mkdir(parents=True, mode=0o700)
    private = target / "config"
    private.mkdir(mode=0o700)
    (private / "template.json").write_text(json.dumps(config, indent=2) + "\n")
    for name in ["tls.crt", "tls.key"]:
        shutil.copyfile(package / name, private / name)
    os.chown(private, 65532, 65532)
    for path in private.iterdir():
        path.chmod(0o600)
        os.chown(path, 65532, 65532)
    deploy = target / "deploy"
    deploy.mkdir(mode=0o700)
    source = Path(__file__).resolve().parent
    for name in ["launch.py", "compose.yaml", "activation.py", "core-flag.compose.json"]:
        shutil.copyfile(source / name, deploy / name)
        (deploy / name).chmod(0o444)
    deploy.chmod(0o555)
    (target / "compose.env").write_text(f"EVA_SPEAKER_STAGE={target}\nEVA_SPEAKER_CORE_STATE={core_state}\n")
    metadata = {"state": "staged_not_activated", "image": IMAGE, "core_state": str(core_state),
                "core_container": "eva2-managed-b-slot3-core", "devices": DEVICES,
                "sha256": {str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for folder in [private, deploy] for p in folder.iterdir()}}
    metadata["sha256"]["compose.env"] = hashlib.sha256((target / "compose.env").read_bytes()).hexdigest()
    (target / "stage.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps({"state": "staged_not_activated", "path": str(target)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["package", "target", "core-state"]:
        parser.add_argument("--" + name, type=Path, required=True)
    try:
        args = parser.parse_args()
        stage(args.package, args.target, args.core_state)
    except Exception:
        print('{"state":"failed","detail":"Private package or target is unavailable"}')
        raise SystemExit(1) from None
