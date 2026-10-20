"""Task-scoped upstream MCP execution in isolated, persistent workspaces.

Only trusted host configuration and delegated-runner identities enter this
boundary. The upstream server supplies programming tools. This module owns
container lifetime and request receipts, not an agent loop or release authority.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
import uuid


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("programming identity must be a trusted SHA-256 key")
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _json_value(value, depth=0):
    if depth > 64:
        raise ValueError("programming JSON nesting exceeds its bound")
    if value is None or type(value) in (str, bool, int, float):
        return
    if type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise ValueError("programming arguments require JSON values and string keys")


def _directory(path, *, private=False):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise ValueError("programming directory must be absolute without symbolic links")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.resolve() != path:
        raise ValueError("programming directory cannot follow symbolic links")
    if private and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError("programming receipts require a private host-owned directory")
    return path


@dataclass(frozen=True)
class ProgrammingWorkspaceConfig:
    image: str
    workspace_root: Path
    receipt_root: Path | None = None
    memory_mb: int = 1024
    cpus: float = 1.0
    pids: int = 128
    tool_timeout_seconds: int = 300
    max_result_bytes: int = 4 * 1024 * 1024
    python_diagnostics: bool = False
    diagnostics_timeout_seconds: int = 5

    def __post_init__(self):
        if not isinstance(self.image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image):
            raise ValueError("programming image must be an exact local SHA-256 image ID")
        root = Path(self.workspace_root)
        receipts = Path(self.receipt_root) if self.receipt_root is not None else root.with_name(root.name + "-receipts")
        for path in (root, receipts):
            if (not path.is_absolute() or ".." in path.parts or path.resolve() != path
                    or path == Path("/") or "," in str(path) or any(ord(c) < 32 for c in str(path))):
                raise ValueError("programming roots must be separate absolute nonsymlink directories")
        if root.is_relative_to(receipts) or receipts.is_relative_to(root):
            raise ValueError("programming receipts must be outside the workspace root")
        if (type(self.memory_mb) is not int or not 128 <= self.memory_mb <= 8192
                or type(self.pids) is not int or not 16 <= self.pids <= 512
                or type(self.cpus) not in (int, float) or not 0.1 <= self.cpus <= 8
                or type(self.tool_timeout_seconds) is not int or not 1 <= self.tool_timeout_seconds <= 3600
                or type(self.max_result_bytes) is not int or not 1024 <= self.max_result_bytes <= 16 * 1024 * 1024):
            raise ValueError("invalid trusted programming resource limits")
        if (type(self.python_diagnostics) is not bool
                or type(self.diagnostics_timeout_seconds) is not int
                or not 1 <= self.diagnostics_timeout_seconds <= 30):
            raise ValueError("invalid trusted programming diagnostics settings")
        object.__setattr__(self, "workspace_root", root)
        object.__setattr__(self, "receipt_root", receipts)


def _prepare_workspace(root, workspace_key):
    path = _directory(root / _key(workspace_key))
    # Only this child is mounted; do not expose artifacts to other host accounts.
    if (path.stat().st_uid, path.stat().st_gid) != (10001, 10001):
        try:
            os.chown(path, 10001, 10001)
        except PermissionError:
            raise RuntimeError("trusted host must prepare workspace ownership for UID 10001") from None
    path.chmod(0o700)
    return path


def _container_name(session_key):
    return "eva-programming-" + _key(session_key)


def _docker_arguments(config, session_key, workspace):
    return ["run", "--rm", "--interactive", "--no-healthcheck", "--pull", "never",
            "--name", _container_name(session_key), "--network", "none",
            "--log-driver", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", "10001:10001",
            "--memory", f"{config.memory_mb}m", "--memory-swap", f"{config.memory_mb}m",
            "--cpus", str(config.cpus), "--pids-limit", str(config.pids),
            "--ulimit", "nofile=1024:1024", "--workdir", "/workspace",
            "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=67108864,mode=1777",
            "--mount", f"type=bind,src={workspace},dst=/workspace",
            config.image]


def _remove_container(session_key):
    name = _container_name(session_key)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    result = subprocess.run(["docker", "rm", "--force", name],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, timeout=10, check=False)
    if result.returncode and b"No such container" not in result.stderr:
        # Docker's --rm exit can already be removing this exact container.
        # Observe that removal rather than issue another destructive command.
        progress = f"removal of container {name} is already in progress".encode()
        if progress in result.stderr:
            _wait_for_container_absence(name, env)
            return
        raise RuntimeError("programming container cleanup was not confirmed")


def _wait_for_container_absence(name, env):
    deadline = time.monotonic() + 10
    while (remaining := deadline - time.monotonic()) > 0:
        result = subprocess.run(["docker", "inspect", "--format", "{{.State.Status}}", name],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, timeout=remaining, check=False)
        if result.returncode:
            error = result.stderr.lower()
            if (name.encode() in error and any(message in error for message in
                    (b"no such object", b"no such container"))):
                return
            break
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    raise RuntimeError("programming container cleanup was not confirmed")


def _reconcile_containers(config):
    """Retire orphaned attempts only after checking their exact workspace bind."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    names = subprocess.check_output(["docker", "ps", "--all", "--format", "{{.Names}}",
        "--filter", "name=eva-programming-"], env=env, timeout=10, text=True).splitlines()
    for name in names:
        if not re.fullmatch(r"eva-programming-[0-9a-f]{64}", name):
            continue
        try:
            entries = json.loads(subprocess.check_output(["docker", "inspect", name],
                env=env, timeout=10, text=True, stderr=subprocess.PIPE))
        except subprocess.CalledProcessError as error:
            # Only an already removed --rm container can be skipped. An
            # unavailable daemon must not admit an overlapping new attempt.
            if any(message in (error.stderr or "") for message in
                   ("No such object", "No such container")):
                continue
            raise RuntimeError("programming orphan inspection was not confirmed") from None
        if len(entries) != 1 or entries[0].get("Name") != "/" + name:
            raise RuntimeError("programming orphan inspection binding differs")
        info = entries[0]
        mounts = [m for m in info.get("Mounts", ())
                  if m.get("Type") == "bind" and m.get("Destination") == "/workspace"]
        if len(mounts) != 1:
            continue
        workspace = Path(mounts[0].get("Source", ""))
        if workspace.parent != config.workspace_root:
            continue
        _key(workspace.name)
        if (workspace.resolve() != workspace or info.get("Image") != config.image
                or mounts[0].get("RW") is not True):
            raise RuntimeError("orphaned programming workspace image or mount differs")
        if any(m.get("Type") != "tmpfs" and m is not mounts[0] for m in info.get("Mounts", ())):
            raise RuntimeError("orphaned programming container has unexpected mounts")
        _remove_container(name.removeprefix("eva-programming-"))


