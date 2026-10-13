"""Exercise bound response ownership, completion and failed closure with HTTPX."""

import asyncio
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "development/benchmark/inspect_ai_v1"))
from cp001_accounting import ProviderSampleLedger, SampleAsyncStream, SampleStopped  # noqa: E402


def ledger(sample):
    return ProviderSampleLedger(sample, {"providerAttempts": 2, "providerInputTokens": 100,
                                        "providerOutputTokens": 100}, {"contact": ("model", "none")})


class Body(httpx.AsyncByteStream):
    def __init__(self, *, block=True, fail_close=False):
        self.block, self.fail_close = block, fail_close
        self.started = asyncio.Event()
        self.close_calls = 0

    async def __aiter__(self):
        self.started.set()
        if self.block:
            await asyncio.Future()
        yield b"unchanged SDK bytes"

    async def aclose(self):
        self.close_calls += 1
        if self.fail_close:
            raise OSError("fixture response close failed")


class IteratorWithoutClose:
    """The installed Responses iterator exposes its response but no close API."""

    def __init__(self, body):
        self.response = httpx.Response(200, stream=body, request=httpx.Request("POST", "https://fixture.invalid"))
        self.parts = self.response.aiter_bytes().__aiter__()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.parts.__anext__()


def test_stop_closes_only_its_owned_response_and_keeps_usage_unknown():
    async def scenario():
        left, right = ledger("left"), ledger("right")
        bodies = [Body(), Body()]
        raw = [IteratorWithoutClose(body) for body in bodies]
        streams = [SampleAsyncStream(book, book.begin("contact", "model", "none"), iterator)
                   for book, iterator in zip([left, right], raw)]
        tasks = [asyncio.create_task(anext(stream)) for stream in streams]
        await asyncio.gather(*(body.started.wait() for body in bodies))
        left.stop_async_attempts()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert raw[0].response.is_closed and bodies[0].close_calls == 1
        assert left.report()["active_async_attempts"] == 0
        assert left.report()["total_usage"]["output_tokens"] is None
        assert not tasks[1].done() and not raw[1].response.is_closed
        assert right.report()["active_async_attempts"] == 1
        right.stop_async_attempts()
        with pytest.raises(asyncio.CancelledError):
            await tasks[1]
        await streams[0].aclose()
        assert [body.close_calls for body in bodies] == [1, 1]
    asyncio.run(scenario())


def test_completed_usage_and_bytes_survive_stream_exhaustion():
    async def scenario():
        book, body = ledger("complete"), Body(block=False)
        attempt = book.begin("contact", "model", "none")
        book.finish(attempt, {"input_tokens": 21, "output_tokens": 9, "total_tokens": 30})
        stream = SampleAsyncStream(book, attempt, IteratorWithoutClose(body))
        assert [part async for part in stream] == [b"unchanged SDK bytes"]
        assert stream.closed and body.close_calls == 1
        assert book.report()["complete"] and book.report()["total_usage"]["output_tokens"] == 9
        assert book.report()["active_async_attempts"] == 0
    asyncio.run(scenario())


def test_failed_response_close_never_turns_into_success_on_retry():
    async def scenario():
        book, body = ledger("failed-close"), Body(fail_close=True)
        raw = IteratorWithoutClose(body)
        stream = SampleAsyncStream(book, book.begin("contact", "model", "none"), raw)
        task = asyncio.create_task(anext(stream))
        await body.started.wait()
        book.stop_async_attempts()
        with pytest.raises(OSError):
            await task
        assert raw.response.is_closed  # HTTPX sets this before awaiting byte-stream close.
        assert not stream.closed and book.report()["active_async_attempts"] == 1
        with pytest.raises(OSError):
            await stream.aclose()
        assert body.close_calls == 1
        assert book.report()["total_usage"]["output_tokens"] is None
    asyncio.run(scenario())


