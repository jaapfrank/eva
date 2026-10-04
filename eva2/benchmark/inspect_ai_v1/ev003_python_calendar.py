"""Evaluator calendar only; never imported by Eva production code.

Requires pinned time-machine 3.5.1 in the disposable worker. The controller must
advance its atomic control file only after native work and memory have settled.
Real monotonic deadlines and TLS remain outside Python calendar virtualization.
"""
from datetime import datetime
import functools
import json
import math
from pathlib import Path
from types import FunctionType
import time
from unittest.mock import patch

import time_machine


def _real_time():
    return (time_machine.escape_hatch.time.time() if time_machine.escape_hatch.is_travelling()
            else time.time())


def _real_time_ns():
    return (time_machine.escape_hatch.time.time_ns() if time_machine.escape_hatch.is_travelling()
            else time.time_ns())


class _RealDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(_real_time(), tz)

    @classmethod
    def utcnow(cls):
        from datetime import UTC
        return cls.now(UTC).replace(tzinfo=None)


class _RealTime:
    def __getattr__(self, name):
        if name == 'time':
            return _real_time
        if name == 'time_ns':
            return _real_time_ns
        return getattr(time, name)


def protect_native_auth(stack, *, core_model=None, memory_auth_module=None):
    """Keep the existing token checks unchanged except for their clock source.

    No token is supplied, decoded differently, refreshed, extended or accepted by
    this adapter. Core functions retain their exact code; only their datetime
    global refers to actual wall time. Hindsight's auth module similarly retains
    real wall time, including its existing refresh/lock checks.
    """
    if core_model is not None:
        for name in ('credential_status', '_request_using'):
            original = getattr(core_model, name)
            if not isinstance(original, FunctionType):
                raise TypeError('native credential method contract differs')
            cloned = FunctionType(original.__code__,
                {**original.__globals__, 'datetime': _RealDateTime},
                original.__name__, original.__defaults__, original.__closure__)
            cloned.__kwdefaults__ = original.__kwdefaults__
            functools.update_wrapper(cloned, original)
            stack.enter_context(patch.object(core_model, name, cloned))
    if memory_auth_module is not None:
        stack.enter_context(patch.object(memory_auth_module, 'time', _RealTime()))
        stack.enter_context(patch.object(memory_auth_module, 'datetime', _RealDateTime))


class ScenarioCalendar:
    """Synchronous refresh/ack; parent must wait for every worker generation.

    No polling thread, provider request, IPC server or runtime restart is added.
    Existing evaluator control invokes refresh before the next admitted input.
    Updates during work are forbidden by the caller's settlement protocol.
    """
    def __init__(self, clock_file):
        self.path = Path(clock_file)
        self._state = None
        self._travel = None
        self._traveller = None

    def refresh(self):
        state = json.loads(self.path.read_text())
        if (not isinstance(state, dict)
            or set(state) != {'contract', 'generation', 'offsetMilliseconds'}
            or state['contract'] != 'EvaScenarioCalendar/v1'
            or type(state['generation']) is not int or not 0 <= state['generation'] <= 2**53 - 1
            or type(state['offsetMilliseconds']) not in (int, float)
            or not math.isfinite(state['offsetMilliseconds'])):
            raise ValueError('invalid scenario calendar control')
        previous = self._state
        if previous is not None:
            if (state['generation'] < previous['generation']
                or (state['generation'] == previous['generation'] and state != previous)
                or state['offsetMilliseconds'] < previous['offsetMilliseconds']):
                raise ValueError('scenario calendar moved backwards or changed without generation')
        if self._traveller is None:
            if time_machine.escape_hatch.is_travelling():
                raise ValueError('calendar already virtualized')
            destination = _real_time() + state['offsetMilliseconds'] / 1000
            datetime.fromtimestamp(destination)  # Reject invalid dates before changing clocks.
            self._travel = time_machine.travel(destination, tick=True)
            self._traveller = self._travel.start()
            time.time()  # Establish the initial ticking anchor before acknowledgment.
        elif state != previous:
            self._traveller.shift((state['offsetMilliseconds'] - previous['offsetMilliseconds']) / 1000)
        self._state = dict(state)
        return dict(state)

    def restore(self):
        if self._travel is not None:
            self._travel.stop()
        self._travel = self._traveller = self._state = None

    def __enter__(self):
        self.refresh()
        return self

    def __exit__(self, *args):
        self.restore()
