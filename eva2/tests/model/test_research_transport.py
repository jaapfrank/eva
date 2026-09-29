"""No external I/O: slow SSE, headers and cancellation retain local ownership."""
import asyncio
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from time import monotonic
import threading
import urllib.error
import urllib.request

import httpx2
import pytest

from eva.adapters.model import research_transport as transport
from eva.adapters.model import pydantic_conversation as model_module
from eva.adapters.model.contracts import ConversationModelError, ConversationModelDeadlineExceeded
from eva.adapters.model.research_tools import NativeResearchTool
from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope


ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
FRAMES = b'data: {"type":"response.completed","response":{"status":"completed","output":[],"usage":{"input_tokens":2,"output_tokens":1,"total_tokens":3}}}\n'


class Stream(httpx2.AsyncByteStream):
    def __init__(self, *, delay=0, chunks=(FRAMES,)):
        self.delay, self.chunks = delay, chunks
        self.closed = self.active = False

    async def __aiter__(self):
        self.active = True
        try:
            for chunk in self.chunks:
                await asyncio.sleep(self.delay)
                yield chunk
        finally:
            self.active = False

    async def aclose(self):
        self.closed = True


def install(monkeypatch, stream, *, status=200, header_delay=0):
    state = SimpleNamespace(requests=[], closed=False, headers_active=False, started=threading.Event())

    class Network(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            state.requests.append(request)
            state.started.set()
            state.headers_active = True
            try:
                await asyncio.sleep(header_delay)
                return httpx2.Response(status, stream=stream, request=request)
            finally:
                state.headers_active = False

        async def aclose(self):
            state.closed = True

    monkeypatch.setattr(transport.httpx2, "AsyncHTTPTransport", lambda **kwargs: Network())
    return state


def request():
    return urllib.request.Request(ENDPOINT, data=b'{"stream":true}', headers={
        "Authorization":"Bearer offline-placeholder", "OpenAI-Account-ID":"offline-account",
        "Content-Type":"application/json", "Origin":"https://chatgpt.com"})


def test_preserves_request_and_closes_success_before_return(monkeypatch):
    stream = Stream(chunks=(FRAMES[:20], FRAMES[20:]))
    state = install(monkeypatch, stream)
    result = transport.open_research_response(request(), deadline=monotonic()+1, maximum_bytes=1000)
    assert result.read() == FRAMES
    assert state.closed and stream.closed and not stream.active
    sent = state.requests[0]
    assert str(sent.url) == ENDPOINT and sent.method == "POST"
    assert sent.content == request().data
    assert sent.headers["authorization"] == "Bearer offline-placeholder"
    assert sent.headers["openai-account-id"] == "offline-account"


@pytest.mark.parametrize("headers", [False, True])
def test_absolute_deadline_closes_dripping_sse_or_blocked_headers(monkeypatch, headers):
    stream = Stream(delay=0.02, chunks=(b": keepalive\n",)*100)
    state = install(monkeypatch, stream, header_delay=10 if headers else 0)
    started = monotonic()
    with pytest.raises(ConversationModelDeadlineExceeded, match="deadline"):
        transport.open_research_response(request(), deadline=started+0.06, maximum_bytes=10000)
    assert monotonic()-started < 0.8
    assert state.closed and not state.headers_active and not stream.active
    assert headers or stream.closed


def test_response_bound_and_http_errors_close_without_retry(monkeypatch):
    stream = Stream(chunks=(b"x"*30,))
    state = install(monkeypatch, stream)
    with pytest.raises(ConversationModelError, match="too large"):
        transport.open_research_response(request(), deadline=monotonic()+1, maximum_bytes=20)
    assert stream.closed and state.closed and len(state.requests)==1
    stream = Stream(chunks=(b"error"*1000,))
    state = install(monkeypatch, stream, status=401)
    with pytest.raises(urllib.error.HTTPError) as caught:
        transport.open_research_response(request(), deadline=monotonic()+1, maximum_bytes=20)
    assert caught.value.code == 401 and len(caught.value.read())==1024
    assert stream.closed and state.closed and len(state.requests)==1


@pytest.mark.parametrize("owner_stop", [False, True])
@pytest.mark.parametrize("headers", [False, True])
def test_native_bridge_joins_provider_before_timeout_or_owner_stop(monkeypatch, owner_stop, headers):
    stream = Stream(delay=10)
    state = install(monkeypatch, stream, header_delay=10 if headers else 0)
    finished = threading.Event()

    class Research:
        def research_current_options(self, **kwargs):
            try:
                return transport.open_research_response(request(), deadline=monotonic()+10, maximum_bytes=1000)
            finally:
                finished.set()

    tool = NativeResearchTool(Research(), lambda value:value)
    monkeypatch.setattr(tool, "_check", lambda _:None)
    monkeypatch.setattr(tool, "_validate_execution_input", lambda _:None)
    signal = CancellationSignal()
    async def run():
        tool_request = SimpleNamespace(arguments={"question":"Public question", "title":"Title", "options":["HTTP"]})
        with cancellation_scope(signal):
            if owner_stop:
                task = asyncio.create_task(tool.execute(tool_request))
                async with asyncio.timeout(1):
                    while not state.started.is_set():
                        await asyncio.sleep(0.001)
                signal.cancel()
                with pytest.raises(WorkInterrupted):
                    await asyncio.wait_for(task,1)
            else:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(tool.execute(tool_request),0.06)
            # These assertions happen before asyncio.run's executor shutdown.
            assert finished.is_set() and state.closed
            assert not stream.active and not state.headers_active
            assert headers or stream.closed
    started = monotonic()
    asyncio.run(run())
    assert monotonic()-started < 0.8


def test_stop_before_transport_never_dispatches(monkeypatch):
    state = install(monkeypatch, Stream())
    signal = CancellationSignal()
    signal.cancel()
    with cancellation_scope(signal), pytest.raises(WorkInterrupted):
        transport.open_research_response(request(), deadline=monotonic()+1, maximum_bytes=1000)
    assert state.requests == []


def test_research_retry_shares_absolute_deadline_and_preserves_usage(monkeypatch):
    model = model_module.PydanticConversationModel(auth_file=Path("/not-read"))
    deadlines = []
    monkeypatch.setattr(model,"_load_auth",lambda:(str(len(deadlines)),"offline-account"))
    def open_response(request, *, deadline, maximum_bytes):
        deadlines.append(deadline)
        if len(deadlines)==1:
            raise urllib.error.HTTPError(ENDPOINT,401,"expired",{},BytesIO(b"expired"))
        return BytesIO(FRAMES)
    monkeypatch.setattr(model_module,"open_research_response",open_response)
    monkeypatch.setattr(model_module.urllib.request,"urlopen",lambda *a,**k:pytest.fail("research must not use sync network"))
    parsed, usage = model._request_using({"tools":[{"search_context_size":"low","type":"web_search"}]},
        model._parse_research_sse, include_usage=True, provider_managed_research_input=True)
    assert deadlines[0] == deadlines[1]
    assert parsed == ("",(),()) and usage.input_tokens==2 and usage.output_tokens==1
    assert usage.model_attempts==2 and usage.tool_calls==0


@pytest.mark.parametrize("send_headers", [False, True])
def test_real_loopback_connection_closes_on_deadline(send_headers):
    """Exercise the pinned client socket, not just a mocked stream's close flag."""
    async def run():
        peer_closed = asyncio.Event()
        async def serve(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                if send_headers:
                    writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
                    await writer.drain()
                # Withhold the next byte: a socket read timeout alone is not
                # the proof; the absolute async deadline must close the peer.
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
                peer_closed.set()
        server = await asyncio.start_server(serve,"127.0.0.1",0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            local_request = urllib.request.Request(f"http://127.0.0.1:{port}/offline-fixture")
            with pytest.raises(ConversationModelDeadlineExceeded, match="deadline"):
                await asyncio.to_thread(transport.open_research_response, local_request,
                    deadline=monotonic()+0.15, maximum_bytes=1000)
            await asyncio.wait_for(peer_closed.wait(),1)
    asyncio.run(run())
