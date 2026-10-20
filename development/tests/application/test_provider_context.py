from contextvars import ContextVar
from threading import Barrier, Event, get_ident
from types import SimpleNamespace

import pytest

from eva.application.cancellation import (
    CancellationSignal, WorkInterrupted, cancellation_scope,
)
from eva.application.provider_context import collect_provider_context


def test_independent_reads_overlap_without_losing_order_or_request_context():
    scope = ContextVar("test_provider_scope", default="missing")
    barrier = Barrier(2)
    main_thread = get_ident()

    def provider(name):
        def read(turn):
            assert turn == "turn"
            assert scope.get() == "principal"
            assert get_ident() != main_thread
            scope.set(name)
            barrier.wait(timeout=3)
            return (name,)
        return SimpleNamespace(parallel_context_reads=True, active_context=read)

    token = scope.set("principal")
    try:
        assert collect_provider_context([provider("first"), provider("second")], "turn") == (
            "first", "second")
        assert scope.get() == "principal"
    finally:
        scope.reset(token)


def test_unmarked_readers_keep_serial_order_and_calling_thread():
    main_thread = get_ident()
    calls = []

    def read(name):
        def run(turn):
            assert get_ident() == main_thread
            calls.append(name)
            return (name,)
        return run

    providers = [SimpleNamespace(active_context=read("first")),
                 SimpleNamespace(parallel_context_reads=lambda turn: False,
                                 active_context=read("second"))]
    assert collect_provider_context(providers, None) == ("first", "second")
    assert calls == ["first", "second"]


def test_reader_failure_propagates_after_other_started_reader_finishes():
    barrier = Barrier(2)
    finished = Event()

    def failing(turn):
        barrier.wait(timeout=3)
        raise PermissionError("revoked")

    def finishing(turn):
        barrier.wait(timeout=3)
        finished.set()
        return ("private context",)

    providers = [SimpleNamespace(parallel_context_reads=True, active_context=read)
                 for read in (failing, finishing)]
    with pytest.raises(PermissionError, match="revoked"):
        collect_provider_context(providers, None)
    assert finished.is_set()


def test_stop_during_reader_prevents_context_from_being_returned():
    signal = CancellationSignal()

    def read(turn):
        signal.cancel()
        return ("must not escape",)

    with cancellation_scope(signal), pytest.raises(WorkInterrupted):
        collect_provider_context([
            SimpleNamespace(parallel_context_reads=True, active_context=read)], None)
