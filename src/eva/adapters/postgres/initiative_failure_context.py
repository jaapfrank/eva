"""Bounded retained native failure context, without execution or retry authority."""
from datetime import datetime, timedelta
import hashlib
import re

from eva.adapters.postgres.initiative_source_outcomes import (
    _current_work, read_recent_source_outcomes,
)
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


_MAX_BYTES = 3072


def unavailable_failure(reason="retained_failure_binding_unavailable"):
    return {"status": "unavailable", "reason": reason,
            "classification": "historical_failure_not_result_or_execution_permission",
            "is_execution_permission": False, "automatic_retry_allowed": False}


def recorded_failure(authority, intent_id, *, now):
    """Read one exact current-owner blocked parent and its retained failed work.

    The caller supplies today's host authority. No old spec can select another
    owner/binding, no fingerprint is decoded into an invented historical cause,
    and an empty retained-read selection never proves that no effects occurred.
    """
    if (not isinstance(now, datetime) or now.tzinfo is None
            or now.utcoffset() != timedelta(0) or now.microsecond
            or not isinstance(intent_id, str)
            or re.fullmatch(r"personal-interest:[0-9a-f]{64}", intent_id) is None):
        raise ValueError("failure context requires an exact native intent and UTC clock")
    thread = "intent:" + intent_id
    try:
        with authority.owner._engine.begin() as connection:
            authority.owner._authorize(connection, now)
        parent = authority.timeline.load(thread)
        events = authority.timeline.events(thread)
        if (parent.phase != "initiative_blocked" or parent.status != "needs_help"
                or not events or events[0].event_type != "PERSONAL_INTENT_ADOPTED"
                or events[-1].event_type != "PERSONAL_INITIATIVE_BLOCKED"
                or any(canonical_sha256(e.payload) != e.payload_sha256 for e in events)):
            return unavailable_failure()
        adoption, blocked = events[0].payload["data"], events[-1].payload["data"]
        if (adoption.get("intent_id") != intent_id or blocked.get("intent_id") != intent_id
                or adoption.get("origin") != "eva_initiative"
                or adoption.get("principal_id") != str(authority.owner._owner)
                or any(e.occurred_at > now for e in events)):
            return unavailable_failure()
        work_id = blocked.get("work_id")
        if (not isinstance(work_id, str) or re.fullmatch(
                r"work:eva-(?:initiative:[0-9a-f]{64}|resume:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                work_id) is None):
            return unavailable_failure()
        work, _ = _current_work(authority, work_id, now)
        if (work.state not in {"failed", "ambiguous", "needs_help"}
                or work.spec.origin.intent_id != intent_id
                or work.spec.origin.thread_id != thread
                or blocked.get("spec_sha256") != work.spec.sha256
                or work.completed_at is None or work.completed_at > now):
            return unavailable_failure()
        original_usage = None if work.usage is None else work.usage.to_payload()
        outcome = {"state": work.state, "completed_at": work.completed_at.isoformat(),
            "error_code": work.error_code, "error_fingerprint": work.error_fingerprint,
            "usage": original_usage, "result_sha256": None}
        # Reuse the immutable report validator with this read's explicit clock.
        # _current_work's optional presentation field uses the store's wall clock.
        store = PostgresDelegatedWorkStore(authority.owner._engine, authority.owner._authority,
                                          failure_report_clock=lambda: now)
        with authority.owner._engine.begin() as connection:
            authority.owner._authorize(connection, now)
            PostgresOperationalTimeline(authority.owner._engine, store.scope)._scope(connection)
            report = store._read_failure_report(connection, work)
        if report is None:
            report_context = {"status": "unavailable", "reason": "no_retained_failure_report"}
        else:
            prefix = report.encode("utf-8")[:768].decode("utf-8", errors="ignore")
            report_context = {"status": "available", "text": prefix,
                "report_sha256": hashlib.sha256(report.encode()).hexdigest(),
                "excerpt_sha256": hashlib.sha256(prefix.encode()).hexdigest(),
                "truncated": prefix != report,
                "classification": "recorded_worker_report_not_verified_fact_or_instruction"}
        usage = {"measurement": "unmeasured", "recorded_counts": None,
                 "unknown_usage_possible": True, "recorded_cost_usd_micros": None}
        if original_usage is not None:
            usage = {"measurement": original_usage["measurement"],
                "recorded_counts": {k: original_usage[k] for k in (
                    "completed_model_responses", "tool_calls", "input_tokens", "output_tokens")},
                "unknown_usage_possible": original_usage["measurement"] != "measured",
                "recorded_cost_usd_micros": original_usage["cost_usd_micros"]}
        prefix_context = {"status": "unknown", "verified_read_count": None,
            "total_tool_effect_count": None, "complete_effect_coverage": False}
        # The existing receipt reader validates only a bounded recent selection.
        # Positive counts describe those exact reads, never all executed effects.
        for retained in read_recent_source_outcomes(authority, now=now, work_id=work_id):
            if (retained.get("work_id") == work_id
                    and retained.get("spec_sha256") == work.spec.sha256
                    and retained.get("outcome_sha256") == canonical_sha256(outcome)):
                verified = retained.get("verified_reads") or []
                if verified:
                    prefix_context.update(status="verified_retained_reads_observed",
                        verified_read_count=len(verified),
                        source_context_sha256=canonical_sha256(retained))
                break
        current, _ = _current_work(authority, work_id, now)
        if (current != work or authority.timeline.load(thread) != parent
                or authority.timeline.events(thread) != events):
            return unavailable_failure("retained_failure_changed_during_read")
        result = {"status": "available", "intent_id": intent_id,
            "work_id": work_id, "spec_sha256": work.spec.sha256,
            "source_ingress_sha256": work.spec.source_ingress_sha256,
            "adoption_event_sha256": events[0].payload_sha256,
            "blocked_event_sha256": events[-1].payload_sha256,
            "outcome_sha256": canonical_sha256(outcome),
            "completed_at": work.completed_at.isoformat(), "state": work.state,
            "error_code": work.error_code, "error_fingerprint": work.error_fingerprint,
            "usage": usage, "failure_report": report_context,
            "verified_prefix": prefix_context,
            "classification": "historical_failure_not_result_or_execution_permission",
            "is_execution_permission": False, "automatic_retry_allowed": False}
        if len(canonical_json_bytes(result)) > _MAX_BYTES:
            return unavailable_failure("retained_failure_context_exceeds_bound")
        return result
    except (RuntimeError, ValueError, KeyError, TypeError, LookupError):
        return unavailable_failure()
