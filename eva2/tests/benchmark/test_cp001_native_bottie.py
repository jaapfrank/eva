"""Adapter -> actual Bottie HTTP/agent/SQLite with controlled model transport."""

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
        process = subprocess.Popen(
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
            ],
            cwd=ROOT,
            env={"PATH": os.defpath, "HOME": str(data)},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=diagnostics,
            text=True,
            bufsize=1,
        )
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
            key = ready["public_key"]
            database = Path(ready["database"])

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
