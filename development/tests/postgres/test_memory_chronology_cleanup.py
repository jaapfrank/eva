"""Disposable PostgreSQL tests for E4.11 chronology isolation, classification, and safe compaction."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.memory_chronology_cleanup import (
    apply_verified_memory_cleanup,
    build_chronology_isolation_proof_sha256,
    classify_memory_remnants,
)
from eva.adapters.postgres.memory_outbox import (
    DurableMemoryRetentionJob,
    PostgresMemoryOutbox,
)
from eva.adapters.postgres.people_memory_overview import PostgresPeopleMemoryOverview
from eva.application.memory import MemoryRetentionReceipt
from eva.application.memory_audience import MemoryAudience
from eva.application.people_memory_overview import PeopleMemoryOverviewError
from eva.contracts.canonical_json import canonical_sha256
from tests.postgres.test_memory_source_lifecycle import complete_dream_exchange
from tests.postgres.test_people_memory_overview import (
    NOW,
    _document,
    _timeline,
    people_memory_engine,
    selected_private_case,
)


def _scoped_doc(tenant: str, installation: str, name: str, *, secret: str, **kwargs):
    base = _document(name)
    return replace(
        base,
        tenant_id=tenant,
        installation_id=installation,
        content=f"Gebruiker: {secret}",
        **kwargs,
    )


def test_chronology_isolation_covers_all_four_historical_defect_classes(people_memory_engine):
    tenant = "e411-four-defects-tenant"
    installation = "e411-four-defects-home"
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id=tenant, installation_id=installation)
    overview = PostgresPeopleMemoryOverview(people_memory_engine, tenant_id=tenant, installation_id=installation)
    timeline = outbox._timeline
    secret_marker = "TOP_SECRET_PRIVATE_FAMILY_NOTE_E411"

    # 1. attempt_out_of_order -> retained
    doc1 = _scoped_doc(tenant, installation, "attempt-order", secret=secret_marker + "_1")
    job1 = outbox.begin(outbox.enqueue(doc1), now=NOW + timedelta(seconds=1))
    job1 = outbox.begin(job1, now=NOW + timedelta(seconds=2))
    outbox.succeed(
        job1,
        MemoryRetentionReceipt("bank-1", doc1.document_id, hashlib.sha256(doc1.content.encode()).hexdigest()),
        now=NOW + timedelta(seconds=3),
    )

    # 2. success_out_of_order -> not_selected (direct queued -> zero selection)
    doc2 = _scoped_doc(tenant, installation, "success-order-zero", secret=secret_marker + "_2")
    job2 = outbox.enqueue(doc2)
    digest2 = hashlib.sha256(doc2.content.encode()).hexdigest()
    timeline.transition(
        thread_id=job2.state.thread_id,
        expected_revision=job2.state.revision,
        checkpoint_id=job2.state.thread_id + ":not-selected",
        operation_id=job2.state.operation_id,
        occurred_at=NOW + timedelta(seconds=2),
        data={
            "bank_id": "bank-2",
            "content_sha256": digest2,
            "document_id": doc2.document_id,
            "selected_fact_count": 0,
            "operation_id": "op-zero-2",
        },
        event_type="MEMORY_RETAIN_NOT_SELECTED",
        phase="memory_not_selected",
        status="completed",
        retry_count=0,
    )

    # 3. erased_not_terminal (erased_at precedes final completed event)
    doc3 = _scoped_doc(tenant, installation, "erased-nonterminal", secret=secret_marker + "_3")
    job3 = outbox.begin(outbox.enqueue(doc3), now=NOW + timedelta(seconds=1))
    outbox.succeed(
        job3,
        MemoryRetentionReceipt("bank-3", doc3.document_id, hashlib.sha256(doc3.content.encode()).hexdigest()),
        now=NOW + timedelta(seconds=5),
    )
    outbox.erase_content(
        principal_sha256=hashlib.sha256(doc3.principal_id.encode()).hexdigest(),
        document_id=doc3.document_id,
        operation_sha256="ab" * 32,
        erased_at=NOW + timedelta(seconds=2),
    )

    # 4. backwards_timestamp -> retained
    doc4 = _scoped_doc(tenant, installation, "clock-backwards", secret=secret_marker + "_4")
    job4 = outbox.enqueue(doc4)
    job4 = outbox.begin(job4, now=NOW - timedelta(seconds=2))
    outbox.succeed(
        job4,
        MemoryRetentionReceipt("bank-4", doc4.document_id, hashlib.sha256(doc4.content.encode()).hexdigest()),
        now=NOW + timedelta(seconds=4),
    )

    cutoff = NOW + timedelta(seconds=10)
    exec_now = NOW + timedelta(seconds=12)

    snap_before = overview.snapshot(now=exec_now, hindsight_state="ready")
    assert snap_before.memory_flow == "unavailable"
    assert snap_before.memory_diagnostic in {"historical_protocol_attention", "historical_timeline_attention"}

    dry = apply_verified_memory_cleanup(
        people_memory_engine,
        tenant_id=tenant,
        installation_id=installation,
        now=exec_now,
        cutoff_utc=cutoff,
        apply=False,
    )
    assert dry.applied is False
    assert dry.isolated_chronology_threads == 0
    assert dry.before.timeline_categories == {"chronology_defect_isolatable": 4}
    assert dry.before.chronology_defects_by_reason == {
        "attempt_out_of_order": 1,
        "backwards_timestamp": 1,
        "erased_not_terminal": 1,
        "success_out_of_order": 1,
    }
    assert secret_marker not in json.dumps(dry.to_payload())

    applied = apply_verified_memory_cleanup(
        people_memory_engine,
        tenant_id=tenant,
        installation_id=installation,
        now=exec_now,
        cutoff_utc=cutoff,
        apply=True,
    )
    assert applied.applied is True
    assert applied.isolated_chronology_threads == 4
    assert applied.isolated_by_disposition == {"erased": 1, "not_selected": 1, "retained": 2}
    assert secret_marker not in json.dumps(applied.to_payload())

    snap_after = overview.snapshot(now=exec_now, hindsight_state="ready")
    assert snap_after.memory_flow == "up_to_date"
    assert snap_after.memory_diagnostic is None

    # Original invalid prefixes remain strictly invalid if checked directly (never rewritten or declared healthy).
    for doc in (doc1, doc2, doc3):
        tid = PostgresMemoryOutbox._thread_id(doc.document_id)
        evs = timeline.events(tid)
        assert evs[-1].event_type == "MEMORY_RETAIN_CHRONOLOGY_ISOLATED"
        with pytest.raises(PeopleMemoryOverviewError):
            overview._validate_memory_timeline(timeline.load(tid), evs[:-1])

    # Replay is idempotent; conflicting proof is rejected.
    replay = apply_verified_memory_cleanup(
        people_memory_engine,
        tenant_id=tenant,
        installation_id=installation,
        now=exec_now + timedelta(seconds=1),
        cutoff_utc=cutoff,
        apply=True,
    )
    assert replay.isolated_chronology_threads == 0
    with pytest.raises(RuntimeError, match="replay differs"):
        outbox.isolate_historical_chronology(
            PostgresMemoryOutbox._thread_id(doc1.document_id),
            proof_sha256="ff" * 32,
            now=exec_now + timedelta(seconds=2),
        )


def test_unresolved_needs_help_chronology_never_fabricates_zero_or_up_to_date(people_memory_engine):
    tenant = "e411-needs-help-truth-tenant"
    installation = "e411-needs-help-truth-home"
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id=tenant, installation_id=installation)
    overview = PostgresPeopleMemoryOverview(people_memory_engine, tenant_id=tenant, installation_id=installation)
    timeline = outbox._timeline

    # Out-of-order terminal failure (retry_count=1 instead of 3) without any zero-selection receipt.
    doc_unresolved = _scoped_doc(tenant, installation, "unresolved-needs-help", secret="UNRESOLVED_SECRET_1")
    job_u = outbox.begin(outbox.enqueue(doc_unresolved), now=NOW + timedelta(seconds=1))
    fp = "aa" * 32
    timeline.transition(
        thread_id=job_u.state.thread_id,
        expected_revision=job_u.state.revision,
        checkpoint_id=job_u.state.thread_id + ":early-needs-help",
        operation_id=job_u.state.operation_id,
        occurred_at=NOW + timedelta(seconds=2),
        data={"document_id": doc_unresolved.document_id, "error_fingerprint": fp, "retry_count": 1},
        event_type="MEMORY_RETAIN_NEEDS_HELP",
        phase="memory_needs_help",
        status="needs_help",
        retry_count=1,
        last_error_fingerprint=fp,
    )

    # Second malformed needs_help thread that will later be forgotten by owner.
    doc_forget = _scoped_doc(tenant, installation, "forget-needs-help", secret="UNRESOLVED_SECRET_2")
    job_f = outbox.begin(outbox.enqueue(doc_forget), now=NOW + timedelta(seconds=1))
    timeline.transition(
        thread_id=job_f.state.thread_id,
        expected_revision=job_f.state.revision,
        checkpoint_id=job_f.state.thread_id + ":early-needs-help",
        operation_id=job_f.state.operation_id,
        occurred_at=NOW + timedelta(seconds=2),
        data={"document_id": doc_forget.document_id, "error_fingerprint": fp, "retry_count": 1},
        event_type="MEMORY_RETAIN_NEEDS_HELP",
        phase="memory_needs_help",
        status="needs_help",
        retry_count=1,
        last_error_fingerprint=fp,
    )

    assert overview.snapshot(now=NOW + timedelta(seconds=5), hindsight_state="ready").memory_flow == "unavailable"

    res = apply_verified_memory_cleanup(
        people_memory_engine,
        tenant_id=tenant,
        installation_id=installation,
        now=NOW + timedelta(seconds=6),
        cutoff_utc=NOW + timedelta(seconds=5),
        apply=True,
    )
    assert res.isolated_chronology_threads == 2
    assert res.isolated_by_disposition == {"needs_help": 2}
    assert res.after.problem_source_summary["unresolved_keep_visible"] == 2

    # Overview is now usable AND truthfully reports needs_help — NEVER fabricated up_to_date!
    snap = overview.snapshot(now=NOW + timedelta(seconds=6), hindsight_state="ready")
    assert snap.memory_flow == "needs_help"
    assert snap.memory_diagnostic is None
    assert timeline.load(job_u.state.thread_id).status == "needs_help"
    assert timeline.load(job_u.state.thread_id).phase == "memory_needs_help"

    # Owner forgets doc_forget -> redacted + MEMORY_RETAIN_ERASED appended; doc_unresolved still keeps overview in needs_help.
    outbox.erase_content(
        principal_sha256=hashlib.sha256(doc_forget.principal_id.encode()).hexdigest(),
        document_id=doc_forget.document_id,
        operation_sha256="bc" * 32,
        erased_at=NOW + timedelta(seconds=7),
    )
    assert overview.snapshot(now=NOW + timedelta(seconds=8), hindsight_state="ready").memory_flow == "needs_help"

    # Once doc_unresolved gets an authentic closed zero-selection proof, reconcile_closed_retention settles it cleanly.
    isolated_u_state = timeline.load(job_u.state.thread_id)
    zero_receipt = MemoryRetentionReceipt(
        bank_id="bank-verified",
        document_id=doc_unresolved.document_id,
        content_sha256=hashlib.sha256(doc_unresolved.content.encode()).hexdigest(),
        disposition="not_selected",
        selected_fact_count=0,
        operation_id="op-verified-zero",
    )
    outbox.reconcile_closed_retention(
        DurableMemoryRetentionJob(doc_unresolved, isolated_u_state),
        zero_receipt,
        proof_sha256="de" * 32,
        now=NOW + timedelta(seconds=9),
    )
    assert overview.snapshot(now=NOW + timedelta(seconds=10), hindsight_state="ready").memory_flow == "up_to_date"


def test_isolation_rejects_already_valid_corrupt_unknown_and_post_cutoff_sources(people_memory_engine):
    tenant = "e411-reject-invalid-tenant"
    installation = "e411-reject-invalid-home"
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id=tenant, installation_id=installation)
    overview = PostgresPeopleMemoryOverview(people_memory_engine, tenant_id=tenant, installation_id=installation)
    timeline = outbox._timeline

    # 1. Valid retained thread cannot be isolated.
    valid_doc = _scoped_doc(tenant, installation, "valid-retained", secret="VALID_SECRET")
    vjob = outbox.begin(outbox.enqueue(valid_doc), now=NOW + timedelta(seconds=1))
    outbox.succeed(
        vjob,
        MemoryRetentionReceipt("bank-v", valid_doc.document_id, hashlib.sha256(valid_doc.content.encode()).hexdigest()),
        now=NOW + timedelta(seconds=2),
    )
    with pytest.raises(RuntimeError, match="already valid"):
        outbox.isolate_historical_chronology(vjob.state.thread_id, proof_sha256="11" * 32, now=NOW + timedelta(seconds=3))

    # 2. Post-cutoff malformed source is excluded by cutoff_utc (reproducing the 2 post-closure sources guard).
    post_doc = _scoped_doc(
        tenant,
        installation,
        "post-cutoff-defect",
        secret="POST_CUTOFF_SECRET",
        occurred_at=(NOW + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    pjob = outbox.begin(outbox.enqueue(post_doc), now=NOW + timedelta(hours=2, seconds=1))
    pjob = outbox.begin(pjob, now=NOW + timedelta(hours=2, seconds=2))
    outbox.succeed(
        pjob,
        MemoryRetentionReceipt("bank-p", post_doc.document_id, hashlib.sha256(post_doc.content.encode()).hexdigest()),
        now=NOW + timedelta(hours=2, seconds=3),
    )
    with pytest.raises(RuntimeError, match="newer than cutoff"):
        outbox.isolate_historical_chronology(
            pjob.state.thread_id,
            proof_sha256="22" * 32,
            now=NOW + timedelta(hours=2, seconds=5),
            cutoff_utc=NOW + timedelta(hours=1),
        )

    audit = classify_memory_remnants(
        people_memory_engine,
        tenant_id=tenant,
        installation_id=installation,
        now=NOW + timedelta(hours=3),
        cutoff_utc=NOW + timedelta(hours=1),
    )
    assert audit.timeline_categories["valid:up_to_date"] == 1
    assert audit.timeline_categories["chronology_defect_post_cutoff_excluded"] == 1

    # 3. Unknown event in prefix is rejected by both isolate_historical_chronology and _isolated_historical_chronology.
    corrupt_doc = _scoped_doc(tenant, installation, "corrupt-unknown-event", secret="CORRUPT_SECRET")
    cjob = outbox.enqueue(corrupt_doc)
    timeline.transition(
        thread_id=cjob.state.thread_id,
        expected_revision=cjob.state.revision,
        checkpoint_id=cjob.state.thread_id + ":mystery",
        operation_id=cjob.state.operation_id,
        occurred_at=NOW + timedelta(seconds=1),
        data={"document_id": corrupt_doc.document_id},
        event_type="MEMORY_RETAIN_MYSTERY_EVENT",
        phase="memory_retained",
        status="completed",
    )
    with pytest.raises(RuntimeError, match="unknown"):
        outbox.isolate_historical_chronology(cjob.state.thread_id, proof_sha256="33" * 32, now=NOW + timedelta(seconds=5))

    # Even if an attacker manually appends a hash-matching MEMORY_RETAIN_CHRONOLOGY_ISOLATED event, overview rejects it!
    c_events = timeline.events(cjob.state.thread_id)
    forged_data = {
        "contract": "EvaHistoricalChronologyIsolation/v1",
        "document_id": corrupt_doc.document_id,
        "content_sha256": hashlib.sha256(corrupt_doc.content.encode()).hexdigest(),
        "terminal_disposition": "retained",
        "source_history_sha256": canonical_sha256([
            {
                "event_id": str(ev.event_id),
                "sequence": ev.sequence,
                "event_type": ev.event_type,
                "occurred_at": ev.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "payload_sha256": ev.payload_sha256,
            }
            for ev in c_events
        ]),
        "tail_history_sha256": canonical_sha256([
            {
                "event_id": str(ev.event_id),
                "sequence": ev.sequence,
                "event_type": ev.event_type,
                "occurred_at": ev.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "payload_sha256": ev.payload_sha256,
            }
            for ev in c_events[1:]
        ]),
        "proof_sha256": "44" * 32,
    }
    timeline.transition(
        thread_id=cjob.state.thread_id,
        expected_revision=2,
        checkpoint_id=cjob.state.thread_id + ":forged-iso",
        operation_id=cjob.state.operation_id,
        occurred_at=NOW + timedelta(seconds=6),
        data=forged_data,
        event_type="MEMORY_RETAIN_CHRONOLOGY_ISOLATED",
        phase="memory_retained",
        status="completed",
    )
    assert overview._isolated_historical_chronology(
        timeline.load(cjob.state.thread_id),
        timeline.events(cjob.state.thread_id),
    ) is None
    assert overview.snapshot(now=NOW + timedelta(hours=3), hindsight_state="ready").memory_flow == "unavailable"


def test_isolated_dream_source_supports_reflection_claim_work_hold_compaction_and_forgetting(selected_private_case):
    c = selected_private_case
    conv = f"dashboard:{c.binding}"
    msg1 = "iso-dream-1:" + str(c.owner)
    doc1 = replace(
        _document(msg1),
        tenant_id=c.reader._tenant,
        installation_id=c.reader._installation,
        principal_id=str(c.owner),
        conversation_id=conv,
        source_ref=msg1,
        evidence_ref="conversation-reply:" + msg1,
        document_id=f"{conv}:exchange:{msg1}",
        content="Private day exchange text eligible after isolation",
        occurred_at=(NOW - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        audience=MemoryAudience.private_person(principal_id=str(c.owner)),
        project_id=None,
        retention_mode="dream",
    )
    case = complete_dream_exchange(c.engine, doc1, now=NOW - timedelta(hours=2))

    # Inject historical out-of-order attempt before final retain success on a second dream cohort,
    # or test reflection + compaction + forgetting on an isolated completed dream exchange:
    msg2 = "iso-dream-2:" + str(c.owner)
    doc2 = replace(
        doc1,
        source_ref=msg2,
        evidence_ref="conversation-reply:" + msg2,
        document_id=f"{conv}:exchange:{msg2}",
        content="Second private day exchange with historical double attempt",
    )
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    from eva.application.conversation import ConversationTurn, GroundedReply

    turn2 = ConversationTurn.private(
        doc2.tenant_id, doc2.installation_id, doc2.principal_id, doc2.conversation_id,
        doc2.source_ref, "User question 2", doc2.occurred_at,
    )
    journal = PostgresConversationJournal(
        c.engine, external_timeline_scope(doc2.tenant_id, doc2.installation_id, "dream-test")
    )
    journal.admit(turn2)
    journal.commit_reply(
        turn2,
        GroundedReply(
            conversation_id=turn2.conversation_id,
            message_id=turn2.message_id,
            text="Eva reply 2",
            generated_at=turn2.occurred_at,
            provider="test",
            evidence_memory_ids=(),
        ),
    )

    class HoldRegistry:
        def __init__(self, held: set[tuple[str, str, str]]) -> None:
            self.held = held

        def protected_sources(self, tenant_id: str, installation_id: str, now) -> frozenset[tuple[str, str, str]]:
            return frozenset(self.held)

    hold = HoldRegistry({(doc2.principal_id, doc2.conversation_id, doc2.source_ref)})
    outbox = PostgresMemoryOutbox(
        c.engine, tenant_id=doc2.tenant_id, installation_id=doc2.installation_id, work_source_retention=hold
    )
    j2 = outbox.enqueue(doc2)
    batch2 = outbox.prepare_dream_batch((j2,), now=NOW - timedelta(hours=1, minutes=30))
    assert batch2 is not None
    outbox.bind_dream_operation(batch2, bank_id="bank-dream", operation_id="op-dream-2", now=NOW - timedelta(hours=1, minutes=29))
    # Historical double begin (attempt out of order) before succeed!
    b_first = outbox.begin(j2, now=NOW - timedelta(hours=1, minutes=28))
    b_second = outbox.begin(b_first, now=NOW - timedelta(hours=1, minutes=27))
    r2 = MemoryRetentionReceipt(
        bank_id="bank-dream",
        document_id=doc2.document_id,
        content_sha256=hashlib.sha256(doc2.content.encode()).hexdigest(),
        disposition="retained",
        selected_fact_count=1,
        operation_id="op-dream-2",
    )
    outbox.succeed(b_second, r2, now=NOW - timedelta(hours=1, minutes=26))
    outbox.bind_dream_receipts(batch2, (r2,), now=NOW - timedelta(hours=1, minutes=25))
    outbox.finish_dream_batch(batch2, now=NOW - timedelta(hours=1, minutes=24))

    overview = PostgresPeopleMemoryOverview(c.engine, tenant_id=doc2.tenant_id, installation_id=doc2.installation_id)
    assert overview.snapshot(now=NOW, hindsight_state="ready").memory_flow == "unavailable"

    # Apply cleanup while doc2 is protected by active work: doc1 compacts, doc2 isolates but stays uncompacted!
    result1 = apply_verified_memory_cleanup(
        c.engine,
        tenant_id=doc2.tenant_id,
        installation_id=doc2.installation_id,
        now=NOW,
        cutoff_utc=NOW - timedelta(minutes=1),
        apply=True,
        work_source_retention=hold,
        compact_eligible_day_sources=True,
    )
    assert result1.isolated_chronology_threads == 1
    assert result1.compacted_day_sources == 1
    assert result1.logical_raw_bytes_reduced == len(doc1.content.encode("utf-8"))
    assert result1.after.raw_source_categories["protected_by_active_work"] == 1
    assert overview.snapshot(now=NOW, hindsight_state="ready").memory_flow == "up_to_date"

    # Claim reflection AFTER chronology isolation -> appends MEMORY_REFLECTION_CLAIMED cleanly!
    assert outbox.claim_reflection_scope(doc2.tenant_id, doc2.installation_id, doc2.principal_id, now=NOW + timedelta(seconds=1))
    assert overview.snapshot(now=NOW + timedelta(seconds=2), hindsight_state="ready").memory_flow == "up_to_date"

    # Release active work hold -> doc2 compacts cleanly while keeping verified chronology isolation!
    hold.held.clear()
    result2 = apply_verified_memory_cleanup(
        c.engine,
        tenant_id=doc2.tenant_id,
        installation_id=doc2.installation_id,
        now=NOW + timedelta(seconds=3),
        cutoff_utc=NOW - timedelta(minutes=1),
        apply=True,
        work_source_retention=hold,
        compact_eligible_day_sources=True,
    )
    assert result2.compacted_day_sources == 1
    assert result2.after.total_logical_raw_source_bytes == 0
    assert overview.snapshot(now=NOW + timedelta(seconds=4), hindsight_state="ready").memory_flow == "up_to_date"

    # Owner forgets compacted isolated source -> erasure succeeds and status stays valid!
    outbox.erase_content(
        principal_sha256=hashlib.sha256(doc2.principal_id.encode()).hexdigest(),
        document_id=doc2.document_id,
        operation_sha256="ee" * 32,
        erased_at=NOW + timedelta(seconds=5),
    )
    assert overview.snapshot(now=NOW + timedelta(seconds=6), hindsight_state="ready").memory_flow == "up_to_date"


def test_native_hindsight_payload_and_problem_source_classification(people_memory_engine):
    tenant = "e411-native-hindsight-tenant"
    installation = "e411-native-hindsight-home"
    outbox = PostgresMemoryOutbox(people_memory_engine, tenant_id=tenant, installation_id=installation)
    overview = PostgresPeopleMemoryOverview(people_memory_engine, tenant_id=tenant, installation_id=installation)
    schema = "e411_hindsight_disposable"

    # Create two failed needs_help problem sources in eva_core:
    # - doc_zero: has a verified closed error-free zero-unit operation in Hindsight (reconcilable)
    # - doc_failed: had an extraction error / unverified outcome in Hindsight (must remain needs_help!)
    doc_zero = _scoped_doc(tenant, installation, "problem-zero", secret="ZERO_SOURCE_PRIVATE_TEXT_" + "x" * 90)
    doc_failed = _scoped_doc(tenant, installation, "problem-failed", secret="FAILED_SOURCE_PRIVATE_TEXT_" + "y" * 90)

    for doc in (doc_zero, doc_failed):
        job = outbox.enqueue(doc)
        for idx in range(3):
            begun = outbox.begin(job, now=NOW + timedelta(seconds=idx * 2 + 1))
            st = outbox.fail(begun, RuntimeError("timeout waiting for hindsight"), now=NOW + timedelta(seconds=idx * 2 + 2))
            job = DurableMemoryRetentionJob(doc, st)

    sql_template = (
        Path(__file__).resolve().parents[3]
        / "deploy/living-core/hindsight-memory/completed_payload_compaction.sql"
    ).read_text()

    with people_memory_engine.begin() as conn:
        conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
        conn.execute(
            sa.text(
                f"CREATE TABLE {schema}.async_operations ("
                f"operation_id uuid PRIMARY KEY, bank_id text NOT NULL, operation_type text NOT NULL, "
                f"status text NOT NULL, task_payload jsonb, result_metadata jsonb, "
                f"error_message text, retry_count integer DEFAULT 0)"
            )
        )
        conn.execute(sa.text(f"CREATE TABLE {schema}.documents (id text PRIMARY KEY, bank_id text NOT NULL)"))
        conn.execute(sa.text(f"CREATE TABLE {schema}.memory_units (id uuid PRIMARY KEY, document_id text NOT NULL)"))
        conn.exec_driver_sql(sql_template.replace("__SCHEMA__", schema))

        def insert_op(op_id: str, kind: str, status: str, payload, meta):
            conn.execute(
                sa.text(
                    f"INSERT INTO {schema}.async_operations "
                    f"(operation_id, bank_id, operation_type, status, task_payload, result_metadata) "
                    f"VALUES (:id, 'owner-bank', :kind, :status, CAST(:p AS jsonb), CAST(:m AS jsonb))"
                ),
                {
                    "id": op_id,
                    "kind": kind,
                    "status": status,
                    "p": None if payload is None else json.dumps(payload),
                    "m": json.dumps(meta),
                },
            )

        # 1. Closed zero-unit error-free family for doc_zero (trigger compacts >96B content to content_sha256!)
        parent_z, child_z = str(uuid4()), str(uuid4())
        insert_op(parent_z, "batch_retain", "pending", None, {"unit_ids_count": 0})
        insert_op(
            child_z,
            "retain",
            "completed",
            {
                "type": "batch_retain",
                "operation_id": child_z,
                "bank_id": "owner-bank",
                "contents": [{
                    "content": doc_zero.content,
                    "context": "Eva conversation and experience memory",
                    "tags": ["eva-memory"],
                    "document_id": doc_zero.document_id,
                    "metadata": {"selection_policy": "later-useful-knowledge-v2", "trust": "direct_user", "principal_id": "owner"},
                }],
            },
            {"parent_operation_id": parent_z, "unit_ids_count": 0, "extraction_errors_count": 0},
        )
        conn.execute(sa.text(f"UPDATE {schema}.async_operations SET status='completed' WHERE operation_id=:id"), {"id": parent_z})

        # 2. Short <=96B direct_user closed item (kept inline)
        op_short = str(uuid4())
        insert_op(
            op_short,
            "retain",
            "completed",
            {
                "type": "retain",
                "operation_id": op_short,
                "bank_id": "owner-bank",
                "contents": [{
                    "content": "Short note",
                    "document_id": "short:1",
                    "metadata": {"selection_policy": "later-useful-knowledge-v2", "trust": "direct_user", "principal_id": "owner"},
                }],
            },
            {"unit_ids_count": 0, "extraction_errors_count": 0},
        )

        # 3. Typed learning model_inference_unconfirmed (>96B, preserved by policy)
        op_learn = str(uuid4())
        insert_op(
            op_learn,
            "retain",
            "completed",
            {
                "type": "retain",
                "operation_id": op_learn,
                "bank_id": "owner-bank",
                "contents": [{
                    "content": "Typed human understanding record " * 5,
                    "document_id": "learning:1",
                    "metadata": {"selection_policy": "later-useful-knowledge-v2", "trust": "model_inference_unconfirmed", "principal_id": "owner"},
                }],
            },
            {"unit_ids_count": 1, "extraction_errors_count": 0},
        )

        # 4. Insufficiently bound / extraction-error legacy payload (80-cohort pattern, preserved!)
        op_legacy = str(uuid4())
        insert_op(
            op_legacy,
            "retain",
            "completed",
            {
                "type": "retain",
                "operation_id": op_legacy,
                "bank_id": "owner-bank",
                "contents": [{
                    "content": doc_failed.content,
                    "document_id": doc_failed.document_id,
                    "metadata": {"selection_policy": "later-useful-knowledge-v2", "trust": "direct_user"},
                }],
            },
            {"unit_ids_count": 0, "extraction_errors_count": 1},
        )

    try:
        audit = classify_memory_remnants(
            people_memory_engine,
            tenant_id=tenant,
            installation_id=installation,
            now=NOW + timedelta(seconds=20),
            cutoff_utc=NOW + timedelta(seconds=15),
            hindsight_engine=people_memory_engine,
            hindsight_schema=schema,
        )
        assert audit.problem_source_summary == {
            "total_needs_help_sources": 2,
            "closed_zero_reconcilable": 1,
            "unresolved_keep_visible": 1,
        }
        assert audit.native_hindsight_summary is not None
        assert audit.native_hindsight_summary["payload_item_counts"] == {
            "already_compacted_sha256": 1,
            "inline_small_direct_user_le96b": 1,
            "insufficiently_bound_or_error_preserved": 1,
            "non_direct_user_or_policy_preserved": 1,
        }
        assert "ZERO_SOURCE_PRIVATE_TEXT" not in json.dumps(audit.to_payload())
        assert "FAILED_SOURCE_PRIVATE_TEXT" not in json.dumps(audit.to_payload())

        applied = apply_verified_memory_cleanup(
            people_memory_engine,
            tenant_id=tenant,
            installation_id=installation,
            now=NOW + timedelta(seconds=20),
            cutoff_utc=NOW + timedelta(seconds=15),
            apply=True,
            hindsight_engine=people_memory_engine,
            hindsight_schema=schema,
            reconcile_verified_hindsight_zero=True,
        )
        assert applied.reconciled_zero_threads == 1
        assert applied.after.problem_source_summary == {
            "total_needs_help_sources": 1,
            "closed_zero_reconcilable": 0,
            "unresolved_keep_visible": 1,
        }
        # doc_zero settled to completed memory_not_selected; doc_failed remains visible as needs_help!
        assert outbox._timeline.load(PostgresMemoryOutbox._thread_id(doc_zero.document_id)).phase == "memory_not_selected"
        assert outbox._timeline.load(PostgresMemoryOutbox._thread_id(doc_failed.document_id)).phase == "memory_needs_help"
        assert overview.snapshot(now=NOW + timedelta(seconds=21), hindsight_state="ready").memory_flow == "needs_help"

        # CLI tool runs strictly read-only via --dsn-file and never leaks private text.
        import io
        from contextlib import redirect_stdout
        from tools.classify_memory_remnants import main as cli_main

        dsn_str = people_memory_engine.url.render_as_string(hide_password=False)
        dsn_path = Path(f"/tmp/eva2-e411-cli-dsn-{uuid4().hex}.txt")
        dsn_path.write_text(dsn_str, encoding="utf-8")
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main([
                    "--dsn-file", str(dsn_path),
                    "--hindsight-dsn-file", str(dsn_path),
                    "--hindsight-schema", schema,
                    "--tenant-id", tenant,
                    "--installation-id", installation,
                    "--now-utc", (NOW + timedelta(seconds=21)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "--cutoff-utc", (NOW + timedelta(seconds=15)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                ])
            assert rc == 0
            cli_out = buf.getvalue()
            cli_json = json.loads(cli_out)
            assert cli_json["contract"] == "EvaMemoryRemnantClassification/v1"
            assert cli_json["contains_raw_content"] is False
            assert cli_json["read_only"] is True
            assert cli_json["overview"] == {"state": "needs_help", "diagnostic": None}
            assert "ZERO_SOURCE_PRIVATE_TEXT" not in cli_out
            assert "FAILED_SOURCE_PRIVATE_TEXT" not in cli_out
        finally:
            dsn_path.unlink(missing_ok=True)
    finally:
        with people_memory_engine.begin() as conn:
            conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
