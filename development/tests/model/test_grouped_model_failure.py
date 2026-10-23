"""Grouped failure diagnosis preserves cause types without exposing payloads."""
import json
import logging
from eva.adapters.model.pydantic_conversation import _log_model_failure
from eva.adapters.model.openai_codex_provider import CodexProviderError, _sanitized


def read_record(caplog, error):
    with caplog.at_level(logging.WARNING):
        _log_model_failure(error, None)
    return json.loads(caplog.records[-1].message.split(" ", 1)[1])


def test_grouped_cause_has_safe_locations_without_changing_classification(caplog):
    secret = "private-token-and-tool-arguments"
    try:
        raise TypeError(secret)
    except TypeError as leaf:
        group = ExceptionGroup(secret, [ExceptionGroup(secret, [leaf])])
    error = CodexProviderError("protocol")
    error.__context__ = group
    result = read_record(caplog, error)
    assert result["exception_chain"] == ["CodexProviderError", "ExceptionGroup", "ExceptionGroup", "TypeError"]
    assert result["exception_locations"][-1]["locations"][-1]["function"] == "test_grouped_cause_has_safe_locations_without_changing_classification"
    assert secret not in json.dumps(result)
    assert result["provider_category"] == "protocol"
    assert _sanitized(error) is error


def test_grouped_diagnostics_are_bounded_and_cycle_safe(caplog):
    leaf = ValueError("private")
    leaf.__context__ = leaf
    group = ExceptionGroup("private", [leaf] + [TypeError("private") for _ in range(100)])
    result = read_record(caplog, group)
    assert len(result["exception_chain"]) <= 16
    assert result["exception_chain"].count("ValueError") == 1
    assert "private" not in json.dumps(result)
    error = ValueError("private")
    for _ in range(30):
        error = ExceptionGroup("private", [error])
    result = read_record(caplog, error)
    assert max(x["depth"] for x in result["exception_locations"]) <= 8
