"""Bounded internal liveness for the serving worker event loop, not its providers.

Opt in with EVA_DELEGATED_WORK_HEALTH_PORT on the worker and the corresponding
EVA_DELEGATED_WORK_HEALTH_URL=http://<worker>:<port>/health on the core. Keep the
port on the internal service network; it needs no published host port. Owner
observations cache their original measurement time for 30 seconds. A live lane
may still be waiting on a provider; this signal does not prove task progress.
"""
from contextlib import asynccontextmanager
import asyncio
import json


@asynccontextmanager
async def worker_liveness(*, port, stop, lanes, host="0.0.0.0"):
    """Serve only while the owning service lives; expose no task or owner data."""
    connections = set()

    async def handle(reader, writer):
        if len(connections) >= 16:
            writer.close()
            return
        task = asyncio.current_task()
        connections.add(task)
        try:
            try:
                headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
                if len(headers) > 2048:
                    raise ValueError("oversized request")
                request = headers.split(b"\r\n", 1)[0]
                if request not in {b"GET /health HTTP/1.0", b"GET /health HTTP/1.1"}:
                    status, body = "404 Not Found", b""
                else:
                    active = sum(not lane.done() for lane in lanes)
                    ready = bool(lanes) and active == len(lanes) and not stop.is_set()
                    body = json.dumps({"contract": "EvaWorkerLiveness/v1",
                        "state": "ready" if ready else "attention", "active_lanes": active},
                        separators=(",", ":")).encode()
                    status = "200 OK" if ready else "503 Service Unavailable"
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
                status, body = "400 Bad Request", b""
            writer.write((f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                          f"Content-Length: {len(body)}\r\nConnection: close\r\n"
                          "Cache-Control: no-store\r\n\r\n").encode() + body)
            await asyncio.wait_for(writer.drain(), timeout=1)
        except (ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            connections.discard(task)

    server = await asyncio.start_server(handle, host, port, limit=2048)
    try:
        yield server
    finally:
        server.close()
        await server.wait_closed()
        pending = tuple(connections)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
