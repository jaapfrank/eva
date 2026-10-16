"""Present existing private conversation/work bindings; never infer an origin."""
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4
import sqlalchemy as sa
from eva.application.conversation import ConversationTurn, ConversationConflict
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.current_catalog import delegated_work_runs as runs
from eva.adapters.postgres.private_work_return import private_episode
from eva.adapters.postgres.delegated_work_delivery import _read as read_destination
from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec, DelegatedInitiativeDigitalWorkSpec
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.mcp_autonomy import _SourceTurnLoader


def owner_turn(runtime):
    service = runtime._delegated_work_owner_service
    turn = ConversationTurn.private(runtime._runtime_tenant_id, runtime._runtime_installation_id,
        str(runtime._dashboard_owner_principal_id), f"dashboard:{runtime._dashboard_principal_binding_id}",
        "dashboard-work-context:" + uuid4().hex, "Werk bekijken",
        datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"))
    service.validate_authority(turn)
    return service, turn


def conversation_jobs(runtime):
    service, turn = owner_turn(runtime); store = service.store
    with store._engine.connect() as conn:
        transaction = conn.begin()
        try:
            store._scope(conn); store._validate_owner_binding(conn, **service._owner)
            ids = conn.execute(sa.select(runs.c.work_id).where(
                runs.c.tenant_id == store.scope.tenant_id, runs.c.installation_id == store.scope.installation_id,
                runs.c.owner_principal_id == service.owner_principal_id,
                runs.c.principal_binding_id == service.principal_binding_id,
                runs.c.spec_payload["audience"].astext == "private",
                runs.c.spec_payload["episode_id"].astext == private_episode(turn),
                runs.c.retention_until > service.now()).order_by(
                    sa.case((runs.c.state.in_(("admitted", "call_started")), 0), else_=1),
                    runs.c.created_at.desc(), runs.c.work_id.desc()).limit(21)).scalars().all()
        finally: transaction.rollback()
    jobs = []
    for identifier in ids[:20]:
        work = store.load(identifier, **service._owner)
        if work.spec.episode_id != private_episode(turn) or work.retention_until <= service.now():
            raise ConversationConflict("conversation work scope changed")
        item = service._view(work, include_result=False, turn=turn)
        item["origin_message_id"] = (work.spec.source.message_id
            if isinstance(work.spec, DelegatedDigitalWorkSpec)
            and not isinstance(work.spec, DelegatedInitiativeDigitalWorkSpec) else None)
        jobs.append(item)
    service.validate_authority(turn)
    return {"contract": "EvaConversationWork/v1", "conversation_id": turn.conversation_id,
            "jobs": jobs, "truncated": len(ids) > 20}


def work_context(runtime, work_id):
    service, dashboard = owner_turn(runtime); store = service.store
    work = store.load(work_id, **service._owner)
    if work.retention_until <= service.now(): raise LookupError("work expired")
    root = work; seen = set()
    while root.spec.generation > 1:
        if root.spec.work_id in seen or len(seen) >= 64: raise ValueError("invalid work ancestry")
        seen.add(root.spec.work_id)
        parent = store.load(root.spec.parent_task_id, **service._owner)
        if parent.spec.generation != root.spec.generation - 1 or parent.spec.episode_id != root.spec.episode_id:
            raise ValueError("resume ancestry differs")
        root = parent
    origin = {"state": "unavailable", "reason": "Er is geen toegankelijke exacte gespreksbron bij deze opdracht. De opdracht en het opgeslagen resultaat blijven afzonderlijk beschikbaar."}
    if not isinstance(root.spec, DelegatedInitiativeDigitalWorkSpec) and root.retention_until > service.now():
        source, ingress_hash = None, None
        if isinstance(root.spec, DelegatedDigitalWorkSpec):
            source = root.spec.source.model_dump(mode="json"); ingress_hash = root.spec.source_ingress_sha256
        else:
            with store._engine.connect() as conn:
                transaction = conn.begin()
                try:
                    store._scope(conn); store._validate_owner_binding(conn, **service._owner)
                    destination = read_destination(conn, store.scope, root.spec)
                finally: transaction.rollback()
            if destination is not None:
                if destination.owner_principal_id != service.owner_principal_id or destination.principal_binding_id != service.principal_binding_id:
                    raise ValueError("origin owner differs")
                source = {key: getattr(destination, "origin_" + key) for key in
                    ("tenant_id", "installation_id", "principal_id", "conversation_id", "message_id")}
                source["audience"] = {"kind": "private_person", "principal_id": source["principal_id"]}
        if source is not None:
            loader = _SourceTurnLoader(store._engine, SimpleNamespace(scope=service.conversation_scope, authorize_turn=service.validate_authority))
            try:
                original = loader(source)
                if ingress_hash is not None and canonical_sha256(PostgresConversationJournal._ingress_data(original)) != ingress_hash:
                    raise ConversationConflict("source ingress hash differs")
                reply = PostgresConversationJournal(store._engine, service.conversation_scope).committed_reply(original)
                loader(source)  # Erasure/current-authority recheck after the reply read.
                origin = {"state": "available", "conversation_id": original.conversation_id,
                    "message_id": original.message_id, "channel": "Telegram" if original.conversation_id.startswith("telegram:") else "Website" if original.conversation_id.startswith("dashboard:") else "Privégesprek",
                    "input": original.text[:4096], "input_truncated": len(original.text) > 4096,
                    "reply": None if reply is None else reply.text[:4096],
                    "reply_truncated": reply is not None and len(reply.text) > 4096,
                    "occurred_at": original.occurred_at, "root_work_id": root.spec.work_id}
            except ConversationConflict:
                # Forgotten/corrupt ingress is not reconstructed from task text.
                pass
    service.validate_authority(dashboard)
    return {"contract": "EvaOwnerWorkContext/v1", "work_id": work_id, "origin": origin}
