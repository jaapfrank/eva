"""Host-only lifecycle control and retained offered-proof callbacks.

No source discovery, recent-event sweep, live camera or caller proof assertions.
An absent retained original context cannot establish a current working proof.
Only an explicit managed pointer is written; lifecycle stays in the PG timeline.
"""
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    external_timeline_scope, operational_checkpoints, operational_events,
    _state_from_event, _state_from_row, _event_from_row,
)
from eva.application.sensory import SensoryEvent
from eva.application.free_will import HouseholdProcedureLineage
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.home_assistant_procedure import HomeAssistantProcedure
from eva.adapters.postgres.household_provisions import PostgresHouseholdProvisions, procedure_provision_id
from eva.application.household_initiative import HouseholdInitiativeBinding
from eva.bootstrap.household_provision_host import HouseholdProvisionHostFactory


def read_exact_chain(connection, scope, thread_id):
    """One exact indexed thread, shared checkpoint lock, bounded retained rows."""
    c, e = operational_checkpoints.c, operational_events.c
    row = connection.execute(sa.select(operational_checkpoints).where(
        c.tenant_id == scope.tenant_id, c.installation_id == scope.installation_id,
        c.thread_id == thread_id).with_for_update(read=True)).mappings().one_or_none()
    if row is None:
        return None
    events = connection.execute(sa.select(operational_events).where(
        e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
        e.thread_id == thread_id).order_by(e.sequence).limit(65)).mappings().all()
    return dict(row), [dict(event) for event in events]


def _read_json(path, maximum):
    with Path(path).open('rb') as stream:
        raw = stream.read(maximum + 1)
    if not 1 <= len(raw) <= maximum:
        raise ValueError('retained host configuration exceeds bound')
    return raw, json.loads(raw)


