"""One registered host evaluation through the ordinary digital factory and store.

No scoring, scheduler, retries, owner-message impersonation or trial controls.
The same file runs only in a transient container to observe native transports.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import contextvars
from copy import deepcopy
import dataclasses
from datetime import UTC, datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from eva.contracts.canonical_json import canonical_sha256

_ACTIVE = contextvars.ContextVar("eva_registered_component_work", default=None)
_ALLOWED_ACTIONS = frozenset({"list_programming_tools", "use_programming_tool",
    "write_programming_file", "start_programming_process", "save_work_progress",
    "native_programming_idle_check", "host_isolated_check"})
_RUNTIME_ROOT = "/eva-component-producer"


class ComponentWorkProductionError(RuntimeError):
    """A safe failure without source, secrets or unmeasured-success claims."""

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()

def sha(raw):return hashlib.sha256(raw).hexdigest()

def require(test,message):
    if not test:raise ComponentWorkProductionError(message)

def read_private(path,*,uid=None,limit=8*1024*1024):
    uid=os.geteuid() if uid is None else uid
    path=Path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        st=os.fstat(fd);require(stat.S_ISREG(st.st_mode) and st.st_uid==uid and st.st_nlink==1 and stat.S_IMODE(st.st_mode) in {0o400,0o600} and st.st_size<=limit,"untrusted private input")
        data=os.read(fd,limit+1);require(len(data)<=limit,"input exceeds bound");return data
    finally:os.close(fd)

def exclusive(path,value,*,uid=None,mode=0o600,raw=False):
    uid=os.geteuid() if uid is None else uid
    path=Path(path);data=value if raw else canonical(value)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    try:
        os.fchown(fd,uid,uid)
        with os.fdopen(fd,"wb",closefd=False) as stream:stream.write(data);stream.flush();os.fsync(fd)
    finally:os.close(fd)
    directory=os.open(path.parent,os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)
    return sha(data)

def directory(path,uid=None):
    uid=os.geteuid() if uid is None else uid
    path=Path(path)
    if not path.exists():path.mkdir(mode=0o700);os.chown(path,uid,uid)
    st=path.lstat();require(stat.S_ISDIR(st.st_mode) and st.st_uid==uid and stat.S_IMODE(st.st_mode)==0o700,"unsafe private directory");return path

def env_map(values):
    pairs=[v.split("=",1) for v in values];require(all(len(p)==2 for p in pairs) and len({p[0] for p in pairs})==len(pairs),"duplicate/invalid environment keys");return dict(pairs)

def verify_launch(actual,plan):
    require(actual["Image"]==plan["image"] and set(actual["NetworkSettings"]["Networks"])==set(plan["network_attachments"]),"image/network changed")
    c=actual["Config"];require(env_map(c["Env"])==env_map(plan["environment"]),"environment changed")
    for k,v in (("User",plan["user"]),("WorkingDir",plan["working_dir"]),("Cmd",plan["command"]),("Entrypoint",plan["entrypoint"])):require(c[k]==v,"runner config changed")
    require(c.get("Healthcheck",{}).get("Test")==["NONE"],"unexpected healthcheck")
    got={m["Destination"]:(m["Type"],m["Name"] if m["Type"]=="volume" else m["Source"],not m["RW"]) for m in actual["Mounts"]}
    want={m["target"]:(m["type"],m["source"],m["read_only"]) for m in plan["mounts"]}
    require(len(got)==len(actual["Mounts"]) and len(want)==len(plan["mounts"]) and got==want,"exact mount binding differs")
    h=actual["HostConfig"];require(h["ReadonlyRootfs"] and h["CapDrop"]==["ALL"] and h["SecurityOpt"]==["no-new-privileges:true"] and not h["Privileged"] and not h["PortBindings"],"container privilege changed")

def check_record(report,check_id):
    result=report["result"]
    report_sha256=sha(canonical(report))
    if result.get("stdout_truncated") or result.get("stderr_truncated") or result.get("cancelled"):
        # Limited original output remains in the raw report; it is not a complete
        # check outcome and must not accidentally pass the fixed scorer.
        return {"check_id":check_id,"status":"incomplete","program_sha256":report["source_sha256"],"stdin_sha256":report["input_sha256"],"actual_report_sha256":report_sha256,"receipt_sha256":report_sha256,"actual":{k:result[k] for k in ("status","stdout_truncated","stderr_truncated","cancelled","timed_out") if k in result}}
    return {"check_id":check_id,"program_sha256":report["source_sha256"],"stdin_sha256":report["input_sha256"],"stdout":result["stdout"],"stderr":result["stderr"],"exit_code":result["exit_code"],"timed_out":result["timed_out"],"receipt_sha256":report_sha256}

def usage_export(raw):
    rows=raw["physical_requests"]
    require(isinstance(rows,list) and all(r["usage"] is not None for r in rows),"physical usage incomplete")
    if not rows:
        journal=raw["journal_usage"]
        require(raw.get("complete_physical_usage") is True and raw.get("original_journal_zero_requests_verified") is True and journal["measurement"]=="unmeasured" and all(journal[k] is None for k in ("model_attempts","input_tokens","output_tokens","tool_calls")),"empty physical export lacks original zero-request attestation")
        return {"measurement":"measured","provider_attempts":0,"input_tokens":0,"output_tokens":0,"total_tokens":0,"unknown_attempts":0,"request_ids":[],"roles":[]}
    ids=[r["request_id"] for r in rows];require(len(ids)==len(set(ids)),"physical request replay")
    roles={}
    for row in rows:
        key=(row["role"],row["model"],row["reasoning_effort"]);roles[key]=roles.get(key,0)+1
    usage={"measurement":"measured","provider_attempts":len(rows),"input_tokens":sum(r["usage"]["input_tokens"] for r in rows),"output_tokens":sum(r["usage"]["output_tokens"] for r in rows),"unknown_attempts":0,"request_ids":ids,"roles":[{"role":k[0],"model":k[1],"reasoning_effort":k[2],"attempts":v} for k,v in sorted(roles.items())]}
    usage["total_tokens"]=usage["input_tokens"]+usage["output_tokens"]
    journal=raw["journal_usage"]
    require(journal["measurement"]=="measured" and journal["model_attempts"]==usage["provider_attempts"] and journal["input_tokens"]==usage["input_tokens"] and journal["output_tokens"]==usage["output_tokens"],"original model journal and physical transport usage differ")
    return usage

def original_cleanup_verified(raw):
    session_key=raw.get("programming_session_key")
    if not isinstance(session_key,str) or not session_key:return False
    for receipt in raw.get("control_receipts",[]):
        if receipt.get("identity")!=raw["identity"] or receipt.get("session_key")!=session_key:continue
        result=receipt.get("result",{})
        if receipt.get("kind")=="idle" and result.get("idle") is True:return True
        if receipt.get("kind")=="close" and (result=={"closed":True} and result.get("closed") is True or result.get("status")=="closed" and result.get("workspace_preserved") is True):return True
    return False


def require_original_cleanup(raw):
    finalizers = [r for r in raw["control_receipts"] if r["kind"] == "programming_finally"
        and r["identity"] == raw["identity"] and r["session_key"] == raw["programming_session_key"]]
    require(len(finalizers) == 1 and type(finalizers[0]["started"]) is bool, "original programming finally state unknown")
    require(not finalizers[0]["started"] or original_cleanup_verified(raw), "started programming lacks original idle or close proof")


def _direct_program_command(command):
    # Recognize one literal invocation, not arbitrary shell execution.
    if not isinstance(command, str) or any(c in command for c in "\n\r\0$\\#" + chr(96)):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False
    if tokens[:3] == ["cd", "/workspace", "&&"]:
        tokens = tokens[3:]
    if len(tokens) < 2 or tokens[0] not in {"python", "python3", "/usr/local/bin/python", "/usr/local/bin/python3"} or tokens[1] not in {"main.py", "./main.py", "/workspace/main.py"}:
        return False
    tail, seen = tokens[2:], set()
    while tail:
        if len(tail) < 2 or tail[0] not in {"<", ">"} or tail[0] in seen:
            return False
        seen.add(tail[0])
        path = tail[1]
        if not re.fullmatch(r"(?:/workspace/)?[A-Za-z0-9][A-Za-z0-9_.-]*", path) or path.rsplit("/", 1)[-1] == "main.py":
            return False
        tail = tail[2:]
    return True


def _program_result_text(receipt):
    output = receipt.get("output", {})
    if not isinstance(output, dict):
        return None
    result = output.get("programming_result", {})
    if output.get("status") != "verified" or output.get("workspace") != "/workspace" or not isinstance(result, dict) or result.get("isError") is not False:
        return None
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict) or content[0].get("type") != "text" or not isinstance(content[0].get("text"), str):
        return None
    return content[0]["text"]


class _NativeProgramObserver:
    """Source evidence from successful original write/start/exit receipts only."""
    _READ_ONLY = {"read_file", "read_multiple_files", "list_directory", "read_process_output",
        "get_file_info", "list_processes", "list_sessions"}

    def __init__(self):
        self.rows = []
        self.scope = None
        self.content = None
        self.write_steps = []
        self.pending = {}
        self.unknown_start = False

    def _invalidate(self):
        self.content, self.write_steps = None, []
        for pending in self.pending.values():
            pending["source_sha256"] = None

    def observe(self, receipt, identity, step_index, session_key, workspace_key):
        row = {"identity": deepcopy(identity), "step_index": step_index,
            "native_program_source_sha256": None, "receipt": deepcopy(receipt)}
        self.rows.append(row)
        if not session_key or not workspace_key:
            return row
        row.update(programming_session_key=session_key, workspace_key=workspace_key)
        scope = (canonical_sha256(identity), session_key, workspace_key)
        if scope != self.scope:
            self.scope, self.content, self.write_steps = scope, None, []
            self.pending, self.unknown_start = {}, False
        call = receipt.get("call", {})
        args = call.get("arguments", {})
        tool = args.get("tool_name") if call.get("name") == "use_programming_tool" else None
        try:
            native_args = json.loads(args["arguments_json"]) if tool else args
        except (KeyError, TypeError, ValueError):
            self._invalidate()
            self.unknown_start = True
            return row
        if not isinstance(native_args, dict):
            self._invalidate()
            return row
        text = _program_result_text(receipt)
        lines = text.splitlines() if text is not None else []
        if call.get("name") == "write_programming_file" or tool == "write_file":
            self._invalidate_pending_writes(native_args, text, lines, step_index)
        elif call.get("name") == "start_programming_process" or tool == "start_process":
            match = re.fullmatch(r"Process started with PID ([1-9]\d*) \(shell: /bin/(?:bash|sh)\)", lines[0].strip()) if lines else None
            source = sha(self.content.encode()) if self.content is not None and not self.pending and not self.unknown_start and _direct_program_command(native_args.get("command")) else None
            writes = list(self.write_steps)
            if self.pending:
                self._invalidate()
            # The invoked program may itself change files; never infer another start.
            self.content, self.write_steps = None, []
            if match and int(match[1]) not in self.pending:
                self.pending[int(match[1])] = {"row": row, "source_sha256": source, "write_steps": writes}
            else:
                self._invalidate()
                self.unknown_start = True
        elif tool == "read_process_output":
            pid = native_args.get("pid")
            header = re.fullmatch(r"\[Reading \d+ new lines \(total: \d+ lines\)\]", lines[0].strip()) if lines else None
            footer = re.fullmatch(r"\u2705 Process completed with exit code (-?\d+) \(runtime: \d+(?:\.\d+)?s\)", lines[-1].strip()) if lines else None
            markers = sum("Process completed with exit code " in line for line in lines)
            if type(pid) is int and pid in self.pending and header and footer and markers == 1:
                pending = self.pending.pop(pid)
                if pending["source_sha256"] is not None:
                    pending["row"].update(native_program_source_sha256=pending["source_sha256"],
                        native_program_write_steps=pending["write_steps"], native_program_pid=pid,
                        native_program_exit_step_index=step_index, native_program_exit_code=int(footer[1]))
        elif tool and tool not in self._READ_ONLY:
            self._invalidate()
            self.unknown_start = True
        return row

    def _invalidate_pending_writes(self, args, text, lines, step):
        before, writes = self.content, list(self.write_steps)
        self._invalidate()
        if self.pending or self.unknown_start or args.get("path") != "/workspace/main.py" or not isinstance(args.get("content"), str):
            return
        mode = args.get("mode", "rewrite")
        verb = "wrote" if mode == "rewrite" else "appended" if mode == "append" else None
        if not verb or text is None or not lines or not re.fullmatch(r"Successfully " + verb + r" to /workspace/main[.]py \(\d+ lines\)", lines[0].strip()):
            return
        if mode == "rewrite":
            self.content, self.write_steps = args["content"], [step]
        elif before is not None:
            self.content, self.write_steps = before + args["content"], writes + [step]


def _native_program_executions(raw, program_sha256):
    if program_sha256 is None:
        return []
    rows = raw["action_receipts"]
    steps = [row["step_index"] for row in rows]
    if any(type(step) is not int for step in steps) or steps != sorted(set(steps)):
        return []
    observer = _NativeProgramObserver()
    for row in rows:
        if row.get("identity") != raw["identity"]:
            return []
        session, workspace = row.get("programming_session_key"), row.get("workspace_key")
        if session and (session != raw["programming_session_key"] or workspace != raw["workspace_key"]):
            return []
        observer.observe(row["receipt"], row["identity"], row["step_index"], session, workspace)
    actual = {row["step_index"]: row for row in rows}
    executions = []
    fields = ("native_program_source_sha256", "native_program_write_steps", "native_program_pid",
        "native_program_exit_step_index", "native_program_exit_code")
    for row in observer.rows:
        if row.get("native_program_source_sha256") != program_sha256 or any(row.get(key) != actual[row["step_index"]].get(key) for key in fields):
            continue
        receipt = row["receipt"]
        exit_row = actual[row["native_program_exit_step_index"]]
        executions.append({"step_index": row["step_index"], "source_sha256": program_sha256,
            "action_name": receipt["call"]["name"], "receipt_sha256": canonical_sha256(receipt), "receipt": receipt,
            "pid": row["native_program_pid"], "exit_code": row["native_program_exit_code"],
            "exit_step_index": exit_row["step_index"], "exit_receipt_sha256": canonical_sha256(exit_row["receipt"]),
            "exit_receipt": exit_row["receipt"], "write_receipt_sha256s":
                [canonical_sha256(actual[step]["receipt"]) for step in row["native_program_write_steps"]]})
    return executions


class PhysicalAccounting:
    def __init__(self, root, policy, *, execution_enabled=False, ledger_uid=None, host_binding=False):
        self.root = Path(root)
        self.policy = dict(policy)
        self.execution_enabled = execution_enabled
        self.ledger_uid = os.geteuid() if ledger_uid is None else ledger_uid
        self.host_binding = host_binding and os.geteuid() == 0
        if self.ledger_uid != os.geteuid() and os.geteuid() != 0:
            raise ComponentWorkProductionError("only host root may seal the runtime-owned ledger")
        self.authority_check = None
        self.authorization_checks = []
        self.program_observer = _NativeProgramObserver()
        for name in ("total_provider_attempts", "case_provider_attempts", "total_tokens", "total_tool_calls"):
            if type(self.policy.get(name)) is not int or self.policy[name] < 1:
                raise ComponentWorkProductionError("invalid registered work resource binding")
        if self.policy.get("retry_attempts") != 0:
            raise ComponentWorkProductionError("retry is not admitted")
        self.policy_sha256 = hashlib.sha256(json.dumps(self.policy,ensure_ascii=False,
            sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
        self.native_projection_check = None
        self.receipts = self.program_observer.rows
        self.journals = {}
        self.control_receipts = []

    @contextlib.contextmanager
    def case(self, identity):
        if not self.execution_enabled:
            raise ComponentWorkProductionError("technical preparation has no work-input admission")
        label = identity.get("leg")
        key = ":".join((str(label), str(identity.get("stage")), str(identity.get("case_id"))))
        bound = {k:v for k,v in identity.items() if k != "source_sha256"}
        if self.policy.get("case_bindings", {}).get(key) != bound:
            raise ComponentWorkProductionError("task/session/ingress identities are not host-prebound")
        if identity["scope_sha256"] != self.policy["scope_sha256"]:
            raise ComponentWorkProductionError("case scope differs")
        with self._ledger() as state:
            expected = self.policy["baseline_sha256"] if label == "baseline" else state.get("candidate_sha256") if label == "candidate" else None
            if expected is None or identity["source_sha256"] != expected:
                raise ComponentWorkProductionError("source not bound to this single candidate")
        token = _ACTIVE.set(identity)
        try:
            yield
        finally:
            _ACTIVE.reset(token)

    @contextlib.contextmanager
    def _ledger(self):
        if not self.execution_enabled and not self.host_binding:
            raise ComponentWorkProductionError("provider and tool dispatch disabled during preparation")
        if not self.root.exists():
            self.root.mkdir(mode=0o700, parents=False)
            if os.geteuid()==0:os.chown(self.root,self.ledger_uid,self.ledger_uid)
        if self.root.is_symlink() or self.root.stat().st_uid != self.ledger_uid or self.root.stat().st_mode & 0o777 != 0o700:
            raise ComponentWorkProductionError("unsafe private accounting root")
        if os.geteuid() == 0:
            os.chown(self.root,self.ledger_uid,self.ledger_uid)
        # The stable lock inode is separate from atomic receipt replacement.
        lock_path = self.root / "accounting.lock"
        try:
            descriptor = os.open(lock_path,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600)
            if os.geteuid() == 0: os.fchown(descriptor,self.ledger_uid,self.ledger_uid)
        except FileExistsError:
            descriptor = os.open(lock_path,os.O_RDWR|os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r+") as lock:
            st = os.fstat(lock.fileno())
            if st.st_uid != self.ledger_uid or st.st_nlink != 1 or st.st_mode & 0o777 != 0o600:
                raise ComponentWorkProductionError("unsafe stable accounting lock")
            fcntl.flock(lock, fcntl.LOCK_EX)
            state_file = self.root / "accounting.json"
            if state_file.exists():
                if state_file.is_symlink() or state_file.stat().st_uid != self.ledger_uid or state_file.stat().st_mode & 0o777 != 0o600 or state_file.stat().st_nlink != 1:
                    raise ComponentWorkProductionError("unsafe accounting receipt")
                state = json.loads(state_file.read_bytes())
                if state["registration_sha256"] != self.policy["registration_sha256"] or state.get("policy_sha256") != self.policy_sha256:
                    raise ComponentWorkProductionError("accounting registration differs; never reset it")
            else:
                state = {"registration_sha256":self.policy["registration_sha256"], "policy_sha256":self.policy_sha256, "candidate_sha256":self.policy["candidate_sha256"],"candidate_source_validation":None,"started_cases":[],"requests":[], "tools":[], "cleanup_reservations":[]}
            yield state
            fd, temporary = tempfile.mkstemp(dir=self.root)
            try:
                if os.geteuid() == 0: os.fchown(fd,self.ledger_uid,self.ledger_uid)
                with os.fdopen(fd,"wb") as stream:
                    stream.write(json.dumps(state,sort_keys=True,separators=(",", ":")).encode())
                    stream.flush(); os.fsync(stream.fileno())
                os.replace(temporary,state_file)
                parent = os.open(self.root,os.O_DIRECTORY)
                try: os.fsync(parent)
                finally: os.close(parent)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)


    def start_case_once(self, identity):
        with self.case(identity):
            with self._ledger() as state:
                key=":".join((identity["leg"],identity["stage"],identity["case_id"]))
                if key in state["started_cases"]:raise ComponentWorkProductionError("case already started; no replay/retry")
                state["started_cases"].append(key)

    def ensure_rankable(self):
        with self._ledger() as state:
            if any(row["usage"] is None for row in state["requests"]):raise ComponentWorkProductionError("unknown attempt prevents rankable sealing")
            if any(row["phase"] not in {"finalized", "not_started"} for row in state["cleanup_reservations"]):raise ComponentWorkProductionError("original programming cleanup is not observed")
            if sum(row["usage"]["total_tokens"] for row in state["requests"])>self.policy["total_tokens"] or len(state["requests"])>self.policy["total_provider_attempts"] or len(state["tools"])>self.policy["total_tool_calls"]:raise ComponentWorkProductionError("measured shared overrun prevents rankable sealing")
            return state

    def _current_authority(self, *, kind):
        if self.authority_check is None:raise ComponentWorkProductionError("current native authority callback absent")
        evidence=self.authority_check()
        require(isinstance(evidence, dict) and evidence.get("authorization_current") is True
            and evidence.get("source_scope_valid") is True and evidence.get("stop_respected") is True,
            "original scoped authority unconfirmed")
        self.authorization_checks.append({"identity":dict(_ACTIVE.get()),"kind":kind,**evidence})
        return evidence


    @staticmethod
    def _case_requests(rows, context):
        return [row for row in rows if row["identity"]["source_sha256"] == context["source_sha256"]
                and row["identity"]["case_id"] == context["case_id"]]

    def budget_context(self, original, grant, actual, *, calls, dispatches, **options):
        """Display the same physical case/shared limits, without changing a grant."""
        context = _ACTIVE.get()
        require(context is not None, "physical budget context has no registered case")
        with self._ledger() as state:
            key = ":".join((context["leg"], context["stage"], context["case_id"]))
            require(key in state["started_cases"], "physical budget context precedes case admission")
            rows = state["requests"]
            case_rows = self._case_requests(rows, context)
            unknown = sum(row["usage"] is None for row in rows)
            for row in rows:
                value = row["usage"]
                if value is not None:
                    require(isinstance(value, dict) and set(value) == {"input_tokens", "output_tokens", "total_tokens"}
                        and all(type(v) is int and v >= 0 for v in value.values())
                        and value["total_tokens"] == value["input_tokens"] + value["output_tokens"],
                        "physical budget usage is incoherent")
            measured = not unknown
            case_input = sum(row["usage"]["input_tokens"] for row in case_rows if row["usage"] is not None)
            case_output = sum(row["usage"]["output_tokens"] for row in case_rows if row["usage"] is not None)
            total_input = sum(row["usage"]["input_tokens"] for row in rows if row["usage"] is not None)
            total_output = sum(row["usage"]["output_tokens"] for row in rows if row["usage"] is not None)
            remaining_tokens = max(0, self.policy["total_tokens"] - total_input - total_output)
            case_limit = self.policy["case_provider_limits"][context["case_id"]]
            shared_remaining = max(0, self.policy["total_provider_attempts"] - len(rows))
            model_ceiling = min(grant.maximum_model_runs, case_limit, len(case_rows) + shared_remaining)
            if measured and not remaining_tokens:
                model_ceiling = min(model_ceiling, len(case_rows))
            case_tools = sum(row["identity"] == context for row in state["tools"])
            pending_cleanup = sum(row["remaining"] for row in state["cleanup_reservations"])
            existing_cleanup = any(row["identity"] == context for row in state["cleanup_reservations"])
            # Reserve the ordinary programming cleanup before displaying generic
            # next-tool capacity, just as the physical programming fence does.
            tool_room = max(0, self.policy["total_tool_calls"] - len(state["tools"])
                - pending_cleanup - (0 if existing_cleanup else 2))
            if not measured or not remaining_tokens:
                tool_room = 0
            projected_grant = SimpleNamespace(maximum_model_runs=model_ceiling,
                maximum_input_tokens=min(grant.maximum_input_tokens, case_input + remaining_tokens),
                maximum_output_tokens=min(grant.maximum_output_tokens, case_output + remaining_tokens),
                maximum_tool_calls=min(grant.maximum_tool_calls, case_tools + tool_room))
            projected_usage = SimpleNamespace(measurement="measured" if measured else "unmeasured",
                model_attempts=len(case_rows), input_tokens=case_input if measured else None,
                output_tokens=case_output if measured else None)
            # This policy binds combined shared tokens, not a separate remaining
            # output quota after construction. Do not invent that split.
            options["response_output_tokens"] = None
            if not measured:
                options["response_input_tokens"] = None
            elif options.get("response_input_tokens") is not None:
                options["response_input_tokens"] = min(options["response_input_tokens"],
                    max(0, projected_grant.maximum_input_tokens - case_input))
            physical = {"measurement": projected_usage.measurement,
                "case_provider_attempts": len(case_rows), "case_provider_limit": case_limit,
                "shared_provider_attempts": len(rows), "shared_provider_limit": self.policy["total_provider_attempts"],
                "remaining_provider_attempts": max(0, model_ceiling - len(case_rows)) if measured else None,
                "shared_input_tokens": total_input if measured else None,
                "shared_output_tokens": total_output if measured else None,
                "shared_remaining_combined_tokens": remaining_tokens if measured else None,
                "shared_token_limit_kind": "combined_only", "shared_remaining_output_tokens": None,
                "physical_tools": len(state["tools"]), "cleanup_reserved": pending_cleanup,
                "unknown_attempts": unknown}
        text = original(projected_grant, projected_usage, calls=case_tools,
            dispatches=case_tools, **options)
        return text + " Host-registered physical budget (helpers share these limits): " + json.dumps(
            physical, sort_keys=True, separators=(",", ":"))

    def reserve_model(self, request_id, body):
        context = _ACTIVE.get()
        if context is None:
            raise ComponentWorkProductionError("no registered native case bound to this provider input")
        self._current_authority(kind="provider_dispatch")
        model = body.get("model")
        effort = body.get("reasoning", {}).get("effort")
        helper = body.get("tools") == [{"search_context_size":"low","type":"web_search"}]
        role = "contact" if model == "gpt-6-sol" else "helper" if helper else "work"
        if (role, model, effort) not in {("contact","gpt-6-sol","none"), ("work","gpt-6-astra","medium"), ("helper","gpt-6-astra","medium")}:
            raise ComponentWorkProductionError("unexpected native model role")
        if not isinstance(request_id,str) or not request_id:
            raise ComponentWorkProductionError("physical request identity missing")
        projection = None
        if self.native_projection_check is not None:
            projection, literal = self.native_projection_check()
            if projection["source_sha256"] != context["source_sha256"]:
                raise ComponentWorkProductionError("actual imported runner differs from selected report leg")
            def contains(value):
                if isinstance(value,str): return literal in value
                if isinstance(value,dict): return any(contains(v) for v in value.values())
                if isinstance(value,list): return any(contains(v) for v in value)
                return False
            if role == "work" and not contains(body):
                raise ComponentWorkProductionError("selected strategy missing from actual SDK request projection")
        with self._ledger() as state:
            rows = state["requests"]
            # Unknown/unfinished attempts fail closed before any second send;
            # this suppresses native transport retries without changing providers.
            if any(row["usage"] is None for row in rows):
                raise ComponentWorkProductionError("unmeasured physical attempt; retry/continuation blocked")
            if any(row["request_id"] == request_id for row in rows):
                raise ComponentWorkProductionError("physical request identity replay")
            case_rows = self._case_requests(rows, context)
            if len(rows) >= self.policy["total_provider_attempts"] or len(case_rows) >= self.policy["case_provider_limits"][context["case_id"]]:
                raise ComponentWorkProductionError("shared/case provider allowance exhausted")
            consumed = sum(row["usage"]["total_tokens"] for row in rows)
            if consumed >= self.policy["total_tokens"]:
                raise ComponentWorkProductionError("shared token allowance exhausted")
            rows.append({"request_id":request_id,"identity":dict(context),"role":role,"model":model,
                "reasoning_effort":effort,"request_body_sha256":hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest(),
                "request_body":body,"runtime_projection":projection,"usage":None})
        return request_id

    def complete_model(self, request_id, usage, *, hosted_tool_ids=()):
        values = {key:getattr(usage,key) for key in ("input_tokens","output_tokens","total_tokens")}
        if any(type(value) is not int or value < 0 for value in values.values()) or values["total_tokens"] != values["input_tokens"] + values["output_tokens"]:
            raise ComponentWorkProductionError("terminal physical provider usage is incoherent")
        with self._ledger() as state:
            rows = [row for row in state["requests"] if row["request_id"] == request_id]
            if len(rows) != 1 or rows[0]["usage"] is not None:
                raise ComponentWorkProductionError("missing/duplicate physical completion")
            rows[0]["usage"] = values
            for identifier in set(hosted_tool_ids):
                state["tools"].append({"identity":rows[0]["identity"],"kind":"provider_hosted_tool","id":identifier})
            consumed = sum(row["usage"]["total_tokens"] for row in state["requests"] if row["usage"] is not None)
            over = consumed > self.policy["total_tokens"] or len(state["tools"]) > self.policy["total_tool_calls"]
        if over:
            # Preserve the actual overrun; no counter reset or follow-up request.
            raise ComponentWorkProductionError("actual shared token/tool allowance exceeded")

    def reserve_tool(self, call, *, kind="native_action", cleanup_session=None):
        context = _ACTIVE.get()
        if context is None:
            raise ComponentWorkProductionError("no native task binding for tool dispatch")
        if call.name not in self.policy.get("allowed_action_names",[]):raise ComponentWorkProductionError("action outside fixed local-work scope")
        self._current_authority(kind="tool_dispatch")
        with self._ledger() as state:
            if any(row["usage"] is None for row in state["requests"]):
                raise ComponentWorkProductionError("unknown physical usage blocks subsequent tool dispatch")
            if sum(row["usage"]["total_tokens"] for row in state["requests"]) >= self.policy["total_tokens"]:
                raise ComponentWorkProductionError("shared measured token stop threshold reached")
            pending = sum(r["remaining"] for r in state["cleanup_reservations"])
            existing = [r for r in state["cleanup_reservations"] if r["identity"] == context and r["session_key"] == cleanup_session]
            # The ordinary client closes once on interrupted request, then its
            # runner-finally closes. Reserve both possible existing sends; only
            # actual sends enter tools, unused capacity is released at finally.
            extra = 2 if cleanup_session is not None and not existing else 0
            if len(state["tools"]) + pending + extra >= self.policy["total_tool_calls"]:
                raise ComponentWorkProductionError("shared tool allowance exhausted")
            if cleanup_session is not None:
                require(isinstance(cleanup_session, str) and cleanup_session, "programming cleanup identity absent")
                require(not existing or existing[0]["phase"] == "reserved", "closed programming session cannot be restarted")
                if not existing:
                    state["cleanup_reservations"].append({"identity": dict(context), "session_key": cleanup_session, "phase": "reserved", "remaining": 2, "dispatched": 0})
            state["tools"].append({"identity":dict(context),"kind":kind,"id":call.call_id,"name":call.name})

    def cleanup_dispatch(self, session_key):
        """Consume reserved capacity for the runner's original finally-close.

        Stop or UNKNOWN cannot strand an existing process. This observes the
        unchanged cleanup; it never issues another cleanup or a retry.
        """
        identity = _ACTIVE.get()
        with self._ledger() as state:
            rows = [r for r in state["cleanup_reservations"] if r["identity"] == identity and r["session_key"] == session_key]
            require(len(rows) == 1 and rows[0]["phase"] in {"reserved", "closing"} and rows[0]["remaining"] > 0, "original cleanup reservation differs or was replayed")
            rows[0]["phase"] = "closing"
            rows[0]["remaining"] -= 1
            rows[0]["dispatched"] += 1
            identifier = "native-close:" + session_key + ":" + str(rows[0]["dispatched"])
            state["tools"].append({"identity": dict(identity), "kind": "native_cleanup", "id": identifier, "name": "close"})
            require(len(state["tools"]) <= self.policy["total_tool_calls"], "reserved cleanup capacity was exceeded")
        return identifier

    def observe_programming_finally(self, session_key, started):
        require(type(started) is bool, "original programming start state unknown")
        identity = _ACTIVE.get()
        with self._ledger() as state:
            rows = [r for r in state["cleanup_reservations"] if r["identity"] == identity and r["session_key"] == session_key]
            if not started:
                require(not rows or rows[0]["phase"] == "reserved", "cleanup receipt conflicts with original no-start state")
                if rows: rows[0].update(phase="not_started", remaining=0)
            else:
                require(len(rows) == 1 and rows[0]["phase"] == "closing", "started programming lacks original cleanup dispatch")
                rows[0].update(phase="finalized", remaining=0)
        self.control_receipts.append({"identity": dict(identity), "kind": "programming_finally", "session_key": session_key, "started": started})

    def install(self):
        """Only call in the producer process, never in the installed core/worker."""
        if self.native_projection_check is None:
            raise ComponentWorkProductionError("native source/literal projection check must be host-bound before transport installation")
        import httpx2
        import eva.adapters.model.openai_codex_provider as codex
        import eva.adapters.model.pydantic_conversation as models
        import eva.bootstrap.delegated_digital_runner as digital
        from eva.adapters.connectors.programming_workspace import ProgrammingWorkspaceClient
        from eva.adapters.postgres.programming_actions import ProgrammingActions
        guard = self
        original_init = codex._CodexTransport.__init__
        original_research = models.open_research_response
        original_actions = digital.BackgroundConnectorActions.execute_step
        original_catalog = digital.BackgroundConnectorActions.__init__
        original_programming = ProgrammingWorkspaceClient.request
        original_close = ProgrammingWorkspaceClient.close
        original_finally = ProgrammingActions.close
        original_budget_context = digital._remaining_budget_context

        class ObservedBody(httpx2.AsyncByteStream):
            def __init__(self, body, request_id):
                self.body,self.request_id = body,request_id
                self.attestor = codex._RawSseUsageAttestor()
                self.pending = b""
                self.hosted = set()
                self.finished = False
            def feed(self, chunk):
                self.attestor.feed(chunk)
                self.pending += chunk
                while b"\n" in self.pending:
                    line,self.pending = self.pending.split(b"\n",1)
                    if line.startswith(b"data: "):
                        try: event = json.loads(line[6:])
                        except ValueError: continue
                        item = event.get("item",{}) if isinstance(event,dict) else {}
                        if isinstance(item,dict) and item.get("type") == "web_search_call" and isinstance(item.get("id"),str): self.hosted.add(item["id"])
                if self.attestor.terminal is not None and not self.finished:
                    self.finished = True
                    guard.complete_model(self.request_id,self.attestor.terminal,hosted_tool_ids=self.hosted)
            async def __aiter__(self):
                async for chunk in self.body:
                    self.feed(chunk)
                    yield chunk
            async def aclose(self): await self.body.aclose()

        class ObservedNetwork(httpx2.AsyncBaseTransport):
            def __init__(self, delegate): self.delegate = delegate
            async def handle_async_request(self, request):
                if not request.url.path.endswith("/responses"):
                    raise ComponentWorkProductionError("unexpected native provider path")
                body = json.loads(await request.aread())
                if body.get("tools") and any(isinstance(t,dict) and t.get("type") == "web_search" for t in body["tools"]):
                    raise ComponentWorkProductionError("provider-hosted search multiplicity lacks pre-dispatch tool fence")
                identifier = guard.reserve_model(request.headers.get("session_id"),body)
                response = await self.delegate.handle_async_request(request)
                return httpx2.Response(response.status_code,headers=response.headers,
                    stream=ObservedBody(response.stream,identifier),request=request,extensions=response.extensions)
            async def aclose(self): await self.delegate.aclose()

        def init(transport,*args,**kwargs):
            original_init(transport,*args,**kwargs)
            transport._network = ObservedNetwork(transport._network)
        def research(request,**kwargs):
            # Native hosted search has no before-each-physical-tool hook. Keep
            # the hard 70-tool admission truthful; no alternative search route.
            if any(isinstance(t,dict) and t.get("type") == "web_search" for t in json.loads(request.data).get("tools", [])):
                raise ComponentWorkProductionError("provider-hosted search multiplicity lacks pre-dispatch tool fence")
            identifier = guard.reserve_model(request.get_header("Session_id"),json.loads(request.data))
            response = original_research(request,**kwargs)
            body = response.getvalue()
            parser = ObservedBody(None,identifier)
            parser.feed(body)
            if not parser.finished: raise ComponentWorkProductionError("research physical terminal usage missing")
            return response
        def actions(port,turn,call,*,step_index):
            port._authorize(turn)
            target = port._by_name.get(call.name)
            guard.reserve_tool(call, cleanup_session=target.session_key if isinstance(target, ProgrammingActions) else None)
            receipt = original_actions(port,turn,call,step_index=step_index)
            from dataclasses import asdict
            programming = isinstance(target, ProgrammingActions)
            guard.program_observer.observe(asdict(receipt), dict(_ACTIVE.get()), step_index,
                target.session_key if programming else None, target.workspace_key if programming else None)
            return receipt
        def catalog(port, tools, allowed_names, authorize, maximum_calls):
            # Narrow the evaluation action/context view, outside the unchanged
            # profile/grant. Other services never contribute active context.
            allowed = set(allowed_names) & _ALLOWED_ACTIONS
            tools = tuple(t for t in tools if allowed.intersection(t.action_names))
            original_catalog(port, tools, allowed, authorize, maximum_calls)
        def programming(client,session_key,workspace_key,action,**arguments):
            if action!="status":return original_programming(client,session_key,workspace_key,action,**arguments)
            from types import SimpleNamespace
            identifier="native-idle:"+session_key
            guard.reserve_tool(SimpleNamespace(call_id=identifier,name="native_programming_idle_check"),kind="native_internal_tool")
            result=original_programming(client,session_key,workspace_key,action,**arguments)
            guard.control_receipts.append({"identity":dict(_ACTIVE.get()),"id":identifier,"kind":"idle","workspace_key":workspace_key,"session_key":session_key,"result":result})
            return result
        def closed(client,session_key):
            # Observe the unchanged existing cleanup, even when owner stop has
            # cancelled action/model dispatch. No new tool or retry is issued.
            identifier = guard.cleanup_dispatch(session_key)
            result=original_close(client,session_key)
            guard.control_receipts.append({"identity":dict(_ACTIVE.get()),"id":identifier,"kind":"close","session_key":session_key,"result":result})
            return result
        def finally_closed(programming):
            result = original_finally(programming)
            guard.observe_programming_finally(programming.session_key, programming.started)
            return result
        def budget_context(grant, actual, **options):
            return guard.budget_context(original_budget_context, grant, actual, **options)
        digital._remaining_budget_context = budget_context
        codex._CodexTransport.__init__ = init
        models.open_research_response = research
        digital.BackgroundConnectorActions.execute_step = actions
        digital.BackgroundConnectorActions.__init__ = catalog
        ProgrammingWorkspaceClient.request=programming
        ProgrammingWorkspaceClient.close=closed
        ProgrammingActions.close=finally_closed
        self._restore = lambda: (setattr(digital,"_remaining_budget_context",original_budget_context),
            setattr(codex._CodexTransport,"__init__",original_init),
            setattr(models,"open_research_response",original_research),setattr(digital.BackgroundConnectorActions,"execute_step",original_actions),setattr(digital.BackgroundConnectorActions,"__init__",original_catalog),setattr(ProgrammingWorkspaceClient,"request",original_programming),setattr(ProgrammingWorkspaceClient,"close",original_close),setattr(ProgrammingActions,"close",original_finally))

    def close(self):
        if hasattr(self,"_restore"): self._restore()



def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _fields(value, fields):
    require(isinstance(value, dict) and set(value) == set(fields), "registered producer fields differ")


def _private_root(path, *, uid=None):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, "private producer root must be absolute")
    for part in (path, *path.parents):
        require(not part.is_symlink(), "private producer path contains a symlink")
    return directory(path, uid=uid)


def validate_settings(settings, assessment):
    """No defaults manufacture a grant, actor, input, resource ceiling or deadline."""
    optional = {k for k in ("parent_bindings", "runtime_admission_sha256") if k in settings}
    _fields(settings, {"contract", "runtime", "inputs_file", "inputs_sha256", "output_root",
        "accounting_root", "workspace_root", "receipt_root", "procedure_execution", "limits", "parent_deadline",
        "resources_file", "resources_sha256", "case_deadline_seconds"} | optional)
    require(settings["contract"] == "EvaNativeComponentWorkProducerSettings/v1", "producer settings contract differs")
    if "runtime_admission_sha256" in optional:
        require(_sha(settings["runtime_admission_sha256"]), "fixed runtime admission hash required")
    if "parent_bindings" in optional:
        parent = settings["parent_bindings"]
        _fields(parent, {"task_id", "spec_sha256", "source_ingress_sha256"})
        require(isinstance(parent["task_id"], str) and parent["task_id"]
            and _sha(parent["spec_sha256"]) and _sha(parent["source_ingress_sha256"]), "host-issued original parent binding required")
    runtime = settings["runtime"]
    _fields(runtime, {"core_container", "image_id", "configuration_sha256", "source_hashes", "release",
        "capability_ids", "model_profile_id", "actor", "client_file", "client_sha256"})
    _fields(runtime["actor"], {"tenant_id", "installation_id", "principal_id", "principal_binding_id"})
    for key, value in runtime["actor"].items():
        require(isinstance(value, str) and 1 <= len(value) <= 200, "fixed scoped evaluation actor required")
        if key in {"principal_id", "principal_binding_id"}:
            require(str(UUID(value)) == value, "canonical private actor required")
    require(isinstance(runtime["core_container"], str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", runtime["core_container"]), "fixed core container required")
    require(isinstance(runtime["image_id"], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", runtime["image_id"]), "fixed runtime image required")
    for value in (settings["inputs_sha256"], runtime["configuration_sha256"], runtime["client_sha256"]):
        require(_sha(value), "fixed host source hash required")
    require(isinstance(runtime["source_hashes"], dict) and runtime["source_hashes"], "fixed runtime sources required")
    for name, digest in runtime["source_hashes"].items():
        require(isinstance(name, str) and name.endswith(".py") and not Path(name).is_absolute()
            and ".." not in Path(name).parts and "\\" not in name and _sha(digest), "unsafe runtime source manifest")
    _fields(runtime["release"], {"worker_profile_release_sha256", "model_profile_release_sha256",
        "worker_program_sha256", "engine_release_sha256", "output_schema_sha256"})
    require(all(_sha(v) for v in runtime["release"].values()), "fixed factory release required")
    require(isinstance(runtime["capability_ids"], list) and runtime["capability_ids"]
        and all(isinstance(v, str) for v in runtime["capability_ids"])
        and len(set(runtime["capability_ids"])) == len(runtime["capability_ids"]), "fixed factory capabilities required")
    require(isinstance(runtime["model_profile_id"], str) and runtime["model_profile_id"], "fixed factory model required")
    config = assessment["work_evidence"]
    require(config["scope_sha256"] == canonical_sha256({k: runtime["actor"][k]
        for k in ("tenant_id", "installation_id", "principal_id")}), "evaluation scope differs from host assessment")
    limits = settings["limits"]
    _fields(limits, {"total_provider_attempts", "case_provider_attempts", "total_tokens", "total_tool_calls", "retry_attempts"})
    require(type(limits["retry_attempts"]) is int and limits["retry_attempts"] == 0, "retry is not admitted")
    require(all(type(limits[k]) is int and limits[k] > 0 for k in limits if k != "retry_attempts"), "fixed positive evaluation limits required")
    per_case = config["limits"]["per_case"]
    require(limits["total_provider_attempts"] <= config["limits"]["total_provider_attempts"]
        and limits["case_provider_attempts"] <= per_case["contact_attempts"] + per_case["worker_and_helper_attempts"]
        and limits["total_tokens"] <= config["limits"]["total_input_tokens"] + config["limits"]["total_output_tokens"], "producer budget exceeds assessment admission")
    resource_durations = _registered_resource_durations(settings)
    durations = settings["case_deadline_seconds"]
    require(isinstance(durations, dict) and durations and all(isinstance(case_id, str) and case_id
        and type(seconds) is int and seconds in resource_durations.values()
        for case_id, seconds in durations.items()), "fixed registered case durations required")
    from eva.contracts.timestamps import parse_utc_timestamp
    parse_utc_timestamp(settings["parent_deadline"])
    # Existing isolated procedure executor validates image/resource fields too.
    from eva.operations.procedure_execution import ProcedureExecutionConfig
    ProcedureExecutionConfig(**settings["procedure_execution"])
    for name in ("output_root", "accounting_root"):
        _private_root(settings[name])
    for name in ("workspace_root", "receipt_root"):
        path = Path(settings[name])
        require(path.is_absolute() and path.resolve() == path and path.is_dir(), "existing isolated workspace service root required")
    return deepcopy(settings)


def _registered_resource_durations(settings):
    """Read only the immutable host resource binding, without changing its bytes."""
    require(_sha(settings["resources_sha256"]), "fixed resource hash required")
    path = Path(settings["resources_file"])
    require(path.is_absolute() and path.resolve() == path, "fixed private resources path required")
    raw = read_private(path)
    require(sha(raw) == settings["resources_sha256"], "registered work resources changed")
    resource = json.loads(raw)
    duration, attempts = resource["duration"], resource["provider_attempts"]
    pairs = [(attempts[family + "_per_episode_including_contact_helpers"],
        duration["per_" + family + "_work_ceiling_seconds"])
        for family in ("simple", "multistep")]
    require(all(type(count) is int and count > 0 and type(seconds) is int and seconds > 0
        for count, seconds in pairs) and len({count for count, _ in pairs}) == len(pairs),
        "fixed registered resource durations invalid")
    return dict(pairs)


def _case_deadline(now, *, case_seconds, profile_seconds, parent_deadline):
    from eva.contracts.timestamps import parse_utc_timestamp
    require(type(case_seconds) is int and case_seconds > 0, "fixed case duration required")
    deadline = min(now + timedelta(seconds=case_seconds), now + timedelta(seconds=profile_seconds),
        parse_utc_timestamp(parent_deadline))
    require(deadline > now, "original parent deadline expired before admission")
    return deadline


def _authority(authorize, binding, deadline):
    from eva.contracts.timestamps import parse_utc_timestamp
    require(datetime.now(UTC) < parse_utc_timestamp(deadline), "original component deadline expired")
    result = authorize()
    require(result == {"verified": True, "execution_binding_ref": binding["execution_binding_ref"], "parent_deadline": deadline}, "original component authority unconfirmed")
    return result


def _configuration(inspect):
    config, host = inspect["Config"], inspect["HostConfig"]
    mounts = [{"type": m["Type"], "source": m.get("Name") if m["Type"] == "volume" else m["Source"],
        "target": m["Destination"], "read_only": not m["RW"]} for m in inspect["Mounts"]]
    require(len({m["target"] for m in mounts}) == len(mounts), "duplicate runtime mounts")
    return {"image": inspect["Image"], "environment": env_map(config["Env"]),
        "mounts": sorted(mounts, key=lambda m: m["target"]),
        "networks": sorted(inspect["NetworkSettings"]["Networks"]), "user": config["User"],
        "command": config["Cmd"], "entrypoint": config["Entrypoint"], "working_dir": config["WorkingDir"],
        "healthcheck": config.get("Healthcheck"), "readonly_rootfs": host["ReadonlyRootfs"],
        "cap_drop": host.get("CapDrop"), "security_opt": host.get("SecurityOpt"),
        "privileged": host["Privileged"], "ports": host.get("PortBindings")}


def _docker(arguments):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True)
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def _prepare_source(settings, request):
    from eva.operations.component_evaluation import WORK_STRATEGY_PATH, validate_work_strategy_candidate
    runtime = settings["runtime"]
    result = _docker(["inspect", runtime["core_container"]])
    require(result["returncode"] == 0, "fixed core metadata unavailable")
    values = json.loads(result["stdout"])
    require(len(values) == 1, "ambiguous core runtime")
    config = _configuration(values[0])
    require(config["image"] == runtime["image_id"] and canonical_sha256(config) == runtime["configuration_sha256"], "current runtime configuration shifted")
    # Read only installed Python source, never provider configuration/credentials.
    code = """import json,pathlib,eva,sqlalchemy as sa
