"""Real subprocess IPC: shared admission, stop, disconnect and ownership."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

BENCH = Path(__file__).resolve().parents[2] / "benchmark/inspect_ai_v1"
sys.path.insert(0, str(BENCH))
from cp001_accounting import ProviderSampleLedger
from cp001_process_accounting import ParentProcessAccounting

CHILD = r"""
import asyncio, json, os, sys
from cp001_process_accounting import ProcessSampleLedger
from cp001_accounting import SampleStopped
config = json.loads(sys.stdin.readline())
try:
    book = ProcessSampleLedger(**config['credentials'])
except (Exception, SampleStopped) as error:
    print(json.dumps({'identity_error': type(error).__name__}), flush=True)
    raise SystemExit(0)
async def run():
    try:
        attempt = book.begin(config['role'], 'model', 'none')
        book.track_async_attempt(attempt)
        print(json.dumps({'begun': attempt}), flush=True)
        if config['mode'] == 'eof':
            os._exit(0)
        if config['mode'] == 'failed-close':
            with book.lock:
                book.stopped = 'fixture response close failed'
            print(json.dumps({'local': book.report()}), flush=True)
            book.close(settled=False)
            return
        if config['mode'] == 'hold':
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                book.incomplete(attempt, 'aborted')
        else:
            book.finish(attempt, {'input_tokens': 17, 'output_tokens': 5, 'total_tokens': 22})
    except SampleStopped:
        print(json.dumps({'refused': True}), flush=True)
    print(json.dumps({'local': book.report()}), flush=True)
    book.close(settled=True)
