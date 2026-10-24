"""Explicit host composition of the existing provision adapter; no enrollment writes."""
from pathlib import Path
import re

from eva.adapters.postgres.household_provisions import (
    PostgresHouseholdProvisions, procedure_provision_id,
)
from eva.contracts.canonical_json import canonical_sha256


class HouseholdProvisionHostFactory:
    """Host-only inputs; callbacks cannot be supplied by request/config content.

    load_enrollment() returns None only for an explicitly legacy route, otherwise
    exactly {contract, provision_id, owner_principal_id, binding_sha256} from the
    existing managed runtime configuration. No database absence fallback.
    callbacks_for(household_binding, procedure_path, config, enrollment) returns
    trusted authorize/verify_current/verify_realization/effect_status callables
    with the existing PostgresHouseholdProvisions callback signatures.
    """
    def __init__(self, engine, *, load_enrollment, callbacks_for):
        if not callable(load_enrollment) or not callable(callbacks_for):
            raise ValueError('provision factory requires host callbacks')
        self.engine = engine
        self.load_enrollment = load_enrollment
        self.callbacks_for = callbacks_for

    def _snapshot(self, procedure_path):
        enrollment = self.load_enrollment()
        if enrollment is None:
            return None, None, canonical_sha256({'enrollment': None, 'path': procedure_path})
        if (not isinstance(enrollment, dict) or set(enrollment) != {
                'contract', 'provision_id', 'owner_principal_id', 'binding_sha256'}
                or enrollment['contract'] != 'EvaHouseholdProvisionEnrollment/v1'
                or not isinstance(enrollment['binding_sha256'], str)
                or re.fullmatch('[0-9a-f]{64}', enrollment['binding_sha256']) is None):
            raise ValueError('explicit provision enrollment configuration differs')
        if not isinstance(procedure_path, str) or not Path(procedure_path).is_absolute():
            raise ValueError('enrolled provision requires the actual absolute procedure config path')
        import json
        with Path(procedure_path).open('rb') as stream:
            raw = stream.read(16385)
        if not 1 <= len(raw) <= 16384:
            raise ValueError('procedure configuration exceeds existing bound')
        config = json.loads(raw)
        if (not isinstance(config, dict)
                or config.get('contract') != 'EvaHouseholdProcedureBinding/v1'
                or enrollment['provision_id'] != procedure_provision_id(config)
                or enrollment['owner_principal_id'] != config['source_principal_id']
                or enrollment['binding_sha256'] != canonical_sha256(config)):
            raise ValueError('explicit provision enrollment does not match current source config')
        key = canonical_sha256({'enrollment': enrollment, 'path': procedure_path,
                                'configuration': canonical_sha256(config)})
        return enrollment, config, key

    def cache_key(self, procedure_path):
        return self._snapshot(procedure_path)[2]

    def __call__(self, procedure_path, *, household_binding):
        enrollment, config, key = self._snapshot(procedure_path)
        if enrollment is None:
            return None
        if household_binding != config['household_binding']:
            raise ValueError('enrolled provision household or output scope differs')
        callbacks = self.callbacks_for(household_binding, procedure_path, config, enrollment)
        names = {'authorize', 'verify_current', 'verify_realization', 'effect_status'}
        if (not isinstance(callbacks, dict) or set(callbacks) != names
                or not all(callable(callbacks[name]) for name in names)):
            raise ValueError('provision host callback composition differs')

        def current_host():
            if self.cache_key(procedure_path) != key:
                raise ValueError('provision managed configuration changed')

        def authorize(connection, now):
            callbacks['authorize'](connection, now)
            current_host()

        def verify_current(connection, evidence):
            current_host()
            callbacks['verify_current'](connection, evidence)
            current_host()

        def verify_realization(connection, evidence):
            current_host()
            # None remains None. Neither a saved source nor health is substituted.
            value = callbacks['verify_realization'](connection, evidence)
            current_host()
            return value

        def effect_status(connection, evidence):
            current_host()
            value = callbacks['effect_status'](connection, evidence)
            current_host()
            return value

        return PostgresHouseholdProvisions(self.engine,
            tenant_id=household_binding['tenant_id'],
            installation_id=household_binding['installation_id'],
            owner_principal_id=enrollment['owner_principal_id'],
            household_binding=household_binding, authorize=authorize,
            verify_current=verify_current, verify_realization=verify_realization,
            effect_status=effect_status)
