"""Autonomous repair goals on Eva's existing operational and initiative journal."""
from datetime import UTC, datetime
from contextlib import nullcontext
import hashlib
import os
import re

import sqlalchemy as sa

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.initiative_adoption import adopt_private_initiative
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline, insert_initial_timeline_thread,
    operational_checkpoints, operational_events, transition_timeline_thread,
)
from eva.application.self_repair import repair_task
from eva.contracts.canonical_json import canonical_sha256


HOST_SCHEDULED_FAILURE_SOURCE = 'EvaHostScheduledFailureSource/v1'
_SHA256 = re.compile(r'[0-9a-f]{64}')
_PRIVATE_SOURCE_FIELDS = {'principal_id', 'conversation_id', 'message_id', 'ingress_sha256'}
_HOST_SOURCE_FIELDS = {'contract', 'observation_sha256', 'privacy_source',
                       'technical_provenance', 'technical_provenance_sha256'}
_PROVENANCE_DIGESTS = {'scheduled_marker_sha256', 'predecessor_spec_sha256',
                      'recovery_stop_sha256', 'source_ingress_data_sha256'}
_PROVENANCE_FIELDS = _PROVENANCE_DIGESTS | {'schedule_id', 'recovery_stop_thread', 'work_id'}


def _current_privacy_principal(owner, source):
    principal = str(owner._owner)
    if source['principal_id'] == principal:
        return True
    if source['conversation_id'] != source['principal_id']:
        return False
    bindings_file = os.environ.get('EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE', '').strip()
    if not bindings_file:
        return False
    from eva.adapters.credentials.telegram_bindings import load_principal_bindings

    try:
        alias = load_principal_bindings(bindings_file).private_bridge_principal(
            principal_id=principal, principal_binding_id=owner._binding
        )
    except (OSError, ValueError, RuntimeError):
        return False
    return alias is not None and alias == source['principal_id']


