"""Render process-private credentials, then exec the existing speaker launcher."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from speaker_service import ServiceConfig, private_bytes, server_tls
from speaker_livekit_service import load_media_authority


def prepare(root=Path("/run/speaker-config"), authority=Path("/run/household-voice"),
            identity=Path("/run/identity/current.json"), ha_token=Path("/run/ha-token"),
            runtime=Path("/run/eva-speaker-runtime")):
    if os.geteuid() != 65532 or runtime.stat().st_uid != 65532 or runtime.stat().st_mode & 0o077:
        raise ValueError("Expected the private UID 65532 runtime")
    config = json.loads(private_bytes(root / "template.json"))
    if "ha_token" in config or "eva_token" in config:
        raise ValueError("Persistent template must not contain account credentials")
    config["ha_token"] = private_bytes(ha_token).decode().strip()
    config["eva_token"] = private_bytes(authority / "token").decode().strip()
    config["certificate"] = str(root / "tls.crt")
    config["private_key"] = str(root / "tls.key")
    os.umask(0o077)
    target = runtime / "config.json"
    descriptor, name = tempfile.mkstemp(prefix="config-", suffix=".json", dir=runtime)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(config, stream)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    loaded = ServiceConfig.load(target)
    current = load_media_authority(policy_file=authority / "current.json",
                                  token_file=authority / "token", identity_file=identity)
    if any(current.policy.devices.get(g.binding.satellite_id) != g.binding.device_id
           for g in loaded.grants):
        raise ValueError("Device grants differ from current household authority")
    server_tls(loaded)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    try:
        args = parser.parse_args()
        config = prepare()
        if args.check_only:
            print('{"state":"validated","uid":65532,"network_calls":0}')
        else:
            os.execv(sys.executable, [sys.executable, "/app/speaker/speaker_livekit_service.py",
                     "--config", str(config), "--policy", "/run/household-voice/current.json",
                     "--household-token", "/run/household-voice/token",
                     "--identity", "/run/identity/current.json"])
    except Exception:
        print('{"state":"failed","detail":"Speaker configuration or current authority unavailable"}',
              file=sys.stderr)
        raise SystemExit(1) from None
