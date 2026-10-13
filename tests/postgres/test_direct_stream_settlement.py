"""Actual SDK/Pydantic/httpx stream closure, offline transport and real PG."""

import asyncio
import json
import os
from pathlib import Path

import httpx2
import pytest

from eva.adapters.model import pydantic_conversation as module
from eva.adapters.model.openai_codex_provider import OpenAICodexProvider
from eva.application.cancellation import (
    CancellationSignal,
    WorkInterrupted,
    cancellation_scope,
)
from eva.application.conversation import (
    ConversationConflict,
    ConversationModelUsage,
    observe_model_requests,
)
from tests.model.test_openai_codex_provider_shared import _sse
from tests.model.test_pydantic_conversation_model import _adapter_type, _generate, _turn
from tests.postgres.test_direct_model_attempts import journal, records  # noqa: F401
from tests.postgres.test_people_memory_overview import people_memory_engine  # noqa: F401


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "read_error",
        "body_close_error",
        "transport_close_error",
        "stop",
        "close_race",
    ],
)
def test_actual_stream_settlement_only_after_successful_close(
    request, tmp_path, monkeypatch, mode
):
    ledger = request.getfixturevalue("journal")
    signal = CancellationSignal()
    observed = {"body_closes": 0, "transport_closes": 0, "requests": 0}
    close_error = RuntimeError("SECRET original transport close failure")

    class Body(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for index, chunk in enumerate(_sse("Hallo Jaap.")):
                if index == 2 and mode == "read_error":
                    raise httpx2.ReadError("SECRET interrupted stream")
                if index == 1 and mode == "stop":
                    ledger.request_turn_stop(_turn())
                    signal.cancel()
                    await asyncio.sleep(0)
                yield chunk

        async def aclose(self):
            observed["body_closes"] += 1
            assert records(ledger) == []  # No terminal record before close returns.
            if mode == "body_close_error":
                raise httpx2.ReadError("SECRET failed body close")
            if mode == "close_race":
                ledger.request_turn_stop(_turn())
                signal.cancel()
                await asyncio.sleep(0)
            await asyncio.sleep(0)

    class Network(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, wire):
            observed["requests"] += 1
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=Body(),
                request=wire,
            )

        async def aclose(self):
            observed["transport_closes"] += 1
            assert records(ledger) == []
            await asyncio.sleep(0)
            if mode == "transport_close_error":
                raise close_error

    sessions = []

    class Provider(OpenAICodexProvider):
        def __init__(self, settings, *, credential_loader):
            super().__init__(
                settings,
                credential_loader=lambda: ("offline", "offline"),
                network_transport=Network(),
            )

        async def __aenter__(self):
            session = await super().__aenter__()
            assert session.local_stream_closed is None
            sessions.append(session)
            return session

    monkeypatch.setattr(module, "OpenAICodexProvider", Provider)
    model = _adapter_type()(auth_file=tmp_path / "unused.json")
    with cancellation_scope(signal), observe_model_requests(_turn(), ledger):
        if mode == "success":
            assert _generate(model).text == "Hallo Jaap."
        elif mode in ("stop", "close_race"):
            with pytest.raises(WorkInterrupted):
                _generate(model)
        elif mode == "transport_close_error":
            with pytest.raises(RuntimeError) as caught:
                _generate(model)
            assert caught.value is close_error
        else:
            with pytest.raises(module.ConversationModelError):
                _generate(model)
    assert observed == {"body_closes": 1, "transport_closes": 1, "requests": 1}
    (row,) = records(ledger)
    closed = mode in ("success", "read_error", "stop")
    assert sessions[0].local_stream_closed is (True if closed else None)
    assert row.get("local_stream_closed") is (True if closed else None)
    assert row["outcome"] == ("succeeded" if mode == "success" else "failed")
    usage = ConversationModelUsage.from_wire(row["usage"])
    ledger.complete_model_request(
        _turn(),
        row["request_id"],
        usage,
        outcome=row["outcome"],
        local_stream_closed=row.get("local_stream_closed"),
    )
    assert records(ledger) == [row]
    if not closed:
        with pytest.raises(ConversationConflict):
            ledger.complete_model_request(
                _turn(),
                row["request_id"],
                usage,
                outcome=row["outcome"],
                local_stream_closed=True,
            )
    with pytest.raises(ValueError):
        ledger.complete_model_request(
            _turn(), row["request_id"], usage, local_stream_closed="true"
        )

    if os.environ.get("CP001_ATTEMPT_EVIDENCE_DIR"):
        (
            Path(os.environ["CP001_ATTEMPT_EVIDENCE_DIR"]) / f"stream-{mode}.json"
        ).write_text(
            json.dumps(
                {
                    "mode": mode,
                    "observed": observed,
                    "local_stream_closed": sessions[0].local_stream_closed,
                    "record": row,
                },
                indent=2,
            )
            + "\n"
        )