def _lock_workspace_root(root):
    path = root / ".host-service.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("programming host lock requires a private regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise RuntimeError("programming workspace root is already managed or its lock is invalid") from None


@asynccontextmanager
async def _mcp_session(config, session_key, workspace):
    # This optional host boundary does not add an SDK dependency to the core.
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command="docker",
        args=_docker_arguments(config, session_key, workspace),
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"})
    with open(os.devnull, "w") as errors:
        async with stdio_client(parameters, errlog=errors) as (reader, writer):
            async with ClientSession(reader, writer,
                    read_timeout_seconds=timedelta(seconds=config.tool_timeout_seconds)) as session:
                async with asyncio.timeout(config.tool_timeout_seconds):
                    await session.initialize()
                yield session


def _read_receipt(path, checksum, limit):
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("programming receipt cannot follow a symbolic link")
    with path.open("rb") as source:
        raw = source.read(limit + 8193)
    if len(raw) > limit + 8192:
        raise ValueError("programming receipt exceeds its bound")
    receipt = json.loads(raw)
    if receipt.get("request_sha256") != checksum:
        raise ValueError("programming request ID was already used with different arguments")
    if receipt.get("state") != "completed":
        raise RuntimeError("programming request outcome is uncertain; it will not be repeated")
    return receipt["result"]


