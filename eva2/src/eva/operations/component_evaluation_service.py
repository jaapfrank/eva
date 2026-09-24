"""Authenticated host endpoint for the fixed isolated component evaluator."""
from __future__ import annotations

import argparse
from dataclasses import fields
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import signal
from pathlib import Path
import threading

from eva.operations.component_evaluation import CandidateRequest, ComponentEvaluator, EvaluationConfig

_MAX_BODY = 96 * 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def load_config(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("evaluation service configuration is too large")
    config = json.loads(raw, object_pairs_hook=_object)
    required = {"image", "command", "token_file", "bind_host", "port", "allowed_paths"}
    limits = {field.name for field in fields(EvaluationConfig)} - {"image", "command"}
    if not isinstance(config, dict) or not required <= config.keys() or config.keys() - required - limits:
        raise ValueError("invalid evaluation service configuration fields")
    if (not isinstance(config["command"], list)
            or not isinstance(config["token_file"], str)
            or not Path(config["token_file"]).is_absolute()
            or type(config["port"]) is not int or not 1 <= config["port"] <= 65535
            or not isinstance(config["bind_host"], str)):
        raise ValueError("invalid evaluation service configuration")
    # Explicit numeric address avoids surprise DNS changes at bind time.
    address = ipaddress.ip_address(config["bind_host"])
    if address.version != 4:
        raise ValueError("evaluation service requires an explicit IPv4 bind address")
    paths = config["allowed_paths"]
    if (not isinstance(paths, list) or not 1 <= len(paths) <= 128
            or any(not isinstance(item, str) for item in paths) or len(set(paths)) != len(paths)):
        raise ValueError("invalid evaluation service path allowlist")
    for item in paths:
        CandidateRequest(item, "0" * 64, "pass")
    settings = EvaluationConfig(image=config["image"], command=tuple(config["command"]),
        **{key: config[key] for key in limits if key in config})
    with Path(config["token_file"]).open("rb") as stream:
        token_bytes = stream.read(4097)
    token = token_bytes.decode("ascii").strip()
    if not 32 <= len(token) <= 256 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("invalid evaluation service token")
    return settings, token, frozenset(paths), (str(address), config["port"])


class EvaluationServer(ThreadingHTTPServer):
    daemon_threads = False
    request_queue_size = 8

    def __init__(self, address, *, evaluator, token, allowed_paths):
        self.evaluator = evaluator
        self.token = token
        self.allowed_paths = allowed_paths
        self.execution_lock = threading.Lock()
        super().__init__(address, EvaluationHandler)

    def handle_error(self, request, client_address):
        # Never send request details or credentials to the server error log.
        pass


class EvaluationHandler(BaseHTTPRequestHandler):
    server_version = "EvaEvaluation"
    sys_version = ""

    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def log_message(self, format, *args):
        pass

    def _reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (OSError, TimeoutError):
            pass

    def _authenticated(self):
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1:
            return False
        try:
            return hmac.compare_digest(values[0].encode("ascii"),
                ("Bearer " + self.server.token).encode("ascii"))
        except UnicodeEncodeError:
            return False

    def do_GET(self):
        if self.path != "/health":
            self._reply(404, {"error": "not_found"})
            return
        self._reply(200, {"status": "ready"})

    def do_POST(self):
        if self.path != "/evaluate":
            self._reply(404, {"error": "not_found"})
            return
        if not self._authenticated():
            self._reply(401, {"error": "unauthorized"})
            return
        if not self.server.execution_lock.acquire(blocking=False):
            self._reply(409, {"error": "busy"})
            return
        try:
            try:
                lengths = self.headers.get_all("Content-Length", [])
                content_types = self.headers.get_all("Content-Type", [])
                if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()
                        or self.headers.get_all("Transfer-Encoding")
                        or len(content_types) != 1
                        or content_types[0].split(";", 1)[0].strip().lower() != "application/json"):
                    raise ValueError()
                length = int(lengths[0])
                if not 1 <= length <= _MAX_BODY:
                    raise ValueError()
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError()
                payload = json.loads(raw, object_pairs_hook=_object)
                if not isinstance(payload, dict) or set(payload) != {"relative_path", "baseline_sha256", "source"}:
                    raise ValueError()
                request = CandidateRequest(**payload)
                if request.relative_path not in self.server.allowed_paths:
                    raise ValueError()
            except (ValueError, TypeError, UnicodeError, OSError, OverflowError, RecursionError):
                self._reply(400, {"error": "invalid_request"})
                return
            try:
                report = self.server.evaluator.evaluate(request)
            except ValueError:
                self._reply(400, {"error": "candidate_rejected"})
                return
            except Exception:
                self._reply(500, {"error": "evaluation_failed"})
                return
            self._reply(200, report)
        finally:
            self.server.execution_lock.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    settings, token, allowed_paths, address = load_config(args.config)
    with EvaluationServer(address, evaluator=ComponentEvaluator(settings), token=token,
                          allowed_paths=allowed_paths) as server:
        def stop(signum, frame):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
