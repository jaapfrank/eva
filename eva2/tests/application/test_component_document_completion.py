"""Only the latest complete component artifact satisfies document completion."""
import json
import pytest
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
from eva.bootstrap.delegated_digital_runner import _has_completed_tool_evidence
from eva.bootstrap.delegated_digital_composition import _digital_document_descriptor
from eva.application.conversation import ConversationToolDescriptor


def receipt(name, arguments, output):
    return ConversationActionReceipt(ConversationActionCall(name, name, arguments), output)


@pytest.mark.parametrize("arguments,expected", [
    ({"component_step": 3}, True), ({"component_step": 2}, False),
    ({"content": '{"complete":true}'}, True), ({"content": "unrelated"}, False),
    ({"content": '{}'}, False), ({}, False),
    ({"component_step": 3, "content": '{"complete":true}'}, False),
])
def test_only_matching_latest_result_counts(arguments, expected):
    component = receipt("prepare_own_component_release", {},
                        {"status": "verified", "component_step": 3, "operation_result": {"complete": True}})
    saved = receipt("save_workspace_document", arguments, {"status": "verified"})
    assert _has_completed_tool_evidence([component, saved]) is expected
    assert not _has_completed_tool_evidence([saved, component])
    later = receipt("prepare_own_component_release", {},
                    {"status": "verified", "component_step": 5, "operation_result": {"complete": False}})
    assert not _has_completed_tool_evidence([component, saved, later])


def test_background_descriptor_keeps_foreground_schema_unchanged():
    descriptor = ConversationToolDescriptor("save_workspace_document", "Save text", {
        "type": "object", "required": ["path", "content"], "additionalProperties": False,
        "properties": {"path": {"type": "string"}, "content": {"type": "string", "maxLength": 12000}}})
    revised = _digital_document_descriptor(descriptor)
    assert descriptor.input_schema["required"] == ["path", "content"]
    assert "component_step" not in descriptor.input_schema["properties"]
    assert revised.input_schema["required"] == ["path"]
    assert revised.input_schema["properties"]["content"]["maxLength"] == 12000
    assert revised.input_schema["properties"]["component_step"] == {"type": "integer", "minimum": 0}