def _host_source_current(connection, owner, source, *, observation_sha256, source_sha256):
    from eva.adapters.postgres.current_catalog import delegated_work_runs

    if set(source) != _HOST_SOURCE_FIELDS or source['observation_sha256'] != observation_sha256:
        return False
    privacy = source['privacy_source']
    proof = source['technical_provenance']
    principal = str(owner._owner)
    if (not isinstance(privacy, dict) or set(privacy) != _PRIVATE_SOURCE_FIELDS
            or not _current_privacy_principal(owner, privacy) or not isinstance(proof, dict)
            or set(proof) != _PROVENANCE_FIELDS
            or any(not isinstance(proof[key], str) or not _SHA256.fullmatch(proof[key])
                   for key in _PROVENANCE_DIGESTS)
            or any(not isinstance(proof[key], str) or not 1 <= len(proof[key]) <= 240
                   for key in ('schedule_id', 'recovery_stop_thread', 'work_id'))
            or source['technical_provenance_sha256'] != canonical_sha256(proof)
            or source_sha256 != canonical_sha256(source)
            or not repair_source_current(connection, owner._scope, privacy)):
        return False
    scope = owner._authority
    c, e, w = operational_checkpoints.c, operational_events.c, delegated_work_runs.c
    spec = w.spec_payload
    with owner._engine.begin() as evidence:
        evidence.exec_driver_sql("SET TRANSACTION READ ONLY")
        PostgresOperationalTimeline(owner._engine, scope)._scope(evidence)
        work = evidence.scalar(sa.select(spec).where(
            w.tenant_id == scope.tenant_id, w.installation_id == scope.installation_id,
            w.work_id == proof['work_id'], w.spec_sha256 == bytes.fromhex(proof['predecessor_spec_sha256']),
            w.owner_principal_id == owner._owner, w.principal_binding_id == owner._binding,
            w.state == 'ambiguous', w.error_code == 'provider_unavailable',
            w.lease_owner.is_(None), w.lease_expires_at.is_(None), w.stop_requested_at.is_(None),
            spec['source']['tenant_id'].astext == owner._tenant_id,
            spec['source']['installation_id'].astext == owner._installation_id,
            *(spec['source'][key].astext == privacy[key]
              for key in ('principal_id', 'conversation_id', 'message_id')),
        ))
        if (not isinstance(work, dict) or canonical_sha256(work) != proof['predecessor_spec_sha256']
                or work.get('source_ingress_sha256') != proof['source_ingress_data_sha256']
                or not isinstance(work.get('execution_id'), str) or not work['execution_id']):
            return False
        execution = work['execution_id']
        schedule = f'scheduled-work:{principal}:provider-recovery:' + hashlib.sha256(execution.encode()).hexdigest()
        stop = 'private-work-recovery-stop:' + canonical_sha256({
            'execution_id': execution, 'source_ingress_sha256': proof['source_ingress_data_sha256'],
        })
        if proof['schedule_id'] != schedule or proof['recovery_stop_thread'] != stop:
            return False
        successor = operational_events.alias('repair_provenance_successor').c
        events = evidence.execute(sa.select(
            e.thread_id, e.event_type, e.checkpoint_id, e.payload, e.payload_sha256
        ).join(operational_checkpoints, sa.and_(
            c.tenant_id == e.tenant_id, c.installation_id == e.installation_id,
            c.thread_id == e.thread_id, c.checkpoint_id == e.checkpoint_id, c.updated_at == e.occurred_at,
        )).where(
            e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
            e.sequence == 1, c.revision == 1, c.operation_id.is_(None),
            sa.or_(sa.and_(e.thread_id == schedule, c.phase == 'blocked', c.status == 'needs_help'),
                   sa.and_(e.thread_id == stop, c.phase == 'stopped', c.status == 'completed')),
            ~sa.exists(sa.select(1).where(successor.tenant_id == e.tenant_id,
                successor.installation_id == e.installation_id, successor.thread_id == e.thread_id,
                successor.sequence > 1)),
        )).all()
    events = {event.thread_id: event for event in events}
    if set(events) != {schedule, stop}:
        return False
    marker, stopped = events[schedule], events[stop]
    data = marker.payload.get('data', {})
    return (
        marker.event_type == 'SCHEDULED_WORK_BLOCKED' and marker.checkpoint_id == schedule + ':blocked'
        and canonical_sha256(marker.payload) == bytes(marker.payload_sha256).hex() == proof['scheduled_marker_sha256']
        and data.get('work_id') == proof['work_id'] and data.get('operation') == 'resume'
        and data.get('automatic_retry') is False
        and stopped.event_type == 'PRIVATE_WORK_RECOVERY_STOPPED' and stopped.checkpoint_id == stop + ':stopped'
        and canonical_sha256(stopped.payload) == bytes(stopped.payload_sha256).hex() == proof['recovery_stop_sha256']
        and stopped.payload.get('data') == {
            'execution_id': execution, 'source_ingress_sha256': proof['source_ingress_data_sha256'],
            'work_id': proof['work_id'], 'spec_sha256': proof['predecessor_spec_sha256'],
        }
    )


def _incident_source_current(connection, owner, data):
    source = data['source']
    if isinstance(source, dict) and source.get('contract') == HOST_SCHEDULED_FAILURE_SOURCE:
        return _host_source_current(connection, owner, source,
            observation_sha256=data['fingerprint'], source_sha256=data['source_sha256'])
    return repair_source_current(connection, owner._scope, source)


