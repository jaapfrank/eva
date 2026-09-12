#!/usr/bin/env python3
"""Owner-authorized project updates through Eva's existing private Telegram route.

Run on the Linux host; no credentials or recipient identifiers cross Docker stdout.
SQLite commits a claim before dispatch. Uncertainty is deliberately not retried.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

# Reuse live runtime wiring: current database authority + trusted private binding,
# mounted bot secret, and the adapter's exact chat/type/text receipt validation.
CONTAINER_SENDER = r"""
import hashlib, json, sys
from eva.bootstrap.runtime import RuntimeSettings, _build_proactive_delivery
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.application.free_will import ProactiveExpressionClaim
import sqlalchemy as sa
stage = "runtime_setup"
observation = {}
try:
    payload = json.load(sys.stdin)
    settings = RuntimeSettings.from_environment()
    engine = sa.create_engine(sqlalchemy_postgres_dsn(settings.database_dsn), pool_pre_ping=True)
    try:
        delivery = _build_proactive_delivery(settings, engine)
        if delivery is None:
            raise RuntimeError("delivery unavailable")
        original_call = delivery._api._call
        def observed_call(method, body, **kwargs):
            global stage, observation
            stage = "provider_request_started"
            result = original_call(method, body, **kwargs)
            stage = "provider_reply_received"
            if isinstance(result, dict):
                chat = result.get("chat")
                private_match = (isinstance(chat, dict) and type(chat.get("id")) is int
                                 and str(chat["id"]) == body.get("chat_id")
                                 and chat.get("type") == "private")
                message_id = result.get("message_id")
                provider_text = result.get("text")
                observation = {"intended_private_chat": private_match,
                               "positive_message_id": type(message_id) is int and message_id > 0,
                               "exact_text": provider_text == body.get("text")}
                if isinstance(provider_text, str):
                    observation["text_sha256"] = hashlib.sha256(provider_text.encode()).hexdigest()
                if private_match and observation["positive_message_id"]:
                    raw_receipt = "telegram:%s:%s" % (chat["id"], message_id)
                    observation["provider_receipt_sha256"] = hashlib.sha256(raw_receipt.encode()).hexdigest()
            return result
        delivery._api._call = observed_call
        stage = "owner_binding_ready"
        claim = ProactiveExpressionClaim(payload["key"], payload["key"], 1, 0,
                                         "owner-authorized-project-update", payload["text"], 1)
        receipt = delivery.deliver(claim)
        print(json.dumps({"provider_receipt_sha256": hashlib.sha256(receipt.encode()).hexdigest(),
                          "private_chat_and_exact_text_verified": True}))
    finally:
        engine.dispose()
except Exception:
    # Exceptions can contain the bot URL, database DSN or personal identifiers.
    print(json.dumps({"error": "delivery_failed_or_uncertain", "stage": stage,
                      "provider_observation": observation}))
    sys.exit(1)