from uuid import UUID
from dataclasses import asdict
from eva.bootstrap.runtime import RuntimeSettings,sqlalchemy_postgres_dsn
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.bootstrap.delegated_digital_composition import build_digital_runner
s=RuntimeSettings.from_environment();e=sa.create_engine(sqlalchemy_postgres_dsn(s.database_dsn))
try:
 p=pathlib.Path(eva.__file__).resolve().parent
 r=build_digital_runner(s,PostgresDelegatedWorkStore(e,TimelineScope(UUID(s.dashboard_tenant_id),UUID(s.dashboard_installation_id))))
 print(json.dumps({'source':(p/'bootstrap/delegated_digital_runner.py').read_text(),'package_path':str(p),'release':asdict(r.profile.release)}))
finally:e.dispose()
"""
    result = _docker(["exec", runtime["core_container"], "python", "-B", "-c", code])
    require(result["returncode"] == 0, "installed baseline factory metadata unavailable")
    metadata = json.loads(result["stdout"])
    baseline = metadata["source"].encode("utf-8")
    require(sha(baseline) == request.baseline_sha256 == runtime["source_hashes"].get(WORK_STRATEGY_PATH), "actual baseline source differs")
    require(Path(metadata["package_path"]).is_absolute(), "installed package location unavailable")
    require(metadata["release"]["model_profile_release_sha256"] != runtime["release"]["model_profile_release_sha256"], "private preview release is not isolated from live workers")
    for key, value in metadata["release"].items():
        if key != "model_profile_release_sha256":
            require(value == runtime["release"][key], "preview configuration changed unrelated release fields")
    config["package_path"] = metadata["package_path"]
    config["live_model_profile_release_sha256"] = metadata["release"]["model_profile_release_sha256"]
    validate_work_strategy_candidate(baseline, request.source.encode("utf-8"))
    require(sha(canonical({"contract": "EvaDigitalWorkProgram/v4", "sources": runtime["source_hashes"]}))
        == runtime["release"]["worker_program_sha256"], "baseline program source manifest differs")
    return baseline, config


def _selected_runtime(settings, source):
    runtime = deepcopy(settings["runtime"])
    runtime["source_hashes"]["bootstrap/delegated_digital_runner.py"] = sha(source)
    runtime["release"]["worker_program_sha256"] = sha(canonical({
        "contract": "EvaDigitalWorkProgram/v4", "sources": runtime["source_hashes"]}))
    tree = ast.parse(source.decode("utf-8"))
    methods = [n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == "_TaskModel"
        for n in c.body if isinstance(n, ast.FunctionDef) and n.name == "_work_strategy"]
    require(len(methods) == 1 and len(methods[0].body) == 1 and isinstance(methods[0].body[0], ast.Return)
        and isinstance(methods[0].body[0].value, ast.Constant) and isinstance(methods[0].body[0].value.value, str), "literal strategy hook required")
    runtime["strategy_sha256"] = sha(methods[0].body[0].value.value.encode("utf-8"))
    return runtime


def _cases(settings, assessment):
    raw = read_private(settings["inputs_file"])
    require(sha(raw) == settings["inputs_sha256"], "registered development inputs changed")
    inputs = json.loads(raw)
    _fields(inputs, {"contract", "stage", "registration_sha256", "case_set_sha256", "scope_sha256", "cases"})
    config = assessment["work_evidence"]
    require(inputs["contract"] == "EvaRegisteredComponentWorkInputs/v1" and inputs["stage"] in {"development", "independent_final"}
        and all(inputs[k] == config[k] for k in ("registration_sha256", "case_set_sha256", "scope_sha256")), "development input registration differs")
    cases_raw = Path(assessment["cases_file"]).read_bytes()
    require(sha(cases_raw) == config["case_set_sha256"], "fixed case manifest changed")
    ids = [c["case_id"] for c in json.loads(cases_raw)]
    require(isinstance(inputs["cases"], list) and inputs["cases"] and len(ids) == len(set(ids)), "fixed cases required")
    seen = set()
    resource_durations = _registered_resource_durations(settings)
    for case in inputs["cases"]:
        _fields(case, {"case_id", "task", "project_id", "fixture_files", "checks", "required_action_names", "case_provider_attempts"})
        require(case["case_id"] in ids and case["case_id"] not in seen, "foreign or duplicate development case")
        seen.add(case["case_id"])
        require(type(case["case_provider_attempts"]) is int and 1 <= case["case_provider_attempts"] <= settings["limits"]["case_provider_attempts"], "case physical request limit exceeds admission")
        require(case["case_provider_attempts"] in resource_durations and
            settings["case_deadline_seconds"].get(case["case_id"]) == resource_durations[case["case_provider_attempts"]],
            "case duration differs from registered provider resource binding")
        require(isinstance(case["task"], str) and case["task"].strip() and len(case["task"].encode()) <= 16384, "bounded fixed development task required")
        require(case["project_id"] is None or isinstance(case["project_id"], str) and 1 <= len(case["project_id"]) <= 200, "fixed evaluation project invalid")
        require(isinstance(case["required_action_names"], list) and set(case["required_action_names"]) <= _ALLOWED_ACTIONS, "case needs nonisolated or unavailable actions")
        require(isinstance(case["fixture_files"], dict) and len(case["fixture_files"]) <= 16, "bounded isolated fixtures required")
        for name, content in case["fixture_files"].items():
            require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name)
                and isinstance(content, str) and len(content.encode()) <= 65536, "unsafe isolated fixture")
        require(isinstance(case["checks"], list), "fixed input-only checks required")
        check_ids = set()
        for check in case["checks"]:
            _fields(check, {"check_id", "stdin"})
            require(isinstance(check["check_id"], str) and check["check_id"] and check["check_id"] not in check_ids
                and isinstance(check["stdin"], dict), "invalid fixed check input")
            canonical(check["stdin"])
            check_ids.add(check["check_id"])
    require(seen == set(ids), "development case coverage differs")
    require(set(settings["case_deadline_seconds"]) == seen, "registered case duration coverage differs")
    return inputs["cases"], inputs["stage"]


def _case_identity(settings, config, leg, case, *, execution_binding_ref, stage="development"):
    from eva.application.conversation import ConversationTurn
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    actor = settings["runtime"]["actor"]
    require(_sha(execution_binding_ref), "evaluation binding required for isolated case identity")
    namespace = "component-work-evaluation:" + config["registration_sha256"] + ":" + stage + ":" + leg + ":" + sha(case["case_id"].encode())[:16]
    # Keep the logical case fixed while separating distinct, bound evaluations.
    # Repeating the same binding still reaches the original once-only fences.
    namespace += ":evaluation:" + execution_binding_ref
    conversation, message = namespace, namespace + ":input"
    turn = ConversationTurn.private(actor["tenant_id"], actor["installation_id"], actor["principal_id"],
        conversation, message, case["task"], "2000-01-01T00:00:00Z", project_id=case["project_id"])
    key = ":".join((turn.tenant_id, turn.installation_id, turn.principal_id, conversation, message))
    return {"case_id": case["case_id"], "leg": leg, "stage": stage,
        "scope_sha256": config["scope_sha256"], "conversation_id": conversation, "message_id": message,
        "task_id": "work:eva-chat:" + str(uuid5(NAMESPACE_URL, key)),
        "session_id": str(uuid5(NAMESPACE_URL, "component-evaluation-session:" + key)),
        "source_ingress_sha256": canonical_sha256(PostgresConversationJournal._ingress_data(turn))}


def _policy(settings, config, binding, request, cases, *, stage="development"):
    limits = settings["limits"]
    identities = {leg + ":" + stage + ":" + c["case_id"]: _case_identity(settings, config, leg, c,
        execution_binding_ref=binding["execution_binding_ref"], stage=stage)
        for leg in ("baseline", "candidate") for c in cases}
    return {**limits, "registration_sha256": config["registration_sha256"],
        "scope_sha256": config["scope_sha256"], "baseline_sha256": request.baseline_sha256,
        "candidate_sha256": sha(request.source.encode()), "execution_binding": deepcopy(binding),
        "case_bindings": identities, "case_provider_limits": {c["case_id"]: c["case_provider_attempts"] for c in cases},
        "case_deadline_seconds": deepcopy(settings["case_deadline_seconds"]),
        "resources_ref": {"path": settings["resources_file"], "sha256": settings["resources_sha256"]},
        "parent_deadline": settings["parent_deadline"], "allowed_action_names": sorted(_ALLOWED_ACTIONS),
        "runtime_sha256": canonical_sha256(settings["runtime"]),
        "host_metadata": {k: deepcopy(settings[k]) for k in ("parent_bindings", "runtime_admission_sha256") if k in settings}}


def plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return {field.name: plain(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    if isinstance(value, (datetime, UUID)):
        return str(value)
    return value

def strategy_projection(raw, imported_method, *, source_sha256, literal_sha256):
    import ast
    if hashlib.sha256(raw).hexdigest() != source_sha256:
        raise ComponentWorkProductionError("selected runner whole-source hash differs")
    tree = ast.parse(raw.decode("utf-8"))
    targets = [method for node in tree.body if isinstance(node, ast.ClassDef)
        and node.name == "_TaskModel" for method in node.body
        if isinstance(method, ast.FunctionDef) and method.name == "_work_strategy"]
    if (len(targets) != 1 or len(targets[0].body) != 1
            or not isinstance(targets[0].body[0], ast.Return)
            or not isinstance(targets[0].body[0].value, ast.Constant)
            or not isinstance(targets[0].body[0].value.value, str)):
        raise ComponentWorkProductionError("selected strategy is not the host-bound literal hook")
    literal = targets[0].body[0].value.value
    if (not literal.strip() or len(literal.encode()) > 4096
            or hashlib.sha256(literal.encode()).hexdigest() != literal_sha256
            or imported_method(None) != literal):
        raise ComponentWorkProductionError("actual imported strategy differs from host AST literal")
    return {"source_sha256":source_sha256, "literal_sha256":literal_sha256,
        "imported_method_equals_ast_literal":True}, literal

class ExactClaimStore:
    """Fence normal lease selection before start_call; never mutate other work."""
    def __init__(self, store, work_id, on_start=None):
        self.store, self.work_id, self.on_start = store, work_id, on_start
    def __getattr__(self, name):
        return getattr(self.store, name)
    def claim_next(self, **arguments):
        claim = self.store.claim_next(**arguments)
        if claim is not None and claim.work_id != self.work_id:
            # Existing store has no lease-release API. Its ordinary 30-second
            # lease expires unchanged; no call is started or outcome rewritten.
            raise ComponentWorkProductionError("ordinary claim selected unrelated work; wait for original lease expiry")
        return claim
    def start_call(self, claim, **arguments):
        if claim.work_id != self.work_id:
            raise ComponentWorkProductionError("claim differs before the durable call fence")
        call=self.store.start_call(claim, **arguments)
        if call is not None and self.on_start is not None:self.on_start(call)
        return call

class _EmptyEvaluationMemory:
    """Explicit registered isolation over the existing MemoryAuthority protocol."""
    def __init__(self, actor, identity, project_id):
        self.actor, self.identity, self.project_id = actor, identity, project_id

    def recall_current(self, query):
        require(query.tenant_id == self.actor["tenant_id"]
            and query.principal_id == self.actor["principal_id"]
            and query.conversation_id == self.identity["conversation_id"]
            and query.project_id == self.project_id, "memory request left registered evaluation scope")
        return ()

    def remember(self, command):
        raise ComponentWorkProductionError("host evaluation cannot write personal memory")


def _reserve_memory_exclusion(engine, store, identity, origin, now):
    """An exclusion, never a processed/successful memory projection."""
    import sqlalchemy as sa
    from eva.adapters.postgres.operational_timeline import insert_initial_timeline_thread
    from eva.adapters.postgres.operational_timeline import operational_checkpoints
    from eva.adapters.postgres.current_catalog import delegated_work_runs
    marker = "work-memory:" + sha(identity["task_id"].encode())
    with engine.begin() as connection:
        store._scope(connection)
        exists = connection.scalar(sa.select(sa.literal(True)).where(sa.exists(sa.select(1).where(
            delegated_work_runs.c.tenant_id == store.scope.tenant_id,
            delegated_work_runs.c.installation_id == store.scope.installation_id,
            delegated_work_runs.c.work_id == identity["task_id"]))))
        prior = connection.scalar(sa.select(sa.literal(True)).where(sa.exists(sa.select(1).where(
            operational_checkpoints.c.tenant_id == store.scope.tenant_id,
            operational_checkpoints.c.installation_id == store.scope.installation_id,
            operational_checkpoints.c.thread_id == marker))))
        require(not exists and not prior, "registered work or memory disposition already exists; no retry")
        insert_initial_timeline_thread(connection, store.scope, thread_id=marker, checkpoint_id=marker,
            occurred_at=now, event_type="HOST_EVALUATION_MEMORY_EXCLUDED", phase="host_evaluation_excluded",
            status="completed", data={"disposition": "host_evaluation_excluded", "origin": origin})
    return {"thread_id": marker, "disposition": "host_evaluation_excluded", "origin_sha256": canonical_sha256(origin)}


class _NativeEntry:
    def __init__(self, settings, engine, store, runner, service, guard, config):
        self.settings, self.engine, self.store = settings, engine, store
        self.runner, self.service, self.guard, self.config = runner, service, guard, config
        self.active_call = None

    def parent_current(self):
        from eva.adapters.connectors.component_evaluation import configured_component_evaluator
        client = configured_component_evaluator()
        require(client is not None, "ordinary component authority client unavailable")
        return _authority(lambda: client._post("/work-authority", self.config["execution_binding"], limit=1024),
            self.config["execution_binding"], self.config["parent_deadline"])

    def admit(self, identity, case):
        from eva.application.conversation import ConversationTurn
        from eva.adapters.postgres.operational_timeline import external_timeline_scope
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        actor = self.config["runtime"]["actor"]
        self.parent_current()
        now = datetime.now(UTC).replace(microsecond=0)
        turn = ConversationTurn.private(actor["tenant_id"], actor["installation_id"], actor["principal_id"],
            identity["conversation_id"], identity["message_id"], case["task"], now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            project_id=case["project_id"])
        self.service.validate_authority(turn)
        require(str(self.service.owner_principal_id) == actor["principal_id"]
            and str(self.service.principal_binding_id) == actor["principal_binding_id"], "current scoped actor differs")
        require(canonical_sha256(PostgresConversationJournal._ingress_data(turn)) == identity["source_ingress_sha256"], "fixed host ingress differs")
        source_journal = PostgresConversationJournal(self.engine, external_timeline_scope(
            turn.tenant_id, turn.installation_id, "host evaluation source scope required"))
        require(not source_journal._timeline.events(source_journal._thread_id(turn)), "registered ingress already exists; no retry")
        require("work:eva-chat:" + str(uuid5(NAMESPACE_URL, self.service.command_key(turn))) == identity["task_id"], "ordinary work identity differs")
        require(self.config["case_deadline_seconds"] == self.config["policy"]["case_deadline_seconds"][case["case_id"]],
            "runtime case duration differs from pinned policy")
        deadline = _case_deadline(now, case_seconds=self.config["case_deadline_seconds"],
            profile_seconds=self.runner.profile.timeout_seconds, parent_deadline=self.config["parent_deadline"])
        spec = self.runner.profile.build_spec(source={"tenant_id": turn.tenant_id,
            "installation_id": turn.installation_id, "principal_id": turn.principal_id,
            "conversation_id": turn.conversation_id, "message_id": turn.message_id,
            "audience": {"kind": "private_person", "principal_id": turn.principal_id}},
            source_ingress_sha256=identity["source_ingress_sha256"], work_id=identity["task_id"],
            episode_id=str(uuid5(NAMESPACE_URL, ":".join(("delegated-private-context-v1", turn.tenant_id,
                turn.installation_id, turn.principal_id, turn.conversation_id)))),
            generation=1, parent_task_id=self.config["execution_binding"]["task_id"],
            owner_principal_id=actor["principal_id"], principal_binding_id=actor["principal_binding_id"],
            task=case["task"], created_at=now, deadline=deadline)
        require(self.runner.profile.accepts_grant(spec.grant), "ordinary profile does not accept its unchanged grant")
        origin = {"contract": "EvaHostComponentWorkOrigin/v1", "identity": identity,
            "actor": actor, "project_id": case["project_id"], "namespace": identity["conversation_id"],
            "execution_binding_ref": self.config["execution_binding"]["execution_binding_ref"],
            "registration_sha256": self.config["policy"]["registration_sha256"],
            "isolation": {"family_memory_read": False, "family_memory_write": False,
                "external_service_effects": False, "component_evaluation_reentry": False},
            "spec_sha256": spec.sha256, "occurred_at": turn.occurred_at}
        self.parent_current()
        exclusion = _reserve_memory_exclusion(self.engine, self.store, identity, origin, now)
        # Once-only marker is durable first. Any later failure remains recorded;
        # neither partial admission nor marker can silently renew this case.
        source_journal.admit(turn)
        self.parent_current()
        admission = self.store.admit(spec=spec, title="Geregistreerde hostevaluatie",
            summary="Geïsoleerde ontwikkelbeoordeling; geen eigenaarvraag of eigen initiatief.",
            worker_role="Hostevaluatie digitale werker", retention_policy_id="retention:delegated-work:v1",
            retention_until=now + timedelta(days=7), occurred_at=now)
        require(admission.disposition == "inserted", "registered case admission was not fresh")
        return turn, {"origin": origin, "memory_disposition": exclusion, "admission": plain(admission)}

    async def run(self, identity):
        from eva.operations.delegated_work_worker import DelegatedWorkWorker
        def started(call):
            self.active_call = call
            def current():
                self.parent_current()
                from eva.contracts.timestamps import parse_utc_timestamp
                require(datetime.now(UTC) < parse_utc_timestamp(call.spec.deadline),
                    "registered case deadline expired before physical dispatch")
                self.store.authorize_tool_dispatch(call)
                turn = self.runner.source_loader(call.spec.source.model_dump(mode="json"))
                self.service.validate_authority(turn)
                from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
                require(canonical_sha256(PostgresConversationJournal._ingress_data(turn)) == identity["source_ingress_sha256"], "current evaluation source differs")
                return {"work_id": call.work_id, "call_id": call.call_id,
                    "source_ingress_sha256": identity["source_ingress_sha256"],
                    "execution_binding_ref": self.config["execution_binding"]["execution_binding_ref"],
                    "source_scope_valid": True, "authorization_current": True, "stop_respected": True}
            self.guard.authority_check = current
        with self.guard.case(identity):
            worker = DelegatedWorkWorker(ExactClaimStore(self.store, identity["task_id"], on_start=started),
                self.runner, worker_id="component-producer:" + identity["session_id"], release=self.runner.profile.release)
            self.parent_current()
            return await worker.run_once()

    def export(self, identity, turn):
            from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
            from eva.adapters.postgres.operational_timeline import external_timeline_scope, PostgresOperationalTimeline
            from eva.adapters.postgres.background_work_progress import load_work_progress
            with self.guard.case(identity):
                self.parent_current()
                self.service.validate_authority(turn)
                work = self.store.load(identity["task_id"], owner_principal_id=self.service.owner_principal_id,
                    principal_binding_id=self.service.principal_binding_id)
                actual_source=self.runner.source_loader(work.spec.source.model_dump(mode="json"))
                from eva.contracts.canonical_json import canonical_sha256
                if actual_source!=turn or canonical_sha256(PostgresConversationJournal._ingress_data(actual_source))!=identity["source_ingress_sha256"]:
                    raise ComponentWorkProductionError("terminal export source differs")
                require(self.active_call is not None, "no original call identity for terminal export")
                stop_requested=self.store.stop_requested(self.active_call)
                if work.state in {"admitted", "claimed", "call_started"}:
                    raise ComponentWorkProductionError("work has no terminal native outcome")
                journal = self.guard.journals.get(work.spec.work_id)
                if journal is None:
                    raise ComponentWorkProductionError("no original accounting journal; no export reconstruction/replay")
                timeline = PostgresOperationalTimeline(self.engine, external_timeline_scope(
                    turn.tenant_id, turn.installation_id, "registered export scope required"))
                source_thread = PostgresConversationJournal._thread_id(turn)
                accounting_thread = PostgresConversationJournal._thread_id(journal._accounting)
                receipts = [r for r in self.guard.receipts if r["identity"] == identity]
                workspace_identity = {"execution_id": work.spec.execution_id,
                    "source": work.spec.source.model_dump(mode="json"),
                    "source_ingress_sha256": work.spec.source_ingress_sha256}
                from eva.contracts.canonical_json import canonical_sha256
                workspace = canonical_sha256(workspace_identity)
                programming_threads = ["programming-action:" + canonical_sha256(
                    {"workspace": workspace, "step": r["step_index"]}) for r in receipts
                    if r["receipt"]["call"]["name"] in {"use_programming_tool", "write_programming_file", "start_programming_process", "list_programming_tools"}]
                with self.guard._ledger() as ledger:
                    physical_requests = [row for row in ledger["requests"] if row["identity"] == identity]
                    physical_tools = [row for row in ledger["tools"] if row["identity"] == identity]
                original_usage=plain(journal.model_usage(turn))
                # model_usage maps each admitted unknown to partial(attempts>=1).
                # Its unmeasured/None aggregate therefore means exactly no admitted
                # requests for this original journal identity, not an unknown call.
                zero=(not physical_requests and original_usage["measurement"]=="unmeasured" and all(original_usage[k] is None for k in ("model_attempts","input_tokens","output_tokens","tool_calls")))
                return {"contract": "EvaNativeComponentWorkRaw/v1", "identity": identity,
                    "physical_requests": physical_requests, "physical_tools": physical_tools,
                    "complete_physical_usage":zero or bool(physical_requests) and all(row["usage"] is not None for row in physical_requests),
                    "original_journal_zero_requests_verified":zero,
                    "runtime_release": plain(self.runner.profile.release),
                    "strategy_projection": self.guard.native_projection_check()[0],
                    "stored_work": plain(work), "journal_usage": original_usage,
                    "source_events": plain(timeline.events(source_thread)),
                    "model_events": plain(timeline.events(accounting_thread)),
                    "programming_events": {thread: plain(timeline.events(thread)) for thread in sorted(set(programming_threads))},
                    "control_receipts":[v for v in self.guard.control_receipts if v["identity"]==identity],
                    "authorization_checks":[v for v in self.guard.authorization_checks if v["identity"]==identity],
                    "terminal_authority": {"source_scope_valid":True,"authorization_current":True,"stop_requested":stop_requested,"state":work.state},
                    "workspace_key": workspace, "programming_session_key":canonical_sha256({**workspace_identity,"work_id":work.spec.work_id}),
                    "action_receipts": receipts, "progress": plain(load_work_progress(self.engine, work.spec)),
                    "semantic_quality_claimed": False,
                    "artifact_evidence": None,
                    "export_sealed_for_assessment": False,
                    "incomplete_reason": "host readback and trusted run-binding seal still required"}

    def close(self):
        self.guard.close()
        self.engine.dispose()


def _compose(config, guard):
    import sqlalchemy as sa
    import eva
    from eva.bootstrap.runtime import RuntimeSettings, sqlalchemy_postgres_dsn
    from eva.operations.restore_activation import require_restore_activation
    from eva.adapters.postgres.operational_timeline import TimelineScope, external_timeline_scope
    from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.adapters.postgres.background_model_journal import BackgroundModelJournal
    from eva.bootstrap.delegated_digital_composition import build_digital_runner
    from eva.bootstrap.delegated_work_result_reader import DelegatedWorkResultReader
    from eva.bootstrap.delegated_digital_runner import _TaskModel
    from eva.application.memory import MemoryAuthority
    package = Path(eva.__file__).resolve().parent
    runtime = config["runtime"]
    for name, digest in runtime["source_hashes"].items():
        require(sha((package / name).read_bytes()) == digest, "actual installed source manifest shifted")
    holder = []
    def projection():
        metadata, literal = strategy_projection((package / "bootstrap/delegated_digital_runner.py").read_bytes(),
            _TaskModel._work_strategy, source_sha256=config["identity"]["source_sha256"],
            literal_sha256=runtime["strategy_sha256"])
        require(not holder or plain(holder[0].profile.release) == runtime["release"], "current factory release shifted")
        metadata["release"] = runtime["release"]
        return metadata, literal
    guard.native_projection_check = projection
    projection()
    settings = RuntimeSettings.from_environment()
    engine = sa.create_engine(sqlalchemy_postgres_dsn(settings.database_dsn), pool_pre_ping=True)
    try:
        require_restore_activation(engine)
        store = PostgresDelegatedWorkStore(engine, TimelineScope(UUID(settings.dashboard_tenant_id), UUID(settings.dashboard_installation_id)))
        guard.install()
        runner = build_digital_runner(settings, store)
        holder.append(runner)
        require(plain(runner.profile.release) == runtime["release"]
            and list(runner.profile.capability_ids) == runtime["capability_ids"]
            and runner.profile.model_profile_id == runtime["model_profile_id"], "fresh ordinary factory differs from host registration")
        # A separate readonly client configuration must produce this registered
        # release. Live workers cannot select it through the existing release fence.
        require(runner.profile.configuration_sha256 != config["live_model_profile_release_sha256"], "live worker could claim unobserved evaluation work")
        services = [tool.service for tool in runner.connector_tools if isinstance(tool, DelegatedWorkResultReader)]
        require(len(services) == 1, "ordinary owner admission service unavailable or ambiguous")
        require(set(config["case"]["required_action_names"]) <= set(runner.profile.capability_ids), "required native actions not in actual grant")
        maximum = min(config["case"]["case_provider_attempts"], runner.profile.grant().maximum_model_runs)
        require(maximum == config["case"]["case_provider_attempts"], "registered case exceeds actual profile grant")
        def journal_factory(turn, spec):
            journal = PostgresConversationJournal(engine, external_timeline_scope(turn.tenant_id,
                turn.installation_id, "host evaluation source scope required"), maximum_model_requests=maximum)
            accounted = BackgroundModelJournal(journal, turn, work_id=spec.work_id)
            guard.journals[spec.work_id] = accounted
            return accounted
        runner.journal_factory = journal_factory
        runner.browser_context = None  # No private browser context in isolated evaluation.
        runner.memory = MemoryAuthority(_EmptyEvaluationMemory(runtime["actor"], config["identity"], config["case"]["project_id"]))
        return _NativeEntry(settings, engine, store, runner, services[0], guard, config)
    except BaseException:
        guard.close()
        engine.dispose()
        raise



def _runtime_main(config_file):
    config = json.loads(read_private(config_file))
    guard = PhysicalAccounting(config["accounting_root"], config["policy"], execution_enabled=True)
    entry = _compose(config, guard)
    try:
        identity = config["identity"]
        with guard.case(identity):
            guard.start_case_once(identity)
            started = time.monotonic()
            turn, admission = entry.admit(identity, config["case"])
            admission_sha = exclusive(Path(config["runtime_output"]) / "admission.json", admission)
            disposition = asyncio.run(entry.run(identity))
            raw = entry.export(identity, turn)
            raw["origin"] = admission["origin"]
            raw["memory_disposition"] = admission["memory_disposition"]
            raw["admission_receipt_sha256"] = admission_sha
            raw["worker_disposition"] = disposition
            raw["timing"] = {"started_monotonic_seconds": started,
                "boot_id_sha256": sha(Path("/proc/sys/kernel/random/boot_id").read_bytes()),
                "elapsed_seconds": time.monotonic() - started,
                "boundary": "immediately_before_admission_to_terminal_original_export"}
            exclusive(Path(config["runtime_output"]) / "raw-native.json", raw)
    finally:
        entry.close()


def _workspace(settings, identity, case):
    # Matches ProgrammingActions' existing workspace identity; no pool/control call.
    actor = settings["runtime"]["actor"]
    source = {"tenant_id": actor["tenant_id"], "installation_id": actor["installation_id"],
        "principal_id": actor["principal_id"], "conversation_id": identity["conversation_id"],
        "message_id": identity["message_id"], "audience": {"kind": "private_person", "principal_id": actor["principal_id"]}}
    key = canonical_sha256({"execution_id": identity["task_id"], "source": source,
        "source_ingress_sha256": identity["source_ingress_sha256"]})
    path = Path(settings["workspace_root"]) / key
    require(not path.exists() and not path.is_symlink(), "evaluation workspace already exists; no retry")
    path.mkdir(mode=0o700)
    os.chown(path, 10001, 10001)
    for name, content in case["fixture_files"].items():
        exclusive(path / name, content.encode("utf-8"), uid=10001, raw=True)
    return path, key


def _launch(settings, config, source, identity, case, policy, run_root, ledger_root, authorize, binding):
    _authority(authorize, binding, settings["parent_deadline"])
    assets = directory(run_root / "runtime", uid=65532)
    output = directory(run_root / "native", uid=65532)
    exclusive(assets / "producer.py", Path(__file__).read_bytes(), uid=65532, mode=0o400, raw=True)
    exclusive(assets / "runner.py", source, uid=65532, mode=0o400, raw=True)
    client = read_private(settings["runtime"]["client_file"], uid=65532)
    require(sha(client) == settings["runtime"]["client_sha256"], "fixed readonly preview client changed")
    runtime = _selected_runtime(settings, source)
    runtime_config = {"runtime": runtime, "case": case, "identity": identity,
        "case_deadline_seconds": policy["case_deadline_seconds"][case["case_id"]],
        "execution_binding": binding, "parent_deadline": settings["parent_deadline"], "policy": policy,
        "accounting_root": _RUNTIME_ROOT + "/accounting", "runtime_output": _RUNTIME_ROOT + "/output",
        "live_model_profile_release_sha256": config["live_model_profile_release_sha256"]}
    exclusive(assets / "config.json", runtime_config, uid=65532, mode=0o400)
    mounts = [m for m in config["mounts"] if m["target"] != "/run/eva-component-evaluation/client.json"]
    require(len(mounts) + 1 == len(config["mounts"]), "existing component client mount absent or ambiguous")
    mounts += [{"type": "bind", "source": str(assets), "target": _RUNTIME_ROOT + "/assets", "read_only": True},
        {"type": "bind", "source": str(output), "target": _RUNTIME_ROOT + "/output", "read_only": False},
        {"type": "bind", "source": str(ledger_root), "target": _RUNTIME_ROOT + "/accounting", "read_only": False},
        {"type": "bind", "source": str(assets / "runner.py"), "target": config["package_path"] + "/bootstrap/delegated_digital_runner.py", "read_only": True},
        {"type": "bind", "source": settings["runtime"]["client_file"], "target": "/run/eva-component-evaluation/client.json", "read_only": True}]
    plan = {"image": config["image"], "environment": [k + "=" + v for k, v in config["environment"].items()],
        "user": config["user"], "working_dir": config["working_dir"], "entrypoint": ["python"],
        "command": ["-B", _RUNTIME_ROOT + "/assets/producer.py", "--runtime-config", _RUNTIME_ROOT + "/assets/config.json"],
        "mounts": mounts, "network_attachments": config["networks"]}
    require(plan["user"] in {"65532", "65532:65532"}, "runtime-owned producer files require the existing unprivileged UID")
    name = "eva-component-work-" + uuid4().hex
    argv = ["create", "--pull", "never", "--name", name, "--read-only", "--no-healthcheck",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--log-driver", "none",
        "--user", plan["user"], "--workdir", plan["working_dir"], "--entrypoint", "python",
        "--network", plan["network_attachments"][0]]
    for value in plan["environment"]:
        argv += ["--env", value]
    for mount in mounts:
        require("," not in mount["source"] and "," not in mount["target"], "unsupported mount path")
        argv += ["--mount", "type=" + mount["type"] + ",src=" + mount["source"] + ",dst=" + mount["target"] + (",readonly" if mount["read_only"] else "")]
    argv += [plan["image"], *plan["command"]]
    created = False
    try:
        result = _docker(argv)
        require(result["returncode"] == 0, "transient ordinary runner container creation failed")
        created = True
        for network in plan["network_attachments"][1:]:
            require(_docker(["network", "connect", network, name])["returncode"] == 0, "existing network attachment failed")
        inspected = _docker(["inspect", name])
        require(inspected["returncode"] == 0, "transient metadata unavailable")
        verify_launch(json.loads(inspected["stdout"])[0], plan)
        _authority(authorize, binding, settings["parent_deadline"])
        result = _docker(["start", "--attach", name])
        exclusive(run_root / "runtime-command.json", result)
        require(result["returncode"] == 0, "native case failed before trusted terminal export")
        raw_bytes = read_private(output / "raw-native.json", uid=65532)
        exclusive(run_root / "raw-native.json", raw_bytes, raw=True)
        admission_bytes = read_private(output / "admission.json", uid=65532)
        exclusive(run_root / "admission.json", admission_bytes, raw=True)
        return json.loads(raw_bytes)
    finally:
        if created:
            cleanup = _docker(["rm", "--force", name])
            exclusive(run_root / "container-cleanup.json", cleanup)
            require(cleanup["returncode"] == 0, "transient runner cleanup unconfirmed")


def _read_workspace_file(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        require(not path.is_symlink(), "workspace source symlink refused")
        return {"path": "/workspace/" + path.name, "status": "missing"}, None
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 10001 and info.st_nlink == 1
            and info.st_size <= 65536, "unsafe or oversized evaluation artifact")
        first = os.read(descriptor, 65537)
        os.lseek(descriptor, 0, os.SEEK_SET)
        second = os.read(descriptor, 65537)
        require(first == second and len(first) <= 65536, "artifact changed during independent readback")
        proof = {"contract": "EvaWorkerStrategyPhysicalReadback/v1", "workspace_key": path.parent.name,
            "path": "/workspace/" + path.name, "inode": info.st_ino, "uid": info.st_uid,
            "content_sha256": sha(first), "readback_sha256": sha(second)}
        return {"path": proof["path"], "content": first.decode("utf-8"), "sha256": sha(first),
            "readback_content": second.decode("utf-8"), "readback_sha256": sha(second),
            "readback_receipt_sha256": canonical_sha256(proof)}, first
    finally:
        os.close(descriptor)


def _artifacts(settings, identity, case, raw, workspace, run_root, guard, authorize, binding):
    _authority(authorize, binding, settings["parent_deadline"])
    require(raw["timing"]["boot_id_sha256"] == sha(Path("/proc/sys/kernel/random/boot_id").read_bytes()), "host and runner monotonic clocks differ")
    require(raw["identity"] == identity, "raw native case identity differs")
    require(raw["workspace_key"] == workspace.name, "native workspace differs from registered workspace")
    require_original_cleanup(raw)
    fixture_proof = {}
    for name, original in case["fixture_files"].items():
        if name in {"main.py", "result.json"}: continue  # Registered mutable task outputs.
        observation, actual = _read_workspace_file(workspace / name)
        require(actual == original.encode("utf-8"), "immutable evaluation fixture changed")
        fixture_proof[name] = observation
    files, contents = {}, {}
    for name in ("main.py", "result.json"):
        observation, content = _read_workspace_file(workspace / name)
        files["program" if name == "main.py" else "result"] = observation
        contents[name] = content
    exclusive(run_root / "physical-readbacks.json", {"files": files})
    program = contents["main.py"]
    checks, reports = [], []
    if program is not None and 1 <= len(program) <= 16384:
        from eva.operations.procedure_execution import ProcedureExecutor, ProcedureExecutionConfig
        executor = ProcedureExecutor(ProcedureExecutionConfig(**settings["procedure_execution"]))
        guard.authority_check = lambda: {**_authority(authorize, binding, settings["parent_deadline"]),
            "source_scope_valid": True, "authorization_current": True, "stop_respected": True}
        with guard.case(identity):
            for check in case["checks"]:
                guard.reserve_tool(SimpleNamespace(name="host_isolated_check", call_id="host-check:" + identity["session_id"] + ":" + check["check_id"]), kind="host_isolated_check")
                report = executor.execute(program.decode(), check["stdin"])
                require(report["source_sha256"] == sha(program)
                    and report["input_sha256"] == canonical_sha256(check["stdin"]), "actual isolated check source/input mismatch")
                reports.append({"check_id": check["check_id"], "report": report})
                exclusive(run_root / ("check-" + sha(check["check_id"].encode()) + ".json"), report)
                checks.append(check_record(report, check["check_id"]))
    else:
        checks = [{"check_id": c["check_id"], "status": "missing"} for c in case["checks"]]
    exclusive(run_root / "isolated-checks.json", reports)
    executions = _native_program_executions(raw, sha(program) if program is not None else None)
    timing = {"elapsed_ms": max(0, round((time.monotonic() - raw["timing"]["started_monotonic_seconds"]) * 1000)),
        "clock_boundary": "admission_start_to_terminal_and_readback_checks"}
    artifact = {"files": files, "checks": checks, "executions": executions, "progress": raw["progress"],
        "timing": timing,
        "progress_receipts": [{"step_index": r["step_index"], "receipt": r["receipt"]}
            for r in raw["action_receipts"] if r["receipt"]["call"]["name"] == "save_work_progress"],
        "meta": {"origin": raw["origin"], "memory_disposition": raw["memory_disposition"],
            "timing": timing,
            "memory_projection_excluded": True, "namespace": "component-work-evaluation",
            **({"parent_bindings": deepcopy(settings["parent_bindings"])} if "parent_bindings" in settings else {}),
            **({"runtime_admission_sha256": settings["runtime_admission_sha256"]} if "runtime_admission_sha256" in settings else {}),
            "execution_binding_ref": binding["execution_binding_ref"],
            "fixture_sha256": {name: sha(content.encode()) for name, content in case["fixture_files"].items()},
            "immutable_fixture_readbacks": fixture_proof,
            "runtime_release": raw["runtime_release"],
            "strategy_projection": raw["strategy_projection"], "raw_native_sha256": sha(read_private(run_root / "raw-native.json")),
            "native_execution_observed": bool(executions)}}
    exclusive(run_root / "artifacts.json", artifact)
    return artifact


def _seal_run(raw, artifact, run_root, policy, guard):
    identity = raw["identity"]
    require(raw["stored_work"]["state"] in {"succeeded", "verified", "failed", "stopped", "needs_help"}, "native outcome is not terminal")
    require(raw["terminal_authority"]["authorization_current"] is True
        and raw["terminal_authority"]["source_scope_valid"] is True
        and raw["terminal_authority"]["stop_requested"] is False, "native authority/stop unknown")
    usage = usage_export(raw)
    require_original_cleanup(raw)
    with guard.case(identity):
        ledger = guard.ensure_rankable()
    physical = [r for r in ledger["requests"] if r["identity"] == identity]
    require(physical == raw["physical_requests"], "original physical request accounting changed")
    physical_tools = [r for r in ledger["tools"] if r["identity"] == identity]
    observed = {r["receipt"]["call"]["call_id"] for r in raw["action_receipts"]}
    observed.update(r["id"] for r in raw["control_receipts"] if "id" in r)
    require(all(r["id"] in observed for r in physical_tools if r["kind"] != "host_isolated_check"), "physical tool lacks original receipt")
    require(all(r["identity"] == identity for r in raw["action_receipts"] + raw["control_receipts"]), "native receipt belongs to a different case")
    require(all(r["receipt"]["call"]["name"] in policy["allowed_action_names"] for r in raw["action_receipts"]), "native action outside isolated scope")
    require(raw["authorization_checks"] and all(r["identity"] == identity
        and r.get("authorization_current") is True and r.get("source_scope_valid") is True
        and r.get("stop_respected") is True for r in raw["authorization_checks"]), "original physical authorization checks missing")
    require(raw["memory_disposition"]["disposition"] == "host_evaluation_excluded"
        and raw["memory_disposition"]["thread_id"] == "work-memory:" + sha(identity["task_id"].encode())
        and raw["memory_disposition"]["origin_sha256"] == canonical_sha256(raw["origin"])
        and raw["origin"]["execution_binding_ref"] == policy["execution_binding"]["execution_binding_ref"], "host evaluation memory exclusion unbound")
    run = {"case_id": identity["case_id"], "task_id": identity["task_id"], "session_id": identity["session_id"],
        "source_ingress_sha256": identity["source_ingress_sha256"], "status": raw["stored_work"]["state"],
        "run_receipt_sha256": sha(read_private(run_root / "raw-native.json")), "usage": usage,
        "help": {"requests": 0, "interventions": 0},
        "hard_conditions": {"source_scope_valid": True, "stop_respected": True,
            "authorization_current": True, "no_forbidden_effects": True}, "artifact_evidence": artifact}
    exclusive(run_root / "sealed-run.json", run)
    return run


def _export_leg(assessment, runtime, source, runs, *, stage):
    config = assessment["work_evidence"]
    evidence_binding = {"stage": stage, **{k: config[k] for k in (
        "registration_sha256", "case_set_sha256", "baseline_sha256", "scope_sha256")},
        **config["runtime_bindings"], "source_sha256": sha(source), "strategy_sha256": runtime["strategy_sha256"]}
    for key, expected in config["runtime_bindings"].items():
        actual = runtime["release"]["model_profile_release_sha256"] if key == "tool_configuration_sha256" else runtime["release"][key]
        require(actual == expected, "actual factory runtime does not match consumer binding")
    bindings = {"contract": "EvaWorkerStrategyWorkRunBindings/v1", "binding": evidence_binding, "runs": []}
    for run in runs:
        row = {k: run[k] for k in ("case_id", "task_id", "session_id", "source_ingress_sha256", "run_receipt_sha256")}
        row.update({k + "_sha256": canonical_sha256(run[k]) for k in ("usage", "help", "hard_conditions", "artifact_evidence")})
        bindings["runs"].append(row)
    binding_sha = exclusive(_private_root(config["run_bindings_root"]) / (sha(source) + ".json"), bindings, mode=0o400)
    evidence = {"contract": "EvaWorkerStrategyWorkEvidence/v1",
        "binding": {**evidence_binding, "run_bindings_sha256": binding_sha}, "runs": runs}
    exclusive(_private_root(config["evidence_root"]) / (sha(source) + ".json"), evidence, mode=0o400)


def produce_work_evidence(*, leg, request, execution_binding, settings, assessment, authorize):
    """Write only genuine host-registered evidence; never score or retry."""
    require(os.geteuid() == 0, "native producer requires trusted host root")
    require(leg in {"baseline", "candidate"}, "producer supports registered baseline/candidate legs only")
    settings = validate_settings(settings, assessment)
    require(isinstance(execution_binding, dict) and execution_binding.get("contract") in {
        "EvaScopedComponentEvaluationBinding/v1", "EvaIndependentComponentWorkBinding/v1"}
        and isinstance(execution_binding.get("task_id"), str) and execution_binding["task_id"]
        and _sha(execution_binding.get("execution_binding_ref")), "verified host component binding required")
    _authority(authorize, execution_binding, settings["parent_deadline"])
    from eva.operations.component_evaluation import WORK_STRATEGY_PATH
    require(request.relative_path == WORK_STRATEGY_PATH and sha(request.source.encode()) != request.baseline_sha256,
        "native work requires one changed registered strategy source")
    baseline, configuration = _prepare_source(settings, request)
    source = baseline if leg == "baseline" else request.source.encode("utf-8")
    cases, stage = _cases(settings, assessment)
    expected = "EvaScopedComponentEvaluationBinding/v1" if stage == "development" else "EvaIndependentComponentWorkBinding/v1"
    require(execution_binding["contract"] == expected, "input stage and current host authority differ")
    require(stage != "independent_final" or {"parent_bindings", "runtime_admission_sha256"} <= set(settings), "final original parent or runtime admission binding missing")
    if "parent_bindings" in settings:
        require(settings["parent_bindings"]["task_id"] == execution_binding["task_id"], "host parent binding belongs to another task")
    policy = _policy(settings, assessment["work_evidence"], execution_binding, request, cases, stage=stage)
    registered_root = directory(Path(settings["output_root"]) / execution_binding["execution_binding_ref"])
    ledger_root = directory(Path(settings["accounting_root"]) / execution_binding["execution_binding_ref"], uid=65532)
    guard = PhysicalAccounting(ledger_root, policy, execution_enabled=True, ledger_uid=65532, host_binding=True)
    leg_root = directory(registered_root / leg)
    # A started or partially exported leg cannot be replayed. The coordinator
    # consumes already sealed evidence; it must not dispatch the producer again.
    once = leg_root / "once.json"
    _authority(authorize, execution_binding, settings["parent_deadline"])
    exclusive(once, {"execution_binding_ref": execution_binding["execution_binding_ref"],
        "source_sha256": sha(source), "policy_sha256": guard.policy_sha256})
    runs = []
    try:
        for case in cases:
            _authority(authorize, execution_binding, settings["parent_deadline"])
            identity = {**_case_identity(settings, assessment["work_evidence"], leg, case,
                execution_binding_ref=execution_binding["execution_binding_ref"], stage=stage), "source_sha256": sha(source)}
            run_root = directory(leg_root / sha(case["case_id"].encode()))
            workspace, _ = _workspace(settings, identity, case)
            raw = _launch(settings, configuration, source, identity, case, policy, run_root, ledger_root, authorize, execution_binding)
            artifact = _artifacts(settings, identity, case, raw, workspace, run_root, guard, authorize, execution_binding)
            _authority(authorize, execution_binding, settings["parent_deadline"])
            runs.append(_seal_run(raw, artifact, run_root, policy, guard))
        _authority(authorize, execution_binding, settings["parent_deadline"])
        guard.ensure_rankable()
        _export_leg(assessment, _selected_runtime(settings, source), source, runs, stage=stage)
        exclusive(leg_root / "completed.json", {"source_sha256": sha(source), "run_receipts": [r["run_receipt_sha256"] for r in runs]})
    except BaseException as error:
        exclusive(leg_root / "failure.json", {"error_type": type(error).__name__,
            "source_sha256": sha(source), "reset_or_retry_permitted": False})
        raise
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-config", required=True)
    _runtime_main(parser.parse_args().runtime_config)
