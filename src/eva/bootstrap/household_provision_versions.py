"""Reviewed saved-artifact versions using the existing owner, reference and timeline routes.

No reviewer, source registry, agent or scheduler is created here. A source change
is a paused PG handoff, exact managed files, and trusted current-source readback.
"""
from copy import deepcopy
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

import sqlalchemy as sa
from eva.adapters.postgres.operational_timeline import operational_events

from eva.adapters.postgres.household_provisions import PostgresHouseholdProvisions, procedure_provision_id
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.operations.saved_artifact_application import receipt_confirms_application
from eva.providers.procedure_reference_actions import ProcedureReferenceProviderFactory


def _read(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('provision version input must be a protected host-owned regular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum+1)
        if not 1 <= len(raw) <= maximum:
            raise ValueError('provision version input exceeds bound')
        after = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('provision version input changed while reading')
        return raw
    finally:
        os.close(fd)


def _write(path, raw, *, immutable=False):
    path = Path(path)
    info = path.parent.stat()
    if path.parent.resolve() != path.parent or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError('provision version output directory must be protected and host-owned')
    if path.exists() or path.is_symlink():
        old = _read(path, 65536)
        if old == raw:
            return
        if immutable:
            raise ValueError('retained provision version bytes differ')
    fd, temporary = tempfile.mkstemp(prefix='.provision-version-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


class HouseholdProvisionVersions:
    def __init__(self, controller, *, evaluator=None):
        self.controller = controller
        if evaluator is None:
            from eva.adapters.connectors.component_evaluation import configured_component_evaluator
            evaluator = configured_component_evaluator()
        self.evaluator = evaluator
        self.directory = controller.procedure_path.parent / 'versions'

    def _anchor(self):
        config = self.controller._source()
        pointer = self.controller._load_enrollment()
        name = config.get('source_file')
        if not isinstance(name, str) or Path(name).name != name or not name.endswith('.py'):
            raise ValueError('version source must be a sibling Python file')
        owner = self.controller.owner_loader()
        if (not isinstance(pointer, dict) or set(pointer) != {
                'contract', 'provision_id', 'owner_principal_id', 'binding_sha256'}
                or pointer['contract'] != 'EvaHouseholdProvisionEnrollment/v1'
                or pointer['owner_principal_id'] != str(owner._owner)
                or config['source_principal_id'] != str(owner._owner)
                or pointer['provision_id'] != procedure_provision_id(config)):
            raise ValueError('source version requires the same explicitly enrolled owner provision')
        self.controller._authorize_household(config['household_binding'])
        with self.controller.engine.begin() as connection:
            owner._authorize(connection, datetime.now(UTC).replace(microsecond=0))
        return deepcopy(config), deepcopy(pointer)

    def _source(self, reference_id, config):
        memory = PostgresMemoryOutbox(self.controller.engine,
            tenant_id=config['household_binding']['tenant_id'],
            installation_id=config['household_binding']['installation_id'])
        doc = memory.resolve_source(reference_id, principal_id=config['source_principal_id'], project_id=None)
        if doc is None:
            raise ValueError('current private source reference is unavailable')
        route = ProcedureReferenceProviderFactory.build(
            ProcedureReferenceProviderFactory.load_settings(), self.controller.engine).external_tools[0]
        turn = route.source_loader({'tenant_id': doc.tenant_id, 'installation_id': doc.installation_id,
            'principal_id': doc.principal_id, 'conversation_id': doc.conversation_id,
            'message_id': doc.source_ref,
            'audience': {'kind': 'private_person', 'principal_id': doc.principal_id}})
        resolved = route.resolve_improvement_source(turn, reference_id)
        if (resolved['source_target']['scope'] != {
                'tenant_id': config['household_binding']['tenant_id'],
                'installation_id': config['household_binding']['installation_id'],
                'principal_id': config['source_principal_id'], 'project_id': doc.project_id}
                or doc.project_id is not None or 'baseline_package_content' in resolved):
            raise ValueError('household version requires the current private Python source scope')
        return resolved

    def _reviewed(self, review_id, reference_id, baseline):
        if (not isinstance(review_id, str) or not 1 <= len(review_id) <= 128
                or (reference_id is not None and (not isinstance(reference_id, str) or not 1 <= len(reference_id) <= 240))
                or self.evaluator is None):
            raise ValueError('registered reviewed source selection is unavailable')
        original = self._source(baseline['reference_id'], baseline)
        if hashlib.sha256(original['baseline_content'].encode()).hexdigest() != baseline['source_sha256']:
            raise ValueError('retained original source reference changed')
        scope = dict(self.evaluator.final_assessment_scopes).get(original['path'])
        if scope is None:
            scope = dict(self.evaluator.reviewed_releases).get(review_id)
        expected_scope = canonical_sha256({
            'tenant_id': baseline['household_binding']['tenant_id'],
            'installation_id': baseline['household_binding']['installation_id'],
            'principal_id': baseline['source_principal_id']})
        if scope is None or scope != expected_scope:
            raise ValueError('no existing independent assessment scope for this source')
        # Status is read-only; the existing endpoint does not admit a new task.
        status = self.evaluator.application('status', review_id=review_id,
            request_id='provision-status:'+review_id, task_id=review_id, scope_sha256=scope)
        assessment = status.get('assessment', {})
        application = status.get('application', {})
        receipt = application.get('saved_artifact', {})
        publication = receipt.get('publication', {})
        if (status.get('review_id') != review_id or status.get('installed') is not True
                or assessment.get('decision') != 'accept'
                or assessment.get('semantic_improvement_verified') is not True
                or assessment.get('baseline_sha256') != baseline['source_sha256']
                or assessment.get('candidate_sha256') != publication.get('candidate_sha256')
                or application.get('state') != 'completed' or application.get('installed') is not True
                or publication.get('review_id') != review_id
                or publication.get('source_target') != original['source_target']
                or publication.get('baseline_sha256') != baseline['source_sha256']
                or not receipt_confirms_application(receipt, publication, application.get('task_id'))):
            raise ValueError('source lacks its original accepted assessment and verified publication')
        if reference_id is None:
            reference_id = receipt['reference']['document_id']
        target = self._source(reference_id, baseline)
        raw = target['baseline_content'].encode('utf-8')
        ref = receipt['reference']
        if (not 1 <= len(raw) <= 16384 or ref['document_id'] != reference_id
                or target['source_target']['scope'] != original['source_target']['scope']
                or target['source_target']['content_sha256'] != publication['candidate_sha256']
                or hashlib.sha256(raw).hexdigest() != publication['candidate_sha256']
                or target['source_target']['artifact_path'] != publication['destination_path']
                or target['source_target']['receipt_sha256'] != ref['receipt_sha256']):
            raise ValueError('current source reference differs from reviewed saved bytes')
        return raw, deepcopy(status)

    def _directory(self):
        base = self.directory.parent
        info = base.stat()
        if base.resolve() != base or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('source versions require the protected writable managed procedure directory')
        self.directory.mkdir(mode=0o700, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('source version directory must be private and host-owned')

    @staticmethod
    def _evidence(config):
        return {**{k: config[k] for k in ('source_sha256', 'review_sha256', 'reference_id')},
            'binding_sha256': canonical_sha256(config), 'realization_receipt_sha256': None}

    def _save(self, bundle):
        self._directory()
        version = PostgresHouseholdProvisions._version_id(self._evidence(bundle['configuration']))
        _write(self.directory / (version+'.json'), canonical_json_bytes(bundle), immutable=True)
        return version

    def _baseline(self, config):
        raw = _read(self.controller.procedure_path.parent / config['source_file'], 16384)
        review = _read(self.controller.review_path, 65536)
        parsed = json.loads(review)
        if (hashlib.sha256(raw).hexdigest() != config['source_sha256']
                or hashlib.sha256(review).hexdigest() != config['review_sha256']
                or parsed.get('contract') != 'EvaIndependentReview/v1' or parsed.get('verdict') != 'GO'
                or parsed.get('source', {}).get('sha256') != config['source_sha256']):
            raise ValueError('installed source/review bytes differ')
        resolved = self._source(config['reference_id'], config)
        if resolved['baseline_content'].encode() != raw:
            raise ValueError('installed source is not the current authorized reference')
        bundle = {'contract': 'EvaHouseholdProvisionVersion/v1', 'configuration': config,
            'source': raw.decode('utf-8'), 'review': review.decode('utf-8'),
            'origin': {'kind': 'retained_installed'}}
        evidence = self._evidence(config)
        existing = self.directory / (PostgresHouseholdProvisions._version_id(evidence)+'.json')
        if existing.exists():
            saved = self._bundle(evidence)
            if any(saved[key] != bundle[key] for key in ('configuration', 'source', 'review')):
                raise ValueError('retained baseline differs from actual installed bytes')
        else:
            self._save(bundle)

    def _bundle(self, evidence):
        version = PostgresHouseholdProvisions._version_id(evidence)
        bundle = json.loads(_read(self.directory / (version+'.json'), 65536))
        if (set(bundle) != {'contract', 'configuration', 'source', 'review', 'origin'}
                or bundle['contract'] != 'EvaHouseholdProvisionVersion/v1'
                or self._evidence(bundle['configuration']) != {**evidence, 'realization_receipt_sha256': None}
                or hashlib.sha256(bundle['source'].encode()).hexdigest() != evidence['source_sha256']
                or hashlib.sha256(bundle['review'].encode()).hexdigest() != evidence['review_sha256']):
            raise ValueError('retained source version bytes differ')
        if Path(bundle['configuration']['source_file']).name != bundle['configuration']['source_file']:
            raise ValueError('version source must be a sibling file')
        return bundle

    def _verify_target(self, connection, evidence):
        config, pointer = self._anchor()
        bundle = self._bundle(evidence)
        target = bundle['configuration']
        if (target['source_principal_id'] != config['source_principal_id']
                or target['household_binding'] != config['household_binding']
                or procedure_provision_id(target) != pointer['provision_id']):
            raise ValueError('retained source version changes owner or household authority')
        origin = bundle['origin']
        if origin.get('kind') == 'reviewed_saved_artifact':
            raw, status = self._reviewed(origin['review_id'], target['reference_id'], origin['baseline'])
            if (raw.decode() != bundle['source'] or status != origin['assessment_and_publication']):
                raise ValueError('reviewed source provenance changed')
        elif origin == {'kind': 'retained_installed'}:
            resolved = self._source(target['reference_id'], target)
            review = json.loads(bundle['review'])
            if (resolved['baseline_content'] != bundle['source']
                    or review.get('verdict') != 'GO'
                    or review.get('source', {}).get('sha256') != target['source_sha256']):
                raise ValueError('retained original source authority changed')
        else:
            raise ValueError('unknown source version origin')
        after, current_pointer = self._anchor()
        if after != config or current_pointer != pointer:
            raise ValueError('provision changed during version verification')

    def _store(self, config):
        callbacks = self.controller._callbacks(config['household_binding'],
            str(self.controller.procedure_path), config, None)
        return PostgresHouseholdProvisions(self.controller.engine,
            tenant_id=config['household_binding']['tenant_id'],
            installation_id=config['household_binding']['installation_id'],
            owner_principal_id=config['source_principal_id'], household_binding=config['household_binding'],
            verify_version=self._verify_target, **callbacks)

    def stage(self, provision_id, review_id, reference_id, expected_revision, binding_sha256, request_id):
        config, pointer = self._anchor()
        if (provision_id != pointer['provision_id'] or binding_sha256 != canonical_sha256(config)
                or pointer['binding_sha256'] != binding_sha256):
            raise ValueError('source version selection differs from current enrolled provision')
        self._baseline(config)
        raw, status = self._reviewed(review_id, reference_id, config)
        if self._anchor() != (config, pointer):
            raise ValueError('provision changed during reviewed source read')
        review = canonical_json_bytes({'contract': 'EvaIndependentReview/v1', 'verdict': 'GO',
            'source': {'sha256': hashlib.sha256(raw).hexdigest()},
            'origin': {'review_id': review_id, 'assessment_and_publication': status}})
        target = {**config, 'contract': 'EvaHouseholdProcedureBinding/v2', 'provision_id': provision_id,
            'reference_id': reference_id, 'source_file': 'provision-'+hashlib.sha256(raw).hexdigest()+'.py',
            'source_sha256': hashlib.sha256(raw).hexdigest(), 'review_sha256': hashlib.sha256(review).hexdigest()}
        self._save({'contract': 'EvaHouseholdProvisionVersion/v1', 'configuration': target,
            'source': raw.decode(), 'review': review.decode(),
            'origin': {'kind': 'reviewed_saved_artifact', 'review_id': review_id, 'baseline': config,
                       'assessment_and_publication': status}})
        receipt = self._store(config).stage_version(provision_id, self._evidence(target),
            expected_revision=expected_revision, binding_sha256=binding_sha256, request_id=request_id,
            now=datetime.now(UTC).replace(microsecond=0))
        return {'contract': 'EvaOwnerHouseholdProvisionVersion/v1', 'receipt': receipt,
                'selected_version_id': PostgresHouseholdProvisions._version_id(self._evidence(target)),
                'enrollment': pointer}

    def change(self, provision_id, version_id, expected_revision, binding_sha256, request_id, *, undo=False):
        # Same lock as existing explicit enrollment, never an installation/service lock.
        lock = self.controller.enrollment_path.with_name(self.controller.enrollment_path.name+'.lock')
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            config, pointer = self._anchor()
            if provision_id != pointer['provision_id']:
                raise ValueError('source change belongs to another provision')
            store = self._store(config)
            now = datetime.now(UTC).replace(microsecond=0)
            state = store.inspect(provision_id, now=now)
            if state is None:
                raise LookupError('provision is not registered')
            pending = state.get('pending_change')
            if pending is None:
                receipt = store.begin_change(provision_id, version_id, undo=undo,
                    expected_revision=expected_revision, binding_sha256=binding_sha256,
                    request_id=request_id+':prepare', now=now)
                state, pending = receipt, receipt['pending_change']
            elif (state['revision'] != expected_revision
                    or state['evidence']['binding_sha256'] != binding_sha256
                    or pending['undo'] != undo or (not undo and version_id != pending['to_version'])):
                raise ValueError('pending source change differs; inspect the same handoff')
            self._validate_pending(config, pointer, state)
            target_evidence = state['versions'][pending['to_version']]['evidence']
            target = self._bundle(target_evidence)
            with self.controller.engine.begin() as connection:
                self._verify_target(connection, target_evidence)
            allowed = {state['evidence']['binding_sha256'], target_evidence['binding_sha256']}
            current_config, current_pointer = self._anchor()
            # Target resolution can block; preserve unrelated managed-file
            # changes made since the earlier endpoint check. No writes on drift.
            self._validate_pending(current_config, current_pointer, state)
            if canonical_sha256(current_config) not in allowed:
                raise ValueError('actual source is neither retained handoff endpoint')
            _write(self.controller.procedure_path.parent / target['configuration']['source_file'],
                target['source'].encode(), immutable=True)
            _write(self.controller.review_path, target['review'].encode())
            _write(self.controller.procedure_path, canonical_json_bytes(target['configuration']))
            next_pointer = {**pointer, 'binding_sha256': target_evidence['binding_sha256']}
            _write(self.controller.enrollment_path,
                canonical_json_bytes({'household_provision_enrollment': next_pointer}))
            # Reconstruct only trusted callbacks after actual target readback;
            # the unchanged paused DB pending state cannot dispatch in between.
            receipt = self._store(target['configuration']).finish_change(provision_id,
                expected_revision=state['revision'], binding_sha256=state['evidence']['binding_sha256'],
                request_id=request_id+':confirm', now=datetime.now(UTC).replace(microsecond=0))
            return {'contract': 'EvaOwnerHouseholdProvisionVersion/v1', 'receipt': receipt,
                    'selected_version_id': receipt['current_version'], 'enrollment': next_pointer}
        finally:
            os.close(fd)

    def history(self, provision_id):
        config, pointer = self._anchor()
        if provision_id != pointer['provision_id']:
            raise ValueError('history belongs to another enrolled provision')
        return self._store(config).version_history(provision_id,
            now=datetime.now(UTC).replace(microsecond=0))


    def _validate_pending(self, config, pointer, state):
        pending = state.get('pending_change')
        if pending is None:
            return None
        before = self._bundle(state['versions'][pending['from_version']]['evidence'])
        after = self._bundle(state['versions'][pending['to_version']]['evidence'])
        if (config not in (before['configuration'], after['configuration'])
                or pointer['binding_sha256'] not in {
                    canonical_sha256(before['configuration']), canonical_sha256(after['configuration'])}
                or _read(self.controller.review_path, 65536) not in {
                    before['review'].encode(), after['review'].encode()}
                or _read(self.controller.procedure_path.parent/config['source_file'], 16384)
                   not in {before['source'].encode(), after['source'].encode()}):
            raise ValueError('actual pending source handoff is outside its two retained endpoints')
        return {'from_version': pending['from_version'], 'to_version': pending['to_version'],
            'expected_revision': state['revision'], 'binding_sha256': state['evidence']['binding_sha256'],
            'needs_completion': True, 'undo': pending['undo'],
            'in_flight_effects_not_replayed': True}

    def pending_overview(self):
        config, pointer = self._anchor()
        state = self._store(config).inspect(pointer['provision_id'],
            now=datetime.now(UTC).replace(microsecond=0))
        if state is None:
            raise LookupError('enrolled provision is missing')
        pending = self._validate_pending(config, pointer, state)
        if pending is None:
            return None
        return {'contract': 'EvaOwnerHouseholdProvisions/v1', 'enrollment': pointer,
            'provision_id': pointer['provision_id'], 'provision': state, 'change_pending': pending,
            'enrolled_record_missing': False, 'actual_working_proof_available': False}

    def choices(self):
        """Existing own saved-release handoffs, freshly verified; never a new assessment."""
        config, pointer = self._anchor()
        if pointer['binding_sha256'] != canonical_sha256(config):
            return {'contract': 'EvaHouseholdProvisionChoices/v1', 'choices': [], 'reason': 'change_pending'}
        source = self._source(config['reference_id'], config)
        owner = self.controller.owner_loader()
        e = operational_events.c
        value = e.payload['data']['output']['operation_result']
        with self.controller.engine.begin() as connection:
            owner._authorize(connection, datetime.now(UTC).replace(microsecond=0))
            rows = connection.execute(sa.select(e.payload, e.payload_sha256).where(
                e.tenant_id == owner._scope.tenant_id, e.installation_id == owner._scope.installation_id,
                e.event_type == 'COMPONENT_ACTION_RECORDED',
                e.payload['data']['name'].astext == 'prepare_own_component_release',
                e.payload['data']['output']['status'].astext == 'verified',
                value['package']['source_target'] == source['source_target'],
                value['package']['baseline_sha256'].astext == config['source_sha256'],
            ).order_by(e.occurred_at.desc(), e.thread_id).limit(8)).mappings().all()
        choices, seen = [], set()
        for row in rows:
            if canonical_sha256(row['payload']) != bytes(row['payload_sha256']).hex():
                raise ValueError('saved release handoff hash differs')
            data = row['payload']['data']['output']['operation_result']
            review_id = data.get('final_assessment_handoff', {}).get('review_id')
            if (review_id != data['package'].get('candidate_package_sha256')
                    or review_id is None or review_id in seen):
                continue
            seen.add(review_id)
            try:
                _, status = self._reviewed(review_id, None, config)
            except ValueError:
                continue  # Absent, rejected or not applied is not a selectable version.
            reference_id = status['application']['saved_artifact']['reference']['document_id']
            choices.append({'choice_id': canonical_sha256({'review_id': review_id,
                'reference_id': reference_id, 'source_target': source['source_target']}),
                'label': str(data['package'].get('expected_gain') or 'Beoordeelde bronverbetering')[:240],
                'review_id': review_id, 'reference_id': reference_id, 'state': 'reviewed_saved'})
        if self._anchor() != (config, pointer):
            raise ValueError('provision changed while listing reviewed versions')
        return {'contract': 'EvaHouseholdProvisionChoices/v1', 'choices': choices}

    def stage_choice(self, provision_id, choice_id, expected_revision, binding_sha256, request_id):
        choice = next((item for item in self.choices()['choices'] if item['choice_id'] == choice_id), None)
        if choice is None:
            raise ValueError('reviewed source choice is no longer current')
        return self.stage(provision_id, choice['review_id'], choice['reference_id'],
                          expected_revision, binding_sha256, request_id)

    def pending_command(self, provision_id, action, expected_revision, binding_sha256, request_id):
        config, pointer = self._anchor()
        if provision_id != pointer['provision_id'] or action not in {'pause', 'stop'}:
            raise ValueError('only pause or stop is available during a pending source change')
        state = self._store(config).inspect(provision_id, now=datetime.now(UTC).replace(microsecond=0))
        if state is None or self._validate_pending(config, pointer, state) is None:
            raise ValueError('no source change is pending')
        return self._store(config).command(provision_id, action, expected_revision=expected_revision,
            binding_sha256=binding_sha256, request_id=request_id,
            now=datetime.now(UTC).replace(microsecond=0))
