"""Session-scoped returns over the existing work and operational journals.

Work specs already bind their episode to the originating private conversation.
Reuse that binding: no second admission record, inferred destination, or task loop.
"""
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.current_catalog import delegated_work_runs as runs
from eva.adapters.postgres.operational_timeline import (
    operational_checkpoints as checkpoints, operational_events as events,
    insert_initial_timeline_thread, transition_timeline_thread,
)
from eva.contracts.canonical_json import canonical_sha256


def private_episode(turn):
    return str(uuid5(NAMESPACE_URL, ":".join(("delegated-private-context-v1",
        turn.tenant_id, turn.installation_id, turn.principal_id, turn.conversation_id))))


class PrivateWorkReturnStore:
    def __init__(self, service):
        self.service, self.work = service, service.store

    def _where(self, table, thread):
        return (table.c.tenant_id == self.work.scope.tenant_id,
                table.c.installation_id == self.work.scope.installation_id,
                table.c.thread_id == thread)

    def _state(self, connection, thread):
        return connection.execute(sa.select(checkpoints).where(
            *self._where(checkpoints, thread)).with_for_update()).mappings().one_or_none()

    def _first(self, connection, thread):
        row = connection.execute(sa.select(events).where(
            *self._where(events, thread), events.c.sequence == 1)).mappings().one()
        if canonical_sha256(row['payload']) != bytes(row['payload_sha256']).hex():
            raise ValueError("return journal integrity differs")
        return row['payload']['data']

    def execute(self, turn, operation, *, work_id=None, operation_id=None):
        self.service.validate_authority(turn)
        if not self.service.available_for(turn):
            raise ValueError("private return authority unavailable")
        voice = turn.conversation_id.startswith('voice:')
        if not voice and turn.conversation_id != 'dashboard:' + str(self.service.principal_binding_id):
            raise ValueError("private return route unavailable")
        if operation not in {'open', 'close', 'renew', 'poll', 'authorize', 'ack'}:
            raise ValueError("unknown return operation")
        if not voice and operation in {'open', 'close'}:
            raise ValueError("dashboard has no speech session")
        route = {'owner': str(self.service.owner_principal_id),
                 'binding': str(self.service.principal_binding_id),
                 'conversation': turn.conversation_id,
                 'episode': private_episode(turn)}
        route_hash = canonical_sha256(route)
        session_thread = 'private-return-session:' + route_hash
        now = self.service.now()
        with self.work._engine.begin() as connection:
            self.work._scope(connection)
            self.work._validate_owner_binding(connection, **self.service._owner)
            # Serialize opening, closing, claiming and acknowledgements per route.
            connection.execute(sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                               {'key': session_thread})
            session = self._state(connection, session_thread) if voice else None
            if operation == 'open':
                if session is not None:
                    raise ValueError("return session cannot be reopened")
                insert_initial_timeline_thread(connection, self.work.scope,
                    thread_id=session_thread, checkpoint_id=session_thread+':open',
                    occurred_at=now, event_type='PRIVATE_RETURN_SESSION_OPENED',
                    phase='open', status='active', deadline=now+timedelta(seconds=15),
                    data={'route': route, 'opened_at': now.isoformat(),
                          'expires_at': (now+timedelta(minutes=30)).isoformat()})
                return {'status': 'open'}
            if voice:
                if session is None:
                    raise ValueError("return session absent")
                from datetime import datetime
                opened = self._first(connection, session_thread)
                if opened['route'] != route:
                    raise ValueError("return session binding differs")
                if operation == 'close':
                    if session['phase'] == 'open':
                        transition_timeline_thread(connection, self.work.scope,
                            thread_id=session_thread, expected_revision=session['revision'],
                            checkpoint_id=session_thread+':closed', occurred_at=now,
                            event_type='PRIVATE_RETURN_SESSION_CLOSED', phase='closed',
                            status='completed', data={})
                    return {'status': 'closed'}
                if (session['phase'] != 'open' or now >= session['deadline']
                        or now >= datetime.fromisoformat(opened['expires_at'])):
                    raise ValueError("return session expired")
                if operation in {'poll', 'renew'} and session['deadline'] - now < timedelta(seconds=10):
                    transition_timeline_thread(connection, self.work.scope,
                        thread_id=session_thread, expected_revision=session['revision'],
                        checkpoint_id=session_thread+':renew:'+str(session['revision']),
                        occurred_at=now, event_type='PRIVATE_RETURN_SESSION_RENEWED',
                        phase='open', status='active', deadline=now+timedelta(seconds=15), data={})
            if operation == 'renew':
                return {'status': 'open'}
            delivery_prefix = 'private-work-return:' + route_hash + ':'
            if operation == 'poll':
                # Query the origin episode directly; the owner's twenty-card UI is
                # not the queue and cannot starve an older, followed completion.
                claimed = sa.exists(sa.select(checkpoints.c.thread_id).where(
                    checkpoints.c.tenant_id == runs.c.tenant_id,
                    checkpoints.c.installation_id == runs.c.installation_id,
                    checkpoints.c.thread_id == delivery_prefix + runs.c.work_id))
                query = sa.select(runs).where(
                    runs.c.tenant_id == self.work.scope.tenant_id,
                    runs.c.installation_id == self.work.scope.installation_id,
                    runs.c.owner_principal_id == self.service.owner_principal_id,
                    runs.c.principal_binding_id == self.service.principal_binding_id,
                    runs.c.spec_payload['episode_id'].astext == route['episode'],
                    runs.c.state.in_(('succeeded', 'verified')),
                    runs.c.retention_until > now, ~claimed)
                if voice:
                    query = query.where(runs.c.created_at >= datetime.fromisoformat(opened['opened_at']))
                row = connection.execute(query.order_by(runs.c.created_at, runs.c.work_id)
                    .limit(1).with_for_update(of=runs, skip_locked=True)).mappings().one_or_none()
                if row is None:
                    return {'status': 'idle', 'result': None}
                work = self.work._validated(connection, row)
            else:
                if not isinstance(work_id, str) or not 1 <= len(work_id) <= 240:
                    raise ValueError("return work identity invalid")
                work = self.work._load_locked(connection, work_id)
            if (work.spec.owner_principal_id != route['owner']
                    or work.spec.principal_binding_id != route['binding']
                    or work.spec.episode_id != route['episode']
                    or work.retention_until <= now or work.stop_requested_at is not None
                    or work.state not in {'succeeded', 'verified'} or work.result is None):
                raise ValueError("private result unavailable")
            result = work.result
            identity = canonical_sha256({'route': route, 'work_id': work.spec.work_id,
                'generation': work.spec.generation, 'result': canonical_sha256(result.to_payload())})
            thread = delivery_prefix + work.spec.work_id
            state = self._state(connection, thread)
            if operation == 'poll':
                if state is not None:
                    raise ValueError("result already claimed")
                insert_initial_timeline_thread(connection, self.work.scope,
                    thread_id=thread, checkpoint_id=thread+':claimed', occurred_at=now,
                    event_type='PRIVATE_WORK_RETURN_CLAIMED', phase='claimed', status='active',
                    deadline=now+timedelta(minutes=10), operation_id=identity,
                    data={'route': route, 'work_id': work.spec.work_id, 'operation_id': identity})
            else:
                if (state is None or operation_id != identity
                        or self._first(connection, thread) != {'route': route,
                            'work_id': work.spec.work_id, 'operation_id': identity}):
                    raise ValueError("return claim differs")
                if state['phase'] == 'acknowledged' and operation == 'ack':
                    return {'status': 'acknowledged'}
                if state['phase'] != 'claimed' or now >= state['deadline']:
                    raise ValueError("return claim unavailable or ambiguous")
                if operation == 'ack':
                    transition_timeline_thread(connection, self.work.scope,
                        thread_id=thread, expected_revision=state['revision'],
                        checkpoint_id=thread+':ack', occurred_at=now,
                        event_type='PRIVATE_WORK_RETURN_ACKNOWLEDGED', phase='acknowledged',
                        status='completed', operation_id=identity,
                        data={'operation_id': identity, 'disposition': 'offered_not_proven_heard'})
                    return {'status': 'acknowledged'}
            if operation == 'authorize':
                return {'status': 'authorized', 'operation_id': identity,
                        'output_sha256': result.output_sha256,
                        'result_sha256': canonical_sha256(result.to_payload())}
            return {'status': 'claimed', 'result': {'work_id': work.spec.work_id,
                'generation': work.spec.generation, 'operation_id': identity,
                'output': result.output, 'output_sha256': result.output_sha256,
                'result_sha256': canonical_sha256(result.to_payload()),
                'completed_at': result.completed_at}}
