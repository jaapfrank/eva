"""Adapter -> actual Bottie HTTP/agent/SQLite with controlled model transport."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
BENCHMARK = ROOT / "eva2/benchmark/inspect_ai_v1"
SPEC = importlib.util.spec_from_file_location(
    "cp001_bottie_native", BENCHMARK / "cp001_native.py"
)
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


@pytest.fixture
def bottie(request):
    with tempfile.TemporaryDirectory(prefix="cp001-bottie-", dir="/tmp") as temp:
        data = Path(temp)
        diagnostics = tempfile.TemporaryFile(mode="w+t")

        # Fresh user/network namespace: only its loopback is enabled.
        def launch(resume=False):
            return subprocess.Popen(
                [
                    "unshare",
                    "--user",
                    "--map-root-user",
                    "--net",
                    "sh",
                    "-c",
                    'ip link set lo up && exec "$@"',
                    "cp001",
                    "node",
                    str(BENCHMARK / "cp001_bottie_worker.mjs"),
                    str(data),
                ]
                + (["--resume"] if resume else []),
                cwd=ROOT,
                env={"PATH": os.defpath, "HOME": str(data)},
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=diagnostics,
                text=True,
                bufsize=1,
            )

        process = launch()
        records = {
            "http": [],
            "readbacks": [],
            "network": "private namespace loopback only",
        }

        def receive(timeout=30):
            if not select.select([process.stdout], [], [], timeout)[0]:
                raise TimeoutError("local Bottie worker deadline")
            line = process.stdout.readline()
            if not line:
                diagnostics.seek(0)
                raise AssertionError(diagnostics.read()[-5000:])
            return json.loads(line)

        try:
            ready = receive()
            assert ready["ready"] and Path(ready["database"]).parent == data
            records["processes"] = [ready]
            key = ready["public_key"]
            database = Path(ready["database"])

            def crash_and_restart():
                nonlocal process
                old_pid = process.pid
                process.kill()  # SIGKILL: no finally, SQLite checkpoint or graceful close.
                process.wait(timeout=5)
                assert process.returncode == -9
                records["crash"] = {
                    "signal": "SIGKILL",
                    "returncode": process.returncode,
                    "pid": old_pid,
                    "boundary": "after response/readback before ACK",
                }
                process.stdin.close()
                process.stdout.close()
                process = launch(resume=True)
                restored = receive()
                assert restored["resumed"] is True and restored["pid"] != ready["pid"]
                assert restored["network_namespace"] != ready["network_namespace"]
                assert restored["public_key"] == key
                assert restored["key_file_sha256"] == ready["key_file_sha256"]
                records["processes"].append(restored)

            def turn(step=0, with_key=True):
                return native.request_for(
                    "eva1-bottie",
                    run_id="0123456789ab",
                    task="A",
                    repeat=1,
                    step=step,
                    text="Vertel een rustige zin.",
                    occurred_at="2026-09-12T00:00:00Z",
                    client_ack_public_key=key if with_key else None,
                )

            def raw_post(path, body, timeout_seconds=10, max_response_bytes=65536):
                process.stdin.write(
                    json.dumps(
                        {
                            "path": path,
                            "body": body,
                            "timeout_ms": int(timeout_seconds * 1000),
                            "max_bytes": max_response_bytes,
                        }
                    )
                    + "\n"
                )
                process.stdin.flush()
                value = receive(timeout_seconds + 1)
                records["http"].append({"path": path, "request": body, **value})
                return value

            def signed_ack(kind="valid"):
                process.stdin.write(json.dumps({"sign_ack": kind}) + "\n")
                process.stdin.flush()
                return receive()["ack"]

            def delivery_readback(body, request_ref):
                with sqlite3.connect(
                    database.as_uri() + "?mode=ro", uri=True
                ) as reader:
                    reader.row_factory = sqlite3.Row
                    receipts = [
                        dict(row)
                        for row in reader.execute(
                            "SELECT ack_receipt_ref, ack_receipt_sha256, request_ref, response_ref, "
                            "response_sha256, wire_sha256, signature, receipt_json "
                            "FROM eva_http_delivery_ack_receipts_v3 WHERE request_ref=?",
                            (request_ref,),
                        )
                    ]
                    counts = {
                        "assistant_messages": reader.execute(
                            "SELECT count(*) FROM messages WHERE channel='web' "
                            "AND conversation_id=? AND role='assistant'",
                            (body["conversation_id"],),
                        ).fetchone()[0],
                        "chat_out": reader.execute(
                            "SELECT count(*) FROM timeline WHERE kind='chat_out' "
                            "AND json_extract(meta,'$.conversationId')=?",
                            (body["conversation_id"],),
                        ).fetchone()[0],
                        "prepared": reader.execute(
                            "SELECT count(*) FROM eva_http_prepared_responses WHERE request_ref=?",
                            (request_ref,),
                        ).fetchone()[0],
                    }
                snapshot = {
                    "request_ref": request_ref,
                    "receipts": receipts,
                    "counts": counts,
                }
                records.setdefault("ack_readbacks", []).append(snapshot)
                return snapshot

            def post(path, body, *, timeout_seconds, max_response_bytes):
                value = raw_post(path, body, timeout_seconds, max_response_bytes)
                if value["status"] != 200:
                    raise ValueError(f"Bottie HTTP {value['status']}")
                return value["body"]

            def readback(body, *, timeout_seconds):
                # Separate Python SQLite connection, no Node journal helper/response map.
                with sqlite3.connect(
                    database.as_uri() + "?mode=ro", uri=True, timeout=timeout_seconds
                ) as reader:
                    rows = reader.execute(
                        "SELECT p.public_body_json, p.public_body_sha256 "
                        "FROM eva_http_prepared_responses p "
                        "JOIN eva_http_ingress_requests r ON r.request_ref=p.request_ref "
                        "WHERE r.channel='web' AND r.conversation_id=? AND r.request_id=?",
                        (body["conversation_id"], body["request_id"]),
                    ).fetchall()
                assert len(rows) == 1
                text, digest = rows[0]
                assert native.digest(text) == digest
                payload = json.loads(text)
                records["readbacks"].append(
                    {
                        "conversation": body["conversation_id"],
                        "request": body["request_id"],
                        "payload": payload,
                        "sha256": digest,
                    }
                )
                return payload

            yield SimpleNamespace(
                turn=turn,
                post=post,
                raw_post=raw_post,
                signed_ack=signed_ack,
                crash_and_restart=crash_and_restart,
                delivery_readback=delivery_readback,
                readback=readback,
                records=records,
                database=database,
            )
        finally:
            if process.poll() is None:
                process.stdin.write('{"stop":true}\n')
                process.stdin.flush()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            diagnostics.close()
            assert process.returncode == 0
            if records["readbacks"]:
                with sqlite3.connect(
                    (data / "eva.db").as_uri() + "?mode=ro", uri=True
                ) as reader:
                    records["persisted_after_process_exit"] = reader.execute(
                        "SELECT count(*) FROM eva_http_prepared_responses"
                    ).fetchone()[0]
                assert records["persisted_after_process_exit"] >= 1
            evidence = os.environ.get("CP001_BOTTIE_EVIDENCE_DIR")
            if evidence:
                out = Path(evidence) / request.node.name
                out.mkdir(mode=0o700)
                (out / "readbacks.json").write_text(
                    json.dumps(records, indent=2) + "\n"
                )
                shutil.copyfile(data / "eva.db", out / "eva.db")


def exchange(bottie, step=0, reader=None):
    path, body = bottie.turn(step)
    return native.exchange(
        "eva1-bottie",
        path,
        body,
        post=bottie.post,
        readback=reader or bottie.readback,
        timeout_seconds=10,
    )


def test_bottie_actual_reply_persists_and_replays_without_second_model(bottie):
    first = exchange(bottie)
    assert first["status"] == "readback_verified"
    assert first["usage"] is None and first["readback"]["total_usage"] is None
    calls = bottie.records["http"][-1]["provider_stub_calls"]
    assert calls > 0
    replay = exchange(bottie)
    assert (
        replay["status"] == "readback_verified" and replay["answer"] == first["answer"]
    )
    assert bottie.records["http"][-1]["provider_stub_calls"] == calls
    assert bottie.records["http"][-1]["replayed"] == "true"
    assert first["quality"] is None and first["product_result"] == "unknown"


def test_bottie_requires_real_ack_key_before_provider_and_rejects_conflicting_intent(
    bottie,
):
    path, bare = bottie.turn(with_key=False)
    missing = bottie.raw_post(path, bare)
    assert (
        missing["status"] == 400
        and missing["body"]["reason"] == "client_ack_key_required"
    )
    assert missing["provider_stub_calls"] == 0
    assert exchange(bottie)["status"] == "readback_verified"
    calls = bottie.records["http"][-1]["provider_stub_calls"]
    _, body = bottie.turn()
    conflict = bottie.raw_post(path, {**body, "text": "Een andere vraag."})
    assert (
        conflict["status"] == 409
        and conflict["body"]["reason"] == "request_intent_conflict"
    )
    assert conflict["provider_stub_calls"] == calls


def test_bottie_wrong_real_persisted_session_reply_is_rejected(bottie):
    first, other = exchange(bottie), exchange(bottie, 2)
    assert first["status"] == other["status"] == "readback_verified"
    assert first["answer"] == other["answer"]
    _, wrong_body = bottie.turn(2)

    def wrong_reader(body, *, timeout_seconds):
        return bottie.readback(wrong_body, timeout_seconds=timeout_seconds)

    rejected = exchange(bottie, reader=wrong_reader)
    assert rejected["status"] == "readback_failed"
    assert rejected["verified_end_ms"] is None and rejected["usage"] is None


def test_bottie_actual_p256_ack_deduplicates_and_rejects_wrong_key_or_hash(bottie):
    stored = exchange(bottie)
    assert stored["status"] == "readback_verified"
    _, body = bottie.turn()
    initial = bottie.records["http"][-1]
    ref = initial["body"]["_httpDelivery"]["requestRef"]
    calls = initial["provider_stub_calls"]
    before = bottie.delivery_readback(body, ref)
    assert before["receipts"] == []
    assert before["counts"] == {"assistant_messages": 0, "chat_out": 0, "prepared": 1}
    for kind in ("wrong-key", "changed-hash"):
        denied = bottie.raw_post("/api/http-delivery/ack", bottie.signed_ack(kind))
        assert denied["status"] == 400
        assert denied["body"]["delivered"] is False
        assert denied["body"]["acknowledged"] is False
        assert denied["provider_stub_calls"] == calls
        assert bottie.delivery_readback(body, ref) == before
    ack = bottie.signed_ack()
    delivered = bottie.raw_post("/api/http-delivery/ack", ack)
    assert delivered["status"] == 200 and delivered["body"]["delivered"] is True
    after = bottie.delivery_readback(body, ref)
    assert len(after["receipts"]) == 1
    receipt = after["receipts"][0]
    assert receipt["ack_receipt_ref"] == delivered["body"]["ack_receipt_ref"]
    assert native.digest(receipt["receipt_json"]) == receipt["ack_receipt_sha256"]
    assert json.loads(receipt["receipt_json"]) == delivered["body"]["ack_receipt"]
    assert receipt["response_sha256"] == ack["responseSha256"]
    assert receipt["wire_sha256"] == ack["wireSha256"]
    assert receipt["signature"] == ack["signature"]
    assert after["counts"] == {"assistant_messages": 1, "chat_out": 1, "prepared": 1}
    duplicate = bottie.raw_post("/api/http-delivery/ack", ack)
    assert duplicate["status"] == 200 and duplicate["body"] == delivered["body"]
    assert bottie.delivery_readback(body, ref) == after
    assert duplicate["provider_stub_calls"] == calls
    assert len([h for h in bottie.records["http"] if h["path"] == "/api/chat"]) == 1


def test_bottie_process_crash_before_ack_replays_and_delivers_once(bottie):
    first = exchange(bottie)
    assert first["status"] == "readback_verified"
    original = bottie.records["http"][-1]
    _, body = bottie.turn()
    ref = original["body"]["_httpDelivery"]["requestRef"]
    before = bottie.delivery_readback(body, ref)
    assert before["receipts"] == []
    assert before["counts"] == {"assistant_messages": 0, "chat_out": 0, "prepared": 1}
    assert original["provider_stub_calls"] == 2
    bottie.crash_and_restart()
    replay = exchange(bottie)
    assert (
        replay["status"] == "readback_verified" and replay["answer"] == first["answer"]
    )
    replay_http = bottie.records["http"][-1]
    assert replay_http["body"] == original["body"]
    assert replay_http["replayed"] == "true" and replay_http["provider_stub_calls"] == 0
    assert bottie.delivery_readback(body, ref) == before
    for kind in ("wrong-key", "changed-hash"):
        denied = bottie.raw_post("/api/http-delivery/ack", bottie.signed_ack(kind))
        assert denied["status"] == 400 and denied["body"]["delivered"] is False
        assert denied["provider_stub_calls"] == 0
        assert bottie.delivery_readback(body, ref) == before
    ack = bottie.signed_ack()
    delivered = bottie.raw_post("/api/http-delivery/ack", ack)
    assert delivered["status"] == 200 and delivered["body"]["delivered"] is True
    after = bottie.delivery_readback(body, ref)
    assert len(after["receipts"]) == 1
    receipt = after["receipts"][0]
    assert receipt["ack_receipt_ref"] == delivered["body"]["ack_receipt_ref"]
    assert native.digest(receipt["receipt_json"]) == receipt["ack_receipt_sha256"]
    assert json.loads(receipt["receipt_json"]) == delivered["body"]["ack_receipt"]
    assert after["counts"] == {"assistant_messages": 1, "chat_out": 1, "prepared": 1}
    duplicate = bottie.raw_post("/api/http-delivery/ack", ack)
    assert duplicate["status"] == 200 and duplicate["body"] == delivered["body"]
    assert bottie.delivery_readback(body, ref) == after
    assert all(h["provider_stub_calls"] == 0 for h in bottie.records["http"][1:])


@pytest.mark.parametrize("reserved_before_stop", [False, True])
def test_controlled_native_provider_binding_usage_and_stop_before_retry(monkeypatch, reserved_before_stop):
    """Actual agent/fetch boundary; parent ledger protocol, no provider network."""
    monkeypatch.syspath_prepend(str(BENCHMARK))
    from cp001_accounting import ProviderSampleLedger
    with tempfile.TemporaryDirectory(prefix="cp001-bottie-", dir="/tmp") as temp:
        with tempfile.TemporaryFile(mode="w+t") as diagnostics:
            process = subprocess.Popen([
                "unshare", "--user", "--map-root-user", "--net", "sh", "-c",
                'ip link set lo up && exec "$@"', "cp001", "node",
                str(BENCHMARK / "cp001_bottie_worker.mjs"), temp, "--sample-control",
            ], cwd=ROOT, env={"PATH": os.defpath, "HOME": temp}, stdin=subprocess.PIPE,
               stdout=subprocess.PIPE, stderr=diagnostics, text=True, bufsize=1)
            def receive():
                assert select.select([process.stdout], [], [], 30)[0], "worker deadline"
                line = process.stdout.readline()
                if not line:
                    diagnostics.seek(0)
                    pytest.fail(diagnostics.read()[-4000:])
                return json.loads(line)
            def send(value):
                process.stdin.write(json.dumps(value) + "\n")
                process.stdin.flush()
            try:
                ready = receive()
                path, body = native.request_for("eva1-bottie", run_id="0123456789ab",
                    task="A", repeat=1, step=0, text="Vertel een rustige zin.",
                    occurred_at="2026-09-12T00:00:00Z", client_ack_public_key=ready["public_key"])
                send({"path": path, "body": body, "timeout_ms": 20000, "max_bytes": 65536})
                first = receive()
                assert first["provider_event"] == "begin"
                assert first["request_id"] == body["request_id"] and first["role"] == "contact"
                assert first["model"] and "reasoning_effort" in first
                ledger = ProviderSampleLedger("controlled-native", {
                    "providerAttempts": 2, "providerInputTokens": 100, "providerOutputTokens": 100},
                    {"contact": (first["model"], first["reasoning_effort"])})
                control = native.NativeProviderControl(ledger, body["request_id"])
                admission = control.handle(first)
                assert admission["allow"] and admission["attempt_id"] == 0
                if reserved_before_stop:
                    # Reserve the first permit but never send it to the worker.
                    # This tests the race without assuming equal native profiles
                    # in the later interpretation and response phases.
                    held = first
                    stop = control.stop()
                else:
                    send(admission)
                    settled = receive()
                    assert settled["provider_event"] == "settle"
                    assert settled["event_id"] == first["event_id"] and settled["attempt_id"] == 0
                    assert settled["usage"] == {"input_tokens": 17, "output_tokens": 5, "total_tokens": 22}
                    continuation = control.handle(settled)
                    assert continuation["allow"]
                    send(continuation)
                    held = receive()
                    assert held["provider_event"] == "begin" and held["event_id"] != first["event_id"]
                    assert held["request_id"] == body["request_id"]
                    stop = control.stop()
                    assert not control.handle(held)["allow"]
                send(stop)
                aborted = receive()
                assert aborted["provider_event"] == "settle" and aborted["event_id"] == held["event_id"]
                assert aborted["attempt_id"] is None and aborted["usage"] is None
                assert aborted["status"] == "aborted"
                assert control.handle(aborted) == {"provider_control": held["event_id"], "allow": False}
                assert len(ledger.attempts) == 1
                if reserved_before_stop:
                    assert ledger.attempts[0]["status"] == "aborted"
                    assert ledger.attempts[0]["usage"] is None
                    assert not ledger.report()["complete"]
                else:
                    assert ledger.attempts[0]["status"] == "completed"
                stopped = receive()
                assert stopped["stopped"] and stopped["provider_stub_calls"] == (0 if reserved_before_stop else 1)
                assert stopped["active_provider_attempts"] == stopped["pending_provider_controls"] == 0
                process.wait(timeout=5)
                assert process.returncode == 0
                assert not process.stdout.read().strip(), "no retries after stop"
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


def test_explicit_actual_bottie_source_completes_two_equal_model_calls(monkeypatch):
    """Explicit disposable native variant, synthetic provider bytes, real SQLite."""
    supplied = os.environ.get("CP001_BOTTIE_SOURCE_ROOT")
    if not supplied:
        pytest.skip("explicit disposable actual-Bottie source fixture required")
    source = Path(supplied)
    assert source.is_absolute() and source.is_dir()
    source = source.resolve()
    monkeypatch.syspath_prepend(str(BENCHMARK))
    from cp001_accounting import ProviderSampleLedger

    with tempfile.TemporaryDirectory(prefix="cp001-bottie-", dir="/tmp") as temp:
        with tempfile.TemporaryFile(mode="w+t") as diagnostics:
            process = subprocess.Popen([
                "unshare", "--user", "--map-root-user", "--net", "sh", "-c",
                'ip link set lo up && exec "$@"', "cp001", "node",
                str(BENCHMARK / "cp001_bottie_worker.mjs"), temp,
                "--sample-control", "--source-root", str(source),
            ], cwd=ROOT, env={"PATH": os.defpath, "HOME": temp,
                "DOTENV_CONFIG_PATH": "/dev/null", "EVA_MODEL": "gpt-6-sol",
                "EVA_MODEL_VOICE": "gpt-6-sol", "EVA_REASONING_CHAT": "none",
                "EVA_REASONING_REPLY": "none"}, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=diagnostics, text=True, bufsize=1)

            def receive():
                assert select.select([process.stdout], [], [], 30)[0], "worker deadline"
                line = process.stdout.readline()
                if not line:
                    diagnostics.seek(0)
                    pytest.fail(diagnostics.read()[-4000:])
                return json.loads(line)

            def send(value):
                process.stdin.write(json.dumps(value) + "\n")
                process.stdin.flush()

            try:
                ready = receive()
                assert ready["explicit_source_root"] is True
                assert ready["source_root"] == str(source)
                assert Path(ready["database"]).parent == Path(temp)
                for relative, digest in ready["source_sha256"].items():
                    assert hashlib.sha256((source / relative).read_bytes()).hexdigest() == digest
                path, body = native.request_for("eva1-bottie", run_id="0123456789ab",
                    task="A", repeat=1, step=0, text="Vertel een rustige zin.",
                    occurred_at="2026-09-12T00:00:00Z", client_ack_public_key=ready["public_key"])
                ledger = ProviderSampleLedger("actual-bottie-equal-model-fixture", {
                    "providerAttempts": 2, "providerInputTokens": 24000,
                    "providerOutputTokens": 4096}, {"contact": ("gpt-6-sol", "none")})
                control = native.NativeProviderControl(ledger, body["request_id"])
                send({"path": path, "body": body, "timeout_ms": 20000, "max_bytes": 65536})
                events = []
                for attempt in range(2):
                    begin = receive()
                    events.append(begin)
                    assert begin["provider_event"] == "begin"
                    assert (begin["model"], begin["reasoning_effort"]) == ("gpt-6-sol", "none")
                    permit = control.handle(begin)
                    assert permit["allow"] and permit["attempt_id"] == attempt
                    send(permit)
                    settled = receive()
                    events.append(settled)
                    assert settled["status"] == "completed"
                    assert settled["usage"] == {"input_tokens": 17, "output_tokens": 5, "total_tokens": 22}
                    continuation = control.handle(settled)
                    assert continuation["allow"]
                    send(continuation)
                response = receive()
                assert response["status"] == 200 and response["provider_stub_calls"] == 2
                assert response["real_model_calls"] == 0
                with sqlite3.connect(Path(ready["database"]).as_uri() + "?mode=ro", uri=True) as reader:
                    rows = reader.execute(
                        "SELECT p.public_body_json, p.public_body_sha256 "
                        "FROM eva_http_prepared_responses p "
                        "JOIN eva_http_ingress_requests r ON r.request_ref=p.request_ref "
                        "WHERE r.channel='web' AND r.conversation_id=? AND r.request_id=?",
                        (body["conversation_id"], body["request_id"])).fetchall()
                assert len(rows) == 1
                stored, digest = rows[0]
                assert native.digest(stored) == digest
                answer = native.response_text("eva1-bottie", body, response["body"])
                readback = native.independent_readback("eva1-bottie", body, answer, json.loads(stored))
                assert readback["integrity"] is True
                assert answer == "Ik geef een lokaal controleantwoord."
                assert len(ledger.attempts) == 2
                assert sum(event["usage"]["input_tokens"] for event in events if event["provider_event"] == "settle") == 34
                assert sum(event["usage"]["output_tokens"] for event in events if event["provider_event"] == "settle") == 10
                send(control.stop())
                stopped = receive()
                assert stopped["stopped"] and stopped["provider_stub_calls"] == 2
                assert stopped["active_provider_attempts"] == stopped["pending_provider_controls"] == 0
                process.wait(timeout=5)
                assert process.returncode == 0 and not process.stdout.read().strip()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


def test_subscription_fetch_observer_preserves_native_stream_and_raw_usage():
    """Exercise the worker's actual fetch wrapper without credentials or network."""
    script = r"""
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const wrapper = source.slice(source.indexOf('globalThis.fetch = async'), source.indexOf("const { db } ="));
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
(async () => {
 const results = [];
 for (const kind of ['complete', 'missing', 'duplicate', 'cancelled', 'error', 'malformed', 'noncompleted', 'terminal-error', 'close-failure', 'nonok', 'nonok-close-failure']) {
  const events = [], pieces = [Buffer.from('data: {"type":"response.output_text.delta","delta":"hello"}\n\n')];
  const completed = Buffer.from('data: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":17,"output_tokens":7}}}\n\n');
  if (kind !== 'missing') pieces.push(completed);
  if (kind === 'duplicate') pieces.push(completed);
  if (['cancelled','error'].includes(kind)) pieces.unshift(Buffer.from('data: '+JSON.stringify({type:kind==='error'?'error':'response.cancelled'})+'\n\n'));
  if (['malformed','close-failure'].includes(kind)) pieces.unshift(Buffer.from('data: {broken}\n\n'));
  if (['noncompleted','terminal-error'].includes(kind)) pieces[pieces.length-1]=Buffer.from('data: '+JSON.stringify({type:'response.completed',response:{status:kind==='noncompleted'?'incomplete':'completed',error:kind==='terminal-error'?{code:'failed'}:null,usage:{input_tokens:17,output_tokens:7}}})+'\n\n');
  let reads = 0, dispatches = 0, closes = 0;
  const nativeFetch = async (_url, init) => {
   if (!init.signal) throw new Error('missing bound abort signal');
   dispatches++;
   return new Response(new ReadableStream({pull(c) { reads++; const part = pieces.shift(); if(part)c.enqueue(part);else c.close(); }, cancel() { closes++; if(kind.includes('close-failure'))throw Error('synthetic close failure'); }}), {status:kind.startsWith('nonok')?403:200,headers:{'content-type':'text/event-stream'}});
  };
  const prelude = `let stopping=false, currentRole='contact', currentRequestId='sample', eventSequence=0, realProviderCalls=0;
    const sampleControl=true, subscription=true, providerControllers=new Set(), pendingControls=new Map();
    const roleContext={getStore:()=> 'contact'};
    const configuredRole=(m,e)=>{if(m!=='gpt-6-sol'||e!=='none')throw Error('role');return 'contact'};
    const stopSample=()=>{stopping=true;for(const c of providerControllers)c.abort()};
    const fixtureFetch=()=>{throw Error('fixture path used')};
    const decision=async(event)=>{events.push(event);return {allow:true,attempt_id:1}};`;
  const run = new AsyncFunction('nativeFetch','events', prelude+wrapper+`
    let text='', error=null;
    try {
      const response=await globalThis.fetch('https://chatgpt.com/backend-api/codex/responses', {body:JSON.stringify({model:'gpt-6-sol',reasoning:{effort:'none'}})});
      text=await response.text();
    } catch(e){error=e.message}
    return {text,error,active:providerControllers.size,closeFailures:[...providerControllers].filter(c=>c.closeFailed).length};`);
  const value = await run(nativeFetch,events);
  results.push({kind,...value,events,dispatches,closes});
 }
 process.stdout.write(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run(
        ["node", "-e", script, str(BENCHMARK / "cp001_bottie_worker.mjs")],
        capture_output=True, text=True, check=True, timeout=15,
    )
    cases = json.loads(result.stdout)
    complete, *failures = cases
    assert complete["error"] is None
    assert '"delta":"hello"' in complete["text"]
    assert complete["events"][-1]["usage"] == {"input_tokens": 17, "output_tokens": 7}
    assert complete["events"][-1]["status"] == "completed"
    for failed in failures:
        assert failed["error"]
        assert failed["events"][-1]["usage"] is None
        assert failed["events"][-1]["status"] == "error"
    for case in cases:
        close_failed = "close-failure" in case["kind"]
        assert case["active"] == int(close_failed)
        assert case["closeFailures"] == int(close_failed)
        assert case["dispatches"] == 1
        if case["kind"].startswith("nonok"):
            assert case["closes"] == 1
