"""Bounded suspend/replay protocol for the existing offline Python executor.

Only host-validated receipts belong in history. A frame is untrusted program
data, never authority, effect proof or task completion. The caller owns current
authorization, STOP, durable subcall identity, budgets and receipt verification.

The serialized ``_call`` stub adapts Hermes' MIT code_execution_tool.py
_UDS_TRANSPORT_HEADER._call at revision 1f502aded81a07fa4127e6a90e06d04271a5b04a.
Socket/token/reconnect dispatch is replaced by exact cached-prefix replay and
one suspended request. See src/vendor/hermes-tool-program for full provenance.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re


MAX_SOURCE_BYTES = 16384
MAX_INPUT_BYTES = 32768
MAX_FRAME_BYTES = 16384
INPUT_CONTRACT = "EvaToolProgramInput/v1"
FRAME_CONTRACT = "EvaToolProgramFrame/v1"

_MIT_NOTICE = """MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


class ToolProgramRejected(ValueError):
    """The program, input, replay prefix or executor report is not usable."""


def _encoded(value) -> bytes:
    def validate(item, depth=0):
        if depth > 64:
            raise ToolProgramRejected("program JSON nesting exceeds bound")
        if item is None or type(item) in (bool, int, float, str):
            return
        if type(item) is list:
            for child in item:
                validate(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child, depth + 1)
            return
        raise ToolProgramRejected("program data must be JSON values with string keys")
    try:
        validate(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, OverflowError, RecursionError) as error:
        raise ToolProgramRejected("program data is not bounded valid JSON") from error


def _name_arguments(name, arguments):
    if (not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,119}", name) is None
            or type(arguments) is not dict):
        raise ToolProgramRejected("program call requires a tool name and argument object")
    _encoded(arguments)


def build_program_input(input_data, history) -> dict:
    """Copy the original input and exact ordered host receipt outputs.

    A history entry is ``{name, arguments, output}``. The byte bound includes
    every earlier output; callers must reject oversize history without truncation.
    """
    if type(history) is not list:
        raise ToolProgramRejected("program history must be an ordered list")
    for item in history:
        if type(item) is not dict or set(item) != {"name", "arguments", "output"}:
            raise ToolProgramRejected("program history entry differs")
        _name_arguments(item["name"], item["arguments"])
    raw = _encoded({"contract": INPUT_CONTRACT, "input_data": input_data, "history": history})
    if len(raw) > MAX_INPUT_BYTES:
        raise ToolProgramRejected("program input plus history exceeds 32768 bytes")
    return json.loads(raw)


