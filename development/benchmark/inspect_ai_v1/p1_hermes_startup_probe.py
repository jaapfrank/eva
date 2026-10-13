"""Read pinned Hermes startup settings without inference or borrowed credentials.

Run under an OS network namespace. Constructor-only cases deliberately use an
explicit inert key and no tools; they do not establish authenticated admission.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time


MODELS = {"contact": ("gpt-6-sol", "none"), "work": ("gpt-6-astra", "medium")}



def technical_error(exc):
    """No exception body, provider body, request or credential reaches technical receipts."""
    import re
    status = getattr(exc, "status_code", None)
    raw_code = getattr(exc, "code", None)
    code = raw_code if isinstance(raw_code, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", raw_code) else None
    return {"error_class": type(exc).__name__, "http_status": status if type(status) is int else None,
            "error_code": code}


class HermesSDKAccounting:
    """Native SDK observation only; ProcessSampleLedger owns IPC, never a child registry."""
    def __init__(self, ledger, deadline, output=None):
        import contextvars, threading
        self.ledger, self.deadline, self.output = ledger, deadline, output
        self.role = contextvars.ContextVar("hermes_native_model_role", default="helper")
        self.owner = contextvars.ContextVar("hermes_native_sdk_owner", default=None)
        self.lock, self.calls, self.streams, self.clients = threading.RLock(), [], {}, {}
        self.monotonic = time.monotonic
        self.wire_dispatches = []
        self.current_turn = None

    def check(self):
        from cp001_accounting import SampleStopped
        if self.ledger.stopped or self.monotonic() >= self.deadline:
            self.ledger.stop_async_attempts("native Hermes original sample deadline or stop")
            raise SampleStopped("native Hermes sample stopped before SDK dispatch")

    def _save(self):
        if self.output is not None:
            self.output.write_text(json.dumps(self.calls, indent=2) + "\n")

    def _pending(self, attempt):
        with self.ledger.lock:
            return self.ledger.attempts[attempt]["status"] == "pending"

    def _enter(self, kwargs):
        self.check()
        owner = self.owner.get()
        role = self._record(owner)["role"] if owner is not None else self.role.get()
        model, effort = ("gpt-6-sol", "none") if role == "contact" else ("gpt-6-astra", "medium")
        # Registered model normalization at the native SDK boundary, including nested factories.
        wire = dict(kwargs); wire["model"] = model
        if "reasoning" in wire or "messages" not in wire:
            wire["reasoning"] = {**(wire.get("reasoning") or {}), "effort": effort}
        else:
            wire["reasoning_effort"] = effort
        if isinstance(wire.get("extra_body"), dict) and "reasoning" in wire["extra_body"]:
            wire["extra_body"] = {**wire["extra_body"], "reasoning": {
                **(wire["extra_body"]["reasoning"] or {}), "effort": effort}}
        if owner is not None:
            return owner, wire, None
        attempt = self.ledger.begin(role, model, effort)
        record = {"attempt_id": attempt, "role": role, "model": model, "reasoning_effort": effort,
                  "status": "pending", "started_monotonic": self.monotonic(), "usage": None}
        with self.lock:
            self.calls.append(record); self._save()
        return attempt, wire, self.owner.set(attempt)

    def _record(self, attempt):
        return next(x for x in self.calls if x["attempt_id"] == attempt)

    @staticmethod
    def usage(value):
        get = value.get if isinstance(value, dict) else lambda k: getattr(value, k, None)
        usage = get("usage")
        if usage is None:
            response = get("response")
            if response is not None:
                usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
        if usage is None:
            return None
        uget = usage.get if isinstance(usage, dict) else lambda k: getattr(usage, k, None)
        incoming = uget("input_tokens")
        outgoing = uget("output_tokens")
        if incoming is None: incoming = uget("prompt_tokens")
        if outgoing is None: outgoing = uget("completion_tokens")
        total = uget("total_tokens")
        if all(type(x) is int and x >= 0 for x in (incoming, outgoing, total)) and incoming + outgoing == total:
            return {"input_tokens": incoming, "output_tokens": outgoing, "total_tokens": total}
        return None

    def observe(self, attempt, value):
        usage = self.usage(value)
        if usage is not None and self._pending(attempt):
            with self.lock:
                self._record(attempt).update(status="sdk_usage_observed", usage=usage,
                    settled_monotonic=self.monotonic()); self._save()
            try:
                self.ledger.finish(attempt, usage)
            except BaseException as exc:
                with self.lock:
                    self._record(attempt).update(status="parent_settlement_failed", error=technical_error(exc)); self._save()
                raise
            with self.lock:
                self._record(attempt).update(status="completed"); self._save()

    def fail(self, attempt, exc=None):
        if not self._pending(attempt):
            if exc is not None:
                with self.lock:
                    self._record(attempt).setdefault("post_settlement_error", technical_error(exc)); self._save()
            return
        if self._pending(attempt):
            # Preserve native class/status before IPC stop can cancel this owning task.
            with self.lock:
                self._record(attempt).update(status="parent_settlement_failed" if self._record(attempt)["usage"] is not None else "sdk_failed_usage_unknown",
                    error=technical_error(exc) if exc is not None else None,
                    settled_monotonic=self.monotonic()); self._save()
            self.ledger.incomplete(attempt, "sdk_failed_usage_unknown")

    def guard_http(self, request):
        """Last parent/deadline check immediately before one actual SDK HTTP dispatch."""
        self.check()
        attempt = self.owner.get()
        if attempt is None or not self._pending(attempt):
            raise ValueError("HTTP lacks pending native parent-owned SDK attempt")
        if request.method != "POST" or request.url.host != "chatgpt.com" or request.url.path != "/backend-api/codex/responses":
            raise ValueError("HTTP route differs from original native Responses route")
        body = json.loads(request.content)
        record = self._record(attempt)
        if body.get("model") != record["model"] or (body.get("reasoning") or {}).get("effort") != record["reasoning_effort"]:
            raise ValueError("native wire model/effort differs from admitted role")
        with self.lock:
            if attempt in self.wire_dispatches:
                raise ValueError("SDK retry is disabled; native retries require a new parent admission")
            if self.output is not None:
                # Retain the exact final serialized body for independent context readback.
                # Headers/auth never enter this private artifact or the technical receipt.
                raw = request.content
                name = f"model-context-{len(self.wire_dispatches):04d}.private.json"
                path = self.output.parent / name
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as capture:
                    capture.write(raw); capture.flush(); os.fsync(capture.fileno())
                record["model_context"] = {"file": name, "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw), "turn": dict(self.current_turn) if self.current_turn is not None else None}
            self.check()  # Retention I/O must not consume the remaining dispatch window.
            self.wire_dispatches.append(attempt)
            record["http_entered_monotonic"] = self.monotonic(); self._save()
        request.extensions["timeout"] = {key: max(.001, self.deadline-self.monotonic())
            for key in ("connect", "read", "write", "pool")}
        return attempt

    def http_finished(self, attempt, response=None, error=None):
        with self.lock:
            row = self._record(attempt)
            if response is not None: row["http_status"] = response.status_code
            if error is not None: row["http_error"] = technical_error(error)
            self._save()

    def sync(self, original, args, kwargs):
        attempt, wire, token = self._enter(kwargs)
        try:
            result = original(*args, **wire)
            self.observe(attempt, result)
            if wire.get("stream"):
                stream = _HermesObservedStream(self, attempt, result)
                with self.lock: self.streams[id(stream)] = stream
                return stream
            if self._pending(attempt): self.fail(attempt)
            return result
        except BaseException as exc:
            self.fail(attempt, exc); raise
        finally:
            if token is not None: self.owner.reset(token)

    async def asynchronous(self, original, args, kwargs):
        attempt, wire, token = self._enter(kwargs)
        try:
            if token is not None: self.ledger.track_async_attempt(attempt)
            result = await original(*args, **wire)
            self.observe(attempt, result)
            if wire.get("stream"):
                stream = _HermesObservedAsyncStream(self, attempt, result)
                with self.lock: self.streams[id(stream)] = stream
                return stream
            if self._pending(attempt): self.fail(attempt)
            return result
        except BaseException as exc:
            self.fail(attempt, exc); raise
        finally:
            if token is not None: self.owner.reset(token)

    def settled(self):
        return not any(x["status"] == "pending" for x in self.calls)


class _HermesObservedStream:
    def __init__(self, observer, attempt, stream):
        self.observer, self.attempt, self.stream = observer, attempt, stream
    def __getattr__(self, name): return getattr(self.stream, name)
    def __enter__(self): return self
    def __exit__(self, *_args): self.close()
    def __iter__(self):
        try:
            for event in self.stream:
                self.observer.observe(self.attempt, event)
                if getattr(event, "type", None) == "response.output_text.delta":
                    with self.observer.lock:
                        self.observer._record(self.attempt).setdefault("first_content_monotonic", self.observer.monotonic())
                yield event
        except BaseException as exc:
            self.observer.fail(self.attempt, exc); raise
        finally: self.close()
    def close(self):
        try: self.stream.close()
        finally:
            self.observer.fail(self.attempt)
            with self.observer.lock: self.observer.streams.pop(id(self), None)


class _HermesObservedAsyncStream:
    def __init__(self, observer, attempt, stream):
        self.observer, self.attempt, self.stream = observer, attempt, stream
    def __getattr__(self, name): return getattr(self.stream, name)
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): await self.close()
    async def __aiter__(self):
        try:
            async for event in self.stream:
                self.observer.observe(self.attempt, event)
                if getattr(event, "type", None) == "response.output_text.delta":
                    with self.observer.lock:
                        self.observer._record(self.attempt).setdefault("first_content_monotonic", self.observer.monotonic())
                yield event
        except BaseException as exc:
            self.observer.fail(self.attempt, exc); raise
        finally: await self.close()
    async def close(self):
        try: await self.stream.close()
        finally:
            self.observer.fail(self.attempt)
            with self.observer.lock: self.observer.streams.pop(id(self), None)


def read_hermes_saved_state(profile, session_id):
    """Native SQLite read-only reader and on-disk memory; no save/repair/model invocation."""
    from hermes_state import SessionDB
    reader = SessionDB(profile / "state.db", read_only=True)
    try:
        messages = reader.get_messages(session_id)
        session = reader.get_session(session_id)
    finally: reader.close()
    memories, hashes = {}, {}
    for name in ("MEMORY.md", "USER.md"):
        path = profile / "memories" / name
        if path.exists():
            raw = path.read_bytes(); memories[name] = raw.decode("utf-8")
            hashes[name] = hashlib.sha256(raw).hexdigest()
    replies = [m for m in messages if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"]]
    return {"session_id": session_id, "session": session, "messages": messages,
            "reply_text": replies[-1]["content"] if replies else None,
            "response_count": len(replies), "memory": memories, "memory_file_hashes": hashes}


def install_hermes_tool_accounting(observer, model_tools, agent_class, tool_limit, events, restore):
    """Reserve each native batch before dispatch; inline and registry share one cap."""
    from cp001_accounting import SampleStopped
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS
    active = {}

    def reserve(names):
        observer.check()
        if any(name != "memory" for name in names) or len(events) + len(names) > tool_limit:
            observer.ledger.stop_async_attempts("native tool/action not admitted or tool cap reached")
            raise SampleStopped("native tool/action not admitted or tool cap reached")
        rows = [{"name": name, "entered": False, "monotonic": observer.monotonic()} for name in names]
        events.extend(rows)
        return rows

    def enter(name, session_id, call_id):
        with observer.lock:
            observer.check()  # Also checked immediately before each actual inline/fallback dispatch.
            if name != "memory":
                observer.ledger.stop_async_attempts("native tool/action not admitted")
                raise SampleStopped("native tool/action not admitted")
            row = active.get((session_id, call_id)) if call_id else None
            if row is None:
                row = reserve([name])[0]
            row["entered"] = True

    original_tool = model_tools.handle_function_call
    restore.append((model_tools, "handle_function_call", original_tool))
    def tool(name, *pos, **kwargs):
        enter(name, kwargs.get("session_id"), kwargs.get("tool_call_id"))
        return original_tool(name, *pos, **kwargs)
    model_tools.handle_function_call = tool

    original_inline = INLINE_TOOL_EXECUTORS["memory"]
    # The dictionary is restored through its item setter by the native probe cleanup.
    class InlineItem:
        def __getattr__(self, name): return INLINE_TOOL_EXECUTORS[name]
        def __setattr__(self, name, value): INLINE_TOOL_EXECUTORS[name] = value
    restore.append((InlineItem(), "memory", original_inline))
    def inline_memory(actor, args, ctx):
        enter("memory", actor.session_id, ctx.tool_call_id)
        return original_inline(actor, args, ctx)
    INLINE_TOOL_EXECUTORS["memory"] = inline_memory

    original_batch = agent_class._execute_tool_calls
    restore.append((agent_class, "_execute_tool_calls", original_batch))
    def tool_batch(actor, message, *pos, **kwargs):
        calls = list(message.tool_calls or [])
        with observer.lock:
            rows = reserve([call.function.name for call in calls])
            bindings = [((actor.session_id, getattr(call, "id", None)), row) for call, row in zip(calls, rows)]
            prior = {key: active.get(key) for key, row in bindings}
            active.update(bindings)
        try:
            return original_batch(actor, message, *pos, **kwargs)
        finally:
            with observer.lock:
                for key, row in bindings:
                    if active.get(key) is row:
                        if prior[key] is None: active.pop(key, None)
                        else: active[key] = prior[key]
    agent_class._execute_tool_calls = tool_batch


def native_hermes_turns(participant):
    """Use the admitted original packet's count without changing any turn."""
    turns = participant["turns"]
    if not isinstance(turns, list) or not turns:
        raise ValueError("native Hermes original turns must be a nonempty list")
    if any(not isinstance(t, dict) or type(t.get("ordinal")) is not int for t in turns):
        raise ValueError("native Hermes original ordinal must be an integer")
    if [t["ordinal"] for t in turns] != list(range(len(turns))):
        raise ValueError("native Hermes original ordinals must be contiguous and ordered")
    return turns


