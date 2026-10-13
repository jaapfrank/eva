"""Plaintext localhost PostgreSQL proxy that drops one COMMIT acknowledgement."""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
from pathlib import Path


FAULT_TIMEOUT_SECONDS = 10.0


def _mark(path: Path, value: str = "1\n") -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


class CommitAckProxy:
    def __init__(
        self, target_host: str, target_port: int, markers: Path, cut: str
    ) -> None:
        self.target = (target_host, target_port)
        self.markers = markers
        self.cut = cut
        self.stop = threading.Event()
        self.cut_pending = threading.Event()
        self.fired = threading.Event()
        self.lock = threading.Lock()

    def _pipe_client_to_server(
        self, client: socket.socket, server: socket.socket
    ) -> None:
        tail = b""
        try:
            while not self.stop.is_set():
                data = client.recv(65536)
                if not data:
                    return
                combined = (tail + data).upper()
                tail = combined[-32:]
                if self.fired.is_set() and b"DATASOURCE_OUTPUTS" in combined:
                    if not (self.markers / "reconciled").exists():
                        _mark(self.markers / "reconciled")
                if (
                    b"COMMIT" in combined
                    and (self.markers / "arm").exists()
                    and not self.fired.is_set()
                ):
                    with self.lock:
                        if not self.fired.is_set() and not self.cut_pending.is_set():
                            _mark(self.markers / "reached")
                            if self.cut == "before":
                                self.fired.set()
                                _mark(self.markers / "fired")
                                return
                            self.cut_pending.set()
                server.sendall(data)
        except OSError:
            return

    def _pipe_server_to_client(
        self, server: socket.socket, client: socket.socket
    ) -> None:
        try:
            while not self.stop.is_set():
                data = server.recv(65536)
                if not data:
                    return
                if self.cut_pending.is_set() and not self.fired.is_set():
                    with self.lock:
                        if not self.fired.is_set():
                            self.fired.set()
                            _mark(self.markers / "fired")
                    return
                client.sendall(data)
        except OSError:
            return

    def _connection(self, client: socket.socket) -> None:
        server = socket.create_connection(self.target, timeout=FAULT_TIMEOUT_SECONDS)
        client.settimeout(FAULT_TIMEOUT_SECONDS)
        server.settimeout(FAULT_TIMEOUT_SECONDS)
        forward = threading.Thread(
            target=self._pipe_client_to_server,
            args=(client, server),
            daemon=True,
        )
        backward = threading.Thread(
            target=self._pipe_server_to_client,
            args=(server, client),
            daemon=True,
        )
        forward.start()
        backward.start()
        while forward.is_alive() and backward.is_alive() and not self.stop.is_set():
            time.sleep(0.005)
        try:
            client.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        client.close()
        server.close()

    def serve(self) -> int:
        self.markers.mkdir(parents=True, exist_ok=True)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.1)
        host, port = listener.getsockname()
        _mark(self.markers / "ready", json.dumps({"host": host, "port": port}) + "\n")
        armed_at: float | None = None
        try:
            while not self.stop.is_set():
                if (self.markers / "stop").exists():
                    return 0
                if (self.markers / "arm").exists() and armed_at is None:
                    armed_at = time.monotonic()
                if (
                    armed_at is not None
                    and not self.fired.is_set()
                    and time.monotonic() - armed_at > FAULT_TIMEOUT_SECONDS
                ):
                    _mark(
                        self.markers / "error", "armed COMMIT was not reached in 10s\n"
                    )
                    return 70
                try:
                    client, _address = listener.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=self._connection,
                    args=(client,),
                    daemon=True,
                ).start()
        finally:
            self.stop.set()
            listener.close()
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", required=True, type=int)
    parser.add_argument("--markers", required=True, type=Path)
    parser.add_argument("--cut", choices=("before", "after"), default="after")
    arguments = parser.parse_args()
    return CommitAckProxy(
        arguments.target_host,
        arguments.target_port,
        arguments.markers,
        arguments.cut,
    ).serve()


if __name__ == "__main__":
    raise SystemExit(main())
