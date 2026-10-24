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
    if config.get('contract') == 'EvaHouseholdProcedureBinding/v2':
        provision_id = config.get('provision_id')
        if not isinstance(provision_id, str) or re.fullmatch(r'household-provision:[0-9a-f]{64}', provision_id) is None:
            raise ValueError('versioned provision identity differs')
        return provision_id
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
                 authorize, verify_current, verify_realization, effect_status, verify_version=None,
                 verify_empty_current=None, verify_trial_realization=None, current_guard=None,
                 effect_status_for_admission=None):
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
        if verify_version is not None and not callable(verify_version):
            raise ValueError("provision version resolver is invalid")
        self.verify_version = verify_version
        for name, callback in (('verify_empty_current', verify_empty_current),
                ('verify_trial_realization', verify_trial_realization), ('current_guard', current_guard),
                ('effect_status_for_admission', effect_status_for_admission)):
            if callback is not None and not callable(callback):
                raise ValueError('trusted provision trial callback is invalid')
            setattr(self, name, callback)

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

    def inspect(self, provision_id, *, now, admission_claim=None):
        with self.engine.begin() as conn:
            self._begin(conn, now)
            state, _ = self._load(conn, self._thread(provision_id))
            if state is not None:
                # This read is not an activation or a permanent status rewrite.
                state['effect_status'] = self._effect(conn, state, admission_claim=admission_claim)
            return state

    def _source_effect(self, conn, state, *, admission_claim=None):
        if admission_claim is not None and self.effect_status_for_admission is not None:
            value = self.effect_status_for_admission(conn, state['evidence'], admission_claim, state['revision'])
        else:
            value = self.effect_status(conn, state['evidence'])
        if admission_claim is None and value == 'unknown' and self.verify_empty_current is not None:
            empty = self.verify_empty_current(conn, state['evidence'])
            if empty is not None:
                _digest(empty)
                value = 'clear'
        if value not in {'clear', 'unknown', 'in_flight', 'stopped'}:
            raise ValueError("provision effect status is invalid")
        return value

    def _effect(self, conn, state, *, admission_claim=None):
        value = self._source_effect(conn, state, admission_claim=admission_claim)
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
                if state.get('pending_change') is not None:
                    raise CheckpointConflict('provision source change is incomplete')
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
                if action in {'pause', 'stop'} and state.get('pending_change') is not None:
                    state['pending_change']['prior_status'] = 'paused'
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
            trial_allowed = False
            if state.get('trial') is not None and self.current_guard is not None:
                trial_allowed = household_trial_dispatchable(state, now=now,
                    execution_guard=self.current_guard(conn, state['evidence']))
            if ((not trial_allowed and (state['status'] != 'active' or not state['adoption']
                    or not state['verification_sha256'])) or state.get('pending_change') is not None
                    or self._effect(conn, state, admission_claim=claim_binding) != 'clear'):
                raise CheckpointConflict('provision is not dispatchable')
            self.verify_current(conn, state['evidence'])
            state['pending_effects'][admission_id] = 'in_flight'
            if trial_allowed:
                state['trial']['claims'][admission_id] = {'claim': deepcopy(claim_binding),
                    'outcome': 'in_flight', 'proof_sha256': None}
                state['trial']['effect_count'] += 1
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
            trial = state.get('trial')
            if trial is not None and admission_id in trial['claims']:
                trial['claims'][admission_id].update(outcome=outcome, proof_sha256=proof_sha256)
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


    @staticmethod
    def _version_id(evidence):
        return canonical_sha256({key: evidence[key] for key in
            ('binding_sha256', 'source_sha256', 'review_sha256', 'reference_id')})

    @classmethod
    def _version_state(cls, state, now):
        if 'current_version' not in state:
            version = cls._version_id(state['evidence'])
            state.update(current_version=version, previous_version=None, pending_change=None,
                versions={version: {'evidence': deepcopy(state['evidence']), 'parent_version': None,
                                     'recorded_at': now.isoformat()}})
        return state

    def _version_target(self, connection, evidence, *, stored=False):
        if not isinstance(evidence, dict) or set(evidence) != {
                'binding_sha256', 'source_sha256', 'review_sha256', 'reference_id',
                'realization_receipt_sha256'}:
            raise ValueError('provision version evidence fields differ')
        _identifier(evidence['reference_id'])
        for key in ('binding_sha256', 'source_sha256', 'review_sha256'):
            _digest(evidence[key])
        # A caller cannot certify operation while proposing a source version.
        if evidence['realization_receipt_sha256'] is not None:
            if not stored:
                raise ValueError('proposed version cannot supply realization proof')
            _digest(evidence['realization_receipt_sha256'])
        if self.verify_version is None:
            raise RuntimeError('trusted provision version resolver is unavailable')
        self.verify_version(connection, evidence)

    def _version_transition(self, connection, provision_id, state, action, request_id,
                            fingerprint, expected_revision, now):
        state['revision'] += 1
        receipt = {**self._receipt(state), 'action': action}
        thread = self._thread(provision_id)
        transition_timeline_thread(connection, self.scope, thread_id=thread,
            expected_revision=expected_revision, checkpoint_id=thread+':'+str(state['revision']),
            phase=state['status'], status='active', event_type='HOUSEHOLD_PROVISION_'+action.upper(),
            occurred_at=now, data={'state': state, 'request_id': request_id,
                                  'request_sha256': fingerprint, 'receipt': receipt})
        return receipt

    def _version_load(self, connection, provision_id, expected_revision, binding_sha256, now):
        _digest(binding_sha256)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('provision revision is invalid')
        state, rows = self._load(connection, self._thread(provision_id))
        if state is None:
            raise LookupError('provision is not registered')
        self._time(rows, now)
        if (state['revision'] != expected_revision
                or state['evidence']['binding_sha256'] != binding_sha256):
            raise CheckpointConflict('provision version or revision changed')
        if state['status'] in {'rejected', 'retired'}:
            raise CheckpointConflict('provision is no longer changeable')
        return self._version_state(state, now), rows

    def stage_version(self, provision_id, evidence, *, expected_revision, binding_sha256,
                      request_id, now):
        """Resolve a reviewed source version, retaining the running version unchanged."""
        evidence, request_id = deepcopy(evidence), _identifier(request_id)
        fingerprint = canonical_sha256({'stage_version': evidence, 'revision': expected_revision,
                                        'binding_sha256': binding_sha256})
        with self.engine.begin() as connection:
            self._begin(connection, now)
            _, rows = self._load(connection, self._thread(provision_id))
            replay = self._replay(rows, request_id, fingerprint)
            if replay is not None:
                return replay
            state, _ = self._version_load(connection, provision_id, expected_revision, binding_sha256, now)
            if state['pending_change'] is not None:
                raise CheckpointConflict('provision source change is incomplete')
            self.verify_current(connection, state['evidence'])
            self._version_target(connection, evidence)
            version = self._version_id(evidence)
            if version == state['current_version']:
                raise CheckpointConflict('source version is already current')
            state['versions'].setdefault(version, {'evidence': evidence,
                'parent_version': state['current_version'], 'recorded_at': now.isoformat()})
            # Full historical receipts remain in the original append-only timeline.
            # Keep only a bounded set of recent source choices in the current cursor.
            keep = {state['current_version'], state['previous_version'], version}
            while len(state['versions']) > 16:
                removable = sorted((v['recorded_at'], k) for k, v in state['versions'].items() if k not in keep)
                if not removable:
                    raise ValueError('provision version cursor cannot be compacted')
                del state['versions'][removable[0][1]]
            self.verify_current(connection, state['evidence'])
            return self._version_transition(connection, provision_id, state, 'version_staged',
                request_id, fingerprint, expected_revision, now)

    def begin_change(self, provision_id, version_id=None, *, undo=False, expected_revision,
                     binding_sha256, request_id, now):
        """Commit a paused, non-dispatchable handoff BEFORE changing real source files."""
        if type(undo) is not bool:
            raise ValueError('undo selector is invalid')
        request_id = _identifier(request_id)
        fingerprint = canonical_sha256({'begin_change': version_id, 'undo': undo,
            'revision': expected_revision, 'binding_sha256': binding_sha256})
        with self.engine.begin() as connection:
            self._begin(connection, now)
            _, rows = self._load(connection, self._thread(provision_id))
            replay = self._replay(rows, request_id, fingerprint)
            if replay is not None:
                return replay
            state, _ = self._version_load(connection, provision_id, expected_revision, binding_sha256, now)
            if state['pending_change'] is not None:
                raise CheckpointConflict('provision source change is incomplete')
            if undo:
                if version_id is not None:
                    raise ValueError('undo selects the retained previous source version')
                version_id = state['previous_version']
            if version_id is None or version_id not in state['versions'] or version_id == state['current_version']:
                raise ValueError('requested source version is unavailable')
            if state['pending_effects'] or self._source_effect(connection, state) not in {'clear', 'stopped'}:
                raise CheckpointConflict('provision effect is not confirmed clear')
            self.verify_current(connection, state['evidence'])
            self._version_target(connection, state['versions'][version_id]['evidence'], stored=True)
            self.verify_current(connection, state['evidence'])
            state['trial'] = None  # A source change never carries a trial or notice to another version.
            state['pending_change'] = {'from_version': state['current_version'], 'to_version': version_id,
                'prior_status': state['status'], 'request_id': request_id, 'undo': undo}
            state['status'] = 'paused'
            return self._version_transition(connection, provision_id, state, 'change_prepared',
                request_id, fingerprint, expected_revision, now)

    def finish_change(self, provision_id, *, expected_revision, binding_sha256, request_id, now):
        """Confirm the actual installed target; source mutation belongs to the host."""
        request_id = _identifier(request_id)
        fingerprint = canonical_sha256({'finish_change': expected_revision, 'binding_sha256': binding_sha256})
        with self.engine.begin() as connection:
            self._begin(connection, now)
            _, rows = self._load(connection, self._thread(provision_id))
            replay = self._replay(rows, request_id, fingerprint)
            if replay is not None:
                return replay
            state, _ = self._version_load(connection, provision_id, expected_revision, binding_sha256, now)
            pending = state['pending_change']
            if pending is None:
                raise CheckpointConflict('no source change is prepared')
            target = state['versions'][pending['to_version']]['evidence']
            self._version_target(connection, target, stored=True)
            self.verify_current(connection, target)
            verified = self.verify_realization(connection, target)
            if verified is not None:
                _digest(verified)
            if self.effect_status(connection, target) != 'clear':
                verified = None
            self.verify_current(connection, target)
            state['previous_version'], state['current_version'] = pending['from_version'], pending['to_version']
            state['evidence'], state['verification_sha256'] = deepcopy(target), verified
            state['status'] = ('paused' if state['stopped'] or pending['prior_status'] == 'paused' else
                'active' if state['adoption'] is not None and verified is not None else 'proposed')
            state['pending_change'] = None
            return self._version_transition(connection, provision_id, state, 'change_verified',
                request_id, fingerprint, expected_revision, now)

    def version_history(self, provision_id, *, now, limit=16):
        """Private, bounded projection of the existing history; never source code."""
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError('provision history limit is outside 1..32')
        with self.engine.begin() as connection:
            self._begin(connection, now)
            state, rows = self._load(connection, self._thread(provision_id))
            if state is None:
                raise LookupError('provision is not registered')
            return {'contract': 'EvaHouseholdProvisionHistory/v1', 'provision_id': provision_id,
                'revision': state['revision'], 'entries': [
                    {'revision': row['sequence'], 'event': row['event_type'],
                     'at': row['occurred_at'].isoformat(),
                     'status': row['payload']['data']['state']['status'],
                     'version_id': self._version_id(row['payload']['data']['state']['evidence'])}
                    for row in rows[-limit:]], 'earlier_entries': max(0, len(rows)-limit)}


    def _trial_guard(self, connection, state):
        if self.current_guard is None:
            raise RuntimeError('trusted current execution guard is unavailable')
        value = self.current_guard(connection, state['evidence'])
        _digest(value)
        return value

    def authorize_trial(self, provision_id, *, expires_at_epoch, max_effects, expected_revision,
                        binding_sha256, request_id, now):
        if (type(expires_at_epoch) is not int or not int(now.timestamp()) < expires_at_epoch <= int(now.timestamp())+604800
                or type(max_effects) is not int or not 1 <= max_effects <= 16):
            raise ValueError('owner trial requires a future bounded deadline and 1..16 effects')
        request_id = _identifier(request_id)
        fingerprint = canonical_sha256({'trial': expires_at_epoch, 'max_effects': max_effects,
            'revision': expected_revision, 'binding_sha256': binding_sha256})
        with self.engine.begin() as connection:
            self._begin(connection, now)
            _, rows = self._load(connection, self._thread(provision_id))
            replay = self._replay(rows, request_id, fingerprint)
            if replay is not None: return replay
            state, _ = self._version_load(connection, provision_id, expected_revision, binding_sha256, now)
            if (state['status'] not in {'proposed', 'paused'} or state['stopped']
                    or state['pending_change'] is not None or self._effect(connection, state) != 'clear'):
                raise CheckpointConflict('provision is not eligible for an owner-authorized trial')
            self.verify_current(connection, state['evidence'])
            guard = self._trial_guard(connection, state)
            if self.verify_trial_realization is None:
                raise RuntimeError('trusted exact trial realization verifier is unavailable')
            state['trial'] = {'trial_id': canonical_sha256({'provision_id': provision_id,
                'request_id': request_id, 'binding_sha256': binding_sha256, 'owner': self.owner}),
                'version_id': state['current_version'], 'binding_sha256': binding_sha256,
                'household_binding_sha256': self.household_sha256, 'owner_principal_id': self.owner,
                'execution_guard_sha256': guard, 'authorized_at': now.isoformat(),
                'expires_at_epoch': expires_at_epoch, 'max_effects': max_effects, 'effect_count': 0,
                'claims': {}, 'phase': 'authorized', 'evaluation': None, 'notice': None}
            state['status'] = 'proposed'
            return self._version_transition(connection, provision_id, state, 'trial_authorized',
                request_id, fingerprint, expected_revision, now)

    def _trial_current(self, connection, state, now):
        trial = state.get('trial')
        if (not isinstance(trial, dict) or state['status'] != 'proposed' or state['stopped']
                or state.get('pending_change') is not None
                or trial['version_id'] != state['current_version']
                or trial['binding_sha256'] != state['evidence']['binding_sha256']
                or trial['owner_principal_id'] != self.owner
                or trial['household_binding_sha256'] != self.household_sha256
                or now.timestamp() >= trial['expires_at_epoch']):
            raise CheckpointConflict('owner trial is expired, paused, stopped or no longer current')
        self.verify_current(connection, state['evidence'])
        if self._trial_guard(connection, state) != trial['execution_guard_sha256']:
            raise CheckpointConflict('trial source or execution authority changed')
        return trial

    def evaluate_trial(self, provision_id, *, working, useful, feedback, expected_revision,
                       binding_sha256, request_id, now):
        """Actual authenticated owner opinion PLUS exact post-grant working evidence."""
        if (type(working) is not bool or type(useful) is not bool
                or not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 1000):
            raise ValueError('trial evaluation requires concrete owner feedback')
        request_id = _identifier(request_id)
        fingerprint = canonical_sha256({'working': working, 'useful': useful, 'feedback': feedback,
            'revision': expected_revision, 'binding_sha256': binding_sha256})
        with self.engine.begin() as connection:
            self._begin(connection, now)
            _, rows = self._load(connection, self._thread(provision_id))
            replay = self._replay(rows, request_id, fingerprint)
            if replay is not None: return replay
            state, _ = self._version_load(connection, provision_id, expected_revision, binding_sha256, now)
            trial = self._trial_current(connection, state, now)
            if trial['phase'] != 'authorized':
                raise CheckpointConflict('trial already has an owner evaluation')
            proof = None
            if working and useful:
                if self.verify_trial_realization is None:
                    raise RuntimeError('trusted exact trial realization verifier is unavailable')
                proof = self.verify_trial_realization(connection, state['evidence'], deepcopy(trial))
                if proof is None or self._effect(connection, state) != 'clear':
                    raise CheckpointConflict('exact completed trial working proof is unavailable')
                _digest(proof)
                self._trial_current(connection, state, now)
            trial['evaluation'] = {'working': working, 'useful': useful, 'feedback': feedback,
                'owner_principal_id': self.owner, 'at': now.isoformat(), 'proof_sha256': proof,
                'evaluation_id': canonical_sha256({'trial_id': trial['trial_id'], 'request_id': request_id})}
            if proof is not None:
                import secrets
                nonce = secrets.token_hex(32)
                message = 'De vooraf toegestane proef werkt en is nuttig beoordeeld. Deze voorziening blijft binnen dezelfde afspraken bestaan.'
                notice_id = canonical_sha256({'trial_id': trial['trial_id'], 'version_id': trial['version_id'],
                    'evaluation': trial['evaluation'], 'nonce_sha256': canonical_sha256(nonce), 'message': message})
                trial['notice'] = {'notice_id': notice_id, 'nonce': nonce, 'message': message,
                    'trial_id': trial['trial_id'], 'version_id': trial['version_id'],
                    'owner_principal_id': self.owner, 'acknowledged_at': None}
                trial['phase'] = 'evaluated_awaiting_notice'
            else:
                trial['phase'], state['status'] = 'evaluated_negative', 'paused'
            return self._version_transition(connection, provision_id, state, 'trial_evaluated',
                request_id, fingerprint, expected_revision, now)

    def acknowledge_trial_notice(self, provision_id, *, notice_id, nonce, expected_revision,
                                 binding_sha256, request_id, now):
        """Owner UI receipt only; message/recipient/proof are never caller assertions."""
        _digest(notice_id); _digest(nonce)
        request_id = _identifier(request_id)
        fingerprint = canonical_sha256({'notice_id': notice_id, 'nonce': nonce,
            'revision': expected_revision, 'binding_sha256': binding_sha256})
        with self.engine.begin() as connection:
            self._begin(connection, now)
            _, rows = self._load(connection, self._thread(provision_id))
            replay = self._replay(rows, request_id, fingerprint)
            if replay is not None: return replay
            state, _ = self._version_load(connection, provision_id, expected_revision, binding_sha256, now)
            trial = self._trial_current(connection, state, now)
            notice = trial['notice']
            import hmac
            if (trial['phase'] != 'evaluated_awaiting_notice' or not notice
                    or notice['notice_id'] != notice_id or not hmac.compare_digest(notice['nonce'], nonce)
                    or notice['owner_principal_id'] != self.owner
                    or notice['version_id'] != state['current_version']):
                raise ValueError('trial notice acknowledgment does not match the server notice')
            proof = self.verify_trial_realization(connection, state['evidence'], deepcopy(trial))
            if proof is None or self._effect(connection, state) != 'clear':
                raise CheckpointConflict('exact completed trial working proof is unavailable')
            _digest(proof); self._trial_current(connection, state, now)
            trial['phase'], notice['acknowledged_at'] = 'adopted', now.isoformat()
            state['adoption'] = {'method': 'prior_authorized_trial', 'owner_principal_id': self.owner,
                'request_id': request_id, 'at': now.isoformat(), 'trial_id': trial['trial_id'],
                'evaluation_id': trial['evaluation']['evaluation_id'], 'notice_id': notice_id}
            state['verification_sha256'], state['status'] = proof, 'active'
            return self._version_transition(connection, provision_id, state, 'trial_adopted',
                request_id, fingerprint, expected_revision, now)


def household_trial_dispatchable(state, *, now, execution_guard):
    """Pure current grant check shared by source preparation and final admission."""
    trial = state.get('trial')
    return bool(isinstance(trial, dict) and state['status'] == 'proposed' and not state['stopped']
        and state.get('pending_change') is None and trial['phase'] == 'authorized'
        and trial['version_id'] == state.get('current_version')
        and trial['binding_sha256'] == state['evidence']['binding_sha256']
        and trial['owner_principal_id'] == state['owner_principal_id']
        and trial['household_binding_sha256'] == state['household_binding_sha256']
        and trial['execution_guard_sha256'] == execution_guard
        and now.timestamp() < trial['expires_at_epoch']
        and trial['effect_count'] < trial['max_effects'])