def native_hermes_identity(participant, turn):
    """Forward original actor/audience controls to existing native origin fields."""
    identity = turn.get("native_identity")
    if identity is None:
        if participant["provisioning"].get("actors"):
            raise ValueError("multi-person native input cannot use a fixed actor fallback")
        return {"platform": "cli", "user_id": participant["provisioning"]["actor_id"]}
    keys = {"user_id", "chat_id", "chat_type", "gateway_session_key"}
    if not isinstance(identity, dict) or set(identity) != keys:
        raise ValueError("native Hermes identity requires every original origin field")
    if any(not isinstance(v, str) or not v for v in identity.values()):
        raise ValueError("native Hermes origin fields must be nonempty strings")
    if identity["chat_type"] not in ("dm", "group"):
        raise ValueError("native Hermes audience is not admitted")
    actors = participant["provisioning"].get("actors")
    if actors:
        matches = [actor for actor in actors if actor["actor_index"] == turn.get("actor_index")]
        if len(matches) != 1 or matches[0]["user_id"] != identity["user_id"]:
            raise ValueError("native Hermes user differs from original actor binding")
    return {"platform": "cli", **identity}


def readback_hermes_identity(snapshot, identity):
    """Verify native durable origin; profile-global memory remains untouched."""
    if "chat_id" not in identity:
        return  # Legacy single-actor binding, unchanged.
    session = snapshot.get("session") or {}
    origin = session.get("origin_json")
    if isinstance(origin, str):
        origin = json.loads(origin)
    if not isinstance(origin, dict):
        raise ValueError("native Hermes durable origin absent")
    for key in ("user_id", "chat_id", "chat_type"):
        if session.get(key) != identity[key] or origin.get(key) != identity[key]:
            raise ValueError("native Hermes durable person/audience differs")
    if session.get("session_key") != identity["gateway_session_key"]:
        raise ValueError("native Hermes durable conversation key differs")
    if origin.get("platform") != identity["platform"]:
        raise ValueError("native Hermes durable platform differs")