def _save_receipt(path, receipt, *, initial=False):
    raw = _canonical(receipt)
    target = path if initial else path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        if not initial:
            os.replace(target, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if not initial and target.exists():
            target.unlink()


class _Session:
    """One actor owns both MCP context managers for their complete lifetime."""
    def __init__(self, config, session_key, workspace, receipts):
        self.config, self.key = config, session_key
        self.workspace, self.receipts = workspace, receipts
        self.diagnostics = None
        if config.python_diagnostics:
            from eva.operations.programming_diagnostics import ProgrammingDiagnostics
            self.diagnostics = ProgrammingDiagnostics(workspace, _container_name(session_key),
                timeout_seconds=config.diagnostics_timeout_seconds)
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.guard = threading.Lock()
        # Public stop and actor teardown may reach Docker at the same time.
        # Serialize only removal, independently of the tool/MCP actor lock.
        self.cleanup_guard = threading.Lock()
        self.pending = set()
        self.loop = self.task = self.queue = None
        self.thread = threading.Thread(target=self._thread_main,
            name="eva-programming-" + session_key[:12], daemon=True)
        self.thread.start()

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        finally:
            self.closed.set()
            self.ready.set()
            with self.guard:
                for future in self.pending:
                    if not future.done():
                        future.set_exception(RuntimeError("programming session closed; inspect uncertain requests"))
                self.pending.clear()

    async def _run(self):
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        self.queue = asyncio.Queue()
        self.ready.set()
        try:
            if self.closed.is_set():
                return
            async with _mcp_session(self.config, self.key, self.workspace) as client:
                while not self.closed.is_set():
                    operation, payload, future = await self.queue.get()
                    if self.closed.is_set():
                        break
                    try:
                        async with asyncio.timeout(self.config.tool_timeout_seconds):
                            if operation == "list":
                                result = await self._list(client)
                            elif operation == "status":
                                result = await self._processes_idle(client)
                            else:
                                result = await self._call(client, *payload)
                        if not future.done():
                            future.set_result(result)
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        if not future.done():
                            future.set_exception(error)
                    finally:
                        if future.done():
                            with self.guard:
                                self.pending.discard(future)
        except BaseException:
            # Never present upstream errors or startup output as user success.
            pass
        finally:
            self.closed.set()
            if self.diagnostics is not None:
                try:
                    await self.diagnostics.close()
                except Exception:
                    pass  # The fixed child container is still removed below.
            try:
                self.remove_container()
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass  # Public close still performs and verifies its own removal.

    async def _list(self, client):
        tools, cursor, seen = [], None, set()
        while True:
            response = await client.list_tools(cursor=cursor)
            tools.extend(tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                         for tool in response.tools)
            cursor = response.nextCursor
            if not cursor:
                return {"tools": tools}
            if cursor in seen:
                raise RuntimeError("upstream programming tool pagination repeated a cursor")
            seen.add(cursor)

    async def _processes_idle(self, client):
        # list_sessions reports the pinned server's own session registry. It
        # does not contain child stdout, which must not forge terminal state.
        response = await client.call_tool("list_sessions", {})
        result = response.model_dump(mode="json", by_alias=True, exclude_none=True)
        content = result.get("content")
        idle = (result.get("isError") is False and isinstance(content, list)
                and len(content) == 1 and content[0].get("type") == "text"
                and content[0].get("text") == "No active sessions")
        return {"idle": idle, "semantic_success_verified": False}

    async def _call(self, client, name, arguments, request_id, checksum):
        _directory(self.receipts, private=True)
        path = self.receipts / (request_id + ".json")
        previous = _read_receipt(path, checksum, self.config.max_result_bytes)
        if previous is not None:
            return previous
        receipt = {"contract": "EvaProgrammingRequest/v1", "request_sha256": checksum,
                   "request_id": request_id, "session_key": self.key, "state": "started"}
        _save_receipt(path, receipt, initial=True)
        # A crash, disconnect or cancelled call deliberately leaves 'started'.
        # It must not dispatch again, including after a host-service restart.
        before, feedback = None, None
        if self.diagnostics is not None:
            try:
                before = await self.diagnostics.before(name, arguments)
            except Exception:
                feedback = {"status": "unavailable", "reason": "diagnostics_failed"}
        response = await client.call_tool(name, arguments)
        result = response.model_dump(mode="json", by_alias=True, exclude_none=True)
        if len(_canonical(result)) > self.config.max_result_bytes:
            raise RuntimeError("programming result exceeds its bound; inspect workspace before continuing")
        if self.diagnostics is not None:
            try:
                feedback = await self.diagnostics.after(before, result) or feedback
            except Exception:
                feedback = {"status": "unavailable", "reason": "diagnostics_failed"}
            if feedback is not None:
                original = result
                result = {**original, "eva_diagnostics": {**feedback,
                    "sandbox_image": self.config.image, "semantic_success_verified": False}}
                if len(_canonical(result)) > self.config.max_result_bytes:
                    # Optional analysis must not make an acknowledged write uncertain.
                    summary = {"contract": "EvaProgrammingDiagnostics/v1", "status": "unavailable",
                        "comparison_status": "unavailable", "reason": "diagnostics_result_bound",
                        "sandbox_image": self.config.image, "semantic_success_verified": False}
                    for phase in ("before", "after"):
                        observed = feedback.get(phase)
                        if isinstance(observed, dict):
                            summary[phase] = {key: observed[key] for key in
                                ("content_sha256", "document_version") if key in observed}
                    result = {**original, "eva_diagnostics": summary}
                    if len(_canonical(result)) > self.config.max_result_bytes:
                        result = original  # Missing diagnostics is never a clean result.
        _save_receipt(path, {**receipt, "state": "completed", "result": result})
        return result

    def submit(self, operation, payload=None):
        self.ready.wait()
        future = Future()
        with self.guard:
            if self.closed.is_set():
                raise RuntimeError("programming session is closed")
            self.pending.add(future)
            try:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, (operation, payload, future))
            except RuntimeError:
                self.pending.discard(future)
                raise RuntimeError("programming session is closed") from None
        return future.result()

    def signal_close(self):
        self.closed.set()
        self.ready.wait()
        if self.loop is not None and self.task is not None:
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass

    def remove_container(self):
        with self.cleanup_guard:
            # Do not cache success: startup may still be unwinding after an
            # earlier removal observed an absent container.
            _remove_container(self.key)