asyncio.run(run())
"""


def make_parent(tmp_path, name, workers):
    directory = tmp_path / name
    directory.mkdir(mode=0o700)
    ledger = ProviderSampleLedger(name, {'providerAttempts': 2, 'providerInputTokens': 24000,
        'providerOutputTokens': 4096}, {'contact': ('model', 'none'), 'helper': ('model', 'none')})
    return ParentProcessAccounting(ledger, 'request', directory / 'control.sock', workers=workers).start()


def launch(parent, namespace, *, mode='normal', role='contact', wrong_identity=False):
    credentials = parent.credentials(namespace)
    if wrong_identity:
        credentials['request_id'] = 'other-request'
    proc = subprocess.Popen([sys.executable, '-c', CHILD], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={'PATH': os.defpath, 'PYTHONPATH': str(BENCH)})
    proc.stdin.write(json.dumps({'credentials': credentials, 'mode': mode, 'role': role}) + '\n')
    proc.stdin.flush()
    return proc


def receive(proc):
    assert select.select([proc.stdout], [], [], 10)[0], 'child IPC deadline'
    line = proc.stdout.readline()
    assert line, proc.stderr.read()
    return json.loads(line)


def finish(proc):
    output, errors = proc.communicate(timeout=10)
    assert proc.returncode == 0, errors
    return [json.loads(line) for line in output.splitlines()]


def wait_until(predicate):
    until = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < until, 'accounting settlement deadline'
        time.sleep(0.01)


def test_actual_processes_share_two_attempts_and_one_usage_total(tmp_path):
    parent = make_parent(tmp_path, 'shared', {'core': {'contact'}, 'memory': {'helper'}, 'retry': {'helper'}})
    try:
        for namespace, role in [('core', 'contact'), ('memory', 'helper')]:
            proc = launch(parent, namespace, role=role)
            assert receive(proc)['begun'] in (0, 1)
            finish(proc)
        proc = launch(parent, 'retry', role='helper')
        assert receive(proc) == {'refused': True}
        finish(proc)
        wait_until(lambda: all(not w['connected'] for w in parent.report()['workers'].values()))
        result = parent.report()
        assert result['ledger']['provider_attempts'] == 2
        assert result['ledger']['total_usage'] == {'input_tokens': 34, 'output_tokens': 10, 'total_tokens': 44}
        assert all(w['terminal']['tracked_http_settled'] for w in result['workers'].values())
        assert all(w['unsettled_events'] == 0 for w in result['workers'].values())
    finally:
        parent.close()


def test_stop_cancels_both_owned_processes_but_not_another_sample(tmp_path):
    parent = make_parent(tmp_path, 'stop', {'core': {'contact'}, 'memory': {'helper'}})
    other = make_parent(tmp_path, 'other', {'core': {'contact'}})
    processes = []
    try:
        for namespace, role in [('core', 'contact'), ('memory', 'helper')]:
            proc = launch(parent, namespace, mode='hold', role=role)
            processes.append(proc)
            assert 'begun' in receive(proc)
        isolated = launch(other, 'core', mode='hold')
        processes.append(isolated)
        assert 'begun' in receive(isolated)
        parent.stop()
        for proc in processes[:2]:
            values = finish(proc)
            local = next(v['local'] for v in values if 'local' in v)
            assert local['active_async_attempts'] == 0 and local['stop_requested']
        assert isolated.poll() is None
        assert other.ledger.attempts[0]['status'] == 'pending'
        assert not other.ledger.stopped
        other.stop()
        finish(isolated)
        wait_until(lambda: all(not w['connected'] for w in parent.report()['workers'].values()))
        assert all(row['status'] == 'aborted' for row in parent.ledger.attempts)
        assert parent.ledger.report()['total_usage']['input_tokens'] is None
        assert all(w['terminal']['tracked_http_settled'] for w in parent.report()['workers'].values())
    finally:
        parent.close()
        other.close()
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


@pytest.mark.parametrize('failure', ['identity', 'role', 'eof'])
def test_mismatch_and_eof_never_fabricate_settlement(tmp_path, failure):
    parent = make_parent(tmp_path, failure, {'core': {'contact'}})
    proc = None
    try:
        proc = launch(parent, 'core', mode='eof' if failure == 'eof' else 'normal',
            role='helper' if failure == 'role' else 'contact', wrong_identity=failure == 'identity')
        if failure == 'eof':
            assert 'begun' in receive(proc)
        finish(proc)
        wait_until(lambda: bool(parent.report()['errors']))
        result = parent.report()
        assert not result['ledger']['complete']
        assert result['workers']['core']['terminal'] is None
        if failure == 'eof':
            assert result['ledger']['attempts'][0]['status'] == 'process_eof_usage_unknown'
            assert result['workers']['core']['unsettled_events'] == 1
        else:
            assert result['ledger']['provider_attempts'] == 0
    finally:
        parent.close()
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()


def test_direct_stopped_assignment_reports_unsettled_close_without_deadlock(tmp_path):
    parent = make_parent(tmp_path, 'failed-close', {'core': {'contact'}})
    try:
        proc = launch(parent, 'core', mode='failed-close')
        assert 'begun' in receive(proc)
        finish(proc)
        wait_until(lambda: parent.report()['workers']['core']['terminal'] is not None)
        worker = parent.report()['workers']['core']
        assert not worker['terminal']['tracked_http_settled']
        assert worker['unsettled_events'] == 1
        assert not parent.ledger.report()['complete']
        assert parent.ledger.stopped
    finally:
        parent.close()


def test_stop_arrives_before_reserved_permit_returns_no_dispatchable_attempt(tmp_path, monkeypatch):
    import cp001_process_accounting as ipc
    parent = make_parent(tmp_path, 'late-permit', {'core': {'contact'}})
    original_write = ipc._write
    stopped = []

    def stop_before_permit(sock, lock, value):
        if value.get('decision', {}).get('allow') and not stopped:
            stopped.append(True)
            # Real wire order: child reader handles stop before the reserved permit.
            # The ledger already owns the attempt; no provider has dispatched.
            parent.stop('stop before permit delivery')
        return original_write(sock, lock, value)

    monkeypatch.setattr(ipc, '_write', stop_before_permit)
    proc = None
    try:
        proc = launch(parent, 'core')
        assert receive(proc) == {'refused': True}  # No 'begun'/native dispatch path.
        remaining = finish(proc)
        assert not any('begun' in value for value in remaining)
        # Read the bound terminal receipt rather than mixing buffered readline
        # with communicate() for a second fast stdout diagnostic line.
        wait_until(lambda: not parent.report()['workers']['core']['connected'])
        report = parent.report()
        assert len(report['ledger']['attempts']) == 1
        assert report['ledger']['attempts'][0]['status'] == 'aborted'
        assert report['ledger']['attempts'][0]['usage'] is None
        assert report['workers']['core']['unsettled_events'] == 0
        assert report['workers']['core']['terminal']['tracked_http_settled']
        assert report['workers']['core']['terminal']['active_tasks'] == 0
    finally:
        parent.close()
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
