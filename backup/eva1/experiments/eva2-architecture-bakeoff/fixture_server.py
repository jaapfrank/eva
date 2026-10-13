from __future__ import annotations

import argparse
import json
import socket
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from common import canonical_json, sha256


STATIC_MAIL = {
    "contract": "FixtureMail/v1",
    "mail_id": "mail:noor:017",
    "sender": "Noor BV",
    "sender_email": "planning@noorbv.example",
    "subject": "Afspraak woensdag",
    "body": "Kunnen we woensdagmiddag een half uur afspreken?",
}
STATIC_CUSTOMER = {
    "contract": "FixtureCustomer/v1",
    "customer_id": "erp:customer:204",
    "name": "Noor BV",
    "email": "planning@noorbv.example",
}
STATIC_FREEBUSY = {
    "contract": "FixtureFreeBusy/v1",
    "day": "2026-08-26",
    "available": [
        {"start": "2026-08-26T15:00:00+02:00", "end": "2026-08-26T15:30:00+02:00"}
    ],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FixtureStore:
    def __init__(self, path: Path):
        self.path = path
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS effects (
                  idempotency_key TEXT PRIMARY KEY,
                  operation TEXT NOT NULL,
                  request_json TEXT NOT NULL,
                  request_sha256 TEXT NOT NULL,
                  response_json TEXT NOT NULL,
                  committed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calls (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT,
                  method TEXT NOT NULL,
                  path TEXT NOT NULL,
                  idempotency_key TEXT,
                  request_sha256 TEXT,
                  status INTEGER NOT NULL,
                  happened_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS faults (
                  operation TEXT PRIMARY KEY,
                  mode TEXT NOT NULL,
                  remaining INTEGER NOT NULL CHECK (remaining >= 0)
                );
                CREATE TABLE IF NOT EXISTS model_calls (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT,
                  episode_id TEXT NOT NULL,
                  request_sha256 TEXT NOT NULL,
                  response_sha256 TEXT NOT NULL,
                  happened_at TEXT NOT NULL
                );
                """
            )

    def log_call(
        self, method: str, path: str, status: int, *,
        idempotency_key: str | None = None, request_sha256: str | None = None,
    ) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                "INSERT INTO calls(method,path,idempotency_key,request_sha256,status,happened_at) "
                "VALUES(?,?,?,?,?,?)",
                (method, path, idempotency_key, request_sha256, status, now_iso()),
            )

    def effect(self, key: str) -> dict | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM effects WHERE idempotency_key=?", (key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "contract": "FixtureEffectReadback/v1",
            "idempotency_key": row["idempotency_key"],
            "operation": row["operation"],
            "request": json.loads(row["request_json"]),
            "request_sha256": row["request_sha256"],
            "response": json.loads(row["response_json"]),
            "response_sha256": sha256(json.loads(row["response_json"])),
            "committed_at": row["committed_at"],
        }

    def create_effect(self, key: str, operation: str, payload: dict) -> tuple[dict, bool]:
        if set(payload) != {"operation", "data"} or payload.get("operation") != operation:
            raise ValueError("invalid_effect_envelope")
        if not isinstance(payload.get("data"), dict):
            raise ValueError("invalid_effect_data")
        request_json = canonical_json(payload)
        request_sha = sha256(payload)
        resource_prefix = {
            "calendar.create": "calendar:event",
            "document.create": "document",
            "delivery.send": "delivery:message",
        }.get(operation)
        if resource_prefix is None:
            raise ValueError("unsupported_operation")
        response = {
            "contract": "FixtureEffectResponse/v1",
            "resource_id": f"{resource_prefix}:{sha256(key)[:16]}",
            "operation": operation,
        }
        response_json = canonical_json(response)
        committed_at = now_iso()
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT request_sha256,response_json FROM effects WHERE idempotency_key=?", (key,),
            ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != request_sha:
                    connection.execute("ROLLBACK")
                    raise RuntimeError("idempotency_conflict")
                connection.execute("COMMIT")
                return json.loads(existing["response_json"]), False
            connection.execute(
                "INSERT INTO effects VALUES(?,?,?,?,?,?)",
                (key, operation, request_json, request_sha, response_json, committed_at),
            )
            connection.execute("COMMIT")
        return response, True

    def set_fault(self, operation: str, mode: str, remaining: int) -> None:
        allowed_operations = {"calendar.create", "document.create", "delivery.send"}
        allowed_modes = {"rate_limit", "reject", "disconnect_before_commit", "disconnect_after_commit"}
        if operation not in allowed_operations or mode not in allowed_modes or remaining < 1:
            raise ValueError("invalid_fault")
        with closing(self.connect()) as connection:
            connection.execute(
                "INSERT INTO faults(operation,mode,remaining) VALUES(?,?,?) "
                "ON CONFLICT(operation) DO UPDATE SET mode=excluded.mode,remaining=excluded.remaining",
                (operation, mode, remaining),
            )

    def take_fault(self, operation: str) -> str | None:
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT mode,remaining FROM faults WHERE operation=?", (operation,),
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            remaining = int(row["remaining"]) - 1
            if remaining == 0:
                connection.execute("DELETE FROM faults WHERE operation=?", (operation,))
            else:
                connection.execute(
                    "UPDATE faults SET remaining=? WHERE operation=?", (remaining, operation),
                )
            connection.execute("COMMIT")
            return str(row["mode"])

    def record_model_call(
        self, episode_id: str, request_sha256: str, response_sha256: str,
    ) -> None:
        if not episode_id or len(request_sha256) != 64 or len(response_sha256) != 64:
            raise ValueError("invalid_model_call")
        with closing(self.connect()) as connection:
            connection.execute(
                "INSERT INTO model_calls(episode_id,request_sha256,response_sha256,happened_at) "
                "VALUES(?,?,?,?)",
                (episode_id, request_sha256, response_sha256, now_iso()),
            )

    def evidence(self) -> dict:
        with closing(self.connect()) as connection:
            calls = [dict(row) for row in connection.execute("SELECT * FROM calls ORDER BY seq")]
            rows = connection.execute("SELECT idempotency_key FROM effects ORDER BY idempotency_key")
            effects = [self.effect(row["idempotency_key"]) for row in rows]
            model_calls = [
                dict(row) for row in connection.execute("SELECT * FROM model_calls ORDER BY seq")
            ]
        return {
            "contract": "FixtureEvidence/v1",
            "calls": calls,
            "effects": effects,
            "model_calls": model_calls,
        }


class Handler(BaseHTTPRequestHandler):
    server: "FixtureHttpServer"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, status: int, body: dict, headers: dict[str, str] | None = None) -> None:
        data = canonical_json(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _disconnect(self) -> None:
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        status = HTTPStatus.OK
        if parsed.path == "/health":
            body = {"ok": True, "contract": "FixtureHealth/v1"}
        elif parsed.path == "/v1/mail/latest":
            sender = parse_qs(parsed.query).get("sender", [""])[0]
            if sender != "Noor BV":
                status, body = HTTPStatus.NOT_FOUND, {"error": "mail_not_found"}
            else:
                body = {**STATIC_MAIL, "content_sha256": sha256(STATIC_MAIL)}
        elif parsed.path == "/v1/erp/customer":
            email = parse_qs(parsed.query).get("email", [""])[0]
            if email != STATIC_CUSTOMER["email"]:
                status, body = HTTPStatus.NOT_FOUND, {"error": "customer_not_found"}
            else:
                body = STATIC_CUSTOMER
        elif parsed.path == "/v1/calendar/freebusy":
            day = parse_qs(parsed.query).get("day", [""])[0]
            if day != STATIC_FREEBUSY["day"]:
                status, body = HTTPStatus.NOT_FOUND, {"error": "day_not_found"}
            else:
                body = STATIC_FREEBUSY
        elif parsed.path.startswith("/v1/effects/"):
            key = unquote(parsed.path.removeprefix("/v1/effects/"))
            body = self.server.store.effect(key)
            if body is None:
                status, body = HTTPStatus.NOT_FOUND, {"error": "effect_not_found"}
        elif parsed.path == "/v1/evidence":
            body = self.server.store.evidence()
        else:
            status, body = HTTPStatus.NOT_FOUND, {"error": "route_not_found"}
        self.server.store.log_call("GET", self.path, int(status))
        self._send(int(status), body)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.server.store.log_call("POST", self.path, 400)
            self._send(400, {"error": "invalid_json"})
            return
        if parsed.path == "/v1/faults":
            operation = payload.get("operation")
            mode = payload.get("mode")
            remaining = payload.get("remaining", 1)
            try:
                self.server.store.set_fault(operation, mode, remaining)
            except (TypeError, ValueError):
                self.server.store.log_call("POST", self.path, 400)
                self._send(400, {"error": "invalid_fault"})
                return
            self.server.store.log_call("POST", self.path, 200)
            self._send(200, {"ok": True})
            return
        if parsed.path == "/v1/model-calls":
            try:
                self.server.store.record_model_call(
                    payload["episode_id"], payload["request_sha256"], payload["response_sha256"],
                )
            except (KeyError, TypeError, ValueError):
                self.server.store.log_call("POST", self.path, 400)
                self._send(400, {"error": "invalid_model_call"})
                return
            self.server.store.log_call("POST", self.path, 200)
            self._send(200, {"ok": True})
            return
        if parsed.path != "/v1/effects":
            self.server.store.log_call("POST", self.path, 404)
            self._send(404, {"error": "route_not_found"})
            return
        key = self.headers.get("Idempotency-Key", "")
        operation = payload.get("operation")
        request_sha = sha256(payload)
        if not key or not isinstance(operation, str):
            self.server.store.log_call(
                "POST", self.path, 400, idempotency_key=key or None,
                request_sha256=request_sha,
            )
            self._send(400, {"error": "missing_effect_identity"})
            return
        fault = self.server.store.take_fault(operation)
        if fault == "rate_limit":
            self.server.store.log_call(
                "POST", self.path, 429, idempotency_key=key, request_sha256=request_sha,
            )
            self._send(429, {"error": "rate_limited", "retry_after_ms": 250}, {"Retry-After": "0.25"})
            return
        if fault == "reject":
            self.server.store.log_call(
                "POST", self.path, 422, idempotency_key=key, request_sha256=request_sha,
            )
            self._send(422, {"error": "effect_rejected"})
            return
        if fault == "disconnect_before_commit":
            self.server.store.log_call(
                "POST", self.path, 599, idempotency_key=key, request_sha256=request_sha,
            )
            self._disconnect()
            return
        try:
            response, created = self.server.store.create_effect(key, operation, payload)
        except ValueError:
            self.server.store.log_call(
                "POST", self.path, 400, idempotency_key=key, request_sha256=request_sha,
            )
            self._send(400, {"error": "unsupported_operation"})
            return
        except RuntimeError:
            self.server.store.log_call(
                "POST", self.path, 409, idempotency_key=key, request_sha256=request_sha,
            )
            self._send(409, {"error": "idempotency_conflict"})
            return
        status = 201 if created else 200
        self.server.store.log_call(
            "POST", self.path, status, idempotency_key=key, request_sha256=request_sha,
        )
        if fault == "disconnect_after_commit":
            self._disconnect()
            return
        self._send(status, response)


class FixtureHttpServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], store: FixtureStore):
        super().__init__(address, Handler)
        self.store = store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    server = FixtureHttpServer((args.host, args.port), FixtureStore(Path(args.db).resolve()))
    bound_host, bound_port = server.server_address[:2]
    print(canonical_json({
        "contract": "FixtureStarted/v1",
        "url": f"http://{bound_host}:{bound_port}",
        "db": str(Path(args.db).resolve()),
    }), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