_BOOTSTRAP = r'''
import base64 as _base64, contextlib as _contextlib, hashlib as _hashlib
import io as _io, json as _json, re as _re, sys as _sys, threading as _threading, types as _types

def _encoded(value):
    def valid(item, depth=0):
        if depth>64:raise ValueError("program JSON nesting exceeds bound")
        if item is None or type(item) in (bool,int,float,str):return
        if type(item) is list:
            for child in item:valid(child,depth+1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():valid(child,depth+1)
            return
        raise ValueError("program data must be JSON values")
    valid(value)
    return _json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode("utf-8")

class _Suspend(BaseException):pass
class _Failure(BaseException):pass
_request=_json.load(_sys.stdin)
if set(_request)!={"contract","input_data","history"} or _request["contract"]!="EvaToolProgramInput/v1":
    raise _Failure("program input differs")
_raw_input=_encoded(_request)
if len(_raw_input)>32768:raise _Failure("program input exceeds bound")
_history=_request["history"]
if type(_history) is not list:raise _Failure("program history differs")
_index=0;_pending=None;_fault=None
# Adapted from Hermes' serialized _call: one request/response at a time.
_call_lock=_threading.Lock()

def _call(tool_name,args):
    global _index,_pending,_fault
    with _call_lock:
        if _pending is not None:raise _Suspend()
        try:
            if not isinstance(tool_name,str) or _re.fullmatch(r"[a-z][a-z0-9_]{0,119}",tool_name) is None or type(args) is not dict:
                raise ValueError("invalid program call")
            request={"name":tool_name,"arguments":args}
            _encoded(request)
            if _index<len(_history):
                saved=_history[_index]
                if type(saved) is not dict or set(saved)!={"name","arguments","output"} or _encoded(request)!=_encoded({"name":saved["name"],"arguments":saved["arguments"]}):
                    raise ValueError("program replay prefix differs")
                result=_json.loads(_encoded(saved["output"]))
                _index+=1
                return result
            _pending={"kind":"call","index":_index,**request}
        except (ValueError,TypeError,UnicodeError,OverflowError,RecursionError):
            _fault="program call or replay prefix differs"
            raise _Failure(_fault)
        raise _Suspend()

def _call_named(name,**arguments):return _call(name,arguments)
_tools=_types.ModuleType("eva_tools")
_tools.call=_call_named;_tools.call_tool=_call;_tools.input_data=_request["input_data"]
_sys.modules["eva_tools"]=_tools

class _Output(_io.StringIO):
    def __init__(self):super().__init__();self.bytes=0
    def write(self,value):
        global _fault
        self.bytes+=len(value.encode("utf-8"))
        if self.bytes>_FRAME_LIMIT:
            if _fault is None:_fault="program stdout exceeds bound"
            raise _Failure(_fault)
        return super().write(value)

_output=_Output();_program_error=None
_globals={"__name__":"__main__","input_data":_request["input_data"],"eva_tools":_tools}
with _contextlib.redirect_stdout(_output):
    try:exec(compile(_base64.b64decode(_USER_SOURCE).decode("utf-8"),"<eva-tool-program>","exec"),_globals)
    except _Suspend:pass
    except _Failure:
        if _fault!="program stdout exceeds bound":raise
    except Exception as error:
        _program_error={"code":"user_exception","hint":type(error).__name__+": "+str(error)[:512]}
if _fault is not None and _fault!="program stdout exceeds bound":raise _Failure(_fault)
if _index!=len(_history):raise _Failure("program did not consume the complete replay prefix")
# Expected local failures are data, never result/effect evidence. Bind them to
# the same complete cached prefix; integrity or incomplete replay stays hard.
if _fault=="program stdout exceeds bound":
    _program_error={"code":"stdout_bound","hint":"Print a short selected result; do not print full receipt or source objects."}
_frame=({"kind":"error",**_program_error} if _program_error is not None else
    _pending if _pending is not None else {"kind":"result","output":_output.getvalue()})
_binding={"contract":"EvaToolProgramFrame/v1","history_length":_index,
    "source_sha256":_hashlib.sha256(open(__file__,"rb").read()).hexdigest(),
    "input_sha256":_hashlib.sha256(_raw_input).hexdigest()}
_frame.update(_binding)
_wire=_encoded(_frame)+b"\n"
if len(_wire)>_FRAME_LIMIT:
    _wire=_encoded({**_binding,"kind":"error","code":"frame_bound",
        "hint":"Encoded output exceeds the frame bound. Print a short selected result."})+b"\n"
_sys.stdout.buffer.write(_wire);_sys.stdout.buffer.flush()
'''


