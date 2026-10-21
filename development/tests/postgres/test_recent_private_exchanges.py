"""Real PG day-source boundary, without semantic retention or model requests."""
from datetime import timedelta
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5
import hashlib

import pytest
import sqlalchemy as sa

from tests.postgres.test_people_memory_overview import (
    people_memory_engine, selected_private_case, NOW,
)


def seed(case, name, *, seconds=10, conversation=None, **changes):
    from tests.postgres.test_people_memory_overview import _document
    from eva.application.memory_audience import MemoryAudience
    timestamp = NOW - timedelta(seconds=seconds)
    principal = changes.pop("principal", str(case.owner))
    conversation = conversation or f"dashboard:{case.binding}"
    message = "day-source:" + uuid5(NAMESPACE_URL, case.query.recall_id + name).hex
    project = changes.pop("project", None)
    ingress = {"kind":"conversation_ingress", "principal_id":principal,
        "conversation_id":conversation, "message_id":message, "text":"Actual ordinary source " + name}
    if project is not None:
        ingress["project_id"] = project
    ingress.update(changes.pop("ingress_changes", {}))
    case.timeline.create_thread(thread_id="conversation:"+message,
        checkpoint_id="conversation:"+message+":ingress",
        occurred_at=changes.pop("ingress_at", timestamp), data=ingress)
    options = dict(tenant_id=case.query.tenant_id, installation_id=case.query.installation_id,
        principal_id=principal, audience=MemoryAudience.private_person(principal_id=principal),
        project_id=project, conversation_id=conversation, source_ref=message,
        evidence_ref="conversation-reply:"+message,
        occurred_at=timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"), retention_mode="dream")
    options.update(changes)
    # The shared memory contract itself forbids model/legacy/project dream
    # sources. Seed their valid immediate representation to test exclusion.
    if project is not None or options.get("trust", "direct_user") != "direct_user":
        options["retention_mode"] = "immediate"
    options["document_id"] = f"{conversation}:exchange:{options['source_ref']}"
    document = replace(_document(name), **options)
    case.outbox.enqueue(document)
    return document


def test_latest_three_private_day_exchanges_are_available_before_dream(selected_private_case):
    case = selected_private_case
    seed(case, "older", seconds=40)
    telegram = seed(case, "telegram", seconds=30, principal="telegram:101", conversation="telegram:101")
    voice = seed(case, "voice", seconds=20, conversation="voice:verified-private")
    dashboard = seed(case, "dashboard", seconds=10)
    result = case.reader.recent_private_exchanges(now=NOW)
    assert [doc.document_id for doc, state in result] == [dashboard.document_id, voice.document_id, telegram.document_id]
    assert [state for doc, state in result] == ["day_context"] * 3
    assert all(doc.trust == "direct_user" and doc.retention_mode == "dream" for doc, state in result)
    assert len(case.reader.recent_private_exchanges(now=NOW, limit=1)) == 1
    # Reading does not retain, consume or rewrite the pending day sources.
    assert case.outbox._timeline.load(case.outbox._thread_id(dashboard.document_id)).phase == "memory_queued"


@pytest.mark.parametrize("reason", ["old", "future", "future_ingress", "old_ingress", "project", "model",
    "legacy", "immediate", "other_person", "wrong_alias_chat", "nonordinary_channel", "work_outcome",
    "erased", "expired", "ingress_erased", "not_selected", "missing_ingress"])
def test_day_projection_filters_unavailable_and_nonordinary_sources_before_limit(selected_private_case, reason):
    case = selected_private_case
    expected = seed(case, "admitted", seconds=20)
    options = {}
    if reason == "old": options["seconds"] = 24 * 3600 + 1
    if reason == "future": options["seconds"] = -1
    if reason == "future_ingress": options["ingress_at"] = NOW + timedelta(seconds=1)
    if reason == "old_ingress": options["ingress_at"] = NOW - timedelta(hours=25)
    if reason == "project": options["project"] = "project:private"
    if reason == "model": options["trust"] = "model_inference_unconfirmed"
    if reason == "legacy": options["trust"] = "legacy_unverified"
    if reason == "immediate": options["retention_mode"] = "immediate"
    if reason == "other_person": options["principal"] = "person:other"
    if reason == "wrong_alias_chat": options.update(principal="telegram:101", conversation="telegram:202")
    if reason == "nonordinary_channel": options["conversation"] = "initiative-task:private"
    if reason == "work_outcome": options["evidence_ref"] = "delegated-work-result:synthetic"
    if reason == "expired": options["retention_until"] = (NOW-timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if reason == "ingress_erased": options["ingress_changes"] = {"content_erasure": {"status":"erased"}, "text":None}
    if reason == "missing_ingress": options["source_ref"] = "absent:ordinary-source"
    rejected = seed(case, "excluded", **options)
    if reason == "erased":
        case.outbox.erase_content(principal_sha256=hashlib.sha256(str(case.owner).encode()).hexdigest(),
            document_id=rejected.document_id, operation_sha256="ad"*32, erased_at=NOW)
    if reason == "not_selected":
        timeline = case.outbox._timeline
        state = timeline.load(case.outbox._thread_id(rejected.document_id))
        timeline.transition(thread_id=state.thread_id, expected_revision=state.revision,
            checkpoint_id=state.thread_id+":test-not-selected", operation_id=state.operation_id,
            occurred_at=NOW, data={"reason":"synthetic ordinary no durable value"},
            event_type="MEMORY_RETAIN_NOT_SELECTED", phase="memory_not_selected", status="completed")
    assert [doc.document_id for doc,state in case.reader.recent_private_exchanges(now=NOW,limit=1)] == [expected.document_id]


def test_day_projection_rechecks_authority_and_source_hashes(selected_private_case, monkeypatch):
    import eva.adapters.postgres.people_memory_overview as module
    from eva.adapters.postgres.current_catalog import metadata
    case = selected_private_case
    seed(case, "current")
    canonical = module.canonical_sha256
    for kind in ("conversation_ingress", "hindsight_memory_document"):
        with monkeypatch.context() as patch:
            patch.setattr(module, "canonical_sha256", lambda value: "00"*32
                if value.get("data",{}).get("kind") == kind else canonical(value))
            with pytest.raises(RuntimeError, match="hash differs"):
                case.reader.recent_private_exchanges(now=NOW)
    bindings=metadata.tables["eva_core.principal_bindings"]
    with case.engine.begin() as connection:
        connection.execute(sa.update(bindings).where(bindings.c.tenant_id==case.authority.tenant_id,
            bindings.c.principal_binding_id==case.binding).values(status="revoked"))
    with pytest.raises(RuntimeError, match="binding is not current"):
        case.reader.recent_private_exchanges(now=NOW)


@pytest.mark.parametrize("limit", [0, 4, True])
def test_day_projection_has_fixed_three_source_limit(selected_private_case, limit):
    with pytest.raises(ValueError, match="limit"):
        selected_private_case.reader.recent_private_exchanges(now=NOW, limit=limit)


def test_exact_day_reference_rechecks_ingress_after_source_selection(selected_private_case, monkeypatch):
    import eva.adapters.postgres.people_memory_overview as module
    case = selected_private_case
    original = seed(case, "selected", seconds=20)
    reference = (original.principal_id, original.source_ref, original.evidence_ref)
    # New arrivals do not hide the exact selected source from revalidation.
    for i in range(4):
        seed(case, "newer-" + str(i), seconds=i+1)
    exact = case.reader.document_reference(*reference, now=NOW, recent_private_exchange=True)
    assert exact is not None and exact[1].document_id == original.document_id
    canonical = module.canonical_sha256
    with monkeypatch.context() as patch:
        patch.setattr(module, "canonical_sha256", lambda value: "00"*32
            if value.get("data",{}).get("message_id") == original.source_ref else canonical(value))
        with pytest.raises(RuntimeError, match="ingress hash differs"):
            case.reader.document_reference(*reference, now=NOW, recent_private_exchange=True)
    erased = seed(case, "ingress-erased-after-selection", ingress_changes={
        "content_erasure":{"status":"erased"},"text":None})
    reference = (erased.principal_id, erased.source_ref, erased.evidence_ref)
    # The generic history source still exists; the day-specific check must not
    # accept it once its original ingress was erased independently.
    assert case.reader.document_reference(*reference, now=NOW) is not None
    assert case.reader.document_reference(*reference, now=NOW, recent_private_exchange=True) is None
