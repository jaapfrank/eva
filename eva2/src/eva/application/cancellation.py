"""Request-owned cooperative cancellation across synchronous and async ports.

This signal stops new work and cancels registered asynchronous provider tasks.
It cannot undo external effects or forcibly terminate synchronous tool code.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import threading
from typing import TypeVar


class WorkInterrupted(RuntimeError):
    """The owning request stopped; no further steps should start."""


class CancellationSignal:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stopped = False
        self._tasks: dict[asyncio.Task, tuple[asyncio.AbstractEventLoop, int]] = {}

    def check(self) -> None:
        with self._lock:
            if self._stopped:
                raise WorkInterrupted("Work was interrupted")

    def cancel(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            tasks = tuple(self._tasks.items())
        for task, (loop, _depth) in tasks:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                # A completed provider's event loop may have just closed.
                pass

    def register(self, task: asyncio.Task, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            if self._stopped:
                raise WorkInterrupted("Work was interrupted")
            previous = self._tasks.get(task)
            self._tasks[task] = (loop, 1 if previous is None else previous[1] + 1)

    def unregister(self, task: asyncio.Task) -> None:
        with self._lock:
            previous = self._tasks.get(task)
            if previous is not None and previous[1] > 1:
                self._tasks[task] = (previous[0], previous[1] - 1)
            else:
                self._tasks.pop(task, None)


_current: ContextVar[CancellationSignal | None] = ContextVar("eva_cancellation", default=None)
T = TypeVar("T")


@contextmanager
def cancellation_scope(signal: CancellationSignal) -> Iterator[None]:
    token = _current.set(signal)
    try:
        yield
    finally:
        _current.reset(token)


def check_cancellation() -> None:
    signal = _current.get()
    if signal is not None:
        signal.check()


async def run_cancellable(operation: Callable[[], Awaitable[T]]) -> T:
    signal = _current.get()
    if signal is None:
        return await operation()
    task = asyncio.current_task()
    assert task is not None
    signal.register(task, asyncio.get_running_loop())
    try:
        try:
            result = await operation()
        except asyncio.CancelledError:
            signal.check()  # Map only an owner-issued cancellation to WorkInterrupted.
            raise
        signal.check()  # A provider may catch cancellation and return late output.
        return result
    finally:
        signal.unregister(task)



async def run_in_cancellation_thread(operation: Callable[[], T]) -> T:
    """Bridge an existing synchronous port without abandoning its worker.

    Owner cancellation signals registered async providers immediately, then
    waits for cooperative cleanup. Already-running synchronous effects may
    finish; this function promises ownership, not forced thread termination.
    """
    signal = CancellationSignal()
    def execute() -> T:
        with cancellation_scope(signal):
            signal.check()
            return operation()
    worker = asyncio.create_task(asyncio.to_thread(execute))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        signal.cancel()
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise
