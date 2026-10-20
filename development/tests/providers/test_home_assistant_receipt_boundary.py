"""Optional receipt interpretation preserves history without creating authority."""
from copy import deepcopy
import subprocess
import sys
from types import SimpleNamespace

import pytest

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
from eva.contracts.canonical_json import canonical_json_bytes
from eva.providers import receipt_policies


def test_general_model_route_does_not_import_any_home_assistant_module():
    # A fresh interpreter proves absence, rather than relying on cached imports.
    script = """
import importlib.abc
import sys
class NoHomeAssistant(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if (fullname.startswith('eva.providers.home_assistant')
                or fullname.startswith('eva.adapters.connectors.home_assistant')):
            raise AssertionError('optional HA import: ' + fullname)
sys.meta_path.insert(0, NoHomeAssistant())
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
from eva.providers.receipt_policies import receipt_policy, retains_action_body
receipt = ConversationActionReceipt(ConversationActionCall('other', 'other_connector', {}),
    {'status': 'verified', 'result': {'text': 'ordinary result'}}, model_output={'unused': True})
assert PydanticConversationModel._receipt_presentation(receipt) is receipt.output
assert PydanticConversationModel._checkpoint_source_read(receipt) is None
assert receipt_policy('other_connector') is None
assert retains_action_body('save_workspace_document')
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_disabled_but_installed_policy_preserves_source_reader_without_provider_import(monkeypatch):
    from tests.model.test_catalogue_model_view import _checkpoint_ha_read
    from tests.connectors.test_connector_results import ConnectorResults, TURN
    receipt, _, timeline, _, _, authorize = _checkpoint_ha_read()
    frozen = canonical_json_bytes(receipt.output)
    monkeypatch.setenv("EVA_ENABLED_PROVIDERS", "")
    monkeypatch.delenv("EVA_HOME_ASSISTANT_URL", raising=False)
    monkeypatch.delenv("EVA_HOME_ASSISTANT_TOKEN_FILE", raising=False)
    # No provider construction or configuration is involved in history reading.
    source = PydanticConversationModel._checkpoint_source_read(receipt)
    assert source["task_completion_verified"] is False
    capture = ConnectorResults(timeline, TURN, authorize)
    page = capture.read(source["reader_tool_id"], {"pointer": source["pointer"] + "/response_json",
        "offset": 0, "limit": 80, "query": None})
    assert page["text"] == receipt.output["result"]["response_json"][:80]
    assert page["external_state_verified"] is False
    assert canonical_json_bytes(receipt.output) == frozen


def test_absent_optional_policy_keeps_original_receipt_literal(monkeypatch):
    from tests.model.test_catalogue_model_view import _checkpoint_ha_read
    receipt, *_ = _checkpoint_ha_read()
    frozen = canonical_json_bytes(receipt.output)
    def absent(name):
        raise ModuleNotFoundError("optional policy absent", name=name)
    monkeypatch.setattr(receipt_policies, "import_module", absent)
    assert PydanticConversationModel._receipt_presentation(receipt) is receipt.output
    assert PydanticConversationModel._receipt_presentation(receipt, ancestor=True) is receipt.output
    assert PydanticConversationModel._receipt_arguments(receipt, ancestor=True) == receipt.call.arguments
    assert PydanticConversationModel._checkpoint_source_read(receipt) is None
    assert canonical_json_bytes(receipt.output) == frozen


def test_missing_policy_dependency_is_not_mistaken_for_uninstalled_policy(monkeypatch):
    def broken(name):
        raise ModuleNotFoundError("broken installation", name="required_dependency")
    monkeypatch.setattr(receipt_policies, "import_module", broken)
    with pytest.raises(ModuleNotFoundError, match="broken installation"):
        receipt_policies.receipt_policy("home_assistant_request")


def _rest_failure(terminal, change=None):
    turn = SimpleNamespace(conversation_id="conversation", principal_id="person", message_id="message")
    arguments = {"transport": "rest", "method": "GET", "path": "/api/states", "body_json": "",
                 "response_pointer": "", "offset": 0, "limit": 50}
    admission = {"action_name": "home_assistant_request", "action_call_id": "old-read",
        "action_arguments": arguments, "conversation_id": turn.conversation_id,
        "principal_id": turn.principal_id, "source_message_id": turn.message_id,
        "receipt_output": {"operation_kind": "invoke", "external_state_verified": False},
        "request_tool_name": "home_assistant.api.request", "request_arguments": deepcopy(arguments),
        "request_expected_proof": {}, "observation_output_key": "result",
        "request_tool_configuration_sha256": "a" * 64}
    if change == "write": admission["action_arguments"] = {**arguments, "method": "POST"}
    elif change == "body": admission["action_arguments"] = {**arguments, "body_json": "{}"}
    elif change == "tool": admission["request_tool_name"] = "other"
    elif change == "arguments": admission["request_arguments"] = {}
    elif change == "proof": admission["request_expected_proof"] = {"effect": True}
    elif change == "key": admission["observation_output_key"] = "inventory"
    elif change == "configuration": admission["request_tool_configuration_sha256"] = "bad"
    elif change == "receipt": admission["receipt_output"]["external_state_verified"] = True
    events = (SimpleNamespace(event_type="TOOL_REQUEST_ADMITTED", payload={"data": admission}),
              SimpleNamespace(event_type=terminal, payload={"data": {"error_fingerprint": "b" * 64}}))
    port = PostgresExternalConversationTools(None,
        SimpleNamespace(action_names=("home_assistant_request",)), None)
    frozen = canonical_json_bytes([event.payload for event in events])
    receipt = port._receipt(turn, events, project=False)
    assert canonical_json_bytes([event.payload for event in events]) == frozen
    return receipt


@pytest.mark.parametrize("terminal,status", [("RECOVERY_FAILED", "failed"),
    ("STATE_VERIFICATION_TIMED_OUT", "timed_out")])
def test_historical_rest_get_failure_keeps_terminal_status_and_no_success(terminal, status):
    receipt = _rest_failure(terminal)
    assert receipt.output["status"] == status
    assert receipt.output["recoverable_read_error"] is True
    assert receipt.output["external_state_verified"] is False
    assert "result" not in receipt.output
    assert receipt.output["proof"]["evidence"]["classification"] == "historical_host_admission"


@pytest.mark.parametrize("change", ["write", "body", "tool", "arguments", "proof", "key", "configuration", "receipt"])
def test_historical_rest_failure_does_not_reclassify_a_different_admission(change):
    receipt = _rest_failure("RECOVERY_FAILED", change)
    assert receipt.output["status"] == "failed"
    assert receipt.output["proof"] is None
    assert "recoverable_read_error" not in receipt.output
    assert "result" not in receipt.output


@pytest.mark.parametrize("name,arguments,key", [
    ("home_assistant_request", {"transport": "rest", "method": "GET"}, "result"),
    ("home_assistant_request", {"transport": "rest", "method": "GET", "path": "", "body_json": "{}"}, "result"),
    ("read_home_assistant_inventory", {}, "inventory"),
    ("home_assistant_request", {"transport": "rest", "method": "POST"}, None),
    ("home_assistant_request", {"transport": "websocket", "method": "GET", "body_json": '{"type":"get_states"}'}, None),
])
def test_retained_initiative_sources_keep_original_read_classification(name, arguments, key):
    assert name in receipt_policies.retained_source_action_names()
    actual, reason = receipt_policies.retained_source_observation(name, arguments)
    assert actual == key
    assert reason == ("only_retained_rest_get_or_inventory" if key is None else None)


def test_absent_optional_policy_does_not_offer_historical_source_reads(monkeypatch):
    def absent(name):
        raise ModuleNotFoundError("optional policy absent", name=name)
    monkeypatch.setattr(receipt_policies, "import_module", absent)
    assert receipt_policies.retained_source_action_names() == ()
    assert receipt_policies.retained_source_observation("home_assistant_request", {}) == (
        None, "provider_receipt_policy_unavailable")