def current_repair_binding(authority, incident_id, *, now, _connection=None):
    """Host-only incident evidence for assessment/application, even after work ends.

    A completed development job grants no installation authority. The original
    owner, mandate, source, erasure and personal stop remain authoritative.
    This returns metadata only; no exception message or private turn is copied.
    """
    import re
    if not isinstance(incident_id, str) or not re.fullmatch(r'self-repair:[0-9a-f]{64}', incident_id):
        raise ValueError('invalid repair incident identity')
    owner = authority.owner
    policy = authority._read()
    digest = canonical_sha256({k: v for k, v in policy.items() if k != 'enabled'})
    if policy.get('self_repair', {}).get('enabled') is not True:
        raise ValueError('automatic repair is disabled')
    if _connection is not None and (not _connection.in_transaction() or _connection.in_nested_transaction()):
        raise ValueError('repair binding requires a root transaction')
    with (owner._engine.begin() if _connection is None else nullcontext(_connection)) as connection:
        owner._authorize(connection, now)
        c, e = operational_checkpoints.c, operational_events.c
        row = connection.execute(sa.select(operational_checkpoints, e.payload.label('original'),
            e.payload_sha256.label('original_sha')).join(operational_events,
                sa.and_(e.tenant_id == c.tenant_id, e.installation_id == c.installation_id,
                        e.thread_id == c.thread_id, e.event_type == 'SELF_REPAIR_OBSERVED')).where(
            c.tenant_id == owner._scope.tenant_id, c.installation_id == owner._scope.installation_id,
            c.thread_id == incident_id)).one_or_none()
        if (row is None or row.phase != 'repair_investigating' or row.status != 'waiting_for_proof'
                or canonical_sha256(row.original) != bytes(row.original_sha).hex()):
            raise ValueError('repair incident is not current')
        data = row.original['data']
        # Serialize revocation with this read, as native dispatch already does.
        mandate = connection.execute(sa.select(operational_checkpoints).where(
            c.tenant_id == owner._scope.tenant_id, c.installation_id == owner._scope.installation_id,
            c.thread_id == authority.thread).with_for_update()).one()
        parent = connection.execute(owner._query().where(
            c.thread_id == 'intent:' + data['intent_id']).with_for_update(of=operational_checkpoints)
        ).one_or_none()
        if parent is None or parent.phase == 'personal_stopped' or parent.status == 'stopped':
            raise ValueError('repair initiative was stopped')
        current_policy = authority._read()
        if (mandate.phase == 'initiative_mandate_revoked'
                or not current_policy['enabled'] or current_policy.get('self_repair', {}).get('enabled') is not True
                or canonical_sha256({k: v for k, v in current_policy.items() if k != 'enabled'}) != digest
                or authority._admitted_digest(connection) != digest or data['mandate_sha256'] != digest
                or not _incident_source_current(connection, owner, data)):
            raise ValueError('repair source or mandate changed')
        original = parent.adoption['data']
        if (original.get('repair_incident_id') != incident_id
                or original.get('repair_source') != data['source']
                or original.get('repair_mandate_sha256') != digest):
            raise ValueError('repair initiative source differs')
        # A completed development result stays immutable. Owner stop then uses
        # the existing native-turn marker to cancel deferred repair application.
        key = data['intent_id'].removeprefix('personal-interest:')
        journal = PostgresConversationJournal(owner._engine, owner._scope)
        identity = journal._stop_identity(str(owner._owner), 'initiative-task:' + key,
                                           'initiative-execute:' + key)
        journal._stop_fence(connection, identity)
        if journal._marker_exists(connection, journal._stop_thread_id(identity),
                                   'CONVERSATION_STOP_REQUESTED', identity):
            raise ValueError('repair development source was stopped')
        from eva.application.self_repair import RepairObservation
        raw = data['observation']
        observation = RepairObservation(raw['error_type_sha256'], tuple(
            (f['path'], f['function'], f['source_sha256']) for f in raw['frames']))
        if observation.fingerprint != data['fingerprint']:
            raise ValueError('repair observation differs')
        identity = {'tenant_id': owner._tenant_id, 'installation_id': owner._installation_id,
                    'principal_id': str(owner._owner)}
        return {'contract': 'EvaCurrentRepairBinding/v1', 'incident_id': incident_id,
                'incident_event_sha256': bytes(row.original_sha).hex(),
                'scope_sha256': canonical_sha256(identity), 'mandate_sha256': digest,
                'source_sha256': data['source_sha256'], 'observation': observation.to_payload(),
                'intent_id': data['intent_id']}


