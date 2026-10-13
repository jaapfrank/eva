"""Local collection transport for sealed fixtures; no task logic or model calls.

Each instance owns one service and one verified synthetic principal. Config:
contract, service (unchanged fixture), access {principal_id, audience_kind,
token_file}, grants {collection: {operations, create_limit?}}. The bearer is a
fixture-only capability, accepted as bearer or host-scoped session cookie.
It is never copied into state, schema or journals.
Readback is a local evaluator API, never an HTTP endpoint.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import CookieError, SimpleCookie
from urllib.parse import unquote, urlsplit
from uuid import uuid4


SESSION_COOKIE = "ev003_fixture_session"


def _capability(authorization, cookie_header, token):
    # An explicit bearer is authoritative; a cookie cannot override its denial.
    if authorization:
        return hmac.compare_digest(authorization.encode("utf-8"), ("Bearer " + token).encode("utf-8")), "bearer"
    jar = SimpleCookie()
    try:
        jar.load(cookie_header or "")
    except CookieError:
        return False, None
    occurrences = sum(part.strip().split("=", 1)[0] == SESSION_COOKIE for part in (cookie_header or "").split(";"))
    item = jar.get(SESSION_COOKIE)
    if item is None or occurrences != 1:
        return False, None
    return hmac.compare_digest(item.value.encode("utf-8"), token.encode("utf-8")), "cookie"


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _atomic(path, value):
    temporary = path.with_suffix(".next")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(_bytes(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replay(state_dir):
    initial = json.loads((state_dir / "initial.json").read_text())
    state = copy.deepcopy(initial["state"])
    journal = []
    path = state_dir / "journal.jsonl"
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("incomplete fixture journal")
    for line in raw.splitlines():
        entry = json.loads(line)
        if entry["sequence"] != len(journal) + 1:
            raise ValueError("fixture journal sequence differs")
        if entry["applied"]:
            collection, identifier = entry["collection"], entry["record_id"]
            if state[collection].get(identifier) != entry["old"]:
                raise ValueError("fixture journal state differs")
            if entry["new"] is None:
                del state[collection][identifier]
            else:
                state[collection][identifier] = entry["new"]
        journal.append(entry)
    return initial, state, journal


def readback(state_dir):
    """Evaluator-only durable before/after state and all admitted/denied effects."""
    state_dir = Path(state_dir)
    initial, state, journal = _replay(state_dir)
    return {"contract": "EV003CollectionReadback/v1", "service_id": initial["service_id"],
            "principal_id": initial["principal_id"], "audience_kind": initial["audience_kind"],
            "initial": initial["state"], "state": state, "journal": journal,
            "initial_sha256": hashlib.sha256((state_dir / "initial.json").read_bytes()).hexdigest(),
            "journal_sha256": hashlib.sha256((state_dir / "journal.jsonl").read_bytes()).hexdigest(),
            "state_sha256": _hash(state), "source_service_sha256": initial["source_service_sha256"],
            "snapshot_matches_journal": json.loads((state_dir / "state.json").read_text()) == state}


class CollectionFixture:
    def __init__(self, config, state_dir):
        if config.get("contract") != "EV003CollectionFixture/v1":
            raise ValueError("fixture config contract differs")
        self.service = copy.deepcopy(config["service"])
        self.access = copy.deepcopy(config["access"])
        self.grants = copy.deepcopy(config["grants"])
        if self.service.get("kind") != "generic_local_fixture_service" or self.service.get("external_egress") is not False:
            raise ValueError("not a local generic fixture service")
        if not self.access.get("principal_id") or not self.access.get("audience_kind"):
            raise ValueError("verified fixture identity required")
        self.token = Path(self.access["token_file"]).read_text().strip()
        if len(self.token) < 24:
            raise ValueError("fixture capability is missing")
        origin = urlsplit(self.service["origin"])
        if (origin.scheme != "http" or not origin.hostname or origin.path not in ("", "/")
                or origin.username or origin.password or origin.query or origin.fragment):
            raise ValueError("fixture origin differs")
        self.host = origin.hostname
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_dir.chmod(0o700)
        self.lock = threading.RLock()
        state = {}
        for collection, records in self.service["collections"].items():
            if "/" in collection or not collection:
                raise ValueError("invalid collection")
            state[collection] = {}
            for source in records:
                record = copy.deepcopy(source)
                identifier = record.get("id")
                if not isinstance(identifier, str) or not identifier or "/" in identifier or identifier in state[collection]:
                    raise ValueError("invalid original record identity")
                record.setdefault("revision", 1)
                if type(record["revision"]) is not int or record["revision"] < 1:
                    raise ValueError("invalid original revision")
                state[collection][identifier] = record
        for collection, grant in self.grants.items():
            if (collection not in state or not set(grant["operations"]) <= {"read", "create", "update", "delete"}
                    or not set(grant["operations"]) <= set(self.service["operations"])):
                raise ValueError("invalid fixture grant")
            if "create_limit" in grant and (type(grant["create_limit"]) is not int or grant["create_limit"] < 0):
                raise ValueError("invalid create grant")
        binding = {"service": self.service, "principal_id": self.access["principal_id"],
                   "audience_kind": self.access["audience_kind"], "grants": self.grants}
        if (self.state_dir / "initial.json").exists():
            initial, state, journal = _replay(self.state_dir)
            if initial["binding_sha256"] != _hash(binding):
                raise ValueError("existing fixture binding differs")
        else:
            if any(self.state_dir.iterdir()):
                raise ValueError("fixture directory is not fresh")
            initial = {"service_id": self.service["id"], "principal_id": self.access["principal_id"],
                       "audience_kind": self.access["audience_kind"], "binding_sha256": _hash(binding),
                       "source_service_sha256": _hash(self.service), "state": state}
            _atomic(self.state_dir / "initial.json", initial)
            fd = os.open(self.state_dir / "journal.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            journal = []
        self.state, self.journal = state, journal
        _atomic(self.state_dir / "state.json", state)

    def _record(self, entry):
        entry["sequence"] = len(self.journal) + 1
        with (self.state_dir / "journal.jsonl").open("ab") as stream:
            stream.write(_bytes(entry) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.journal.append(entry)
        if entry["applied"]:
            records = self.state[entry["collection"]]
            if entry["new"] is None:
                del records[entry["record_id"]]
            else:
                records[entry["record_id"]] = entry["new"]
            _atomic(self.state_dir / "state.json", self.state)
        return entry["status"], copy.deepcopy(entry["response"])

    def request(self, method, target, *, authorization, host, body=None, idempotency_key=None, transport_error=False, cookie_header=None):
        """Dispatch only ordinary collection operations; never chooses task data."""
        with self.lock:
            authenticated, auth_transport = _capability(authorization, cookie_header, self.token)
            parsed = urlsplit(target)
            parts = [unquote(v) for v in parsed.path.split("/") if v]
            principal = self.access["principal_id"] if authenticated else None
            entry = {"principal_id": principal, "capability_transport": auth_transport if authenticated else None, "audience_kind": self.access["audience_kind"] if authenticated else None,
                     "request": {"method": method, "path": parsed.path, "body": copy.deepcopy(body)},
                     "idempotency_key": idempotency_key, "applied": False, "collection": None,
                     "record_id": None, "old": None, "new": None, "status": 400,
                     "response": {"error": "invalid_request"}}
            def finish(status, response):
                entry.update(status=status, response=response)
                return self._record(entry)
            if not authenticated:
                entry["request"]["body"] = None
                return finish(401, {"error": "access_denied"})
            if transport_error:
                entry["request"]["malformed"] = True
                return finish(400, {"error": "invalid_request"})
            if urlsplit("//" + host).hostname != self.host or parsed.query or any("/" in v for v in parts):
                return finish(404, {"error": "not_available"})
            if method == "GET" and parts == ["schema"]:
                return finish(200, {"service": self.service["id"], "collections": {
                    k: {"operations": v["operations"], "record_schema": {"type": "object", "additionalProperties": True},
                        "identity_fields": {"id": "stable string", "revision": "positive integer"}}
                    for k, v in self.grants.items()}, "contract": self.service["contract"]})
            if len(parts) not in (2, 3) or parts[0] != "collections" or parts[1] not in self.grants:
                return finish(404, {"error": "not_available"})
            collection = parts[1]
            operation = {"GET": "read", "POST": "create", "PATCH": "update", "DELETE": "delete"}.get(method)
            grant = self.grants[collection]
            if operation not in grant["operations"]:
                return finish(404, {"error": "not_available"})
            if method == "POST" and len(parts) != 2 or method in ("PATCH", "DELETE") and len(parts) != 3:
                return finish(400, {"error": "invalid_request"})
            entry["collection"] = collection
            identifier = parts[2] if len(parts) == 3 else None
            old = self.state[collection].get(identifier) if identifier else None
            if method == "GET":
                if identifier and old is None:
                    return finish(404, {"error": "not_available"})
                return finish(200, copy.deepcopy(old) if identifier else {"records": list(copy.deepcopy(self.state[collection]).values())})
            if body is not None and not isinstance(body, dict) or method in ("POST", "PATCH") and not isinstance(body, dict):
                return finish(400, {"error": "invalid_request"})
            fingerprint = _hash(entry["request"])
            entry["request_sha256"] = fingerprint
            if idempotency_key is not None:
                if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 256:
                    return finish(400, {"error": "invalid_idempotency_key"})
                prior = next((v for v in self.journal if v["idempotency_key"] == idempotency_key and v["applied"]), None)
                if prior:
                    if prior["request_sha256"] != fingerprint:
                        return finish(409, {"error": "idempotency_conflict"})
                    entry["replayed_sequence"] = prior["sequence"]
                    return finish(prior["status"], prior["response"])
            if method == "POST":
                creates = sum(v["applied"] and v["collection"] == collection and v["request"]["method"] == "POST" for v in self.journal)
                if "create_limit" in grant and creates >= grant["create_limit"]:
                    return finish(403, {"error": "mandate_limit"})
                if any(k in body for k in ("id", "revision", "expected_revision")):
                    return finish(400, {"error": "reserved_field"})
                identifier = uuid4().hex
                new = {**copy.deepcopy(body), "id": identifier, "revision": 1}
            else:
                if old is None:
                    return finish(404, {"error": "not_available"})
                expected = (body or {}).get("expected_revision")
                if expected is not None and (type(expected) is not int or expected != old["revision"]):
                    return finish(409, {"error": "revision_conflict"})
                if any(k in (body or {}) for k in ("id", "revision")):
                    return finish(400, {"error": "reserved_field"})
                changes = {k: v for k, v in (body or {}).items() if k != "expected_revision"}
                new = {**old, **copy.deepcopy(changes), "revision": old["revision"] + 1} if method == "PATCH" else None
            entry.update(applied=True, record_id=identifier, old=copy.deepcopy(old), new=new)
            return finish(201 if method == "POST" else 200, new if new is not None else {"deleted": identifier})


def http_server(fixture, host="127.0.0.1", port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def dispatch(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 1_048_576:
                    raise ValueError("invalid length")
                raw = self.rfile.read(length)
                body = json.loads(raw) if raw else None
                status, value = fixture.request(self.command, self.path,
                    authorization=self.headers.get("Authorization"), host=self.headers.get("Host", ""),
                    body=body, idempotency_key=self.headers.get("Idempotency-Key"),
                    cookie_header="; ".join(self.headers.get_all("Cookie", [])))
            except (ValueError, UnicodeError, TypeError):
                status, value = fixture.request(self.command, "/",
                    authorization=self.headers.get("Authorization"), host="",
                    transport_error=True, cookie_header="; ".join(self.headers.get_all("Cookie", [])))
            data = _bytes(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PATCH = do_DELETE = dispatch
    return ThreadingHTTPServer((host, port), Handler)


def serve(config_path, state_dir, host, port, ready_path):
    fixture = CollectionFixture(json.loads(Path(config_path).read_text()), state_dir)
    server = http_server(fixture, host, port)
    _atomic(Path(ready_path), {"contract": "EV003CollectionReady/v1", "service_id": fixture.service["id"],
                             "address": server.server_address, "source_service_sha256": _hash(fixture.service)})
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("serve")
    for flag in ("config", "state-dir", "ready"):
        start.add_argument("--" + flag, type=Path, required=True)
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=0)
    read = commands.add_parser("readback")
    read.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "serve":
        serve(args.config, args.state_dir, args.host, args.port, args.ready)
    else:
        print(json.dumps(readback(args.state_dir), ensure_ascii=False))
