"""Exact retained failure context on disposable PostgreSQL; no live dispatch."""
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
from uuid import uuid4

import pytest
import sqlalchemy as sa

from eva.adapters.postgres import initiative_failure_context as failure
from eva.adapters.postgres.current_catalog import delegated_work_runs
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.delegated_work import DelegatedWorkUsage
from tests.postgres.test_native_initiative_digital_work import native_digital, admitted, started
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_initiative_source_outcomes import _read


def fail(f, *, report=None, read=False):
    _, work = admitted(f)
    call = started(f, work)
    if read:
        _read(f, work)
    usage = DelegatedWorkUsage(contract="DelegatedWorkUsage/v1", schema_version=1,
        measurement="partial", completed_model_responses=1, tool_calls=1,
        input_tokens=100, output_tokens=20, total_tokens=120, cost_usd_micros=None)
    saved = f.store.record_failure(call, usage=usage, error_code="budget_exceeded",
        error_fingerprint="f" * 64, ambiguous=False, now=NOW, failure_report=report)
    return saved


def events(f):
    with f.store._engine.begin() as conn:
        # This administrative test connection reads only this disposable fixture.
        return tuple(conn.execute(sa.select(operational_events.c.thread_id,
            operational_events.c.sequence, operational_events.c.payload_sha256).order_by(
            operational_events.c.thread_id, operational_events.c.sequence)).all())


@pytest.mark.parametrize("report", [None, "Retained worker failure.\n" + "é" * 2000], ids=["missing", "unicode_report"])
def test_real_failure_context_preserves_unknowns_reports_and_original_events(native_digital, monkeypatch, report):
    f = native_digital
    work = fail(f, report=report)
    before = events(f)
    monkeypatch.setattr(PostgresExternalConversationTools, "execute",
                        lambda *a, **kw: pytest.fail("context cannot dispatch"))
    actual = failure.recorded_failure(f.auth, f.claim.intent_id, now=NOW)
    assert actual["status"] == "available", actual
    assert actual["work_id"] == work.spec.work_id
    assert actual["spec_sha256"] == work.spec.sha256
    assert actual["source_ingress_sha256"] == work.spec.source_ingress_sha256
    assert actual["error_code"] == "budget_exceeded"
    assert actual["error_fingerprint"] == "f" * 64
    assert actual["usage"]["measurement"] == "partial"
    assert actual["usage"]["recorded_counts"]["input_tokens"] == 100
    assert "total_tokens" not in actual["usage"] and "total_tokens" not in actual["usage"]["recorded_counts"]
    assert actual["usage"]["unknown_usage_possible"] is True
    assert actual["usage"]["recorded_cost_usd_micros"] is None
    assert actual["verified_prefix"]["status"] == "unknown"
    assert actual["verified_prefix"]["verified_read_count"] is None
    assert actual["verified_prefix"]["total_tool_effect_count"] is None
    assert actual["automatic_retry_allowed"] is actual["is_execution_permission"] is False
    if report is None:
        assert actual["failure_report"] == {"status": "unavailable", "reason": "no_retained_failure_report"}
    else:
        value = actual["failure_report"]
        assert value["report_sha256"] == hashlib.sha256(report.encode()).hexdigest()
        assert value["excerpt_sha256"] == hashlib.sha256(value["text"].encode()).hexdigest()
        assert len(value["text"].encode()) <= 768 and value["truncated"]
    assert len(canonical_json_bytes(actual)) <= 3072
    assert events(f) == before
    assert f.auth.timeline.load(f.claim.thread_id).phase == "initiative_blocked"


def test_positive_prefix_count_is_only_exact_retained_verified_reads(native_digital):
    f = native_digital
    fail(f, read=True)
    actual = failure.recorded_failure(f.auth, f.claim.intent_id, now=NOW)
    assert actual["status"] == "available", actual
    prefix = actual["verified_prefix"]
    assert prefix["status"] == "verified_retained_reads_observed"
    assert prefix["verified_read_count"] == 1
    assert prefix["source_context_sha256"]
    assert prefix["total_tool_effect_count"] is None and not prefix["complete_effect_coverage"]


