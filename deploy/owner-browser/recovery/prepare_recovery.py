#!/usr/bin/env python3
"""Stage the two browser recovery units and root-owned firewall inputs; never install/start."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess


def private(path, *, directory=False):
    metadata = path.lstat()
    if (not path.is_absolute() or path.resolve(strict=True) != path
            or metadata.st_uid != 1000 or stat.S_IMODE(metadata.st_mode) != (0o700 if directory else 0o600)
            or not (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode))):
        raise RuntimeError("retained browser private state must remain owned by UID 1000")


def docker(*args):
    result = subprocess.run(["/usr/bin/docker", "--host", "unix:///var/run/docker.sock", *args],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True,
                            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "DOCKER_CONFIG": "/nonexistent"}, timeout=20)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-directory", type=Path, required=True)
    parser.add_argument("--host-source-directory", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or pwd.getpwnam("administrator").pw_uid != 1000:
        raise RuntimeError("root staging and the existing administrator UID 1000 are required")
    for path in (args.deployment_directory, args.host_source_directory, args.python, args.output_directory):
        if not path.is_absolute() or any(c.isspace() or c in '\"$%' for c in str(path)):
            raise RuntimeError("system unit paths must be absolute and unambiguous")
    if args.output_directory.exists():
        raise RuntimeError("use a new output directory; retained material is never overwritten")
    if not args.python.is_file() or not (args.host_source_directory / "src/eva/operations/browser_session.py").is_file():
        raise RuntimeError("existing host runtime source or Python is missing")
    config_path = args.deployment_directory / "host-config.json"
    private(config_path)
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw)
    if config.get("contract") != "EvaBrowserSessionHost/v1":
        raise RuntimeError("existing browser host contract differs")
    private(Path(config["state_directory"]), directory=True)
    for name in ("access_token_file", "viewer_password_file", "viewer_session_token_file", "viewer_master_token_file"):
        private(Path(config[name]))
    containers = {}
    records = {}
    for role in ("browser", "mcp"):
        identity = config[role + "_container_id"]
        if re.fullmatch(r"[0-9a-f]{64}", identity) is None:
            raise RuntimeError("full retained container identity required")
        rows = docker("inspect", identity)
        if len(rows) != 1 or rows[0]["Id"] != identity:
            raise RuntimeError("retained container identity differs")
        row = rows[0]
        containers[role] = {"id":identity, "image_reference":row["Config"]["Image"], "image_id":row["Image"]}
        records[role] = row
    if records["mcp"]["HostConfig"]["NetworkMode"] != "container:" + containers["browser"]["id"]:
        raise RuntimeError("MCP namespace differs from retained browser")
    networks = []
    for name, endpoint in records["browser"]["NetworkSettings"]["Networks"].items():
        rows = docker("network", "inspect", name)
        if len(rows) != 1 or rows[0]["Name"] != name or rows[0].get("EnableIPv6") is not False:
            raise RuntimeError("retained network or IPv6 policy differs")
        address = endpoint.get("IPAddress") or (endpoint.get("IPAMConfig") or {}).get("IPv4Address")
        networks.append({"name":name, "id":rows[0]["Id"], "address":address})
    profiles = [m for m in records["browser"]["Mounts"] if m.get("Destination") == "/config"]
    if len(profiles) != 1 or not profiles[0].get("RW"):
        raise RuntimeError("persistent browser profile binding differs")
    firewall = {"contract":"EvaOwnerBrowserFirewallRecovery/v1", "instance":args.deployment_directory.name,
                "containers":containers, "networks":sorted(networks,key=lambda n:n["address"] or ""),
                "profile_directory":profiles[0]["Source"]}
    # Import only the reviewed staging source once as operator, not at service runtime.
    import importlib.util
    root = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("firewall_recovery_stage", root / "recover_firewall.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.validate_binding(firewall)
    substitutions = {"HOST_SOURCE_DIR":str(args.host_source_directory), "EVA_PYTHON":str(args.python), "HOST_CONFIG":str(config_path)}
    host_unit = (root / "eva-owner-browser.service.template").read_text()
    for key, value in substitutions.items(): host_unit = host_unit.replace("${" + key + "}", value)
    if "${" in host_unit: raise RuntimeError("unresolved host unit input")
    material = {
        "recover_firewall.py": (root / "recover_firewall.py").read_bytes(),
        "firewall.json": (json.dumps(firewall, indent=2)+"\n").encode(),
        "eva-owner-browser.service":host_unit.encode(),
        "eva-owner-browser-firewall.service":(root / "eva-owner-browser-firewall.service.template").read_bytes(),
    }
    destinations = {"recover_firewall.py":"/usr/local/lib/eva-owner-browser/recover_firewall.py",
                    "firewall.json":"/etc/eva/owner-browser/firewall.json",
                    "eva-owner-browser.service":"/etc/systemd/system/eva-owner-browser.service",
                    "eva-owner-browser-firewall.service":"/etc/systemd/system/eva-owner-browser-firewall.service"}
    digests = {name:hashlib.sha256(raw).hexdigest() for name,raw in material.items()}
    material["recovery.sha256"] = "".join(digests[name]+"  "+destinations[name]+"\n" for name in sorted(destinations)).encode()
    destinations["recovery.sha256"] = "/etc/eva/owner-browser/recovery.sha256"
    if config_path.read_bytes() != config_raw:
        raise RuntimeError("host configuration changed during staging")
    args.output_directory.mkdir(mode=0o700)
    for name, raw in material.items():
        fd=os.open(args.output_directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"wb") as out: out.write(raw);out.flush();os.fsync(out.fileno())
    receipt = {"status":"staged_only_no_install_or_runtime_change", "destinations":destinations,
               "sha256":{name:hashlib.sha256(raw).hexdigest() for name,raw in material.items()},
               "hostUser":"administrator", "hostUid":1000, "existingConfig":str(config_path),
               "existingConfigSha256":hashlib.sha256(config_raw).hexdigest(),
               "existingState":config["state_directory"], "profileDirectory":firewall["profile_directory"],
               "containerIds":{role:row["id"] for role,row in containers.items()},
               "containerActions":[], "serviceActions":[]}
    p=args.output_directory/"receipt.json";p.write_text(json.dumps(receipt,indent=2)+"\n");p.chmod(0o600)
    print(json.dumps({"status":receipt["status"],"receipt":str(p)}))


if __name__ == "__main__":
    main()
