"""Strict historical watch metadata; no source lookup or execution authority."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from eva.application.resident_attention import validate_attention_source_watch
from eva.application.sensory import SensoryAttentionSource


NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def watch():
    return {"source_event_id": "retained-source:example", "source_sha256": "a" * 64,
        "selected_at": "2026-10-20T12:00:00Z",
        "binding": SensoryAttentionSource("example", "state_changed", "entity_id",
            "sensor.example", "b" * 64, "retained changes only").to_payload()}


def validate(value, **clocks):
    return validate_attention_source_watch(value,
        decided_at=clocks.get("decided_at", NOW),
        observed_at=clocks.get("observed_at", NOW - timedelta(seconds=1)))


def test_exact_watch_is_returned_without_rewriting_historical_bytes():
    value = watch()
    before = deepcopy(value)
    assert validate(value) is value
    assert value == before


@pytest.mark.parametrize("key", tuple(watch()))
def test_watch_requires_every_existing_field(key):
    value = watch(); del value[key]
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize("path,value", [
    (("extra",), True),
    (("source_event_id",), ""), (("source_event_id",), " x"),
    (("source_event_id",), "x\n"), (("source_event_id",), "x" * 241),
    (("source_event_id",), 7),
    (("source_sha256",), "A" * 64), (("source_sha256",), "a" * 63),
    (("source_sha256",), 1),
    (("selected_at",), "2026-10-20T12:00:00+00:00"),
    (("selected_at",), "2026-10-20T12:00:00.000Z"),
    (("selected_at",), "2026-10-20T11:59:59Z"),
    (("selected_at",), "2026-10-20T12:00:01Z"),
    (("selected_at",), None), (("binding",), None),
    (("binding", "extra"), "not part of coverage"),
    (("binding", "configuration_sha256"), "B" * 64),
    (("binding", "selector_key"), "not-an-identifier"),
    (("binding", "selector_value"), ""),
    (("binding", "coverage"), "x" * 256),
])
def test_malformed_nested_watch_is_rejected(path, value):
    payload = watch()
    target = payload if len(path) == 1 else payload[path[0]]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        validate(payload)


@pytest.mark.parametrize("key", tuple(watch()["binding"]))
def test_binding_requires_exact_existing_contract(key):
    value = watch(); del value["binding"][key]
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize("clocks", [
    {"observed_at": NOW + timedelta(seconds=1)},
    {"decided_at": NOW.replace(tzinfo=None)},
    {"decided_at": NOW.replace(microsecond=1)},
    {"observed_at": "2026-10-20T12:00:00Z"},
])
def test_watch_chronology_requires_canonical_commit_clocks(clocks):
    with pytest.raises(ValueError):
        validate(watch(), **clocks)
