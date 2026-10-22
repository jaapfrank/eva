"""Exercise the installed composition with the real cold-path scheduling guards."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from eva.application.memory_background import HindsightColdPathWorker
import eva.bootstrap.runtime as runtime_module


def _composed_tasks(callbacks):
    tree = ast.parse(Path(runtime_module.__file__).read_text())
    assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'idle_tasks' for t in n.targets)
        and isinstance(n.value, ast.Tuple)
        and any(isinstance(v, ast.Name) and v.id == 'consider_one_private_opportunity'
                for v in n.value.elts)]
    assert len(assignments) == 1
    return tuple(callbacks.get(v.id, lambda: 'idle') for v in assignments[0].value.elts)


@pytest.mark.parametrize('decision', ['adopt', 'idle', 'error', 'foreground', 'capacity', 'stop'])
def test_new_initiative_handoff_uses_same_cycle_and_rechecks_admission(monkeypatch, decision):
    clock = [100.0]
    monkeypatch.setattr('eva.application.memory_background.time.monotonic', lambda: clock[0])
    state = {'pending': False, 'capacity': True}
    calls = []

    def consider():
        calls.append('consider')
        if decision == 'error':
            raise RuntimeError('review unavailable')
        if decision == 'idle':
            return 'idle'
        state['pending'] = True
        if decision == 'foreground':
            with worker.foreground():
                pass
        elif decision == 'capacity':
            state['capacity'] = False
        elif decision == 'stop':
            worker._stop.set()
        return 'adopted'

    def explore():
        calls.append('admitted' if state['pending'] else 'existing-work-check')
        state['pending'] = False

    worker = HindsightColdPathWorker(SimpleNamespace(), idle_seconds=300,
        idle_tasks=_composed_tasks({'consider_one_private_opportunity': consider,
                                   'explore_one_interest': explore}),
        idle_capacity=lambda: state['capacity'])
    clock[0] += 301
    worker._schedule_reflection_if_idle()
    if decision == 'adopt':
        assert calls == ['consider', 'admitted']
        assert not state['pending']
    elif decision in ('idle', 'error'):
        assert calls == ['consider', 'existing-work-check']
    else:
        assert calls == ['consider']
        assert state['pending']
