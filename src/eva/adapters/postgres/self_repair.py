"""Autonomous repair goals on Eva's existing operational and initiative journal."""
from datetime import UTC, datetime

import sqlalchemy as sa

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.initiative_adoption import adopt_private_initiative
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline, insert_initial_timeline_thread,
    operational_checkpoints, operational_events, transition_timeline_thread,
)
from eva.application.self_repair import repair_task
from eva.contracts.canonical_json import canonical_sha256


def current_repair_binding(authority, incident_id, *, now):
    """Host-only incident evidence for assessment/application, even after work ends.

    A completed development job grants no installation authority. The original
    owner, mandate, source, erasure and personal stop remain authoritative.
    This returns metadata only; no exception message or private turn is copied.
    """
    import re
    if not isinstance(incident_id, str) or not re.fullmatch(r'self-repair:[0-9a-f]{64}', incident_id):
        raise ValueError('invalid repair incident identity')
    owner = authority.owner
    policy, digest = authority.policy()
    if policy.get('self_repair', {}).get('enabled') is not True:
        raise ValueError('automatic repair is disabled')
    with owner._engine.begin() as connection:
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
                or not repair_source_current(connection, owner._scope, data['source'])):
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
    def __init__(self, authority, service, *, clock=None):
        self.authority, self.service = authority, service
        self.owner = authority.owner
        self.timeline = PostgresOperationalTimeline(self.owner._engine, self.owner._scope)
        self.clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))

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
        identity = canonical_sha256({'owner': str(self.owner._owner),
                                     'failure': observation.fingerprint})
        thread = 'self-repair:' + identity
        source = canonical_sha256({key: getattr(turn, key) for key in (
            'tenant_id', 'installation_id', 'principal_id', 'conversation_id', 'message_id')})
        with self.owner._engine.begin() as connection:
            self.owner._authorize(connection, now)
            connection.execute(sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                               {'key': thread})
            source_ref = self._source(connection, turn)
            if self._permission() != digest or source_ref is None:
                return None
            row = connection.execute(sa.select(operational_checkpoints).where(
                operational_checkpoints.c.tenant_id == self.owner._scope.tenant_id,
                operational_checkpoints.c.installation_id == self.owner._scope.installation_id,
                operational_checkpoints.c.thread_id == thread).with_for_update()).one_or_none()
            if row is not None:
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
                    or not repair_source_current(connection, self.owner._scope, data['source'])):
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
                return 'idle'
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
            if not repair_source_current(connection, self.owner._scope, data['source']):
                transition_timeline_thread(connection, self.owner._scope, thread_id=row.thread_id,
                    expected_revision=row.revision, checkpoint_id=row.thread_id + ':source-unavailable',
                    phase='repair_source_unavailable', status='needs_help',
                    event_type='SELF_REPAIR_SOURCE_UNAVAILABLE', occurred_at=now, data={})
                return 'source_unavailable'
            observation = RepairObservation(raw['error_type_sha256'], tuple(
                (f['path'], f['function'], f['source_sha256']) for f in raw['frames']))
            outcome = self._adopt(connection, data, observation, now=now)
            if outcome['status'] not in ('adopted', 'already_adopted'):
                return 'waiting'
            transition_timeline_thread(connection, self.owner._scope, thread_id=row.thread_id,
                expected_revision=row.revision, checkpoint_id=row.thread_id + ':adopted',
                phase='repair_investigating', status='waiting_for_proof',
                event_type='SELF_REPAIR_INITIATIVE_ADMITTED', occurred_at=now,
                data={'intent_id': data['intent_id']})
            return 'admitted'