class ProgrammingWorkspaces:
    def __init__(self, config: ProgrammingWorkspaceConfig):
        if not isinstance(config, ProgrammingWorkspaceConfig):
            raise ValueError("trusted programming workspace configuration required")
        self.config = config
        _directory(config.workspace_root, private=True)
        _directory(config.receipt_root, private=True)
        self._lock_fd = _lock_workspace_root(config.workspace_root)
        try:
            _reconcile_containers(config)
        except BaseException:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise
        self._guard = threading.Lock()
        self._sessions, self._bindings, self._workspace_sessions = {}, {}, {}
        self._closed = set()
        self._shutdown = False

    def _session(self, session_key, workspace_key):
        key = _key(session_key)
        workspace_key = _key(workspace_key if workspace_key is not None else key)
        with self._guard:
            if self._shutdown or key in self._closed:
                raise RuntimeError("programming session is closed")
            if key in self._bindings and self._bindings[key] != workspace_key:
                raise ValueError("programming session workspace binding changed")
            active = self._workspace_sessions.get(workspace_key)
            if active is not None and active != key:
                raise RuntimeError("programming workspace already has an active attempt")
            self._bindings[key] = workspace_key
            if key not in self._sessions:
                _directory(self.config.workspace_root, private=True)
                _directory(self.config.receipt_root, private=True)
                workspace = _prepare_workspace(self.config.workspace_root, workspace_key)
                receipts = _directory(self.config.receipt_root / workspace_key, private=True)
                self._sessions[key] = _Session(self.config, key, workspace, receipts)
                self._workspace_sessions[workspace_key] = key
            return self._sessions[key]

    def list_tools(self, session_key, *, workspace_key=None):
        return self._session(session_key, workspace_key).submit("list")

    def processes_idle(self, session_key, *, workspace_key=None):
        return self._session(session_key, workspace_key).submit("status")

    def call_tool(self, session_key, name, arguments, request_id, *, workspace_key=None):
        _key(request_id)
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", name)
                or not isinstance(arguments, dict)):
            raise ValueError("invalid programming tool request")
        try:
            _json_value(arguments)
            raw = _canonical({"image": self.config.image, "name": name, "arguments": arguments})
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ValueError("programming arguments must be bounded JSON") from None
        if len(raw) > 1024 * 1024:
            raise ValueError("programming arguments exceed one MiB")
        checksum = hashlib.sha256(raw).hexdigest()
        snapshot = json.loads(raw)["arguments"]
        return self._session(session_key, workspace_key).submit("call", (name, snapshot, request_id, checksum))

    def close(self, session_key):
        key = _key(session_key)
        with self._guard:
            if self._lock_fd is None:
                return {"status": "closed", "workspace_preserved": True}
            self._closed.add(key)
            session = self._sessions.get(key)
        if session is not None:
            session.signal_close()
        # Never take a tool lock or wait for an MCP call to return before removal.
        if session is None:
            _remove_container(key)
        else:
            session.remove_container()
        if session is not None:
            session.thread.join(timeout=10)
            if session.thread.is_alive():
                raise RuntimeError("programming actor shutdown was not confirmed")
        with self._guard:
            workspace_key = self._bindings.get(key)
            if self._workspace_sessions.get(workspace_key) == key:
                del self._workspace_sessions[workspace_key]
        return {"status": "closed", "workspace_preserved": True}

    def close_all(self):
        with self._guard:
            if self._lock_fd is None:
                return
            self._shutdown = True
            keys = tuple(self._sessions)
        errors = []
        for key in keys:
            try:
                self.close(key)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                errors.append(error)
        if errors:
            raise RuntimeError("one or more programming sessions did not close") from None
        with self._guard:
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
