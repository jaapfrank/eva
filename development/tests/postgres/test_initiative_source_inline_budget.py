"""Retained source bodies use the existing whole-context budget without redispatch."""
import hashlib

import pytest

from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.initiative_source_outcomes import read_recent_source_outcomes
from eva.contracts.canonical_json import canonical_json_bytes
from tests.postgres.test_initiative_source_outcomes import (
    _read, _finish, native_digital, costless_native, admitted, started, NOW,
)


def observation(size):
    value = {"success": True, "http_status": 200,
        "measurements": [{"at": NOW.isoformat(), "value": "synthetic"}], "padding": ""}
    value["padding"] = "x" * (size - len(canonical_json_bytes(value)))
    assert len(canonical_json_bytes(value)) == size
    return value


def test_retained_3324_byte_body_and_another_read_fit_whole_context(native_digital, monkeypatch):
    f = native_digital
    _, work = admitted(f)
    call = started(f, work)
    large = observation(3324)
    timeline, thread = _read(f, work, step=0, payload=large)
    other = {"success": True, "http_status": 200, "state": "synthetic"}
    _read(f, work, step=1, payload=other)
    _finish(f, work, call)
    before = timeline.events(thread)
    monkeypatch.setattr(PostgresExternalConversationTools, "execute",
        lambda *a, **k: pytest.fail("retained context must never redispatch"))
    values = read_recent_source_outcomes(f.auth, now=NOW, work_id=work.spec.work_id)
    assert len(canonical_json_bytes(list(values))) <= 8192
    assert len(values[0]["verified_reads"]) == 2
    retained = next(v for v in values[0]["verified_reads"] if v["source"]["thread_id"] == thread)
    assert retained["observation"] == large
    assert retained["observation_bytes"] == 3324
    assert retained["observation_sha256"] == hashlib.sha256(canonical_json_bytes(large)).hexdigest()
    assert retained["status"] == "verified_original_read" and retained["historical"] is True
    assert retained["is_current_proof"] is False and "omitted_reason" not in retained
    assert values[0]["coverage"]["context_truncated"] is False
    assert timeline.events(thread) == before


@pytest.mark.parametrize("size,inline", [(4096, True), (4097, False), (10000, False)])
def test_per_read_boundary_retains_hash_and_marks_oversized_omission(native_digital, size, inline):
    f = native_digital
    _, work = admitted(f)
    call = started(f, work)
    body = observation(size)
    _read(f, work, payload=body)
    _finish(f, work, call)
    values = read_recent_source_outcomes(f.auth, now=NOW)
    assert len(canonical_json_bytes(list(values))) <= 8192
    read = values[0]["verified_reads"][0]
    assert read["observation_bytes"] == size
    assert read["observation_sha256"] == hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    if inline:
        assert read["observation"] == body
        assert "omitted_reason" not in read
    else:
        assert "observation" not in read
        assert read["omitted_reason"] == "original_observation_exceeds_inline_bound"


def test_multiple_admitted_bodies_still_use_whole_8192_byte_bound(native_digital):
    f = native_digital
    _, work = admitted(f)
    call = started(f, work)
    body = observation(3324)
    for step in range(4): _read(f, work, step=step, payload=body)
    _finish(f, work, call)
    values = read_recent_source_outcomes(f.auth, now=NOW)
    assert len(canonical_json_bytes(list(values))) <= 8192
    assert values[0]["coverage"]["context_truncated"] is True
    assert values[0]["coverage"]["omitted_reason"] == "whole_context_byte_bound"
    assert 0 < len(values[0]["verified_reads"]) < 4
    assert all(v["observation"] == body for v in values[0]["verified_reads"])
