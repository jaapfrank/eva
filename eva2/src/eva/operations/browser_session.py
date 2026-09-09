"""Host-only lifecycle for one pre-provisioned, pinned browser/MCP pair.

Run with --config pointing to a private EvaBrowserSessionHost/v1 JSON file.
Provisioning, egress and MCP registration belong to deployment. This process
can only inspect/start/stop the two exact container IDs; it never creates,
reconfigures, execs into, pulls or removes containers. Do not run it in the core.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
import hashlib
from ipaddress import IPv4Address, IPv4Network
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

from eva.adapters.connectors.browser_session import (
    BrowserSessionConflict, BrowserSessionError, fixed_origin, read_browser_http,
)
from eva.adapters.credentials.runtime_files import read_private_runtime_file_bytes, read_runtime_secret_file


BROWSER_IMAGE = "lscr.io/linuxserver/chromium@sha256:63e8e38540462a38c70ef85497c13c412485b39979394ea95da450fca5b4efa8"
MCP_IMAGE = "mcr.microsoft.com/playwright/mcp@sha256:e3f6a85f0fd771dd9440584bd905011f1b4ea25b115bfea8b796d17d23e7b767"
_MODES = {"stopped", "starting", "eva", "taking_over", "owner", "resuming", "stopping", "recovery_required"}


@dataclass(frozen=True)
class BrowserContainers:
    browser_id: str
    mcp_id: str

    def __post_init__(self):
        if (not all(isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value)
                    for value in (self.browser_id, self.mcp_id))
                or self.browser_id == self.mcp_id):
            raise ValueError("two distinct full browser container IDs are required")

    @property
    def binding(self):
        return hashlib.sha256((self.browser_id + ":" + self.mcp_id).encode()).hexdigest()


class DockerBrowserContainers:
    def __init__(self, containers: BrowserContainers):
        self.containers = containers

    def _run(self, *args):
        try:
            result = subprocess.run(
                ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock", *args],
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "DOCKER_CONFIG": "/nonexistent"},
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BrowserSessionError("browser container command did not complete") from error
        if result.returncode or len(result.stdout) > 131072:
            raise BrowserSessionError("browser container command failed")
        return result.stdout

    def inspect(self):
        try:
            records = json.loads(self._run("inspect", self.containers.browser_id, self.containers.mcp_id))
            if not isinstance(records, list) or len(records) != 2:
                raise ValueError()
            browser, mcp = records
            for item, container_id, image in (
                (browser, self.containers.browser_id, BROWSER_IMAGE),
                (mcp, self.containers.mcp_id, MCP_IMAGE),
            ):
                config, host = item["Config"], item["HostConfig"]
                options = host.get("SecurityOpt") or []
                if (item["Id"] != container_id or config["Image"] != image
                        or host.get("Privileged") or host.get("CapAdd")
                        or not any(value in {"no-new-privileges", "no-new-privileges:true"} for value in options)
                        or "seccomp=unconfined" in options
                        or host.get("RestartPolicy", {}).get("Name") not in {"", "no"}
                        or not config.get("Healthcheck") or item["State"].get("Paused")
                        or item["State"].get("Restarting")):
                    raise ValueError()
            if (mcp["HostConfig"]["NetworkMode"] != "container:" + self.containers.browser_id
                    or not mcp["HostConfig"]["ReadonlyRootfs"]
                    or "ALL" not in (mcp["HostConfig"].get("CapDrop") or [])
                    or mcp["Config"]["User"] != "1000:1000"
                    or browser["HostConfig"]["NetworkMode"] in {"host", "bridge", "default"}
                    or not any(value.startswith("seccomp=") for value in browser["HostConfig"]["SecurityOpt"])):
                raise ValueError()
            commands = mcp["Config"].get("Cmd") or []
            endpoint_at = commands.index("--cdp-endpoint") if "--cdp-endpoint" in commands else -1
            if (mcp["Config"].get("Entrypoint") != ["node"]
                    or endpoint_at < 0 or commands[endpoint_at + 1:endpoint_at + 2] != ["http://127.0.0.1:9222"]
                    or "--shared-browser-context" not in commands or "--no-sandbox" in commands
                    or mcp.get("Mounts") or mcp["HostConfig"].get("PortBindings")):
                raise ValueError()
            for port, bindings in (browser["HostConfig"].get("PortBindings") or {}).items():
                if port != "3000/tcp" or any(item.get("HostIp") not in {"127.0.0.1", "::1"} for item in bindings or []):
                    raise ValueError()
            return {
                "browser_running": bool(browser["State"]["Running"]),
                "mcp_running": bool(mcp["State"]["Running"]),
                "browser_ready": browser["State"]["Running"] and browser["State"].get("Health", {}).get("Status") == "healthy",
                "mcp_ready": mcp["State"]["Running"] and mcp["State"].get("Health", {}).get("Status") == "healthy",
                "browser_started_at": browser["State"].get("StartedAt", ""),
                "mcp_started_at": mcp["State"].get("StartedAt", ""),
            }
        except (KeyError, TypeError, ValueError) as error:
            raise BrowserSessionError("browser container binding or isolation changed") from error

    def start(self, role):
        self._run("start", getattr(self.containers, role + "_id"))

    def stop(self, role):
        self._run("stop", "--time", "10", getattr(self.containers, role + "_id"))


class BrowserSessionHost:
    def __init__(self, *, containers: BrowserContainers, state_directory: Path,
                 driver=None, readiness_seconds=30, viewer_control=None):
        self.containers = containers
        self.driver = driver or DockerBrowserContainers(containers)
        self.readiness_seconds = readiness_seconds
        self.viewer_control = viewer_control
        directory = state_directory.lstat()
        if (not state_directory.is_absolute() or not stat.S_ISDIR(directory.st_mode)
                or stat.S_ISLNK(directory.st_mode) or directory.st_uid != os.geteuid()
                or stat.S_IMODE(directory.st_mode) != 0o700):
            raise ValueError("browser state directory must be private and host-owned")
        self.directory = state_directory
        self.state_file = state_directory / "state.json"
        self.lock_fd = os.open(state_directory / "manager.lock",
                               os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.lock_fd)
            raise BrowserSessionError("one browser manager already owns this session")
        self.gate = asyncio.Lock()
        try:
            if self.state_file.exists():
                self.state = json.loads(read_private_runtime_file_bytes(
                    str(self.state_file), setting_name="browser session state",
                ))
                if (self.state.get("binding") != containers.binding
                        or self.state.get("mode") not in _MODES
                        or type(self.state.get("revision")) is not int
                        or self.state["revision"] < 0):
                    raise ValueError("browser state does not bind this container pair")
            else:
                observed = self.driver.inspect()
                self.state = {"binding": containers.binding, "revision": 0,
                              "mode": "recovery_required" if observed["browser_running"] or observed["mcp_running"] else "stopped",
                              "browser_started_at": "", "mcp_started_at": ""}
                self._save()
        except Exception:
            self.close()
            raise

    def close(self):
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def _save(self):
        descriptor, temporary = tempfile.mkstemp(prefix="state-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(self.state, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.state_file)
            directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _transition(self, mode, observed=None):
        self.state.update(mode=mode, revision=self.state["revision"] + 1,
                          changed_at=datetime.now(UTC).isoformat())
        if observed is not None:
            self.state.update({key: observed[key] for key in ("browser_started_at", "mcp_started_at")})
        self._save()

    def _status(self):
        observed = self.driver.inspect()
        mode = self.state["mode"]
        matches = (
            mode == "stopped" and not observed["browser_running"] and not observed["mcp_running"]
            or mode == "eva" and observed["browser_ready"] and observed["mcp_ready"]
            and all(observed[key] == self.state.get(key) for key in ("browser_started_at", "mcp_started_at"))
            or mode == "owner" and observed["browser_ready"] and not observed["mcp_running"]
            and observed["browser_started_at"] == self.state.get("browser_started_at")
        )
        state = mode if matches else "recovery_required"
        return {"contract": "EvaBrowserSession/v1", "revision": self.state["revision"],
                "state": state, "owner_control": state == "owner", "eva_control": state == "eva",
                "browser_ready": observed["browser_ready"], "mcp_ready": observed["mcp_ready"],
                "profile_preserved": True, "fresh_observation_required": state != "stopped",
                "viewer_path": "/internal/v1/dashboard/browser/view/" if state == "owner" else None}

    async def status(self):
        async with self.gate:
            return await asyncio.to_thread(self._status)

    def _wait_ready(self, role):
        deadline = time.monotonic() + self.readiness_seconds
        while True:
            observed = self.driver.inspect()
            if observed[role + "_ready"]:
                return observed
            if not observed[role + "_running"] or time.monotonic() >= deadline:
                raise BrowserSessionError("browser did not become ready")
            time.sleep(0.25)

    def _stop(self, role):
        if self.driver.inspect()[role + "_running"]:
            self.driver.stop(role)
        if self.driver.inspect()[role + "_running"]:
            raise BrowserSessionError("browser control did not stop")

    def _control(self, action, revision):
        current = self._status()
        if revision != current["revision"]:
            raise BrowserSessionConflict("browser revision changed")
        allowed = {"start": {"stopped"}, "takeover": {"eva", "recovery_required"},
                   "resume": {"owner"}, "stop": {"stopped", "eva", "owner", "recovery_required"}}
        if action not in allowed or current["state"] not in allowed[action]:
            raise BrowserSessionConflict("browser transition is not available")
        try:
            if action == "stop":
                self._transition("stopping")
                self._stop("mcp")
                self._stop("browser")
                self._transition("stopped", self.driver.inspect())
            elif action == "takeover":
                if not current["browser_ready"]:
                    raise BrowserSessionConflict("browser is unavailable for takeover")
                self._transition("taking_over")
                self._stop("mcp")
                if self.viewer_control is not None:
                    self.viewer_control(True)
                self._transition("owner", self.driver.inspect())
            else:
                self._transition("starting" if action == "start" else "resuming")
                if action == "start":
                    self.driver.start("browser")
                self._wait_ready("browser")
                if self.viewer_control is not None:
                    # Native Selkies also rejects browser-local websocket access;
                    # revoke its controller token before automation can resume.
                    self.viewer_control(False)
                self.driver.start("mcp")
                self._transition("eva", self._wait_ready("mcp"))
            result = self._status()
            if result["state"] == "recovery_required":
                raise BrowserSessionError("browser state changed during control")
            return result
        except BrowserSessionConflict:
            raise
        except Exception as error:
            # A timeout is an uncertain result, never permission to repeat a start
            # or browser action. Recovery only permits takeover or explicit stop.
            self._transition("recovery_required")
            raise BrowserSessionError("browser transition requires observation") from error

    async def control(self, action, revision):
        async with self.gate:
            # A disconnected owner HTTP request must not leave a Docker operation
            # running outside this lock, or overlap a subsequent transition.
            work = asyncio.create_task(asyncio.to_thread(self._control, action, revision))
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                await work
                raise


def main():
    from fastapi import FastAPI
    import uvicorn
    from eva.adapters.api.browser_session import BrowserSession, install_browser_session_routes
    from eva.adapters.api.dashboard_access import DashboardAccess

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(read_private_runtime_file_bytes(args.config, setting_name="browser host configuration"))
    fields = {"contract", "browser_container_id", "mcp_container_id", "state_directory",
              "viewer_origin", "owner_origin", "listen_host", "listen_port", "access_token_file",
              "viewer_password_file", "viewer_session_token_file", "viewer_master_token_file", "viewer_control_origin"}
    listen_address = IPv4Address(config.get("listen_host", "0.0.0.0"))
    admitted_bind = str(listen_address) == "127.0.0.1" or any(
        listen_address in IPv4Network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    )
    if (set(config) != fields or config["contract"] != "EvaBrowserSessionHost/v1"
            or not admitted_bind
            or type(config["listen_port"]) is not int or not 1024 <= config["listen_port"] <= 65535):
        raise ValueError("invalid browser host configuration")
    viewer_password = read_runtime_secret_file(config["viewer_password_file"], setting_name="browser viewer password")
    viewer_session_token = read_runtime_secret_file(config["viewer_session_token_file"], setting_name="browser viewer session")
    viewer_master_token = read_runtime_secret_file(config["viewer_master_token_file"], setting_name="browser viewer master")
    viewer_control_origin = fixed_origin(config["viewer_control_origin"])

    def viewer_control(enabled):
        payload = {viewer_session_token: {"role": "controller", "mk_control": True}} if enabled else {}
        status, _headers, _content = read_browser_http(
            viewer_control_origin + "/tokens", token=viewer_master_token,
            payload=payload, timeout=5,
        )
        if status != 200:
            raise BrowserSessionError("native browser viewer authorization failed")

    host = BrowserSessionHost(containers=BrowserContainers(config["browser_container_id"], config["mcp_container_id"]),
                              state_directory=Path(config["state_directory"]), viewer_control=viewer_control)
    token = read_runtime_secret_file(config["access_token_file"], setting_name="browser host access")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_browser_session_routes(app, access=DashboardAccess(token), session=BrowserSession(
        backend=host, allowed_origin=config["owner_origin"], viewer_origin=config["viewer_origin"],
        upstream_basic_authorization="Basic " + base64.b64encode(("eva-browser-owner:" + viewer_password).encode()).decode(),
        upstream_websocket_token=viewer_session_token,
    ))
    try:
        uvicorn.run(app, host=config["listen_host"], port=config["listen_port"], workers=1,
                    access_log=True, proxy_headers=False)
    finally:
        host.close()


if __name__ == "__main__":
    main()
