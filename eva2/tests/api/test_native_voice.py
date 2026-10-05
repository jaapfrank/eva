"""Native HTTP lifecycle tests, not HA hardware or runtime admission proof."""
import asyncio
from threading import Event, Lock

from eva.adapters.api.app import create_app
import httpx2 as httpx
import pytest

from eva.adapters.api.native_voice import (
    NativeVoiceService, VoiceReply, VoiceRequest,
)


class Ledger:
    def __init__(self):
        self.states = {}
        self.digests = {}
        self.reports = []
        self.lock = Lock()

    def begin(self, *, device_id, request_id, now, delivery_mode="speaker"):
        with self.lock:
            key = (device_id, request_id)
            if key in self.states:
                return False
            self.states[key] = "running"
            return True

    def interrupt(self, *, device_id, request_id, now):
        with self.lock:
            self.states[device_id, request_id] = "stopped"

    def claim_reply(self, *, device_id, request_id, now, reply_sha256):
        with self.lock:
            key = (device_id, request_id)
            if self.states.get(key) != "running":
                return False
            self.states[key] = "claimed"
            self.digests[key] = reply_sha256
            return True

    def record_playback(self, **kwargs):
        from eva.application.audio_playback import PlaybackConflict
        if self.digests.get((kwargs["device_id"], kwargs["request_id"])) != kwargs["reply_sha256"]:
            raise PlaybackConflict("no reply")
        self.reports.append(kwargs)


def payload(request_id="b" * 32, device="a" * 32, satellite="room"):
    return dict(request_id=request_id, device_id=device,
                satellite_id="assist_satellite." + satellite,
                conversation_id="ha-session", text="Hallo", language="nl")


def setup(respond, ledger=None, validate=lambda: None):
    ledger = ledger or Ledger()
    service = NativeVoiceService(token="synthetic", devices={
        "assist_satellite.room": "a" * 32, "assist_satellite.garden": "c" * 32,
    }, ledger=ledger, respond=respond, validate_current=validate)
    app = create_app(object(), native_voice=service)
    return service, ledger, httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test",
        headers={"Authorization": "Bearer synthetic"})


async def answer(request):
    return VoiceReply("Dag!", True)


def test_native_http_reply_schema_replay_and_stop_before_start():
    async def run():
        service, ledger, client = setup(answer)
        async with client:
            data = payload()
            response = await client.post("/internal/v1/ha-voice/turns", json=data)
            assert response.status_code == 200
            assert response.json() == {"request_id": data["request_id"], "reply": "Dag!",
                                       "continue_conversation": True}
            assert (await client.post("/internal/v1/ha-voice/turns", json=data)).status_code == 409
            data["request_id"] = "d" * 32
            stop = {k: data[k] for k in ("device_id", "satellite_id")}
            assert (await client.post(f"/internal/v1/ha-voice/turns/{data['request_id']}/interrupt",
                                      json=stop)).status_code == 200
            assert (await client.post("/internal/v1/ha-voice/turns", json=data)).status_code == 409
            assert ledger.states[data["device_id"], data["request_id"]] == "stopped"
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize("mutation,status", [
    ({"device_id": "d" * 32}, 403), ({"principal_id": "Jaap"}, 422),
    ({"text": "  "}, 422), ({"request_id": "bad"}, 422),
    ({"text": True}, 422), ({"satellite_id": "assist_satellite.unknown"}, 403),
])
def test_native_http_rejects_unbound_identity_and_invalid_input(mutation, status):
    async def run():
        service, ledger, client = setup(answer)
        async with client:
            assert (await client.post("/internal/v1/ha-voice/turns",
                                      json={**payload(), **mutation})).status_code == status
            assert not ledger.states
            assert (await client.post("/internal/v1/ha-voice/turns", json=payload(),
                                      headers={"Authorization": "Bearer wrong"})).status_code == 401
        await service.close()
    asyncio.run(run())


