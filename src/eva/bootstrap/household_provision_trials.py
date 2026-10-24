"""Bounded prior trial, exact working evidence and owner UI notice on the same artifact.

No background process, new feedback store or external message transport. A prior
HTTP success or absence of complaints is never an owner evaluation/adoption.
"""
from copy import copy, deepcopy
from datetime import datetime
import re

import sqlalchemy as sa

from eva.adapters.postgres.household_provisions import PostgresHouseholdProvisions, procedure_provision_id
from eva.adapters.postgres.operational_timeline import operational_events
from eva.contracts.canonical_json import canonical_sha256


class HouseholdProvisionTrialEvidence:
    def __init__(self, callbacks, load_enrollment):
        self.callbacks, self.load_enrollment = callbacks, load_enrollment

    def current_guard(self, connection, evidence):
        return self.callbacks._current(connection, evidence)[1]

    def verify_empty_current(self, connection, evidence):
        """Known new enrolled binding after checked paused handoff, not legacy absence."""
        cb = self.callbacks
        try:
            config, guard = cb._current(connection, evidence)
            if config.get('contract') != 'EvaHouseholdProcedureBinding/v2':
                return None
            pointer = self.load_enrollment()
            pid = procedure_provision_id(config)
            expected = {'contract': 'EvaHouseholdProvisionEnrollment/v1', 'provision_id': pid,
                'owner_principal_id': str(cb.owner._owner), 'binding_sha256': canonical_sha256(config)}
            if pointer != expected:
                return None
            store = PostgresHouseholdProvisions(cb.owner._engine,
                tenant_id=cb.binding['tenant_id'], installation_id=cb.binding['installation_id'],
                owner_principal_id=str(cb.owner._owner), household_binding=cb.binding,
                authorize=cb.authorize, verify_current=cb.verify_current,
                verify_realization=cb.verify_realization, effect_status=cb.effect_status)
            state, rows = store._load(connection, store._thread(pid))
            if (state is None or state.get('pending_change') is not None or state['pending_effects']
                    or state.get('current_version') != store._version_id(evidence)
                    or state['evidence'] != evidence or state['status'] not in {'proposed', 'paused'}):
                return None
            changed = next((i for i in range(len(rows)-1, -1, -1)
                if rows[i]['event_type'] == 'HOUSEHOLD_PROVISION_CHANGE_VERIFIED'
                and rows[i]['payload']['data']['state']['current_version'] == state['current_version']
                and rows[i]['payload']['data']['state']['evidence'] == evidence), None)
            if changed is None or any(row['event_type'] == 'HOUSEHOLD_PROVISION_EFFECT_ADMITTED'
                                      for row in rows[changed+1:]):
                return None
            # A source preparation can carry unknown external state from a prior
            # execution; absence of a read result must never turn it into clear.
            e = operational_events.c
            prepared = connection.execute(sa.select(e.event_id).where(
                e.tenant_id == cb.scope.tenant_id, e.installation_id == cb.scope.installation_id,
                e.event_type == 'PROACTIVE_EXPRESSION_PREPARED',
                e.payload['data']['household_binding'] == cb.binding,
                e.payload['data']['household_procedure_lineage']['configuration']['config'] == config,
            ).limit(1)).first()
            if prepared is not None:
                return None
            after, after_guard = cb._current(connection, evidence)
            if after != config or after_guard != guard or self.load_enrollment() != pointer:
                return None
            return canonical_sha256({'contract': 'EvaVerifiedEmptyHouseholdVersion/v1',
                'binding_sha256': evidence['binding_sha256'], 'execution_guard_sha256': guard,
                'change_event_sha256': bytes(rows[changed]['payload_sha256']).hex(),
                'provision_id': pid, 'version_id': state['current_version']})
        except (KeyError, TypeError, ValueError, RuntimeError, OSError, sa.exc.SQLAlchemyError):
            return None  # Unknown source/history/rights is never clear.

    def effect_status_for_admission(self, connection, evidence, claim, expected_revision):
        """Exclude only this verified live, never-admitted host claim, not other effects."""
        cb = self.callbacks
        try:
            config, guard = cb._current(connection, evidence)
            if (set(claim) != {'thread_id', 'source_event_id', 'operation_id', 'revision'}
                    or claim['thread_id'] != 'sensory:'+claim['source_event_id']
                    or not callable(cb.select_reference)):
                return 'unknown'
            result = cb.read_chain(connection, cb.scope, claim['thread_id'])
            if result is None: return 'unknown'
            checkpoint, rows = result
            prepared = next((row for row in reversed(rows)
                             if row['event_type'] == 'PROACTIVE_EXPRESSION_PREPARED'), None)
            if prepared is None: return 'unknown'
            verifier = copy(cb)
            verifier.select_reference = None
            verifier.proof_reference = {'thread_id': claim['thread_id'], 'source_event_id': claim['source_event_id'],
                'source_context_sha256': prepared['payload']['data']['source_context_sha256']}
            chain = verifier._chain(connection, evidence)
            if chain is None: return 'unknown'
            data = verifier._bound(chain, evidence, guard)
            lineage, checkpoint, rows, _, _ = chain
            provision = lineage.get('configuration', {}).get('provision')
            pid = procedure_provision_id(config)
            if (provision != {'id': pid, 'revision': expected_revision}
                    or checkpoint['phase'] != 'expression_claimed' or checkpoint['status'] != 'active'
                    or checkpoint['deadline'] is None or checkpoint['deadline'] <= cb.clock()
                    or rows[-1]['event_type'] != 'PROACTIVE_EXPRESSION_CLAIMED'
                    or rows[-1]['sequence'] != claim['revision']
                    or rows[-1]['operation_id'] != claim['operation_id']
                    or rows[-1]['payload']['data'].get('source_event_id') != claim['source_event_id']
                    or cb.clock().timestamp() >= data['expression_expires_at_epoch']):
                return 'unknown'
            pointer = self.load_enrollment()
            if pointer != {'contract': 'EvaHouseholdProvisionEnrollment/v1', 'provision_id': pid,
                    'owner_principal_id': str(cb.owner._owner), 'binding_sha256': evidence['binding_sha256']}:
                return 'unknown'
            store = PostgresHouseholdProvisions(cb.owner._engine,
                tenant_id=cb.binding['tenant_id'], installation_id=cb.binding['installation_id'],
                owner_principal_id=str(cb.owner._owner), household_binding=cb.binding,
                authorize=cb.authorize, verify_current=cb.verify_current,
                verify_realization=cb.verify_realization, effect_status=cb.effect_status)
            state, history = store._load(connection, store._thread(pid))
            if state is None or state['revision'] != expected_revision or state['evidence'] != evidence:
                return 'unknown'
            identity = canonical_sha256(claim)
            if any(row['event_type'] == 'HOUSEHOLD_PROVISION_EFFECT_ADMITTED'
                    and row['payload']['data'].get('admission_id') == identity for row in history):
                return 'unknown'  # It may already have posted; never authorize a replay.
            other = cb.select_reference(connection, evidence, for_proof=False, exclude_thread=claim['thread_id'])
            if other is None:
                status = 'clear'
            else:
                verifier.proof_reference = other
                status = verifier.effect_status(connection, evidence)
            after, after_guard = cb._current(connection, evidence)
            if after != config or after_guard != guard or self.load_enrollment() != pointer:
                return 'unknown'
            return status
        except (KeyError, TypeError, ValueError, RuntimeError, OSError, sa.exc.SQLAlchemyError):
            return 'unknown'

    def verify_trial_realization(self, connection, evidence, trial):
        cb = self.callbacks
        config, guard = cb._current(connection, evidence)
        if (trial['owner_principal_id'] != str(cb.owner._owner)
                or trial['binding_sha256'] != evidence['binding_sha256']
                or trial['version_id'] != PostgresHouseholdProvisions._version_id(evidence)
                or trial['household_binding_sha256'] != canonical_sha256(cb.binding)
                or trial['execution_guard_sha256'] != guard
                or len(trial['claims']) > 16):
            return None
        authorized = datetime.fromisoformat(trial['authorized_at'])
        for recorded in trial['claims'].values():
            claim = recorded['claim']
            if (recorded['outcome'] != 'completed'
                    or not isinstance(recorded['proof_sha256'], str)
                    or re.fullmatch('[0-9a-f]{64}', recorded['proof_sha256']) is None
                    or claim['thread_id'] != 'sensory:'+claim['source_event_id']):
                continue
            result = cb.read_chain(connection, cb.scope, claim['thread_id'])
            if result is None: continue
            _, rows = result
            prepared = next((row for row in reversed(rows)
                             if row['event_type'] == 'PROACTIVE_EXPRESSION_PREPARED'), None)
            claimed = next((row for row in rows
                if row['event_type'] == 'PROACTIVE_EXPRESSION_CLAIMED'
                and row['sequence'] == claim['revision']
                and row['operation_id'] == claim['operation_id']), None)
            if (prepared is None or claimed is None or claimed['occurred_at'] < authorized
                    or rows[-1]['occurred_at'].timestamp() >= trial['expires_at_epoch']
                    or rows[-1]['payload']['data'].get('provider_message_id') != 'ha-offered:'+recorded['proof_sha256']):
                continue
            verifier = copy(cb)
            verifier.select_reference = None
            verifier.proof_reference = {'thread_id': claim['thread_id'], 'source_event_id': claim['source_event_id'],
                'source_context_sha256': prepared['payload']['data']['source_context_sha256']}
            proof = verifier.verify_realization(connection, evidence)
            if proof is None: continue
            after, after_guard = cb._current(connection, evidence)
            if after != config or after_guard != guard:
                raise ValueError('source changed during exact trial realization read')
            return canonical_sha256({'trial_id': trial['trial_id'], 'working_chain_sha256': proof,
                                     'claim_binding_sha256': canonical_sha256(claim)})
        return None


