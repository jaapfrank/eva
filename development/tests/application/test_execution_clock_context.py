"""A resumed task keeps its old source time and sees the current host clock."""
from datetime import UTC, datetime, timezone, timedelta
import json
from types import SimpleNamespace
import pytest
from eva.bootstrap.delegated_digital_runner import _remaining_budget_context
from eva.application.conversation import ConversationModelUsage


def context(observed_at):
    grant = SimpleNamespace(maximum_model_runs=8, maximum_input_tokens=10000,
        maximum_output_tokens=2000, maximum_tool_calls=6)
    return _remaining_budget_context(grant, ConversationModelUsage.unmeasured(),
        calls=1, dispatches=0, remaining_wall_seconds=120, observed_at=observed_at)


def test_live_execution_clock_is_separate_from_historical_source():
    text = context(datetime(2026, 10, 18, 16, 0, tzinfo=timezone(timedelta(hours=2))))
    data, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
    assert data["execution_observed_at_utc"] == "2026-10-18T14:00:00Z"
    assert data["source_message_timestamp_is_historical"] is True
    assert data["remaining"]["wall_seconds"] == 120
    assert data["remaining"]["model_runs"] is None


def test_naive_execution_clock_is_not_presented_as_current_utc():
    with pytest.raises(ValueError, match="aware host clock"):
        context(datetime(2026, 10, 18, 14, 0))
