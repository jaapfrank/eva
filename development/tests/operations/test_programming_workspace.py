"""Focused programming workspace lifecycle checks without Docker or model calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest

from eva.operations import programming_workspace as pw


IMAGE = "sha256:" + "a" * 64
SESSION = "1" * 64
NEXT_SESSION = "2" * 64
WORKSPACE = "3" * 64
REQUEST = "4" * 64


class Model:
    def __init__(self, value):
        self.value = value

    def model_dump(self, **kwargs):
        return self.value


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    state = SimpleNamespace(calls=[], entered=[], exited=[], removed=[],
        started=threading.Event(), hold=False, fail=False, pages=False,
        sessions_text="No active sessions", sessions_error=False)

    class Client:
        def __init__(self, key, workspace):
            self.key, self.workspace = key, workspace

        async def list_tools(self, cursor=None):
            page = "second" if cursor else "first"
            tool = Model({"name": "write_file" if not cursor else "start_process",
                "description": page, "inputSchema": {"type": "object", "properties": {
                    "path": {"type": "string"}, "content": {"type": "string"}}}})
            return SimpleNamespace(tools=[tool],
                nextCursor="next" if state.pages and cursor is None else None)

        async def call_tool(self, name, arguments):
            if name == "list_sessions":
                return Model({"content": [{"type": "text", "text": state.sessions_text}],
                              "isError": state.sessions_error})
            state.calls.append((self.key, name, arguments))
            (self.workspace / "authored.py").write_text("print('retained')\n")
            state.started.set()
            if state.fail:
                raise OSError("simulated lost response after external write")
            if state.hold:
                await asyncio.Event().wait()
            return Model({"content": [{"type": "text", "text": "done"}], "isError": False})

    @asynccontextmanager
    async def mcp(config, key, workspace):
        actor = asyncio.current_task()
        state.entered.append((key, actor))
        try:
            yield Client(key, workspace)
        finally:
            state.exited.append((key, asyncio.current_task()))
            assert actor is asyncio.current_task()

    def prepare(root, key):
        return pw._directory(root / key)

    monkeypatch.setattr(pw, "_prepare_workspace", prepare)
    monkeypatch.setattr(pw, "_reconcile_containers", lambda config: None)
    monkeypatch.setattr(pw, "_mcp_session", mcp)
    monkeypatch.setattr(pw, "_remove_container", state.removed.append)
    config = pw.ProgrammingWorkspaceConfig(IMAGE, tmp_path / "workspaces")
    managers = []

    def create():
        manager = pw.ProgrammingWorkspaces(config)
        managers.append(manager)
        return manager

    yield create, config, state
    for manager in managers:
        manager.close_all()


def test_upstream_discovery_preserves_full_schemas_and_actor_context(boundary):
    create, _, state = boundary
    state.pages = True
    manager = create()
    result = manager.list_tools(SESSION, workspace_key=WORKSPACE)
    assert [tool["name"] for tool in result["tools"]] == ["write_file", "start_process"]
    assert result["tools"][0]["inputSchema"]["properties"]["content"] == {"type": "string"}
    manager.close(SESSION)
    assert state.entered == state.exited
    with pytest.raises(RuntimeError, match="closed"):
        manager.list_tools(SESSION, workspace_key=WORKSPACE)


def test_separate_sessions_cannot_cross_bindings_or_active_workspace(boundary):
    create, config, state = boundary
    manager = create()
    manager.call_tool(SESSION, "write_file", {"path": "/workspace/a.py"}, REQUEST, workspace_key=WORKSPACE)
    with pytest.raises(ValueError, match="binding"):
        manager.list_tools(SESSION, workspace_key="5" * 64)
    with pytest.raises(RuntimeError, match="active attempt"):
        manager.list_tools(NEXT_SESSION, workspace_key=WORKSPACE)
    manager.list_tools(NEXT_SESSION, workspace_key="5" * 64)
    manager.close(SESSION)
    assert (config.workspace_root / WORKSPACE / "authored.py").is_file()
    assert NEXT_SESSION not in state.removed
    assert manager.list_tools(NEXT_SESSION, workspace_key="5" * 64)["tools"]


def test_same_request_replays_exact_receipt_and_changed_arguments_reject(boundary):
    create, config, state = boundary
    manager = create()
    first = manager.call_tool(SESSION, "write_file", {"content": "hello"}, REQUEST, workspace_key=WORKSPACE)
    second = manager.call_tool(SESSION, "write_file", {"content": "hello"}, REQUEST, workspace_key=WORKSPACE)
    assert first == second and len(state.calls) == 1
    with pytest.raises(ValueError, match="different arguments"):
        manager.call_tool(SESSION, "write_file", {"content": "changed"}, REQUEST, workspace_key=WORKSPACE)
    manager.close(SESSION)
    # A later attempt uses the same stable workspace and request receipt.
    assert manager.call_tool(NEXT_SESSION, "write_file", {"content": "hello"}, REQUEST,
                             workspace_key=WORKSPACE) == first
    assert len(state.calls) == 1
    receipt = config.receipt_root / WORKSPACE / (REQUEST + ".json")
    assert json.loads(receipt.read_text())["state"] == "completed"
    assert receipt.stat().st_mode & 0o077 == 0
    assert not config.receipt_root.is_relative_to(config.workspace_root)


def test_completed_request_replays_after_host_restart(boundary):
    create, _, state = boundary
    first = create()
    expected = first.call_tool(SESSION, "write_file", {}, REQUEST, workspace_key=WORKSPACE)
    first.close_all()
    restarted = create()
    assert restarted.call_tool(NEXT_SESSION, "write_file", {}, REQUEST,
                               workspace_key=WORKSPACE) == expected
    assert len(state.calls) == 1


def test_uncertain_result_never_repeats_even_after_restart(boundary):
    create, config, state = boundary
    state.fail = True
    manager = create()
    with pytest.raises(OSError, match="lost response"):
        manager.call_tool(SESSION, "write_file", {}, REQUEST, workspace_key=WORKSPACE)
    manager.close_all()
    state.fail = False
    restarted = create()
    with pytest.raises(RuntimeError, match="uncertain"):
        restarted.call_tool(NEXT_SESSION, "write_file", {}, REQUEST, workspace_key=WORKSPACE)
    assert len(state.calls) == 1
    assert (config.workspace_root / WORKSPACE / "authored.py").is_file()


def test_close_interrupts_inflight_call_without_waiting_for_tool_lock(boundary):
    create, config, state = boundary
    state.hold = True
    manager = create()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(manager.call_tool, SESSION, "start_process", {},
                              REQUEST, workspace_key=WORKSPACE)
        assert state.started.wait(timeout=2)
        started = time.monotonic()
        assert manager.close(SESSION)["workspace_preserved"] is True
        assert time.monotonic() - started < 2
        with pytest.raises(RuntimeError, match="closed"):
            pending.result(timeout=2)
    assert SESSION in state.removed
    assert (config.workspace_root / WORKSPACE / "authored.py").exists()
    assert json.loads((config.receipt_root / WORKSPACE / (REQUEST + ".json")).read_text())["state"] == "started"
    state.hold = False
    with pytest.raises(RuntimeError, match="uncertain"):
        manager.call_tool(NEXT_SESSION, "start_process", {}, REQUEST, workspace_key=WORKSPACE)
    assert len(state.calls) == 1
    assert state.entered[0] == state.exited[0]


def test_closed_before_first_call_cannot_start_a_container(boundary):
    create, _, state = boundary
    manager = create()
    manager.close(SESSION)
    with pytest.raises(RuntimeError, match="closed"):
        manager.call_tool(SESSION, "start_process", {}, REQUEST)
    assert not state.entered


def test_public_close_and_actor_teardown_do_not_overlap_container_removal(boundary, monkeypatch):
    create, _, state = boundary
    manager = create()
    manager.list_tools(SESSION, workspace_key=WORKSPACE)
    guard = threading.Lock()
    active = peak = calls = 0
    start = threading.Barrier(2)

    def remove(key):
        nonlocal active, peak, calls
        assert key == SESSION
        with guard:
            active += 1
            peak = max(peak, active)
            calls += 1
        try:
            # Represent a Docker removal in progress while the cancelled
            # actor and both public close calls reach the same boundary.
            time.sleep(0.05)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(pw, "_remove_container", remove)

    def close():
        start.wait(timeout=2)
        return manager.close(SESSION)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(close) for _ in range(2)]
        assert all(f.result(timeout=2)["workspace_preserved"] for f in futures)
    assert peak == 1
    assert calls == 3  # Two public closes plus the existing actor finally.
    assert state.entered == state.exited
    monkeypatch.setattr(pw, "_remove_container", state.removed.append)


def test_docker_boundary_mounts_only_task_directory_and_immutable_image(tmp_path):
    config = pw.ProgrammingWorkspaceConfig(IMAGE, tmp_path / "workspaces")
    path = config.workspace_root / WORKSPACE
    args = pw._docker_arguments(config, SESSION, path)
    assert args[-1] == IMAGE
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--user") + 1] == "10001:10001"
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "--read-only" in args and "no-new-privileges" in args
    assert args.count("--mount") == 1
    assert args[args.index("--mount") + 1] == f"type=bind,src={path},dst=/workspace"
    assert not any("docker.sock" in value or str(config.receipt_root) in value for value in args)
    assert args[args.index("--name") + 1] == pw._container_name(SESSION)


def test_invalid_image_roots_and_symlinks_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="exact local"):
        pw.ProgrammingWorkspaceConfig("desktop-commander:latest", tmp_path / "workspace")
    with pytest.raises(ValueError, match="outside"):
        pw.ProgrammingWorkspaceConfig(IMAGE, tmp_path / "workspace", tmp_path / "workspace" / "receipts")
    with pytest.raises(ValueError, match="absolute"):
        pw.ProgrammingWorkspaceConfig(IMAGE, Path("relative"))
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="nonsymlink"):
        pw.ProgrammingWorkspaceConfig(IMAGE, link)
    root = tmp_path / "root"
    root.mkdir()
    (root / WORKSPACE).symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        pw._prepare_workspace(root, WORKSPACE)


def test_receipt_symlink_and_nonprivate_directory_fail_closed(boundary):
    create, config, _ = boundary
    manager = create()
    manager.list_tools(SESSION, workspace_key=WORKSPACE)
    target = config.receipt_root / "outside"
    target.write_text("{}")
    path = config.receipt_root / WORKSPACE / (REQUEST + ".json")
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic"):
        manager.call_tool(SESSION, "write_file", {}, REQUEST, workspace_key=WORKSPACE)
    path.unlink()
    path.parent.chmod(0o755)
    with pytest.raises(ValueError, match="private host"):
        manager.call_tool(SESSION, "write_file", {}, REQUEST, workspace_key=WORKSPACE)
    path.parent.chmod(0o700)


def test_request_identity_and_non_json_arguments_reject_before_start(boundary):
    create, _, state = boundary
    manager = create()
    for key in ("../escape", "A" * 64, "short"):
        with pytest.raises(ValueError, match="SHA-256"):
            manager.list_tools(key)
    with pytest.raises(ValueError, match="SHA-256"):
        manager.call_tool(SESSION, "write_file", {}, "../receipt")
    with pytest.raises(ValueError, match="bounded JSON"):
        manager.call_tool(SESSION, "write_file", {"value": float("nan")}, REQUEST)
    with pytest.raises(ValueError, match="invalid"):
        manager.call_tool(SESSION, "bad/tool", {}, REQUEST)
    assert not state.entered


def test_container_removal_uses_exact_name_and_rejects_unconfirmed_failure(monkeypatch):
    seen = []
    def run(args, **kwargs):
        seen.append((args, kwargs))
        return SimpleNamespace(returncode=1, stderr=b"No such container: already removed")
    monkeypatch.setattr(pw.subprocess, "run", run)
    pw._remove_container(SESSION)
    assert seen[0][0] == ["docker", "rm", "--force", pw._container_name(SESSION)]
    assert "HOME" not in seen[0][1]["env"]
    monkeypatch.setattr(pw.subprocess, "run",
                        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=b"daemon unavailable"))
    with pytest.raises(RuntimeError, match="not confirmed"):
        pw._remove_container(SESSION)


def test_auto_removal_race_observes_absence_without_repeating_removal(monkeypatch):
    name = pw._container_name(SESSION)
    responses = iter([
        SimpleNamespace(returncode=1, stderr=f"removal of container {name} is already in progress".encode()),
        SimpleNamespace(returncode=0, stdout=b"removing\n", stderr=b""),
        SimpleNamespace(returncode=1, stdout=b"", stderr=f"error: no such object: {name}".encode()),
    ])
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return next(responses)
    monkeypatch.setattr(pw.subprocess, "run", run)
    monkeypatch.setattr(pw.time, "sleep", lambda seconds: None)
    pw._remove_container(SESSION)
    assert calls == [["docker", "rm", "--force", name],
                     ["docker", "inspect", "--format", "{{.State.Status}}", name],
                     ["docker", "inspect", "--format", "{{.State.Status}}", name]]


@pytest.mark.parametrize("error", [b"daemon unavailable", b"no such object: another-container"])
def test_auto_removal_race_does_not_claim_absence_without_exact_confirmation(monkeypatch, error):
    name = pw._container_name(SESSION)
    responses = iter([
        SimpleNamespace(returncode=1, stderr=f"removal of container {name} is already in progress".encode()),
        SimpleNamespace(returncode=1, stdout=b"", stderr=error),
    ])
    monkeypatch.setattr(pw.subprocess, "run", lambda *args, **kwargs: next(responses))
    with pytest.raises(RuntimeError, match="not confirmed"):
        pw._remove_container(SESSION)


def test_auto_removal_race_cannot_report_a_persistent_container_as_closed(monkeypatch):
    name = pw._container_name(SESSION)
    times = iter([0, 0, 10, 10])
    responses = iter([
        SimpleNamespace(returncode=1, stderr=f"removal of container {name} is already in progress".encode()),
        SimpleNamespace(returncode=0, stdout=b"removing\n", stderr=b""),
    ])
    monkeypatch.setattr(pw.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(pw.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(pw.subprocess, "run", lambda *args, **kwargs: next(responses))
    with pytest.raises(RuntimeError, match="not confirmed"):
        pw._remove_container(SESSION)


def test_non_string_json_keys_reject_and_argument_snapshot_matches_receipt(boundary, monkeypatch):
    create, _, state = boundary
    manager = create()
    with pytest.raises(ValueError, match="bounded JSON"):
        manager.call_tool(SESSION, "write_file", {1: "value"}, REQUEST)
    arguments = {"nested": {"value": "original"}}
    original_session = manager._session
    def session(key, workspace):
        arguments["nested"]["value"] = "mutated"
        return original_session(key, workspace)
    monkeypatch.setattr(manager, "_session", session)
    manager.call_tool(SESSION, "write_file", arguments, REQUEST)
    assert state.calls[0][2] == {"nested": {"value": "original"}}


def test_close_during_startup_closes_actor_and_rejects_future_calls(boundary, monkeypatch):
    create, _, state = boundary
    starting = threading.Event()
    exited = threading.Event()
    @asynccontextmanager
    async def startup(*args):
        try:
            starting.set()
            await asyncio.Event().wait()
            yield
        finally:
            exited.set()
    monkeypatch.setattr(pw, "_mcp_session", startup)
    manager = create()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(manager.list_tools, SESSION)
        assert starting.wait(timeout=2)
        manager.close(SESSION)
        with pytest.raises(RuntimeError, match="closed"):
            pending.result(timeout=2)
    assert exited.is_set()
    assert SESSION in state.removed


def test_sdk_stdio_and_session_contexts_share_one_actor_task(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    captured, scopes = {}, []
    mcp, client, stdio = ModuleType("mcp"), ModuleType("mcp.client"), ModuleType("mcp.client.stdio")
    class Parameters:
        def __init__(self, **kwargs):
            captured["parameters"] = kwargs
    @asynccontextmanager
    async def transport(parameters, *, errlog):
        scopes.append(("stdio_enter", asyncio.current_task()))
        try:
            yield ("reader", "writer")
        finally:
            scopes.append(("stdio_exit", asyncio.current_task()))
    class Session:
        def __init__(self, reader, writer, *, read_timeout_seconds):
            captured["channels"] = (reader, writer)
            captured["timeout"] = read_timeout_seconds
        async def __aenter__(self):
            scopes.append(("session_enter", asyncio.current_task()))
            return self
        async def initialize(self):
            captured["initialized"] = True
        async def __aexit__(self, *args):
            scopes.append(("session_exit", asyncio.current_task()))
    mcp.ClientSession, mcp.StdioServerParameters = Session, Parameters
    stdio.stdio_client = transport
    for name, module in (("mcp", mcp), ("mcp.client", client), ("mcp.client.stdio", stdio)):
        monkeypatch.setitem(sys.modules, name, module)
    config = pw.ProgrammingWorkspaceConfig(IMAGE, tmp_path / "workspaces")
    async def exercise():
        async with pw._mcp_session(config, SESSION, config.workspace_root / WORKSPACE):
            assert captured["initialized"] is True
    asyncio.run(exercise())
    assert [scope[0] for scope in scopes] == ["stdio_enter", "session_enter", "session_exit", "stdio_exit"]
    assert len({id(scope[1]) for scope in scopes}) == 1
    assert captured["parameters"]["command"] == "docker"
    assert captured["parameters"]["env"]["HOME"] == "/nonexistent"
    assert captured["timeout"].total_seconds() == config.tool_timeout_seconds


def test_workspace_root_has_one_host_manager_and_releases_after_shutdown(boundary):
    create, _, state = boundary
    first = create()
    with pytest.raises(RuntimeError, match="already managed"):
        create()
    first.close_all()
    second = create()
    second.list_tools(SESSION)
    removed = len(state.removed)
    first.close(SESSION)
    assert len(state.removed) == removed
    assert second.list_tools(SESSION)["tools"]


def test_restart_reconciliation_removes_only_exact_root_image_and_mount(tmp_path, monkeypatch):
    config = pw.ProgrammingWorkspaceConfig(IMAGE, tmp_path / "workspaces")
    workspace = config.workspace_root / WORKSPACE
    workspace.mkdir(parents=True)
    own, unrelated = pw._container_name(SESSION), pw._container_name(NEXT_SESSION)
    entries = {
        own: {"Name": "/" + own, "Image": IMAGE, "Mounts": [
            {"Type": "bind", "Source": str(workspace), "Destination": "/workspace", "RW": True},
            {"Type": "tmpfs", "Source": "", "Destination": "/tmp", "RW": True}]},
        unrelated: {"Name": "/" + unrelated, "Image": IMAGE, "Mounts": [
            {"Type": "bind", "Source": str(tmp_path / "other" / WORKSPACE),
             "Destination": "/workspace", "RW": True}]},
    }
    removed = []
    def output(args, **kwargs):
        if args[1] == "ps":
            assert args[-1] == "name=eva-programming-"
            return "\n".join([own, unrelated, "eva-programming-not-an-identity"])
        return json.dumps([entries[args[-1]]])
    monkeypatch.setattr(pw.subprocess, "check_output", output)
    monkeypatch.setattr(pw, "_remove_container", removed.append)
    pw._reconcile_containers(config)
    assert removed == [SESSION]
    removed.clear()
    entries[own]["Image"] = "sha256:" + "b" * 64
    with pytest.raises(RuntimeError, match="image or mount"):
        pw._reconcile_containers(config)
    assert removed == []
    entries[own]["Image"] = IMAGE
    entries[own]["Mounts"].append({"Type": "bind", "Source": "/unexpected",
                                  "Destination": "/other", "RW": True})
    with pytest.raises(RuntimeError, match="unexpected mounts"):
        pw._reconcile_containers(config)
    assert removed == []


def test_failed_reconciliation_releases_host_lock_without_opening_session(boundary, monkeypatch):
    create, _, state = boundary
    def reject(config):
        raise RuntimeError("orphan could not be stopped")
    monkeypatch.setattr(pw, "_reconcile_containers", reject)
    with pytest.raises(RuntimeError, match="could not be stopped"):
        create()
    assert state.entered == []
    monkeypatch.setattr(pw, "_reconcile_containers", lambda config: None)
    assert create().list_tools(SESSION)["tools"]


def test_idle_verification_uses_server_registry_not_child_output(boundary):
    create, _, state = boundary
    manager = create()
    manager.call_tool(SESSION, "start_process", {"command": "print fake completion"}, REQUEST)
    state.sessions_text = "PID: 12, Blocked: true, Runtime: 1s"
    assert manager.processes_idle(SESSION) == {"idle": False, "semantic_success_verified": False}
    # File success while a child runs must not hide the pending session.
    manager.call_tool(SESSION, "write_file", {"content": "No active sessions"}, "9" * 64)
    assert manager.processes_idle(SESSION)["idle"] is False
    state.sessions_text = "No active sessions"
    assert manager.processes_idle(SESSION) == {"idle": True, "semantic_success_verified": False}
    state.sessions_error = True
    assert manager.processes_idle(SESSION)["idle"] is False
    state.sessions_error = False
    state.sessions_text = "No active sessions\\nadditional output"
    assert manager.processes_idle(SESSION)["idle"] is False


def test_restart_reconciliation_does_not_ignore_daemon_inspection_failure(tmp_path, monkeypatch):
    config = pw.ProgrammingWorkspaceConfig(IMAGE, tmp_path / "workspaces")
    def output(args, **kwargs):
        if args[1] == "ps":
            return pw._container_name(SESSION)
        raise pw.subprocess.CalledProcessError(1, args, stderr="daemon unavailable")
    monkeypatch.setattr(pw.subprocess, "check_output", output)
    with pytest.raises(RuntimeError, match="not confirmed"):
        pw._reconcile_containers(config)
