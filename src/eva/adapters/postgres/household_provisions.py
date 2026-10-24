"""Owner-controlled provision lifecycle on the existing scoped event timeline.

Registration records provenance, not adoption or successful operation. Callbacks
are trusted host integrations, never verifier assertions supplied by a model.
"""
from copy import deepcopy
import re

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict, OperationalTimelineError, PostgresOperationalTimeline,
    external_timeline_scope, insert_initial_timeline_thread,
    operational_checkpoints, operational_events, transition_timeline_thread,
    _state_from_event, _state_from_row, _event_from_row,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.timestamps import require_utc_second


def procedure_provision_id(config):
    """Keep identity stable across versions and restoration of the same source."""
    return "household-provision:" + canonical_sha256({key: config[key] for key in
        ("reference_id", "source_principal_id", "household_binding")})


def _digest(value):
    if not isinstance(value, str) or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise ValueError("provision digest is invalid")
    return value


def _identifier(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 160 or not value.strip():
        raise ValueError("provision identifier is invalid")
    return value


class PostgresHouseholdProvisions:
    """No new schema, queue, background worker, credentials or execution grant.

    authorize(connection, now) must check the actual scoped owner/read authority.
    verify_current(connection, evidence) raises if source/rights/version changed.
    verify_realization returns a verified result receipt SHA, or None if absent.
    effect_status returns clear, in_flight, unknown or stopped from real receipts.
    """
    def __init__(self, engine, *, tenant_id, installation_id, owner_principal_id, household_binding,
                 authorize, verify_current, verify_realization, effect_status):
        self.engine = engine
        self.owner = _identifier(owner_principal_id)
        self.household_sha256 = canonical_sha256(household_binding)
        self.scope = external_timeline_scope(tenant_id, installation_id,
                                             "provision installation is absent")
        self.timeline = PostgresOperationalTimeline(engine, self.scope)
        self.authorize = authorize
        self.verify_current = verify_current
        self.verify_realization = verify_realization
        self.effect_status = effect_status

    def _thread(self, provision_id):
        return "provision:" + canonical_sha256(_identifier(provision_id))

    def _load(self, conn, thread):
        c, e = operational_checkpoints.c, operational_events.c
        checkpoint = conn.execute(sa.select(operational_checkpoints).where(
            c.tenant_id == self.scope.tenant_id,
            c.installation_id == self.scope.installation_id,
            c.thread_id == thread).with_for_update()).mappings().one_or_none()
        if checkpoint is None:
            return None, []
        rows = conn.execute(sa.select(operational_events).where(
            e.tenant_id == self.scope.tenant_id,
            e.installation_id == self.scope.installation_id,
            e.thread_id == thread).order_by(e.sequence)).mappings().all()
        if (len(rows) != checkpoint['revision'] or not rows
                or any(row['sequence'] != i for i, row in enumerate(rows, 1))
                or any(canonical_sha256(row['payload']) != bytes(row['payload_sha256']).hex()
                       for row in rows)
                or rows[-1]['payload']['checkpoint']['revision'] != checkpoint['revision']
                or _state_from_event(_event_from_row(rows[-1])) != _state_from_row(checkpoint)
                or any(later['occurred_at'] < earlier['occurred_at']
                       for earlier, later in zip(rows, rows[1:]))):
            raise OperationalTimelineError("provision history or projection differs")
        state = rows[-1]['payload']['data']['state']
        if (state['owner_principal_id'] != self.owner
                or state['household_binding_sha256'] != self.household_sha256
                or state['status'] != checkpoint['phase']
                or state['revision'] != checkpoint['revision']):
            raise OperationalTimelineError("provision owner or state differs")
        return deepcopy(state), rows

    def _begin(self, conn, now):
        require_utc_second(now, 'now')
        self.timeline._scope(conn)
        self.authorize(conn, now)

    @staticmethod
    def _time(rows, now):
        if rows and now < rows[-1]['occurred_at']:
            raise CheckpointConflict('provision time moved backwards')

    @staticmethod
    def _replay(rows, request_id, fingerprint):
        for row in rows:
            data = row['payload']['data']
            if data['request_id'] == request_id:
                if data['request_sha256'] != fingerprint:
                    raise ValueError("provision request changed during replay")
                return deepcopy(data['receipt'])
        return None

    def register(self, provision_id, evidence, *, request_id, now):
        """Register exact provenance as proposed; never activate a legacy route."""
        thread = self._thread(provision_id)
        request_id = _identifier(request_id)
        evidence = deepcopy(evidence)
        if set(evidence) != {'binding_sha256', 'source_sha256', 'review_sha256',
                             'reference_id', 'realization_receipt_sha256'}:
            raise ValueError("provision evidence fields differ")
        _identifier(evidence['reference_id'])
        for key in evidence.keys() - {'reference_id'}:
            if key == 'realization_receipt_sha256' and evidence[key] is None:
                continue  # Proposed provenance may honestly lack actual working proof.
            _digest(evidence[key])
        fingerprint = canonical_sha256({'register': provision_id, 'evidence': evidence})
        with self.engine.begin() as conn:
            self._begin(conn, now)
            conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                         {'key': f'{self.scope.tenant_id}:{self.scope.installation_id}:{thread}'})
            old, rows = self._load(conn, thread)
            replay = self._replay(rows, request_id, fingerprint)
            if replay is not None:
                return replay
            self._time(rows, now)
            if old is not None:
                raise CheckpointConflict("provision already registered")
            self.verify_current(conn, evidence)
            state = {'provision_id': provision_id, 'owner_principal_id': self.owner,
                     'household_binding_sha256': self.household_sha256,
                     'evidence': evidence, 'status': 'proposed', 'revision': 1,
                     'adoption': None, 'verification_sha256': None, 'stopped': False,
                     'pending_effects': {}}
            receipt = self._receipt(state)
            insert_initial_timeline_thread(conn, self.scope, thread_id=thread,
                checkpoint_id=thread + ':1', occurred_at=now,
                event_type='HOUSEHOLD_PROVISION_REGISTERED', phase='proposed',
                data={'state': state, 'request_id': request_id,
                      'request_sha256': fingerprint, 'receipt': receipt})
            return receipt

    @staticmethod
    def _receipt(state):
        return {'contract': 'EvaHouseholdProvisionReceipt/v1', **deepcopy(state)}

    def inspect(self, provision_id, *, now):
        with self.engine.begin() as conn:
            self._begin(conn, now)
            state, _ = self._load(conn, self._thread(provision_id))
            if state is not None:
                # This read is not an activation or a permanent status rewrite.
                state['effect_status'] = self._effect(conn, state)
            return state

    def _effect(self, conn, state):
        value = self.effect_status(conn, state['evidence'])
        if value not in {'clear', 'unknown', 'in_flight', 'stopped'}:
            raise ValueError("provision effect status is invalid")
        if value == 'stopped' or state['stopped']:
            return 'stopped'
        if value == 'unknown' or 'unknown' in state['pending_effects'].values():
            return 'unknown'
        if value == 'in_flight' or state['pending_effects']:
            return 'in_flight'
        return value

    def command(self, provision_id, action, *, expected_revision, binding_sha256,
                request_id, now):
        if action not in {'adopt', 'pause', 'resume', 'reject', 'retire', 'stop'}:
            raise ValueError("provision action is invalid")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("provision revision is invalid")
        _digest(binding_sha256)
        request_id = _identifier(request_id)
        fingerprint = canonical_sha256({'action': action, 'revision': expected_revision,
                                        'binding_sha256': binding_sha256})
        thread = self._thread(provision_id)
        with self.engine.begin() as conn:
            self._begin(conn, now)
            state, rows = self._load(conn, thread)
            if state is None:
                raise LookupError("provision is not registered")
            replay = self._replay(rows, request_id, fingerprint)
            if replay is not None:
                return replay
            self._time(rows, now)
            if (state['revision'] != expected_revision
                    or state['evidence']['binding_sha256'] != binding_sha256):
                raise CheckpointConflict("provision version or revision changed")
            if state['status'] in {'retired', 'rejected'}:
                raise CheckpointConflict("provision is no longer resumable")
            if action == 'reject' and (state['status'] != 'proposed' or state['adoption'] is not None):
                raise CheckpointConflict('only an unadopted proposal can be rejected')
            effect = self._effect(conn, state)
            if action in {'adopt', 'resume'}:
                if state['stopped'] or effect == 'stopped':
                    raise CheckpointConflict("provision is stopped")
                if effect != 'clear':
                    raise CheckpointConflict("provision effect is not confirmed clear")
                if action == 'resume' and (state['status'] != 'paused' or not state['adoption']):
                    raise CheckpointConflict("only an adopted paused provision can resume")
                self.verify_current(conn, state['evidence'])
                verified = self.verify_realization(conn, state['evidence'])
                if verified is not None:
                    _digest(verified)
                if action == 'resume' and verified is None:
                    raise CheckpointConflict("verified realization is unavailable")
                if action == 'adopt':
                    state['adoption'] = {'owner_principal_id': self.owner,
                                         'request_id': request_id, 'at': now.isoformat()}
                state['verification_sha256'] = verified
                state['status'] = 'active' if verified is not None else 'proposed'
            else:
                state['status'] = {'pause': 'paused', 'stop': 'paused',
                                   'reject': 'rejected', 'retire': 'retired'}[action]
                if action == 'stop':
                    state['stopped'] = True
            state['revision'] += 1
            receipt = {**self._receipt(state), 'effect_status': effect,
                       'in_flight_effect_may_complete': effect in {'unknown', 'in_flight'}}
            transition_timeline_thread(conn, self.scope, thread_id=thread,
                expected_revision=expected_revision, checkpoint_id=thread + ':' + str(state['revision']),
                phase=state['status'], status='completed' if state['status'] in {'retired', 'rejected'} else 'active',
                event_type='HOUSEHOLD_PROVISION_' + action.upper(), occurred_at=now,
                data={'state': state, 'request_id': request_id,
                      'request_sha256': fingerprint, 'receipt': receipt})
            return receipt

    def admit_effect(self, provision_id, claim_binding, *, expected_revision,
                     binding_sha256, request_id, now):
        """Serialize final admission with pause/STOP; admission is not an effect.

        The caller must not POST on replay. Once committed, an in-flight effect
        can finish despite a later pause. Files are revalidated, not DB-locked.
        """
        if (set(claim_binding) != {'thread_id', 'operation_id', 'revision', 'source_event_id'}
                or type(claim_binding['revision']) is not int or claim_binding['revision'] < 1):
            raise ValueError('provision claim binding differs')
        for key in ('thread_id', 'operation_id', 'source_event_id'):
            value = claim_binding[key]
            if not isinstance(value, str) or not 1 <= len(value) <= 240:
                raise ValueError('provision claim identifier differs')
        request_id = _identifier(request_id)
        _digest(binding_sha256)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('provision revision is invalid')
        fingerprint = canonical_sha256({'claim': claim_binding,
            'revision': expected_revision, 'binding_sha256': binding_sha256})
        admission_id = canonical_sha256(claim_binding)
        thread = self._thread(provision_id)
        with self.engine.begin() as conn:
            self._begin(conn, now)
            state, rows = self._load(conn, thread)
            if state is None:
                raise LookupError('provision is not registered')
            replay = self._replay(rows, request_id, fingerprint)
            if replay is not None:
                return {'admitted_now': False, 'receipt': replay}
            self._time(rows, now)
            for row in rows:
                data = row['payload']['data']
                if data.get('admission_id') == admission_id and row['event_type'] == 'HOUSEHOLD_PROVISION_EFFECT_ADMITTED':
                    return {'admitted_now': False, 'receipt': deepcopy(data['receipt'])}
            if (state['revision'] != expected_revision
                    or state['evidence']['binding_sha256'] != binding_sha256):
                raise CheckpointConflict('provision version or revision changed')
            if (state['status'] != 'active' or not state['adoption']
                    or not state['verification_sha256'] or self._effect(conn, state) != 'clear'):
                raise CheckpointConflict('provision is not dispatchable')
            self.verify_current(conn, state['evidence'])
            state['pending_effects'][admission_id] = 'in_flight'
            state['revision'] += 1
            receipt = {'contract': 'EvaHouseholdProvisionEffectAdmission/v1',
                'provision_id': provision_id, 'admission_id': admission_id,
                'binding_sha256': binding_sha256, 'claim_binding': deepcopy(claim_binding),
                'revision': state['revision']}
            transition_timeline_thread(conn, self.scope, thread_id=thread,
                expected_revision=expected_revision,
                checkpoint_id=thread + ':' + str(state['revision']), phase=state['status'],
                status='active', event_type='HOUSEHOLD_PROVISION_EFFECT_ADMITTED', occurred_at=now,
                data={'state': state, 'request_id': request_id, 'request_sha256': fingerprint,
                      'admission_id': admission_id, 'receipt': receipt})
            return {'admitted_now': True, 'receipt': receipt}

    def settle_effect(self, provision_id, *, admission_id, outcome, proof_sha256,
                      request_id, now):
        """A real effect receipt may settle after pause; uncertain is not replayable."""
        _digest(admission_id)
        _digest(proof_sha256)
        request_id = _identifier(request_id)
        if outcome not in {'completed', 'unknown', 'not_dispatched'}:
            raise ValueError('provision effect outcome is invalid')
        fingerprint = canonical_sha256({'admission_id': admission_id,
                                        'outcome': outcome, 'proof_sha256': proof_sha256})
        thread = self._thread(provision_id)
        with self.engine.begin() as conn:
            self._begin(conn, now)
            state, rows = self._load(conn, thread)
            if state is None:
                raise LookupError('provision is not registered')
            replay = self._replay(rows, request_id, fingerprint)
            if replay is not None:
                return replay
            self._time(rows, now)
            if admission_id not in state['pending_effects']:
                raise CheckpointConflict('provision effect is not pending')
            if outcome == 'unknown':
                state['pending_effects'][admission_id] = 'unknown'
            else:
                del state['pending_effects'][admission_id]
            revision = state['revision']
            state['revision'] += 1
            receipt = {'contract': 'EvaHouseholdProvisionEffectSettlement/v1',
                       'provision_id': provision_id, 'admission_id': admission_id,
                       'outcome': outcome, 'proof_sha256': proof_sha256,
                       'revision': state['revision']}
            transition_timeline_thread(conn, self.scope, thread_id=thread,
                expected_revision=revision, checkpoint_id=thread + ':' + str(state['revision']),
                phase=state['status'], status='completed' if state['status'] in {'retired', 'rejected'} else 'active',
                event_type='HOUSEHOLD_PROVISION_EFFECT_SETTLED', occurred_at=now,
                data={'state': state, 'request_id': request_id, 'request_sha256': fingerprint,
                      'admission_id': admission_id, 'receipt': receipt})
            return receipt
