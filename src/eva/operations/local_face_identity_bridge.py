"""Narrow optional transport from Eva to an existing loopback ArcFace service.

No agent, camera watcher, enrollment or database migration runs here. The caller
supplies one still image. Existing enrolled profiles are opened read-only for each
request, so corrections remain effective without a copied biometric database.
"""

from __future__ import annotations

import argparse
import base64
from datetime import UTC, datetime
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess

import httpx2 as httpx

from eva.adapters.connectors.home_assistant_camera import CameraSnapshot
from eva.adapters.connectors.local_face_identity import (
    MAX_RESPONSE_BYTES, face_observation, local_http_endpoint, read_enrolled_profiles,
)
from eva.adapters.credentials.runtime_files import read_runtime_secret_file


class FaceIdentityBridge:
    def __init__(self, *, profiles_db: str, backend_endpoint: str, backend_revision: str,
                 cpu_python: str | None = None, cpu_model_root: str | None = None):
        self.profiles_db = str(Path(profiles_db).resolve(strict=True))
        self.backend_endpoint = local_http_endpoint(backend_endpoint, loopback=True)
        if not backend_revision.strip() or len(backend_revision) > 200:
            raise ValueError("face backend revision is required")
        self.backend_revision = backend_revision
        if bool(cpu_python) != bool(cpu_model_root):
            raise ValueError("CPU face mode needs both explicit Python and model root")
        self.cpu_command = None
        if cpu_python and cpu_model_root:
            if not backend_revision.endswith(":cpu"):
                raise ValueError("CPU face mode requires an explicit :cpu backend revision")
            python = Path(cpu_python)
            if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
                raise ValueError("CPU face Python must be an installed absolute executable")
            models = Path(cpu_model_root).resolve(strict=True)
            self.cpu_command = (str(python), "-I", str(Path(__file__).with_name("local_face_cpu.py")),
                                "--model-root", str(models))

    def _cpu_result(self, image: bytes | None) -> dict:
        if self.cpu_command is None:
            raise RuntimeError("CPU face mode is not selected")
        command = self.cpu_command + (("--check",) if image is None else ())
        environment = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1",
                       "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
        try:
            completed = subprocess.run(command, input=image, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=12, check=True, env=environment)
        except (subprocess.SubprocessError, OSError):
            raise RuntimeError("explicit CPU face extraction did not complete") from None
        if len(completed.stdout) > MAX_RESPONSE_BYTES:
            raise RuntimeError("CPU face extraction response exceeds limit")
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise ValueError("invalid CPU face extraction result")
        return result

    def health(self) -> dict:
        profiles, revision = read_enrolled_profiles(self.profiles_db)
        if self.cpu_command is not None:
            state = self._cpu_result(None)
            if state.get("ok") is not True or state.get("execution_provider") != "CPUExecutionProvider":
                raise RuntimeError("CPU face runtime is not available")
            return {"ok": True, "backend": "arcface", "backend_revision": self.backend_revision,
                    "execution_provider": "CPUExecutionProvider", "model_hashes": state["model_hashes"],
                    "profile_revision": revision, "enrolled_profiles": len(profiles),
                    "automatic_enrollment": False, "inference_checked": False}
        with httpx.Client(timeout=3, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", self.backend_endpoint + "/health") as response:
                if response.status_code != 200:
                    raise RuntimeError("local face backend is unavailable")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 16_384:
                        raise RuntimeError("local face health response exceeds limit")
        state = json.loads(body)
        if (not isinstance(state, dict) or state.get("ok") is not True
                or state.get("faces_klaar") is not True):
            raise RuntimeError("local face backend is not ready")
        return {"ok": True, "backend": "arcface", "backend_revision": self.backend_revision,
                "profile_revision": revision, "enrolled_profiles": len(profiles),
                "automatic_enrollment": False}

    def analyze(self, snapshot: CameraSnapshot) -> dict:
        profiles, revision = read_enrolled_profiles(self.profiles_db)
        if self.cpu_command is not None:
            return face_observation(snapshot, self._cpu_result(snapshot.data), profiles,
                                    profile_revision=revision, backend_revision=self.backend_revision)
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", self.backend_endpoint + "/faces_b64",
                    json={"image_b64": base64.b64encode(snapshot.data).decode("ascii")}) as response:
                if response.status_code != 200:
                    raise RuntimeError("local face backend did not complete")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise RuntimeError("local face backend response exceeds limit")
        return face_observation(snapshot, json.loads(body), profiles,
                                profile_revision=revision, backend_revision=self.backend_revision)


