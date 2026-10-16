"""Session-scoped returns over the existing work and operational journals.

Work specs already bind their episode to the originating private conversation.
Reuse that binding: no second admission record, inferred destination, or task loop.
"""
from eva.application.work_failure_notice import delivery_outcome, WorkFailureNotice
from eva.adapters.postgres.work_failure_notice import failure_notice_cutoff, deliverable_states

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
    def __init__(self, service, *, document_source=None):
        self.service, self.work = service, service.store
        from eva.adapters.postgres.scheduled_work_failure import PrivateCompletionSources
        self.documents = PrivateCompletionSources(self.work,
            document_source if document_source is not None else getattr(service, "document_completion_source", None))

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
        from eva.application.channel_routes import is_whatsapp_conversation
        if (not voice and turn.conversation_id != 'dashboard:' + str(self.service.principal_binding_id)
                and not is_whatsapp_conversation(turn.conversation_id)):
            raise ValueError("private return route unavailable")
        if operation not in {'open', 'close', 'renew', 'poll', 'authorize', 'ack', 'history', 'replay'}:
            raise ValueError("unknown return operation")
        if voice and operation in {'history', 'replay'}:
            raise ValueError("speech results cannot be replayed")
        if not voice and operation in {'open', 'close'}:
            raise ValueError("dashboard has no speech session")
        route = {'owner': str(self.service.owner_principal_id),
                 'binding': str(self.service.principal_binding_id),
                 'conversation': turn.conversation_id,
                 'episode': private_episode(turn)}
        route_hash = canonical_sha256(route)
        session_thread = 'private-return-session:' + route_hash
        now = self.service.now()
        if operation == 'history':
            return self._history(turn, 'private-work-return:' + route_hash + ':')
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
            document = None
            if operation == 'poll':
                # Query the origin episode directly; the owner's twenty-card UI is
                # not the queue and cannot starve an older, followed completion.
                claimed = sa.exists(sa.select(checkpoints.c.thread_id).where(
                    checkpoints.c.tenant_id == runs.c.tenant_id,
                    checkpoints.c.installation_id == runs.c.installation_id,
                    checkpoints.c.thread_id == delivery_prefix + runs.c.work_id,
                    # Text can retry an unacknowledged transfer without replaying work.
                    sa.true() if voice else checkpoints.c.phase != 'claimed'))
                cutoff = failure_notice_cutoff(connection, self.work, now)
                query = sa.select(runs).where(
                    runs.c.tenant_id == self.work.scope.tenant_id,
                    runs.c.installation_id == self.work.scope.installation_id,
                    runs.c.owner_principal_id == self.service.owner_principal_id,
                    runs.c.principal_binding_id == self.service.principal_binding_id,
                    runs.c.spec_payload['episode_id'].astext == route['episode'],
                    deliverable_states(runs, cutoff),
                    runs.c.stop_requested_at.is_(None),
                    runs.c.retention_until > now, ~claimed)
                if voice:
                    query = query.where(runs.c.created_at >= datetime.fromisoformat(opened['opened_at']))
                row = connection.execute(query.order_by(runs.c.created_at, runs.c.work_id)
                    .limit(1).with_for_update(of=runs, skip_locked=True)).mappings().one_or_none()
                if row is None:
                    document = self._next_document(connection, route, delivery_prefix,
                        opened_at=datetime.fromisoformat(opened['opened_at']) if voice else None,
                        retry_claimed=not voice)
                    if document is None:
                        return {'status': 'idle', 'result': None}
                else:
                    work = self.work._validated(connection, row)
            else:
                if not isinstance(work_id, str) or not 1 <= len(work_id) <= 240:
                    raise ValueError("return work identity invalid")
                if self.documents is not None and work_id.startswith(('document-task:', 'scheduled-work:')):
                    document = self._document(work_id, route)
                    if document is None:
                        raise ValueError('document return unavailable')
                else:
                    work = self.work._load_locked(connection, work_id)
            if document is not None:
                result, returned_id, generation = document, document.goal_id, 1
            else:
                if (work.spec.audience != 'private' or work.spec.owner_principal_id != route['owner']
                        or work.spec.principal_binding_id != route['binding']
                        or work.spec.episode_id != route['episode']
                        or work.retention_until <= now or work.stop_requested_at is not None
                        or delivery_outcome(work) is None):
                    raise ValueError("private result unavailable")
                result, returned_id, generation = delivery_outcome(work), work.spec.work_id, work.spec.generation
            identity = canonical_sha256({'route': route, 'work_id': returned_id,
                'generation': generation, 'result': canonical_sha256(result.to_payload())})
            thread = delivery_prefix + returned_id
            state = self._state(connection, thread)
            if operation == 'poll':
                if state is not None:
                    if (voice or state['phase'] != 'claimed'
                            or state['operation_id'] != identity
                            or self._first(connection, thread) != {'route': route,
                                'work_id': returned_id, 'operation_id': identity}):
                        raise ValueError("result already claimed or binding changed")
                    if now >= state['deadline']:
                        transition_timeline_thread(connection, self.work.scope,
                            thread_id=thread, expected_revision=state['revision'],
                            checkpoint_id=thread+':retry:'+str(state['revision']), occurred_at=now,
                            event_type='PRIVATE_WORK_RETURN_TRANSFER_RETRIED',
                            phase='claimed', status='active', deadline=now+timedelta(minutes=10),
                            operation_id=identity, data={'operation_id': identity})
                else:
                    insert_initial_timeline_thread(connection, self.work.scope,
                        thread_id=thread, checkpoint_id=thread+':claimed', occurred_at=now,
                        event_type='PRIVATE_WORK_RETURN_CLAIMED', phase='claimed', status='active',
                        deadline=now+timedelta(minutes=10), operation_id=identity,
                        data={'route': route, 'work_id': returned_id, 'operation_id': identity})
            else:
                if (state is None or operation_id != identity
                        or self._first(connection, thread) != {'route': route,
                            'work_id': returned_id, 'operation_id': identity}):
                    raise ValueError("return claim differs")
                if state['phase'] == 'acknowledged' and operation == 'ack':
                    return {'status': 'acknowledged'}
                if operation == 'replay' and state['phase'] != 'acknowledged':
                    raise ValueError("return has not been acknowledged")
                if (state['phase'] not in {'claimed', 'acknowledged'}
                        or (state['phase'] == 'claimed' and now >= state['deadline'])):
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
            return {'status': 'claimed', 'result': {'work_id': returned_id,
                'generation': generation, 'operation_id': identity,
                'title': document.title if document is not None else work.title,
                'output': result.output, 'output_sha256': result.output_sha256,
                'result_sha256': canonical_sha256(result.to_payload()),
                'completed_at': result.completed_at,
                'outcome': (document.outcome if document is not None else
                            result.state if isinstance(result, WorkFailureNotice) else 'completed')}}

    def _history(self, turn, prefix):
        """Read acknowledged text returns through the same current result checks.

        Release the listing transaction before replay validation: execute takes
        the route lock. History never claims, acknowledges or reruns work.
        """
        with self.work._engine.begin() as connection:
            self.work._scope(connection)
            self.work._validate_owner_binding(connection, **self.service._owner)
            rows = connection.execute(sa.select(
                checkpoints.c.thread_id, checkpoints.c.operation_id).where(
                    checkpoints.c.tenant_id == self.work.scope.tenant_id,
                    checkpoints.c.installation_id == self.work.scope.installation_id,
                    checkpoints.c.thread_id.startswith(prefix, autoescape=True),
                    checkpoints.c.phase == 'acknowledged',
                ).order_by(checkpoints.c.updated_at.desc(), checkpoints.c.thread_id.desc())
                .limit(20)).all()
        results = []
        for row in reversed(rows):
            try:
                replay = self.execute(turn, 'replay', work_id=row.thread_id[len(prefix):],
                                      operation_id=row.operation_id)
            except (ValueError, RuntimeError):
                # Expired, stopped or no-longer-authorized results stay hidden.
                continue
            results.append(replay['result'])
        return {'status': 'history', 'results': results}

    def _document(self, work_id, route):
        result = self.documents.load(work_id)
        if result is None:
            return None
        binding = result.binding
        if (binding['channel'] != 'private_session'
                or binding['owner_principal_id'] != route['owner']
                or binding['principal_binding_id'] != route['binding']
                or binding['conversation_id'] != route['conversation']
                or binding['episode_id'] != route['episode']):
            raise ValueError('document return belongs to another private route')
        return result

    def _next_document(self, connection, route, prefix, *, opened_at, retry_claimed=False):
        if self.documents is None:
            return None
        for work_id in self.documents.candidates():
            state = self._state(connection, prefix + work_id)
            if state is not None and not (retry_claimed and state['phase'] == 'claimed'):
                continue
            try:
                result = self._document(work_id, route)
            except (ValueError, RuntimeError):
                continue
            if result is not None and (opened_at is None or result.created_at >= opened_at):
                return result
        return None
