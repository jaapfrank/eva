"""Read original task ingress; never infer a WhatsApp recipient from model text."""
from eva.adapters.postgres.private_work_return import PrivateWorkReturnStore
from eva.contracts.canonical_json import canonical_sha256


def _legacy_source(service, turn, work):
    """Prove old spec ancestry from its existing deterministic admission key.

    Legacy research specs omit source fields. Their IDs bind exact ingress and
    action-step keys. Check the existing journal at the immutable creation time;
    never choose a message by recency, model text or a matching task title.
    """
    from dataclasses import replace
    from uuid import uuid5, NAMESPACE_URL
    import sqlalchemy as sa
    from eva.adapters.postgres.operational_timeline import operational_events as events, PostgresOperationalTimeline
    from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
    seen = set()
    while work.spec.generation > 1:
        if work.spec.work_id in seen or len(seen) >= 64:
            raise ValueError("invalid work ancestry")
        seen.add(work.spec.work_id)
        parent = service.store.load(work.spec.parent_task_id, **service._owner)
        if parent.spec.episode_id != work.spec.episode_id or parent.spec.generation != work.spec.generation - 1:
            raise ValueError("work origin changed")
        work = parent
    with service.store._engine.begin() as connection:
        PostgresOperationalTimeline(service.store._engine, service.conversation_scope)._scope(connection)
        rows = connection.execute(sa.select(events).where(
            events.c.tenant_id == service.conversation_scope.tenant_id,
            events.c.installation_id == service.conversation_scope.installation_id,
            events.c.event_type == 'INGRESS_COMMITTED', events.c.occurred_at == work.created_at,
            events.c.payload['data']['conversation_id'].astext == turn.conversation_id,
            events.c.payload['data']['principal_id'].astext == turn.principal_id).limit(65)).mappings().all()
    if len(rows) > 64:
        raise ValueError("ambiguous source window")
    matches = []
    for row in rows:
        if canonical_sha256(row['payload']) != bytes(row['payload_sha256']).hex():
            raise ValueError("source journal integrity differs")
        data = row['payload']['data']
        original = replace(turn, message_id=data['message_id'], text=data['text'],
            occurred_at=row['occurred_at'].strftime('%Y-%m-%dT%H:%M:%SZ'))
        keys = [service.command_key(original)]
        for step in range(128):
            request = PostgresDelegatedWorkActions._thread_id(original, step)
            keys.append(canonical_sha256({'tenant': original.tenant_id,
                'installation': original.installation_id, 'owner': original.principal_id,
                'conversation': original.conversation_id, 'message': original.message_id, 'request': request}))
        if any('work:eva-chat:' + str(uuid5(NAMESPACE_URL, key)) == work.spec.work_id for key in keys):
            matches.append({key: getattr(original, key) for key in
                ('tenant_id', 'installation_id', 'principal_id', 'conversation_id', 'message_id')})
    if len(matches) != 1:
        raise ValueError("exact original message not proven")
    return matches[0]


def original_result_message(runtime, turn, work_id):
    service = runtime._delegated_work_owner_service
    service.validate_authority(turn)
    if work_id.startswith(("document-task:", "scheduled-work:")):
        # These sources retain the admission binding; use its exact source ID.
        item = PrivateWorkReturnStore(service).documents.load(work_id)
        source = item.binding.get("source") if item is not None else None
    else:
        work = service.store.load(work_id, **service._owner)
        source = getattr(work.spec, "source", None)
        if source is not None:
            source = source.model_dump(mode="json")
        else:
            source = _legacy_source(service, turn, work)
    if (not isinstance(source, dict) or any(source.get(key) != getattr(turn, key)
            for key in ('tenant_id', 'installation_id', 'principal_id', 'conversation_id'))
            or not isinstance(source.get("message_id"), str)
            or not source["message_id"].startswith(turn.conversation_id + ":")):
        raise ValueError("original WhatsApp message binding unavailable")
    from types import SimpleNamespace
    from eva.providers.mcp_autonomy import _SourceTurnLoader
    source = source | {'audience': {'kind': 'private_person', 'principal_id': turn.principal_id}}
    _SourceTurnLoader(service.store._engine, SimpleNamespace(scope=service.conversation_scope,
        authorize_turn=service.validate_authority))(source)
    service.validate_authority(turn)
    return source["message_id"].removeprefix(turn.conversation_id + ":")
