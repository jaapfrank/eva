"""Preserve SDK failure origin before exception-group unwrapping loses it."""
import json
import logging
from eva.adapters.model.openai_codex_provider import _sanitized, _log_provider_failure_origin


def test_origin_survives_group_context_replacement(caplog):
    secret = "private_response_body_and_token"
    with caplog.at_level(logging.WARNING):
        try:
            try:
                raise ValueError(secret)
            except ValueError as original:
                raise _sanitized(original) from None
        except Exception as sanitized:
            try:
                raise ExceptionGroup(secret, [sanitized])
            except ExceptionGroup as group:
                try:
                    raise group.exceptions[0]
                except Exception as exposed:
                    assert isinstance(exposed.__context__, ExceptionGroup)
                    assert exposed.category == "protocol"
    record = json.loads(caplog.records[0].message.split(" ", 1)[1])
    assert record[0]["type"] == "ValueError"
    assert record[0]["locations"][-1]["function"] == "test_origin_survives_group_context_replacement"
    assert secret not in caplog.text


def test_origin_is_bounded_and_cycle_safe(caplog):
    error = ValueError("private")
    error.__context__ = error
    with caplog.at_level(logging.WARNING):
        _log_provider_failure_origin(ExceptionGroup("private", [error] + [TypeError("private") for _ in range(100)]))
    record = json.loads(caplog.records[-1].message.split(" ", 1)[1])
    assert len(record) <= 16
    assert sum(x["type"] == "ValueError" for x in record) == 1
    assert "private" not in caplog.text
