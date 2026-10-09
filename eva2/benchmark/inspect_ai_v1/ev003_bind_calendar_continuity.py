"""Exact existing driver seams for a future, separately admitted source assembly.

This module only transforms source text. It cannot create resources or release input.
Caller must bind all resulting source hashes in the normal source/admission tables.
"""
import hashlib
from pathlib import Path

def _once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("native calendar source seam differs")
    return source.replace(old, new, 1)

def bind_native_driver(source):
    if 'observer.restoreNativeCompletedResponses(' in source:
        raise ValueError("native calendar source already bound")
    source = _once(source, "OpenClawProcessLedger, OpenClawAccounting,", "nativeCalendarScope, OpenClawProcessLedger, OpenClawAccounting,")
    # Preserve the actual indentation of the already-reviewed native driver.
    anchor = "  const result=await agentCliCommand("
    addition = """  const continuityScope=nativeCalendarScope(entry,fs.readFileSync(get('--absolute-deadline-file')),before,sessionKey,observer.mainRequestRole);
  if(phase===0)observer.bindNativeCalendarScope(continuityScope);
  else observer.restoreNativeCompletedResponses(control.original_restart.completed_calendar,continuityScope);
"""
    return _once(source, anchor, addition + anchor)

def _helper_import(helper_path, helper_sha256):
    path = str(Path(helper_path))
    return ("\n# Source-bound future metadata helper; ordinary source admission also pins these bytes.\n"
            "_calendar_helper_path=Path(" + repr(path) + ")\n"
            "assert hashlib.sha256(_calendar_helper_path.read_bytes()).hexdigest()==" + repr(helper_sha256) + "\n"
            "_calendar_spec=importlib.util.spec_from_file_location('bound_calendar_continuity',_calendar_helper_path)\n"
            "_calendar_helper=importlib.util.module_from_spec(_calendar_spec);_calendar_spec.loader.exec_module(_calendar_helper)\n")

def bind_openclaw_parent(source, helper_path, helper_sha256):
    if "restart['completed_calendar']=" in source:
        raise ValueError("native calendar source already bound")
    source = _once(source, "def sha(p):", _helper_import(helper_path,helper_sha256) + "def sha(p):")
    anchor = "      receipt['original_interruption']['latest_tool_observation']=latest"
    addition = """      calendar_scope=_calendar_helper.native_calendar_scope(ENTRY,deadline_path.read_bytes(),
        json.loads((RUNTIME/'sample/native-person-binding-0.private.json').read_text()),
        original_packet['participants']['openclaw']['turns'][0]['session_key'],
        RUNTIME/'p1_openclaw_parent_accounting.mjs','work')
      restart['completed_calendar']=_calendar_helper.completed_calendar_snapshot(
        json.loads((RUNTIME/'sample/sdk-attempts-technical.json').read_text()),parent.report()['ledger'],calendar_scope)
"""
    source = _once(source, anchor, addition + anchor)
    source = _once(source, "def checked(admission):", "def checked(admission):\n _calendar_helper.require_private_root(HERE.parent)")
    return _once(source, "  deadline,deadline_path=persist_absolute_deadline(", "  _calendar_helper.require_private_root(HERE.parent)\n  deadline,deadline_path=persist_absolute_deadline(")

def bind_eva_parent(source, helper_path, helper_sha256):
    if '_calendar_helper.require_private_root(' in source:
        raise ValueError("native calendar source already bound")
    source = _once(source, "input_policy=json.loads(cli.input_policy.read_bytes())", _helper_import(helper_path,helper_sha256) + "input_policy=json.loads(cli.input_policy.read_bytes())")
    source = _once(source, "os.umask(0o077)", "_calendar_helper.require_private_root(input_policy['private_base'])\nos.umask(0o077)")
    return _once(source, "deadline_at,deadline_path=persist_absolute_deadline(", "_calendar_helper.require_private_root(input_policy['private_base'])\ndeadline_at,deadline_path=persist_absolute_deadline(")