class HouseholdProvisionTrials:
    def __init__(self, controller):
        self.controller = controller

    def _store(self, provision_id):
        versions = self.controller._version_controls()
        config, pointer = versions._anchor()
        if provision_id != pointer['provision_id']:
            raise ValueError('owner trial belongs to another enrolled provision')
        return versions._store(config)

    def authorize(self, provision_id, expires_at_epoch, max_effects,
                  expected_revision, binding_sha256, request_id):
        return self._store(provision_id).authorize_trial(provision_id,
            expires_at_epoch=expires_at_epoch, max_effects=max_effects, expected_revision=expected_revision,
            binding_sha256=binding_sha256, request_id=request_id, now=self._now())

    def evaluate(self, provision_id, working, useful, feedback,
                 expected_revision, binding_sha256, request_id):
        return self._store(provision_id).evaluate_trial(provision_id, working=working, useful=useful,
            feedback=feedback, expected_revision=expected_revision, binding_sha256=binding_sha256,
            request_id=request_id, now=self._now())

    def acknowledge_notice(self, provision_id, notice_id, nonce,
                           expected_revision, binding_sha256, request_id):
        return self._store(provision_id).acknowledge_trial_notice(provision_id,
            notice_id=notice_id, nonce=nonce, expected_revision=expected_revision,
            binding_sha256=binding_sha256, request_id=request_id, now=self._now())

    @staticmethod
    def _now():
        from datetime import UTC
        return datetime.now(UTC).replace(microsecond=0)
