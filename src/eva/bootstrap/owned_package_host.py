"""Host-only package ancestry and independent review admission in Eva's one worker.

The host supplies no owner text or new task permissions. Review is a separate
native initiative with local tools, bound to the original retained work/source.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import sqlalchemy as sa

from eva.contracts.canonical_json import canonical_sha256


def _owner_binding(spec, scope):
    return canonical_sha256({'owner_principal_id': spec.owner_principal_id,
        'principal_binding_id': spec.principal_binding_id,
        'queue_tenant_id': str(scope.tenant_id), 'queue_installation_id': str(scope.installation_id)})


def _source_service(authority, store):
    """Reuse current private-channel binding rules, including the Telegram alias."""
    from eva.bootstrap.runtime import RuntimeSettings
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    from eva.bootstrap.telegram_ingress import load_principal_bindings
    settings = RuntimeSettings.from_environment()
    owner = authority.owner
    def alias():
        return load_principal_bindings(settings.telegram_principal_bindings_file).private_bridge_principal(
            principal_id=str(owner._owner), principal_binding_id=owner._binding)
    from eva.bootstrap.delegated_work_delivery import private_destination_loader, delivery_status_loader
    bindings = lambda: load_principal_bindings(settings.telegram_principal_bindings_file)
    delivery = ({'delivery_destination_loader': private_destination_loader(owner_principal_id=owner._owner,
        principal_binding_id=owner._binding, bindings_loader=bindings),
        'delivery_status_loader': delivery_status_loader(store, owner_principal_id=owner._owner,
        principal_binding_id=owner._binding, bindings_loader=bindings)}
        if settings.telegram_principal_bindings_file else {})
    return DelegatedWorkOwnerService(store, **delivery, owner_principal_id=owner._owner,
        principal_binding_id=owner._binding,
        conversation_scope=external_timeline_scope(settings.background_tenant_id,
            settings.background_installation_id, 'package conversation source scope required'),
        private_bridge_principal_loader=(alias if settings.telegram_principal_bindings_file else None))


def _execution_leaf(store, spec, owner):
    from eva.adapters.postgres.current_catalog import delegated_work_runs as runs
    from eva.operations.component_application_runtime import _execution_chain
    with store._engine.begin() as connection:
        store._scope(connection)
        rows = connection.execute(sa.select(runs).where(runs.c.tenant_id == store.scope.tenant_id,
            runs.c.installation_id == store.scope.installation_id, runs.c.owner_principal_id == owner._owner,
            runs.c.spec_payload['execution_id'].astext == spec.execution_id)).mappings().all()
    if any(bytes(row['spec_sha256']).hex() != canonical_sha256(row['spec_payload']) for row in rows):
        raise ValueError('package execution chain source changed')
    scope = canonical_sha256({k: getattr(spec.source, k) for k in ('tenant_id', 'installation_id', 'principal_id')})
    chain = _execution_chain(rows, spec.work_id, scope, canonical_sha256, strict=True)
    if any(row['stop_requested_at'] is not None or row['state'] == 'stopped' for row in chain):
        raise ValueError('package original execution was stopped')
    return store.load(chain[-1]['work_id'], owner_principal_id=owner._owner, principal_binding_id=owner._binding)


def current_package_origin(authority, origin, *, service=None, store=None, _connection=None, leaf=False):
    from eva.adapters.postgres.current_catalog import delegated_work_runs as runs
    from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    from eva.adapters.postgres.work_source_retention import TaskSourceLoader
    from eva.providers.mcp_autonomy import _SourceTurnLoader
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    owner = authority.owner
    store = store or PostgresDelegatedWorkStore(owner._engine, owner._authority)
    required = {"task_id", "execution_id", "scope_sha256", "source_ingress_sha256", "spec_sha256", "workspace_key", "owner_binding_sha256"}
    if not isinstance(origin, dict) or set(origin) != required:
        raise ValueError("exact owned package task origin required")
    record = store.load(origin["task_id"], owner_principal_id=owner._owner, principal_binding_id=owner._binding)
    spec = record.spec
    if origin['owner_binding_sha256'] != _owner_binding(spec, store.scope):
        raise ValueError('package canonical owner binding differs')
    if service is None and spec.contract != 'DelegatedInitiativeDigitalWork/v1':
        service = _source_service(authority, store)
    now = service.now() if service is not None else datetime.now(UTC).replace(microsecond=0)
    if origin['scope_sha256'] != canonical_sha256({key: getattr(spec.source, key) for key in
            ('tenant_id', 'installation_id', 'principal_id')}):
        raise ValueError('package original source scope differs')
    if (spec.sha256 != origin["spec_sha256"] or spec.execution_id != origin["execution_id"]
            or spec.source_ingress_sha256 != origin["source_ingress_sha256"]
            or record.stop_requested_at is not None or record.state == "stopped"
            or record.retention_until <= now):
        raise ValueError("package source task changed, stopped or expired")
    expected_workspace = canonical_sha256({"execution_id": spec.execution_id,
        "source": spec.source.model_dump(mode="json"), "source_ingress_sha256": spec.source_ingress_sha256})
    if expected_workspace != origin["workspace_key"]:
        raise ValueError("package workspace does not belong to this task")
    # Queue RLS is a separate authority domain. Never switch the caller's
    # initiative transaction to queue scope while it still owns native locks.
    current = _execution_leaf(store, spec, owner)
    if current.retention_until <= now:
        raise ValueError('package execution retention expired')
    if spec.contract == "DelegatedInitiativeDigitalWork/v1":
        if current.state in {'admitted', 'call_started'}:
            guard, turn = authority.bind_work(current.spec)
            guard.check(turn)
        elif current.state == 'failed':
            policy, digest = authority.policy()
            checkpoint = authority.timeline.load(current.spec.origin.thread_id)
            if (checkpoint.phase != 'initiative_blocked' or checkpoint.status != 'needs_help'
                    or digest != authority._work_mandate(current.spec)
                    or policy.get('digital_work', {}).get('worker_profile_release_sha256') != current.spec.worker_profile_release_sha256):
                raise ValueError('failed native package task has no current waiting source')
            turn = authority.work_memory_source(current, now=now)  # Read-only; never new tool authority.
        else:
            _, turn = authority.reviewed_work_current(current.spec.work_id, now=now)
        journal = PostgresConversationJournal(owner._engine, owner._scope)
    else:
        loader = TaskSourceLoader(service, _SourceTurnLoader(owner._engine,
            SimpleNamespace(scope=service.conversation_scope, authorize_turn=service.validate_authority)))
        turn = loader.for_task(current.spec)
        journal = PostgresConversationJournal(owner._engine, service.conversation_scope)
    if canonical_sha256(journal._ingress_data(turn)) != spec.source_ingress_sha256:
        raise ValueError("package original source differs")
    journal.ensure_running(turn)
    return (current if leaf else record), turn


def _metadata(authority, service, store, task_id, *, include_link=True):
    from eva.adapters.postgres.current_catalog import delegated_work_runs as runs
    owner = authority.owner
    record = store.load(task_id, owner_principal_id=owner._owner, principal_binding_id=owner._binding)
    spec = record.spec
    identity = {"execution_id": spec.execution_id, "source": spec.source.model_dump(mode="json"),
                "source_ingress_sha256": spec.source_ingress_sha256}
    origin = {"task_id": task_id, "execution_id": spec.execution_id,
              'owner_binding_sha256': _owner_binding(spec, store.scope),
              "scope_sha256": canonical_sha256({key: getattr(spec.source, key) for key in
                  ("tenant_id", "installation_id", "principal_id")}),
              "source_ingress_sha256": spec.source_ingress_sha256, "spec_sha256": spec.sha256,
              "workspace_key": canonical_sha256(identity)}
    current, _ = current_package_origin(authority, origin, service=service, store=store, leaf=True)
    with owner._engine.begin() as connection:
        store._scope(connection)
        row = connection.execute(sa.select(runs.c.lease_owner, runs.c.lease_expires_at).where(
            runs.c.work_id == current.spec.work_id)).one()
    extra = {}
    if spec.contract == "DelegatedInitiativeDigitalWork/v1":
        events = authority.timeline.events("intent:" + spec.origin.intent_id)
        if events and events[0].event_type == "PERSONAL_INTENT_ADOPTED":
            data = events[0].payload["data"]
            if "owned_package_origin" in data:
                extra = {"review_origin": data["owned_package_origin"],
                         "review_package_sha256": data["owned_package_sha256"]}
    from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
    if (include_link and type(spec) is DelegatedDigitalWorkSpec and spec.generation == 1
            and spec.parent_task_id.startswith("work:") and current.state == "call_started"):
        predecessor = store.load(spec.parent_task_id, owner_principal_id=owner._owner,
                                 principal_binding_id=owner._binding)
        if (type(predecessor.spec) is not DelegatedDigitalWorkSpec
                or any(getattr(predecessor.spec.source, name) != getattr(spec.source, name)
                       for name in ("tenant_id", "installation_id", "principal_id", "conversation_id"))):
            raise ValueError("linked package source differs")
        linked = _metadata(authority, service, store, spec.parent_task_id, include_link=False)
        if (linked["latest_work_id"] != linked["task_id"] or linked["active_lease"]
                or linked["state"] not in {"failed", "ambiguous", "succeeded"}
                or not linked["can_publish_package"]):
            raise ValueError("linked package source must be closed")
        extra["linked_package_origin"] = {name: linked[name] for name in origin}
    return {**origin, **extra, "task_text": spec.task,
            "session_key": canonical_sha256({**identity, "work_id": task_id}),
            'latest_work_id': current.spec.work_id,
            'can_publish_package': 'apply_reviewed_component_improvement' in spec.grant.capability_ids,
            "state": current.state, "active_lease": bool(row.lease_owner and row.lease_expires_at
                and row.lease_expires_at > datetime.now(UTC))}



def _continue_private_package(authority, service, store, request):
    """Wake the original private task once per changed host fact, never owner text."""
    from eva.adapters.postgres.owned_package_context import REASONS, record_package_notice
    from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
    from eva.adapters.postgres.operational_timeline import operational_checkpoints
    from eva.operations.owned_package import key
    origin = request['origin']
    if request['scope_sha256'] != origin['scope_sha256'] or request['reason'] not in REASONS:
        raise ValueError('package continuation source or reason differs')
    review_id, package = key(request['review_id']), key(request['package_sha256'])
    notification = canonical_sha256({'origin': origin, 'review_id': review_id,
                                     'package_sha256': package, 'reason': request['reason']})
    # Serialize only this idempotent admission, without retaining a transaction
    # across source validation, timeline writes or the scheduler's own commit.
    lock_key = 'owned-package-continuation:' + notification
    with store._engine.connect() as lock:
        if not lock.scalar(sa.text('SELECT pg_try_advisory_lock(hashtextextended(:key, 0))'), {'key': lock_key}):
            return {'state': 'waiting_for_admission'}
        lock.commit()
        try:
            current, turn = current_package_origin(authority, origin, service=service, store=store, leaf=True)
            from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
            if type(current.spec) is not DelegatedDigitalWorkSpec:
                return {'state': 'native_continuation_requires_current_native_admission'}
            scheduler = ScheduledOwnerWork(service)
            import hashlib
            request_key = 'owned-package-host-status:' + notification
            thread_id = scheduler.prefix + hashlib.sha256(request_key.encode()).hexdigest()
            with store._engine.begin() as connection:
                scheduler._authorize(connection)
                existing = connection.execute(scheduler._rows().where(
                    operational_checkpoints.c.thread_id == thread_id)).one_or_none()
                if existing is not None:
                    return {'state': 'scheduled', 'schedule': scheduler._view(connection, existing)}
            if (current.state in {'admitted', 'call_started'} or current.lease_owner is not None
                    and current.lease_expires_at is not None and current.lease_expires_at > service.now()):
                return {'state': 'waiting_for_original_task'}
            if current.state != 'failed':
                return {'state': 'original_task_not_resumable', 'work_state': current.state}
            record_package_notice(store._engine, current.spec, notification_key=notification,
                review_id=review_id, package_sha256=package, reason=request['reason'], now=service.now())
            # The actual source turn remains unchanged. Only the admission clock
            # is fresh; ordinary resume/effect/stop validation remains authoritative.
            due = (service.now() + timedelta(seconds=2)).isoformat().replace('+00:00', 'Z')
            result = scheduler.schedule(turn, request_key=request_key,
                request={'work_id': current.spec.work_id, 'run_at': due}, resume=True)
            return {'state': 'scheduled', 'schedule': result}
        finally:
            lock.execute(sa.text('SELECT pg_advisory_unlock(hashtextextended(:key, 0))'), {'key': lock_key})
            lock.commit()


def handle_package_request(request, *, authority=None, service=None, store=None):
    from eva.bootstrap.runtime_repair_host import _context
    from eva.adapters.postgres.initiative_adoption import adopt_private_initiative
    with _context(authority, service, store) as (authority, service, store, settings):
        if settings is not None:
            service = _source_service(authority, store)
        if request.get('action') == 'continue' and set(request) == {
                'action', 'origin', 'scope_sha256', 'review_id', 'package_sha256', 'reason'}:
            return _continue_private_package(authority, service, store, request)
        if request.get('action') == 'owner_current' and set(request) == {'action', 'scope_sha256'}:
            owner = authority.owner
            identities = [(owner._scope, str(owner._owner)), (service.conversation_scope, str(owner._owner))]
            loader = getattr(service, '_private_bridge_principal_loader', None)
            alias = loader() if loader is not None else None
            if alias is not None:
                identities.append((service.conversation_scope, alias))
            scopes = {canonical_sha256({'tenant_id': str(scope.tenant_id), 'installation_id': str(scope.installation_id),
                                        'principal_id': principal}) for scope, principal in identities}
            if request['scope_sha256'] not in scopes:
                raise ValueError('current package owner channel scope differs')
            with owner._engine.begin() as connection:
                owner._authorize(connection, service.now())
            binding = canonical_sha256({'owner_principal_id': str(owner._owner),
                'principal_binding_id': str(owner._binding), 'queue_tenant_id': str(store.scope.tenant_id),
                'queue_installation_id': str(store.scope.installation_id)})
            return {'current': True, 'owner_binding_sha256': binding}
        if request.get("action") == "metadata" and set(request) == {"action", "task_id", "scope_sha256"}:
            value = _metadata(authority, service, store, request["task_id"])
            if value["scope_sha256"] != request["scope_sha256"]:
                raise ValueError("package scope differs")
            return value
        if request.get("action") == "review_metadata" and set(request) == {
                "action", "origin", "package_sha256", "scope_sha256"}:
            from eva.adapters.postgres.current_catalog import delegated_work_runs as runs
            current_package_origin(authority, request["origin"], service=service, store=store)
            identity = canonical_sha256({"owned_package_review": request["package_sha256"],
                                         "origin": request["origin"]})
            with authority.owner._engine.begin() as connection:
                store._scope(connection)
                work_id = connection.execute(sa.select(runs.c.work_id).where(
                    runs.c.spec_payload["origin"]["intent_id"].astext == "personal-interest:" + identity)
                    .order_by(sa.cast(runs.c.spec_payload["generation"].astext, sa.Integer).desc()).limit(1)).scalar_one_or_none()
            return {"state": "not_admitted"} if work_id is None else _metadata(authority, service, store, work_id)
        if request.get('action') == 'authorize_network' and set(request) == {
                'action', 'origin', 'hosts', 'scope_sha256'}:
            from eva.providers.mcp_autonomy import McpAutonomyProviderFactory
            from eva.providers.mcp_owner_authority import McpOwnerAuthority
            from eva.providers.private_owner_authority import configured_private_owner_resolver
            from eva.operations.owned_package import key
            origin = request['origin']
            if request['scope_sha256'] != origin['scope_sha256']:
                raise ValueError('package network source scope differs')
            record, turn = current_package_origin(authority, origin, service=service, store=store)
            hosts = request['hosts']
            import re
            if (not isinstance(hosts, list) or len(hosts) > 16 or len(set(hosts)) != len(hosts)
                    or any(not isinstance(host, str) or re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', host) is None
                           for host in hosts)):
                raise ValueError('exact package network hosts required')
            digest = None
            if hosts:
                if not {'connect_service', 'use_connector_tool'} <= set(record.spec.grant.capability_ids):
                    raise ValueError('original package task has no connection/invocation authority')
                mcp = McpAutonomyProviderFactory.load_settings()
                if mcp is None:
                    raise ValueError('existing owner connector authority is unavailable')
                policy = McpOwnerAuthority(store._engine, mcp.mandate_file, mcp.managed_endpoints_file,
                    private_owner_resolver=configured_private_owner_resolver(store._engine,
                        tenant_id=turn.tenant_id, installation_id=turn.installation_id))
                for host in hosts:
                    policy.authorize_endpoint(turn, 'https://' + host + '/')
                digest = policy.configuration_sha256
            return {'contract': 'EvaOwnedPackageNetworkAuthority/v1', 'origin': origin,
                    'hosts': hosts, 'configuration_sha256': digest, 'current': True}
        if request.get("action") != "admit_review" or set(request) != {
                "action", "origin", "package_sha256", "scope_sha256"}:
            raise ValueError("unknown host package request")
        from eva.operations.owned_package import key
        package = key(request["package_sha256"])
        origin = request["origin"]
        if request["scope_sha256"] != origin["scope_sha256"]:
            raise ValueError("package review scope differs")
        current_package_origin(authority, origin, service=service, store=store)
        if settings is not None:
            from eva.bootstrap.delegated_digital_composition import build_digital_runner
            service.digital_profile = build_digital_runner(settings, store).profile
        authority.digital_grant(service.digital_profile)
        identity = canonical_sha256({"owned_package_review": package, "origin": origin})
        owner = authority.owner
        task = ("Beoordeel het Eva-pakket onafhankelijk op de oorspronkelijke gebruikersopdracht, "
            "de broncode en de opgegeven rechten. Je bent geen bouwer en mag de kandidaat of "
            "zijn criteria niet wijzigen. Roep eerst get_reviewed_component_application aan met "
            "review_id=" + package + ". De host zet jouw exacte invoer onder /workspace/package-review/. "
            "Lees task.json, descriptor.json en source/. Maak eigen gerichte controles voor werkelijk "
            "gedrag en rechten; voer deze lokaal uit. Eigen tests van de bouwer zijn onvoldoende. "
            "Bewaar package/review.json met contract=EvaOwnedPackageReview/v1, "
            "package_sha256=" + package + ", decision=accept of reject, reason (tekst), "
            "checks (niet-lege lijst met name, passed en evidence), rights_verified (boolean). "
            "Bewaar jouw eigen onafhankelijk uitvoerbare test als package/verify.py; deze draait "
            "op de exacte kandidaat in een afzonderlijke netwerkloze hostcontainer. Gebruik "
            "/package voor alleen-lezen kandidaatbestanden en /review voor je eigen invoer. "
            "Accepteer uitsluitend als alle eigen controles slagen, de oorspronkelijke uitkomst "
            "wordt geleverd en netwerk/geheimen/getoonde tools exact begrensd zijn. Ontbrekende "
            "echte configuratie is een expliciete resterende voorwaarde, geen geslaagde verbinding. "
            "Geen installatie, externe acties of wijzigingen aan de toepassing. Rond daarna af.")
        payload = {"principal_id": str(owner._owner), "source_principal_id": str(owner._owner),
            "conversation_id": "initiative-task:" + identity, "source_message_id": "initiative-execute:" + identity,
            "intent_id": "personal-interest:" + identity, "origin": "eva_initiative", "execution_mode": "autonomous_task",
            "adoption_source": "automatic_owned_package_review", "unprompted": True, "kind": "personal_interest",
            "title": "Eigen pakket onafhankelijk beoordelen (" + package[:12] + ")",
            "motivation": "Een werkelijk door Eva gemaakt pakket afzonderlijk controleren vóór gebruik.",
            "next_exploration": task, "owned_package_origin": origin, "owned_package_sha256": package,
            "authority": "current_original_owned_package_task_required"}
        with owner._engine.begin() as connection:
            # Lost admission replies observe this exact existing initiative;
            # they never create a second reviewer or reactivate a stopped one.
            from eva.adapters.postgres.operational_timeline import operational_events
            authority.timeline._scope(connection)
            prior = connection.execute(sa.select(operational_events.c.payload).where(
                operational_events.c.tenant_id == owner._scope.tenant_id,
                operational_events.c.installation_id == owner._scope.installation_id,
                operational_events.c.thread_id == 'intent:' + payload['intent_id'],
                operational_events.c.event_type == 'PERSONAL_INTENT_ADOPTED')).scalar_one_or_none()
            if prior is not None:
                if prior.get('data') != payload:
                    raise ValueError('package review admission identity changed')
                return {'status': 'adopted', 'intent_id': payload['intent_id'],
                        'thread_id': 'intent:' + payload['intent_id'], 'already_exists': True}
            return adopt_private_initiative(connection, owner._scope, payload, now=service.now(),
                revalidate=lambda: current_package_origin(authority, origin, service=service, store=store,
                                                          _connection=connection))