def test_native_new_question_stops_same_speaker_only():
    async def run():
        started = {key: asyncio.Event() for key in ("b", "d", "e")}
        cancelled = asyncio.Event()
        finish = asyncio.Event()
        async def responder(request):
            key = request.request_id[0]
            started[key].set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                if key == "b":
                    cancelled.set()
                raise
            return VoiceReply(key)
        service, ledger, client = setup(responder)
        async with client:
            old = asyncio.create_task(client.post("/internal/v1/ha-voice/turns", json=payload()))
            await started["b"].wait()
            garden = asyncio.create_task(client.post("/internal/v1/ha-voice/turns",
                json=payload("e" * 32, "c" * 32, "garden")))
            await started["e"].wait()
            new = asyncio.create_task(client.post("/internal/v1/ha-voice/turns", json=payload("d" * 32)))
            await started["d"].wait()
            await cancelled.wait()
            assert (await old).status_code == 409
            assert not garden.done()
            finish.set()
            assert (await new).json()["reply"] == "d"
            assert (await garden).json()["reply"] == "e"
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize("delivery_mode", ["speaker", "automation"])
def test_native_interrupt_suppresses_responder_that_returns_after_cancel(delivery_mode):
    async def run():
        started = asyncio.Event()
        async def responder(request):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return VoiceReply("Oud antwoord")
        service, ledger, client = setup(responder)
        async with client:
            data = {**payload(), "delivery_mode": delivery_mode}
            question = asyncio.create_task(client.post("/internal/v1/ha-voice/turns", json=data))
            await started.wait()
            stop = {k: data[k] for k in ("device_id", "satellite_id")}
            assert (await client.post(f"/internal/v1/ha-voice/turns/{data['request_id']}/interrupt",
                                      json=stop)).status_code == 200
            assert (await question).status_code == 409
            assert ledger.states[data["device_id"], data["request_id"]] == "stopped"
        await service.close()
    asyncio.run(run())


def test_native_cancel_while_database_admission_is_still_in_flight():
    class SlowLedger(Ledger):
        def __init__(self):
            super().__init__()
            self.entered, self.release = Event(), Event()
        def begin(self, **kwargs):
            self.entered.set()
            assert self.release.wait(3)
            return super().begin(**kwargs)
    async def run():
        ledger = SlowLedger()
        service, _, client = setup(answer, ledger)
        task = asyncio.create_task(service.run(VoiceRequest(**payload())))
        assert await asyncio.to_thread(ledger.entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        ledger.release.set()
        await asyncio.to_thread(ledger.begin, **{k: payload()[k] for k in ("device_id", "request_id")}, now=None)
        assert ledger.states["a" * 32, "b" * 32] == "stopped"
        await client.aclose()
        await service.close()
    asyncio.run(run())


def test_native_rechecks_revocation_before_reply():
    async def run():
        active = True
        def validate():
            if not active:
                raise ValueError("synthetic revoked grant")
        async def responder(request):
            nonlocal active
            active = False
            return VoiceReply("Private content must not escape")
        service, ledger, client = setup(responder, validate=validate)
        async with client:
            response = await client.post("/internal/v1/ha-voice/turns", json=payload())
            assert response.status_code == 503
            assert "Private content" not in response.text
            assert ledger.states["a" * 32, "b" * 32] == "stopped"
        await service.close()
    asyncio.run(run())



def test_native_stop_after_commit_before_admission_return_prevents_model_start():
    class CommittedLedger(Ledger):
        def __init__(self):
            super().__init__()
            self.committed, self.release = Event(), Event()
        def begin(self, **kwargs):
            result = super().begin(**kwargs)
            self.committed.set()
            assert self.release.wait(3)
            return result
    async def run():
        ledger = CommittedLedger()
        called = False
        async def responder(request):
            nonlocal called
            called = True
            return VoiceReply("Should never run")
        service, _, client = setup(responder, ledger)
        task = asyncio.create_task(service.run(VoiceRequest(**payload())))
        assert await asyncio.to_thread(ledger.committed.wait, 3)
        await service.interrupt("a" * 32, "b" * 32)
        ledger.release.set()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            await task
        assert error.value.status_code == 409
        assert not called
        assert not service._pending
        await client.aclose()
        await service.close()
    asyncio.run(run())


def test_native_close_waits_for_responder_cancellation_cleanup():
    async def run():
        started, cleaning, release, finished = (asyncio.Event() for _ in range(4))
        async def responder(request):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await release.wait()
                finished.set()
                raise
        service, ledger, client = setup(responder)
        task = asyncio.create_task(service.run(VoiceRequest(**payload())))
        await started.wait()
        await service.interrupt("a" * 32, "b" * 32)
        await cleaning.wait()
        close = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        assert not close.done()
        assert service._workers
        release.set()
        await close
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        assert not service._workers
        await client.aclose()
    asyncio.run(run())



def test_native_shutdown_joins_worker_even_when_durable_stop_fails():
    class FailingLedger(Ledger):
        def interrupt(self, **kwargs):
            raise RuntimeError("synthetic database unavailable")
    async def run():
        started, cleaning, release, finished = (asyncio.Event() for _ in range(4))
        async def responder(request):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await release.wait()
                finished.set()
                raise
        service, ledger, client = setup(responder, FailingLedger())
        task = asyncio.create_task(service.run(VoiceRequest(**payload())))
        await started.wait()
        close = asyncio.create_task(service.close())
        await cleaning.wait()
        await asyncio.sleep(0)
        assert not close.done()
        release.set()
        with pytest.raises(RuntimeError, match="could not commit every stop"):
            await close
        await asyncio.gather(task, return_exceptions=True)
        assert finished.is_set() and not service._workers
        await client.aclose()
    asyncio.run(run())



def test_native_route_is_disabled_by_default_and_closed_by_core_lifespan():
    async def run():
        app = create_app(object())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.post("/internal/v1/ha-voice/turns", json=payload())).status_code == 404
        service, ledger, client = setup(answer)
        app = create_app(object(), native_voice=service)
        async with app.router.lifespan_context(app):
            assert not service._closed
        assert service._closed
        await client.aclose()
    asyncio.run(run())