def repair_source_current(connection, scope, source):
    """Fence the original private source against STOP and explicit erasure."""
    if (not isinstance(source, dict) or set(source) != _PRIVATE_SOURCE_FIELDS
            or any(not isinstance(value, str) or not value for value in source.values())
            or not _SHA256.fullmatch(source['ingress_sha256'])):
        return False
    e = operational_events.c
    row = connection.execute(sa.select(e.event_id, e.payload, e.payload_sha256).where(
        e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
        e.thread_id == 'conversation:' + source['message_id'],
        e.event_type == 'INGRESS_COMMITTED')).one_or_none()
    if row is None or canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
        return False
    # Runtime keeps append-only event privileges. This narrow scoped function
    # only locks an existing ingress; erasure's normal UPDATE must wait for this
    # admission transaction. It grants no content mutation or unscoped read.
    if connection.execute(sa.text('SELECT eva_core.lock_conversation_source(:event_id,:digest)'),
            {'event_id': row.event_id, 'digest': bytes(row.payload_sha256)}).scalar_one() is not True:
        return False
    data = row.payload.get('data', {})
    if ('content_erasure' in data or any(data.get(key) != source[key]
            for key in ('principal_id', 'conversation_id', 'message_id'))):
        return False
    digest = bytes(row.payload_sha256).hex()
    if 'content_compaction' in data:
        try:
            digest = PostgresConversationJournal._require_compacted_content(data).ingress_payload_sha256
        except Exception:
            return False
    if digest != source['ingress_sha256']:
        return False
    journal = object.__new__(PostgresConversationJournal)
    journal._scope_value = scope
    identity = journal._stop_identity(source['principal_id'], source['conversation_id'], source['message_id'])
    journal._stop_fence(connection, identity)
    return not journal._marker_exists(connection, journal._stop_thread_id(identity),
                                      'CONVERSATION_STOP_REQUESTED', identity)


