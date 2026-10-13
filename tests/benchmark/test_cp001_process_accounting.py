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


def agentzero_parent(tmp_path, name):
    directory = tmp_path / name
    directory.mkdir(mode=0o700)
    book = ProviderSampleLedger(name, {"providerAttempts": 2,
        "providerInputTokens": 24000, "providerOutputTokens": 4096},
        {"contact": ("gpt-6-sol", "none"), "helper": ("gpt-6-astra", "medium")})
    return ParentProcessAccounting(book, name, directory / "control.sock",
        workers={"agent_zero": {"contact", "helper"}}).start()


def agentzero_peer(parent, *, deadline=None):
    from p1_agentzero_bridge_probe import AgentZeroParentLedger
    return AgentZeroParentLedger(parent.request_id, {**parent.credentials("agent_zero"),
        "monotonic_deadline": time.monotonic() + 10 if deadline is None else deadline})


def test_agentzero_contact_helpers_wait_for_shared_parent_admission(tmp_path):
    parent = agentzero_parent(tmp_path, "agentzero-shared")
    peer = agentzero_peer(parent)
    dispatched = []
    try:
        for role, model, effort in [("foreground_contact", "gpt-6-sol", "none"),
                                    ("helper", "gpt-6-astra", "medium")]:
            attempt = peer.begin(role, model, effort)
            # This boundary represents the SDK dispatch; parent persisted it first.
            assert parent.report()["ledger"]["provider_attempts"] == len(dispatched) + 1
            assert parent.ledger.attempts[attempt]["role"] == ("contact" if role == "foreground_contact" else "helper")
            dispatched.append(attempt)
            peer.finish(attempt, {"input_tokens": 17, "output_tokens": 5, "total_tokens": 22})
        from cp001_accounting import SampleStopped
        with pytest.raises(SampleStopped):
            peer.begin("helper", "gpt-6-astra", "medium")
        assert len(dispatched) == 2
        mirror = peer.report()
        assert mirror["complete"] and mirror["total_usage"]["total_tokens"] == 44
        assert mirror["parent_accounting_bound"] and not mirror["authoritative_local_registry"]
        assert parent.report()["ledger"]["total_usage"] == mirror["total_usage"]
        assert peer.close(settled=True)
    finally:
        parent.close()


def test_agentzero_failed_sdk_usage_is_unknown_in_parent(tmp_path):
    parent = agentzero_parent(tmp_path, "agentzero-failure")
    peer = agentzero_peer(parent)
    try:
        attempt = peer.begin("helper", "gpt-6-astra", "medium")
        peer.incomplete(attempt, "sdk_failed_usage_unknown")
        report = parent.report()["ledger"]
        assert report["provider_attempts"] == 1 and not report["complete"]
        assert report["attempts"][0]["usage"] is None
        assert all(value is None for value in report["total_usage"].values())
        assert not peer.report()["complete"]
        assert peer.close(settled=True)
    finally:
        parent.close()


def test_agentzero_original_deadline_refuses_before_dispatch(tmp_path):
    parent = agentzero_parent(tmp_path, "agentzero-expired")
    peer = agentzero_peer(parent, deadline=time.monotonic() - 1)
    try:
        from cp001_accounting import SampleStopped
        with pytest.raises(SampleStopped):
            peer.begin("foreground_contact", "gpt-6-sol", "none")
        assert parent.report()["ledger"]["provider_attempts"] == 0
        assert peer.close(settled=True)
    finally:
        parent.close()


def test_agentzero_final_parent_guard_precedes_private_input_read(monkeypatch, tmp_path):
    from contextlib import ExitStack
    from p1_agentzero_bridge_probe import _main
    monkeypatch.setattr(sys, "argv", ["agentzero-probe", "--source", "unused", "--output", str(tmp_path / "unused"),
        "--auth-store", "unused", "--dockerized=1", "--case", "native-loop", "--bind-stream",
        "--embedding-cache", "unused", "--provider-mode", "subscription",
        "--final-cm-inputs", "not-created-private-input", "--final-cm-admission", "not-created-admission"])
    with ExitStack() as lifecycle, pytest.raises(SystemExit) as result:
        _main(lifecycle)
    assert result.value.code == 2
    assert not (tmp_path / "unused").exists()


