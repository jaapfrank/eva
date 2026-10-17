from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eva.application.conversation import ConversationConflict
from eva.application.orchestration import (
    ToolDispatchResult, ToolProofObservation, ToolRequest, VerifiedToolOrchestrator,
)
from eva.application.schema_validation import validate_schema_value


@pytest.mark.parametrize("sealed,value", [
    (True, 1), (1, True), (False, 0), (0, False),
    ({"items": [True]}, {"items": [1]}),
    ([{"enabled": False}], [{"enabled": 0}]),
])
@pytest.mark.parametrize("keyword", ["const", "enum"])
@pytest.mark.parametrize("provider_schema", [False, True])
def test_closed_schema_preserves_json_types_in_sealed_values(sealed, value, keyword, provider_schema):
    # Python equality would accept these values, including the nested cases.
    assert sealed == value
    schema = {"type": "boolean" if isinstance(value, bool) else
              "integer" if isinstance(value, int) else
              "object" if isinstance(value, dict) else "array",
              keyword: [sealed] if keyword == "enum" else sealed}
    with pytest.raises(ConversationConflict, match="sealed value|admitted values"):
        validate_schema_value(value, schema, "arguments", provider_schema=provider_schema)


@pytest.mark.parametrize("keyword", ["const", "enum"])
def test_closed_schema_canonical_object_order_and_enum_alternatives(keyword):
    sealed = {"enabled": True, "items": [1, 2]}
    value = {"items": [1, 2], "enabled": True}
    schema = {"type": "object", "properties": {
        "enabled": {"type": "boolean"},
        "items": {"type": "array", "items": {"type": "integer"}},
    }, "required": ["enabled", "items"], "additionalProperties": False,
        keyword: [{"enabled": 1}, sealed] if keyword == "enum" else sealed}
    validate_schema_value(value, schema, "arguments")


@pytest.mark.parametrize("keyword,sealed", [("enum", []), ("enum", "bad"), ("const", object())])
def test_invalid_sealed_values_keep_conversation_conflict(keyword, sealed):
    with pytest.raises(ConversationConflict):
        validate_schema_value(True, {"type": "boolean", keyword: sealed}, "arguments")


class _Timeline:
    def __init__(self):
        self.state = SimpleNamespace(thread_id="thread:typed", checkpoint_id="checkpoint:ingress",
            operation_id=None, phase="admitted", status="active", retry_count=0,
            last_error_fingerprint=None, deadline=None, revision=1)
        self.recorded = []

    def load(self, thread_id):
        assert thread_id == self.state.thread_id
        return self.state

    def transition(self, **values):
        assert values["expected_revision"] == self.state.revision
        updated = vars(self.state) | {key: values[key] for key in vars(self.state) if key in values}
        updated["revision"] += 1
        self.state = SimpleNamespace(**updated)
        self.recorded.append(values["event_type"])
        return self.state


@pytest.mark.parametrize("expected,observed,status", [
    ({"ready": True}, {"ready": 1}, "failed"),
    ({"ready": 1}, {"ready": True}, "failed"),
    ({"items": [{"ready": False}]}, {"items": [{"ready": 0}]}, "failed"),
    ({"items": [1, True], "ready": False}, {"ready": False, "items": [1, True]}, "verified"),
])
def test_provider_verified_proof_still_requires_typed_canonical_equality(expected, observed, status):
    class Tool:
        async def execute(self, request):
            return ToolDispatchResult(True, "synthetic:typed", {})

        async def observe(self, request, dispatch):
            return ToolProofObservation("verified", observed, {"source": "synthetic-readback"})

    timeline = _Timeline()
    request = ToolRequest("thread:typed", "operation:typed", "checkpoint:typed", "synthetic.tool",
        {}, expected, verification_timeout_seconds=1)
    result = asyncio.run(VerifiedToolOrchestrator(timeline, Tool(),
        clock=lambda: datetime(2026, 10, 17, tzinfo=UTC)).run(request))
    assert result.status == status
    assert timeline.recorded[-1] == ("STATE_VERIFIED" if status == "verified"
        else "STATE_VERIFICATION_CONFLICT")
    assert timeline.state.status == ("completed" if status == "verified" else "failed")
