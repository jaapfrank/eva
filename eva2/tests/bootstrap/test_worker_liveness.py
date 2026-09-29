"""Real local HTTP checks without provider traffic or task data."""
import asyncio
import json

import pytest

from eva.bootstrap.worker_liveness import worker_liveness
from eva.bootstrap.delegated_work_service import DelegatedWorkService, DelegatedWorkServiceSettings


async def request(port, raw=b"GET /health HTTP/1.1\r\nHost: local\r\n\r\n"):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 3)
    writer.close()
    await writer.wait_closed()
    return data


def test_liveness_tracks_actual_lane_tasks_stop_and_server_shutdown():
    async def run():
        stop, release = asyncio.Event(), asyncio.Event()
        lanes = (asyncio.create_task(release.wait()), asyncio.create_task(stop.wait()))
        async with worker_liveness(port=0, host="127.0.0.1", stop=stop, lanes=lanes) as server:
            port = server.sockets[0].getsockname()[1]
            data = await request(port)
            assert data.startswith(b"HTTP/1.1 200")
            assert json.loads(data.split(b"\r\n\r\n")[1]) == {
                "contract": "EvaWorkerLiveness/v1", "state": "ready", "active_lanes": 2}
            release.set()
            await lanes[0]
            data = await request(port)
            assert data.startswith(b"HTTP/1.1 503")
            assert json.loads(data.split(b"\r\n\r\n")[1])["active_lanes"] == 1
            stop.set()
            await lanes[1]
            assert (await request(port)).startswith(b"HTTP/1.1 503")
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", port)
    asyncio.run(run())


def test_stopping_live_lanes_is_not_ready_and_bad_requests_are_bounded():
    async def run():
        stop, release = asyncio.Event(), asyncio.Event()
        lane = asyncio.create_task(release.wait())
        try:
            async with worker_liveness(port=0, host="127.0.0.1", stop=stop, lanes=(lane,)) as server:
                port = server.sockets[0].getsockname()[1]
                stop.set()
                assert (await request(port)).startswith(b"HTTP/1.1 503")
                for raw in [b"POST /health HTTP/1.1\r\n\r\n", b"GET /other HTTP/1.1\r\n\r\n"]:
                    assert (await request(port, raw)).startswith(b"HTTP/1.1 404")
                assert (await request(port, b"x" * 3000)).startswith(b"HTTP/1.1 400")
                assert (await request(port, b"GET /health")).startswith(b"HTTP/1.1 400")
        finally:
            release.set()
            await lane
    asyncio.run(run())


def test_port_opt_in_and_default_service_does_not_start_listener(monkeypatch):
    env = {"EVA_DATABASE_DSN_FILE": "/run/db", "EVA_RUNTIME_TENANT_ID": "tenant",
           "EVA_RUNTIME_INSTALLATION_ID": "installation", "EVA_DELEGATED_WORKER_ID": "worker"}
    assert DelegatedWorkServiceSettings.from_environment(env).health_port is None
    assert DelegatedWorkServiceSettings.from_environment(env | {"EVA_DELEGATED_WORK_HEALTH_PORT": "8081"}).health_port == 8081
    for value in ["0", "65536", "bad", "1.5"]:
        with pytest.raises((RuntimeError, ValueError)):
            DelegatedWorkServiceSettings.from_environment(env | {"EVA_DELEGATED_WORK_HEALTH_PORT": value})
    async def forbidden(*args, **kwargs):
        raise AssertionError("disabled liveness must not open a listener")
    monkeypatch.setattr(asyncio, "start_server", forbidden)
    class Worker:
        async def run_once(self, **kwargs):
            return "idle"
    assert asyncio.run(DelegatedWorkService(Worker(), poll_interval_seconds=.05).serve(once=True)) == "completed"
