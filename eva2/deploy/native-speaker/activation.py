"""Capture a fresh baseline; start/stop only this speaker after core coordination.

Core commands are emitted as argv in a private file for the coordinator's existing
quiescent release procedure. This helper never edits/restarts core or worker.
"""
import argparse
from datetime import datetime, UTC
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

SPEAKER = "eva2-household-speaker-1"


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.PIPE)


def inspect(name):
    return json.loads(docker("inspect", name))[0]


def environment(container):
    return dict(row.split("=", 1) for row in container["Config"]["Env"])


def verify_stage(stage):
    data = json.loads((stage / "stage.json").read_text())
    for name, expected in data["sha256"].items():
        if hashlib.sha256((stage / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Prepared stage changed")
    return data


def compose(stage):
    return ["docker", "compose", "--env-file", str(stage / "compose.env"),
            "-f", str(stage / "deploy/compose.yaml")]


def capture(stage):
    data = verify_stage(stage)
    # The reviewed baseline is no installed continuous speaker. Do not adopt an
    # unrelated or partially installed container automatically.
    if docker("ps", "-aq", "--filter", "name=^/" + SPEAKER + "$").strip():
        raise ValueError("Speaker already exists; inspect before new activation")
    core = inspect(data["core_container"])
    if not core["State"]["Running"]:
        raise ValueError("Current core is not running")
    policy_mount = next(m for m in core["Mounts"] if m["Destination"] == "/run/eva/household-voice")
    if policy_mount["Source"] != data["core_state"] + "/household-voice":
        raise ValueError("Core state binding changed; restage explicitly")
    identity_mount = next(m for m in core["Mounts"] if m["Destination"] == "/run/eva/identity/current.json")
    if identity_mount["Source"] != data["core_state"] + "/identity/current.json":
        raise ValueError("Core identity binding changed; restage explicitly")
    labels = core["Config"]["Labels"]
    project = labels["com.docker.compose.project"]
    service = labels["com.docker.compose.service"]
    if service != "core":
        raise ValueError("Expected the existing core Compose service")
    root = stage / "activations"
    root.mkdir(exist_ok=True, mode=0o700)
    run = root / datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run.mkdir(mode=0o700)
    files = labels["com.docker.compose.project.config_files"].split(",")
    envfile = labels["com.docker.compose.project.environment_file"]
    if not envfile or "," in envfile:
        raise ValueError("Ambiguous core environment file")
    hashes = {}
    backups = []
    for index, name in enumerate([envfile, *files]):
        path = Path(name)
        raw = path.read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        backup = run / f"baseline-{index}{path.suffix}"
        backup.write_bytes(raw)
        backups.append(str(backup))
    original_flag = environment(core).get("EVA_HOUSEHOLD_VOICE_SESSION_ENABLED", "false")
    for name, flag in [("enable", "true"), ("restore", original_flag)]:
        (run / f"core-{name}.json").write_text(json.dumps({"services": {service: {
            "image": core["Image"], "environment": {"EVA_HOUSEHOLD_VOICE_SESSION_ENABLED": flag}}}}))
    prefix = ["docker", "compose", "--project-name", project, "--project-directory",
              labels["com.docker.compose.project.working_dir"], "--env-file", backups[0]]
    for name in backups[1:]:
        prefix.extend(["-f", name])
    commands = {key: prefix + ["-f", str(run / f"core-{key}.json"), "up", "-d", "--no-deps",
                              "--pull", "never", service] for key in ["enable", "restore"]}
    # A pending/uninstalled edit in a Compose input is not a faithful rollback.
    rendered = json.loads(subprocess.check_output(
        prefix + ["-f", str(run / "core-restore.json"), "config", "--format", "json"],
        text=True, stderr=subprocess.PIPE))["services"][service]
    image_env = json.loads(docker("image", "inspect", core["Image"]))[0]["Config"].get("Env", [])
    projected = dict(row.split("=", 1) for row in image_env)
    for key, value in rendered.get("environment", {}).items():
        if value is None: projected.pop(key, None)
        else: projected[key] = str(value)
    actual = environment(core)
    actual.setdefault("EVA_HOUSEHOLD_VOICE_SESSION_ENABLED", "false")
    if projected != actual:
        raise ValueError("Current Compose inputs do not reproduce the running core environment")
    # The coordinator validates this full projection against the running core;
    # environment equality does not prove equivalent mounts/security/ports.
    (run / "core-restore.rendered.json").write_text(json.dumps(rendered, indent=2) + "\n")
    baseline = {"core": core, "source_hashes": hashes, "speaker_existed": False,
                "protected": {name: inspect(name)["Id"] for name in
                              ["eva2-owner-voice-media-1", "eva2-owner-voice-livekit-1"]}}
    (run / "baseline.json").write_text(json.dumps(baseline))
    (run / "core-commands.json").write_text(json.dumps(commands, indent=2) + "\n")
    print(json.dumps({"state": "baseline_captured", "baseline": str(run), "core_commands_executed": False}))


def check(stage, run):
    data = verify_stage(stage)
    if not run.resolve().is_relative_to((stage / "activations").resolve()):
        raise ValueError("Baseline must belong to this stage")
    baseline = json.loads((run / "baseline.json").read_text())
    core = inspect(data["core_container"])
    if not core["State"]["Running"] or core["Image"] != baseline["core"]["Image"]:
        raise ValueError("Core changed after baseline; coordinate a fresh activation")
    before, now = environment(baseline["core"]), environment(core)
    for values in [before, now]:
        values.pop("EVA_HOUSEHOLD_VOICE_SESSION_ENABLED", None)
    if before != now:
        raise ValueError("Unrelated core environment changed")
    def mounts(value):
        return sorted((m["Source"], m["Destination"], m["RW"]) for m in value["Mounts"])
    if mounts(core) != mounts(baseline["core"]):
        raise ValueError("Core mount bindings changed")
    for name, expected in baseline["source_hashes"].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
            raise ValueError("Core release inputs changed after baseline")
    for name, expected in baseline["protected"].items():
        if inspect(name)["Id"] != expected:
            raise ValueError("Owner voice changed after baseline")
    return data, core


def start(stage, run):
    data, core = check(stage, run)
    if environment(core).get("EVA_HOUSEHOLD_VOICE_SESSION_ENABLED") != "true":
        raise ValueError("Coordinator must enable the existing core flag first")
    token = (Path(data["core_state"]) / "household-voice/token").read_text().strip()
    for satellite, device in data["devices"].items():
        request = urllib.request.Request("http://127.0.0.1:44710/internal/v1/ha-voice/session/ready",
            data=json.dumps({"satellite_id": satellite, "device_id": device}).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise ValueError("Household core not ready")
    if docker("ps", "-aq", "--filter", "name=^/" + SPEAKER + "$").strip():
        raise ValueError("Speaker already exists; no implicit replacement")
    try:
        subprocess.run(compose(stage) + ["up", "-d", "--no-deps", "--pull", "never", "speaker"],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(30):
            current = inspect(SPEAKER)
            if current["Image"] != data["image"] or current["Config"]["User"] != "65532:65532":
                raise ValueError("Speaker runtime differs from reviewed image/user")
            if current["State"]["Running"] and '"state":"listening"' in docker("logs", SPEAKER):
                check(stage, run)
                print('{"state":"speaker_started","firmware_changed":false}')
                return
            time.sleep(1)
        raise ValueError("Speaker did not reach listening state")
    except Exception:
        stop(stage)
        raise


def stop(stage):
    data = verify_stage(stage)
    if not docker("ps", "-aq", "--filter", "name=^/" + SPEAKER + "$").strip():
        print('{"state":"speaker_absent"}')
        return
    current = inspect(SPEAKER)
    if (current["Image"] != data["image"] or
            current["Config"]["Labels"].get("com.docker.compose.project") != "eva2-household-speaker" or
            current["Config"]["Labels"].get("com.docker.compose.project.config_files") != str(stage / "deploy/compose.yaml")):
        raise ValueError("Refusing to remove a different speaker deployment")
    subprocess.run(compose(stage) + ["stop", "speaker"], check=True, capture_output=True)
    subprocess.run(compose(stage) + ["rm", "-f", "speaker"], check=True, capture_output=True)
    print('{"state":"speaker_removed","core_and_firewall_unchanged":true}')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["capture", "check", "start", "stop"])
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    try:
        os.umask(0o077)
        args = parser.parse_args()
        if os.geteuid() != 0:
            raise ValueError("Activation requires the existing root management route")
        if args.action == "capture": capture(args.stage)
        elif args.action == "stop": stop(args.stage)
        elif args.action == "start": start(args.stage, args.baseline)
        else:
            check(args.stage, args.baseline)
            print('{"state":"baseline_current"}')
    except Exception:
        print('{"state":"failed","detail":"Activation precondition failed; inspect the private baseline"}')
        raise SystemExit(1) from None
