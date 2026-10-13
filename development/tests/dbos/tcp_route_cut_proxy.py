"""Local PostgreSQL proxy that cuts one armed route and then refuses reconnects."""

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


class RouteCutProxy:
    def __init__(self, target_host: str, target_port: int, markers: Path) -> None:
        self.target = (target_host, target_port)
        self.markers = markers
        self.stop = threading.Event()
        self.faulted = threading.Event()
        self.lock = threading.Lock()
        self.connections: set[socket.socket] = set()

    def _track(self, connection: socket.socket) -> None:
        with self.lock:
            self.connections.add(connection)

    def _close(self, connection: socket.socket) -> None:
        with self.lock:
            self.connections.discard(connection)
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        connection.close()

    def _pipe(self, source: socket.socket, target: socket.socket) -> None:
        try:
            while not self.stop.is_set() and not self.faulted.is_set():
                data = source.recv(65536)
                if not data:
                    return
                target.sendall(data)
        except OSError:
            return

    def _connection(self, client: socket.socket) -> None:
        if self.faulted.is_set():
            if not (self.markers / "reconciled").exists():
                _mark(self.markers / "reconciled")
            self._close(client)
            return
        try:
            server = socket.create_connection(
                self.target,
                timeout=FAULT_TIMEOUT_SECONDS,
            )
        except OSError:
            self._close(client)
            return
        client.settimeout(FAULT_TIMEOUT_SECONDS)
        server.settimeout(FAULT_TIMEOUT_SECONDS)
        self._track(client)
        self._track(server)
        forward = threading.Thread(
            target=self._pipe,
            args=(client, server),
            daemon=True,
        )
        backward = threading.Thread(
            target=self._pipe,
            args=(server, client),
            daemon=True,
        )
        forward.start()
        backward.start()
        while (
            forward.is_alive()
            and backward.is_alive()
            and not self.stop.is_set()
            and not self.faulted.is_set()
        ):
            time.sleep(0.005)
        self._close(client)
        self._close(server)

    def _fire(self) -> None:
        _mark(self.markers / "reached")
        self.faulted.set()
        with self.lock:
            connections = list(self.connections)
        for connection in connections:
            self._close(connection)
        _mark(self.markers / "fired")

    def serve(self) -> int:
        self.markers.mkdir(parents=True, exist_ok=True)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.05)
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
                    and (self.markers / "cut").exists()
                    and not self.faulted.is_set()
                ):
                    self._fire()
                if (
                    armed_at is not None
                    and not self.faulted.is_set()
                    and time.monotonic() - armed_at > FAULT_TIMEOUT_SECONDS
                ):
                    _mark(
                        self.markers / "error", "armed route cut did not fire in 10s\n"
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
            with self.lock:
                connections = list(self.connections)
            for connection in connections:
                self._close(connection)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", required=True, type=int)
    parser.add_argument("--markers", required=True, type=Path)
    arguments = parser.parse_args()
    return RouteCutProxy(
        arguments.target_host,
        arguments.target_port,
        arguments.markers,
    ).serve()


if __name__ == "__main__":
    raise SystemExit(main())