class PostgresSelfRepair:
    def __init__(self, authority, service, *, clock=None, source_root=None):
        from pathlib import Path
        self.authority, self.service = authority, service
        self.owner = authority.owner
        self.timeline = PostgresOperationalTimeline(self.owner._engine, self.owner._scope)
        self.clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))
        self.source_root = None if source_root is None else Path(source_root).resolve(strict=True)

    def _superseded_paths(self, observation):
        if self.source_root is None:
            return None
        from eva.application.component_improvements import component_source
        from eva.application.self_repair import MAX_REPAIR_SOURCE_BYTES, repairable_source
        superseded, seen = [], set()
        selected_count = 0
        for path, _, expected in reversed(observation.frames):
            if path in seen:
                continue
            seen.add(path)
            if not repairable_source(path) or selected_count >= 8:
                continue
            try:
                target = component_source(self.source_root, path)
            except FileNotFoundError:
                continue
            if not target.is_file() or target.stat().st_size > MAX_REPAIR_SOURCE_BYTES:
                return None
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                superseded.append(path)
                continue
            selected_count += 1
        return sorted(superseded) if superseded else None

    def _permission(self):
        policy, digest = self.authority.policy()
        if policy.get('self_repair', {}).get('enabled') is not True:
            raise ValueError('automatic repair is not enabled')
        self.authority.digital_grant(self.service.digital_profile)
        return digest

    def _source(self, connection, turn):
        journal = PostgresConversationJournal(self.owner._engine, self.owner._scope)
        e = operational_events.c
        row = connection.execute(sa.select(e.payload, e.payload_sha256).where(
            e.tenant_id == self.owner._scope.tenant_id,
            e.installation_id == self.owner._scope.installation_id,
            e.thread_id == 'conversation:' + turn.message_id,
            e.event_type == 'INGRESS_COMMITTED')).one_or_none()
        if (row is None or row.payload.get('data') != journal._ingress_data(turn)
                or canonical_sha256(row.payload) != bytes(row.payload_sha256).hex()):
            return None
        source = {key: getattr(turn, key) for key in ('principal_id', 'conversation_id', 'message_id')}
        source['ingress_sha256'] = bytes(row.payload_sha256).hex()
        return source if repair_source_current(connection, self.owner._scope, source) else None

    def _failure_identity(self, observation):
        return canonical_sha256({'owner': str(self.owner._owner), 'failure': observation.fingerprint})

    @staticmethod
    def _lock_incident(connection, thread):
        connection.execute(sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                           {'key': thread})

    def observe(self, turn, observation):
        """One repair goal per source-version failure, never a copied private prompt."""
        if (turn.audience.kind != 'private_person' or turn.project_id is not None
                or not observation.frames):
            return None
        self.service.validate_authority(turn)
        if (turn.tenant_id != self.owner._tenant_id
                or turn.installation_id != self.owner._installation_id):
            return None
        digest = self._permission()
        now = self.clock()
        identity = self._failure_identity(observation)
        source = canonical_sha256({key: getattr(turn, key) for key in (
            'tenant_id', 'installation_id', 'principal_id', 'conversation_id', 'message_id')})
        with self.owner._engine.begin() as connection:
            self.owner._authorize(connection, now)
            self._lock_incident(connection, 'self-repair:' + identity)
            source_ref = self._source(connection, turn)
            if self._permission() != digest or source_ref is None:
                return None
            return self._record_observation(connection, observation, identity, source_ref, source, digest, now)

    def observe_host_scheduled_failure(self, connection, observation, source_ref):
        """Use standing local repair authority and a verified host technical source."""
        if (connection.engine is not self.owner._engine or not connection.in_transaction()
                or connection.in_nested_transaction()
                or self.service.conversation_scope != self.owner._scope
                or not isinstance(source_ref, dict) or source_ref.get('contract') != HOST_SCHEDULED_FAILURE_SOURCE
                or not any(path == 'adapters/postgres/scheduled_work_failure.py' and function.startswith('load:')
                           for path, function, _ in observation.frames)):
            return None
        digest = self._permission()
        now = self.clock()
        identity = self._failure_identity(observation)
        self.owner._authorize(connection, now)
        # The host already holds the privacy anchor. Yield to any observer
        # that took the incident lock first instead of waiting on its source.
        if not connection.scalar(sa.text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                                 {'key': 'self-repair:' + identity}):
            return None
        source = canonical_sha256(source_ref)
        if (self._permission() != digest or not _host_source_current(connection, self.owner, source_ref,
                observation_sha256=observation.fingerprint, source_sha256=source)):
            return None
        return self._record_observation(connection, observation, identity, source_ref, source, digest, now)

    def _record_observation(self, connection, observation, identity, source_ref, source, digest, now):
        thread = 'self-repair:' + identity
        row = connection.execute(sa.select(operational_checkpoints).where(
            operational_checkpoints.c.tenant_id == self.owner._scope.tenant_id,
            operational_checkpoints.c.installation_id == self.owner._scope.installation_id,
            operational_checkpoints.c.thread_id == thread).with_for_update()).one_or_none()
        if row is not None:
            if source_ref.get('contract') == HOST_SCHEDULED_FAILURE_SOURCE:
                return {'incident_id': thread, 'created': False, 'admitted': False}
            # Repeated sources are evidence, not another task or notification.
            evidence = connection.execute(sa.select(operational_events.c.payload).where(
                operational_events.c.tenant_id == self.owner._scope.tenant_id,
                operational_events.c.installation_id == self.owner._scope.installation_id,
                operational_events.c.thread_id == thread)).scalars().all()
            if (len(evidence) < 33 and not any(
                    event['data'].get('source_sha256') == source for event in evidence)):
                transition_timeline_thread(connection, self.owner._scope, thread_id=thread,
                    expected_revision=row.revision, checkpoint_id=thread + ':evidence:' + source,
                    phase=row.phase, status=row.status, event_type='SELF_REPAIR_EVIDENCE_ADDED',
                    occurred_at=now, data={'source_sha256': source})
            return {'incident_id': thread, 'created': False, 'admitted': False}
        intent_id = 'personal-interest:' + identity
        data = {'incident_id': thread, 'owner_principal_id': str(self.owner._owner),
                'fingerprint': observation.fingerprint, 'observation': observation.to_payload(),
                'source_sha256': source, 'source': source_ref,
                'mandate_sha256': digest, 'intent_id': intent_id}
        insert_initial_timeline_thread(connection, self.owner._scope, thread_id=thread,
            checkpoint_id=thread + ':observed', occurred_at=now, data=data,
            event_type='SELF_REPAIR_OBSERVED', phase='repair_queued', status='active')
        outcome = self._adopt(connection, data, observation, now=now)
        if outcome['status'] in ('adopted', 'already_adopted'):
            transition_timeline_thread(connection, self.owner._scope, thread_id=thread,
                expected_revision=1, checkpoint_id=thread + ':adopted',
                phase='repair_investigating', status='waiting_for_proof',
                event_type='SELF_REPAIR_INITIATIVE_ADMITTED', occurred_at=now,
                data={'intent_id': intent_id})
        return {'incident_id': thread, 'created': True,
                'admitted': outcome['status'] in ('adopted', 'already_adopted')}

    def _adopt(self, connection, data, observation, *, now):
        """Use the same one-active-initiative admission and current owner mandate."""
        incident = data['incident_id']
        payload = {'intent_id': data['intent_id'], 'principal_id': str(self.owner._owner),
            'conversation_id': incident, 'source_message_id': incident,
            'origin': 'eva_initiative', 'execution_mode': 'autonomous_task',
            'adoption_source': 'automatic_runtime_failure', 'unprompted': True,
            'kind': 'personal_interest', 'title': 'Mijn uitvoering herstellen (' + data['fingerprint'][:12] + ')',
            'motivation': 'Een interne uitvoering liep werkelijk vast; ik houd dit hersteldoel bij tot gedragscontrole.',
            'next_exploration': repair_task(observation, incident_id=incident),
            'repair_incident_id': incident, 'repair_source': data['source'],
            'repair_mandate_sha256': data['mandate_sha256'],
            'authority': 'current_host_self_repair_mandate_required'}
        def revalidate():
            if (self._permission() != data['mandate_sha256']
                    or not _incident_source_current(connection, self.owner, data)):
                raise ValueError('automatic repair source or authorization changed')
        return adopt_private_initiative(connection, self.owner._scope, payload, now=now,
            revalidate=revalidate)

    def run_once(self):
        """Admit one queued repair after other current native work releases its lane."""
        digest = self._permission()
        now = self.clock()
        with self.owner._engine.begin() as connection:
            self.owner._authorize(connection, now)
            c, e = operational_checkpoints.c, operational_events.c
            row = connection.execute(sa.select(operational_checkpoints, e.payload.label('original'),
                e.payload_sha256.label('original_sha')).join(
                operational_events, sa.and_(e.tenant_id == c.tenant_id,
                    e.installation_id == c.installation_id, e.thread_id == c.thread_id,
                    e.event_type == 'SELF_REPAIR_OBSERVED')).where(
                c.tenant_id == self.owner._scope.tenant_id,
                c.installation_id == self.owner._scope.installation_id,
                c.thread_id.startswith('self-repair:'), c.phase == 'repair_queued',
                c.status == 'active').order_by(
                    # Expire obsolete permissions before a waiting current goal;
                    # second-resolution receive times can legitimately tie.
                    sa.case((e.payload['data']['mandate_sha256'].astext != digest, 0), else_=1),
                    c.updated_at, c.thread_id).limit(1)).one_or_none()
            if row is None:
                return self._reconcile_superseded_investigating(connection, now=now)
            if not connection.execute(sa.text('SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))'),
                                      {'key': row.thread_id}).scalar_one():
                return 'busy'
            current = connection.execute(sa.select(operational_checkpoints).where(
                c.tenant_id == self.owner._scope.tenant_id,
                c.installation_id == self.owner._scope.installation_id,
                c.thread_id == row.thread_id).with_for_update()).one()
            if current.revision != row.revision or current.phase != 'repair_queued':
                return 'busy'
            from eva.application.self_repair import RepairObservation
            data = row.original['data']; raw = data['observation']
            if (canonical_sha256(row.original) != bytes(row.original_sha).hex()
                    or digest != data['mandate_sha256']):
                transition_timeline_thread(connection, self.owner._scope, thread_id=row.thread_id,
                    expected_revision=row.revision, checkpoint_id=row.thread_id + ':authority-changed',
                    phase='repair_authority_changed', status='needs_help',
                    event_type='SELF_REPAIR_AUTHORITY_CHANGED', occurred_at=now, data={})
                return 'mandate_changed'
            if not _incident_source_current(connection, self.owner, data):
                transition_timeline_thread(connection, self.owner._scope, thread_id=row.thread_id,
                    expected_revision=row.revision, checkpoint_id=row.thread_id + ':source-unavailable',
                    phase='repair_source_unavailable', status='needs_help',
                    event_type='SELF_REPAIR_SOURCE_UNAVAILABLE', occurred_at=now, data={})
                return 'source_unavailable'
            observation = RepairObservation(raw['error_type_sha256'], tuple(
                (f['path'], f['function'], f['source_sha256']) for f in raw['frames']))
            superseded = self._superseded_paths(observation)
            if superseded:
                transition_timeline_thread(connection, self.owner._scope, thread_id=row.thread_id,
                    expected_revision=row.revision,
                    checkpoint_id=f"{row.thread_id}:superseded:{row.revision + 1}",
                    phase='repair_superseded', status='completed',
                    event_type='SELF_REPAIR_SUPERSEDED', occurred_at=now,
                    data={'superseded_paths': superseded})
                return 'superseded'
            outcome = self._adopt(connection, data, observation, now=now)
            if outcome['status'] not in ('adopted', 'already_adopted'):
                return 'waiting'
            transition_timeline_thread(connection, self.owner._scope, thread_id=row.thread_id,
                expected_revision=row.revision, checkpoint_id=row.thread_id + ':adopted',
                phase='repair_investigating', status='waiting_for_proof',
                event_type='SELF_REPAIR_INITIATIVE_ADMITTED', occurred_at=now,
                data={'intent_id': data['intent_id']})
            return 'admitted'

    def _reconcile_superseded_investigating(self, connection, *, now):
        if self.source_root is None:
            return 'idle'
        from eva.adapters.postgres.current_catalog import delegated_work_runs
        from eva.application.self_repair import RepairObservation
        c, e, w = operational_checkpoints.c, operational_events.c, delegated_work_runs.c
        terminal = {'release_ready', 'personal_stopped', 'initiative_completed', 'initiative_blocked'}
        candidates = connection.execute(sa.select(operational_checkpoints, e.payload.label('original'),
            e.payload_sha256.label('original_sha')).join(
            operational_events, sa.and_(e.tenant_id == c.tenant_id,
                e.installation_id == c.installation_id, e.thread_id == c.thread_id,
                e.event_type == 'SELF_REPAIR_OBSERVED')).where(
            c.tenant_id == self.owner._scope.tenant_id,
            c.installation_id == self.owner._scope.installation_id,
            c.thread_id.startswith('self-repair:'), c.phase == 'repair_investigating',
            c.status == 'waiting_for_proof').order_by(c.updated_at, c.thread_id).limit(16)).all()
        for inv in candidates:
            if canonical_sha256(inv.original) != bytes(inv.original_sha).hex():
                continue
            data = inv.original['data']; raw = data['observation']
            observation = RepairObservation(raw['error_type_sha256'], tuple(
                (f['path'], f['function'], f['source_sha256']) for f in raw['frames']))
            superseded = self._superseded_paths(observation)
            if not superseded:
                continue
            if not connection.execute(sa.text('SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))'),
                                      {'key': inv.thread_id}).scalar_one():
                continue
            # Match RuntimeRepairHost lock hierarchy: intent:<parent> -> intent:<children> -> self-repair:<id>.
            owner_query = self.owner._query()
            intents = connection.execute(owner_query.where(
                owner_query.selected_columns.adoption['data']['repair_incident_id'].astext == inv.thread_id
            ).order_by(
                sa.case((c.thread_id == 'intent:' + data['intent_id'], 0), else_=1),
                c.thread_id,
            ).with_for_update(of=operational_checkpoints)).all()
            parent = next((r for r in intents if r.thread_id == 'intent:' + data['intent_id']), None)
            if parent is None or any(r.phase not in terminal for r in intents):
                continue
            current = connection.execute(sa.select(operational_checkpoints).where(
                c.tenant_id == self.owner._scope.tenant_id,
                c.installation_id == self.owner._scope.installation_id,
                c.thread_id == inv.thread_id).with_for_update()).one()
            if (current.revision != inv.revision or current.phase != 'repair_investigating'
                    or current.status != 'waiting_for_proof'):
                continue
            latest_event = connection.execute(sa.select(e.event_type).where(
                e.tenant_id == self.owner._scope.tenant_id,
                e.installation_id == self.owner._scope.installation_id,
                e.thread_id == inv.thread_id, e.sequence == current.revision)).scalar_one_or_none()
            # ComponentImprovementActions commits RUNTIME_REPAIR_REQUEST_ADMITTED in owner._scope.
            handoff_pending = (parent.phase in {'release_ready', 'initiative_completed'}
                and latest_event != 'SELF_REPAIR_REMAINS_OPEN'
                and (len(intents) > 1 or connection.scalar(sa.select(sa.literal(True)).where(
                    e.tenant_id == self.owner._scope.tenant_id,
                    e.installation_id == self.owner._scope.installation_id,
                    e.event_type == 'RUNTIME_REPAIR_REQUEST_ADMITTED',
                    e.payload['data']['request']['incident_id'].astext == inv.thread_id,
                    e.payload['data']['request']['action'].astext == 'submit').limit(1)) is True))
            if handoff_pending:
                continue
            intent_ids = tuple(r.adoption['data']['intent_id'] for r in intents)
            work_scope = self.service.store.scope
            try:
                PostgresOperationalTimeline(self.owner._engine, work_scope)._scope(connection)
                active_work = connection.scalar(sa.select(sa.literal(True)).where(
                    w.tenant_id == work_scope.tenant_id, w.installation_id == work_scope.installation_id,
                    w.spec_payload['origin']['intent_id'].astext.in_(intent_ids),
                    sa.or_(w.state.in_(('admitted', 'call_started')),
                           sa.and_(w.lease_owner.is_not(None), w.lease_expires_at > now))).limit(1)) is True
            finally:
                self.owner._authorize(connection, now)
            if active_work:
                continue
            superseded = self._superseded_paths(observation)
            if not superseded:
                continue
            transition_timeline_thread(connection, self.owner._scope, thread_id=inv.thread_id,
                expected_revision=current.revision,
                checkpoint_id=f"{inv.thread_id}:superseded:{current.revision + 1}",
                phase='repair_superseded', status='completed',
                event_type='SELF_REPAIR_SUPERSEDED', occurred_at=now,
                data={'superseded_paths': superseded})
            return 'superseded'
        return 'idle'


