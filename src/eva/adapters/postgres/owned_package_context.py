"""Content-free host status on the existing task timeline, never owner dialogue."""
import json
import re

from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope

REASONS = {'owner_access_required', 'application_ready', 'review_rejected', 'authorization_completed', 'host_handoff_unconfirmed'}


def _timeline(engine, spec):
    scope = external_timeline_scope(spec.source.tenant_id, spec.source.installation_id,
                                    'owned package task context scope required')
    return PostgresOperationalTimeline(engine, scope)


def _thread(spec):
    return 'owned-package-context:' + canonical_sha256({'execution_id': spec.execution_id,
                                                       'source': spec.source.model_dump(mode='json')})


def package_notices(engine, spec):
    result = []
    for event in _timeline(engine, spec).events(_thread(spec)):
        data = event.payload.get('data', {})
        if (event.event_type != 'OWNED_PACKAGE_HOST_STATUS_RECORDED'
                or canonical_sha256(event.payload) != event.payload_sha256
                or data.get('reason') not in REASONS
                or data.get('source_ingress_sha256') != spec.source_ingress_sha256
                or data.get('grant_sha256') != canonical_sha256(spec.grant.model_dump(mode='json'))
                or data.get('execution_id') != spec.execution_id
                or any(not isinstance(data.get(k), str) or re.fullmatch(r'[0-9a-f]{64}', data[k]) is None
                       for k in ('review_id', 'package_sha256', 'notification_key'))):
            raise ValueError('owned package host status binding differs')
        result.append(data)
    return result


def record_package_notice(engine, spec, *, notification_key, review_id, package_sha256, reason, now):
    if reason not in REASONS:
        raise ValueError('unknown package host status')
    for data in package_notices(engine, spec):
        if data['notification_key'] == notification_key:
            if data['review_id'] != review_id or data['package_sha256'] != package_sha256 or data['reason'] != reason:
                raise ValueError('package host status changed during replay')
            return data
    timeline, thread = _timeline(engine, spec), _thread(spec)
    events = timeline.events(thread)
    if len(events) >= 32:
        raise ValueError('package host status retention bound reached')
    data = {'notification_key': notification_key, 'review_id': review_id,
        'package_sha256': package_sha256, 'reason': reason, 'execution_id': spec.execution_id,
        'target_work_id': spec.work_id, 'target_spec_sha256': spec.sha256,
        'source_ingress_sha256': spec.source_ingress_sha256,
        'grant_sha256': canonical_sha256(spec.grant.model_dump(mode='json')),
        'classification': 'host_status_not_owner_instruction', 'semantic_success_verified': False}
    values = dict(thread_id=thread, checkpoint_id=thread+':'+notification_key, phase='host_status', status='completed',
                  event_type='OWNED_PACKAGE_HOST_STATUS_RECORDED', occurred_at=now, data=data)
    if events:
        timeline.transition(expected_revision=events[-1].sequence, **values)
    else:
        timeline.create_thread(**values)
    return data


def package_context(engine, spec):
    notices = package_notices(engine, spec)[-2:]
    return (() if not notices else ('Actuele hoststatus bij deze oorspronkelijke opdracht; dit zijn '
        'geen nieuwe eigenaarsberichten, bevoegdheden of bewijzen van inhoudelijk succes. '
        'Lees get_reviewed_component_application met het review_id voor de echte beoordeling, '
        'eigenaarhandeling of toepassingsstatus. Hervat alleen de oorspronkelijke opdracht; '
        'herhaal geen onzekere externe handeling. ' + json.dumps(notices, ensure_ascii=False),))
