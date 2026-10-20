"""A refused optional lookup cannot fabricate or invalidate verified task work."""
from copy import deepcopy

import pytest

from eva.adapters.connectors.delegated_completion import completed_tool_evidence, recoverable_completion_context
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt


def saved():
    content = "# Public result\n\nA verified conclusion."
    return ConversationActionReceipt(ConversationActionCall("save", "save_workspace_document",
        {"path": "/proof-state/note.md", "content": content}), {
        "status": "verified", "proof": {"expected": {"content": content},
        "observed": {"content": content}, "evidence": {"source": "mcp_gateway_readback"}}})


def refused():
    return ConversationActionReceipt(ConversationActionCall("refused", "manage_connected_services",
        {"action": "status"}), {"status": "failed", "recoverable_pre_dispatch_refusal": True,
        "proof": {"evidence": {"source": "native_connector_status_pre_dispatch_refusal",
        "operation_kind": "read", "provider_execution": "not_started",
        "resume_binding": {"work_id": "work:successor", "execution_namespace": "bound-original"}}}})


def test_verified_saved_result_survives_nonexecuted_optional_status_lookup():
    receipt = refused()
    before = deepcopy(receipt.output)
    assert recoverable_completion_context(receipt)
    assert completed_tool_evidence([saved(), receipt])
    assert receipt.output == before
    assert receipt.output["status"] == "failed"


def test_refused_lookup_alone_never_completes_work():
    assert not completed_tool_evidence([refused()])


@pytest.mark.parametrize("fault", ["unbound", "unknown_execution", "missing_flag", "effect",
                                  "pause", "wrong_source", "verified_claim", "empty_binding"])
def test_unproven_or_effect_failure_still_blocks_completion(fault):
    receipt = refused()
    output = deepcopy(receipt.output)
    evidence = output["proof"]["evidence"]
    call = receipt.call
    if fault == "unbound": evidence.pop("resume_binding")
    if fault == "unknown_execution": evidence["provider_execution"] = "unknown"
    if fault == "missing_flag": output.pop("recoverable_pre_dispatch_refusal")
    if fault == "effect": evidence["operation_kind"] = "effect"
    if fault == "pause": call = ConversationActionCall("refused", call.name, {"action": "pause"})
    if fault == "wrong_source": evidence["source"] = "provider_reported_text"
    if fault == "verified_claim": output["status"] = "rejected"
    if fault == "empty_binding": evidence["resume_binding"] = {}
    assert not completed_tool_evidence([saved(), ConversationActionReceipt(call, output)])


def test_refused_lookup_cannot_hide_another_unverified_write():
    write = ConversationActionReceipt(ConversationActionCall("unknown-write", "save_workspace_document",
        {"path": "/proof-state/other.md", "content": "unconfirmed"}), {"status": "timed_out"})
    assert not completed_tool_evidence([saved(), write, refused()])
