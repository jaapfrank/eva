import asyncio
from threading import Event

import pytest

from eva.application.cancellation import (
    CancellationSignal, WorkInterrupted, cancellation_scope,
    check_cancellation, run_cancellable, run_in_cancellation_thread,
)


def test_signal_is_permanent_and_does_not_escape_its_context():
    signal = CancellationSignal()
    signal.cancel()
    with cancellation_scope(signal), pytest.raises(WorkInterrupted):
        check_cancellation()
    check_cancellation()


def test_stopped_signal_never_starts_provider():
    async def run():
        signal = CancellationSignal()
        signal.cancel()
        called = False
        async def provider():
            nonlocal called
            called = True
        with cancellation_scope(signal), pytest.raises(WorkInterrupted):
            await run_cancellable(provider)
        assert not called
    asyncio.run(run())


def test_cancelled_sync_bridge_joins_cooperative_provider_cleanup():
    started, cleaning, release, finished = (Event() for _ in range(4))
    async def provider():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await asyncio.to_thread(release.wait, 3)
            finished.set()
    async def run():
        task = asyncio.create_task(run_in_cancellation_thread(lambda: asyncio.run(run_cancellable(provider))))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        assert await asyncio.to_thread(cleaning.wait, 3)
        assert not task.done()
        task.cancel()  # Repeated stop must not abandon cleanup or recancel provider.
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        assert await run_in_cancellation_thread(lambda: 42) == 42
    asyncio.run(run())


def test_late_output_after_provider_swallows_cancel_is_rejected():
    async def run():
        signal = CancellationSignal()
        started = asyncio.Event()
        async def provider():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "stale answer"
        with cancellation_scope(signal):
            task = asyncio.create_task(run_cancellable(provider))
            await started.wait()
            signal.cancel()
            with pytest.raises(WorkInterrupted):
                await task
    asyncio.run(run())



def test_nested_provider_scope_keeps_outer_cancellation_registration():
    async def run():
        signal = CancellationSignal()
        ready = asyncio.Event()
        async def inner():
            return 1
        async def outer():
            assert await run_cancellable(inner) == 1
            ready.set()
            await asyncio.Event().wait()
        with cancellation_scope(signal):
            task = asyncio.create_task(run_cancellable(outer))
            await ready.wait()
            signal.cancel()
            with pytest.raises(WorkInterrupted):
                await asyncio.wait_for(task, 1)
        assert not signal._tasks
    asyncio.run(run())