class TrustedHouseholdProvisionCallbacks:
    """Construct only in trusted runtime code, never from model/request values.

    owner is the existing scoped PostgresOwnerInitiatives authority; legacy is
    the existing HomeAssistantProcedure with provisions=None (no recursion).
    Optional proof_reference is an actual retained {thread_id, source_event_id,
    source_context_sha256} reference. None means no proof, without any lookup.
    load_lineage(conn,proof_reference,evidence) returns the retained full context:
    {thread_id, source_event_id, context, source_binding}, or None. context and
    source_binding must be loaded from original retained evidence, not invented.
    No lineage means no working proof and unknown legacy effect state.
    """
    def __init__(self, owner, legacy, *, household_binding, procedure_path,
                 review_path, proof_reference=None, load_lineage=None, select_reference=None, load_enrollment=None, clock=None, read_chain=read_exact_chain):
        self.owner, self.legacy = owner, legacy
        self.binding = dict(household_binding)
        self.path, self.review_path = Path(procedure_path), Path(review_path)
        self.clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))
        self.proof_reference = None if proof_reference is None else dict(proof_reference)
        self.load_lineage, self.read_chain = load_lineage, read_chain
        self.select_reference = select_reference
        self.load_enrollment = load_enrollment
        self.scope = external_timeline_scope(self.binding['tenant_id'],
            self.binding['installation_id'], 'household provision scope absent')
        if (owner._scope != self.scope or owner._tenant_id != self.binding['tenant_id']
                or owner._installation_id != self.binding['installation_id']
                or legacy.household.to_payload() != self.binding
                or owner._engine is not legacy.engine
                or legacy.provisions is not None
                or Path(legacy.path).resolve() != self.path.resolve()
                or ((proof_reference is not None or select_reference is not None) and not callable(load_lineage))
                or (select_reference is not None and not callable(select_reference))
                or not callable(read_chain)):
            raise ValueError('trusted provision runtime scope or legacy route differs')

    def callbacks(self):
        result = {name: getattr(self, name) for name in
                  ('authorize', 'verify_current', 'verify_realization', 'effect_status')}
        if self.load_enrollment is not None:
            if not callable(self.load_enrollment):
                raise ValueError('trusted enrollment loader is invalid')
            from eva.bootstrap.household_provision_trials import HouseholdProvisionTrialEvidence
            trial = HouseholdProvisionTrialEvidence(self, self.load_enrollment)
            result.update({name: getattr(trial, name) for name in
                ('current_guard', 'verify_empty_current', 'verify_trial_realization', 'effect_status_for_admission')})
        return result

    def authorize(self, connection, now):
        self.owner._authorize(connection, now)

    def _current(self, connection, evidence):
        self.authorize(connection, self.clock())
        _, config = _read_json(self.path, 16384)
        if (config['household_binding'] != self.binding
                or config['source_principal_id'] != str(self.owner._owner)
                or canonical_sha256(config) != evidence['binding_sha256']
                or any(config[key] != evidence[key] for key in
                       ('source_sha256', 'review_sha256', 'reference_id'))):
            raise ValueError('current provision owner, household or source version differs')
        review_raw, review = _read_json(self.review_path, 65536)
        if (hashlib.sha256(review_raw).hexdigest() != config['review_sha256']
                or review.get('contract') != 'EvaIndependentReview/v1'
                or review.get('verdict') != 'GO'
                or review.get('source', {}).get('sha256') != config['source_sha256']):
            raise ValueError('current provision independent review differs')
        source, guard = self.legacy._current()
        if hashlib.sha256(source.encode()).hexdigest() != config['source_sha256']:
            raise ValueError('legacy current source differs')
        # Current authority can expire while resolving an existing source.
        self.authorize(connection, self.clock())
        _, after = _read_json(self.path, 16384)
        if after != config:
            raise ValueError('current provision configuration changed during verification')
        return config, guard

    def verify_current(self, connection, evidence):
        self._current(connection, evidence)

    def _chain(self, connection, evidence, *, for_proof=False):
        reference = self.proof_reference
        if reference is None and self.select_reference is not None:
            reference = self.select_reference(connection, evidence, for_proof=for_proof)
        if reference is None:
            return None
        if (set(reference) != {'thread_id', 'source_event_id', 'source_context_sha256'}
                or not isinstance(reference['source_context_sha256'], str)
                or len(reference['source_context_sha256']) != 64):
            raise ValueError('trusted retained proof reference differs')
        lineage = self.load_lineage(connection, reference, evidence)
        if lineage is None:
            return None
        if (set(lineage) not in ({'thread_id', 'source_event_id', 'context', 'source_binding'},
                                {'thread_id', 'source_event_id', 'context', 'source_binding', 'configuration'})
                or not isinstance(lineage['source_event_id'], str)
                or lineage['thread_id'] != 'sensory:' + lineage['source_event_id']
                or len(lineage['thread_id']) > 240
                or lineage['thread_id'] != reference['thread_id']
                or lineage['source_event_id'] != reference['source_event_id']
                or canonical_sha256(lineage['context']) != reference['source_context_sha256']):
            raise ValueError('retained household lineage differs')
        result = self.read_chain(connection, self.scope, lineage['thread_id'])
        if result is None:
            return None
        checkpoint, rows = result
        if (not rows or len(rows) > 64 or len(rows) != checkpoint['revision']
                or any(row['sequence'] != i for i, row in enumerate(rows, 1))
                or any(row['thread_id'] != lineage['thread_id'] for row in rows)
                or any(row['tenant_id'] != self.scope.tenant_id
                       or row['installation_id'] != self.scope.installation_id for row in rows)
                or any(bytes(row['payload_sha256']).hex() != canonical_sha256(row['payload']) for row in rows)
                or _state_from_event(_event_from_row(SimpleNamespace(**rows[-1]))) != _state_from_row(SimpleNamespace(**checkpoint))
                or any(b['occurred_at'] < a['occurred_at'] for a, b in zip(rows, rows[1:]))):
            raise ValueError('retained household chain hash or projection differs')
        origin = rows[0]
        data = origin['payload']['data']
        if (origin['event_type'] != 'SENSORY_EVENT_OBSERVED'
                or data['source_event_id'] != lineage['source_event_id']):
            raise ValueError('retained household origin differs')
        event = SensoryEvent(data['source'], data['source_event_id'], data['event_type'],
                             origin['occurred_at'], data['payload'])
        if not self.legacy.household.matches_event(event):
            raise ValueError('retained household origin audience differs')
        prepared = next((row for row in reversed(rows)
                         if row['event_type'] == 'PROACTIVE_EXPRESSION_PREPARED'), None)
        if prepared is None:
            return None
        if 'configuration' in lineage:
            retained = prepared['payload']['data'].get('household_procedure_lineage')
            if retained != {'contract': 'EvaHouseholdProcedureLineage/v1',
                    **{key: lineage[key] for key in ('context', 'source_binding', 'configuration')}}:
                raise ValueError('selected lineage is not the original prepared evidence')
        return lineage, checkpoint, rows, prepared, event

    def _bound(self, chain, evidence, guard):
        lineage, checkpoint, rows, prepared, event = chain
        data, context = prepared['payload']['data'], lineage['context']
        binding = lineage['source_binding']
        if (not isinstance(data.get('expression'), str) or not 1 <= len(data['expression']) <= 600
                or data.get('household_binding') != self.binding
                or data.get('source_event_id') != lineage['source_event_id']
                or canonical_sha256(context) != data.get('source_context_sha256')
                or context.get('source_sha256') != evidence['source_sha256']
                or context.get('source_binding_sha256') != canonical_sha256(binding)):
            raise ValueError('retained context does not bind this source and household')
        observation = context['observation']
        parent = event.payload['observation']
        selection = {key: context[key] for key in
                     ('parent_observation_sha256', 'selected_observation_sha256') if key in context}
        if parent['event_type'] == 'household_observation_batch':
            if (set(selection) != {'parent_observation_sha256', 'selected_observation_sha256'}
                    or selection['parent_observation_sha256'] != canonical_sha256(parent)
                    or selection['selected_observation_sha256'] != canonical_sha256(observation)
                    or observation not in parent['payload']['observations']):
                raise ValueError('retained original batch selection differs')
        elif observation != parent or selection:
            raise ValueError('retained original observation differs')
        if any(binding[key] != observation[key] for key in ('source', 'source_event_id', 'occurred_at')):
            raise ValueError('retained registry binding origin differs')
        configuration = lineage.get('configuration')
        expression_configuration = guard
        if configuration is not None:
            # Source/execution guard without historical lifecycle revision must
            # match the CURRENT actual legacy route. Preserve the exact original
            # revision in the expression guard, never re-authorize old speech.
            base = {key: value for key, value in configuration.items() if key != 'provision'}
            if canonical_sha256(base) != guard:
                raise ValueError('retained configuration is not the current source and execution')
            expression_configuration = canonical_sha256(configuration)
        expected = HomeAssistantProcedure._proposal_guard(expression_configuration, binding, selection)
        if data.get('expression_guard_sha256') != expected or context.get('binding_sha256') != expected:
            raise ValueError('retained expression guard does not match current legacy version')
        return data

    def _offered(self, chain, prepared_data, evidence, guard):
        lineage, checkpoint, rows, prepared, event = chain
        final = rows[-1]
        claims = [row for row in rows if row['sequence'] > prepared['sequence']
                  and row['event_type'] == 'PROACTIVE_EXPRESSION_CLAIMED']
        if not claims:
            return None
        claim = claims[-1]
        data = final['payload']['data']
        if (checkpoint['phase'] != 'expression_offered' or checkpoint['status'] != 'completed'
                or final['event_type'] != 'PROACTIVE_EXPRESSION_OFFERED'
                or final['sequence'] != claim['sequence'] + 1
                or final['operation_id'] != claim['operation_id']
                or data.get('channel') != 'home_assistant' or data.get('disposition') != 'offered'
                or data.get('household_binding') != self.binding
                or data.get('source_event_id') != lineage['source_event_id']
                or claim['payload']['data'].get('source_event_id') != lineage['source_event_id']
                or final['occurred_at'].timestamp() >= prepared_data['expression_expires_at_epoch']):
            return None
        base = {'binding': self.binding, 'operation_id': claim['operation_id'],
                'thread_id': lineage['thread_id'], 'revision': claim['sequence'],
                'source_event_id': lineage['source_event_id'],
                'text_sha256': canonical_sha256(prepared_data['expression'])}
        # Historical OFFERED omitted raw status; reconstruct ONLY the original
        # closed successful HTTP domain, never assume status200 or rewrite it.
        statuses = [status for status in range(200, 300) if data.get('provider_message_id') ==
                    'ha-offered:' + canonical_sha256({**base, 'http_status': status})]
        if len(statuses) != 1:
            return None
        return canonical_sha256({'contract': 'EvaExistingHouseholdOfferedProof/v1',
            'scope': {'tenant_id': str(self.scope.tenant_id), 'installation_id': str(self.scope.installation_id)},
            'household_binding_sha256': canonical_sha256(self.binding),
            'source_binding_sha256': evidence['binding_sha256'], 'legacy_guard': guard,
            'source_event_id': lineage['source_event_id'], 'http_status': statuses[0],
            'offered_receipt': data['provider_message_id'],
            'event_hashes': [bytes(row['payload_sha256']).hex() for row in rows]})

    def verify_realization(self, connection, evidence):
        config, guard = self._current(connection, evidence)  # Source/authority faults remain visible.
        try:
            chain = self._chain(connection, evidence, for_proof=True)
            if chain is None:
                return None
            prepared = self._bound(chain, evidence, guard)
            proof = self._offered(chain, prepared, evidence, guard)
        except (KeyError, TypeError, ValueError, RuntimeError, sa.exc.SQLAlchemyError):
            return None  # Missing/corrupt/uncertain retained proof is not success.
        if proof is not None:
            after, after_guard = self._current(connection, evidence)
            if after != config or after_guard != guard:
                raise ValueError('current source or authority guard changed during proof read')
        return proof

    def _legacy_effect(self, chain, evidence, guard):
        lineage, checkpoint, rows, prepared, event = chain
        phase = checkpoint['phase']
        if phase == 'expression_pending':
            return ('clear' if checkpoint['status'] == 'completed' and not any(
                row['event_type'] == 'PROACTIVE_EXPRESSION_CLAIMED' for row in rows) else 'unknown')
        if phase == 'expression_claimed':
            deadline = checkpoint['deadline']
            return ('in_flight' if checkpoint['status'] == 'active' and deadline is not None
                    and deadline > self.clock() else 'unknown')
        if phase == 'expression_ambiguous':
            return 'unknown'
        if phase == 'expression_offered':
            return 'clear' if self._offered(chain, prepared['payload']['data'], evidence, guard) else 'unknown'
        if phase == 'expression_interrupted':
            data = rows[-1]['payload']['data']
            claims = [row for row in rows if row['sequence'] > prepared['sequence']
                      and row['event_type'] == 'PROACTIVE_EXPRESSION_CLAIMED']
            return ('clear' if checkpoint['status'] == 'completed' and claims
                    and rows[-1]['sequence'] == claims[-1]['sequence'] + 1
                    and rows[-1]['operation_id'] == claims[-1]['operation_id']
                    and claims[-1]['payload']['data'].get('source_event_id') == lineage['source_event_id']
                    and rows[-1]['event_type'] == 'PROACTIVE_EXPRESSION_INTERRUPTED'
                    and data.get('channel') == 'home_assistant'
                    and data.get('disposition') == 'interrupted'
                    and data.get('household_binding') == self.binding
                    and data.get('source_event_id') == lineage['source_event_id']
                    and data.get('provider_message_id') is None else 'unknown')
        if phase == 'expression_expired':
            if checkpoint['status'] != 'completed' or rows[-1]['event_type'] != 'PROACTIVE_EXPRESSION_EXPIRED':
                return 'unknown'
            return ('unknown' if any(row['event_type'] == 'PROACTIVE_EXPRESSION_CLAIMED'
                                    for row in rows) else 'clear')
        return 'unknown'

    def effect_status(self, connection, evidence):
        try:
            config, guard = self._current(connection, evidence)
            chain = self._chain(connection, evidence)
            if chain is None:
                return 'unknown'
            self._bound(chain, evidence, guard)
            result = self._legacy_effect(chain, evidence, guard)
            if result == 'clear':
                after, after_guard = self._current(connection, evidence)
                if after != config or after_guard != guard:
                    return 'unknown'
            return result
        except Exception:
            # Pause/STOP remain possible when old source/read state is uncertain.
            return 'unknown'