def record_failed_work_repair_notice(store,spec,repair,*,now):
    """One verified notice sidecar; no result/state/usage/failure-report mutation."""
    data={'work_id':spec.work_id,'spec_sha256':spec.sha256,
        'source_ingress_sha256':spec.source_ingress_sha256,'incident_id':repair['incident_id'],
        'admitted':repair['admitted']}
    thread='self-repair-work-notice:'+canonical_sha256({'work_id':spec.work_id,'spec_sha256':spec.sha256})
    timeline=PostgresOperationalTimeline(store._engine,store.scope)
    with store._engine.begin() as connection:
        timeline._scope(connection)
        work=store._load_locked(connection,spec.work_id)
        if work.spec!=spec or work.state!='call_started' or work.stop_requested_at is not None:
            raise ValueError('repair notice work is not the current running attempt')
        row=connection.execute(sa.select(operational_events.c.payload).where(
            operational_events.c.tenant_id==store.scope.tenant_id,
            operational_events.c.installation_id==store.scope.installation_id,
            operational_events.c.thread_id==thread)).scalar_one_or_none()
        if row is not None:
            if row['data']!=data:raise ValueError('repair notice replay differs')
            return
        from eva.adapters.postgres.operational_timeline import insert_initial_timeline_thread
        insert_initial_timeline_thread(connection,store.scope,thread_id=thread,
            checkpoint_id=thread+':recorded',occurred_at=now,event_type='SELF_REPAIR_FAILED_WORK_NOTICE',
            phase='recorded',status='completed',data=data)


