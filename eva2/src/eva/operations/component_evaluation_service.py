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
    if not isinstance(config, dict) or not required <= config.keys() or config.keys() - required - limits - {"procedure_execution_enabled", "programming_workspace", "reviewed_component_applications"}:
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
    if type(config.get("procedure_execution_enabled", False)) is not bool:
        raise ValueError("invalid procedure execution switch")
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

    def __init__(self, address, *, evaluator, token, allowed_paths, procedure_executor=None, programming_workspaces=None, reviewed_applications=None):
        self.evaluator = evaluator
        self.procedure_executor = procedure_executor
        self.programming_workspaces = programming_workspaces
        self.reviewed_applications = reviewed_applications
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

    def _programming(self):
        if self.server.programming_workspaces is None:
            self._reply(404, {"error": "not_found"})
            return
        if not self._authenticated():
            self._reply(401, {"error": "unauthorized"})
            return
        # In particular, close must never wait for the evaluator or a tool call.
        try:
            lengths = self.headers.get_all("Content-Length", [])
            types = self.headers.get_all("Content-Type", [])
            if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()
                    or self.headers.get_all("Transfer-Encoding") or len(types) != 1
                    or types[0].split(";", 1)[0].strip().lower() != "application/json"):
                raise ValueError()
            length = int(lengths[0])
            if not 1 <= length <= _MAX_BODY:
                raise ValueError()
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError()
            value = json.loads(raw, object_pairs_hook=_object)
            if not isinstance(value, dict):
                raise ValueError()
            action = value.get("action")
            keys = {"session_key", "action"}
            if action == "close" and set(value) == keys:
                self.server.programming_workspaces.close(value["session_key"])
                result = {"closed": True}
            elif action == "status" and set(value) == keys | {"workspace_key"}:
                result = self.server.programming_workspaces.processes_idle(
                    value["session_key"], workspace_key=value["workspace_key"])
            elif action == "list" and set(value) == keys | {"workspace_key"}:
                result = self.server.programming_workspaces.list_tools(
                    value["session_key"], workspace_key=value["workspace_key"])
            elif action == "call" and set(value) == keys | {"workspace_key", "name", "arguments", "request_id"}:
                result = self.server.programming_workspaces.call_tool(
                    value["session_key"], value["name"], value["arguments"], value["request_id"],
                    workspace_key=value["workspace_key"])
            else:
                raise ValueError()
            if not isinstance(result, dict):
                raise ValueError("programming result must be an object")
        except (ValueError, TypeError, UnicodeError, OSError, OverflowError, RecursionError):
            self._reply(400, {"error": "invalid_programming_request"})
            return
        except Exception:
            self._reply(500, {"error": "programming_execution_unconfirmed"})
            return
        self._reply(200, result)

    def _reviewed_component(self):
        if self.server.reviewed_applications is None:
            self._reply(404, {"error": "not_found"})
            return
        if not self._authenticated():
            self._reply(401, {"error": "unauthorized"})
            return
        try:
            lengths = self.headers.get_all("Content-Length", [])
            types = self.headers.get_all("Content-Type", [])
            if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()
                    or self.headers.get_all("Transfer-Encoding") or len(types) != 1
                    or types[0].split(";", 1)[0].strip().lower() != "application/json"):
                raise ValueError()
            length = int(lengths[0])
            if not 1 <= length <= 8192:
                raise ValueError()
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError()
            value = json.loads(raw, object_pairs_hook=_object)
            if not isinstance(value, dict) or set(value) != {"action", "review_id", "request_id", "task_id"}:
                raise ValueError()
            if value["action"] == "request":
                result = self.server.reviewed_applications.request(
                    value["review_id"], value["request_id"], value["task_id"])
            elif value["action"] == "status":
                result = self.server.reviewed_applications.review_status(value["review_id"])
            else:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, OSError, OverflowError, RecursionError):
            self._reply(400, {"error": "invalid_component_application"})
            return
        except Exception:
            self._reply(500, {"error": "component_application_unconfirmed"})
            return
        self._reply(200, result)

    def do_POST(self):
        if self.path == "/reviewed-component":
            self._reviewed_component()
            return
        if self.path == "/programming":
            self._programming()
            return
        procedure = self.path == "/run-procedure" and self.server.procedure_executor is not None
        if self.path != "/evaluate" and not procedure:
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
                if procedure:
                    if not isinstance(payload, dict) or set(payload) != {"source", "input_data"}:
                        raise ValueError()
                    request = payload
                else:
                    if not isinstance(payload, dict) or set(payload) != {"relative_path", "baseline_sha256", "source"}:
                        raise ValueError()
                    request = CandidateRequest(**payload)
                    if request.relative_path not in self.server.allowed_paths:
                        raise ValueError()
            except (ValueError, TypeError, UnicodeError, OSError, OverflowError, RecursionError):
                self._reply(400, {"error": "invalid_request"})
                return
            try:
                report = (self.server.procedure_executor.execute(**request) if procedure
                          else self.server.evaluator.evaluate(request))
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
    executor = None
    if json.loads(Path(args.config).read_text()).get("procedure_execution_enabled", False):
        from eva.operations.procedure_execution import ProcedureExecutor, ProcedureExecutionConfig
        executor = ProcedureExecutor(ProcedureExecutionConfig(image=settings.image,
            deadline_seconds=min(settings.deadline_seconds, 30),
            output_bytes=min(settings.output_bytes, 16384), memory_mb=settings.memory_mb,
            cpus=settings.cpus, pids=settings.pids))
    programming = None
    config = json.loads(Path(args.config).read_text())
    if "programming_workspace" in config:
        from eva.operations.programming_workspace import ProgrammingWorkspaceConfig, ProgrammingWorkspaces
        programming = ProgrammingWorkspaces(ProgrammingWorkspaceConfig(**config["programming_workspace"]))
    applications = None
    if "reviewed_component_applications" in config:
        from eva.operations.component_application_runtime import configured_component_applications
        applications = configured_component_applications(config["reviewed_component_applications"])
    with EvaluationServer(address, evaluator=ComponentEvaluator(settings), token=token,
                          allowed_paths=allowed_paths, procedure_executor=executor,
                          programming_workspaces=programming, reviewed_applications=applications) as server:
        def stop(signum, frame):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            if applications is not None:
                applications.close()
            if programming is not None:
                programming.close_all()


if __name__ == "__main__":
    main()
