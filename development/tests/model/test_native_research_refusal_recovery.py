"""A finite audited refusal is context; unknown/altered effects remain fenced."""
from types import SimpleNamespace
import copy
import pytest
from eva.adapters.model import research_tools as module
from eva.adapters.connectors.delegated_completion import recoverable_completion_context, completed_tool_evidence
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
from eva.contracts.canonical_json import canonical_sha256


def events():
    return [SimpleNamespace(event_type=kind, payload={"data": {"slot": i}},
        payload_sha256=canonical_sha256({"data": {"slot": i}}))
        for i, kind in enumerate(("TOOL_REQUEST_ADMITTED", "TOOL_PREPARED", "RECOVERY_FAILED"))]


def test_only_exact_independently_certified_native_history_can_recover(monkeypatch):
    monkeypatch.setattr(module, "require_initiative_context", lambda _: object())
    history = events()
    fixture_hash = canonical_sha256([{"type": x.event_type, "payload_sha256": x.payload_sha256} for x in history])
    # Use synthetic payloads instead of publishing the private historical admission.
    monkeypatch.setattr(module, "_CERTIFIED_RESEARCH_REFUSAL_SHA256", fixture_hash)
    tool = module.NativeResearchTool(object(), lambda x:x)
    assert tool.recoverable_pre_dispatch_refusal(None, history)["evidence"]["provider_execution"] == "not_started"
    changed = copy.deepcopy(history)
    changed[-1].payload["data"]["slot"] = 7
    assert tool.recoverable_pre_dispatch_refusal(None, changed) is None
    changed[-1].payload_sha256 = canonical_sha256(changed[-1].payload)
    assert tool.recoverable_pre_dispatch_refusal(None, changed) is None
    assert tool.recoverable_pre_dispatch_refusal(None, history[:-1]) is None
    changed = copy.deepcopy(history); changed[-1].event_type = "TOOL_EXECUTED"
    assert tool.recoverable_pre_dispatch_refusal(None, changed) is None
    monkeypatch.setattr(module, "require_initiative_context", lambda _:None)
    assert tool.recoverable_pre_dispatch_refusal(None, history) is None


@pytest.mark.parametrize("alter", [None, "certificate", "execution", "action", "proof"])
def test_refusal_never_counts_as_completed_work_and_unknown_effect_cannot_continue(alter):
    evidence={"source":"native_research_certified_pre_dispatch_refusal",
        "certificate_sha256":"f9707883bf69747df06f1b42fd24fdf4d07e16b87f103108f7e054a75f3256b1",
        "provider_execution":"not_started"}
    output={"status":"failed","recoverable_pre_dispatch_refusal":True,"proof":{"evidence":evidence}}
    name="research_current_options"
    if alter == "certificate": evidence["certificate_sha256"]="0"*64
    if alter == "execution": evidence["provider_execution"]="unknown"
    if alter == "action": name="use_connector_tool"
    if alter == "proof": output["proof"]=None
    receipt=ConversationActionReceipt(ConversationActionCall("old", name, {}),output)
    assert recoverable_completion_context(receipt) is (alter is None)
    assert not completed_tool_evidence([receipt])
    verified=ConversationActionReceipt(ConversationActionCall("new", "research_current_options", {}),{"status":"verified"})
    assert completed_tool_evidence([receipt,verified]) is (alter is None)