def make_handler(bridge: FaceIdentityBridge, token: str) -> type[BaseHTTPRequestHandler]:
    if not isinstance(token, str) or not 32 <= len(token) <= 4096 or any(not 33 <= ord(c) <= 126 for c in token):
        raise ValueError("face bridge bearer token is required")

    class Handler(BaseHTTPRequestHandler):
        # HTTPServer intentionally serializes inference. There is no image queue,
        # background enrollment or unbounded inference-thread creation.
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, _format: str, *args: object) -> None:
            pass  # Paths, names, images, tokens and inference payloads are private.

        def _respond(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            values = self.headers.get_all("Authorization") or []
            if len(values) != 1 or not hmac.compare_digest(values[0].encode("utf-8"), ("Bearer " + token).encode()):
                self._respond(401, {"error": "unauthorized"})
                return False
            return True

        def do_GET(self) -> None:
            if not self._authorized():
                return
            if self.path != "/health":
                self._respond(404, {"error": "not_found"})
                return
            try:
                self._respond(200, bridge.health())
            except (RuntimeError, ValueError, OSError, httpx.HTTPError):
                self._respond(503, {"error": "face_backend_unavailable"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            if self.path != "/analyze":
                self._respond(404, {"error": "not_found"})
                return
            lengths = self.headers.get_all("Content-Length") or []
            media = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            try:
                if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
                    raise ValueError("invalid content framing")
                length = int(lengths[0])
                if not 1 <= length <= 2_097_152:
                    self._respond(413, {"error": "image_size_exceeded"})
                    return
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError("incomplete image")
                snapshot = CameraSnapshot("camera.local_face_input", media, data, datetime.now(UTC))
                hashes = self.headers.get_all("X-Image-SHA256") or []
                if len(hashes) != 1 or not hmac.compare_digest(hashes[0], snapshot.sha256):
                    raise ValueError("image hash mismatch")
            except (ValueError, RuntimeError, OSError):
                self._respond(400, {"error": "invalid_image_request"})
                return
            try:
                self._respond(200, bridge.analyze(snapshot))
            except (RuntimeError, ValueError, OSError, httpx.HTTPError):
                self._respond(503, {"error": "face_analysis_unavailable"})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", required=True, help="Exact local loopback or Docker bridge address")
    parser.add_argument("--port", type=int, default=44741)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--profiles-db", required=True)
    parser.add_argument("--backend-endpoint", default="http://127.0.0.1:3220")
    parser.add_argument("--backend-revision", required=True)
    parser.add_argument("--cpu-python", help="Explicit existing InsightFace Python; no automatic fallback")
    parser.add_argument("--cpu-model-root", help="Existing pinned InsightFace root; no downloads")
    args = parser.parse_args()
    address = ipaddress.ip_address(args.bind)
    if (address.is_unspecified or address.is_multicast
            or not (address.is_loopback or any(address in ipaddress.ip_network(network)
                for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")))):
        parser.error("bind must name an exact local interface, never a wildcard")
    if not 1 <= args.port <= 65535:
        parser.error("port must be within 1..65535")
    token = read_runtime_secret_file(args.token_file, setting_name="face_bridge_token").strip()
    bridge = FaceIdentityBridge(profiles_db=args.profiles_db,
                                backend_endpoint=args.backend_endpoint,
                                backend_revision=args.backend_revision,
                                cpu_python=args.cpu_python, cpu_model_root=args.cpu_model_root)
    handler = make_handler(bridge, token)
    class Server(HTTPServer):
        address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    with Server((args.bind, args.port), handler) as server:
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