"""


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class DeliveryUncertain(RuntimeError):
    def __init__(self, diagnostics):
        super().__init__("runtime delivery failed or uncertain")
        self.diagnostics = diagnostics


def safe_diagnostics(value):
    # Allowlist only fixed stages, booleans and hashes; never copy provider text/errors.
    result = {}
    if not isinstance(value, dict):
        return result
    if value.get("stage") in {"runtime_setup", "owner_binding_ready", "provider_request_started", "provider_reply_received"}:
        result["stage"] = value["stage"]
    observed = value.get("provider_observation")
    if isinstance(observed, dict):
        clean = {key: observed[key] for key in ("intended_private_chat", "positive_message_id", "exact_text")
                 if type(observed.get(key)) is bool}
        for key in ("text_sha256", "provider_receipt_sha256"):
            digest = observed.get(key)
            if isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
                clean[key] = digest
        result["provider_observation"] = clean
    return result


def send_via_runtime(container, key, text):
    result = subprocess.run(
        ["sudo", "-n", "docker", "exec", "-i", container, "python", "-c", CONTAINER_SENDER],
        input=json.dumps({"key": key, "text": text}), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False,
    )
    if result.returncode:
        try:
            diagnostics = safe_diagnostics(json.loads(result.stdout))
        except (ValueError, TypeError):
            diagnostics = {}
        raise DeliveryUncertain(diagnostics)
    receipt = json.loads(result.stdout)
    digest = receipt.get("provider_receipt_sha256", "")
    if (receipt.get("private_chat_and_exact_text_verified") is not True
            or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
        raise RuntimeError("runtime receipt invalid")
    return receipt


def deliver_once(state_dir, key, text, sender):
    # Hour keys use UTC, so DST cannot create duplicate local hours.
    parsed = datetime.strptime(key, "%Y-%m-%dT%H:00Z")
    if parsed.strftime("%Y-%m-%dT%H:00Z") != key:
        raise ValueError("invalid hourly key")
    wire_text = text.strip(" \t\r\n")
    if not wire_text.strip() or len(wire_text.encode("utf-16-le")) // 2 > 4096:
        raise ValueError("message must contain 1 to 4096 Telegram UTF-16 units")
    state_dir = Path(state_dir)
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state_dir.is_symlink() or state_dir.stat().st_uid != os.getuid():
        raise ValueError("state directory must be owned by the invoking user")
    os.chmod(state_dir, 0o700)
    db = state_dir / "delivery.sqlite3"
    if db.is_symlink():
        raise ValueError("state database may not be a symlink")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with sqlite3.connect(db, timeout=30) as connection:
        os.chmod(db, 0o600)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("CREATE TABLE IF NOT EXISTS deliveries (hour_key TEXT PRIMARY KEY, message_sha256 TEXT NOT NULL, record TEXT NOT NULL)")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT message_sha256, record FROM deliveries WHERE hour_key=?", (key,)).fetchone()
        if row:
            if row[0] != digest:
                raise ValueError("hour key already claimed with a different message; no send")
            return json.loads(row[1])
        record = {"contract": "EvaProjectTelegramDelivery/v1", "hour_key": key,
                  "message_sha256": digest,
                  "sent_message_sha256": hashlib.sha256(wire_text.encode("utf-8")).hexdigest(),
                  "edge_whitespace_normalized": wire_text != text,
                  "status": "claimed_uncertain",
                  "claimed_at": utcnow(), "automatic_retry_allowed": False}
        connection.execute("INSERT INTO deliveries VALUES (?, ?, ?)", (key, digest, json.dumps(record)))
        connection.commit()  # Durable before any provider call, also across process death.
        try:
            result = sender(key, wire_text)
            record.update(result)
            record.update(status="delivered", delivered_at=utcnow())
        except Exception as error:
            record.update(status="uncertain", finished_at=utcnow())
            if isinstance(error, DeliveryUncertain):
                record["diagnostics"] = safe_diagnostics(error.diagnostics)
        connection.execute("UPDATE deliveries SET record=? WHERE hour_key=?", (json.dumps(record), key))
        connection.commit()
        return record


def write_receipt(path, record):
    path = Path(path)
    # Atomic publication; the durable DB remains authoritative if this fails.
    fd, temporary = tempfile.mkstemp(prefix=".telegram-receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message-file", type=Path, required=True)
    parser.add_argument("--hour-key", required=True, help="UTC YYYY-MM-DDTHH:00Z; reuse unchanged on retry")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--container", default="eva2-managed-b-slot3-core")
    args = parser.parse_args()
    try:
        text = args.message_file.read_text(encoding="utf-8")
        record = deliver_once(Path.home() / ".local/state/eva2/project-telegram-updates",
                              args.hour_key, text,
                              lambda key, message: send_via_runtime(args.container, key, message))
        write_receipt(args.receipt, record)
        print(json.dumps({"status": record["status"], "receipt": str(args.receipt)}))
        return 0 if record["status"] == "delivered" else 2
    except Exception:
        print("Project update not confirmed; inspect the local register. Do not use a new key to retry.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
