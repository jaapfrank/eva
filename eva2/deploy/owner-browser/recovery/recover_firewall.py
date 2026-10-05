#!/usr/bin/env python3
"""Restore the delivered browser firewall before its existing owner controller.

Only root-owned installed code/configuration is accepted. This helper never starts,
stops, execs into or recreates containers and never performs browser/MCP actions.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile

CHAINS = {"DOCKER-USER": "EVA2VB3F", "INPUT": "EVA2VB3I"}
PRIVATE = ("0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
           "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16",
           "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4")
IPV6_OBSERVATION = """import json;from pathlib import Path
p=Path('/proc/sys/net/ipv6/conf')
print(json.dumps({'disabled':{x.name:(x/'disable_ipv6').read_text().strip() for x in p.iterdir() if x.name not in {'all','default','lo'}},'addresses':Path('/proc/net/if_inet6').read_text(),'routes':Path('/proc/net/ipv6_route').read_text()}))
"""


def trusted_path(path: Path, *, directory=False):
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise RuntimeError("root-owned canonical deployment path is required")
    for item in (path, *path.parents):
        metadata = item.lstat()
        if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise RuntimeError("root service cannot use a user-writable deployment path")
    metadata = path.lstat()
    if not (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)):
        raise RuntimeError("deployment input type differs")


def run(argv, *, check=True):
    result = subprocess.run(argv, check=False, capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=20,
                            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "DOCKER_CONFIG": "/nonexistent"})
    if len(result.stdout) > 262144 or (check and result.returncode):
        raise RuntimeError("browser firewall command failed: " + argv[0])
    return result


def docker(*args):
    return json.loads(run(["/usr/bin/docker", "--host", "unix:///var/run/docker.sock", *args]).stdout)


def iptables(*args, check=True):
    return run(["/usr/sbin/iptables", "-w", "5", *args], check=check)


def validate_binding(config):
    if (set(config) != {"contract", "instance", "containers", "networks", "profile_directory"}
            or config["contract"] != "EvaOwnerBrowserFirewallRecovery/v1"
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", config["instance"]) is None
            or set(config["containers"]) != {"browser", "mcp"}
            or len(config["networks"]) != 2):
        raise RuntimeError("browser recovery binding is invalid")
    for pinned in config["containers"].values():
        if (set(pinned) != {"id", "image_reference", "image_id"}
                or re.fullmatch(r"[0-9a-f]{64}", pinned["id"]) is None
                or re.fullmatch(r"sha256:[0-9a-f]{64}", pinned["image_id"]) is None
                or re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", pinned["image_reference"]) is None):
            raise RuntimeError("browser recovery container identity is invalid")
    if config["containers"]["browser"]["id"] == config["containers"]["mcp"]["id"]:
        raise RuntimeError("browser pair must have distinct identities")
    names, addresses = set(), set()
    for network in config["networks"]:
        if (set(network) != {"name", "id", "address"}
                or re.fullmatch(r"[0-9a-f]{64}", network["id"]) is None
                or network["name"] not in {config["instance"] + "-net", config["instance"] + "-egress"}):
            raise RuntimeError("browser recovery network identity is invalid")
        address = ipaddress.IPv4Address(network["address"])
        if not address.is_private or address.is_loopback or address.is_link_local or str(address) != network["address"]:
            raise RuntimeError("browser recovery source address is invalid")
        names.add(network["name"]); addresses.add(str(address))
    if len(names) != 2 or len(addresses) != 2 or not Path(config["profile_directory"]).is_absolute():
        raise RuntimeError("browser recovery profile or source binding is invalid")


def observe_binding(config):
    validate_binding(config)
    records = {}
    for role, pinned in config["containers"].items():
        rows = docker("inspect", pinned["id"])
        if not isinstance(rows, list) or len(rows) != 1:
            raise RuntimeError("browser recovery container is absent")
        row = rows[0]
        if (row["Id"] != pinned["id"] or row["Image"] != pinned["image_id"]
                or row["Config"]["Image"] != pinned["image_reference"]
                or row["HostConfig"].get("RestartPolicy", {}).get("Name") not in {"", "no"}):
            raise RuntimeError("browser recovery container changed")
        records[role] = row
    browser, mcp = records["browser"], records["mcp"]
    if mcp["HostConfig"]["NetworkMode"] != "container:" + browser["Id"]:
        raise RuntimeError("MCP no longer shares the exact browser namespace")
    profiles = [m for m in browser["Mounts"] if m.get("Destination") == "/config"]
    if len(profiles) != 1 or profiles[0].get("Source") != config["profile_directory"] or not profiles[0].get("RW"):
        raise RuntimeError("browser persistent profile changed")
    attached = browser["NetworkSettings"]["Networks"]
    if set(attached) != {n["name"] for n in config["networks"]}:
        raise RuntimeError("browser network membership changed")
    for network in config["networks"]:
        rows = docker("network", "inspect", network["id"])
        if (len(rows) != 1 or rows[0]["Id"] != network["id"] or rows[0]["Name"] != network["name"]
                or rows[0].get("EnableIPv6") is not False):
            raise RuntimeError("browser network or IPv6 policy changed")
        endpoint = attached[network["name"]]
        retained = endpoint.get("IPAMConfig") or {}
        address = endpoint.get("IPAddress") or retained.get("IPv4Address")
        if address != network["address"]:
            raise RuntimeError("browser exact source address changed")
    ipv6 = {"status": "container_stopped_network_ipv6_disabled"}
    if browser["State"]["Running"]:
        pid = browser["State"]["Pid"]
        if type(pid) is not int or pid <= 1:
            raise RuntimeError("browser namespace process identity is unavailable")
        # Execute root-owned host Python in only the existing network namespace;
        # never execute code or an interpreter from the writable container.
        ipv6 = json.loads(run(["/usr/bin/nsenter", "--target", str(pid), "--net",
                              "/usr/bin/python3", "-I", "-c", IPV6_OBSERVATION]).stdout)
        if (not ipv6["disabled"] or any(v != "1" for v in ipv6["disabled"].values())
                or any(line.split()[-1] != "lo" for line in ipv6["addresses"].splitlines())
                or any(line.split()[-1] != "lo" for line in ipv6["routes"].splitlines())):
            raise RuntimeError("browser has an unexpected IPv6 route")
    return {"browser_running": browser["State"]["Running"], "mcp_running": mcp["State"]["Running"], "ipv6": ipv6}


def desired_rules(hook):
    result = [["-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "--ctdir", "REPLY", "-j", "RETURN"]]
    if hook == "DOCKER-USER":
        result.extend(["-d", net, "-j", "REJECT", "--reject-with", "icmp-port-unreachable"] for net in PRIVATE)
        result.append(["-j", "RETURN"])
    else:
        result.append(["-j", "REJECT", "--reject-with", "icmp-port-unreachable"])
    return result


def exact_hooks_reachable(hook_rules, wanted_hooks):
    """Require our exact hooks before any rule that could match our sources."""
    remaining = {tuple(rule) for rule in wanted_hooks}
    protected = {ipaddress.IPv4Network(rule[3]) for rule in wanted_hooks}
    hook = wanted_hooks[0][1]
    for rule in hook_rules:
        if tuple(rule) in remaining:
            remaining.remove(tuple(rule))
            if not remaining:
                return True
            continue
        # Only simple, positive, single-host source jumps are understood here.
        # Their targets cannot affect us because the source match excludes us.
        if rule[:3] != ["-A", hook, "-s"]:
            return False
        if len(rule) == 10 and rule[4:7] == ["-m", "comment", "--comment"] and rule[7]:
            jump = rule[8:]
        elif len(rule) == 6:
            jump = rule[4:]
        else:
            return False
        if jump[0] != "-j" or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,28}", jump[1]) is None:
            return False
        try:
            source = ipaddress.IPv4Network(rule[3])
        except ValueError:
            return False
        if source.prefixlen != 32 or str(source) != rule[3] or source in protected:
            return False
    return False


def restore_rules(config):
    # Rebuild a missing chain or finish our exact interrupted prefix. Never flush
    # unrelated rules or accept a same-named chain with different contents.
    for hook, chain in CHAINS.items():
        expected = [["-A", chain, *r] for r in desired_rules(hook)]
        current = iptables("-S", chain, check=False)
        if current.returncode:
            iptables("-N", chain)
            actual = []
        else:
            actual = [shlex.split(line) for line in current.stdout.splitlines() if line.startswith("-A ")]
        if actual != expected[:len(actual)]:
            raise RuntimeError("existing browser firewall chain differs")
        for rule in expected[len(actual):]:
            iptables(*rule)
        for network in config["networks"]:
            args = ["-s", network["address"] + "/32", "-m", "comment", "--comment", config["instance"], "-j", chain]
            if iptables("-C", hook, *args, check=False).returncode:
                iptables("-I", hook, "1", *args)
        observed = [shlex.split(line) for line in iptables("-S", chain).stdout.splitlines() if line.startswith("-A ")]
        hook_rules = [shlex.split(line) for line in iptables("-S", hook).stdout.splitlines() if line.startswith("-A ")]
        wanted_hooks = [["-A", hook, "-s", n["address"] + "/32", "-m", "comment", "--comment", config["instance"], "-j", chain] for n in config["networks"]]
        if observed != expected or not exact_hooks_reachable(hook_rules, wanted_hooks):
            raise RuntimeError("browser firewall readback differs")
    return {hook: iptables("-S", chain).stdout for hook, chain in CHAINS.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("browser firewall restoration requires root")
    trusted_path(Path(__file__))
    trusted_path(args.config)
    trusted_path(args.receipt.parent, directory=True)
    raw = args.config.read_bytes()
    config = json.loads(raw)
    observed = observe_binding(config)
    rules = restore_rules(config)
    receipt = {"contract": "EvaOwnerBrowserFirewallRecoveryReceipt/v1", "status": "restored",
               "at": datetime.now(UTC).isoformat(), "configuration_sha256": hashlib.sha256(raw).hexdigest(),
               "observed": observed, "rules": rules, "container_actions": []}
    fd, temporary = tempfile.mkstemp(prefix="firewall-", dir=args.receipt.parent)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(receipt, out, sort_keys=True); out.flush(); os.fsync(out.fileno())
        os.replace(temporary, args.receipt)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    print(json.dumps({"status": "restored", "receipt": str(args.receipt), "container_actions": []}))


if __name__ == "__main__":
    main()
