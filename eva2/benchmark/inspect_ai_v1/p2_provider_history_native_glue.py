"""One P2 contact/memory pair: glue over the existing native controls only.

No task/oracle is defined here. Bind this adapter before independent curation.
All text, native replies and raw events stay in the evaluator-owned output.
"""
from __future__ import annotations
import json, math, os, queue, sqlite3, subprocess, threading, time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from cp001_accounting import ProviderSampleLedger
from cp001_native import NativeHTTPResponse, NativeIngressError, NativeProviderControl, exchange, read_eva2_committed_reply, read_eva2_memory_settlement
from cp001_process_accounting import ParentProcessAccounting

def ledger_for(sample_id, plan, *, aggregate=None):
    family = next(x for x in plan["families"] if x["family"] == "conversation_memory")
    return ProviderSampleLedger(sample_id, family["perSampleLimits"],
        {"contact": ("gpt-6-sol", "none"), "helper": ("gpt-6-astra", "medium")},
        aggregate=aggregate, family=family["family"])

def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write("\n")

class BottieIO:
    def __init__(self, command, environment, ledger, stderr_path):
        self.ledger = ledger
        self.events, self.messages = [], queue.Queue()
        self.stderr = open(stderr_path, "xb")
        self.child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, env=environment)
        def collect():
            for line in self.child.stdout:
                try: self.messages.put(json.loads(line))
                except Exception: self.messages.put({"invalid_native_output": True})
            self.messages.put({"native_eof": True})
        self.reader = threading.Thread(target=collect, daemon=True); self.reader.start()
        self.ready = self.receive(time.monotonic()+30)
        if self.ready.get("ready") is not True:
            raise ValueError("Native Bottie readiness not established")

    def send(self, value):
        self.child.stdin.write(json.dumps(value).encode()+b"\n"); self.child.stdin.flush()

    def receive(self, deadline):
        return self.messages.get(timeout=max(.001, deadline-time.monotonic()))

    def transact(self, value, timeout_seconds):
        deadline=time.monotonic()+timeout_seconds
        request_id=value.get("body",{}).get("request_id")
        control=NativeProviderControl(self.ledger, request_id) if request_id else None
        self.send(value)
        while True:
            event=self.receive(deadline); self.events.append(event)
            if event.get("provider_event"):
                if control is None: raise ValueError("Provider outside bound turn")
                self.send(control.handle(event)); continue
            if event.get("native_eof") or event.get("invalid_native_output"):
                raise ValueError("Native worker terminated without receipt")
            return event

    def post(self, path, body, *, timeout_seconds, max_response_bytes):
        response=self.transact({"path":path,"body":body,"timeout_ms":max(1,int(timeout_seconds*1000)),
            "max_bytes":max_response_bytes,"sample_role":"contact"},timeout_seconds)
        if response.get("status")!=200:
            raise NativeIngressError("http_rejected", response.get("status"))
        return NativeHTTPResponse(response["body"], 200)

    def readback(self, body, *, timeout_seconds):
        connection=sqlite3.connect(Path(self.ready["database"]).resolve().as_uri()+"?mode=ro",uri=True,
            timeout=timeout_seconds)
        try:
            connection.execute("PRAGMA query_only=ON")
            rows=connection.execute("SELECT s.public_body_json FROM eva_http_prepared_responses s "
                "JOIN eva_http_ingress_requests r ON r.id=s.request_row_id AND r.request_ref=s.request_ref "
                "WHERE r.channel=? AND r.conversation_id=? AND r.request_id=?",
                ("web",body["conversation_id"],body["request_id"])).fetchall()
            if len(rows)>1: raise ValueError("Multiple native replies for one exact request")
            return None if not rows else json.loads(rows[0][0])
        finally: connection.close()

    def acknowledge(self, timeout_seconds):
        signed=self.transact({"sign_ack":"valid"},timeout_seconds)
        return self.transact({"path":"/api/http-delivery/ack","body":signed["ack"],
            "timeout_ms":max(1,int(timeout_seconds*1000)),"max_bytes":65536},timeout_seconds)

    def stop(self):
        self.child.stdin.close()
        try: self.child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.child.terminate(); self.child.wait(timeout=10)
        self.reader.join(timeout=2); self.stderr.close()
        remaining=[]
        while not self.messages.empty(): remaining.append(self.messages.get_nowait())
        return {"returncode":self.child.returncode,"terminal_events":remaining,
            "reader_stopped":not self.reader.is_alive(),"ledger":self.ledger.report()}

def core_exchange(base_url, body, core_engine, *, timeout_seconds):
    def post(path, value, *, timeout_seconds, max_response_bytes):
        request=Request(base_url+path,data=json.dumps(value).encode(),
            headers={"Content-Type":"application/json"},method="POST")
        try:
            with urlopen(request,timeout=timeout_seconds) as response:
                status=response.status
                data=response.read(max_response_bytes+1)
        except HTTPError as error:
            # Do not read, retain or publish the server's error detail/body.
            status=error.code
            error.close()
            raise NativeIngressError("http_rejected", status) from None
        if len(data)>max_response_bytes:
            raise NativeIngressError("response_size_exceeded", status)
        try:
            return NativeHTTPResponse(json.loads(data), status)
        except (ValueError, UnicodeDecodeError):
            raise NativeIngressError("invalid_json", status) from None
    return exchange("eva2","/internal/v1/turns/respond",body,post=post,
        readback=lambda value,timeout_seconds:read_eva2_committed_reply(
            core_engine,value,timeout_seconds=timeout_seconds),
        timeout_seconds=timeout_seconds)

def memory_settlement(core_engine, memory_engine, body, timeout_seconds):
    """Await native retention without extending the remaining sample deadline.

    The HTTP reply precedes its background retention callback. A pending
    snapshot, including a queue not yet committed, is not a terminal result.
    The existing reader still owns all identity and completion checks.
    """
    if (type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("a positive remaining readback timeout is required")
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("native memory settlement deadline reached")
        result = read_eva2_memory_settlement(core_engine, memory_engine, body,
            timeout_seconds=remaining)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("native memory settlement deadline reached")
        if result["status"] != "pending":
            return result
        time.sleep(min(0.1, remaining))

def core_parent(ledger, sample_id, directory):
    directory=Path(directory)
    directory.mkdir(mode=0o700,exist_ok=False)
    parent=ParentProcessAccounting(ledger,sample_id,directory/"accounting.sock",
        workers={"core":("contact","helper"),"memory":("helper",)}).start()
    # Directory/socket access is granted only to the actual native image UID.
    os.chown(directory,65532,65532)
    os.chown(directory/"accounting.sock",65532,65532)
    for namespace in ("core","memory"):
        path=directory/(namespace+"-control.json")
        private_json(path,parent.credentials(namespace));os.chown(path,65532,65532)
    return parent
