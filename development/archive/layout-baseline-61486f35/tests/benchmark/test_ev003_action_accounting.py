"""Native invocation wrappers and one shared cross-process action boundary."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

BENCH = Path(__file__).resolve().parents[2] / "benchmark/inspect_ai_v1"
sys.path.insert(0, str(BENCH))
from cp001_accounting import ProviderSampleLedger, SampleStopped
from cp001_process_accounting import ParentProcessAccounting, ProcessSampleLedger
from ev003_action_accounting import ActionAccounting, ActionBudget

CONFIG = {"contract": "Ev003ActionControl/v1", "allowed_actions": ["native_tool", "protocol:structured_response"], "max_dispatches": 2}


def parent_at(tmp_path, workers, config=CONFIG):
    ledger = ProviderSampleLedger("sample", {"providerAttempts": 2, "providerInputTokens": 24000,
        "providerOutputTokens": 4096, "toolCalls": 2}, {"contact": ("model", "none")})
    return ParentProcessAccounting(ledger, "request", tmp_path / "control.sock",
                                   workers=workers, action_control=config, action_deadline_monotonic=time.monotonic()+30).start()


CHILD = r"""
import json, os, sys
from cp001_process_accounting import ProcessSampleLedger
from cp001_accounting import SampleStopped
from ev003_action_accounting import ActionAccounting
config=json.loads(sys.stdin.readline())
ledger=ProcessSampleLedger(**config['credentials'])
actions=ActionAccounting(ledger,config['actions'])
def invoke():
    print(json.dumps({'entered':True}),flush=True)
    if config.get('eof'):os._exit(0)
    return 'native result'
try:
    result=actions.call('native_tool',invoke,binding={'call_id':'call'})
    print(json.dumps({'result':result}),flush=True)
except SampleStopped:
    print(json.dumps({'refused':True}),flush=True)
finally:
    ledger.close(settled=True)
"""


def run_child(parent, namespace, *, eof=False):
    process = subprocess.Popen([sys.executable, "-c", CHILD], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={"PATH": os.defpath, "PYTHONPATH": str(BENCH)})
    output, errors = process.communicate(json.dumps({"credentials": parent.credentials(namespace),
        "actions": CONFIG, "eof": eof}) + "\n", timeout=10)
    assert process.returncode == 0, errors
    return [json.loads(line) for line in output.splitlines()]


def test_actual_foreground_and_delegated_processes_share_one_tool_cap(tmp_path):
    parent = parent_at(tmp_path, {"core": {"contact"}, "delegated": {"contact"}, "late": {"contact"}})
    try:
        for name in ("core", "delegated"):
            assert run_child(parent, name) == [{"entered": True}, {"result": "native result"}]
        assert run_child(parent, "late") == [{"refused": True}]
        report = parent.report()
        assert report["actions"]["reserved_dispatches"] == 2
        assert [r["namespace"] for r in report["actions"]["attempts"]] == ["core", "delegated"]
        assert all(r["status"] == "returned" and r["entered"] for r in report["actions"]["attempts"])
        assert report["ledger"]["provider_attempts"] == 0
        assert report["ledger"]["stop"] and report["actions"]["refused"][0]["dispatched"] is False
    finally:
        parent.close()


def test_original_method_return_exception_and_explicit_stop_are_preserved(tmp_path):
    parent = parent_at(tmp_path, {"core": {"contact"}})
    child = ProcessSampleLedger(**parent.credentials("core"))
    try:
        observer = ActionAccounting(child, CONFIG)
        expected, seen = object(), []
        class Native:
            def execute(self, turn, call, **kwargs):
                seen.append((self, turn, call, kwargs))
                if kwargs.get("fail"):
                    raise ValueError("native failure")
                return expected
        native, turn = Native(), SimpleNamespace(message_id="message", principal_id="person")
        call = SimpleNamespace(name="native_tool", call_id="call")
        original = Native.execute
        bound = observer.bind(original)
        assert bound(native, turn, call, step_index=3) is expected
        assert seen[0] == (native, turn, call, {"step_index": 3})
        with pytest.raises(ValueError, match="native failure"):
            bound(native, turn, call, fail=True)
        parent.stop("explicit stop")
        with pytest.raises(SampleStopped):
            bound(native, turn, call)
        assert len(seen) == 2 and Native.execute is original
        assert [r["status"] for r in parent.report()["actions"]["attempts"]] == ["returned", "failed"]
    finally:
        child.close(settled=True)
        parent.close()


def test_incomplete_action_and_eof_do_not_claim_effect_or_quiescence(tmp_path):
    parent = parent_at(tmp_path, {"core": {"contact"}})
    try:
        assert run_child(parent, "core", eof=True) == [{"entered": True}]
        # Join the exact child connection handler; no native/model process exists.
        for thread in parent.threads[1:]:
            thread.join(timeout=2)
        report = parent.report()
        assert report["actions"]["complete"] is False
        assert report["actions"]["attempts"][0]["status"] == "incomplete"
        assert report["actions"]["attempts"][0]["entered"] is None
        assert report["workers"]["core"]["terminal"] is None
        assert report["ledger"]["stop"]
    finally:
        parent.close()


def test_parent_refuses_config_above_original_budget_and_wrong_name(tmp_path):
    ledger = ProviderSampleLedger("sample", {"toolCalls": 2}, {})
    with pytest.raises(ValueError, match="declared sample"):
        ActionBudget(ledger, {**CONFIG, "max_dispatches": 3}, deadline_monotonic=time.monotonic()+30)
    parent = parent_at(tmp_path, {"core": {"contact"}})
    child = ProcessSampleLedger(**parent.credentials("core"))
    try:
        observed=[]
        with pytest.raises(SampleStopped):
            ActionAccounting(child, CONFIG).call("not_allowed",lambda:observed.append(True),binding={})
        assert observed == [] and parent.report()["actions"]["reserved_dispatches"] == 0
    finally:
        child.close(settled=True)
        parent.close()


def test_protocol_and_native_actions_share_total_and_wall_deadline(tmp_path):
    parent = parent_at(tmp_path, {"core": {"contact"}})
    child = ProcessSampleLedger(**parent.credentials("core"))
    try:
        accounting = ActionAccounting(child, CONFIG)
        accounting.call("protocol:structured_response", lambda: None, binding={})
        accounting.call("native_tool", lambda: None, binding={})
        report = parent.report()["actions"]
        assert report["reserved_dispatches"] == 2
        assert report["native_action_reservations"] == report["protocol_reservations"] == 1
        with pytest.raises(SampleStopped):
            accounting.call("native_tool", lambda: pytest.fail("dispatch after cap"), binding={})
    finally:
        child.close(settled=True)
        parent.close()
    deadline = [time.monotonic()-1]
    ledger = ProviderSampleLedger("expired", {"toolCalls": 2}, {})
    budget = ActionBudget(ledger, CONFIG, deadline_monotonic=lambda: deadline[0])
    assert budget.reserve("core", "native_tool", {}, budget.digest) == {"allow": False}
    assert budget.report()["reserved_dispatches"] == 0 and ledger.stopped
