"""Public contract checks; no independent final cases or model calls."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from eva.operations import programming_diagnostics as pd
from eva.operations._hermes_lsp_protocol import encode_message, read_message, LSPProtocolError


CONTAINER = "eva-programming-" + "a" * 64
PATH = "/workspace/code.py"


def diagnostic(message="undefined symbol", line=0, code="reportUndefinedVariable"):
    return {"message": message, "severity": 1, "source": "Pyright", "code": code,
            "range": {"start": {"line": line, "character": 0},
                      "end": {"line": line, "character": 3}}}


class Server:
    """Framed in-memory LSP peer, not a replacement semantic analyzer."""
    def __init__(self, policy=None):
        self.stdout = asyncio.StreamReader()
        self.stdin = self
        self.returncode = None
        self.messages = []
        self.policy = policy or (lambda text, version: (version, [diagnostic()] if "bad" in text else []))

    def write(self, raw):
        body = json.loads(raw.split(b"\r\n\r\n", 1)[1])
        self.messages.append(body)
        method = body.get("method")
        if method == "initialize":
            self.emit({"jsonrpc": "2.0", "id": body["id"], "result": {"capabilities": {}}})
        elif method in {"textDocument/didOpen", "textDocument/didChange"}:
            params = body["params"]
            doc = params["textDocument"]
            text = doc.get("text", (params.get("contentChanges") or [{}])[0].get("text"))
            report = self.policy(text, doc["version"])
            if report is not None:
                version, items = report
                values = {"uri": doc["uri"], "diagnostics": items}
                if version is not None:
                    values["version"] = version
                self.emit({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": values})
        elif method == "shutdown":
            self.emit({"jsonrpc": "2.0", "id": body["id"], "result": None})
        elif method == "exit":
            self.terminate()

    def emit(self, body):
        self.stdout.feed_data(encode_message(body))

    async def drain(self):
        await asyncio.sleep(0)

    def close(self):
        pass

    def terminate(self):
        if self.returncode is None:
            self.returncode = 0
            self.stdout.feed_eof()

    kill = terminate

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0)
        return self.returncode


@pytest.fixture
def peer(monkeypatch):
    servers, calls = [], []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return servers[0]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return servers, calls


def run(coroutine):
    return asyncio.run(coroutine)


def test_actual_before_bytes_and_semantic_delta_are_bound_to_versions(tmp_path, peer):
    async def check():
        server = Server()
        peer[0].append(server)
        path = tmp_path / "code.py"
        path.write_text("bad\n")
        helper = pd.ProgrammingDiagnostics(tmp_path, CONTAINER)
        before = await helper.before("write_file", {"path": PATH, "content": "not actual disk"})
        path.write_text("good\n")
        result = await helper.after(before, {"isError": False})
        assert result["before"]["content_sha256"] == hashlib.sha256(b"bad\n").hexdigest()
        assert result["after"]["content_sha256"] == hashlib.sha256(b"good\n").hexdigest()
        assert result["before"]["document_version"] == 1
        assert result["after"]["document_version"] == 2
        assert result["comparison_status"] == "available"
        assert result["new"] == [] and result["preexisting"] == []
        assert result["resolved"] == [diagnostic()]
        assert result["semantic_success_verified"] is False
        assert peer[1][0][0] == ("docker", "exec", "-i", CONTAINER, "pyright-langserver", "--stdio")
        assert set(peer[1][0][1]["env"]) == {"PATH"}
        await helper.close()
        assert server.returncode == 0
    run(check())


def test_preexisting_errors_remain_preexisting_when_lines_are_inserted(tmp_path, peer):
    async def check():
        peer[0].append(Server(lambda text, version: (version, [diagnostic(line=text.count("\n") - 1)])))
        path = tmp_path / "code.py"
        path.write_text("bad\n")
        helper = pd.ProgrammingDiagnostics(tmp_path, CONTAINER)
        before = await helper.before("edit_block", {"path": PATH})
        path.write_text("# comment\nbad\n")
        result = await helper.after(before, {})
        assert result["new"] == result["resolved"] == []
        assert result["preexisting"] == [diagnostic(line=1)]
        await helper.close()
    run(check())


def test_new_file_has_explicit_absence_baseline(tmp_path, peer):
    async def check():
        peer[0].append(Server())
        helper = pd.ProgrammingDiagnostics(tmp_path, CONTAINER)
        before = await helper.before("write_file", {"path": PATH})
        assert before.baseline["status"] == "absent"
        (tmp_path / "code.py").write_text("bad\n")
        result = await helper.after(before, {})
        assert result["before"]["diagnostics"] is None
        assert result["new"] == [diagnostic()]
        assert result["preexisting"] == result["resolved"] == []
        await helper.close()
    run(check())


@pytest.mark.parametrize("name,path", [
    ("start_process", PATH), ("read_file", PATH), ("write_file", "code.py"),
    ("write_file", "/tmp/code.py"), ("write_file", "/workspace/../code.py"),
    ("write_file", "/workspace//code.py"), ("write_file", "/workspace/./code.py"),
    ("write_file", "/workspace/code.js"), ("write_file", "/workspace/code\\bad.py"),
])
def test_only_direct_canonical_python_edits_are_in_scope(tmp_path, peer, name, path):
    async def check():
        helper = pd.ProgrammingDiagnostics(tmp_path, CONTAINER)
        assert await helper.before(name, {"path": path}) is None
        assert await helper.after(None, {}) is None
        assert peer[1] == []
    run(check())


@pytest.mark.parametrize("kind", ["file_symlink", "parent_symlink", "fifo", "non_utf8", "oversize"])
def test_source_reads_refuse_links_devices_or_unanalyzable_bytes(tmp_path, peer, kind):
    external = tmp_path.parent / (tmp_path.name + "-external")
    external.mkdir()
    (external / "secret.py").write_text("secret\n")
    target = tmp_path / "code.py"
    path = PATH
    if kind == "file_symlink":
        target.symlink_to(external / "secret.py")
    elif kind == "parent_symlink":
        (tmp_path / "linked").symlink_to(external, target_is_directory=True)
        path = "/workspace/linked/secret.py"
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "non_utf8":
        target.write_bytes(b"\xff")
    else:
        target.write_bytes(b"a" * (pd._MAX_SOURCE + 1))

    async def check():
        helper = pd.ProgrammingDiagnostics(tmp_path, CONTAINER)
        before = await helper.before("write_file", {"path": path})
        result = await helper.after(before, {})
        assert before.baseline["status"] == result["after"]["status"] == "unavailable"
        assert result["new"] is None and result["resolved"] is None
        assert peer[1] == []
    run(check())


@pytest.mark.parametrize("mode", ["missing", "stale", "versionless", "future"])
def test_missing_or_unbound_reports_never_mean_clean(tmp_path, peer, mode):
    async def check():
        def policy(text, version):
            return None if mode == "missing" else ({"stale": version - 1,
                "versionless": None, "future": version + 1}[mode], [])
        peer[0].append(Server(policy))
        (tmp_path / "code.py").write_text("bad\n")
        helper = pd.ProgrammingDiagnostics(tmp_path, CONTAINER, timeout_seconds=0.03)
        before = await helper.before("write_file", {"path": PATH})
        result = await helper.after(before, {})
        assert result["after"]["status"] == "unavailable"
        assert result["after"]["diagnostics"] is None
        assert result["comparison_status"] == "unavailable"
        assert result["new"] is result["resolved"] is result["preexisting"] is None
        await helper.close()
    run(check())


def test_concurrent_source_change_invalidates_report(tmp_path, peer):
    async def check():
        path = tmp_path / "code.py"
        path.write_text("good\n")
        def policy(text, version):
            path.write_text("changed without this version\n")
            return version, []
        peer[0].append(Server(policy))
        helper = pd.ProgrammingDiagnostics(tmp_path, CONTAINER)
        before = await helper.before("write_file", {"path": PATH})
        assert before.baseline["status"] == "unavailable"
        assert before.baseline["reason"] == "source_changed"
        await helper.close()
    run(check())


def test_stale_after_report_cannot_resolve_a_real_baseline_error(tmp_path, peer):
    async def check():
        peer[0].append(Server(lambda text, version: (version, [diagnostic()])
                             if version == 1 else (version - 1, [])))
        path = tmp_path / "code.py"
        path.write_text("bad\n")
        helper = pd.ProgrammingDiagnostics(tmp_path, CONTAINER, timeout_seconds=0.3)
        before = await helper.before("edit_block", {"path": PATH})
        assert before.baseline["status"] == "available"
        path.write_text("good\n")
        result = await helper.after(before, {})
        assert result["after"]["status"] == "unavailable"
        assert result["resolved"] is None and result["new"] is None
        await helper.close()
    run(check())


def test_eviction_reopen_never_reuses_version_for_late_old_push(tmp_path, peer):
    async def check():
        peer[0].append(Server(lambda text, version: (1, []) if text == "changed\n"
                             else (version, [diagnostic()])))
        path = tmp_path / "code.py"
        path.write_text("bad\n")
        helper = pd.ProgrammingDiagnostics(tmp_path, CONTAINER, timeout_seconds=0.3)
        first = await helper.before("write_file", {"path": PATH})
        assert first.baseline["status"] == "available"
        assert first.baseline["document_version"] == 1
        # Fill the bounded tracking table without another 63 semantic waits.
        for index in range(63):
            future = asyncio.get_running_loop().create_future()
            future.set_result(None)
            helper._documents[f"file:///workspace/other{index}.py"] = {
                "version": 1, "future": future}
        (tmp_path / "new.py").write_text("bad\n")
        await helper.before("write_file", {"path": "/workspace/new.py"})
        assert "file:///workspace/code.py" not in helper._documents
        path.write_text("changed\n")
        reopened = await helper.before("write_file", {"path": PATH})
        assert reopened.baseline["document_version"] == 3
        assert reopened.baseline["status"] == "unavailable"
        assert reopened.baseline["reason"] == "unbound_diagnostics"
        def late_then_current(text, version):
            asyncio.get_running_loop().call_later(0.01, peer[0][0].emit, {
                "jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {
                    "uri": "file:///workspace/code.py", "version": version,
                    "diagnostics": [diagnostic("current error")]}})
            return 1, []
        peer[0][0].policy = late_then_current
        current = await helper.before("edit_block", {"path": PATH})
        assert current.baseline["status"] == "available"
        assert current.baseline["document_version"] == 4
        assert current.baseline["diagnostics"] == [diagnostic("current error")]
        await helper.close()
    run(check())


def test_missing_binary_does_not_raise_or_claim_clean(tmp_path, monkeypatch):
    async def fail(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    async def check():
        (tmp_path / "code.py").write_text("bad\n")
        helper = pd.ProgrammingDiagnostics(tmp_path, CONTAINER)
        before = await helper.before("write_file", {"path": PATH})
        result = await helper.after(before, {"isError": False})
        assert result["after"]["reason"] == "server_unavailable"
        assert result["new"] is None
        await helper.close()
    run(check())


def test_cancellation_propagates_to_existing_stop_route(tmp_path, peer):
    async def check():
        peer[0].append(Server(lambda text, version: None))
        (tmp_path / "code.py").write_text("bad\n")
        helper = pd.ProgrammingDiagnostics(tmp_path, CONTAINER)
        task = asyncio.create_task(helper.before("write_file", {"path": PATH}))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await helper.close()
    run(check())


def test_optional_disable_never_starts_server(tmp_path, peer):
    async def check():
        (tmp_path / "code.py").write_text("bad\n")
        helper = pd.ProgrammingDiagnostics(tmp_path, CONTAINER, enabled=False)
        result = await helper.after(await helper.before("write_file", {"path": PATH}), {})
        assert result["after"]["reason"] == "disabled"
        assert peer[1] == []
        await helper.close()
    run(check())


def test_framing_handles_utf8_and_rejects_oversized_server_body():
    async def check():
        reader = asyncio.StreamReader()
        body = {"jsonrpc": "2.0", "id": 1, "result": "café"}
        reader.feed_data(encode_message(body))
        assert await read_message(reader) == body
        reader.feed_data(b"Content-Length: 2097153\r\n\r\n")
        with pytest.raises(LSPProtocolError):
            await read_message(reader)
    run(check())


@pytest.mark.skipif(not os.environ.get("EVA_PROGRAMMING_DIAGNOSTICS_IMAGE"),
                    reason="requires the exact locally built optional sandbox image")
def test_actual_pyright_semantic_feedback_in_same_offline_sandbox(tmp_path):
    """Public undefined-name/type case, independent of the protected final task."""
    from eva.operations.programming_workspace import ProgrammingWorkspaceConfig, _docker_arguments
    image = os.environ["EVA_PROGRAMMING_DIAGNOSTICS_IMAGE"]
    config = ProgrammingWorkspaceConfig(image, tmp_path / "root")
    workspace = tmp_path / "work"
    workspace.mkdir(mode=0o777)
    workspace.chmod(0o777)
    key = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    name = "eva-programming-" + key
    args = _docker_arguments(config, key, workspace)
    args.insert(1, "--detach")
    args[-1:-1] = ["--entrypoint", "node"]
    args.extend(["-e", "setInterval(()=>{},1000)"])
    subprocess.run(["docker", *args], check=True, capture_output=True, timeout=20)
    async def check():
        helper = pd.ProgrammingDiagnostics(workspace, name, timeout_seconds=15)
        path = workspace / "code.py"
        path.write_text("previous = missing_before\n")
        before = await helper.before("write_file", {"path": PATH})
        path.write_text('previous = missing_before\nvalue: int = "wrong"\nprint(missing_after)\n')
        feedback = await helper.after(before, {})
        assert feedback["comparison_status"] == "available", feedback
        assert any("missing_before" in d["message"] for d in feedback["preexisting"])
        assert any("missing_after" in d["message"] for d in feedback["new"])
        assert any(d.get("code") == "reportAssignmentType" for d in feedback["new"])
        before = await helper.before("edit_block", {"path": PATH})
        path.write_text("previous = missing_before\nvalue: int = 3\nprint(value)\n")
        fixed = await helper.after(before, {})
        assert fixed["comparison_status"] == "available", fixed
        assert len(fixed["resolved"]) >= 2
        assert len(fixed["preexisting"]) >= 1
        assert fixed["new"] == []
        assert fixed["after"]["content_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        await helper.close()
    try:
        run(check())
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=False, capture_output=True, timeout=10)