class HouseholdProvisionRuntime:
    """Owner controls and provider wiring; only the enrollment pointer is a file.

    Lifecycle remains in the existing PG timeline. Startup never registers,
    adopts or activates. Explicit registration enrolls an optional legacy route
    as proposed, so that the source-bound lifecycle gates further dispatch.
    """
    def __init__(self, engine, *, owner_loader, enrollment_path, procedure_path, review_path):
        self.engine, self.owner_loader = engine, owner_loader
        self.enrollment_path, self.procedure_path = Path(enrollment_path), Path(procedure_path)
        self.review_path = Path(review_path)
        if not all(p.is_absolute() for p in (self.enrollment_path, self.procedure_path, self.review_path)):
            raise ValueError('managed household provision paths must be absolute')
        self.settings, self.context = None, None
        self.factory = HouseholdProvisionHostFactory(engine,
            load_enrollment=self._load_enrollment, callbacks_for=self._callbacks)

    def _managed(self):
        import os, stat
        fd = os.open(self.enrollment_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
                raise ValueError('managed enrollment must be a protected regular file')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(16385)
            if not 1 <= len(raw) <= 16384:
                raise ValueError('managed enrollment exceeds bound')
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {'household_provision_enrollment'}:
                raise ValueError('managed enrollment requires its explicit key')
            return raw, value
        finally:
            os.close(fd)

    def _load_enrollment(self):
        return self._managed()[1]['household_provision_enrollment']

    def factory_for(self, settings, context):
        self.settings, self.context = settings, context
        return self.factory

    def _authorize_household(self, binding):
        context = self.context
        if context is None or context.shared_output is None or context.work_server is None:
            raise RuntimeError('household provision provider composition unavailable')
        context.shared_output.validate_current()
        context.work_server.authority.validate_current()
        context.current_authority()
        policy = context.shared_output.policy
        if (binding['tenant_id'] != policy.tenant_id
                or binding['installation_id'] != policy.installation_id
                or binding['household_id'] != policy.household_id
                or binding['consent_operation_id'] != policy.consent_operation_id
                or binding['policy_sha256'] != canonical_sha256(policy.model_dump(mode='json'))
                or policy.devices.get(binding['satellite_id']) != binding['device_id']
                or binding['actor_id'] != 'household-device:' + binding['device_id']):
            raise ValueError('provision differs from current household authority')

    def _reference(self, connection, binding, evidence, *, for_proof, exclude_thread=None):
        """One bounded current-source lookup; historical missing lineage is not repaired."""
        scope = external_timeline_scope(binding['tenant_id'], binding['installation_id'],
                                        'household provision scope absent')
        e, c = operational_events.c, operational_checkpoints.c
        data = e.payload['data']
        config = data['household_procedure_lineage']['configuration']['config']
        filters = [e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
            e.event_type == 'PROACTIVE_EXPRESSION_PREPARED',
            data['household_binding'] == binding,
            config['household_binding'] == binding,
            config == self._source(),
            config['source_principal_id'].astext == str(self.owner_loader()._owner)]
        filters += [config[key].astext == evidence[key] for key in
                    ('source_sha256', 'review_sha256', 'reference_id')]
        if exclude_thread is not None:
            filters.append(e.thread_id != exclude_thread)
        # The config hash is verified on the returned exact chain, not asserted
        # from caller data or a timestamp. Filter current source before LIMIT.
        query = sa.select(e.thread_id, e.payload, e.payload_sha256).join(
            operational_checkpoints, sa.and_(c.tenant_id == e.tenant_id,
                c.installation_id == e.installation_id, c.thread_id == e.thread_id)).where(*filters)
        if for_proof:
            query = query.where(c.phase == 'expression_offered', c.status == 'completed')
            ordering = []
        else:
            # An older unresolved effect is not cleared by a newer offer or a
            # whole-second tie. Inspect unresolved outcomes BEFORE settled work.
            # The exact same owner/source/household filters apply to this lookup.
            claimed = operational_events.alias('provision_effect_claim')
            has_claim = sa.exists(sa.select(1).where(
                claimed.c.tenant_id == e.tenant_id,
                claimed.c.installation_id == e.installation_id,
                claimed.c.thread_id == e.thread_id,
                claimed.c.event_type == 'PROACTIVE_EXPRESSION_CLAIMED'))
            live = sa.and_(c.phase == 'expression_claimed', c.status == 'active',
                           c.deadline.is_not(None), c.deadline > datetime.now(UTC).replace(microsecond=0))
            uncertain = sa.or_(c.phase == 'expression_ambiguous',
                sa.and_(has_claim, c.phase.not_in(('expression_offered', 'expression_interrupted')),
                        ~live))
            ordering = [sa.case((uncertain, 3), (live, 2), else_=0).desc()]
        row = connection.execute(query.order_by(*ordering, e.occurred_at.desc(),
            e.sequence.desc(), e.thread_id).limit(1)).mappings().one_or_none()
        if row is None:
            return None
        if canonical_sha256(row['payload']) != bytes(row['payload_sha256']).hex():
            raise ValueError('retained provision reference hash differs')
        data = row['payload']['data']
        return {'thread_id': row['thread_id'], 'source_event_id': data['source_event_id'],
                'source_context_sha256': data['source_context_sha256']}

    def _lineage(self, connection, reference, evidence):
        config = self._source()
        scope = external_timeline_scope(config['household_binding']['tenant_id'],
            config['household_binding']['installation_id'], 'household provision scope absent')
        result = read_exact_chain(connection, scope, reference['thread_id'])
        if result is None:
            return None
        _, rows = result
        prepared = next((row for row in reversed(rows)
                         if row['event_type'] == 'PROACTIVE_EXPRESSION_PREPARED'), None)
        if prepared is None:
            return None
        payload = prepared['payload']['data'].get('household_procedure_lineage')
        if payload is None:
            return None
        from eva.contracts.canonical_json import canonical_json_bytes
        retained = HouseholdProcedureLineage(canonical_json_bytes(payload)).payload()
        if canonical_sha256(retained['configuration']['config']) != evidence['binding_sha256']:
            raise ValueError('retained lineage source version differs')
        return {'thread_id': reference['thread_id'], 'source_event_id': reference['source_event_id'],
                **{key: retained[key] for key in ('context', 'source_binding', 'configuration')}}

    def _working_proof(self, config):
        evidence = {key: config[key] for key in ('source_sha256', 'review_sha256', 'reference_id')}
        evidence.update(binding_sha256=canonical_sha256(config), realization_receipt_sha256=None)
        callbacks = self._callbacks(config['household_binding'], str(self.procedure_path), config, None)
        with self.engine.begin() as connection:
            callbacks['verify_current'](connection, evidence)
            proof = callbacks['verify_realization'](connection, evidence)
            clear = callbacks['effect_status'](connection, evidence) == 'clear'
            callbacks['verify_current'](connection, evidence)
        return proof if clear else None

    def _callbacks(self, binding, procedure_path, config, enrollment):
        if self.settings is None:
            raise RuntimeError('household provision provider settings unavailable')
        legacy = HomeAssistantProcedure(procedure_path, settings=self.settings,
            engine=self.engine, household=HouseholdInitiativeBinding(**binding),
            authorize=lambda: self._authorize_household(binding), provisions=None)
        return TrustedHouseholdProvisionCallbacks(self.owner_loader(), legacy,
            household_binding=binding, procedure_path=procedure_path,
            review_path=self.review_path, proof_reference=None,
            select_reference=lambda connection, evidence, **kw: self._reference(
                connection, binding, evidence, **kw), load_lineage=self._lineage,
            load_enrollment=self._load_enrollment).callbacks()

    def _source(self):
        _, config = _read_json(self.procedure_path, 16384)
        if (config.get('contract') not in {'EvaHouseholdProcedureBinding/v1', 'EvaHouseholdProcedureBinding/v2'}
                or not isinstance(config.get('household_binding'), dict)):
            raise ValueError('household provision source configuration differs')
        return config

    def _store(self, config, enrollment):
        if enrollment is not None:
            return self.factory(str(self.procedure_path), household_binding=config['household_binding'])
        return PostgresHouseholdProvisions(self.engine,
            tenant_id=config['household_binding']['tenant_id'],
            installation_id=config['household_binding']['installation_id'],
            owner_principal_id=config['source_principal_id'],
            household_binding=config['household_binding'],
            **self._callbacks(config['household_binding'], str(self.procedure_path), config, None))

    def _trial_controls(self):
        from eva.bootstrap.household_provision_trials import HouseholdProvisionTrials
        return HouseholdProvisionTrials(self)

    def authorize_trial(self, provision_id, expires_at_epoch, max_effects,
                        expected_revision, binding_sha256, request_id):
        return self._trial_controls().authorize(provision_id, expires_at_epoch, max_effects,
                                                expected_revision, binding_sha256, request_id)

    def evaluate_trial(self, provision_id, working, useful, feedback,
                       expected_revision, binding_sha256, request_id):
        return self._trial_controls().evaluate(provision_id, working, useful, feedback,
                                               expected_revision, binding_sha256, request_id)

    def acknowledge_trial_notice(self, provision_id, notice_id, nonce,
                                 expected_revision, binding_sha256, request_id):
        return self._trial_controls().acknowledge_notice(provision_id, notice_id, nonce,
            expected_revision, binding_sha256, request_id)

    def _version_controls(self):
        from eva.bootstrap.household_provision_versions import HouseholdProvisionVersions
        return HouseholdProvisionVersions(self)

    def version_choices(self):
        return self._version_controls().choices()

    def stage_version_choice(self, provision_id, choice_id, expected_revision, binding_sha256, request_id):
        return self._version_controls().stage_choice(provision_id, choice_id, expected_revision,
                                                     binding_sha256, request_id)

    def change_version(self, provision_id, version_id, expected_revision, binding_sha256, request_id):
        return self._version_controls().change(provision_id, version_id, expected_revision,
                                               binding_sha256, request_id)

    def undo_version(self, provision_id, expected_revision, binding_sha256, request_id):
        return self._version_controls().change(provision_id, None, expected_revision,
                                               binding_sha256, request_id, undo=True)

    def version_history(self, provision_id):
        return self._version_controls().history(provision_id)

    def overview(self):
        config, enrollment = self._source(), self._load_enrollment()
        if enrollment is not None:
            pending = self._version_controls().pending_overview()
            if pending is not None:
                return pending
        provision_id = procedure_provision_id(config)
        state = self._store(config, enrollment).inspect(provision_id,
            now=datetime.now(UTC).replace(microsecond=0))
        return {'contract': 'EvaOwnerHouseholdProvisions/v1', 'enrollment': enrollment,
                'provision_id': provision_id, 'provision': state,
                'enrolled_record_missing': enrollment is not None and state is None,
                'actual_working_proof_available': self._working_proof(config) is not None}

    def register(self, request_id):
        import fcntl, os, tempfile
        if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 160
                or not request_id.strip()):
            raise ValueError('household provision request id is invalid')
        # A file lock coordinates the pointer handoff, not lifecycle storage.
        lock = self.enrollment_path.with_name(self.enrollment_path.name + '.lock')
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            raw, managed = self._managed()
            config = self._source()
            enrollment = managed['household_provision_enrollment']
            provision_id = procedure_provision_id(config)
            pointer = {'contract': 'EvaHouseholdProvisionEnrollment/v1',
                'provision_id': provision_id, 'owner_principal_id': config['source_principal_id'],
                'binding_sha256': canonical_sha256(config)}
            if enrollment is not None and enrollment != pointer:
                raise ValueError('existing enrollment differs; do not overwrite it')
            evidence = {key: config[key] for key in ('source_sha256', 'review_sha256', 'reference_id')}
            evidence.update(binding_sha256=pointer['binding_sha256'], realization_receipt_sha256=None)
            store = self._store(config, enrollment)
            existing = store.inspect(provision_id, now=datetime.now(UTC).replace(microsecond=0))
            recovered = enrollment is None and existing is not None
            if recovered:
                # A previous DB commit may have preceded a failed pointer write.
                # Recover only that exact unadopted proposal; never rewrite an
                # active/paused version or create a second registration.
                if (existing['provision_id'] != provision_id
                        or existing['owner_principal_id'] != config['source_principal_id']
                        or existing['household_binding_sha256'] != canonical_sha256(config['household_binding'])
                        or existing['status'] != 'proposed' or existing['adoption'] is not None
                        or existing['verification_sha256'] is not None or existing['stopped']
                        or existing['pending_effects']
                        or any(existing['evidence'][key] != evidence[key] for key in
                               ('binding_sha256', 'source_sha256', 'review_sha256', 'reference_id'))):
                    raise ValueError('partial registration is not the same unadopted proposal')
                # Inspect checks current owner; it deliberately maps uncertain
                # legacy reads to unknown. Recovery additionally requires the
                # real current-source/rights callback to succeed visibly.
                with self.engine.begin() as connection:
                    callbacks = self._callbacks(config['household_binding'],
                        str(self.procedure_path), config, None)
                    callbacks['verify_current'](connection, existing['evidence'])
                receipt = {'contract': 'EvaHouseholdProvisionReceipt/v1', **existing}
            else:
                receipt = store.register(provision_id, evidence,
                    request_id=request_id, now=datetime.now(UTC).replace(microsecond=0))
            proof = self._working_proof(config)
            if self._managed()[0] != raw or self._source() != config:
                raise ValueError('registration saved; managed handoff changed, no activation')
            if enrollment is None and proof is not None:
                if self._managed()[0] != raw or self._source() != config:
                    raise ValueError('registration saved; managed handoff changed, no activation')
                temp_fd, temporary = tempfile.mkstemp(prefix='.household-provision-',
                                                       dir=self.enrollment_path.parent)
                try:
                    with os.fdopen(temp_fd, 'w') as stream:
                        json.dump({'household_provision_enrollment': pointer}, stream, sort_keys=True)
                        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
                    os.replace(temporary, self.enrollment_path)
                finally:
                    if os.path.exists(temporary): os.unlink(temporary)
            return {'contract': 'EvaOwnerHouseholdProvisionRegistration/v1',
                    'receipt': receipt, 'enrollment': pointer if enrollment is not None or proof is not None else None,
                    'recovered_registration': recovered, 'request_id': request_id,
                    'route_handoff': ('explicitly_enrolled_proposed_dispatch_gated'
                        if enrollment is not None or proof is not None else 'proposed_saved_awaiting_working_proof'),
                    'actual_working_proof_available': proof is not None}
        finally:
            os.close(fd)

    def command(self, provision_id, action, expected_revision, binding_sha256, request_id):
        config = self._source()
        if self._load_enrollment() is not None:
            controls = self._version_controls()
            if controls.pending_overview() is not None:
                return controls.pending_command(provision_id, action, expected_revision,
                                                binding_sha256, request_id)
        if provision_id != procedure_provision_id(config):
            raise LookupError('household provision is not the current host source')
        enrollment = self._load_enrollment()
        pointer = {'contract': 'EvaHouseholdProvisionEnrollment/v1',
            'provision_id': provision_id, 'owner_principal_id': config['source_principal_id'],
            'binding_sha256': canonical_sha256(config)}
        if enrollment != pointer:
            raise ValueError('household provision enrollment is incomplete or differs')
        return self._store(config, enrollment).command(provision_id, action,
            expected_revision=expected_revision, binding_sha256=binding_sha256,
            request_id=request_id, now=datetime.now(UTC).replace(microsecond=0))


def configured_household_provision_runtime(engine, *, owner_loader, environ=None):
    import os
    env = os.environ if environ is None else environ
    key = 'EVA_HOUSEHOLD_PROVISION_ENROLLMENT_FILE'
    if key not in env:
        return None
    enrollment_path = env[key]
    if not isinstance(enrollment_path, str) or not enrollment_path.strip():
        raise ValueError('explicit enrollment setting must name its managed file')
    procedure_path = env.get('EVA_HOUSEHOLD_PROCEDURE_FILE')
    if not procedure_path:
        raise ValueError('explicit provision enrollment requires its existing procedure route')
    review_path = env.get('EVA_HOUSEHOLD_PROVISION_REVIEW_FILE') or str(
        Path(procedure_path).parent / 'adoption-review.json')
    return HouseholdProvisionRuntime(engine, owner_loader=owner_loader,
        enrollment_path=enrollment_path, procedure_path=procedure_path, review_path=review_path)
