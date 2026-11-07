"""Host-only assessment admission through Eva's existing native initiative lane."""
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import re

import sqlalchemy as sa

from eva.adapters.postgres.initiative_adoption import adopt_private_initiative
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.self_repair import current_repair_binding
from eva.application.repair_assessment import repair_assessment_task
from eva.application.self_repair import MAX_REPAIR_SOURCE_BYTES
from eva.contracts.canonical_json import canonical_sha256


def _assessment_runtime_role(scope, *, target_id, binding, sources):
    if scope is None:
        return 'core'
    fields = {'contract', 'failure', 'installation', 'sources', 'excluded_paths', 'repair_services'}
    if (not isinstance(scope, dict) or set(scope) != fields
            or scope['contract'] != 'EvaRuntimeRepairTarget/v2'
            or scope['failure'] != binding or scope['sources'] != sources
            or canonical_sha256(scope) != target_id):
        raise ValueError('single-role assessment target differs from original failure')
    roles = ('core', 'delegated-work-worker')
    selected, installation = scope['repair_services'], scope['installation']
    if (not isinstance(selected, list) or len(selected) != 1 or selected[0] not in roles
            or not isinstance(installation, dict) or set(installation) != set(roles)):
        raise ValueError('exact single-role assessment installation required')
    for entry in installation.values():
        if (not isinstance(entry, dict) or set(entry) != {'image', 'configuration_sha256'}
                or not isinstance(entry['image'], str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', entry['image'])
                or not isinstance(entry['configuration_sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', entry['configuration_sha256'])):
            raise ValueError('invalid single-role assessment installation binding')
    return selected[0]


def admit_repair_assessment(authority, service, *, incident_id, role, target_id, sources,
                            candidate_sha256=None, comparison_sha256=None, source_root=None,
                            repair_scope=None):
    """Queue a distinct bounded stage, never masquerade as an owner request.

    Host composition supplies the frozen target/source pins. No endpoint accepts
    arbitrary instructions or a replacement mandate. The normal native worker
    later rechecks the original source/owner/STOP and narrows stage capabilities.
    """
    if service is None or service.digital_profile is None:
        raise ValueError('existing digital work executor required')
    binding = current_repair_binding(authority, incident_id, now=service.now())
    if role not in {'original_behavior_oracle', 'independent_review'}:
        raise ValueError('unknown repair assessment stage')
    if (not isinstance(target_id, str) or not re.fullmatch(r'[0-9a-f]{64}', target_id)
            or not isinstance(sources, dict) or not 1 <= len(sources) <= 8):
        raise ValueError('host-bound repair source scope required')
    runtime_role = _assessment_runtime_role(repair_scope,
        target_id=target_id, binding=binding, sources=sources)
    root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[1]
    if not root.resolve(strict=True).is_dir():
        raise ValueError('installed assessment source root required')
    frames = {frame['path']:frame['source_sha256'] for frame in binding['observation']['frames']}
    from eva.application.component_improvements import component_source
    from eva.application.self_repair import repairable_source
    for path,digest in sources.items():
        if (not repairable_source(path) or frames.get(path) != digest
                or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise ValueError('repair assessment path is outside the observed host target')
        try:
            target = component_source(root, path)
        except FileNotFoundError:
            if runtime_role != 'delegated-work-worker':
                raise
            # The trusted evaluator proved the worker bytes from the pinned
            # immutable image. Core only attests its own absence here.
            continue
        if (runtime_role == 'delegated-work-worker' or not target.is_file()
                or target.stat().st_size > MAX_REPAIR_SOURCE_BYTES
                or hashlib.sha256(target.read_bytes()).hexdigest() != digest):
            raise ValueError('installed assessment source changed')
    # Validate existing model/worker admission before persisting another stage.
    authority.digital_grant(service.digital_profile)
    task = repair_assessment_task(role, target_id=target_id, sources=sources,
        observation=binding['observation'], candidate_sha256=candidate_sha256,
        comparison_sha256=comparison_sha256)
    identity = {'incident_id':incident_id, 'target_id':target_id, 'role':role}
    if role == 'independent_review':
        identity.update(candidate_sha256=candidate_sha256, comparison_sha256=comparison_sha256)
    key = canonical_sha256(identity)
    intent_id = 'personal-interest:' + key
    now = service.now();owner = authority.owner
    with owner._engine.begin() as connection:
        current = current_repair_binding(authority, incident_id, now=now, _connection=connection)
        if current != binding:
            raise ValueError('repair assessment authority changed')
        e = operational_events.c
        original = connection.execute(sa.select(e.payload).where(
            e.tenant_id == owner._scope.tenant_id, e.installation_id == owner._scope.installation_id,
            e.thread_id == incident_id, e.event_type == 'SELF_REPAIR_OBSERVED')).scalar_one()['data']
        payload = {'principal_id':str(owner._owner), 'source_principal_id':str(owner._owner),
            'conversation_id':'initiative-task:' + key, 'source_message_id':'initiative-execute:' + key,
            'intent_id':intent_id, 'origin':'eva_initiative', 'execution_mode':'autonomous_task',
            'adoption_source':'automatic_runtime_repair_assessment', 'unprompted':True,
            'kind':'personal_interest', 'title':('Herstelgedrag controleren' if role=='original_behavior_oracle'
                else 'Herstel onafhankelijk beoordelen') + ' (' + key[:12] + ')',
            'motivation':'Afzonderlijk oorspronkelijk gedragsbewijs voor een werkelijk waargenomen fout.',
            'next_exploration':task, 'repair_incident_id':incident_id,
            'repair_source':original['source'], 'repair_mandate_sha256':binding['mandate_sha256'],
            'repair_role':role, 'repair_target_id':target_id,
            'repair_candidate_sha256':candidate_sha256,
            'repair_comparison_sha256':comparison_sha256,
            'authority':'current_host_self_repair_mandate_required'}
        def revalidate():
            if current_repair_binding(authority, incident_id, now=now, _connection=connection) != binding:
                raise ValueError('repair assessment stopped or superseded')
        return adopt_private_initiative(connection, owner._scope, payload, now=now, revalidate=revalidate)