def hermes_parent_peer(tmp_path, name, *, attempts=2, deadline=None):
    from cp001_process_accounting import ProcessSampleLedger
    from p1_hermes_startup_probe import HermesSDKAccounting
    directory = tmp_path / name; directory.mkdir(mode=0o700)
    book = ProviderSampleLedger(name, {"providerAttempts": attempts,
        "providerInputTokens": 24000, "providerOutputTokens": 4096},
        {"contact": ("gpt-6-sol", "none"), "helper": ("gpt-6-astra", "medium")})
    parent = ParentProcessAccounting(book, name, directory / "control.sock",
        workers={"hermes": {"contact", "helper"}}).start()
    peer = ProcessSampleLedger(**parent.credentials("hermes"))
    observer = HermesSDKAccounting(peer, time.monotonic()+10 if deadline is None else deadline)
    return parent, peer, observer


def hermes_fake_request(model="gpt-6-astra", effort="medium"):
    from types import SimpleNamespace
    return SimpleNamespace(method="POST", url=SimpleNamespace(host="chatgpt.com", path="/backend-api/codex/responses"),
        content=json.dumps({"model": model,"reasoning":{"effort":effort}}).encode(), extensions={})


def test_hermes_parent_precedes_contact_and_nested_helper_sdk_dispatch(tmp_path):
    from types import SimpleNamespace
    from cp001_accounting import SampleStopped
    parent, peer, observer = hermes_parent_peer(tmp_path,"hermes-sdk")
    calls=[]
    def wire(**kwargs):
        assert parent.ledger.report()["provider_attempts"] == len(calls)+1
        assert kwargs["model"] in {"gpt-6-sol","gpt-6-astra"}
        observer.guard_http(hermes_fake_request(kwargs["model"],kwargs["reasoning"]["effort"]))
        calls.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=17,output_tokens=5,total_tokens=22))
    try:
        token=observer.role.set("contact")
        observer.sync(wire,(),{"model":"discarded","reasoning":{"effort":"medium"}})
        observer.role.reset(token)
        # Native helper factories may wrap Responses through a compatibility resource.
        observer.sync(lambda **kwargs: observer.sync(wire,(),kwargs),(),{"model":"discarded"})
        assert len(calls)==2 and calls[0]["reasoning"]["effort"]=="none"
        assert calls[1]["model"]=="gpt-6-astra" and calls[1]["reasoning"]["effort"]=="medium"
        with pytest.raises(SampleStopped):observer.sync(wire,(),{})
        assert parent.ledger.report()["total_usage"]["total_tokens"]==44
        assert len(observer.wire_dispatches)==2 and peer.close(settled=True)
    finally:parent.close()


def test_hermes_helper_stream_failure_records_class_before_unknown_stop(tmp_path):
    from cp001_accounting import SampleStopped
    parent, peer, observer=hermes_parent_peer(tmp_path,"hermes-helper-fail")
    class NativeFailure(Exception):
        status_code=502;code="upstream_failure"
    class FailedStream:
        def __iter__(self):raise NativeFailure("private body must not be copied")
        def close(self):pass
    try:
        stream=observer.sync(lambda **kwargs:FailedStream(),(),{"stream":True})
        with pytest.raises(NativeFailure):list(stream)
        row=observer.calls[0]
        assert row["error"]=={"error_class":"NativeFailure","http_status":502,"error_code":"upstream_failure"}
        assert "private body" not in json.dumps(row)
        report=parent.ledger.report()
        assert report["provider_attempts"]==1 and report["attempts"][0]["usage"] is None and not report["complete"]
        # Native explicit retry still reaches the parent guard, and is refused after UNKNOWN.
        with pytest.raises(SampleStopped):observer.sync(lambda **kwargs:None,(),{})
        assert not observer.streams and peer.close(settled=True)
    finally:parent.close()


def test_hermes_sdk_retry_cannot_reuse_one_parent_admission(tmp_path):
    parent,peer,observer=hermes_parent_peer(tmp_path,"hermes-sdk-retry")
    def native(**kwargs):
        observer.guard_http(hermes_fake_request())
        observer.guard_http(hermes_fake_request())
    try:
        with pytest.raises(ValueError):observer.sync(native,(),{})
        assert len(observer.wire_dispatches)==1
        assert parent.ledger.report()["provider_attempts"]==1
        assert not parent.ledger.report()["complete"] and peer.close(settled=True)
    finally:parent.close()


@pytest.mark.parametrize("late",[False,True])
def test_hermes_original_deadline_and_stop_guard_before_http(tmp_path,late):
    from cp001_accounting import SampleStopped
    parent,peer,observer=hermes_parent_peer(tmp_path,"hermes-deadline"+str(late))
    dispatched=[]
    def native(**kwargs):
        if late:parent.stop("fixture stop after SDK reservation")
        else:observer.deadline=time.monotonic()-1
        wait_until(lambda: bool(peer.stopped)) if late else None
        observer.guard_http(hermes_fake_request());dispatched.append(True)
    try:
        with pytest.raises(SampleStopped):observer.sync(native,(),{})
        assert not dispatched and not observer.wire_dispatches
        assert parent.ledger.report()["attempts"][0]["usage"] is None
        assert peer.close(settled=True)
    finally:parent.close()