def run_native_cm(args):
    """One reviewed native Hermes CM sample. The parent alone opens the private registry."""
    import asyncio, functools, threading
    from cp001_accounting import SampleStopped
    from cp001_process_accounting import ProcessSampleLedger
    source, own, profile, output = args.source.resolve(), args.owned_profile.resolve(), args.profile.resolve(), args.output.resolve()
    if not args.parent_accounting_control or not args.absolute_deadline_file or not args.sample_id:
        raise ValueError("final native Hermes requires parent/deadline/sample binding before input decode")
    # Existing native profile mode provides same-participant own global auth fallback.
    if profile.parent.name != "profiles" or profile.parent.parent != own or (profile / "auth.json").exists():
        raise ValueError("fresh Hermes profile must use own native global auth fallback, no token copy")
    if output.exists() or (source / ".env").exists():
        raise ValueError("closed native output/source dotenv cannot be reused")
    output.mkdir(mode=0o700, parents=True)
    control = json.loads(args.parent_accounting_control.read_text())
    deadline = json.loads(args.absolute_deadline_file.read_text())["monotonic_deadline"]
    # Deadline is a separate durable receipt; the IPC constructor receives only its existing contract.
    if control.get("monotonic_deadline") != deadline:
        raise ValueError("native control and durable absolute deadline differ")
    ledger = ProcessSampleLedger(**{k:control[k] for k in ("socket_path","namespace","request_id","token")})
    observer = HermesSDKAccounting(ledger, deadline, output / "sdk-attempts-technical.json")
    restore, agents, turn_results, observed_responses, tool_events, error = [], [], [], [], [], None
    calendar = None; db = None; close_ack = False; close_errors = []; case = None; credentials = None
    clock_restored = False; pending_native = None; expected_turn_count = 0
    try:
        observer.check()  # No private case bytes before actual parent/deadline gate.
        with os.fdopen(args.native_cm_input_fd) as fd: case = json.load(fd)
        if case.get("contract") != "Ev003PrivateNativeInputs/v1" or case.get("oracle_included") is not False:
            raise ValueError("exact original native input contract required")
        participant = case["participants"]["hermes"]; turns = native_hermes_turns(participant)
        expected_turn_count = len(turns)
        identities = [native_hermes_identity(participant, turn) for turn in turns]
        session_bindings = {}
        for turn, identity in zip(turns, identities):
            binding = (turn["context_id"], tuple(sorted(identity.items())))
            prior = session_bindings.setdefault(turn["session_index"], binding)
            if prior != binding:
                raise ValueError("one original native session cannot change person/audience")
        retained = {key: os.environ[key] for key in ("PATH","HOME","LANG","PYTHONPATH","HTTPS_PROXY","HTTP_PROXY","ALL_PROXY","NO_PROXY") if key in os.environ}
        os.environ.clear(); os.environ.update(retained)
        os.environ.update(HERMES_HOME=str(profile), CODEX_HOME=str(profile / "empty-codex-home"), HERMES_CWD=str(profile))
        os.chdir(profile); sys.path.insert(0,str(source))
        from hermes_cli.auth import resolve_codex_runtime_credentials
        from hermes_cli.config import load_config_readonly
        from run_agent import AIAgent
        from hermes_state import SessionDB
        from agent import chat_completion_helpers as calls
        import model_tools
        credentials = resolve_codex_runtime_credentials(read_only=True)
        if credentials["source"] != "hermes-auth-store" or load_config_readonly().get("auth",{}).get("adopt_external_logins") is not False:
            raise ValueError("original own provider profile required")
        from openai.resources.responses.responses import Responses, AsyncResponses
        from openai.resources.chat.completions.completions import Completions, AsyncCompletions
        for cls, asynchronous in ((Responses,False),(Completions,False),(AsyncResponses,True),(AsyncCompletions,True)):
            original = cls.create; restore.append((cls,"create",original))
            if asynchronous:
                async def wrapped(resource,*pos,_original=original,**kwargs):
                    if resource._client.max_retries != 0:
                        raise ValueError("original native SDK retry zero required")
                    observer.clients[id(resource._client)] = resource._client
                    return await observer.asynchronous(functools.partial(_original,resource),pos,kwargs)
            else:
                def wrapped(resource,*pos,_original=original,**kwargs):
                    if resource._client.max_retries != 0:
                        raise ValueError("original native SDK retry zero required")
                    observer.clients[id(resource._client)] = resource._client
                    return observer.sync(functools.partial(_original,resource),pos,kwargs)
            cls.create = wrapped
        import httpx
        for cls, asynchronous in ((httpx.Client, False), (httpx.AsyncClient, True)):
            original = cls.send; restore.append((cls,"send",original))
            if asynchronous:
                async def send(client,request,*pos,_original=original,**kwargs):
                    attempt = observer.guard_http(request)
                    try:
                        response = await _original(client,request,*pos,**{**kwargs,"follow_redirects":False})
                        observer.http_finished(attempt,response); return response
                    except BaseException as exc:
                        observer.http_finished(attempt,error=exc); raise
            else:
                def send(client,request,*pos,_original=original,**kwargs):
                    attempt = observer.guard_http(request)
                    try:
                        response = _original(client,request,*pos,**{**kwargs,"follow_redirects":False})
                        observer.http_finished(attempt,response); return response
                    except BaseException as exc:
                        observer.http_finished(attempt,error=exc); raise
            cls.send = send
        for name in ("_dispatch_nonstreaming_api_request","_stream_codex_passthrough"):
            original = getattr(calls,name); restore.append((calls,name,original))
            def foreground(actor,*pos,_original=original,**kwargs):
                token = observer.role.set("contact" if any(actor is a for a in agents) else "helper")
                try:return _original(actor,*pos,**kwargs)
                finally:observer.role.reset(token)
            setattr(calls,name,foreground)
        install_hermes_tool_accounting(observer, model_tools, AIAgent, args.tool_limit, tool_events, restore)
        from ev003_python_calendar import ScenarioCalendar
        from datetime import datetime
        first = turns[0]["clock"]; initial = datetime.fromisoformat(case["clock"]["initial"].replace("Z","+00:00"))
        offset = (initial.timestamp()-time.time())*1000
        clock = output / "scenario-clock.json"
        clock.write_text(json.dumps({"contract":"EvaScenarioCalendar/v1","generation":first["generation"],"offsetMilliseconds":offset+first["offset_delta_milliseconds"]}))
        # Token expiry uses the actual wall clock; scenario time belongs to native task context.
        import hermes_cli.auth_codex as native_auth
        import time_machine
        original_auth_time=native_auth.time;restore.append((native_auth,"time",original_auth_time))
        class ActualAuthClock:
            time = staticmethod(time_machine.escape_hatch.time.time)
            def __getattr__(self, name): return getattr(original_auth_time, name)
        native_auth.time=ActualAuthClock()
        calendar=ScenarioCalendar(clock);calendar.refresh()
        db=SessionDB(profile / "state.db")
        prior_session=None; actor=None
        from agent.review_idle_queue import QUEUE
        def settle():
            while True:
                observer.check()
                pending_reviews=sum(getattr(a,"_background_review_run",None) is not None for a in agents)
                if observer.settled() and not observer.streams and not pending_reviews and QUEUE.pending_count()==0:
                    return {"sdk_pending":0,"native_reviews_pending":0,"idle_queue_pending":0}
                time.sleep(min(.05,max(.001,deadline-observer.monotonic())))
        for turn, identity in zip(turns, identities):
            if turn_results:
                settle();state={"contract":"EvaScenarioCalendar/v1","generation":turn["clock"]["generation"],"offsetMilliseconds":offset+turn["clock"]["offset_delta_milliseconds"]}
                clock.write_text(json.dumps(state));assert calendar.refresh()==state
            if prior_session != turn["session_index"]:
                if actor is not None: actor.close();settle()
                actor=AIAgent(model="gpt-6-sol",provider="openai-codex",base_url=credentials["base_url"],api_key=credentials["api_key"],
                    reasoning_config={"effort":"none"},enabled_toolsets=["memory"],quiet_mode=True,load_soul_identity=True,
                    skip_memory=False,skip_background_review=False,session_id=turn["context_id"],session_db=db,
                    **identity,cwd=str(profile),run_budget_seconds=max(.001,deadline-observer.monotonic()))
                agents.append(actor);prior_session=turn["session_index"]
            with observer.lock:
                observer.current_turn = {"ordinal": turn["ordinal"], "session_index": turn["session_index"],
                    "calendar_generation": turn["clock"]["generation"]}
            started=observer.monotonic();result=actor.run_conversation(turn["text"])
            final=result.get("final_response")
            if not isinstance(final,str):raise ValueError("native final response absent")
            observed_responses.append({"ordinal":turn["ordinal"],"context_id":actor.session_id,
                "native_result":result,"elapsed_seconds":observer.monotonic()-started})
            (output/"native-responses.private.json").write_text(json.dumps(observed_responses,indent=2,default=str)+"\n")
            settlement=settle();snapshot=read_hermes_saved_state(profile,actor.session_id)
            if snapshot["reply_text"] != final:raise ValueError("durable native Hermes response differs")
            readback_hermes_identity(snapshot, identity)
            entry={"ordinal":turn["ordinal"],"session_index":turn["session_index"],"context_id":actor.session_id,
                   "native_result":result,"independent_readback":snapshot,"settlement":settlement,
                   "elapsed_seconds":observer.monotonic()-started,"calendar":calendar.refresh()}
            turn_results.append(entry);(output/"cm-turns.private.json").write_text(json.dumps(turn_results,indent=2,default=str)+"\n")
            with observer.lock:
                observer.current_turn = None
    except BaseException as exc:
        error=technical_error(exc)
    finally:
        credentials = None
        if agents:
            from agent.background_review import cancel_background_review_for_live_turn
        for actor in agents:
            try:cancel_background_review_for_live_turn(actor);actor.close()
            except BaseException as exc:close_errors.append(technical_error(exc))
        for stream in list(observer.streams.values()):
            try:
                if isinstance(stream,_HermesObservedAsyncStream):asyncio.run(stream.close())
                else:stream.close()
            except BaseException as exc:close_errors.append(technical_error(exc))
        for client in observer.clients.values():
            try:
                close=client.close
                if asyncio.iscoroutinefunction(close):asyncio.run(close())
                else:close()
            except BaseException as exc:close_errors.append(technical_error(exc))
        if agents:
            from agent.review_idle_queue import QUEUE
            pending_native = {"background_reviews": sum(getattr(a,"_background_review_run",None) is not None for a in agents),
                "idle_queue": QUEUE.pending_count()}
        else: pending_native = {"background_reviews":0,"idle_queue":0}
        if db is not None:
            try: db.close()
            except BaseException as exc: close_errors.append(technical_error(exc))
        if calendar is not None:
            try: calendar.restore(); clock_restored = True
            except BaseException as exc: close_errors.append(technical_error(exc))
        for obj,name,original in reversed(restore):setattr(obj,name,original)
        close_ack=ledger.close(settled=observer.settled() and not observer.streams and not close_errors
            and pending_native=={"background_reviews":0,"idle_queue":0})
    report={"contract":"HermesNativeCmParentOutcome/v1","case_id":case["case_id"] if case is not None else None,"epoch":case["epoch"] if case is not None else None,"participant":"hermes",
        "turn_count":len(turn_results),"expected_turn_count":expected_turn_count,"native_responses_received":len(observed_responses),"successful_result":expected_turn_count > 0 and len(turn_results)==expected_turn_count and error is None,
        "error":error,"sdk_attempts":observer.calls,"tool_dispatches":tool_events,"parent_mirror":ledger.report(),
        "parent_close_acknowledged":close_ack,"close_errors":close_errors,"scenario_clock_restored":clock_restored,"native_pending_at_close":pending_native,
        "external_inference_requests":len(observer.wire_dispatches),
        "native_auth_copied":False,"speech_requests":0,"external_actions":0}
    (output/"native-cm-technical.json").write_text(json.dumps(report,indent=2)+"\n")
    if not report["successful_result"]:raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--role", choices=MODELS, required=True)
    parser.add_argument("--request-readback", action="store_true",
                        help="Also inspect native request assembly; never invoke the SDK")
    parser.add_argument("--native-cm-input-fd", type=int)
    parser.add_argument("--parent-accounting-control", type=Path)
    parser.add_argument("--absolute-deadline-file", type=Path)
    parser.add_argument("--owned-profile", type=Path)
    parser.add_argument("--sample-id")
    parser.add_argument("--tool-limit", type=int, default=16)
    args = parser.parse_args()
    if args.native_cm_input_fd is not None:
        run_native_cm(args)
        return
    source = args.source.resolve()
    profile = args.profile.resolve()
    output = args.output.resolve()
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    if (source / ".env").exists():
        raise RuntimeError("Source dotenv must be absent for this isolated startup check")
    # Do not inherit paid-provider keys or native auth/config locations.
    retained = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
    os.environ.clear()
    os.environ.update(retained)
    os.environ["HERMES_HOME"] = str(profile)
    os.environ["CODEX_HOME"] = str(profile / "empty-codex-home")
    os.environ["HERMES_CWD"] = str(profile)
    os.chdir(profile)
    sys.path.insert(0, str(source))
    model, effort = MODELS[args.role]
    config = {
        "model": {"provider": "openai-codex", "default": model,
                  "base_url": "https://chatgpt.com/backend-api/codex"},
        "agent": {"reasoning_effort": effort},
        "auth": {"adopt_external_logins": False},
    }
    # JSON is a YAML subset; native Hermes reads this file, not a replacement loader.
    (profile / "config.yaml").write_text(json.dumps(config, indent=2) + "\n")
    report = {
        "contract": "HermesNativeStartupReadback/v1", "role": args.role,
        "sourceCommit": subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
        "sourceDirty": bool(subprocess.check_output(
            ["git", "-C", str(source), "status", "--porcelain"], text=True).strip()),
        "lockSha256": hashlib.sha256((source / "uv.lock").read_bytes()).hexdigest(),
        "packages": {name: importlib.metadata.version(name)
                     for name in ("hermes-agent", "openai", "httpx", "pydantic")},
        "externalInferenceRequests": 0, "authRequests": 0,
        "networkIsolation": "Caller must use unshare -n; no provider transport invoked",
        "constructorCredential": "Inert explicit startup key; not native authentication",
        "src/tools": "Empty constructor toolsets; no tool execution or comparison admission",
        "admission": "not_admitted",
    }
    log = io.StringIO()
    started = time.monotonic()
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            from hermes_cli.config import load_config_readonly
            from hermes_cli.auth import AuthError, resolve_codex_runtime_credentials
            from agent.credential_sources import adopt_external_logins_enabled
            from run_agent import AIAgent
            native = load_config_readonly()
            report["externalLoginAdoption"] = adopt_external_logins_enabled()
            try:
                credentials = resolve_codex_runtime_credentials(read_only=True)
                report["auth"] = {"state": "present_read_only", "source": credentials.get("source")}
                del credentials
            except AuthError as exc:
                report["auth"] = {"state": "missing_or_unavailable", "code": exc.code}
            agent = AIAgent(
                model=native["model"]["default"], provider=native["model"]["provider"],
                base_url=native["model"]["base_url"], api_key="inert-startup-only",
                reasoning_config={"effort": native["agent"]["reasoning_effort"]},
                enabled_toolsets=[], quiet_mode=True, skip_context_files=True,
                skip_memory=True, skip_background_review=True, cwd=str(profile),
            )
            report["nativeReadback"] = {"model": agent.model, "provider": agent.provider,
                "apiMode": agent.api_mode, "baseUrl": agent.base_url,
                "reasoning": agent.reasoning_config, "toolCount": len(agent.tools or [])}
            assert agent.model == model and agent.reasoning_config == {"effort": effort}
            assert agent.api_mode == "codex_responses" and not report["externalLoginAdoption"]
            if args.request_readback:
                prepared = agent._build_api_kwargs([
                    {"role": "system", "content": "Native request configuration check."},
                    {"role": "user", "content": "Synthetic input; do not execute."},
                ])
                report["nativeRequestReadback"] = {
                    "model": prepared.get("model"), "reasoning": prepared.get("reasoning"),
                    "toolCount": len(prepared.get("src/tools") or []),
                    "store": prepared.get("store"), "stream": prepared.get("stream"),
                    "inputItemCount": len(prepared.get("input") or []),
                    "sdkInvoked": False,
                }
                assert prepared.get("model") == model
                assert (prepared.get("reasoning") or {}).get("effort") == effort
                report["requestStatus"] = "native_request_settings_passed"
            agent.client.close()
            report["status"] = "constructor_readback_passed"
    except Exception as exc:
        report["status"] = "failed"
        report["errorType"] = type(exc).__name__
        report["errorCode"] = getattr(exc, "code", None)
    report["durationSeconds"] = round(time.monotonic() - started, 3)
    output.write_text(json.dumps(report, indent=2) + "\n")
    output.with_suffix(".log").write_text(log.getvalue())
    print(json.dumps(report))
    if report["status"] != "constructor_readback_passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
