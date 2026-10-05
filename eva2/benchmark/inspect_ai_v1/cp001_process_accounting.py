"""Small private Unix IPC binding for one comparison-owned sample ledger.

Only the parent owns accounting authority. Child close receipts concern tracked
HTTP attempts, never the complete native agent, cloud cancellation or DB work.
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import threading

from cp001_accounting import SampleStopped
from cp001_native import NativeProviderControl

_MAX_FRAME = 65536


def _read(stream):
    line = stream.readline(_MAX_FRAME + 1)
    if not line:
        raise EOFError("accounting peer disconnected")
    if len(line) > _MAX_FRAME or not line.endswith(b"\n"):
        raise ValueError("invalid accounting frame")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("accounting object required")
    return value


def _write(sock, lock, value):
    raw = json.dumps(value, separators=(",", ":")).encode() + b"\n"
    if len(raw) > _MAX_FRAME:
        raise ValueError("accounting frame too large")
    with lock:
        sock.sendall(raw)


class ParentProcessAccounting:
    def __init__(self, ledger, request_id, socket_path, *, workers, action_control=None, action_deadline_monotonic=None):
        if not workers or any(not key or not roles for key, roles in workers.items()):
            raise ValueError("explicit worker namespaces and roles required")
        self.actions = None
        if action_control is not None:
            from ev003_action_accounting import ActionBudget
            self.actions = ActionBudget(ledger, action_control, deadline_monotonic=action_deadline_monotonic)
        self.ledger, self.request_id = ledger, request_id
        self.path = Path(socket_path)
        self.lock = threading.RLock()
        self.workers = {name: {"roles": frozenset(roles), "token": secrets.token_hex(32),
            "control": NativeProviderControl(ledger, request_id), "connected": False,
            "seen": False, "terminal": None, "disconnect": None, "events": {},
            "send_lock": threading.Lock(), "socket": None} for name, roles in workers.items()}
        self.errors, self.threads, self.connections = [], [], []
        self.server = None
        self.closing = False

    def credentials(self, namespace):
        return {"socket_path": str(self.path), "namespace": namespace,
            "request_id": self.request_id, "token": self.workers[namespace]["token"]}

    def start(self):
        if self.server is not None or self.path.exists():
            raise ValueError("new accounting socket required")
        if not self.path.is_absolute() or self.path.parent.stat().st_mode & 0o077:
            raise ValueError("private absolute socket directory required")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.server.listen(len(self.workers))
        self.server.settimeout(0.2)
        thread = threading.Thread(target=self._accept, daemon=True)
        self.threads.append(thread)
        thread.start()
        return self

    def _accept(self):
        while not self.closing:
            try:
                sock, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.lock:
                self.connections.append(sock)
            thread = threading.Thread(target=self._serve, args=(sock,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def _serve(self, sock):
        worker = None
        stream = sock.makefile("rb")
        try:
            hello = _read(stream)
            with self.lock:
                worker = self.workers.get(hello.get("namespace"))
                if (worker is None or worker["seen"] or hello.get("request_id") != self.request_id
                    or not secrets.compare_digest(str(hello.get("token", "")), worker["token"])):
                    worker = None
                    raise ValueError("accounting peer identity rejected")
                worker.update(connected=True, seen=True, socket=sock)
            _write(sock, worker["send_lock"], {"hello": True, "stopped": self.ledger.stopped})
            while True:
                message = _read(stream)
                rpc = message.get("rpc")
                if message.get("op") == "stop":
                    self.stop("child requested accounting stop")
                    continue
                if type(rpc) is not int:
                    raise ValueError("accounting rpc identity required")
                with self.lock:
                    if message.get("op") == "event":
                        event = message["event"]
                        if event.get("provider_event") == "begin" and event.get("role") not in worker["roles"]:
                            raise ValueError("worker role rejected")
                        decision = worker["control"].handle(event)
                        event_id = event.get("event_id")
                        if event.get("provider_event") == "begin":
                            worker["events"].setdefault(event_id, {"attempt_id": decision.get("attempt_id"), "settled": False})
                        elif event.get("provider_event") == "settle" and event_id in worker["events"]:
                            row = worker["events"][event_id]
                            attempt = row["attempt_id"]
                            row["settled"] = (attempt is None or self.ledger.attempts[attempt]["status"] != "pending")
                        response = {"rpc": rpc, "decision": decision}
                    elif message.get("op") in {"action_reserve", "action_settle"}:
                        if self.actions is None:
                            raise ValueError("actions not enabled for sample")
                        namespace = hello["namespace"]
                        if message["op"] == "action_reserve":
                            decision = self.actions.reserve(namespace, message["name"], message["binding"], message["config_sha256"])
                        else:
                            self.actions.settle(namespace, message["action_id"], message["status"], message["entered"])
                            decision = {"settled": True}
                        response = {"rpc": rpc, "decision": decision}
                    elif message.get("op") == "close":
                        pending = any(not row["settled"] for row in worker["events"].values())
                        action_pending = self.actions.pending(hello["namespace"]) if self.actions else 0
                        worker["terminal"] = {"reported_settled": message.get("settled") is True,
                            "active_tasks": message.get("active_tasks"), "pending_local": message.get("pending_local"),
                            "cancel_errors": message.get("cancel_errors"),
                            **({"pending_actions": action_pending} if self.actions else {}),
                            "tracked_http_settled": message.get("settled") is True and not pending
                                and message.get("active_tasks") == 0 and message.get("pending_local") == 0
                                and message.get("cancel_errors") == []}
                        response = {"rpc": rpc, "closed": True}
                    else:
                        raise ValueError("unknown accounting operation")
                _write(sock, worker["send_lock"], response)
                if self.ledger.stopped:
                    self._broadcast_stop()
                if message.get("op") == "close":
                    if not worker["terminal"]["tracked_http_settled"] or (self.actions and worker["terminal"]["pending_actions"]):
                        self.stop("child closed without tracked HTTP settlement")
                    return
        except (EOFError, OSError, ValueError, KeyError, TypeError) as error:
            with self.lock:
                if worker is not None:
                    worker["disconnect"] = type(error).__name__
                    if self.actions:
                        self.actions.disconnect(hello["namespace"])
                    for row in worker["events"].values():
                        if row["attempt_id"] is not None and not row["settled"]:
                            self.ledger.incomplete(row["attempt_id"], "process_eof_usage_unknown")
                self.errors.append(type(error).__name__)
            self.stop("accounting connection failed")
        finally:
            with self.lock:
                if worker is not None:
                    worker["connected"] = False
            stream.close()
            sock.close()

    def _broadcast_stop(self):
        with self.lock:
            targets = [(w["socket"], w["send_lock"]) for w in self.workers.values() if w["connected"]]
        for sock, lock in targets:
            try:
                _write(sock, lock, {"stop": True})
            except OSError:
                pass  # The peer reader records disconnect and unresolved ownership.

    def stop(self, reason="comparison sample stopped"):
        self.ledger.stop_async_attempts(reason)
        self._broadcast_stop()

    def report(self):
        with self.lock:
            return {"ledger": self.ledger.report(), "errors": list(self.errors),
                **({"actions": self.actions.report()} if self.actions else {}),
                "workers": {name: {"connected": w["connected"], "seen": w["seen"],
                    "terminal": w["terminal"], "disconnect": w["disconnect"],
                    "unsettled_events": sum(not e["settled"] for e in w["events"].values())}
                    for name, w in self.workers.items()},
                "scope": "Child terminal evidence covers tracked HTTP only; native process/DB completion is separate."}

    def close(self):
        self.stop("accounting parent closing")
        self.closing = True
        if self.server is not None:
            self.server.close()
        for sock in self.connections:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for thread in self.threads:
            thread.join(timeout=2)
        if any(thread.is_alive() for thread in self.threads):
            self.errors.append("accounting_thread_not_settled")
        if self.server is not None:
            self.path.unlink(missing_ok=True)


class ProcessSampleLedger:
    def __init__(self, socket_path, namespace, request_id, token, *, rpc_timeout=10):
        self.namespace, self.request_id = namespace, request_id
        self.lock = threading.RLock()
        self.attempts, self.async_attempts, self.cancel_errors = {}, {}, []
        self.actions = {}
        self.stop_requested, self._stopped = False, None
        self._events, self._waiters = {}, {}
        self._rpc_lock, self._send_lock = threading.Lock(), threading.Lock()
        self._rpc_number = self._event_number = 0
        self._rpc_timeout = rpc_timeout
        self._closed = False
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.settimeout(rpc_timeout)
        self._socket.connect(str(socket_path))
        self._stream = self._socket.makefile("rb")
        _write(self._socket, self._send_lock, {"namespace": namespace, "request_id": request_id, "token": token})
        hello = _read(self._stream)
        if hello.get("hello") is not True:
            raise ValueError("parent accounting handshake rejected")
        self._socket.settimeout(None)
        if hello.get("stopped"):
            self._stopped = "parent already stopped"
            self.stop_requested = True
        self._reader = threading.Thread(target=self._receive, daemon=True)
        self._reader.start()

    @property
    def stopped(self):
        return self._stopped

    @stopped.setter
    def stopped(self, reason):
        if reason and not self._stopped:
            self._stopped = str(reason)
            self.stop_requested = True
            try:
                _write(self._socket, self._send_lock, {"op": "stop"})
            except OSError:
                pass
            self._cancel_local()

    def _cancel_local(self):
        with self.lock:
            owned = list(self.async_attempts.items())
        for attempt, (loop, task) in owned:
            def cancel(attempt=attempt, loop=loop, task=task):
                with self.lock:
                    if self.async_attempts.get(attempt) == (loop, task) and not task.done():
                        task.cancel()
            try:
                loop.call_soon_threadsafe(cancel)
            except RuntimeError:
                self.cancel_errors.append("owned loop already closed")

    def _receive(self):
        try:
            while not self._closed:
                message = _read(self._stream)
                if message.get("stop"):
                    self._stopped = self._stopped or "parent accounting stopped"
                    self.stop_requested = True
                    self._cancel_local()
                    continue
                with self._rpc_lock:
                    waiter = self._waiters.get(message.get("rpc"))
                if waiter is None:
                    raise ValueError("unbound accounting response")
                waiter[1].append(message)
                waiter[0].set()
        except (EOFError, OSError, ValueError):
            if not self._closed:
                self._stopped = "parent disconnected; accounting unknown"
                self.stop_requested = True
                self._cancel_local()
            with self._rpc_lock:
                for event, values in self._waiters.values():
                    event.set()

    def _rpc(self, **message):
        with self._rpc_lock:
            self._rpc_number += 1
            number = self._rpc_number
            event, values = threading.Event(), []
            self._waiters[number] = (event, values)
        try:
            try:
                _write(self._socket, self._send_lock, {"rpc": number, **message})
            except OSError as error:
                self.stopped = "accounting connection unavailable"
                raise SampleStopped(self._stopped) from error
            if not event.wait(self._rpc_timeout) or not values:
                self.stopped = "accounting response unavailable"
                raise SampleStopped(self._stopped)
            return values[0]
        finally:
            with self._rpc_lock:
                self._waiters.pop(number, None)

    def begin(self, role, model, effort):
        with self.lock:
            if self.stopped:
                raise SampleStopped(self.stopped)
            self._event_number += 1
            event_id = f"{self.namespace}:{self._event_number}"
        base = {"event_id": event_id, "request_id": self.request_id}
        decision = self._rpc(op="event", event={**base, "provider_event": "begin", "role": role,
            "model": model, "reasoning_effort": effort})["decision"]
        if not decision.get("allow"):
            self._rpc(op="event", event={**base, "provider_event": "settle", "attempt_id": None,
                "status": "aborted", "usage": None})
            self.stopped = "parent refused provider attempt"
            raise SampleStopped(self.stopped)
        attempt = decision["attempt_id"]
        with self.lock:
            self._events[attempt] = base
            self.attempts[attempt] = {"status": "pending", "usage": None}
            stopped_before_permit = self.stop_requested or bool(self.stopped)
        if stopped_before_permit:
            # A reservation is not permission to dispatch after an observed stop.
            # Preserve the parent's granted ID and settle it without HTTP usage.
            self.incomplete(attempt, "aborted")
            raise SampleStopped(self.stopped or "stopped before permit delivery")
        return attempt

    def reserve_action(self, name, binding, config_sha256):
        if self.stopped or self.stop_requested:
            raise SampleStopped("sample stopped before action reservation")
        decision = self._rpc(op="action_reserve", name=name, binding=binding,
                             config_sha256=config_sha256)["decision"]
        if not decision.get("allow"):
            self.stopped = "parent refused action"
            raise SampleStopped(self.stopped)
        index = decision["action_id"]
        with self.lock:
            self.actions[index] = {"status": "pending", "entered": None}
        if self.stopped or self.stop_requested:
            self.settle_action(index, "incomplete", entered=False)
            raise SampleStopped("sample stopped before action permit delivery")
        return index

    def settle_action(self, index, status, *, entered):
        response = self._rpc(op="action_settle", action_id=index, status=status, entered=entered)
        if response.get("decision", {}).get("settled") is not True:
            raise SampleStopped("action settlement unavailable")
        with self.lock:
            self.actions[index].update(status=status, entered=entered)

    def track_async_attempt(self, attempt):
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        with self.lock:
            if self.attempts[attempt]["status"] != "pending" or task is None:
                raise ValueError("pending native task required")
            self.async_attempts[attempt] = (loop, task)
        if self.stop_requested:
            self._cancel_local()

    def finish(self, attempt, usage):
        decision = self._rpc(op="event", event={**self._events[attempt], "provider_event": "settle",
            "attempt_id": attempt, "status": "completed", "usage": usage})["decision"]
        with self.lock:
            self.async_attempts.pop(attempt, None)
            self.attempts[attempt].update(status="completed" if decision.get("allow") else "unattested", usage=usage)
        if not decision.get("allow"):
            self.stopped = "parent rejected provider settlement"
            raise SampleStopped(self.stopped)

    def incomplete(self, attempt, status):
        with self.lock:
            if self.attempts[attempt]["status"] != "pending":
                return
        self._rpc(op="event", event={**self._events[attempt], "provider_event": "settle",
            "attempt_id": attempt, "status": "aborted", "usage": None})
        with self.lock:
            self.attempts[attempt]["status"] = status
            self.async_attempts.pop(attempt, None)
        self.stopped = "native provider usage unknown"

    def stop_async_attempts(self, reason="comparison sample stopped"):
        self.stopped = reason
        self._cancel_local()

    def report(self):
        with self.lock:
            return {"stop": self.stopped, "stop_requested": self.stop_requested,
                "active_async_attempts": len(self.async_attempts), "cancel_errors": list(self.cancel_errors),
                "attempts": {str(k): dict(v) for k, v in self.attempts.items()},
                **({"actions": {str(k): dict(v) for k, v in self.actions.items()}} if self.actions else {}),
                "scope": "local mirror only; authoritative usage is in parent"}

    def close(self, *, settled=False):
        acknowledged = False
        try:
            self._rpc(op="close", settled=settled and not any(r["status"] == "pending" for r in self.actions.values()), active_tasks=len(self.async_attempts),
                pending_local=sum(r["status"] == "pending" for r in self.attempts.values()),
                cancel_errors=list(self.cancel_errors))
            acknowledged = True
        except (SampleStopped, OSError):
            self._stopped = self._stopped or "close receipt unavailable"
        finally:
            self._closed = True
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._reader.join(timeout=2)
            self._stream.close()
            self._socket.close()
        return acknowledged
