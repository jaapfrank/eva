"""Prepare a new isolated browser from the retained, pinned owner-browser recipe."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
import zipfile
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


def _write(path, value, mode=0o600):
    data = json.dumps(value, indent=2).encode() if not isinstance(value, bytes) else value
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _save_phase(root, state):
    temporary = root / "phase-next.json"
    _write(temporary, state)
    os.replace(temporary, root / "phase.json")


def _render(value, replacements):
    if isinstance(value, str):
        for key, replacement in replacements.items():
            value = value.replace(key, str(replacement))
        if "${" in value:
            raise ValueError("unresolved browser recipe input")
        return value
    if isinstance(value, list):
        return [_render(item, replacements) for item in value]
    return {key: _render(item, replacements) for key, item in value.items()}


def native_browser_wheel(package_root, wheel_path=None):
    """Verify the exact compatible offline dependency; version labels are insufficient."""
    recipe = Path(package_root) / "deploy/owner-browser"
    pin = json.loads((recipe / "pixelflux-idle-source.json").read_bytes())
    wheel = Path(wheel_path) if wheel_path is not None else recipe / pin["wheel"]["filename"]
    if (pin["contract"] != "EvaOwnerBrowserNativeOverlay/v1"
            or wheel.name != pin["wheel"]["filename"]
            or hashlib.sha256(wheel.read_bytes()).hexdigest() != pin["wheel"]["sha256"]):
        raise ValueError("exact pinned browser native wheel required")
    return pin, wheel


def _install_native_overlay(root, browser_id, pin, wheel):
    """Copy the verified wheel into the stopped new browser; retain original bytes."""
    target = pin["installedTarget"]
    previous = root / "native-original"
    previous.mkdir(mode=0o700)
    for name in pin["original"]["preserve"]:
        _run(["docker", "cp", browser_id + ":" + target["sitePackages"] + "/" + name, str(previous / name)])
    if hashlib.sha256((previous / target["extension"]).read_bytes()).hexdigest() != pin["original"]["extensionSha256"]:
        raise ValueError("browser original native extension differs")
    extracted = root / "native-overlay"
    extracted.mkdir(mode=0o755)
    extracted.chmod(0o755)
    with zipfile.ZipFile(wheel) as archive:
        for entry in archive.infolist():
            relative = Path(entry.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("browser native wheel entry escapes target")
            output = extracted / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            for parent in output.parents:
                if parent == root:
                    break
                parent.chmod(0o755)
            if entry.is_dir():
                output.mkdir(exist_ok=True)
                output.chmod(0o755)
            else:
                _write(output, archive.read(entry), 0o444)
    if hashlib.sha256((extracted / target["extension"]).read_bytes()).hexdigest() != target["extensionSha256"]:
        raise ValueError("browser native extension differs from pin")
    _run(["docker", "cp", str(extracted) + "/.", browser_id + ":" + target["sitePackages"]])


def prepare_browser_module(*, package_root: Path, workspace: Path, namespace: str,
                           owner_origin: str, internal_subnet: str, egress_subnet: str,
                           control_port: int, viewer_port: int, python_executable: Path) -> dict:
    """Write only new private installation files; never inspect or mutate Docker."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,47}", namespace):
        raise ValueError("invalid browser namespace")
    package_root, workspace = Path(package_root).resolve(), Path(workspace).resolve()
    python_executable = Path(os.path.abspath(python_executable))
    if not python_executable.is_file():
        raise ValueError("packaged Python is absent")
    parsed = urlsplit(owner_origin)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("owner origin must be an exact HTTP origin")
    networks = [ipaddress.IPv4Network(value, strict=True) for value in (internal_subnet, egress_subnet)]
    private = [ipaddress.IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    if any(net.prefixlen != 24 or not any(net.subnet_of(allowed) for allowed in private) for net in networks) or networks[0].overlaps(networks[1]):
        raise ValueError("browser needs two distinct private /24 networks")
    if any(type(port) is not int or not 1024 <= port <= 65535 for port in (control_port, viewer_port)) or control_port == viewer_port:
        raise ValueError("browser ports must be distinct unprivileged ports")
    recipe = package_root / "deploy/owner-browser"
    provenance = json.loads((recipe / "provenance.json").read_text())
    # Pins are release material, not operator-selected image tags.
    for name, record in provenance["files"].items():
        if hashlib.sha256((recipe / name).read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("browser recipe hash differs: " + name)
    images = [provenance["upstream"][name]["image"] for name in ("chromium", "playwright_mcp")]
    if any(not re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", image) for image in images):
        raise ValueError("browser image must be pinned")
    native_pin, native_wheel = native_browser_wheel(package_root)
    if native_pin["baseImage"] != images[0]:
        raise ValueError("browser native overlay base differs")
    root = workspace / "browser" / namespace
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)  # Refuse existing state; no silent reuse or replacement.
    for name in ("profile", "state", "secrets"):
        (root / name).mkdir(mode=0o700)
    for name in ("control-token", "viewer-password", "viewer-master-token", "viewer-session-token"):
        _write(root / "secrets" / name, secrets.token_urlsafe(48).encode())
    suffix = hashlib.sha256(namespace.encode()).hexdigest()[:10].upper()
    replacements = {"192.168.0.0/20": str(networks[0]), "192.168.0.1": str(networks[0][1]),
        "192.168.0.2": str(networks[0][2]), "192.168.16.0/20": str(networks[1]),
        "192.168.16.1": str(networks[1][1]), "192.168.16.2": str(networks[1][2]),
        "172.30.0.1": str(networks[0][1]), "EVA2VB3F": "EVA" + suffix + "F",
        "EVA2VB3I": "EVA" + suffix + "I"}
    copied = {}
    for name in provenance["files"]:
        data = (recipe / name).read_bytes()
        if name in {"configure-egress.py", "browser-firewall.py"}:
            data = _render(data.decode(), replacements).encode()
        _write(root / name, data, 0o755 if name in {"autostart", "selkies-run"} else 0o444)
        copied[name] = hashlib.sha256(data).hexdigest()
    values = {**replacements, "${INSTANCE}": namespace, "${DEPLOYMENT_DIR}": str(root),
              "${OWNER_ORIGIN}": owner_origin, "${CONTROL_PORT}": str(control_port),
              "${VIEWER_PORT}": str(viewer_port)}
    plan = {"contract": "EvaInstallerBrowserModule/v1", "namespace": namespace,
        "deployment_dir": str(root), "workspace": str(workspace), "package_root": str(package_root),
        "python_executable": str(python_executable), "operator_uid": os.geteuid(),
        "internal_subnet": str(networks[0]), "egress_subnet": str(networks[1]),
        "internal_network": namespace + "-net", "control_url": f"http://{networks[0][1]}:{control_port}",
        "control_token_file": str(root / "secrets/control-token"),
        "mcp_url": f"http://{networks[0][2]}:8931/mcp", "images": images,
        "owner_origin": owner_origin, "render_values": values, "recipe_hashes": copied,
        "overlay": provenance["selkiesOverlay"], "native_overlay": native_pin,
        "native_wheel": str(native_wheel), "host_reboot_recovery_verified": False}
    _write(root / "plan.json", plan)
    _save_phase(root, {"phase": "prepared"})
    return plan


def _run(argv, *, input=None):
    return subprocess.run(argv, input=input, capture_output=True, check=True).stdout.decode().strip()


def _control(plan, path, payload=None):
    token = Path(plan["control_token_file"]).read_text().strip()
    request = Request(plan["control_url"] + "/internal/v1/dashboard/browser/" + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + token, "Origin": plan["owner_origin"],
                 "Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(request, timeout=100) as response:
        return json.load(response)


def start_browser_module(plan: dict) -> dict:
    """Provision this new instance, retain phases on failure, use the existing controller."""
    root = Path(plan["deployment_dir"])
    if os.geteuid() != 0 or plan["operator_uid"] != os.geteuid():
        raise ValueError("browser provisioning requires the preparing root operator")
    if json.loads((root / "plan.json").read_text()) != plan:
        raise ValueError("browser preparation changed")
    state = json.loads((root / "phase.json").read_text())
    if state != {"phase": "prepared"}:
        raise ValueError("browser already attempted; preserve and inspect the existing phase")
    for name, expected in plan["recipe_hashes"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("prepared browser material changed")
    native_pin, native_wheel = native_browser_wheel(plan["package_root"], plan["native_wheel"])
    if native_pin != plan["native_overlay"]:
        raise ValueError("prepared native overlay changed")
    try:
        for image in plan["images"]:
            _run(["docker", "image", "inspect", image])
        existing = _run(["docker", "network", "ls", "-q"]).split()
        if existing:
            for network in json.loads(_run(["docker", "network", "inspect", *existing])):
                for ipam in network.get("IPAM", {}).get("Config", []) or []:
                    subnet = ipam.get("Subnet")
                    if subnet and ":" not in subnet and any(ipaddress.ip_network(subnet).overlaps(ipaddress.ip_network(plan[key])) for key in ("internal_subnet", "egress_subnet")):
                        raise ValueError("assigned browser network is already used")
        state.update(phase="creating_network")
        _save_phase(root, state)
        state["internal_network_id"] = _run(["docker", "network", "create", "--internal", "--subnet", plan["internal_subnet"],
            "--gateway", str(ipaddress.ip_network(plan["internal_subnet"])[1]), "--label", "io.eva2.browser-candidate=" + plan["namespace"], plan["internal_network"]])
        _save_phase(root, state)
        os.chown(root / "profile", 1000, 1000)
        _run(["python3", str(root / "provision-master.py")])
        password_hash = _run(["openssl", "passwd", "-apr1", "-stdin"], input=(root / "secrets/viewer-password").read_bytes() + b"\n")
        _write(root / "viewer.htpasswd", ("eva-browser-owner:" + password_hash + "\n").encode(), 0o444)
        values = dict(plan["render_values"])
        browser_argv = _render(json.loads((root / "browser-create-argv.template.json").read_text()), values)
        state["browser"] = _run(browser_argv)
        if not re.fullmatch(r"[a-f0-9]{64}", state["browser"]):
            raise ValueError("browser create did not return a full ID")
        state["phase"] = "browser_created"
        _save_phase(root, state)
        _install_native_overlay(root, state["browser"], native_pin, native_wheel)
        state["native_overlay_sha256"] = native_pin["wheel"]["sha256"]
        _save_phase(root, state)
        overlay = plan["overlay"]
        upstream, patched = root / "selkies-upstream.py", root / "selkies-auth-patched.py"
        _run(["docker", "cp", state["browser"] + ":" + overlay["path"], str(upstream)])
        if hashlib.sha256(upstream.read_bytes()).hexdigest() != overlay["upstream_sha256"]:
            raise ValueError("browser upstream source differs")
        _run(["patch", "--output", str(patched), str(upstream), str(root / "selkies-auth.patch")])
        if hashlib.sha256(patched.read_bytes()).hexdigest() != overlay["patched_sha256"]:
            raise ValueError("browser patch output differs")
        patched.chmod(0o444)
        _run(["docker", "cp", str(patched), state["browser"] + ":" + overlay["path"]])
        values["${BROWSER_CONTAINER_ID}"] = state["browser"]
        state["mcp"] = _run(_render(json.loads((root / "mcp-create-argv.template.json").read_text()), values))
        if not re.fullmatch(r"[a-f0-9]{64}", state["mcp"]):
            raise ValueError("MCP create did not return a full ID")
        _write(root / "container-ids.json", {key: state[key] for key in ("browser", "mcp")})
        state["phase"] = "pair_created"
        _save_phase(root, state)
        _run(["docker", "start", state["browser"]])
        _run(["python3", str(root / "configure-egress.py")])
        _run(["python3", str(root / "browser-firewall.py"), "apply"])
        _run(["docker", "stop", state["browser"]])
        state["phase"] = "protected_stopped"
        _save_phase(root, state)
        values["${MCP_CONTAINER_ID}"] = state["mcp"]
        config = _render(json.loads((root / "host-config.template.json").read_text()), values)
        config["listen_port"] = int(config["listen_port"])
        _write(root / "host-config.json", config)
        argv = [plan["python_executable"], "-m", "eva.operations.browser_session", "--config", str(root / "host-config.json")]
        environment = dict(os.environ, PYTHONPATH=str(Path(plan["package_root"]) / "src"))
        with (root / "controller.log").open("xb") as log:
            os.chmod(root / "controller.log", 0o600)
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       env=environment, start_new_session=True)
        state.update(phase="controller_started", controller_pid=process.pid, controller_argv=argv,
                     controller_start_ticks=Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
        _save_phase(root, state)
        deadline = time.monotonic() + 20
        while True:
            if process.poll() is not None:
                raise RuntimeError("browser controller exited; inspect private log")
            try:
                status = _control(plan, "status")
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("browser controller status unavailable") from None
                time.sleep(.2)
        if status["state"] != "stopped":
            raise RuntimeError("fresh browser controller is not stopped")
        status = _control(plan, "control", {"action": "start", "revision": status["revision"]})
        if status["state"] != "eva":
            raise RuntimeError("browser pair did not reach verified Eva control")
        state.update(phase="ready", control_status=status)
        _save_phase(root, state)
        return {**plan, **state, "network_id": state["internal_network_id"],
                "browser_id": state["browser"], "mcp_id": state["mcp"], "state": "eva"}
    except Exception as error:
        state["error_type"] = type(error).__name__
        _save_phase(root, state)
        raise