def failed_work_repair_notice(connection,store,spec):
    """Read only a host-recorded exact-work sidecar in the current work scope."""
    thread='self-repair-work-notice:'+canonical_sha256({'work_id':spec.work_id,'spec_sha256':spec.sha256})
    e=operational_events.c
    rows=connection.execute(sa.select(e.payload,e.payload_sha256,e.event_type).where(
        e.tenant_id==store.scope.tenant_id,e.installation_id==store.scope.installation_id,
        e.thread_id==thread)).all()
    if not rows:return None
    if len(rows)!=1 or rows[0].event_type!='SELF_REPAIR_FAILED_WORK_NOTICE':
        raise ValueError('repair work notice evidence differs')
    row=rows[0];data=row.payload['data']
    if (canonical_sha256(row.payload)!=bytes(row.payload_sha256).hex()
            or data.get('work_id')!=spec.work_id or data.get('spec_sha256')!=spec.sha256
            or data.get('source_ingress_sha256')!=spec.source_ingress_sha256):
        raise ValueError('repair work notice binding differs')
    return ('Ik ben tegen een probleempje aangelopen. Ik heb het herstel voor mezelf opgepakt.'
        if data['admitted'] else 'Ik ben tegen een probleempje aangelopen. Ik heb het herstel '
        'voor mezelf vastgelegd en pak het op zodra mijn lopende werk klaar is.')