@pytest.mark.parametrize("fault", ["owner", "binding", "scope", "stop", "expired", "spec_hash", "parent_hash", "parent_spec", "parent_owner"])
def test_foreign_stopped_expired_or_changed_sources_are_unavailable(native_digital, monkeypatch, fault):
    f = native_digital
    work = fail(f)
    if fault == "owner":
        f.control._owner = uuid4()
    elif fault == "binding":
        f.control._binding = uuid4()
    elif fault == "scope":
        f.control._scope = replace(f.control._scope, installation_id=uuid4())
    elif fault == "stop":
        f.control.stop(f.claim.intent_id, now=NOW)
    elif fault in {"expired", "spec_hash"}:
        changes = {"retention_until": NOW} if fault == "expired" else {"spec_sha256": bytes.fromhex("a" * 64)}
        with f.store._engine.begin() as conn:
            conn.execute(sa.update(delegated_work_runs).where(
                delegated_work_runs.c.work_id == work.spec.work_id).values(**changes))
    else:
        original = type(f.auth.timeline).events
        def changed(self, thread):
            values = original(self, thread)
            if thread != f.claim.thread_id:
                return values
            if fault == "parent_hash":
                return (*values[:-1], replace(values[-1], payload_sha256="a" * 64))
            index = 0 if fault == "parent_owner" else -1
            event = values[index]
            data = {**event.payload["data"], **({"principal_id": str(uuid4())}
                if fault == "parent_owner" else {"spec_sha256": "a" * 64})}
            payload = {**event.payload, "data": data}
            result = list(values)
            result[index] = replace(event, payload=payload, payload_sha256=canonical_sha256(payload))
            return tuple(result)
        monkeypatch.setattr(type(f.auth.timeline), "events", changed)
    actual = failure.recorded_failure(f.auth, f.claim.intent_id, now=NOW)
    assert actual["status"] == "unavailable"
    assert "error_fingerprint" not in actual and "failure_report" not in actual


def test_live_or_waiting_work_is_not_misrepresented_as_failed_context(native_digital):
    f = native_digital
    admitted(f)
    assert failure.recorded_failure(f.auth, f.claim.intent_id, now=NOW)["status"] == "unavailable"


def test_only_private_ordinary_history_uses_optional_failure_reader(native_digital, monkeypatch):
    from eva.adapters.postgres import sensory_events as sensory
    f = native_digital
    fail(f)
    reads = []
    class FixedTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(sensory, "datetime", FixedTime)
    def read(intent, *, now):
        reads.append((intent, now))
        return failure.recorded_failure(f.auth, intent, now=now)
    kwargs = dict(tenant_id=f.claim.tenant_id, installation_id=f.claim.installation_id,
        initiative_principal_id=str(f.control._owner), initiative_authorizer=f.auth.policy,
        context_reviews_only=True)
    old = PostgresSensoryEventStore(f.control._engine, **kwargs)
    assert "recorded_failure" not in old.recent_initiatives()[0]
    current = PostgresSensoryEventStore(f.control._engine,
        **kwargs, initiative_failure_context_reader=read)
    before = events(f)
    history = current.recent_initiatives()
    assert len(reads) == 1 and reads[0] == (f.claim.intent_id, NOW)
    assert history[0]["recorded_failure"]["status"] == "available"
    assert history[0]["phase"] == "initiative_blocked"
    assert history[0]["recorded_failure"]["outcome_sha256"]
    assert events(f) == before
    with pytest.raises(ValueError, match="failure context"):
        PostgresSensoryEventStore(f.control._engine, tenant_id=f.claim.tenant_id,
            installation_id=f.claim.installation_id, initiative_principal_id=str(f.control._owner),
            initiative_failure_context_reader=read)


def test_callback_failure_is_unavailable_without_changing_history(native_digital):
    f = native_digital
    fail(f)
    def unavailable(*args, **kw):
        raise RuntimeError("current owner unavailable")
    store = PostgresSensoryEventStore(f.control._engine, tenant_id=f.claim.tenant_id,
        installation_id=f.claim.installation_id, initiative_principal_id=str(f.control._owner),
        initiative_authorizer=f.auth.policy, context_reviews_only=True,
        initiative_failure_context_reader=unavailable)
    result = store.recent_initiatives()[0]
    assert result["recorded_failure"]["status"] == "unavailable"
    assert result["phase"] == "initiative_blocked"