def test_revoked_authority_blocks_new_work_but_authenticated_stop_still_cancels():
    async def run():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        active = [True]
        def validate():
            if not active[0]:
                raise ValueError("Revoked")
        async def waiting(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        service, ledger, client = setup(waiting, validate=validate)
        async with client:
            data = payload()
            running = asyncio.create_task(client.post("/internal/v1/ha-voice/turns", json=data))
            await asyncio.wait_for(entered.wait(), 2)
            active[0] = False
            stop = {key: data[key] for key in ("device_id", "satellite_id")}
            url = f"/internal/v1/ha-voice/turns/{data['request_id']}/interrupt"
            assert (await client.post(url, json=stop,
                                      headers={"Authorization": "Bearer wrong"})).status_code == 401
            assert not cancelled.is_set()
            assert (await client.post("/internal/v1/ha-voice/turns",
                                      json=payload("d" * 32))).status_code == 503
            assert (await client.post(url, json=stop)).status_code == 200
            await asyncio.wait_for(cancelled.wait(), 2)
            assert (await running).status_code == 409
            assert ledger.states[data["device_id"], data["request_id"]] == "stopped"
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize("change,expected", [
    ({}, 200), ({"device_id": "c" * 32}, 403), ({"reply_sha256": "f" * 64}, 409),
    ({"principal_id": "Jaap"}, 422),
    ({"result": {"status": "completed"}}, 422),
])
def test_playback_api_binds_reply_and_device_without_starting_new_work(change, expected):
    async def run():
        import hashlib
        service, ledger, client = setup(answer)
        async with client:
            request = payload()
            assert (await client.post("/internal/v1/ha-voice/turns", json=request)).status_code == 200
            report = {"satellite_id": request["satellite_id"], "device_id": request["device_id"],
                "reply_sha256": hashlib.sha256("Dag!".encode()).hexdigest(),
                "result": {"session_id": "a" * 16, "generation": 1, "status": "interrupted",
                           "sample_rate": 24000, "offered_frames": 2400, "confirmed_frames": 480}}
            response = await client.post("/internal/v1/ha-voice/turns/" + request["request_id"] + "/playback",
                                         json={**report, **change})
            assert response.status_code == expected
            assert len(ledger.reports) == (1 if expected == 200 else 0)
            assert len(ledger.states) == 1
        await service.close()
    asyncio.run(run())


def test_revocation_blocks_capabilities_but_allows_audit_of_existing_answer():
    async def run():
        import hashlib
        active = True
        def validate():
            if not active:
                raise ValueError("revoked")
        service, ledger, client = setup(answer, validate=validate)
        async with client:
            request = payload()
            params = {k: request[k] for k in ("satellite_id", "device_id")}
            assert (await client.get("/internal/v1/ha-voice/capabilities", params=params)).json() == {"playback_reporting": 1}
            await client.post("/internal/v1/ha-voice/turns", json=request)
            active = False
            assert (await client.get("/internal/v1/ha-voice/capabilities", params=params)).status_code == 503
            report = {**params, "reply_sha256": hashlib.sha256("Dag!".encode()).hexdigest(),
                "result": {"session_id": "a" * 16, "generation": 1, "status": "completed",
                           "sample_rate": 24000, "offered_frames": 2400, "confirmed_frames": 2400}}
            assert (await client.post("/internal/v1/ha-voice/turns/" + request["request_id"] + "/playback", json=report)).status_code == 200
        await service.close()
    asyncio.run(run())


def test_stream_advertises_shared_input_receipt_without_changing_reply_or_digest():
    import hashlib
    import json
    from eva.application.conversation_receipt import RECEIVED_INPUT_TEXT
    async def run():
        service, ledger, client = setup(answer)
        async with client:
            response = await client.post("/internal/v1/ha-voice/turns", json=payload(),
                headers={"Accept": "application/x-ndjson"})
            assert response.status_code == 200
            assert response.headers["X-Eva-Input-Receipt"] == RECEIVED_INPUT_TEXT
            final = json.loads(response.text.splitlines()[-1])
            assert final["reply"] == "Dag!"
            assert ledger.digests["a" * 32, "b" * 32] == hashlib.sha256(b"Dag!").hexdigest()
            denied = await client.post("/internal/v1/ha-voice/turns", json=payload(),
                headers={"Accept": "application/x-ndjson", "Authorization": "Bearer wrong"})
            assert denied.status_code == 401 and "X-Eva-Input-Receipt" not in denied.headers
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize("delivery_mode", ["speaker", "automation"])
def test_automation_handoff_outlives_audio_window_without_changing_speaker_timeout(monkeypatch, delivery_mode):
    async def run():
        observed = []
        original_wait = asyncio.wait_for

        async def elapsed_audio_window(awaitable, timeout):
            observed.append(timeout)
            if timeout == 32:
                awaitable.cancel()
                raise TimeoutError
            return await original_wait(awaitable, timeout)

        monkeypatch.setattr(asyncio, "wait_for", elapsed_audio_window)
        service, ledger, client = setup(answer)
        data = {**payload(), "delivery_mode": delivery_mode}
        async with client:
            if delivery_mode == "speaker":
                with pytest.raises(TimeoutError):
                    await service.run(VoiceRequest(**data))
                assert ledger.states[data["device_id"], data["request_id"]] == "stopped"
                assert observed == [32]
            else:
                reply = await service.run(VoiceRequest(**data))
                assert reply.text == "Dag!"
                assert ledger.states[data["device_id"], data["request_id"]] == "claimed"
                assert observed == [None]
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True], ids=["json", "ndjson"])
def test_response_failure_logs_only_bounded_code_locations(stream, caplog):
    import hashlib
    import json
    import logging

    exception_message = "private-exception-message"
    prompt = "private-household-prompt"
    token = "synthetic"
    local_secret = "private-frame-local"

    def fail_with_private_context(depth, private_prompt):
        private_local = local_secret
        if depth:
            return fail_with_private_context(depth - 1, private_prompt)
        assert private_local
        raise RuntimeError(f"{exception_message}: {private_prompt}; token={token}")

    async def responder(request):
        fail_with_private_context(24, request.text)

    async def run():
        service, ledger, client = setup(responder)
        data = {**payload(), "text": prompt, "delivery_mode": "automation"}
        try:
            async with client:
                response = await client.post("/internal/v1/ha-voice/turns", json=data,
                    headers={"Accept": "application/x-ndjson"} if stream else {})
            if stream:
                assert response.status_code == 200
                assert [json.loads(line) for line in response.text.splitlines()] == [
                    {"request_id": data["request_id"], "type": "error"}]
            else:
                assert response.status_code == 503
                assert response.json() == {"detail": "Voice response unavailable"}

            records = [record for record in caplog.records
                       if record.name == "eva.adapters.api.native_voice"]
            assert len(records) == 1
            record = records[0]
            assert record.levelno == logging.ERROR
            assert record.exc_info is None and record.stack_info is None
            message = record.getMessage()
            prefix = "native_voice_response_failed "
            assert message.startswith(prefix)
            diagnostic = json.loads(message[len(prefix):])
            assert set(diagnostic) == {"request_id_sha256", "exception_type", "frames"}
            assert diagnostic["request_id_sha256"] == hashlib.sha256(data["request_id"].encode()).hexdigest()
            assert diagnostic["exception_type"] == "RuntimeError"
            assert len(diagnostic["frames"]) == 16
            assert diagnostic["frames"][-1][:2] == ["test_native_voice.py", "fail_with_private_context"]
            for filename, function, line in diagnostic["frames"]:
                assert filename and "/" not in filename and "\\" not in filename
                assert isinstance(function, str) and function
                assert type(line) is int and line > 0

            exposed = caplog.text + response.text + str(response.headers)
            for secret in (exception_message, prompt, token, local_secret):
                assert secret not in exposed
            assert data["request_id"] not in caplog.text
            assert "RuntimeError" not in response.text
            assert ledger.states[data["device_id"], data["request_id"]] == "stopped"
            assert not ledger.digests
        finally:
            await service.close()
            await client.aclose()
        assert not service._workers

    with caplog.at_level(logging.ERROR, logger="eva.adapters.api.native_voice"):
        asyncio.run(run())