def build_program_source(user_source: str, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> str:
    """Wrap Python source without interpolating it as executable framework code."""
    if type(max_frame_bytes) is not int or not 1024 <= max_frame_bytes <= MAX_FRAME_BYTES:
        raise ToolProgramRejected("program output bound is invalid")
    if not isinstance(user_source, str) or not user_source.strip():
        raise ToolProgramRejected("program source must be nonempty UTF-8 Python")
    try:
        raw = user_source.encode("utf-8")
        compile(user_source, "<eva-tool-program>", "exec")
    except (UnicodeError, ValueError, SyntaxError, RecursionError) as error:
        raise ToolProgramRejected("program source is invalid") from error
    notice = "".join("# " + line + "\n" for line in _MIT_NOTICE.splitlines())
    source = notice + "_USER_SOURCE=" + repr(base64.b64encode(raw).decode("ascii")) + "\n_FRAME_LIMIT=" + str(max_frame_bytes) + "\n" + _BOOTSTRAP
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ToolProgramRejected("program plus wrapper exceeds 16384 bytes")
    return source


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ToolProgramRejected("program frame contains duplicate fields")
        result[key] = value
    return result


def parse_program_report(report, *, source: str, input_data: dict, image_id: str) -> dict:
    """Bind one complete isolated report, then return only its untrusted frame.

    ``source`` and ``input_data`` are the exact outputs of the builders. No
    program frame authorizes dispatch or proves a service write succeeded.
    """
    try:
        source_bytes = source.encode("utf-8")
        if not 1 <= len(source_bytes) <= MAX_SOURCE_BYTES:
            raise ToolProgramRejected("program source binding exceeds bound")
        if (not isinstance(input_data, dict) or set(input_data) != {"contract", "input_data", "history"}
                or input_data["contract"] != INPUT_CONTRACT
                or _encoded(input_data) != _encoded(build_program_input(
                    input_data["input_data"], input_data["history"]))):
            raise ToolProgramRejected("program input binding differs")
        if not isinstance(image_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
            raise ToolProgramRejected("program image binding is invalid")
        source_sha, input_sha = hashlib.sha256(source_bytes).hexdigest(), hashlib.sha256(_encoded(input_data)).hexdigest()
        if (type(report) is not dict or report.get("contract") != "EvaIsolatedProcedureExecution/v1"
                or report.get("image_id") != image_id or report.get("source_sha256") != source_sha
                or report.get("input_sha256") != input_sha):
            raise ToolProgramRejected("program execution report binding differs")
        result = report["result"]
        if (type(result) is not dict or type(result.get("exit_code")) is not int
                or result["exit_code"] != 0
                or any(result.get(key) is not False for key in (
                    "timed_out", "cancelled", "stdout_truncated", "stderr_truncated"))
                or not isinstance(result.get("stdout"), str) or not isinstance(result.get("stderr"), str)
                or len(result["stdout"].encode("utf-8")) > MAX_FRAME_BYTES
                or len(result["stderr"].encode("utf-8")) > MAX_FRAME_BYTES):
            raise ToolProgramRejected("program execution did not produce a complete result")
        frame = json.loads(result["stdout"], object_pairs_hook=_unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ToolProgramRejected("nonfinite frame")))
        _encoded(frame)
        common = {"contract", "source_sha256", "input_sha256", "history_length", "kind"}
        if (type(frame) is not dict or frame.get("contract") != FRAME_CONTRACT
                or frame.get("source_sha256") != source_sha or frame.get("input_sha256") != input_sha
                or type(frame.get("history_length")) is not int
                or frame["history_length"] != len(input_data["history"])):
            raise ToolProgramRejected("program frame binding or replay prefix differs")
        if frame["kind"] == "call" and set(frame) == common | {"index", "name", "arguments"}:
            if type(frame["index"]) is not int or frame["index"] != len(input_data["history"]):
                raise ToolProgramRejected("program call index differs")
            _name_arguments(frame["name"], frame["arguments"])
            return {key: frame[key] for key in ("kind", "index", "name", "arguments")}
        if frame["kind"] == "result" and set(frame) == common | {"output"} and isinstance(frame["output"], str):
            return {"kind": "result", "output": frame["output"]}
        if (frame["kind"] == "error" and set(frame) == common | {"code", "hint"}
                and frame["code"] in {"user_exception", "stdout_bound", "frame_bound"}
                and isinstance(frame["hint"], str) and len(frame["hint"].encode("utf-8")) <= 4096):
            return {key: frame[key] for key in ("kind", "code", "hint")}
        raise ToolProgramRejected("program frame shape differs")
    except (KeyError, TypeError, AttributeError, ValueError, UnicodeError, OverflowError, RecursionError) as error:
        raise ToolProgramRejected("program report is unavailable or invalid") from error


__all__ = ["ToolProgramRejected", "build_program_source", "build_program_input", "parse_program_report"]
