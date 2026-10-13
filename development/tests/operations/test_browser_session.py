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


BOOT_BEFORE = "850edb18-8188-4247-ae67-e098d03f5f8f"
BOOT_AFTER = "fa220e39-cde6-460a-aac4-38dc545f5341"


def test_confirmed_reboot_reopens_only_stable_stopped_eva_session(tmp_path):
    tmp_path.chmod(0o700)
    driver = Containers()
    pair = BrowserContainers("a" * 64, "b" * 64)
    host = BrowserSessionHost(containers=pair, state_directory=tmp_path,
                              driver=driver, boot_id=BOOT_BEFORE)
    active = asyncio.run(host.control("start", 0))
    host.close()
    driver.running = {"browser": False, "mcp": False}
    calls = driver.calls[:]
    host = BrowserSessionHost(containers=pair, state_directory=tmp_path,
                              driver=driver, boot_id=BOOT_AFTER)
    try:
        status = asyncio.run(host.status())
        assert status["state"] == "stopped"
        assert status["revision"] == active["revision"] + 1
        assert driver.calls == calls
        assert json.loads((tmp_path / "state.json").read_text())["boot_id"] == BOOT_AFTER
        with pytest.raises(BrowserSessionConflict):
            asyncio.run(host.control("start", active["revision"]))
        started = asyncio.run(host.control("start", status["revision"]))
        assert started["eva_control"] and started["fresh_observation_required"]
    finally:
        host.close()
    host = BrowserSessionHost(containers=pair, state_directory=tmp_path,
                              driver=driver, boot_id=BOOT_AFTER)
    try:
        assert asyncio.run(host.status())["revision"] == started["revision"]
        assert driver.calls == calls + [("start", "browser"), ("start", "mcp")]
    finally:
        host.close()


@pytest.mark.parametrize("mode,previous_boot,running", [
    ("eva", BOOT_AFTER, (False, False)),
    ("eva", None, (False, False)),
    ("owner", BOOT_BEFORE, (False, False)),
    ("taking_over", BOOT_BEFORE, (False, False)),
    ("starting", BOOT_BEFORE, (False, False)),
    ("resuming", BOOT_BEFORE, (False, False)),
    ("stopping", BOOT_BEFORE, (False, False)),
    ("recovery_required", BOOT_BEFORE, (False, False)),
    ("eva", BOOT_BEFORE, (True, False)),
    ("eva", BOOT_BEFORE, (False, True)),
])
def test_reboot_does_not_override_owner_uncertainty_or_missing_boot_evidence(
        tmp_path, mode, previous_boot, running):
    tmp_path.chmod(0o700)
    pair = BrowserContainers("a" * 64, "b" * 64)
    state = {"binding": pair.binding, "revision": 41, "mode": mode,
             "browser_started_at": "before", "mcp_started_at": "before"}
    if previous_boot is not None:
        state["boot_id"] = previous_boot
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(state))
    state_file.chmod(0o600)
    original = state_file.read_bytes()
    driver = Containers()
    driver.running = dict(zip(("browser", "mcp"), running))
    host = BrowserSessionHost(containers=pair, state_directory=tmp_path,
                              driver=driver, boot_id=BOOT_AFTER)
    try:
        status = asyncio.run(host.status())
        assert status["state"] == "recovery_required"
        assert status["revision"] == 41
        assert not status["eva_control"]
        assert not driver.calls
        assert state_file.read_bytes() == original
        with pytest.raises(BrowserSessionConflict):
            asyncio.run(host.control("start", 41))
    finally:
        host.close()
