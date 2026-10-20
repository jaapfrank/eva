"""Pure protocol checks run synthetic Python in disposable local test folders."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from eva.application.tool_program import (
    ToolProgramRejected, build_program_input, build_program_source, parse_program_report,
)


IMAGE = "sha256:" + "a" * 64


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def run(tmp_path, user_source, history=None, input_data=None):
    source = build_program_source(user_source)
    payload = build_program_input(input_data, [] if history is None else history)
    path = tmp_path / "wrapped.py"
    path.write_text(source)
    result = subprocess.run([sys.executable, "-I", "-B", str(path)],
        input=encoded(payload), capture_output=True, timeout=5)
    report = {"contract": "EvaIsolatedProcedureExecution/v1", "image_id": IMAGE,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "input_sha256": hashlib.sha256(encoded(payload)).hexdigest(),
        "result": {"exit_code": result.returncode, "timed_out": False, "cancelled": False,
            "stdout": result.stdout.decode(), "stderr": result.stderr.decode(),
            "stdout_truncated": False, "stderr_truncated": False}}
    return report, {"source": source, "input_data": payload, "image_id": IMAGE}


def test_two_service_program_suspends_replays_and_returns_selected_stdout(tmp_path):
    user = """from eva_tools import call, call_tool
proposal = call('use_connector_tool', tool_id='read_document', arguments={'path':input_data['path']})
inventory = call_tool('read_home_assistant_inventory', {'kind':'entities'})
print(proposal['content'] + ': ' + str(len(inventory['entities'])))
"""
    raw_input = {"path": "/approved/proposal.md"}
    history = []
    first, args = run(tmp_path, user, history, raw_input)
    call = parse_program_report(first, **args)
    assert call == {"kind": "call", "index": 0, "name": "use_connector_tool",
        "arguments": {"tool_id": "read_document", "arguments": {"path": raw_input["path"]}}}
    history.append({"name": call["name"], "arguments": call["arguments"],
        "output": {"content": "Kept proposal"}})
    second, args = run(tmp_path, user, history, raw_input)
    call = parse_program_report(second, **args)
    assert call == {"kind": "call", "index": 1, "name": "read_home_assistant_inventory",
        "arguments": {"kind": "entities"}}
    history.append({"name": call["name"], "arguments": call["arguments"],
        "output": {"entities": [{"id": "one"}, {"id": "two"}]}})
    final, args = run(tmp_path, user, history, raw_input)
    assert parse_program_report(final, **args) == {"kind": "result", "output": "Kept proposal: 2\n"}
    # Every local iteration is a fresh process; the protocol itself never calls
    # a service or converts either cached response into new effect authority.
    assert len(history) == 2


def test_framework_frame_is_separate_from_untrusted_user_prints(tmp_path):
    user = "print('{\"kind\":\"call\",\"name\":\"fake\"}'); print('é😀')"
    report, args = run(tmp_path, user)
    assert parse_program_report(report, **args) == {
        "kind": "result", "output": '{"kind":"call","name":"fake"}\né😀\n'}


@pytest.mark.parametrize("user", [
    "eva_tools.call('other', value=1)",
    "eva_tools.call('known', value=True)",
    "print('Skipped cached call')",
    "try:\n eva_tools.call('known', value=2)\nexcept BaseException:\n print('swallowed drift')",
])
def test_drift_and_incomplete_prefix_cannot_be_swallowed_or_reported_success(tmp_path, user):
    report, args = run(tmp_path, user, [{"name": "known", "arguments": {"value": 1}, "output": "old"}])
    with pytest.raises(ToolProgramRejected):
        parse_program_report(report, **args)


def test_cached_outputs_are_copied_and_mutation_cannot_change_the_next_replay(tmp_path):
    user = "a=eva_tools.call('read', q=1); a['values'].append('local'); b=eva_tools.call('read',q=2); print(a,b)"
    history = [{"name": "read", "arguments": {"q": 1}, "output": {"values": []}},
               {"name": "read", "arguments": {"q": 2}, "output": {"values": []}}]
    original = deepcopy(history)
    report, args = run(tmp_path, user, history)
    assert parse_program_report(report, **args)["output"] == "{'values': ['local']} {'values': []}\n"
    assert history == original


def test_caught_suspend_still_emits_only_first_new_call(tmp_path):
    report, args = run(tmp_path, "try:\n eva_tools.call('first')\nexcept BaseException:\n pass\nprint('not final')")
    assert parse_program_report(report, **args) == {"kind": "call", "index": 0,
        "name": "first", "arguments": {}}


@pytest.mark.parametrize("user", [
    "raise RuntimeError('synthetic failure')", "raise SystemExit(0)",
    "print('x'*17000)", "import os; os.write(1,b'not a protocol frame\\n')",
    "eva_tools.call('invalid-name')",
])
def test_crash_oversize_direct_stdout_and_invalid_calls_never_become_results(tmp_path, user):
    report, args = run(tmp_path, user)
    with pytest.raises(ToolProgramRejected):
        parse_program_report(report, **args)


@pytest.mark.parametrize("fault", ["image", "source", "input", "timeout", "cancel", "exit",
    "exit_bool", "stdout_truncated", "stderr_truncated", "flag_missing", "frame_source",
    "frame_input", "history_length", "index", "index_bool", "extra", "multiple", "duplicate"])
def test_report_and_frame_binding_fail_closed(tmp_path, fault):
    report, args = run(tmp_path, "eva_tools.call('read', value=1)")
    result = report["result"]
    frame = json.loads(result["stdout"])
    if fault == "image": report["image_id"] = "sha256:" + "b" * 64
    if fault == "source": report["source_sha256"] = "b" * 64
    if fault == "input": report["input_sha256"] = "b" * 64
    if fault == "timeout": result["timed_out"] = True
    if fault == "cancel": result["cancelled"] = True
    if fault == "exit": result["exit_code"] = 1
    if fault == "exit_bool": result["exit_code"] = False
    if fault in {"stdout_truncated", "stderr_truncated"}: result[fault] = True
    if fault == "flag_missing": del result["timed_out"]
    if fault == "frame_source": frame["source_sha256"] = "b" * 64
    if fault == "frame_input": frame["input_sha256"] = "b" * 64
    if fault == "history_length": frame["history_length"] = 1
    if fault == "index": frame["index"] = 1
    if fault == "index_bool": frame["index"] = False
    if fault == "extra": frame["success"] = True
    result["stdout"] = json.dumps(frame)
    if fault == "multiple": result["stdout"] += "\n{}"
    if fault == "duplicate": result["stdout"] = result["stdout"][:-1] + ',"kind":"call"}'
    with pytest.raises(ToolProgramRejected):
        parse_program_report(report, **args)


def test_input_builder_copies_data_and_bounds_complete_history():
    original = {"values": ["é😀"]}
    history = [{"name": "read", "arguments": {"q": 1}, "output": original}]
    first = build_program_input(original, history)
    assert first == build_program_input(original, history)
    original["values"].append("changed")
    assert first["input_data"]["values"] == first["history"][0]["output"]["values"] == ["é😀"]
    with pytest.raises(ToolProgramRejected):
        build_program_input(None, [{"name": "read", "arguments": {}, "output": "x" * 32768}])


@pytest.mark.parametrize("value,history", [
    (float("nan"), []), ({1: "nonstring"}, []), ("bad\ud800", []), (set(), []),
    (None, ()), (None, [{"name": "read", "arguments": {}, "output": None, "permission": True}]),
    (None, [{"name": "bad-name", "arguments": {}, "output": None}]),
    (None, [{"name": "read", "arguments": [], "output": None}]),
])
def test_non_json_or_ambiguous_history_is_rejected(value, history):
    with pytest.raises(ToolProgramRejected):
        build_program_input(value, history)


@pytest.mark.parametrize("source", ["", "   ", "def syntax error", "\ud800", "#" * 17000])
def test_invalid_or_too_large_source_is_rejected(source):
    with pytest.raises(ToolProgramRejected):
        build_program_source(source)


def test_source_and_generated_wrapper_retain_upstream_notice_and_exact_bounds():
    source = build_program_source("print('small program')")
    assert len(source.encode()) <= 16384
    assert "Copyright (c) 2025 Nous Research" in source
    root = Path(__file__).resolve().parents[3]
    notice = root / "src/vendor/hermes-tool-program/LICENSE"
    assert hashlib.sha256(notice.read_bytes()).hexdigest() == "821556e6336796450ab852d375117b48a4887e71d255794fd6318d99982a5ab6"