def test_consumed_event_records_usage_before_return_without_changing_bytes():
    async def scenario():
        book, body = ledger("observed"), Body(block=False)
        attempt = book.begin("contact", "model", "none")
        seen = []
        def observe(event):
            seen.append(event)
            book.finish(attempt, {"input_tokens": 21, "output_tokens": 9, "total_tokens": 30})
        stream = SampleAsyncStream(book, attempt, IteratorWithoutClose(body), on_event=observe)
        assert await anext(stream) == b"unchanged SDK bytes"
        assert seen == [b"unchanged SDK bytes"] and book.report()["complete"]
        await stream.aclose()
        assert body.close_calls == 1 and book.report()["total_usage"]["output_tokens"] == 9
    asyncio.run(scenario())


def test_invalid_consumed_usage_closes_owned_response_and_remains_unknown():
    async def scenario():
        book, body = ledger("missing-usage"), Body(block=False)
        attempt = book.begin("contact", "model", "none")
        raw = IteratorWithoutClose(body)
        stream = SampleAsyncStream(book, attempt, raw, on_event=lambda event: book.finish(attempt, None))
        with pytest.raises(SampleStopped):
            await anext(stream)
        assert raw.response.is_closed and body.close_calls == 1
        assert book.report()["active_async_attempts"] == 0
        assert book.report()["attempts"][0]["status"] == "unattested"
        assert book.report()["total_usage"]["output_tokens"] is None
    asyncio.run(scenario())


def test_native_blocking_proxy_ownership_survives_caller_stop():
    import importlib.util
    import threading
    from pathlib import Path
    module_path = Path(__file__).resolve().parents[3] / "development/benchmark/inspect_ai_v1/p1_agentzero_bridge_probe.py"
    spec = importlib.util.spec_from_file_location("native_proxy_ownership", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    async def run():
        class Ledger:
            stopped = None
            def stop_async_attempts(self, reason): self.stopped = reason
        ledger = Ledger()
        owner = module.NativeSyncOwnership(ledger, 120)
        entered, release = threading.Event(), threading.Event()
        def held():
            entered.set()
            release.wait(5)
            return "unchanged native bytes"
        task = asyncio.create_task(owner.call(held))
        while not entered.is_set(): await asyncio.sleep(.001)
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        assert owner.report()["pending_native_io"] == 1
        assert owner.report()["quiescent"] is False
        release.set()
        while owner.report()["pending_native_io"]: await asyncio.sleep(.001)
        assert await owner.call(lambda: b"wire") == b"wire"
        def failed_close(): raise OSError("synthetic close failure")
        try: await owner.close(failed_close)
        except OSError: pass
        assert owner.report()["close_errors"] == ["OSError"]
        assert owner.report()["quiescent"] is False
        owner.executor.shutdown(wait=True)
    asyncio.run(run())


def test_native_close_after_deadline_releases_held_context():
    import contextvars
    import importlib.util
    import threading
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("native_bridge_close", Path(__file__).parents[3] / "development/benchmark/inspect_ai_v1/p1_agentzero_bridge_probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class Ledger:
        def stop_async_attempts(self, reason):
            pass
    async def check():
        owner = module.NativeSyncOwnership(Ledger(), 2)
        entered, release = threading.Event(), threading.Event()
        context = contextvars.copy_context()
        class Upstream:
            native_closed = False
            def close(self):
                self.native_closed = True
                release.set()
        class Response:
            closed = False
            def close(self):
                self.closed = True
        upstream, response = Upstream(), Response()
        def read():
            entered.set()
            release.wait(2)
            return b"original bytes"
        task = asyncio.create_task(owner.call(context.run, read))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        owner.deadline = 0
        await owner.close_response(context, response, [upstream])
        assert await task == b"original bytes"
        assert response.closed and upstream.native_closed
        assert owner.report()["quiescent"]
        owner.executor.shutdown(wait=True)
    asyncio.run(check())