def test_hermes_async_stream_raw_usage_and_parent_close(tmp_path):
    import asyncio
    from types import SimpleNamespace
    parent,peer,observer=hermes_parent_peer(tmp_path,"hermes-async")
    class Stream:
        async def __aiter__(self):
            yield SimpleNamespace(type="response.output_text.delta")
            yield SimpleNamespace(response=SimpleNamespace(usage=SimpleNamespace(input_tokens=17,output_tokens=5,total_tokens=22)))
        async def close(self):pass
    async def run():
        async def native(**kwargs):
            observer.guard_http(hermes_fake_request());return Stream()
        stream=await observer.asynchronous(native,(),{"stream":True})
        async for event in stream:pass
    try:
        asyncio.run(run())
        assert observer.calls[0]["first_content_monotonic"]>=observer.calls[0]["started_monotonic"]
        assert parent.ledger.report()["complete"] and not observer.streams
        assert peer.report()["active_async_attempts"]==0 and peer.close(settled=True)
    finally:parent.close()


def test_hermes_input_guard_does_not_decode_without_parent_deadline(tmp_path):
    from argparse import Namespace
    from p1_hermes_startup_probe import run_native_cm
    args=Namespace(source=tmp_path,owned_profile=tmp_path,profile=tmp_path,output=tmp_path/"not-created",
        parent_accounting_control=None,absolute_deadline_file=None,sample_id=None)
    with pytest.raises(ValueError):run_native_cm(args)
    assert not args.output.exists()


def test_hermes_raw_sdk_usage_survives_parent_settlement_rejection(tmp_path):
    from types import SimpleNamespace
    from cp001_accounting import SampleStopped
    parent,peer,observer=hermes_parent_peer(tmp_path,"h-reject")
    try:
        # Actual parent rejects this usage total after the native response arrived.
        with pytest.raises(SampleStopped):
            observer.sync(lambda **kwargs:SimpleNamespace(usage=SimpleNamespace(
                input_tokens=30000,output_tokens=5,total_tokens=30005)),(),{})
        assert observer.calls[0]["usage"]=={"input_tokens":30000,"output_tokens":5,"total_tokens":30005}
        assert observer.calls[0]["status"]=="parent_settlement_failed"
        assert peer.close(settled=True)
    finally:parent.close()


def test_hermes_exact_http_context_private_retention_before_dispatch(tmp_path):
    import hashlib
    import stat
    from types import SimpleNamespace
    from cp001_accounting import SampleStopped
    parent, peer, observer = hermes_parent_peer(tmp_path, "hermes-context", attempts=3)
    output = tmp_path / "private-context"; output.mkdir(mode=0o700)
    observer.output = output / "sdk-attempts-technical.json"
    marker = "PUBLIC FIXTURE MEMORY — no protected evaluation input"
    raw = json.dumps({"model": "gpt-6-astra", "reasoning": {"effort": "medium"},
        "input": [{"role": "user", "content": marker}]}, ensure_ascii=False, indent=1).encode()
    request = hermes_fake_request()
    request.content = raw
    request.headers = {"Authorization": "Bearer public-fixture-header"}
    observer.current_turn = {"ordinal": 2, "session_index": 1, "calendar_generation": 1}
    def native(**kwargs):
        observer.guard_http(request)
        # Verified at the real HTTP boundary, before a (fixture) transport result.
        capture = output / "model-context-0000.private.json"
        assert capture.read_bytes() == raw
        assert stat.S_IMODE(capture.stat().st_mode) == 0o600
        row = observer.calls[-1]["model_context"]
        assert row == {"file": capture.name, "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw), "turn": observer.current_turn}
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=17, output_tokens=5, total_tokens=22))
    try:
        observer.sync(native, (), {})
        assert marker not in observer.output.read_text()
        assert "public-fixture-header" not in observer.output.read_text()
        assert b"public-fixture-header" not in (output / "model-context-0000.private.json").read_bytes()
        # A retained file is never overwritten by a fresh HTTP attempt.
        occupied = output / "model-context-0001.private.json"
        occupied.write_bytes(b"preserved")
        with pytest.raises(FileExistsError): observer.sync(native, (), {})
        assert occupied.read_bytes() == b"preserved"
        assert len(observer.wire_dispatches) == 1
        assert peer.close(settled=True)
    finally:
        parent.close()
