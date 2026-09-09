import asyncio
import json
import pytest

from eva.adapters.connectors.browser_session import BrowserSessionConflict, BrowserSessionError
from eva.operations.browser_session import (
    BROWSER_IMAGE, MCP_IMAGE, BrowserContainers, BrowserSessionHost, DockerBrowserContainers,
)


class Containers:
    def __init__(self):
        self.running = {"browser": False, "mcp": False}
        self.started = {"browser": "", "mcp": ""}
        self.calls = []
        self.fail_stop = False

    def inspect(self):
        return {**{role + "_running": value for role, value in self.running.items()},
                **{role + "_ready": value for role, value in self.running.items()},
                **{role + "_started_at": value for role, value in self.started.items()}}

    def start(self, role):
        self.calls.append(("start", role))
        self.running[role] = True
        self.started[role] = str(len(self.calls))

    def stop(self, role):
        self.calls.append(("stop", role))
        if not self.fail_stop:
            self.running[role] = False


def test_same_browser_survives_takeover_and_unknown_mcp_stop_never_grants_owner(tmp_path):
    tmp_path.chmod(0o700)
    driver = Containers()
    pair = BrowserContainers("a" * 64, "b" * 64)
    host = BrowserSessionHost(containers=pair, state_directory=tmp_path, driver=driver)

    async def exercise():
        initial = await host.status()
        active = await host.control("start", initial["revision"])
        browser_start = driver.started["browser"]
        with pytest.raises(BrowserSessionConflict):
            await host.control("takeover", initial["revision"])
        owner = await host.control("takeover", active["revision"])
        assert owner["owner_control"] and not driver.running["mcp"]
        assert driver.started["browser"] == browser_start
        resumed = await host.control("resume", owner["revision"])
        assert resumed["eva_control"] and resumed["fresh_observation_required"]
        assert driver.started["browser"] == browser_start
        driver.fail_stop = True
        with pytest.raises(BrowserSessionError):
            await host.control("takeover", resumed["revision"])
        failed = await host.status()
        assert failed["state"] == "recovery_required" and not failed["owner_control"]
        assert driver.running["mcp"]

    try:
        asyncio.run(exercise())
    finally:
        host.close()
    # A manager restart retains uncertainty and doesn't replay the failed command.
    calls = driver.calls[:]
    host = BrowserSessionHost(containers=pair, state_directory=tmp_path, driver=driver)
    try:
        assert asyncio.run(host.status())["state"] == "recovery_required"
        assert driver.calls == calls
        with pytest.raises(BrowserSessionError):
            BrowserSessionHost(containers=pair, state_directory=tmp_path, driver=driver)
    finally:
        host.close()


def test_mcp_crash_or_out_of_band_restart_cannot_be_mistaken_for_owner_or_ready(tmp_path):
    tmp_path.chmod(0o700)
    driver = Containers()
    host = BrowserSessionHost(containers=BrowserContainers("a" * 64, "b" * 64),
                              state_directory=tmp_path, driver=driver)
    async def exercise():
        active = await host.control("start", 0)
        driver.running["mcp"] = False
        assert (await host.status())["state"] == "recovery_required"
        driver.running["mcp"] = True
        driver.started["mcp"] = "unmanaged-restart"
        assert not (await host.status())["eva_control"]
        with pytest.raises(BrowserSessionConflict):
            await host.control("start", active["revision"])
        stopped = await host.control("stop", active["revision"])
        assert stopped["state"] == "stopped"
        assert driver.calls[-2:] == [("stop", "mcp"), ("stop", "browser")]
    try:
        asyncio.run(exercise())
    finally:
        host.close()


def test_docker_driver_rejects_replaced_id_and_host_privileges_before_mutation(monkeypatch):
    pair = BrowserContainers("a" * 64, "b" * 64)
    driver = DockerBrowserContainers(pair)
    def container(container_id, image, role):
        return {"Id": container_id,
                "Config": {"Image": image, "User": "1000:1000", "Healthcheck": {"Test": ["CMD", "ready"]},
                           "Entrypoint": ["node"], "Cmd": ["/app/cli.js", "--cdp-endpoint",
                           "http://127.0.0.1:9222", "--shared-browser-context"]},
                "HostConfig": {"Privileged": False, "CapAdd": [], "CapDrop": ["ALL"],
                               "SecurityOpt": ["no-new-privileges:true", "seccomp=profile.json"],
                               "RestartPolicy": {"Name": "no"}, "ReadonlyRootfs": True,
                               "NetworkMode": "none" if role == "browser" else "container:" + pair.browser_id},
                "State": {"Running": False}}
    browser = container(pair.browser_id, BROWSER_IMAGE, "browser")
    mcp = container(pair.mcp_id, MCP_IMAGE, "mcp")
    monkeypatch.setattr(driver, "_run", lambda *args: json.dumps([browser, mcp]))
    assert not driver.inspect()["mcp_running"]
    mcp["Id"] = "c" * 64
    with pytest.raises(BrowserSessionError):
        driver.inspect()
    mcp["Id"] = pair.mcp_id
    browser["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]
    with pytest.raises(BrowserSessionError):
        driver.inspect()
