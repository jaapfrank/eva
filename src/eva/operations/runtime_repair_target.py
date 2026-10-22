"""Resolve automatic repair scope from host-verified failures and installed bytes.

This is an input adapter for the existing component assessment/application route.
It grants no execution or installation rights. Candidate code supplies neither a
source path, a baseline image, a criterion, nor an installed behavior verifier.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path, PurePosixPath
import re
import stat
import threading

from eva.application.self_repair import RepairObservation
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.reviewed_component_application import _save


# Reject direct authority/assessment modules. Product modules can also contain
# dispatch/authority logic: this filter never replaces independent semantic
# assessment or grants installation rights.
_PROTECTED_PREFIXES = ('operations/', 'contracts/', 'adapters/credentials/',
    'bootstrap/runtime_settings', 'bootstrap/delegated_work_service',
    'bootstrap/delegated_digital_', 'bootstrap/native_initiative_',
    'bootstrap/self_repair', 'application/self_repair', 'application/initiative_',
    'application/cancellation', 'application/background_execution',
    'application/component_', 'application/tool_program',
    'adapters/postgres/initiative_', 'adapters/postgres/owner_initiatives',
    'adapters/postgres/self_repair', 'adapters/postgres/scope',
    'adapters/postgres/delegated_work', 'adapters/postgres/conversation_journal',
    'adapters/postgres/component_', 'adapters/postgres/programming_',
    'adapters/postgres/tool_program', 'adapters/connectors/component_',
    'adapters/connectors/programming_', 'adapters/connectors/procedure_execution')


def repairable_source(path):
    if not isinstance(path, str) or not 1 <= len(path) <= 240:
        return False
    parsed = PurePosixPath(path)
    return (str(parsed) == path and not parsed.is_absolute()
        and re.fullmatch(r'[A-Za-z0-9_/]+\.py', path) is not None
        and not any(part in {'.', '..'} for part in parsed.parts)
        and path.startswith(('application/', 'adapters/', 'bootstrap/', 'domain/', 'providers/'))
        and not path.startswith(_PROTECTED_PREFIXES))


def _sha(value):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('repair target hash differs')
    return value


def verified_failure_binding(value):
    fields = {'contract', 'incident_id', 'incident_event_sha256', 'scope_sha256',
              'mandate_sha256', 'source_sha256', 'observation', 'intent_id'}
    if (not isinstance(value, dict) or set(value) != fields
            or value['contract'] != 'EvaCurrentRepairBinding/v1'
            or not isinstance(value['incident_id'], str)
            or re.fullmatch(r'self-repair:[0-9a-f]{64}', value['incident_id']) is None
            or not isinstance(value['intent_id'], str)
            or re.fullmatch(r'personal-interest:[0-9a-f]{64}', value['intent_id']) is None):
        raise ValueError('current host repair binding differs')
    for key in ('incident_event_sha256', 'scope_sha256', 'mandate_sha256', 'source_sha256'):
        _sha(value[key])
    raw = value['observation']
    if (not isinstance(raw, dict) or set(raw) != {'error_type_sha256', 'frames'}
            or not isinstance(raw['frames'], list)
            or any(not isinstance(f, dict) or set(f) != {'path', 'function', 'source_sha256'}
                   for f in raw['frames'])):
        raise ValueError('current host repair observation differs')
    observation = RepairObservation(raw['error_type_sha256'], tuple(
        (f['path'], f['function'], f['source_sha256']) for f in raw['frames']))
    if not observation.frames:
        raise ValueError('repair requires an observed Eva source frame')
    return deepcopy(value)


class RuntimeRepairTargets:
    """Immutable host receipts, using the current application runtime as authority.

    `binding` must re-read original scoped PostgreSQL evidence/STOP/mandate.
    `installation` returns actual image/configuration pins for core and worker.
    `source` reads bounded bytes from one of those immutable installed images.
    All callbacks are host composition, never supplied in an HTTP/model request.
    """
    def __init__(self, root, *, binding, installation, source, criteria):
        self.root = Path(root)
        if not self.root.is_absolute() or self.root.resolve() != self.root:
            raise ValueError('exact private repair receipt root required')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        import os
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('private host repair receipts required')
        if not all(callable(c) for c in (binding, installation, source, criteria)):
            raise ValueError('trusted repair callbacks required')
        self.binding, self.installation, self.source, self.criteria = binding, installation, source, criteria
        self._lock = threading.Lock()

    def _installation(self):
        current = self.installation()
        if not isinstance(current, dict) or set(current) != {'core', 'delegated-work-worker'}:
            raise ValueError('both actual runtime services are required')
        for value in current.values():
            if (not isinstance(value, dict) or set(value) != {'image', 'configuration_sha256'}
                    or not isinstance(value['image'], str)
                    or re.fullmatch(r'sha256:[0-9a-f]{64}', value['image']) is None):
                raise ValueError('installed repair runtime binding differs')
            _sha(value['configuration_sha256'])
        return deepcopy(current)

    def prepare(self, incident_id):
        with self._lock:
            return self._prepare(incident_id)

    def _prepare(self, incident_id):
        # The request supplies only an incident ID, not forged failure metadata.
        current = verified_failure_binding(self.binding(incident_id))
        if current['incident_id'] != incident_id:
            raise ValueError('host resolved another repair incident')
        installation = self._installation()
        selected, excluded, total = {}, [], 0
        for frame in reversed(current['observation']['frames']):
            path = frame['path']
            if path in selected or path in excluded:
                continue
            if not repairable_source(path) or len(selected) >= 8:
                excluded.append(path)
                continue
            versions = {}
            for service, runtime in installation.items():
                raw = self.source(runtime['image'], path)
                if not isinstance(raw, bytes) or not 1 <= len(raw) <= 262144:
                    raise ValueError('bounded installed repair source required')
                raw.decode('utf-8')
                versions[service] = hashlib.sha256(raw).hexdigest()
            # A patch drafted for one source must never overwrite another
            # executor's different implementation. No implicit merge or rebase.
            if set(versions.values()) != {frame['source_sha256']}:
                excluded.append(path)
                continue
            total += len(raw)
            if total > 524288:
                raise ValueError('repair source scope exceeds bound')
            selected[path] = frame['source_sha256']
        if not selected:
            raise ValueError('failure has no currently repairable source')
        scope = {'contract': 'EvaRuntimeRepairTarget/v1', 'failure': current,
                 'installation': installation, 'sources': dict(sorted(selected.items())),
                 'excluded_paths': sorted(excluded)}
        identity = canonical_sha256(scope)
        directory = self.root / identity
        if directory.exists():
            return self.load(identity)
        # Recheck authority and all runtime pins after source acquisition.
        if (verified_failure_binding(self.binding(incident_id)) != current
                or self._installation() != installation):
            raise ValueError('repair authority or installation changed during target resolution')
        # The independent verifier chooses/fixes its protocol before receiving
        # any candidate. Its receipt is host-only, not a builder's test report.
        criteria = self.criteria(deepcopy(scope))
        if (not isinstance(criteria, dict) or set(criteria) != {'sha256', 'path'}
                or not isinstance(criteria['path'], str)):
            raise ValueError('independent repair criteria receipt required')
        _sha(criteria['sha256'])
        from eva.operations.reviewed_component_application import _artifact
        _artifact(criteria)
        if (verified_failure_binding(self.binding(incident_id)) != current
                or self._installation() != installation):
            raise ValueError('repair authority or installation changed during criteria preparation')
        directory.mkdir(mode=0o700)
        target = {**scope, 'target_id': identity, 'criteria': criteria, 'installed': False}
        _save(directory / 'target.json', target, initial=True)
        return self.load(identity)

    def load(self, identity):
        _sha(identity)
        from eva.operations.component_release_import import load_object
        path = self.root / identity / 'target.json'
        info = path.lstat()
        import os
        if (not stat.S_ISREG(info.st_mode) or path.resolve() != path or info.st_nlink != 1
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('host repair target receipt changed')
        target = load_object(path)
        fields = {'contract', 'failure', 'installation', 'sources', 'excluded_paths'}
        if (set(target) != fields | {'target_id', 'criteria', 'installed'}
                or target['contract'] != 'EvaRuntimeRepairTarget/v1'
                or target['target_id'] != identity or target['installed'] is not False
                or canonical_sha256({k: target[k] for k in fields}) != identity):
            raise ValueError('host repair target identity changed')
        verified_failure_binding(target['failure'])
        from eva.operations.reviewed_component_application import _artifact
        _artifact(target['criteria'])
        return target

    def current(self, identity):
        target = self.load(identity)
        if (verified_failure_binding(self.binding(target['failure']['incident_id'])) != target['failure']
                or self._installation() != target['installation']):
            raise ValueError('repair target was stopped, changed, or superseded')
        return target


def public_repair_target(target):
    """No host criteria path or protected cases enter the developer's tool result."""
    return {'contract': target['contract'], 'target_id': target['target_id'],
            'incident_id': target['failure']['incident_id'], 'sources': deepcopy(target['sources']),
            'criteria_sha256': target['criteria']['sha256'], 'installed': False}
