"""Privacy-safe classification and proof-bound cleanup for historical memory timelines."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import re
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.memory_outbox import (
    DurableMemoryRetentionJob,
    PostgresMemoryOutbox,
    conversation_memory_document_from_outbox_data,
    validate_historical_retain_prefix,
)
from eva.adapters.postgres.operational_timeline import (
    CheckpointState,
    OperationalTimelineError,
    PostgresOperationalTimeline,
    TimelineEvent,
    TimelineScope,
    operational_checkpoints,
    operational_events,
)
from eva.adapters.postgres.people_memory_overview import PostgresPeopleMemoryOverview
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.application.memory_source_lifecycle import CompactedPrivateMemorySource
from eva.application.people_memory_overview import PeopleMemoryOverviewError
from eva.contracts.canonical_json import canonical_sha256

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class VerifiedZeroSelectionProof:
    """Content-free native proof that a closed retain produced zero selected facts."""

    receipt: MemoryRetentionReceipt
    proof_sha256: str


@dataclass(frozen=True, slots=True)
class MemoryRemnantClassification:
    """Content-free audit snapshot of durable memory timelines and raw payload storage."""

    recorded_at: str
    cutoff_utc: str | None
    overview_flow: str
    overview_diagnostic: str | None
    total_retain_threads: int
    timeline_categories: dict[str, int]
    chronology_defects_by_reason: dict[str, int]
    chronology_defects_by_disposition: dict[str, int]
    isolated_by_disposition: dict[str, int]
    problem_source_summary: dict[str, int]
    raw_source_categories: dict[str, int]
    raw_source_bytes_by_category: dict[str, int]
    total_logical_raw_source_bytes: int
    dream_batch_summary: dict[str, int]
    protected_work_source_count: int
    isolatable_thread_prefixes: tuple[str, ...]
    unresolved_problem_thread_prefixes: tuple[str, ...]
    corrupt_thread_prefixes: tuple[str, ...]
    native_hindsight_summary: dict[str, object] | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "contract": "EvaMemoryRemnantClassification/v1",
            "contains_raw_content": False,
            "read_only": True,
            "recorded_at": self.recorded_at,
            "cutoff_utc": self.cutoff_utc,
            "overview": {
                "state": self.overview_flow,
                "diagnostic": self.overview_diagnostic,
            },
            "total_retain_threads": self.total_retain_threads,
            "timeline_categories": dict(self.timeline_categories),
            "chronology_defects_by_reason": dict(self.chronology_defects_by_reason),
            "chronology_defects_by_disposition": dict(self.chronology_defects_by_disposition),
            "isolated_by_disposition": dict(self.isolated_by_disposition),
            "problem_source_summary": dict(self.problem_source_summary),
            "raw_source_categories": dict(self.raw_source_categories),
            "raw_source_bytes_by_category": dict(self.raw_source_bytes_by_category),
            "total_logical_raw_source_bytes": self.total_logical_raw_source_bytes,
            "dream_batch_summary": dict(self.dream_batch_summary),
            "protected_work_source_count": self.protected_work_source_count,
            "isolatable_thread_prefixes": list(self.isolatable_thread_prefixes),
            "unresolved_problem_thread_prefixes": list(self.unresolved_problem_thread_prefixes),
            "corrupt_thread_prefixes": list(self.corrupt_thread_prefixes),
        }
        if self.native_hindsight_summary is not None:
            payload["native_hindsight"] = dict(self.native_hindsight_summary)
        return payload


@dataclass(frozen=True, slots=True)
class MemoryCleanupExecutionResult:
    """Content-free report of dry-run or applied historical memory cleanup."""

    applied: bool
    cutoff_utc: str
    executed_at: str
    reconciled_zero_threads: int
    isolated_chronology_threads: int
    isolated_by_disposition: dict[str, int]
    compacted_day_sources: int
    logical_raw_bytes_before: int
    logical_raw_bytes_after: int
    logical_raw_bytes_reduced: int
    before: MemoryRemnantClassification
    after: MemoryRemnantClassification

    def to_payload(self) -> dict[str, object]:
        return {
            "contract": "EvaMemoryChronologyCleanupResult/v1",
            "contains_raw_content": False,
            "applied": self.applied,
            "cutoff_utc": self.cutoff_utc,
            "executed_at": self.executed_at,
            "reconciled_zero_threads": self.reconciled_zero_threads,
            "isolated_chronology_threads": self.isolated_chronology_threads,
            "isolated_by_disposition": dict(self.isolated_by_disposition),
            "compacted_day_sources": self.compacted_day_sources,
            "logical_raw_bytes_before": self.logical_raw_bytes_before,
            "logical_raw_bytes_after": self.logical_raw_bytes_after,
            "logical_raw_bytes_reduced": self.logical_raw_bytes_reduced,
            "before": self.before.to_payload(),
            "after": self.after.to_payload(),
        }


def _require_utc_whole_second(value: datetime, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
        or value.microsecond
    ):
        raise ValueError(f"{name} must be whole-second UTC")


def _thread_prefix(thread_id: str) -> str:
    if thread_id.startswith("memory-retain:"):
        return "memory-retain:" + thread_id.removeprefix("memory-retain:")[:16]
    return thread_id[:28]


def _read_all_scoped_threads_raw(
    engine: Engine,
    scope: TimelineScope,
    *,
    prefix: str,
) -> list[tuple[CheckpointState, tuple[TimelineEvent, ...]]]:
    """Read scoped checkpoints and events in one read-only snapshot, including backwards clocks."""
    timeline = PostgresOperationalTimeline(engine, scope)
    c = operational_checkpoints.c
    e = operational_events.c
    with engine.connect() as connection:
        tx = connection.begin()
        try:
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            timeline.apply_scope(connection)
            cp_rows = connection.execute(
                sa.select(operational_checkpoints)
                .where(
                    c.tenant_id == scope.tenant_id,
                    c.installation_id == scope.installation_id,
                    c.thread_id.startswith(prefix),
                )
                .order_by(c.thread_id)
            ).all()
            if not cp_rows:
                return []
            ev_rows = connection.execute(
                sa.select(operational_events)
                .where(
                    e.tenant_id == scope.tenant_id,
                    e.installation_id == scope.installation_id,
                    e.thread_id.startswith(prefix),
                )
                .order_by(e.thread_id, e.sequence)
            ).all()
            by_thread: dict[str, list[TimelineEvent]] = {}
            for row in ev_rows:
                event = TimelineEvent(
                    event_id=row.event_id,
                    thread_id=str(row.thread_id),
                    sequence=int(row.sequence),
                    checkpoint_id=str(row.checkpoint_id),
                    operation_id=None if row.operation_id is None else str(row.operation_id),
                    event_type=str(row.event_type),
                    payload=row.payload,
                    payload_sha256=bytes(row.payload_sha256).hex(),
                    occurred_at=row.occurred_at,
                )
                by_thread.setdefault(event.thread_id, []).append(event)
            results: list[tuple[CheckpointState, tuple[TimelineEvent, ...]]] = []
            for row in cp_rows:
                raw_fp = row.last_error_fingerprint
                state = CheckpointState(
                    thread_id=str(row.thread_id),
                    checkpoint_id=str(row.checkpoint_id),
                    operation_id=None if row.operation_id is None else str(row.operation_id),
                    phase=str(row.phase),
                    status=str(row.status),  # type: ignore[arg-type]
                    retry_count=int(row.retry_count),
                    last_error_fingerprint=None if raw_fp is None else bytes(raw_fp).hex(),
                    deadline=row.deadline,
                    revision=int(row.revision),
                    updated_at=row.updated_at,
                )
                results.append((state, tuple(by_thread.get(state.thread_id, ()))))
            return results
        finally:
            tx.rollback()


def _conversation_exchange_ready_set(
    engine: Engine,
    scope: TimelineScope,
    source_refs: set[str],
) -> set[str]:
    if not source_refs:
        return set()
    timeline = PostgresOperationalTimeline(engine, scope)
    e = operational_events.c
    c = operational_checkpoints.c
    thread_ids = tuple("conversation:" + ref for ref in sorted(source_refs))
    ready: set[str] = set()
    with engine.connect() as connection:
        tx = connection.begin()
        try:
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            timeline.apply_scope(connection)
            for offset in range(0, len(thread_ids), 256):
                chunk = thread_ids[offset:offset + 256]
                cp_status = {
                    str(row.thread_id): (str(row.phase), str(row.status))
                    for row in connection.execute(
                        sa.select(c.thread_id, c.phase, c.status).where(
                            c.tenant_id == scope.tenant_id,
                            c.installation_id == scope.installation_id,
                            c.thread_id.in_(chunk),
                        )
                    ).all()
                }
                ev_map: dict[str, list[tuple[str, dict[str, Any]]]] = {}
                for row in connection.execute(
                    sa.select(e.thread_id, e.event_type, e.payload)
                    .where(
                        e.tenant_id == scope.tenant_id,
                        e.installation_id == scope.installation_id,
                        e.thread_id.in_(chunk),
                        e.event_type.in_(("INGRESS_COMMITTED", "REPLY_COMMITTED")),
                    )
                    .order_by(e.thread_id, e.sequence)
                ).all():
                    data = row.payload.get("data") if isinstance(row.payload, dict) else {}
                    ev_map.setdefault(str(row.thread_id), []).append(
                        (str(row.event_type), data if isinstance(data, dict) else {})
                    )
                for tid, items in ev_map.items():
                    phase_status = cp_status.get(tid)
                    if phase_status is None or phase_status[1] != "completed":
                        continue
                    if phase_status[0] not in {"reply_committed", "memory_queued"}:
                        continue
                    if (
                        len(items) == 2
                        and [it[0] for it in items] == ["INGRESS_COMMITTED", "REPLY_COMMITTED"]
                        and all("content_erasure" not in it[1] and "text" in it[1] for it in items)
                    ):
                        ready.add(tid.removeprefix("conversation:"))
            return ready
        finally:
            tx.rollback()


def _inspect_native_hindsight(
    hindsight_engine: Engine,
    *,
    schema: str = "public",
    problem_sources: dict[str, str],
) -> tuple[dict[str, object], dict[str, VerifiedZeroSelectionProof]]:
    """Read-only inspection of Hindsight async_operations and problem-source unit/receipt state."""
    if not _SAFE_IDENT_RE.fullmatch(schema):
        raise ValueError("hindsight schema identifier is invalid")
    payload_counts: Counter[str] = Counter()
    payload_bytes: Counter[str] = Counter()
    verified_zero_proofs: dict[str, VerifiedZeroSelectionProof] = {}
    problem_indexed_docs = 0
    problem_indexed_units = 0

    with hindsight_engine.connect() as connection:
        tx = connection.begin()
        try:
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            has_docs = bool(
                connection.scalar(
                    sa.text(
                        "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = :schema AND table_name = 'documents')"
                    ),
                    {"schema": schema},
                )
            )
            has_units = bool(
                connection.scalar(
                    sa.text(
                        "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = :schema AND table_name = 'memory_units')"
                    ),
                    {"schema": schema},
                )
            )
            doc_unit_counts: dict[str, int] = {}
            if problem_sources and has_docs:
                doc_ids = list(problem_sources)
                existing_docs = set(
                    connection.scalars(
                        sa.text(f"SELECT id FROM {schema}.documents WHERE id = ANY(:ids)"),
                        {"ids": doc_ids},
                    ).all()
                )
                problem_indexed_docs = len(existing_docs)
            if problem_sources and has_units:
                for row in connection.execute(
                    sa.text(
                        f"SELECT document_id, count(*)::int AS cnt "
                        f"FROM {schema}.memory_units WHERE document_id = ANY(:ids) "
                        f"GROUP BY document_id"
                    ),
                    {"ids": list(problem_sources)},
                ).all():
                    doc_unit_counts[str(row.document_id)] = int(row.cnt)
                problem_indexed_units = sum(doc_unit_counts.values())

            op_rows = connection.execute(
                sa.text(
                    f"SELECT operation_id::text AS operation_id, bank_id, operation_type, "
                    f"status, task_payload, result_metadata "
                    f"FROM {schema}.async_operations"
                )
            ).mappings().all()
        finally:
            tx.rollback()

    by_id = {str(r["operation_id"]): r for r in op_rows}
    children_by_parent: dict[str, list[Mapping[str, Any]]] = {}
    for row in op_rows:
        meta = row["result_metadata"]
        parent_id = meta.get("parent_operation_id") if isinstance(meta, dict) else None
        if isinstance(parent_id, str) and parent_id:
            children_by_parent.setdefault(parent_id, []).append(row)

    def _family_closed_error_free(row: Mapping[str, Any]) -> tuple[bool, str | None]:
        op_id = str(row["operation_id"])
        meta = row["result_metadata"] if isinstance(row["result_metadata"], dict) else {}
        parent_id = meta.get("parent_operation_id")
        root_id = str(parent_id) if isinstance(parent_id, str) and parent_id else op_id
        root = by_id.get(root_id)
        if root is None or root["status"] != "completed":
            return False, None
        siblings = children_by_parent.get(root_id, [row] if root_id == op_id else [])
        if not siblings:
            return False, None
        for child in siblings:
            cmeta = child["result_metadata"] if isinstance(child["result_metadata"], dict) else {}
            err_cnt = cmeta.get("extraction_errors_count")
            if child["status"] != "completed" or type(err_cnt) is not int or err_cnt != 0:
                return False, None
        return True, root_id

    candidate_zero_matches: dict[str, list[tuple[str, str, str]]] = {}

    for row in op_rows:
        tp = row["task_payload"]
        if not isinstance(tp, dict):
            continue
        contents = tp.get("contents")
        if not isinstance(contents, list):
            if row["operation_type"] in {"retain", "batch_retain"}:
                payload_counts["insufficiently_bound_or_error_preserved"] += 1
            continue
        payload_type_ok = tp.get("type") in {"batch_retain", "retain"}
        bank_matches = tp.get("bank_id") == row["bank_id"] and isinstance(row["bank_id"], str) and bool(row["bank_id"])
        family_ok, root_op_id = _family_closed_error_free(row)

        for item in contents:
            if not isinstance(item, dict):
                payload_counts["insufficiently_bound_or_error_preserved"] += 1
                continue
            doc_id = item.get("document_id")
            if "content_sha256" in item and "content" not in item:
                payload_counts["already_compacted_sha256"] += 1
                if (
                    family_ok
                    and root_op_id is not None
                    and bank_matches
                    and isinstance(doc_id, str)
                    and doc_id in problem_sources
                    and doc_unit_counts.get(doc_id, 0) == 0
                    and item.get("content_sha256") == problem_sources[doc_id]
                    and has_units
                ):
                    rmeta = row["result_metadata"] if isinstance(row["result_metadata"], dict) else {}
                    unit_cnt = rmeta.get("unit_ids_count")
                    if type(unit_cnt) is int and unit_cnt == 0:
                        candidate_zero_matches.setdefault(doc_id, []).append(
                            (str(row["bank_id"]), root_op_id, str(item["content_sha256"]))
                        )
                continue
            content = item.get("content")
            if not isinstance(content, str):
                payload_counts["insufficiently_bound_or_error_preserved"] += 1
                continue
            raw_len = len(content.encode("utf-8"))
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            policy = meta.get("selection_policy")
            trust = meta.get("trust")
            has_source_binding = isinstance(doc_id, str) and bool(doc_id) and isinstance(meta.get("principal_id"), str)
            rmeta = row["result_metadata"] if isinstance(row["result_metadata"], dict) else {}
            is_batch_child = (
                row["operation_type"] == "retain"
                and isinstance(rmeta.get("parent_operation_id"), str)
                and bool(rmeta.get("parent_operation_id"))
                and tp.get("operation_id") == str(row["operation_id"])
                and type(rmeta.get("unit_ids_count")) is int
            )
            has_eva_memory_tag = (
                item.get("context") == "Eva conversation and experience memory"
                and isinstance(item.get("tags"), list)
                and "eva-memory" in item["tags"]
            )

            if not payload_type_ok or not bank_matches or not family_ok or not has_source_binding:
                payload_counts["insufficiently_bound_or_error_preserved"] += 1
                payload_bytes["insufficiently_bound_or_error_preserved"] += raw_len
            elif policy != "later-useful-knowledge-v2" or trust != "direct_user":
                payload_counts["non_direct_user_or_policy_preserved"] += 1
                payload_bytes["non_direct_user_or_policy_preserved"] += raw_len
            elif raw_len <= 96:
                payload_counts["inline_small_direct_user_le96b"] += 1
                payload_bytes["inline_small_direct_user_le96b"] += raw_len
            elif is_batch_child and has_eva_memory_tag:
                payload_counts["eligible_closed_family_direct_user_gt96b"] += 1
                payload_bytes["eligible_closed_family_direct_user_gt96b"] += raw_len
            else:
                payload_counts["non_direct_user_or_policy_preserved"] += 1
                payload_bytes["non_direct_user_or_policy_preserved"] += raw_len

            if (
                family_ok
                and root_op_id is not None
                and bank_matches
                and isinstance(doc_id, str)
                and doc_id in problem_sources
                and doc_unit_counts.get(doc_id, 0) == 0
            ):
                content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if content_digest == problem_sources[doc_id]:
                    unit_cnt = rmeta.get("unit_ids_count")
                    if type(unit_cnt) is int and unit_cnt == 0 and has_units:
                        candidate_zero_matches.setdefault(doc_id, []).append(
                            (str(row["bank_id"]), root_op_id, content_digest)
                        )

    for doc_id, matches in candidate_zero_matches.items():
        unique = sorted(set(matches))
        if len(unique) == 1:
            bank_id, op_id, content_digest = unique[0]
            receipt = MemoryRetentionReceipt(
                bank_id=bank_id,
                document_id=doc_id,
                content_sha256=content_digest,
                disposition="not_selected",
                selected_fact_count=0,
                operation_id=op_id,
            )
            proof_sha = canonical_sha256({
                "contract": "EvaClosedNativeZeroSelectionProof/v1",
                "bank_id": bank_id,
                "document_id": doc_id,
                "content_sha256": content_digest,
                "operation_id": op_id,
                "selected_fact_count": 0,
            })
            verified_zero_proofs[doc_id] = VerifiedZeroSelectionProof(receipt=receipt, proof_sha256=proof_sha)

    summary: dict[str, object] = {
        "total_async_operations": len(op_rows),
        "payload_item_counts": dict(sorted(payload_counts.items())),
        "payload_item_raw_bytes": dict(sorted(payload_bytes.items())),
        "problem_sources_in_hindsight_documents": problem_indexed_docs,
        "problem_sources_in_hindsight_memory_units": problem_indexed_units,
        "verified_closed_zero_problem_sources": len(verified_zero_proofs),
    }
    return summary, verified_zero_proofs


def classify_memory_remnants(
    engine: Engine,
    *,
    tenant_id: str,
    installation_id: str,
    now: datetime,
    cutoff_utc: datetime | None = None,
    work_source_retention: Any = None,
    hindsight_engine: Engine | None = None,
    hindsight_schema: str = "public",
    verified_zero_receipts: Mapping[str, VerifiedZeroSelectionProof] | None = None,
) -> MemoryRemnantClassification:
    """Classify all memory-retain, memory-dream, and optional native Hindsight remnants read-only."""
    _require_utc_whole_second(now, "now")
    if cutoff_utc is not None:
        _require_utc_whole_second(cutoff_utc, "cutoff_utc")
    scope = TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
        installation_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"),
    )
    timeline = PostgresOperationalTimeline(engine, scope)
    overview = PostgresPeopleMemoryOverview(engine, tenant_id=tenant_id, installation_id=installation_id)
    overview_flow, overview_diagnostic = overview._memory_flow_status()

    protected_sources: frozenset[tuple[str, str, str]] = (
        frozenset()
        if work_source_retention is None
        else frozenset(work_source_retention.protected_sources(tenant_id, installation_id, now))
    )

    dream_threads = _read_all_scoped_threads_raw(engine, scope, prefix="memory-dream:")
    dream_summary: Counter[str] = Counter()
    acknowledged_dream_members: dict[str, str] = {}
    cohort_members_by_batch: dict[str, list[str]] = {}
    for d_state, d_events in dream_threads:
        dream_summary[f"{d_state.phase}:{d_state.status}"] += 1
        if not d_events or d_events[0].event_type != "MEMORY_DREAM_BATCH_ASSIGNED":
            continue
        first_data = d_events[0].payload.get("data") if isinstance(d_events[0].payload, dict) else None
        try:
            members = PostgresMemoryOutbox._dream_members(first_data, d_state.thread_id)
        except Exception:
            dream_summary["malformed_manifest"] += 1
            continue
        doc_ids = [str(m["document_id"]) for m in members]
        cohort_members_by_batch[d_state.thread_id] = doc_ids
        has_ack = any(ev.event_type == "MEMORY_DREAM_RECEIPTS_BOUND" for ev in d_events)
        has_dispatch = sum(1 for ev in d_events if ev.event_type == "MEMORY_DREAM_DISPATCH_BOUND") == 1
        if d_state.phase == "memory_dream_completed" and d_state.status == "completed" and has_ack and has_dispatch:
            for doc_id in doc_ids:
                acknowledged_dream_members[doc_id] = d_state.thread_id

    retain_threads = _read_all_scoped_threads_raw(engine, scope, prefix="memory-retain:")

    doc_state_by_id: dict[str, tuple[CheckpointState, dict[str, Any]]] = {}
    candidate_exchange_refs: set[str] = set()
    for r_state, r_events in retain_threads:
        if r_events and isinstance(r_events[0].payload, dict):
            qdata = r_events[0].payload.get("data")
            if isinstance(qdata, dict) and isinstance(qdata.get("document_id"), str):
                doc_state_by_id[qdata["document_id"]] = (r_state, qdata)
                if (
                    "content" in qdata
                    and "content_erasure" not in qdata
                    and "content_compaction" not in qdata
                    and qdata.get("retention_mode") == "dream"
                    and isinstance(qdata.get("source_ref"), str)
                ):
                    candidate_exchange_refs.add(str(qdata["source_ref"]))

    ready_exchanges = _conversation_exchange_ready_set(engine, scope, candidate_exchange_refs)

    eligible_dream_batches: set[str] = set()
    day_cutoff_str = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    midnight_str = now.replace(hour=0, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    for batch_tid, member_doc_ids in cohort_members_by_batch.items():
        if not member_doc_ids or any(acknowledged_dream_members.get(did) != batch_tid for did in member_doc_ids):
            continue
        batch_ok = True
        for did in member_doc_ids:
            entry = doc_state_by_id.get(did)
            if entry is None:
                batch_ok = False
                break
            m_state, m_qdata = entry
            occ = str(m_qdata.get("occurred_at", ""))
            if (
                "content_erasure" in m_qdata
                or m_state.status != "completed"
                or m_state.phase not in {"memory_retained", "memory_not_selected"}
                or not occ
                or occ >= day_cutoff_str
                or occ >= midnight_str
            ):
                batch_ok = False
                break
        if batch_ok:
            eligible_dream_batches.add(batch_tid)

    timeline_categories: Counter[str] = Counter()
    defects_by_reason: Counter[str] = Counter()
    defects_by_disposition: Counter[str] = Counter()
    isolated_by_disposition: Counter[str] = Counter()
    raw_categories: Counter[str] = Counter()
    raw_bytes: Counter[str] = Counter()
    isolatable_prefixes: list[str] = []
    unresolved_problem_prefixes: list[str] = []
    corrupt_prefixes: list[str] = []
    problem_source_hashes: dict[str, str] = {}
    problem_thread_by_doc: dict[str, str] = {}

    for state, events in retain_threads:
        t_prefix = _thread_prefix(state.thread_id)
        payload_hashes_valid = bool(events) and len(events) == state.revision and all(
            canonical_sha256(ev.payload) == ev.payload_sha256
            and ev.sequence == idx
            for idx, ev in enumerate(events, 1)
        )
        if not payload_hashes_valid:
            timeline_categories["corrupt_or_unisolatable"] += 1
            corrupt_prefixes.append(t_prefix)
            continue
        last_cp = events[-1].payload.get("checkpoint") if isinstance(events[-1].payload, dict) else None
        if not isinstance(last_cp, dict) or (
            last_cp.get("checkpoint_id") != state.checkpoint_id
            or last_cp.get("phase") != state.phase
            or last_cp.get("status") != state.status
            or last_cp.get("revision") != state.revision
        ):
            timeline_categories["corrupt_or_unisolatable"] += 1
            corrupt_prefixes.append(t_prefix)
            continue

        qdata = events[0].payload.get("data") if isinstance(events[0].payload, dict) else {}
        if not isinstance(qdata, dict):
            qdata = {}
        doc_id = qdata.get("document_id") if isinstance(qdata.get("document_id"), str) else None
        raw_content = qdata.get("content") if isinstance(qdata.get("content"), str) else None
        content_len = len(raw_content.encode("utf-8")) if raw_content is not None else 0

        if "content_erasure" in qdata and raw_content is None:
            raw_categories["erased_tombstone"] += 1
        elif "content_compaction" in qdata and raw_content is None:
            raw_categories["already_compacted_day_source"] += 1
        elif raw_content is not None:
            source_tuple = (
                str(qdata.get("principal_id", "")),
                str(qdata.get("conversation_id", "")),
                str(qdata.get("source_ref", "")),
            )
            occ = str(qdata.get("occurred_at", ""))
            batch_tid = acknowledged_dream_members.get(doc_id or "")
            if source_tuple in protected_sources:
                raw_categories["protected_by_active_work"] += 1
                raw_bytes["protected_by_active_work"] += content_len
            elif qdata.get("retention_mode") == "dream" and qdata.get("trust") == "direct_user":
                if occ >= day_cutoff_str:
                    raw_categories["within_24h_day_window"] += 1
                    raw_bytes["within_24h_day_window"] += content_len
                elif (
                    batch_tid in eligible_dream_batches
                    and str(qdata.get("source_ref", "")) in ready_exchanges
                ):
                    raw_categories["eligible_for_day_compaction"] += 1
                    raw_bytes["eligible_for_day_compaction"] += content_len
                else:
                    raw_categories["unacknowledged_or_unresolved_preserved"] += 1
                    raw_bytes["unacknowledged_or_unresolved_preserved"] += content_len
            elif qdata.get("trust") == "model_inference_unconfirmed" or qdata.get("retention_mode", "immediate") == "immediate":
                raw_categories["typed_learning_or_immediate_retained"] += 1
                raw_bytes["typed_learning_or_immediate_retained"] += content_len
            else:
                raw_categories["unacknowledged_or_unresolved_preserved"] += 1
                raw_bytes["unacknowledged_or_unresolved_preserved"] += content_len

        has_backwards = any(
            later.occurred_at < earlier.occurred_at
            for earlier, later in zip(events, events[1:])
        )
        isolated_flow = overview._isolated_historical_chronology(state, events)
        if isolated_flow is not None:
            timeline_categories[f"isolated_historical:{isolated_flow}"] += 1
            iso_ev = next(ev for ev in events if ev.event_type == "MEMORY_RETAIN_CHRONOLOGY_ISOLATED")
            disp = str(iso_ev.payload["data"]["terminal_disposition"])
            isolated_by_disposition[disp] += 1
            if isolated_flow == "needs_help" and doc_id is not None and raw_content is not None:
                problem_source_hashes[doc_id] = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
                problem_thread_by_doc[doc_id] = t_prefix
            continue

        if not has_backwards:
            try:
                flow = overview._validate_memory_timeline(state, events)
                timeline_categories[f"valid:{flow}"] += 1
                if flow == "needs_help" and doc_id is not None and raw_content is not None:
                    problem_source_hashes[doc_id] = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
                    problem_thread_by_doc[doc_id] = t_prefix
                continue
            except (PeopleMemoryOverviewError, OperationalTimelineError):
                pass

        try:
            validated = validate_historical_retain_prefix(
                timeline,
                tenant_id=tenant_id,
                installation_id=installation_id,
                thread_id=state.thread_id,
                prefix=events,
            )
        except Exception:
            timeline_categories["corrupt_or_unisolatable"] += 1
            corrupt_prefixes.append(t_prefix)
            continue

        if validated.defect_code is None or validated.terminal_disposition is None:
            timeline_categories["corrupt_or_unisolatable"] += 1
            corrupt_prefixes.append(t_prefix)
            continue

        defects_by_reason[validated.defect_code] += 1
        defects_by_disposition[validated.terminal_disposition] += 1
        if validated.terminal_disposition == "needs_help" and doc_id is not None and raw_content is not None:
            problem_source_hashes[doc_id] = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
            problem_thread_by_doc[doc_id] = t_prefix

        if cutoff_utc is not None and events[0].occurred_at > cutoff_utc:
            timeline_categories["chronology_defect_post_cutoff_excluded"] += 1
        else:
            timeline_categories["chronology_defect_isolatable"] += 1
            isolatable_prefixes.append(t_prefix)

    explicit_zero_ids: set[str] = set()
    for z_doc_id, z_proof in (verified_zero_receipts or {}).items():
        if (
            isinstance(z_proof, VerifiedZeroSelectionProof)
            and z_doc_id in problem_source_hashes
            and z_proof.receipt.document_id == z_doc_id
            and z_proof.receipt.content_sha256 == problem_source_hashes[z_doc_id]
            and z_proof.receipt.disposition == "not_selected"
            and z_proof.receipt.selected_fact_count == 0
            and isinstance(z_proof.receipt.operation_id, str)
            and bool(z_proof.receipt.operation_id.strip())
            and isinstance(z_proof.proof_sha256, str)
            and _SHA256_RE.fullmatch(z_proof.proof_sha256)
        ):
            explicit_zero_ids.add(z_doc_id)
    native_summary: dict[str, object] | None = None
    hindsight_zero_proofs: dict[str, VerifiedZeroSelectionProof] = {}
    if hindsight_engine is not None:
        native_summary, hindsight_zero_proofs = _inspect_native_hindsight(
            hindsight_engine,
            schema=hindsight_schema,
            problem_sources=problem_source_hashes,
        )
    reconcilable_ids = (explicit_zero_ids | set(hindsight_zero_proofs)) & set(problem_source_hashes)
    for doc_id, t_prefix in sorted(problem_thread_by_doc.items(), key=lambda item: item[1]):
        if doc_id not in reconcilable_ids:
            unresolved_problem_prefixes.append(t_prefix)

    problem_summary = {
        "total_needs_help_sources": len(problem_source_hashes),
        "closed_zero_reconcilable": len(reconcilable_ids),
        "unresolved_keep_visible": len(problem_source_hashes) - len(reconcilable_ids),
    }

    return MemoryRemnantClassification(
        recorded_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        cutoff_utc=None if cutoff_utc is None else cutoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        overview_flow=overview_flow,
        overview_diagnostic=overview_diagnostic,
        total_retain_threads=len(retain_threads),
        timeline_categories=dict(sorted(timeline_categories.items())),
        chronology_defects_by_reason=dict(sorted(defects_by_reason.items())),
        chronology_defects_by_disposition=dict(sorted(defects_by_disposition.items())),
        isolated_by_disposition=dict(sorted(isolated_by_disposition.items())),
        problem_source_summary=problem_summary,
        raw_source_categories=dict(sorted(raw_categories.items())),
        raw_source_bytes_by_category=dict(sorted(raw_bytes.items())),
        total_logical_raw_source_bytes=sum(raw_bytes.values()),
        dream_batch_summary=dict(sorted(dream_summary.items())),
        protected_work_source_count=len(protected_sources),
        isolatable_thread_prefixes=tuple(isolatable_prefixes),
        unresolved_problem_thread_prefixes=tuple(unresolved_problem_prefixes),
        corrupt_thread_prefixes=tuple(corrupt_prefixes),
        native_hindsight_summary=native_summary,
    )


def build_chronology_isolation_proof_sha256(
    *,
    thread_id: str,
    defect_code: str,
    terminal_disposition: str,
    source_history_sha256: str,
    cutoff_utc: datetime,
) -> str:
    """Derive a deterministic, content-free isolation proof hash for one historical thread."""
    _require_utc_whole_second(cutoff_utc, "cutoff_utc")
    if not _SHA256_RE.fullmatch(source_history_sha256):
        raise ValueError("source_history_sha256 must be lowercase SHA-256")
    return canonical_sha256({
        "contract": "EvaHistoricalChronologyIsolationProof/v1",
        "cutoff_utc": cutoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "defect_code": defect_code,
        "source_history_sha256": source_history_sha256,
        "terminal_disposition": terminal_disposition,
        "thread_id": thread_id,
    })


def apply_verified_memory_cleanup(
    engine: Engine,
    *,
    tenant_id: str,
    installation_id: str,
    now: datetime,
    cutoff_utc: datetime,
    apply: bool = False,
    work_source_retention: Any = None,
    verified_zero_receipts: Mapping[str, VerifiedZeroSelectionProof] | None = None,
    hindsight_engine: Engine | None = None,
    hindsight_schema: str = "public",
    reconcile_verified_hindsight_zero: bool = False,
    compact_eligible_day_sources: bool = False,
    compaction_limit: int = 128,
) -> MemoryCleanupExecutionResult:
    """Plan or execute proof-bound reconciliation, chronology isolation, and day compaction."""
    _require_utc_whole_second(now, "now")
    _require_utc_whole_second(cutoff_utc, "cutoff_utc")
    if cutoff_utc > now:
        raise ValueError("cutoff_utc cannot exceed now")
    if type(apply) is not bool:
        raise ValueError("apply must be a boolean")

    before = classify_memory_remnants(
        engine,
        tenant_id=tenant_id,
        installation_id=installation_id,
        now=now,
        cutoff_utc=cutoff_utc,
        work_source_retention=work_source_retention,
        hindsight_engine=hindsight_engine,
        hindsight_schema=hindsight_schema,
        verified_zero_receipts=verified_zero_receipts,
    )
    if not apply:
        return MemoryCleanupExecutionResult(
            applied=False,
            cutoff_utc=cutoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            executed_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            reconciled_zero_threads=0,
            isolated_chronology_threads=0,
            isolated_by_disposition={},
            compacted_day_sources=0,
            logical_raw_bytes_before=before.total_logical_raw_source_bytes,
            logical_raw_bytes_after=before.total_logical_raw_source_bytes,
            logical_raw_bytes_reduced=0,
            before=before,
            after=before,
        )

    scope = TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
        installation_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"),
    )
    outbox = PostgresMemoryOutbox(
        engine,
        tenant_id=tenant_id,
        installation_id=installation_id,
        work_source_retention=work_source_retention,
    )
    overview = PostgresPeopleMemoryOverview(engine, tenant_id=tenant_id, installation_id=installation_id)

    zero_proofs: dict[str, VerifiedZeroSelectionProof] = dict(verified_zero_receipts or {})
    if reconcile_verified_hindsight_zero and hindsight_engine is not None:
        problem_hashes: dict[str, str] = {}
        for r_state, r_events in _read_all_scoped_threads_raw(engine, scope, prefix="memory-retain:"):
            if r_state.phase == "memory_needs_help" and r_events:
                qd = r_events[0].payload.get("data") if isinstance(r_events[0].payload, dict) else None
                if isinstance(qd, dict) and isinstance(qd.get("document_id"), str) and isinstance(qd.get("content"), str):
                    problem_hashes[qd["document_id"]] = hashlib.sha256(qd["content"].encode("utf-8")).hexdigest()
        _, discovered = _inspect_native_hindsight(
            hindsight_engine, schema=hindsight_schema, problem_sources=problem_hashes
        )
        for doc_id, proof in discovered.items():
            zero_proofs.setdefault(doc_id, proof)

    reconciled_count = 0
    isolated_count = 0
    isolated_dispositions: Counter[str] = Counter()
    compacted_count = 0

    with outbox.retention_guard():
        raw_threads = _read_all_scoped_threads_raw(engine, scope, prefix="memory-retain:")
        for state, events in raw_threads:
            if not events or events[0].occurred_at > cutoff_utc:
                continue
            has_backwards = any(
                later.occurred_at < earlier.occurred_at
                for earlier, later in zip(events, events[1:])
            )
            if overview._isolated_historical_chronology(state, events) is not None:
                continue
            is_valid = False
            if not has_backwards:
                try:
                    overview._validate_memory_timeline(state, events)
                    is_valid = True
                except (PeopleMemoryOverviewError, OperationalTimelineError):
                    is_valid = False
            if not is_valid:
                try:
                    validated = validate_historical_retain_prefix(
                        outbox._timeline,
                        tenant_id=tenant_id,
                        installation_id=installation_id,
                        thread_id=state.thread_id,
                        prefix=events,
                    )
                except Exception:
                    continue
                if validated.defect_code is not None and validated.terminal_disposition is not None:
                    proof_sha = build_chronology_isolation_proof_sha256(
                        thread_id=state.thread_id,
                        defect_code=validated.defect_code,
                        terminal_disposition=validated.terminal_disposition,
                        source_history_sha256=validated.source_history_sha256,
                        cutoff_utc=cutoff_utc,
                    )
                    state = outbox.isolate_historical_chronology(
                        state.thread_id,
                        proof_sha256=proof_sha,
                        now=now,
                        cutoff_utc=cutoff_utc,
                    )
                    events = outbox._timeline.events(state.thread_id)
                    isolated_count += 1
                    isolated_dispositions[validated.terminal_disposition] += 1

            if (state.phase, state.status) in {
                ("memory_needs_help", "needs_help"),
                ("memory_retry", "failed"),
            }:
                qdata = events[0].payload.get("data") if isinstance(events[0].payload, dict) else None
                if not isinstance(qdata, dict) or "content_erasure" in qdata:
                    continue
                try:
                    doc = conversation_memory_document_from_outbox_data(qdata)
                except Exception:
                    continue
                if isinstance(doc, ConversationMemoryDocument) and doc.document_id in zero_proofs:
                    zproof = zero_proofs[doc.document_id]
                    outbox.reconcile_closed_retention(
                        DurableMemoryRetentionJob(doc, state),
                        zproof.receipt,
                        proof_sha256=zproof.proof_sha256,
                        now=now,
                    )
                    reconciled_count += 1

    if compact_eligible_day_sources:
        while True:
            batch_compacted = outbox.compact_day_sources(now=now, limit=min(max(1, compaction_limit), 128))
            compacted_count += len(batch_compacted)
            if len(batch_compacted) < min(max(1, compaction_limit), 128):
                break

    after = classify_memory_remnants(
        engine,
        tenant_id=tenant_id,
        installation_id=installation_id,
        now=now,
        cutoff_utc=cutoff_utc,
        work_source_retention=work_source_retention,
        hindsight_engine=hindsight_engine,
        hindsight_schema=hindsight_schema,
        verified_zero_receipts=verified_zero_receipts,
    )
    return MemoryCleanupExecutionResult(
        applied=True,
        cutoff_utc=cutoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        executed_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        reconciled_zero_threads=reconciled_count,
        isolated_chronology_threads=isolated_count,
        isolated_by_disposition=dict(sorted(isolated_dispositions.items())),
        compacted_day_sources=compacted_count,
        logical_raw_bytes_before=before.total_logical_raw_source_bytes,
        logical_raw_bytes_after=after.total_logical_raw_source_bytes,
        logical_raw_bytes_reduced=before.total_logical_raw_source_bytes - after.total_logical_raw_source_bytes,
        before=before,
        after=after,
    )


__all__ = [
    "MemoryCleanupExecutionResult",
    "MemoryRemnantClassification",
    "VerifiedZeroSelectionProof",
    "apply_verified_memory_cleanup",
    "build_chronology_isolation_proof_sha256",
    "classify_memory_remnants",
]
