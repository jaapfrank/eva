"""Optional, source-bound Python diagnostics in an existing programming sandbox.

The JSON-RPC framing is adapted from NousResearch/hermes-agent at
1f502aded81a07fa4127e6a90e06d04271a5b04a (agent/lsp/protocol.py).
Its copyright and MIT permission notice are retained in _hermes_lsp_protocol.
Only a fixed language-server command runs, inside the caller's existing sandbox.
This feedback does not replace tests or the independent component assessment.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import quote

from eva.operations._hermes_lsp_protocol import (
    classify_message, encode_message, make_error_response, make_notification,
    make_request, make_response, read_message,
)

_MAX_SOURCE = 1024 * 1024
_MAX_DIAGNOSTICS = 200
_EDIT_TOOLS = frozenset({"write_file", "edit_block"})


@dataclass(frozen=True)
class _Source:
    raw: bytes | None

    @property
    def sha256(self):
        return hashlib.sha256(self.raw).hexdigest() if self.raw is not None else None

    @property
    def text(self):
        # A BOM is an encoding marker, not Python source text. Keep newlines exact.
        return self.raw.decode("utf-8-sig") if self.raw is not None else ""


@dataclass(frozen=True)
class _Checkpoint:
    owner: object
    path: str
    source: _Source | None
    baseline: dict


def _target(name, arguments):
    if name not in _EDIT_TOOLS or not isinstance(arguments, dict):
        return None
    path = arguments.get("path")
    if (not isinstance(path, str) or not path.startswith("/workspace/")
            or not path.endswith(".py") or "\\" in path
            or any(ord(c) < 32 for c in path)):
        return None
    parts = PurePosixPath(path).parts
    if any(p in {".", ".."} for p in parts) or str(PurePosixPath(path)) != path:
        return None
    return path


def _read_source(workspace, path):
    """Walk by directory descriptors; never follow a workspace-created symlink."""
    descriptors = []
    try:
        current = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(current)
        parts = PurePosixPath(path).parts[2:]
        for part in parts[:-1]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=current)
            descriptors.append(current)
        try:
            source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=current)
        except FileNotFoundError:
            return _Source(None)
        descriptors.append(source)
        before = os.fstat(source)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_SOURCE:
            raise ValueError("source is not a bounded regular file")
        chunks, length = [], 0
        while chunk := os.read(source, min(65536, _MAX_SOURCE + 1 - length)):
            chunks.append(chunk)
            length += len(chunk)
            if length > _MAX_SOURCE:
                raise ValueError("source exceeds diagnostic bound")
        after = os.fstat(source)
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("source changed while reading")
        result = _Source(b"".join(chunks))
        result.text  # Reject undecodable source rather than analyze substituted bytes.
        return result
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _unavailable(reason, source=None, version=None):
    return {"status": "unavailable", "reason": reason,
            "content_sha256": source.sha256 if source else None,
            "document_version": version, "diagnostics": None}


def _diagnostic(value):
    if not isinstance(value, dict) or not isinstance(value.get("message"), str):
        raise ValueError("invalid diagnostic")
    result = {"message": value["message"][:4096]}
    for key in ("severity", "code", "source"):
        if key in value:
            if type(value[key]) not in (str, int):
                raise ValueError("invalid diagnostic metadata")
            result[key] = value[key] if type(value[key]) is int else value[key][:256]
    bounds = value.get("range")
    if not isinstance(bounds, dict):
        raise ValueError("missing diagnostic range")
    result["range"] = {}
    for key in ("start", "end"):
        position = bounds.get(key)
        if (not isinstance(position, dict) or any(type(position.get(p)) is not int
                or position[p] < 0 for p in ("line", "character"))):
            raise ValueError("invalid diagnostic position")
        result["range"][key] = {p: position[p] for p in ("line", "character")}
    return result


def _key(diagnostic):
    return json.dumps(diagnostic, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _shifted(diagnostic, before, after):
    """Map an unchanged line block, so inserted lines do not invent new errors."""
    result = {**diagnostic, "range": {k: dict(v) for k, v in diagnostic["range"].items()}}
    start, end = (diagnostic["range"][key]["line"] for key in ("start", "end"))
    for block in difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines()).get_matching_blocks():
        if block.a <= start <= end < block.a + block.size:
            for key in ("start", "end"):
                result["range"][key]["line"] += block.b - block.a
            break
    return result


class ProgrammingDiagnostics:
    """One optional LSP connection owned by the existing serial MCP actor.

    before/after return JSON-ready feedback; None means the tool/path is outside
    this bounded feature. Tool success remains the MCP result's responsibility.
    """
    def __init__(self, workspace: Path, container_name: str, *, timeout_seconds=5.0,
                 enabled=True):
        self.workspace = Path(workspace)
        if (not self.workspace.is_absolute() or self.workspace.resolve() != self.workspace
                or ".." in self.workspace.parts or self.workspace == Path("/")):
            raise ValueError("diagnostics require the existing nonsymlink workspace")
        if not re.fullmatch(r"eva-programming-[0-9a-f]{64}", container_name):
            raise ValueError("diagnostics require the existing programming container")
        if (type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30
                or type(enabled) is not bool):
            raise ValueError("invalid diagnostic configuration")
        self.container_name, self.timeout_seconds, self.enabled = container_name, timeout_seconds, enabled
        self._process = self._reader = None
        self._pending, self._documents = {}, {}
        self._next_id = 0
        # URI eviction must never reuse a version that a delayed push can echo.
        self._next_document_version = 0
        self._closed = False
        self._owner = object()

    async def before(self, name, arguments):
        path = _target(name, arguments)
        if path is None:
            return None
        try:
            source = _read_source(self.workspace, path)
        except (OSError, ValueError, UnicodeError):
            return _Checkpoint(self._owner, path, None, _unavailable("source_unreadable"))
        if source.raw is None:
            # Absence is known; it is not a clean language-server verdict.
            baseline = {"status": "absent", "content_sha256": None,
                        "document_version": None, "diagnostics": None}
        else:
            baseline = await self._assess(path, source)
        return _Checkpoint(self._owner, path, source, baseline)

    async def after(self, before, result):
        if before is None:
            return None
        if not isinstance(before, _Checkpoint) or before.owner is not self._owner:
            raise ValueError("diagnostic checkpoint belongs to another session")
        try:
            source = _read_source(self.workspace, before.path)
            after = (await self._assess(before.path, source) if source.raw is not None
                     else _unavailable("source_absent", source))
        except (OSError, ValueError, UnicodeError):
            source, after = None, _unavailable("source_unreadable")
        feedback = {"contract": "EvaProgrammingDiagnostics/v1", "path": before.path,
                    "server": "pyright", "before": before.baseline, "after": after,
                    "comparison_status": "unavailable", "preexisting": None,
                    "new": None, "resolved": None, "semantic_success_verified": False}
        if after["status"] == "available" and before.baseline["status"] in {"available", "absent"}:
            old = before.baseline["diagnostics"] or []
            shifted = [_shifted(d, before.source.text, source.text) for d in old]
            old_counts, new_counts = Counter(map(_key, shifted)), Counter(map(_key, after["diagnostics"]))
            feedback.update(comparison_status="available",
                preexisting=[d for d in after["diagnostics"] if old_counts[_key(d)]],
                new=[json.loads(k) for k in (new_counts - old_counts).elements()],
                resolved=[old[i] for i, d in enumerate(shifted) if not new_counts[_key(d)]])
        return feedback

    async def _assess(self, path, source):
        if not self.enabled:
            return _unavailable("disabled", source)
        if self._closed:
            return _unavailable("closed", source)
        version = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self._start()
                uri = "file://" + quote(path, safe="/")
                old = self._documents.get(uri)
                self._next_document_version += 1
                version = self._next_document_version
                future = asyncio.get_running_loop().create_future()
                if old is None and len(self._documents) >= 64:
                    retired = next(iter(self._documents))
                    del self._documents[retired]
                    await self._write(make_notification("textDocument/didClose", {
                        "textDocument": {"uri": retired}}))
                doc = {"version": version, "future": future, "diagnostics": None,
                       "unbound_report": False}
                self._documents[uri] = doc
                if old:
                    await self._write(make_notification("textDocument/didChange", {
                        "textDocument": {"uri": uri, "version": version},
                        "contentChanges": [{"text": source.text}]}))
                else:
                    await self._write(make_notification("textDocument/didOpen", {"textDocument": {
                        "uri": uri, "languageId": "python", "version": version, "text": source.text}}))
                await self._write(make_notification("textDocument/didSave", {"textDocument": {"uri": uri}}))
                await future
                # Match Hermes' short push debounce: an immediate second report
                # replaces an initial report for this same document version.
                await asyncio.sleep(0.15)
                if self._reader.done():
                    raise RuntimeError("language server connection closed")
                diagnostics = doc["diagnostics"]
                if _read_source(self.workspace, path) != source:
                    return _unavailable("source_changed", source, version)
                return {"status": "available", "content_sha256": source.sha256,
                        "document_version": version, "diagnostics": diagnostics}
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            doc = self._documents.get("file://" + quote(path, safe="/"))
            reason = "unbound_diagnostics" if doc and doc["unbound_report"] else "timeout"
            return _unavailable(reason, source, version)
        except Exception:
            return _unavailable("server_unavailable", source, version)

    async def _start(self):
        if self._process is not None:
            if self._process.returncode is not None or self._reader.done():
                raise RuntimeError("language server closed")
            return
        self._process = await asyncio.create_subprocess_exec("docker", "exec", "-i",
            self.container_name, "pyright-langserver", "--stdio",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, limit=65536)
        self._reader = asyncio.create_task(self._read())
        await self._request("initialize", {"processId": None, "rootUri": "file:///workspace",
            "workspaceFolders": [{"uri": "file:///workspace", "name": "workspace"}],
            "capabilities": {"workspace": {"configuration": True}, "textDocument": {
                "publishDiagnostics": {"versionSupport": True}}}})
        await self._write(make_notification("initialized", {}))

    async def _write(self, message):
        self._process.stdin.write(encode_message(message))
        await self._process.stdin.drain()

    async def _request(self, method, params):
        self._next_id += 1
        key = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[key] = future
        try:
            await self._write(make_request(key, method, params))
            return await future
        finally:
            self._pending.pop(key, None)

    async def _read(self):
        try:
            while (message := await read_message(self._process.stdout)) is not None:
                kind, key = classify_message(message)
                if kind == "response":
                    future = self._pending.get(key)
                    if future is not None and not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError("language server request failed"))
                        else:
                            future.set_result(message.get("result"))
                elif kind == "request":
                    method, params = message.get("method"), message.get("params") or {}
                    if method == "workspace/configuration":
                        # Fixed analysis policy; model-controlled project config grants no host ability.
                        config = {"analysis": {"typeCheckingMode": "basic", "diagnosticMode": "openFilesOnly"}}
                        result = [config if item.get("section") == "python" else
                                  config["analysis"] if item.get("section") == "python.analysis" else None
                                  for item in params.get("items", [])]
                        reply = make_response(key, result)
                    elif method in {"client/registerCapability", "window/workDoneProgress/create"}:
                        reply = make_response(key, None)
                    elif method == "workspace/workspaceFolders":
                        reply = make_response(key, [{"uri": "file:///workspace", "name": "workspace"}])
                    else:
                        reply = make_error_response(key, -32601, "method not supported")
                    await self._write(reply)
                elif kind == "notification" and key == "textDocument/publishDiagnostics":
                    params = message.get("params") or {}
                    doc = self._documents.get(params.get("uri"))
                    # Versionless and older/future reports cannot prove this source version.
                    if doc is not None and (type(params.get("version")) is not int
                            or params["version"] != doc["version"]):
                        doc["unbound_report"] = True
                    elif doc is not None:
                        values = params.get("diagnostics")
                        if not isinstance(values, list) or len(values) > _MAX_DIAGNOSTICS:
                            raise ValueError("diagnostic bound exceeded")
                        else:
                            normalized = {_key(item): item for item in map(_diagnostic, values)}
                            doc["diagnostics"] = list(normalized.values())
                            if not doc["future"].done():
                                doc["future"].set_result(None)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            for future in [*self._pending.values(), *(d["future"] for d in self._documents.values())]:
                if not future.done():
                    future.set_exception(RuntimeError("language server connection closed"))

    async def close(self):
        self._closed = True
        process = self._process
        if process is None:
            return
        try:
            if process.returncode is None:
                with suppress(Exception):
                    async with asyncio.timeout(0.5):
                        await self._request("shutdown", None)
                        await self._write(make_notification("exit", None))
                        process.stdin.close()
                        await process.wait()
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 0.5)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
        finally:
            if self._reader is not None:
                self._reader.cancel()
                await asyncio.gather(self._reader, return_exceptions=True)
